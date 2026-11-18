# -*- coding: utf-8 -*-
"""MixCut · 顺序帧管道

用 ffmpeg 一次性顺序解码，按目标帧率缩小后经 stdout 管道逐帧交给 Python。

为什么要这个模块：
    cv2.VideoCapture + cap.set(CAP_PROP_POS_FRAMES / POS_MSEC) 是随机 seek，
    每次跳转都要从最近的关键帧重新解码到目标帧。一条 1080p 十分钟的素材，
    按每 5 秒 3 帧采样就是 360 次 seek，等于把片子解码了几十遍。

    这里改成「解码一遍、边解边抽」，配合 scale 把帧缩小到分析需要的尺寸，
    Python 侧的内存和 CPU 开销也跟着降下来。

用法：
    for timestamp, frame in iter_frames(path, fps=0.6, max_side=960):
        ...  # frame 是 BGR uint8 ndarray

    for timestamp, frame in iter_frames(path, fps=0.6, start=60, duration=30):
        ...  # 只处理 60s 起的 30 秒，长素材可以分段跑
"""

import os
import subprocess
from typing import Iterator, Optional, Tuple

import numpy as np

from .ffmpeg_tools import find_ffmpeg, find_ffprobe


def _even(value: int) -> int:
    """ffmpeg 的 yuv 缩放更喜欢偶数；这里统一向下取偶。"""
    value = int(value)
    return value if value % 2 == 0 else value - 1


def probe_media(path: str) -> Optional[Tuple[int, int, float]]:
    """返回 (宽, 高, 时长秒)；拿不到返回 None。"""
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None
    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "default=noprint_wrappers=1",
        str(path),
    ]
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=30).stdout.decode("utf-8", "replace")
    except Exception:
        return None

    width = height = 0
    duration = 0.0
    for line in out.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        try:
            if key == "width":
                width = int(value)
            elif key == "height":
                height = int(value)
            elif key == "duration":
                duration = float(value)
        except ValueError:
            continue
    if width <= 0 or height <= 0:
        return None
    return width, height, duration


def scaled_size(width: int, height: int, max_side: int) -> Tuple[int, int]:
    """按最长边限制算出输出尺寸，并保证是偶数。"""
    longest = max(width, height)
    if max_side <= 0 or longest <= max_side:
        return _even(width), _even(height)
    scale = max_side / float(longest)
    return max(2, _even(round(width * scale))), max(2, _even(round(height * scale)))


def probe_pix_fmt(path: str) -> str:
    """返回视频流的像素格式，例如 ``yuv420p10le`` / ``yuv422p10le``。

    这个值决定能否走 GPU 硬件解码：
    · 8bit 4:2:0 / 10bit 4:2:0（yuv420p / yuv420p10le）→ NVDEC 支持
    · 10bit 4:2:2（yuv422p10le）→ 消费级 GeForce 的 NVDEC 不支持，
      硬解会报 "not supported with this chroma format"，只能软解
    """
    ffprobe = find_ffprobe()
    if not ffprobe:
        return ""
    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=pix_fmt",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=20)
        return (out.stdout or b"").decode("utf-8", "replace").strip().splitlines()[0] \
            if out.stdout else ""
    except Exception:
        return ""


# NVDEC 能硬解的像素格式（消费级 GeForce 的常见能力范围）
_HW_OK_PIX_FMTS = {
    "yuv420p", "yuvj420p", "nv12",
    "yuv420p10le", "p010le", "yuv420p12le",
    "yuv444p", "yuv444p10le",      # 部分型号支持 4:4:4
}

# 明确不能硬解的格式：10bit/8bit 4:2:2 在 GeForce 上必然失败
_HW_NO_PIX_FMTS = {
    "yuv422p", "yuv422p10le", "yuv422p12le", "yuvj422p",
}


def hwaccel_available(path: str) -> bool:
    """判断该素材能否走 GPU 硬解。

    两道关：
    1. 像素格式白/黑名单（4:2:2 在 GeForce 上必然失败，直接排除，省一次探测）
    2. 实际跑一小段硬解验证（防止驱动/素材特例导致白跑一趟）
    结果按 (路径, 修改时间, 大小) 缓存，同一文件只探测一次。
    """
    try:
        st = os.stat(path)
        key = (str(path), int(st.st_mtime), int(st.st_size))
    except OSError:
        key = (str(path), 0, 0)
    if key in _HW_CACHE:
        return _HW_CACHE[key]

    fmt = probe_pix_fmt(path)
    if not fmt or fmt in _HW_NO_PIX_FMTS or fmt not in _HW_OK_PIX_FMTS:
        _HW_CACHE[key] = False
        return False

    ok = _hw_smoke_test(path, fmt)
    _HW_CACHE[key] = ok
    return ok


_HW_CACHE = {}


def _hw_smoke_test(path: str, pix_fmt: str) -> bool:
    """用素材第一段做一次极小规模的硬解验证，能解出帧才算可用。"""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    is_10 = ("10le" in pix_fmt) or (pix_fmt == "p010le")
    dl_fmt = "p010le" if is_10 else "nv12"
    cmd = [
        ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error",
        "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
        "-t", "1",
        "-i", str(path),
        "-an", "-sn",
        "-vf", "scale_cuda=64:64:interp_algo=bilinear,hwdownload,format=%s,format=yuv420p" % dl_fmt,
        "-pix_fmt", "bgr24", "-f", "rawvideo", "-",
    ]
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        return False
    return bool(out.stdout) and len(out.stdout) >= 64 * 64 * 3


def iter_frames(
    path: str,
    fps: float = 0.6,
    max_side: int = 960,
    start: Optional[float] = None,
    duration: Optional[float] = None,
    extra_vf: str = "",
    use_hwaccel: Optional[bool] = None,
    keyframes_only: bool = True,
) -> Iterator[Tuple[float, np.ndarray]]:
    """顺序产出 (时间戳秒, BGR 帧)。

    参数：
        fps           —— 抽帧频率，默认 0.6（等价于每 5 秒 3 帧）
        max_side      —— 输出最长边像素，0 表示不缩放
        start         —— 起点秒数，None 从头开始
        duration      —— 处理时长秒数，None 到结尾
        extra_vf      —— 追加的滤镜，例如 "eq=brightness=0.02"
        use_hwaccel   —— True 强制硬解 / False 强制软解 / None 自动判断
        keyframes_only—— True 时只用关键帧（I 帧）做采样，跳过所有 B/P 帧

    关键帧采样说明（默认开启）：
        分析只需要清晰度、亮度、噪点这些**单帧指标**，完全可以从 I 帧拿到。
        加上 ``-skip_frame nokey`` 后 ffmpeg 只解 I 帧、直接丢弃其余帧，
        实测（每 5 秒 3 帧的采样密度）：
          · 4K 10bit 4:2:2：1.85 倍
          · 6K HEVC 10bit 4:2:0：11.68 倍
        前提是素材要有足够密的关键帧——实测 A7M4 的 4:2:2 与 6K HEVC
        都是每 0.5 秒一个 I 帧，5 秒内 10 个，足够取 3 帧。
        若素材关键帧过稀（如每秒不到 1 个），采样帧会变少，可传 False 关闭。

    硬件解码说明：
        能用 NVDEC 硬解的素材（8bit 4:2:0、10bit 4:2:0 等）实测比软解快 7 倍以上；
        10bit 4:2:2（索尼 A7M4 的 4:2:2 档）在 GeForce 上无法硬解，自动回退软解。
        与关键帧采样可以叠加：硬解负责"解得快"，跳帧负责"解得少"。

    说明：
        start 用输入侧 -ss（快速定位到关键帧），抽帧时间戳是该段的相对时间，
        调用方若需要绝对时间请自行加上 start。
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，请检查程序目录或 MIXCUT_FFMPEG 环境变量。")

    meta = probe_media(path)
    if not meta:
        return
    width, height, _ = meta
    out_w, out_h = scaled_size(width, height, max_side)
    if out_w < 2 or out_h < 2:
        return

    pix_fmt = probe_pix_fmt(path)
    if use_hwaccel is None:
        use_hwaccel = hwaccel_available(path)
    elif use_hwaccel and pix_fmt in _HW_NO_PIX_FMTS:
        # 显式要求硬解但格式明确不支持（如 10bit 4:2:2）：
        # 直接走软解，避免 ffmpeg 报错后一帧都产不出来。
        use_hwaccel = False

    fps = max(0.01, float(fps))
    extra = ("," + extra_vf) if extra_vf else ""

    if use_hwaccel:
        hw_args = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        dl_fmt = "p010le" if ("10le" in pix_fmt or pix_fmt == "p010le") else "nv12"
        # 缩放走 scale_cuda（在显存里完成），最后下载回内存转成 yuv420p
        vf = ("fps={:.6f},scale_cuda={}:{}:interp_algo=bilinear,"
              "hwdownload,format={},format=yuv420p{}").format(
            fps, out_w, out_h, dl_fmt, extra)
    else:
        hw_args = []
        vf = "fps={:.6f},scale={}:{}:flags=fast_bilinear{}".format(
            fps, out_w, out_h, extra)

    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error"]
    # 只解关键帧：ffmpeg 会跳过所有 B/P 帧，不解码它们的残差。
    # 实测 6K 4:2:0 提速 11.68 倍、4K 4:2:2 提速 1.85 倍（每 5 秒 3 帧的密度下）。
    if keyframes_only:
        cmd += ["-skip_frame", "nokey"]
    cmd += hw_args
    if start is not None and start > 0:
        cmd += ["-ss", "{:.3f}".format(float(start))]
    cmd += ["-i", str(path)]
    if duration is not None and duration > 0:
        cmd += ["-t", "{:.3f}".format(float(duration))]
    cmd += [
        "-an", "-sn",
        "-vf", vf,
        "-pix_fmt", "bgr24",
        "-f", "rawvideo",
        "-",
    ]

    frame_bytes = out_w * out_h * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    index = 0
    try:
        while True:
            chunk = proc.stdout.read(frame_bytes)
            if not chunk or len(chunk) < frame_bytes:
                break
            frame = np.frombuffer(chunk, dtype=np.uint8).reshape(out_h, out_w, 3)
            timestamp = index / fps
            index += 1
            yield timestamp, frame
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def count_frames(path: str, fps: float = 0.6,
                 start: Optional[float] = None,
                 duration: Optional[float] = None) -> int:
    """预估抽帧数量，用于进度条；不实际解码。"""
    meta = probe_media(path)
    if not meta:
        return 0
    _, _, total = meta
    span = total
    if start is not None and start > 0:
        span = max(0.0, span - float(start))
    if duration is not None and duration > 0:
        span = min(span, float(duration))
    if span <= 0:
        return 0
    return max(1, int(round(span * max(0.01, float(fps)))))
