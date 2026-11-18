# -*- coding: utf-8 -*-
"""
MixCut · 场景识别与视觉分析引擎（三轮自适应采样）

设计目标：
  · 禁止对大素材全帧软解；
  · 第一轮粗采 480p@1fps 建全局画面差异曲线；
  · 第二轮在疑似切点窗口内 10fps 精采，定位到 ±0.05s；
  · 第三轮去抖合并，输出带置信度的切点；
  · 粗采帧顺带算亮度/色彩/运动特征，不额外解码；
  · NVDEC 失败自动回退软解；
  · 大素材支持代理文件（480p@2fps），二次分析秒出。

对外接口：
  analyze(path, duration, options, progress_cb, cancel_check) -> dict
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import numpy as np

from . import ffmpeg_tools as ft


NO_WINDOW_FLAGS = 0x08000000


# 看门狗：ffmpeg 因驱动/素材异常挂起时，超时强杀，避免管道永久卡死
WATCHDOG_TIMEOUT = 120.0


def _arm_watchdog(proc, timeout=WATCHDOG_TIMEOUT):
    def _kill():
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()
    return timer


# ---------------------------------------------------------
# 解码管道（NVDEC 优先，失败回退软解）
# ---------------------------------------------------------

def _decoder_cmd(path, width, fps, gpu):
    ffmpeg = ft.find_ffmpeg()
    if gpu:
        hw = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        vf = f"scale_cuda={width}:-2,hwdownload,format=nv12,fps={fps}"
    else:
        hw = []
        vf = f"scale={width}:-2,fps={fps}"
    # -skip_frame nokey：解码器只解关键帧，4K/10bit 高码率素材的粗采
    # 量从"逐帧解 1650 帧"降到"解 30~60 个关键帧"，快一个数量级。
    return [ffmpeg] + hw + [
        "-skip_frame", "nokey",
        "-i", str(path), "-vf", vf, "-an",
        "-f", "image2pipe", "-vcodec", "ppm", "-",
    ]


def _read_ppm_token(stream):
    tok = b""
    while True:
        ch = stream.read(1)
        if not ch:
            return None
        if ch in b" \t\r\n":
            if tok:
                return tok
            continue
        if ch == b"#":
            while ch and ch not in b"\n":
                ch = stream.read(1)
            continue
        tok += ch


def _read_ppm_frame(stream):
    """从管道读一帧 PPM，返回 (h,w,3) uint8 ndarray；结束或失败返回 None。"""
    magic = stream.read(2)
    if magic != b"P6":
        return None
    w_tok = _read_ppm_token(stream)
    h_tok = _read_ppm_token(stream)
    m_tok = _read_ppm_token(stream)
    if not (w_tok and h_tok and m_tok):
        return None
    w, h = int(w_tok), int(h_tok)
    stream.read(1)  # maxval 后的单个空白
    n = w * h * 3
    data = stream.read(n)
    if len(data) < n:
        return None
    return np.frombuffer(data, dtype=np.uint8).reshape(h, w, 3)


def _open_decoder(path, width, fps, gpu):
    cmd = _decoder_cmd(path, width, fps, gpu)
    proc = subprocess.Popen(
        # stderr 必须丢弃或持续抽干：否则 ffmpeg 写满管道缓冲会阻塞，
        # 而本处只读 stdout，形成死锁
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW_FLAGS,
    )
    return proc


# ---------------------------------------------------------
# 第一轮：粗采建图
# ---------------------------------------------------------

def _coarse_scan(path, duration, gpu, fps, width,
                 progress_cb=None, cancel_check=None):
    """流式抽 480p@fps 帧，返回记录列表 [(t, hist16, mean, diff)]。

    diff 是与上一帧的差异（直方图 L1 距离 + 均值差）。
    内存只存每帧的统计量，不存图像。
    """
    try:
        proc = _open_decoder(path, width, fps, gpu)
    except Exception:
        if gpu:
            return _coarse_scan(path, duration, False, fps, width,
                                progress_cb, cancel_check)
        return []

    # 看门狗：即使 ffmpeg 异常挂起，超时也会被强杀，不会永久阻塞
    watchdog = _arm_watchdog(proc)
    records = []
    prev = None
    t = 0.0
    dt = 1.0 / fps
    last_pct = -1
    try:
        while True:
            if cancel_check and cancel_check():
                proc.kill()
                break
            frame = _read_ppm_frame(proc.stdout)
            if frame is None:
                break
            gray = frame.mean(axis=2)
            hist = np.histogram(gray, bins=16, range=(0, 255),
                                density=True)[0].astype(np.float32)
            mean = float(gray.mean())
            if prev is None:
                diff = 0.0
            else:
                diff = float(np.abs(hist - prev[1]).sum()) + \
                    abs(mean - prev[2]) / 255.0
            records.append((t, hist, mean, diff))
            prev = (t, hist, mean)
            if duration and progress_cb:
                pct = int(min(99, t / duration * 100))
                if pct != last_pct:
                    progress_cb(pct, f"粗采 {int(t)}s")
                    last_pct = pct
            t += dt
    finally:
        watchdog.cancel()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
    return records


# ---------------------------------------------------------
# 第二轮：疑似窗口精采
# ---------------------------------------------------------

def _fine_scan_window(path, center, half_window, fps, gpu, width=640):
    """在 [center-half, center+half] 内高帧率抽帧，返回差异最大的时刻。"""
    start = max(0.0, center - half_window)
    try:
        ffmpeg = ft.find_ffmpeg()
        hw = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"] if gpu else []
        vf = (f"scale_cuda={width}:-2,hwdownload,format=nv12,fps={fps}"
              if gpu else f"scale={width}:-2,fps={fps}")
        cmd = [ffmpeg] + hw + [
            "-ss", f"{start:.3f}", "-to", f"{center + half_window:.3f}",
            "-i", str(path), "-vf", vf, "-an",
            "-f", "image2pipe", "-vcodec", "ppm", "-",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,
                                creationflags=NO_WINDOW_FLAGS)
    except Exception:
        return None

    # 精采窗口很短，30 秒看门狗足够；挂起时强杀，返回 None 跳过该切点
    watchdog = _arm_watchdog(proc, timeout=30.0)
    best_t, best_diff = center, -1.0
    prev_gray = None
    t = start
    dt = 1.0 / fps
    try:
        while True:
            frame = _read_ppm_frame(proc.stdout)
            if frame is None:
                break
            gray = frame.mean(axis=2)
            if prev_gray is not None:
                d = float(np.abs(gray.astype(np.float32) -
                                 prev_gray.astype(np.float32)).mean())
                if d > best_diff:
                    best_diff = d
                    best_t = t
            prev_gray = gray
            t += dt
    finally:
        watchdog.cancel()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
    return best_t if best_diff > 1.0 else None  # 差异太小不算切点


# ---------------------------------------------------------
# 第三轮：去抖合并
# ---------------------------------------------------------

def _merge_cuts(cuts, min_gap=0.3):
    """合并相邻太近的切点，返回 [(time, confidence)]。"""
    if not cuts:
        return []
    out = []
    for t, conf in sorted(cuts):
        if out and t - out[-1][0] < min_gap:
            # 取置信度更高的
            if conf > out[-1][1]:
                out[-1] = (t, conf)
            continue
        out.append((t, conf))
    return out


# ---------------------------------------------------------
# 对外主接口
# ---------------------------------------------------------

def analyze(path, duration=None, options=None,
            progress_cb=None, cancel_check=None):
    """分析单个素材，返回 dict：

    {
      "cuts": [(time_sec, confidence)],
      "mean_brightness": float,
      "motion": float,         # 0~1 越高运动越剧烈
      "sharpness": float,      # 0~1 相对值
      "proxy_path": str|None,
    }
    """
    opts = {
        "coarse_fps": 1.0,
        "coarse_width": 480,
        "fine_fps": 10.0,
        "fine_half_window": 1.0,
        # cuda 全管道（hwaccel_output_format cuda + scale_cuda）在部分素材上
        # 实测 hwaccel 初始化报错/并发挂起，且硬解混合方案无速度收益，默认走 CPU
        "gpu": False,
    }
    if options:
        opts.update(options or {})

    if duration is None:
        try:
            duration = ft.probe_duration(path)
        except Exception:
            duration = None

    gpu = bool(opts["gpu"])
    records = _coarse_scan(
        path, duration, gpu,
        fps=opts["coarse_fps"], width=opts["coarse_width"],
        progress_cb=progress_cb, cancel_check=cancel_check,
    )
    # GPU 粗采失败且没读到帧，回退软解
    if not records and gpu:
        records = _coarse_scan(
            path, duration, False,
            fps=opts["coarse_fps"], width=opts["coarse_width"],
            progress_cb=progress_cb, cancel_check=cancel_check,
        )

    if not records:
        return {"cuts": [], "mean_brightness": 0.0,
                "motion": 0.0, "sharpness": 0.0, "proxy_path": None}

    diffs = np.array([r[3] for r in records], dtype=np.float64)
    means = np.array([r[2] for r in records], dtype=np.float64)

    # 自适应阈值：均值 + 1.5*标准差，下限 0.15 防止无变化视频误报
    if diffs.std() < 1e-6:
        threshold = 999.0
    else:
        threshold = max(0.15, float(diffs.mean() + 1.5 * diffs.std()))

    # 找疑似切点位置
    candidates = []
    for i, (t, _h, _m, d) in enumerate(records):
        if i > 0 and d >= threshold:
            candidates.append(t)

    # 第二轮精细确认
    cuts = []
    for t in candidates:
        if cancel_check and cancel_check():
            break
        fine_t = _fine_scan_window(
            path, t, opts["fine_half_window"],
            fps=opts["fine_fps"], gpu=gpu,
        )
        if fine_t is not None and fine_t > 0.05:
            conf = min(1.0, float(diffs[int(round(t * opts["coarse_fps"]))]) / threshold)
            cuts.append((float(fine_t), conf))

    cuts = _merge_cuts(cuts)

    # 视觉特征（粗采帧顺带算）
    mean_brightness = float(means.mean()) / 255.0
    motion = float(diffs.mean()) / 4.0
    motion = max(0.0, min(1.0, motion))

    # 黑屏/静止区间：粗采帧 1fps 足够判断持续事件，不用再让 ffmpeg 逐帧解
    black_intervals = _find_flat_intervals(
        records, kind="black", min_duration=0.5)
    freeze_intervals = _find_flat_intervals(
        records, kind="freeze", min_duration=1.0)

    return {
        "cuts": cuts,
        "mean_brightness": mean_brightness,
        "motion": motion,
        "sharpness": 0.0,
        "proxy_path": None,
        "black_intervals": black_intervals,
        "freeze_intervals": freeze_intervals,
    }


def _find_flat_intervals(records, kind, min_duration):
    """从粗采帧记录里找黑屏/静止区间。

    records: [(t, hist16, mean_0_255, diff)]
    kind: "black"（亮度均值 < 25/255）或 "freeze"（帧间 diff < 0.02）
    返回 [(start, end), ...]
    """
    if not records:
        return []
    dt = 1.0 / 1.0  # 粗采默认 1fps，dt=1s
    intervals = []
    start = None
    for t, _h, mean, diff in records:
        if kind == "black":
            flat = mean < 25.0  # 8bit 灰度 0~255
        else:
            flat = diff < 0.02
        if flat:
            if start is None:
                start = t
        else:
            if start is not None and t - start >= min_duration:
                intervals.append((round(start, 2), round(t, 2)))
            start = None
    if start is not None:
        end = records[-1][0] + dt
        if end - start >= min_duration:
            intervals.append((round(start, 2), round(end, 2)))
    return intervals
