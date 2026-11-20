"""媒体层：ffprobe 探测、缩略图、波形、导出参数构建。

所有对外部程序的调用都走 find_tool()，优先使用工程自带的
ffmpeg-20200831-...-win64-static\\bin，其次 PATH，最后 ffmpeg 目录。
任何环节失败都返回空结果而不是抛异常，保证界面不会因坏文件崩溃。
"""

from __future__ import annotations

import array
import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from . import MediaAsset, media_kind

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_BASE_DIR = Path(__file__).resolve().parent.parent


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _BASE_DIR


def find_tool(name: str) -> str | None:
    suffix = ".exe" if sys.platform == "win32" else ""
    base = app_base_dir()
    candidates = [
        base / f"{name}{suffix}",
        base / "ffmpeg" / "bin" / f"{name}{suffix}",
        base / "third_party" / "ffmpeg" / "bin" / f"{name}{suffix}",
        Path(getattr(sys, "_MEIPASS", base)) / f"{name}{suffix}",
    ]
    # 工程自带的 ffmpeg-*-win64-static\\bin
    candidates.extend(sorted(base.glob(f"ffmpeg-*-win64-static/bin/{name}{suffix}")))
    candidates.extend(sorted(base.glob(f"ffmpeg*/bin/{name}{suffix}")))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def tools_available() -> tuple[bool, str]:
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        return False, "找不到 ffmpeg.exe。请把 ffmpeg 放到程序目录或加入 PATH。"
    return True, ffmpeg


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


def parse_fps(text: str) -> Fraction:
    """解析 "30000/1001" / "30" 形式的帧率；无法解析或非正数时返回 30/1。"""
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            if int(den) == 0 or int(num) == 0:
                return Fraction(30, 1)
            value = Fraction(int(num), int(den))
        else:
            value = Fraction(text)
    except (ValueError, ZeroDivisionError, TypeError):
        return Fraction(30, 1)
    return value if value > 0 else Fraction(30, 1)


def find_native() -> str | None:
    """用 VS/MSVC 编译出的原生辅助工具（Media Foundation + WIC）。

    存在时抽帧与探测走它：不启动 ffmpeg 子进程，速度快很多；
    不存在时调用方自动退回 ffmpeg，不影响功能。
    """
    base = app_base_dir()
    for candidate in (base / "build" / "mixcut_native.exe", base / "mixcut_native.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def native_frame(path: str, out_path: str, at_ms: int = 0, max_width: int = 0) -> bool:
    exe = find_native()
    if not exe:
        return False
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "frame", str(path), str(max(0, int(at_ms))), str(out)]
    if max_width > 0:
        cmd.append(str(int(max_width)))
    try:
        proc = _run(cmd, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and out.is_file() and out.stat().st_size > 0


def native_probe(path: str) -> MediaAsset | None:
    exe = find_native()
    if not exe or not Path(path).is_file():
        return None
    try:
        proc = _run([exe, "probe", str(path)], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    values: dict[str, int] = {}
    for token in (proc.stdout or "").split():
        if "=" not in token:
            continue
        key, _, raw = token.partition("=")
        try:
            values[key] = int(raw)
        except ValueError:
            continue
    if "duration_ms" not in values or values.get("duration_ms", 0) <= 0:
        return None
    duration = values["duration_ms"]
    width = values.get("width", 0)
    height = values.get("height", 0)
    fps_num = values.get("fps_num", 30)
    fps_den = values.get("fps_den", 1) or 1
    has_video = width > 0 and height > 0
    has_audio = bool(values.get("has_audio", 0))
    kind = media_kind(path)
    if has_video and not has_audio:
        kind = "video"
    elif has_audio and not has_video:
        kind = "audio"
    return MediaAsset(
        path=str(Path(path)),
        kind=kind,
        duration=duration,
        width=width,
        height=height,
        fps=f"{fps_num}/{fps_den}",
        has_video=has_video,
        has_audio=has_audio,
        codec="",
    )


def probe(path: str) -> MediaAsset | None:
    """读取媒体信息：优先原生探测，失败再退回 ffprobe；都失败返回 None。"""
    native = native_probe(path)
    if native is not None:
        return native
    ffprobe = find_tool("ffprobe")
    if not ffprobe or not Path(path).is_file():
        return None
    try:
        proc = _run([
            ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", path,
        ], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams
                  if s.get("codec_type") == "video" and s.get("width")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration = int(float(fmt.get("duration", 0)) * 1000)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0 and video:
        try:
            duration = int(float(video.get("duration", 0)) * 1000)
        except (TypeError, ValueError):
            duration = 0

    kind = media_kind(path)
    if video and not audio:
        kind = "video"
    elif audio and not video:
        kind = "audio"

    fps = parse_fps(video.get("avg_frame_rate", "")) if video else Fraction(30, 1)

    return MediaAsset(
        path=str(Path(path)),
        kind=kind,
        duration=duration,
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
        fps=f"{fps.numerator}/{fps.denominator}",
        has_video=video is not None,
        has_audio=audio is not None,
        codec=(video or audio or {}).get("codec_name", ""),
    )


def extract_thumbnail(path: str, out_path: str, at_ms: int = 0, size: int = 160) -> bool:
    # 原生工具优先：速度快且不产生 ffmpeg 子进程
    if native_frame(path, out_path, max(0, at_ms), size):
        return True
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        return False
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = _run([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0, at_ms) / 1000:.3f}", "-i", path,
            "-frames:v", "1",
            "-vf", f"scale={size}:{size}:force_original_aspect_ratio=increase,"
                   f"crop={size}:{size}",
            str(out),
        ], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and out.is_file()


def extract_waveform(path: str, buckets: int = 1200, sample_rate: int = 8000) -> list[float]:
    """解码为单声道 PCM 并压缩成归一化包络；失败返回空列表。"""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        return []
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path,
             "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=90, creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    raw = proc.stdout or b""
    if not raw:
        return []

    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return []

    per = max(1, len(samples) // buckets)
    peaks: list[float] = []
    for start in range(0, len(samples), per):
        chunk = samples[start:start + per]
        if not chunk:
            break
        peak = max(abs(v) for v in chunk) / 32768.0
        peaks.append(peak)
        if len(peaks) >= buckets:
            break
    top = max(peaks, default=0.0)
    if top > 1e-4:
        peaks = [min(1.0, p / top) for p in peaks]
    return peaks


def grade_filters(grade: dict | None) -> list[str]:
    """把调色参数转成 ffmpeg 滤镜，导出/预览共用同一套。

    顺序固定：曝光 → eq（亮度/对比度/饱和） → colorchannelmixer（色温/色调）
    → unsharp（锐度）。曝光必须在 eq 之前：先把线性增益打到原信号上，再走
    后续曲线，才不会被 eq 的亮度偏移吞掉（B-42）。

    本机自带 FFmpeg（2020-08-31）没有 `exposure` 滤镜，故曝光用 `lutyuv`
    对 Y 通道做 `val*gain` 的线性增益（亮度乘性、色度不动），EV 与增益换算
    gain = 2^EV。
    """
    if not grade:
        return []
    out: list[str] = []

    exposure = float(grade.get("exposure", 0.0) or 0.0)
    if abs(exposure) > 1e-4:
        ev = max(-4.0, min(4.0, exposure))
        gain = 2.0 ** ev
        # 以 16 为黑电平做乘性增益（YUV limited range），避免把暗部直接压死；
        # 实测 -1EV 时中灰 128 → 64，与线性光圈换算一致。
        out.append(
            f"lutyuv=y=clip((val-16)*{gain:.4f}+16\\,16\\,235):u=val:v=val")

    brightness = float(grade.get("brightness", 0.0) or 0.0)
    contrast = float(grade.get("contrast", 1.0) or 1.0)
    saturation = float(grade.get("saturation", 1.0) or 1.0)
    if any((abs(brightness) > 1e-4, abs(contrast - 1.0) > 1e-4,
            abs(saturation - 1.0) > 1e-4)):
        out.append(
            f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:"
            f"saturation={saturation:.3f}")

    temperature = float(grade.get("temperature", 0.0) or 0.0)
    tint = float(grade.get("tint", 0.0) or 0.0)
    if abs(temperature) > 1e-4 or abs(tint) > 1e-4:
        # 白平衡用通道增益（colorchannelmixer）而不是 colorbalance：
        # 实测 colorbalance 在本机 FFmpeg 上会做保亮度色相搬移，暖调/色调
        # 对整帧几乎无位移（红蓝均值只差 0.1/255），达不到可用强度。
        # 通道增益的语义也更接近相机白平衡：色温高低 = 红/蓝通道反向增益，
        # 色调 = 绿通道与红蓝的反向增益。
        t = max(-1.0, min(1.0, temperature)) * 0.30      # 红 + / 蓝 -
        u = max(-50.0, min(50.0, tint)) / 50.0 * 0.25    # 绿 + / 红蓝 -
        rr = (1.0 + t) * (1.0 - u * 0.5)
        gg = 1.0 + u
        bb = (1.0 - t) * (1.0 - u * 0.5)
        out.append(
            f"colorchannelmixer=rr={max(0.0, rr):.4f}:gg={max(0.0, gg):.4f}:"
            f"bb={max(0.0, bb):.4f}")

    sharpness = float(grade.get("sharpness", 0.0) or 0.0)
    if abs(sharpness) > 1e-4:
        amount = max(0.0, min(100.0, sharpness)) / 100.0 * 1.5
        out.append(f"unsharp=5:5:{amount:.3f}:5:5:0.0")
    return out


def _clip_video_chain(source_in: int, length: int, speed: float, grade: dict | None,
                      width: int, height: int, fps_text: str) -> list[str]:
    """单个片段从源裁剪到统一画幅的滤镜链（不含 overlay 相关处理）。"""
    speed = speed if speed > 0.01 else 1.0
    # 时间线上 1 秒对应素材内 speed 秒，因此源裁剪长度要乘上速度
    source_len = int(max(1, length) * speed)
    chain = [f"trim=start={source_in / 1000:.3f}:duration={source_len / 1000:.3f}"]
    chain.append("setpts=PTS-STARTPTS" if abs(speed - 1.0) < 1e-4
                 else f"setpts=(PTS-STARTPTS)/{speed:.4f}")

    chain += grade_filters(grade)

    chain.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
    chain.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
    chain.append("setsar=1")
    chain.append(f"fps={fps_text}")
    # 素材比时间线片段短（例如 2 倍速却按原时长摆放）时，用末帧补足，
    # 再把结果精确裁到片段时长，保证拼接后的总长与时间线一致。
    chain.append(f"tpad=stop_mode=clone:stop_duration={length / 1000:.3f}")
    chain.append(f"trim=duration={length / 1000:.3f}")
    return chain


def _normalize_video_segments(
        video_segments: list[tuple]) -> list[tuple[str, int, int, int]]:
    """把主轨片段统一成 (路径, 源入点, 时长, 时间线绝对起点)。

    兼容两种写法：3 元组（旧口径，按顺序首尾相接）与 4 元组（带绝对起点）。
    同轨重叠没有明确定义（B-10 归项目 05），这里不退化成负空隙，
    而是把该段顺延到上一段结束处，保证导出不会出现空标签/负时长。
    """
    out: list[tuple[str, int, int, int]] = []
    cursor = 0
    for item in video_segments:
        path, source_in, length = item[0], int(item[1]), int(item[2])
        start = int(item[3]) if len(item) >= 4 else cursor
        start = max(cursor, max(0, start))
        out.append((str(path), source_in, max(1, length), start))
        cursor = start + max(1, length)
    return out


def _base_track_plan(segments: list[tuple[str, int, int, int]],
                     duration: int) -> list[tuple[str, int]]:
    """把主轨片段排成"片段 + 黑场空隙"的拼接计划，总长严格等于时间线时长。

    返回 [("clip", 片段序号)] / [("gap", 空隙毫秒)] 的有序列表。
    """
    plan: list[tuple[str, int]] = []
    cursor = 0
    for index, (_path, _source_in, length, start) in enumerate(segments):
        if start > cursor:
            plan.append(("gap", start - cursor))
        plan.append(("clip", index))
        cursor = start + length
    if duration > cursor:
        plan.append(("gap", duration - cursor))
    return plan


def build_export_command_full(
    video_segments: list[tuple],
    audio_segments: list[tuple[str, int, int, int]],
    output: str,
    *,
    width: int,
    height: int,
    fps: Fraction,
    duration: int,
    crf: int = 18,
    preset: str = "medium",
    prores: bool = False,
    include_audio: bool = True,
    grades: list[dict] | None = None,
    speeds: list[float] | None = None,
    volumes: list[float] | None = None,
    overlays: list[dict] | None = None,
) -> list[str]:
    """完整的导出命令。

    参数
    ----
    video_segments: [(路径, 源入点, 时长)] 或 [(路径, 源入点, 时长, 绝对起点)]
                    —— 主视频轨（底层）；给绝对起点时空隙会显式补黑场
    audio_segments: [(路径, 源入点, 时长, 轨道位置)] —— 混音，按位置延迟
    duration:       导出总时长（毫秒）
    grades:         逐段的调色参数（与 video_segments 对齐，缺省为不调色）
    speeds:         逐段的播放速度（与 video_segments 对齐）
    volumes:        逐段音频音量（与 audio_segments 对齐）
    overlays:       上层视频轨的片段，dict 键为
                    path / source_in / duration / start / grade / speed / opacity

    画面：主轨按时间线绝对时间逐段 trim + 变速 + 调色 + 缩放补边 + 统一帧率，
          空隙用 color=black 补足，全部按序 concat —— 输出画面时间 == 时间线时间；
          再把上层轨片段按绝对时间点 overlay 叠加（带不透明度）。
    声音：逐段 atrim + 音量 + adelay 定位（与画面同一坐标系）-> amix。

    说明：不使用输入级 -ss，统一在滤镜里 trim，这样同一素材被多次使用
    （例如重复镜头）时每个输入都从 0 时刻独立裁剪，语义更简单可靠。
    """
    ffmpeg = find_tool("ffmpeg") or "ffmpeg"
    fps_value = float(fps.numerator) / float(fps.denominator)
    fps_text = f"{fps_value:.6f}".rstrip("0").rstrip(".")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-stats"]

    overlays = overlays or []
    base_segments = _normalize_video_segments(video_segments)
    overlay_inputs = [str(item.get("path", "")) for item in overlays]
    input_paths: list[str] = [path for path, _in, _len, _start in base_segments]
    input_paths.extend(overlay_inputs)
    if include_audio:
        input_paths.extend(path for path, _in, _len, _start in audio_segments)
    for path in input_paths:
        cmd += ["-i", path]

    filters: list[str] = []

    # ---- 主视频轨（底层）：片段 + 黑场空隙，按绝对时间拼接 ----
    video_input_count = len(base_segments)
    if video_input_count:
        plan = _base_track_plan(base_segments, duration)
        labels: list[str] = []
        gap_index = 0
        for kind, value in plan:
            if kind == "gap":
                filters.append(
                    f"color=c=black:s={width}x{height}:r={fps_text}"
                    f":d={value / 1000:.3f},format=yuv420p[g{gap_index}]")
                labels.append(f"[g{gap_index}]")
                gap_index += 1
                continue
            index = value
            grade = grades[index] if grades and index < len(grades) else None
            speed = float(speeds[index]) if speeds and index < len(speeds) else 1.0
            chain = _clip_video_chain(int(base_segments[index][1]), int(base_segments[index][2]),
                                      speed, grade, width, height, fps_text)
            chain.append("format=yuv420p")
            filters.append(f"[{index}:v]" + ",".join(chain) + f"[c{index}]")
            labels.append(f"[c{index}]")
        if len(labels) == 1:
            filters.append(f"{labels[0]}null[vbase]")
        else:
            filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[vbase]")
    else:
        filters.append(
            f"color=c=black:s={width}x{height}:r={fps_text}:d={duration / 1000:.3f},"
            f"format=yuv420p[vbase]"
        )

    # ---- 上层视频轨（叠加）----
    current = "[vbase]"
    for order, item in enumerate(overlays):
        index = video_input_count + order
        chain = _clip_video_chain(
            int(item.get("source_in", 0)), int(item.get("duration", 1000)),
            float(item.get("speed", 1.0) or 1.0), item.get("grade"),
            width, height, fps_text)
        chain.append("format=rgba")
        opacity = max(0.0, min(1.0, float(item.get("opacity", 1.0) or 0.0)))
        if opacity < 0.999:
            chain.append(f"colorchannelmixer=aa={opacity:.3f}")
        start = max(0, int(item.get("start", 0)))
        chain.append(f"setpts=PTS-STARTPTS+{start / 1000:.3f}/TB")
        filters.append(f"[{index}:v]" + ",".join(chain) + f"[o{order}]")
        label = "[vout]" if order == len(overlays) - 1 else f"[vx{order}]"
        filters.append(f"{current}[o{order}]overlay=eof_action=pass:format=auto{label}")
        current = label

    if not overlays:
        filters.append(f"{current}null[vout]")

    # ---- 声音 ----
    audio_label = ""
    audio_base = video_input_count + len(overlays)
    if include_audio and audio_segments:
        mixes: list[str] = []
        for offset, (_path, source_in, length, start) in enumerate(audio_segments):
            index = audio_base + offset
            gain = float(volumes[offset]) if volumes and offset < len(volumes) else 1.0
            filters.append(
                f"[{index}:a]atrim=start={source_in / 1000:.3f}"
                f":duration={length / 1000:.3f},asetpts=PTS-STARTPTS,"
                f"adelay={int(start)}|{int(start)},volume={max(0.0, gain):.3f}[a{offset}]"
            )
            mixes.append(f"[a{offset}]")
        if len(mixes) == 1:
            filters.append(f"{mixes[0]}anull[aout]")
        else:
            # 旧版 ffmpeg（项目自带 2020 构建）没有 amix 的 normalize 选项，
            # 默认 normalize 会按 1/N 衰减，这里用 volume=N 补回来。
            count = len(mixes)
            filters.append(
                "".join(mixes) + f"amix=inputs={count}:duration=longest,volume={count}[aout]"
            )
        audio_label = "[aout]"

    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
    cmd += ["-map", audio_label] if audio_label else ["-an"]

    if prores:
        cmd += ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
        if audio_label:
            cmd += ["-c:a", "pcm_s24le"]
    else:
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
        if audio_label:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-t", f"{duration / 1000:.3f}", "-movflags", "+faststart", output]
    return cmd


def parse_progress_ms(text: str) -> int | None:
    """从 ffmpeg -stats 输出中解析已编码时长（毫秒）。

    ffmpeg 输出的形如 "frame=  120 fps= 30 q=... time=00:00:04.00 bitrate=..."，
    这里只取 time= 字段，解析失败返回 None（调用方保持上一次进度）。
    """
    if "time=" not in text:
        return None
    fragment = text.split("time=", 1)[1].split()[0]
    if fragment.startswith("-"):
        return None
    parts = fragment.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)
        if len(parts) == 2:
            minutes, seconds = parts
            return int((int(minutes) * 60 + float(seconds)) * 1000)
        return int(float(fragment) * 1000)
    except (ValueError, IndexError):
        return None
