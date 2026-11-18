"""
MixCut · FFmpeg 检测工具

封装 ffmpeg / ffprobe 的定位与三类废镜头检测：
  - 黑屏   blackdetect
  - 静止画面 freezedetect（长时间不动 / 对墙 / 虚焦近似）
  - 静音段 silencedetect

所有检测函数都返回 [(start, end), ...]，单位秒，浮点。
模块只做“检测 + 解析”，不决定如何剔除（由 analysis 模块负责）。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# Windows 下隐藏子进程控制台窗口，避免分析时疯狂弹出黑窗
NO_WINDOW_FLAGS = 0x08000000 if sys.platform == "win32" else 0


# ---------------------------------------------------------
# 可执行文件定位
# ---------------------------------------------------------

def app_base_dir():
    """源码运行时为项目根目录；打包后为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # 本文件位于 <root>/mixcut/ffmpeg_tools.py
    return Path(__file__).resolve().parent.parent


def _candidate_paths(name):
    exe = f"{name}.exe" if sys.platform == "win32" else name
    base = app_base_dir()
    meipass = Path(getattr(sys, "_MEIPASS", base))

    candidates = [
        base / exe,
        base / "ffmpeg" / "bin" / exe,
        meipass / exe,
    ]

    # 兼容自带的 ffmpeg-<日期>-<hash>-win64-static/bin 目录
    try:
        for static_dir in base.glob("ffmpeg*/bin"):
            candidates.append(static_dir / exe)
    except OSError:
        pass

    return candidates


def find_tool(name):
    # 允许用环境变量显式指定
    env_key = f"MIXCUT_{name.upper()}"
    env_path = os.environ.get(env_key)
    if env_path and Path(env_path).is_file():
        return env_path

    for candidate in _candidate_paths(name):
        if candidate.is_file():
            return str(candidate)

    return shutil.which(name)


def find_ffmpeg():
    return find_tool("ffmpeg")


def find_ffprobe():
    return find_tool("ffprobe")


# ---------------------------------------------------------
# 子进程封装
# ---------------------------------------------------------

def _run_ffmpeg(args):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "找不到 ffmpeg。请把 ffmpeg 放到程序目录，或设置环境变量 MIXCUT_FFMPEG。"
        )
    cmd = [ffmpeg, "-hide_banner", "-nostats"] + args
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW_FLAGS,
    )
    # 检测滤镜即使返回非 0（例如 -f null 被中断）也可能已输出结果，
    # 这里把 stderr 交给上层解析，不直接抛错。
    return proc.returncode, proc.stderr or ""


def _parse_intervals(text, start_key, end_key, clip_duration=None):
    """
    从 ffmpeg 输出里解析成对的 start/end。
    兼容两种输出：
      - blackdetect：start/end 打在同一行
      - freezedetect / silencedetect：start、end 分属不同事件行
    也兼容带前缀的行，例如 “lavfi.freezedetect.freeze_start: 2.0”。
    """
    intervals = []
    open_start = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        s_val = _extract_number(line, start_key)
        e_val = _extract_number(line, end_key)

        if s_val is not None and e_val is not None:
            # 同一行同时给出起止
            if e_val > s_val:
                intervals.append((s_val, e_val))
            open_start = None

        elif s_val is not None:
            open_start = s_val

        elif e_val is not None and open_start is not None:
            if e_val > open_start:
                intervals.append((open_start, e_val))
            open_start = None

    # 只有 start 没有 end（区间延续到文件结尾）
    if open_start is not None and clip_duration and clip_duration > open_start:
        intervals.append((open_start, clip_duration))

    return intervals


def _extract_number(line, key):
    token = f"{key}:"
    pos = line.find(token)
    if pos < 0:
        return None
    rest = line[pos + len(token):].strip()
    # 取行内第一个数字，忽略其后的其它字段
    num = ""
    for ch in rest:
        if ch.isdigit() or ch in ".-+eE":
            num += ch
        else:
            break
    try:
        return float(num)
    except ValueError:
        return None


# ---------------------------------------------------------
# 时长探测
# ---------------------------------------------------------

def probe_duration(path):
    ffprobe = find_ffprobe()
    if not ffprobe:
        raise RuntimeError("找不到 ffprobe。")
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW_FLAGS,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:] or "ffprobe 执行失败")
    data = json.loads(proc.stdout)
    return float(data["format"]["duration"])


# ---------------------------------------------------------
# 三类检测
# ---------------------------------------------------------

def detect_black(path, min_duration=0.5, pixel_threshold=0.10, picture_threshold=0.98):
    """
    黑屏检测。
    pixel_threshold：单像素低于该亮度视为黑（0~1）。
    picture_threshold：画面中黑像素占比超过该值判定为黑屏。
    """
    flt = (
        f"blackdetect=d={float(min_duration)}"
        f":pix_th={float(pixel_threshold)}"
        f":pic_th={float(picture_threshold)}"
    )
    duration = None
    try:
        duration = probe_duration(path)
    except Exception:
        pass
    _, stderr = _run_ffmpeg(["-i", str(path), "-vf", flt, "-an", "-f", "null", "-"])
    return _parse_intervals(stderr, "black_start", "black_end", duration)


def detect_freeze(path, min_duration=1.0, noise_threshold=0.001):
    """
    静止画面检测（freezedetect）。
    noise_threshold：帧间差异低于该值视为无运动。
    """
    flt = f"freezedetect=n={float(noise_threshold)}:d={float(min_duration)}"
    duration = None
    try:
        duration = probe_duration(path)
    except Exception:
        pass
    _, stderr = _run_ffmpeg(["-i", str(path), "-vf", flt, "-f", "null", "-"])
    return _parse_intervals(stderr, "freeze_start", "freeze_end", duration)


def detect_silence(path, min_duration=0.5, noise_db=-35.0):
    """
    静音 / 空音频检测。
    noise_db：音量低于该分贝值视为安静。
    没有音轨的文件返回空列表，不把无音轨误判成整段静音。
    """
    flt = f"silencedetect=n={float(noise_db)}dB:d={float(min_duration)}"
    duration = None
    try:
        duration = probe_duration(path)
    except Exception:
        pass
    _, stderr = _run_ffmpeg(["-i", str(path), "-af", flt, "-f", "null", "-"])
    return _parse_intervals(stderr, "silence_start", "silence_end", duration)


# 硬件加速模式缓存（按素材路径，会话内只探测一次）
_HWACCEL_CACHE = {}


def resolve_gpu_mode(path):
    """
    返回 "gpu" 或 "cpu"。
    - 环境变量 MIXCUT_HWACCEL："0"/"off"/"none" 强制 cpu；"cuda"/"nvdec" 强制 gpu。
    - 自动：用严格 GPU 链（NVDEC 输出 cuda → scale_cuda → hwdownload）跑首帧，
      成功才认定 gpu。旧版 NVDEC 对宽 >4096（如 6K）会失败，自动回退 cpu。
    """
    override = os.environ.get("MIXCUT_HWACCEL", "").strip().lower()
    if override in ("0", "off", "none", "no"):
        return "cpu"
    if override in ("cuda", "nvdec"):
        return "gpu"

    key = str(Path(path).resolve())
    if key in _HWACCEL_CACHE:
        return _HWACCEL_CACHE[key]

    ffmpeg = find_ffmpeg()
    mode = "cpu"
    if ffmpeg:
        probe = subprocess.run(
            [ffmpeg, "-hide_banner",
             "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
             "-i", str(path),
             "-vf", "scale_cuda=640:-2,hwdownload,format=nv12",
             "-frames:v", "1", "-an", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW_FLAGS,
        )
        if probe.returncode == 0:
            mode = "gpu"
    _HWACCEL_CACHE[key] = mode
    return mode


def detect_all(path, options, duration=None, progress_cb=None, cancel_check=None,
               force_mode=None):
    """
    单进程一次性完成黑屏/静止/静音检测（旧逻辑是最多 3 个 ffmpeg 各解码一遍）。

    - 视频滤镜前先缩小到 640 宽：黑屏/静止判定不需要原始高分辨率，显著降低开销。
    - 用 -progress 回传真实进度（0~100），避免长视频界面看起来“卡死”。
    - force_mode: "cpu"/"gpu" 强制硬件模式；None 时自动选择。
      强制 gpu 但环境不满足会自动回退 cpu。
    返回 {"black": [...], "freeze": [...], "silence": [...]}，元素均为 (start, end)。
    """
    import threading

    if duration is None:
        try:
            duration = probe_duration(path)
        except Exception:
            duration = None

    video_on = options.get("video_detect", True)
    audio_on = options.get("audio_detect", True)

    if force_mode == "cpu":
        # 用户强制 CPU：完全不碰硬件解码
        gpu_mode = "cpu"
    elif force_mode == "gpu":
        # 强制 GPU：仍用真实链路验证，失败则回退 CPU
        gpu_mode = resolve_gpu_mode(path)
    else:
        # 自动选择 GPU（≤4K 走 NVDEC）或 CPU（6K 等超宽自动回退）
        gpu_mode = resolve_gpu_mode(path) if video_on else "cpu"

    vf_chain = []
    if video_on:
        # 采样式检测：scale 到 480 宽 + fps=2，blackdetect/freezedetect 每秒
        # 只看 2 帧而非原帧率。黑屏/静止是持续事件，采样足够，4K/10bit
        # 高码率素材的检测时间可降一个数量级。
        if gpu_mode == "gpu":
            vf_chain.append("scale_cuda=480:-2,hwdownload,format=nv12,fps=2")
        else:
            vf_chain.append("scale=480:-2,fps=2")
        if options.get("detect_black", True):
            vf_chain.append(
                "blackdetect=d=%s:pix_th=%s:pic_th=%s"
                % (float(options["black_min_duration"]),
                   float(options["black_pixel_threshold"]),
                   float(options["black_picture_threshold"]))
            )
        if options.get("detect_freeze", True):
            vf_chain.append(
                "freezedetect=n=%s:d=%s"
                % (float(options["freeze_noise"]),
                   float(options["freeze_min_duration"]))
            )

    af_chain = None
    has_audio = True
    if audio_on:
        has_audio = has_audio_stream(path)
        if has_audio:
            af_chain = "silencedetect=n=%sdB:d=%s" % (
                float(options["silence_noise_db"]),
                float(options["silence_min_duration"]))

    args = ["-i", str(path)]
    if vf_chain:
        args += ["-vf", ",".join(vf_chain)]
    else:
        # 视频检测关闭：-vn 彻底跳过视频流，否则 ffmpeg 仍会解码 4K/10bit
        # 视频流到 null muxer，白等几十秒。
        args += ["-vn"]
    if af_chain:
        args += ["-af", af_chain]
    # -threads 0 让 ffmpeg 解码用满 CPU 核
    args += ["-threads", "0", "-f", "null", "-", "-progress", "pipe:1", "-nostats"]

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg。")

    # GPU 模式输出保留在显存（配合 scale_cuda）；CPU 模式无硬件参数
    hw_args = (["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
               if gpu_mode == "gpu" else [])

    proc = subprocess.Popen(
        [ffmpeg, "-hide_banner"] + hw_args + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW_FLAGS,
    )

    err_buf = []

    def _drain_stderr():
        # 滤镜事件在 stderr，单独线程读，避免管道写满阻塞
        for line in proc.stderr:
            err_buf.append(line)

    err_thread = threading.Thread(target=_drain_stderr, daemon=True)
    err_thread.start()

    last_pct = -1
    for line in proc.stdout:
        if cancel_check and cancel_check():
            proc.kill()
            break
        line = line.strip()
        if line.startswith("out_time_us="):
            try:
                current = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                current = None
            if current is not None and duration:
                pct = max(0, min(99, int(current / duration * 100)))
                if pct != last_pct:
                    if progress_cb:
                        progress_cb(pct)
                    last_pct = pct

    proc.wait()
    err_thread.join(timeout=5)
    err_text = "".join(err_buf)

    result = {"black": [], "freeze": [], "silence": []}
    if video_on:
        if options.get("detect_black", True):
            result["black"] = _parse_intervals(
                err_text, "black_start", "black_end", duration)
        if options.get("detect_freeze", True):
            result["freeze"] = _parse_intervals(
                err_text, "freeze_start", "freeze_end", duration)
    if audio_on and has_audio and af_chain:
        result["silence"] = _parse_intervals(
            err_text, "silence_start", "silence_end", duration)

    return result


def probe_details(path):
    """
    详细探测：返回格式信息 + 视频/音频流的关键参数。
    结构 {"format": {...}, "video": {...}|None, "audio": {...}|None,
          "file_size": int, "path": str}
    """
    ffprobe = find_ffprobe()
    if not ffprobe:
        raise RuntimeError("找不到 ffprobe。")
    cmd = [
        ffprobe, "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW_FLAGS,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:] or "ffprobe 执行失败")

    data = json.loads(proc.stdout)
    fmt = data.get("format", {})
    video = None
    audio = None

    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video is None:
            video = {
                "codec": stream.get("codec_name"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "fps": _parse_fps(stream.get("r_frame_rate")),
                "pix_fmt": stream.get("pix_fmt"),
                "bit_rate": _to_int(stream.get("bit_rate")),
                "nb_frames": _to_int(stream.get("nb_frames")),
            }
        elif codec_type == "audio" and audio is None:
            audio = {
                "codec": stream.get("codec_name"),
                "channels": stream.get("channels"),
                "sample_rate": _to_int(stream.get("sample_rate")),
                "bit_rate": _to_int(stream.get("bit_rate")),
            }

    return {
        "path": str(path),
        "file_size": _to_int(fmt.get("size")),
        "format": {
            "duration": _to_float(fmt.get("duration")),
            "bit_rate": _to_int(fmt.get("bit_rate")),
            "format_name": fmt.get("format_name"),
        },
        "video": video,
        "audio": audio,
    }


# ---------------------------------------------------------
# 缩略图 / 预览片段
# ---------------------------------------------------------

# 缩略图缓存目录（放在系统临时目录下，程序退出后由系统清理；
# 也可以在启动时调用 clear_thumbnail_cache() 主动清空）
THUMB_DIR_NAME = "mixcut_thumbs"


def thumbnail_dir():
    folder = Path(tempfile.gettempdir()) / THUMB_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _thumb_cache_path(path, width, height, seek_seconds):
    """缓存文件名 = 素材路径 + 文件签名 + 输出尺寸的哈希，素材变了自动换新。"""
    p = Path(path)
    try:
        stat = p.stat()
        signature = f"{p}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        signature = str(p)
    digest = hashlib.sha1(
        f"{signature}|{width}x{height}|{seek_seconds:.3f}".encode("utf-8")
    ).hexdigest()
    return thumbnail_dir() / f"{digest}.jpg"


def make_thumbnail(path, seek_seconds=1.0, width=320, height=180,
                   quality=4):
    """用 ffmpeg 从视频里取一帧真实画面，裁成指定比例后缓存为 JPEG。

    返回缩略图文件路径；抽帧失败返回 None（上层显示“无缩略图”，不造假图）。
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    out = _thumb_cache_path(path, width, height, seek_seconds)
    if out.is_file() and out.stat().st_size > 0:
        return str(out)

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, float(seek_seconds)):.3f}",
        "-i", str(path),
        "-frames:v", "1", "-vf", vf, "-q:v", str(quality),
        "-f", "image2", str(out),
    ]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=NO_WINDOW_FLAGS,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        try:
            out.unlink()
        except OSError:
            pass
        return None
    return str(out)


def make_preview_clip(path, start, duration, max_height=240):
    """把素材里的一段切成小预览片段（用于草稿结果预览）。

    只做缩放与转码，画面内容来自真实素材，返回片段文件路径。
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg or not duration or duration <= 0:
        return None
    out = _thumb_cache_path(path, 0, max_height, float(start)) .with_suffix(".mp4")
    if out.is_file() and out.stat().st_size > 0:
        return str(out)
    vf = f"scale=-2:{max_height}"
    base = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, float(start)):.3f}",
        "-i", str(path), "-t", f"{float(duration):.3f}",
        "-vf", vf, "-an",
    ]
    # 优先 NVENC 硬件编码，失败自动回退 libx264
    for encoder, extra in (
        ("h264_nvenc", ["-preset", "p4", "-tune", "hq",
                        "-rc", "vbr", "-cq", "23",
                        "-b:v", "2M", "-maxrate", "4M"]),
        ("libx264", ["-preset", "ultrafast", "-crf", "23"]),
    ):
        cmd = base + ["-c:v", encoder] + extra + ["-pix_fmt", "yuv420p", str(out)]
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=NO_WINDOW_FLAGS,
            )
        except Exception:
            continue
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 0:
            return str(out)
        try:
            out.unlink()
        except OSError:
            pass
    return None


def clear_thumbnail_cache():
    """清空缩略图/预览缓存。"""
    folder = thumbnail_dir()
    removed = 0
    for item in folder.glob("*"):
        try:
            item.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _parse_fps(rate):
    try:
        num, den = rate.split("/")
        den = float(den)
        if den == 0:
            return None
        return round(float(num) / den, 3)
    except Exception:
        return None


def _to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def has_audio_stream(path):
    """判断文件是否包含音轨；无法探测时保守假定存在音轨。"""
    ffprobe = find_ffprobe()
    if not ffprobe:
        return True
    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "json",
        str(path),
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW_FLAGS,
    )
    if proc.returncode != 0:
        return True
    try:
        return bool(json.loads(proc.stdout).get("streams"))
    except Exception:
        return True
