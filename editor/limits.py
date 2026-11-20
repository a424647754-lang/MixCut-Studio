"""属性值域单点定义（B-15 / B-22）。

Inspector 校验器、命令层归一、预览增益、导出命令都从这里取值，
禁止在各自模块里再写一份 min/max 字面量。
"""

from __future__ import annotations

SPEED_MIN = 0.1
SPEED_MAX = 10.0
VOLUME_MIN = 0.0
VOLUME_MAX = 2.0
OPACITY_MIN = 0.0
OPACITY_MAX = 1.0

# QAudioOutput 的线性音量上限就是 1.0；volume>1 只在成片（ffmpeg volume 滤镜）生效。
PREVIEW_GAIN_LIMIT = 1.0


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def clamp_speed(value: float) -> float:
    return clamp(value, SPEED_MIN, SPEED_MAX)


def clamp_volume(value: float) -> float:
    return clamp(value, VOLUME_MIN, VOLUME_MAX)


def clamp_opacity(value: float) -> float:
    return clamp(value, OPACITY_MIN, OPACITY_MAX)


def preview_gain(master_volume: float, clip_volume: float) -> float:
    """预览实际可用增益：受 QAudioOutput 上限约束，最多 1.0。"""
    return clamp(master_volume * clamp_volume(clip_volume), 0.0, PREVIEW_GAIN_LIMIT)


def volume_needs_render_warning(volume: float) -> bool:
    """音量超过 1.0 时预览无法完全还原，需要提示用户"只在成片生效"。"""
    return float(volume) > PREVIEW_GAIN_LIMIT
