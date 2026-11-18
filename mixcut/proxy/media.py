# -*- coding: utf-8 -*-
"""ffprobe 读取与代理输出校验。

校验项：编码、profile、像素格式、分辨率、帧率、时长、音轨、音画同步、色彩标记。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from .spec import (
    AUDIO_CODEC,
    PROXY_CODEC,
    PROXY_PIX_FMT,
    PROXY_PROFILE,
    color_params,
    parse_rate,
    target_dimensions,
)

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class VideoInfo:
    width: int = 0
    height: int = 0
    codec: str = ""
    profile: str = ""
    pix_fmt: str = ""
    fps: float = 0.0
    duration: float = 0.0
    start_time: float = 0.0
    color_range: str = ""
    color_space: str = ""
    color_transfer: str = ""
    color_primaries: str = ""
    rotation: int = 0


@dataclass
class AudioInfo:
    codec: str = ""
    duration: float = 0.0
    start_time: float = 0.0
    channels: int = 0
    sample_rate: int = 0
    bit_rate: int = 0


@dataclass
class MediaInfo:
    path: str = ""
    duration: float = 0.0
    start_time: float = 0.0
    video: VideoInfo | None = None
    audio: AudioInfo | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_audio(self) -> bool:
        return self.audio is not None


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    output: MediaInfo | None = None
    expected_width: int = 0
    expected_height: int = 0

    def message(self) -> str:
        return "；".join(self.errors) if self.errors else ""


def _float(value, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        if not text or text in ("N/A", "nan", "None"):
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _int(value, default: int = 0) -> int:
    try:
        text = str(value).strip()
        if not text or text == "N/A":
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _run_ffprobe(ffprobe: str, path: str) -> dict[str, Any]:
    cmd = [
        ffprobe, "-v", "error", "-hide_banner",
        "-show_streams", "-show_format", "-of", "json", path,
    ]
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="ignore",
        creationflags=NO_WINDOW, timeout=45,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip()[-500:] or "ffprobe 失败")
    return json.loads(proc.stdout or "{}")


def _rotation(stream: dict[str, Any]) -> int:
    for item in stream.get("side_data_list") or []:
        try:
            value = int(float(item.get("rotation", 0)))
        except (TypeError, ValueError, AttributeError):
            continue
        if value:
            return value
    try:
        return int(float(stream.get("rotate", 0) or 0))
    except (TypeError, ValueError):
        return 0


def probe_media(path: str, ffprobe: str) -> MediaInfo:
    data = _run_ffprobe(ffprobe, path)
    streams = data.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    info = MediaInfo(
        path=os.path.abspath(path),
        duration=_float(fmt.get("duration")),
        start_time=_float(fmt.get("start_time")),
        raw=data,
    )
    if video_stream:
        info.video = VideoInfo(
            width=_int(video_stream.get("width")),
            height=_int(video_stream.get("height")),
            codec=str(video_stream.get("codec_name") or "").lower(),
            profile=str(video_stream.get("profile") or ""),
            pix_fmt=str(video_stream.get("pix_fmt") or ""),
            fps=parse_rate(video_stream.get("avg_frame_rate")) or parse_rate(video_stream.get("r_frame_rate")),
            duration=_float(video_stream.get("duration"), info.duration),
            start_time=_float(video_stream.get("start_time")),
            color_range=str(video_stream.get("color_range") or ""),
            color_space=str(video_stream.get("color_space") or ""),
            color_transfer=str(video_stream.get("color_transfer") or ""),
            color_primaries=str(video_stream.get("color_primaries") or ""),
            rotation=_rotation(video_stream),
        )
    if audio_stream:
        info.audio = AudioInfo(
            codec=str(audio_stream.get("codec_name") or "").lower(),
            duration=_float(audio_stream.get("duration"), info.duration),
            start_time=_float(audio_stream.get("start_time")),
            channels=_int(audio_stream.get("channels")),
            sample_rate=_int(audio_stream.get("sample_rate")),
            bit_rate=_int(audio_stream.get("bit_rate")),
        )
    if not info.duration:
        candidates = [s.duration for s in (info.video, info.audio) if s is not None and s.duration]
        info.duration = max(candidates) if candidates else 0.0
    return info


def expected_output(source: MediaInfo, target_height: int) -> tuple[int, int, int]:
    """返回 (期望宽, 期望高, 实际目标高)。"""
    if not source.video or source.video.width <= 0 or source.video.height <= 0:
        return (0, 0, 0)
    width, height = target_dimensions(
        source.video.width, source.video.height, target_height, source.video.rotation
    )
    return (width, height, height)


def _profile_norm(value: str) -> str:
    return "".join(str(value or "").lower().split())


def validate_proxy(source: MediaInfo, output_path: str, target_height: int,
                   ffprobe: str) -> ValidationResult:
    """生成后用 ffprobe 校验；任何一项不满足都返回 is_error。"""
    expected_w, expected_h, target_h = expected_output(source, target_height)
    errors: list[str] = []
    if not source.video:
        return ValidationResult(False, ["源文件没有视频流"], None, expected_w, expected_h)
    if expected_h <= 0:
        return ValidationResult(False, ["无法计算目标尺寸"], None, 0, 0)

    try:
        out = probe_media(output_path, ffprobe)
    except Exception as exc:
        return ValidationResult(False, [f"ffprobe 读取失败: {exc}"], None, expected_w, expected_h)

    if not out.video:
        return ValidationResult(False, ["代理没有视频流"], out, expected_w, expected_h)
    video = out.video

    if video.codec != PROXY_CODEC:
        errors.append(f"编码={video.codec or '?'}（应为 {PROXY_CODEC}）")
    if _profile_norm(video.profile) != _profile_norm(PROXY_PROFILE):
        errors.append(f"profile={video.profile or '?'}（应为 {PROXY_PROFILE}）")
    if video.pix_fmt != PROXY_PIX_FMT:
        errors.append(f"像素格式={video.pix_fmt or '?'}（应为 {PROXY_PIX_FMT}）")
    if video.height != expected_h:
        errors.append(f"高度={video.height}（应为 {expected_h}）")
    if abs(video.width - expected_w) > max(2, round(expected_w * 0.02)):
        errors.append(f"宽度={video.width}（应约为 {expected_w}）")
    if video.width % 2 or video.height % 2:
        errors.append(f"宽高不是偶数: {video.width}x{video.height}")

    fps = source.video.fps
    if fps > 0 and video.fps > 0:
        if abs(video.fps - fps) > max(0.05, fps * 0.005):
            errors.append(f"帧率={video.fps:.3f}（源 {fps:.3f}）")

    tolerance = max(0.2, (2.0 / fps) if fps > 0 else 0.2)
    if source.duration > 0 and out.duration > 0:
        if abs(out.duration - source.duration) > tolerance:
            errors.append(f"时长={out.duration:.3f}s（源 {source.duration:.3f}s）")
    elif source.duration > 0:
        errors.append("代理时长不可读")

    if source.audio:
        if not out.audio:
            errors.append("代理缺少音轨")
        else:
            audio = out.audio
            if audio.codec != AUDIO_CODEC:
                errors.append(f"音频编码={audio.codec or '?'}（应为 {AUDIO_CODEC}）")
            sync_tol = max(0.1, (2.0 / fps) if fps > 0 else 0.1)
            if abs(audio.start_time - video.start_time) > sync_tol:
                errors.append(
                    f"音画起点不同步: 视频 {video.start_time:.3f}s / 音频 {audio.start_time:.3f}s"
                )
            if audio.duration > 0 and video.duration > 0:
                if abs(audio.duration - video.duration) > tolerance:
                    errors.append(
                        f"音画时长不同步: 视频 {video.duration:.3f}s / 音频 {audio.duration:.3f}s"
                    )
            # 不在校验阶段卡 AAC 实际平均码率：静音段/短片段会天然低于 192k，
            # 生成命令已经固定 -b:a 192k，这里只保证编码与音画同步。

    # 色彩标记：源有标记就要求一致；源没有标记则要求我们写入的默认 bt709/tv。
    want_color = color_params(source.video)
    expected_key = {
        "color_range": "range",
        "color_space": "colorspace",
        "color_transfer": "color_trc",
        "color_primaries": "color_primaries",
    }
    for field_name, label in (
        ("color_range", "色彩范围"),
        ("color_space", "色彩空间"),
        ("color_transfer", "传递函数"),
        ("color_primaries", "色彩原色"),
    ):
        actual = getattr(video, field_name)
        if not actual:
            errors.append(f"缺少{label}标记（{field_name}）")
            continue
        expected = want_color[expected_key[field_name]]
        if str(actual).lower() != str(expected).lower():
            errors.append(f"{label}={actual}（应为 {expected}）")

    return ValidationResult(not errors, errors, out, expected_w, expected_h)
