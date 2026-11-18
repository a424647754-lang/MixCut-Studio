# -*- coding: utf-8 -*-
"""代理规格与纯计算：目标尺寸、文件名、色彩滤镜、帧率解析。

本模块不启动子进程、不读写工程状态，方便单元测试。
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

# 代理规格版本：任何影响输出格式/命名/校验的改动都必须提升它。
PROXY_SPEC_VERSION = "3"

PROXY_CODEC = "hevc"
PROXY_PROFILE = "Main 10"
PROXY_PIX_FMT = "yuv420p10le"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"
SHORT_GOP = 12

DEFAULT_TARGET_HEIGHT = 960
WIDTH_CHOICES = (0, 720, 960, 1280)  # 兼容旧 UI：数值现在表示目标高度

_VALID_PRIMARIES = {
    "bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "film",
    "bt2020", "smpte428", "smpte431", "smpte432", "jedec-p22",
}
_VALID_TRC = {
    "bt709", "gamma22", "gamma28", "smpte170m", "smpte240m", "linear",
    "log100", "log316", "iec61966-2-1", "iec61966-2-4", "bt1361e",
    "smpte2084", "smpte428", "arib-std-b67", "bt2020-10", "bt2020-12",
}
_VALID_SPACE = {
    "bt709", "fcc", "bt470bg", "smpte170m", "smpte240m", "ycgco",
    "bt2020nc", "bt2020c", "smpte2085", "chroma-derived-nc",
    "chroma-derived-c", "ictcp",
}


def even(value: float) -> int:
    """向下取偶，最小 2。"""
    try:
        number = int(math.floor(float(value)))
    except (TypeError, ValueError):
        return 2
    if number < 2:
        return 2
    return number - (number % 2)


def target_height_for(source_height: int, requested_height: int) -> int:
    """不放大：目标高度不超过源高度，并保持偶数。"""
    try:
        source = int(source_height)
        requested = int(requested_height)
    except (TypeError, ValueError):
        return 0
    if source <= 0 or requested <= 0:
        return 0
    return even(min(source, requested))


def target_dimensions(source_width: int, source_height: int, requested_height: int,
                      rotation: int = 0) -> tuple[int, int]:
    """返回代理的 (宽, 高)。

    以高度为基准，保持显示比例；宽高都取偶数。
    rotation=90/270 时先交换源宽高（FFmpeg 默认自动旋转后再缩放）。
    """
    try:
        width = int(source_width)
        height = int(source_height)
    except (TypeError, ValueError):
        return (0, 0)
    if width <= 0 or height <= 0:
        return (0, 0)
    if abs(int(rotation or 0)) % 180 == 90:
        width, height = height, width
    target_h = target_height_for(height, requested_height)
    if not target_h:
        return (0, 0)
    target_w = even(round(width * (target_h / float(height))))
    return (max(2, target_w), max(2, target_h))


def parse_rate(value) -> float:
    """把 ``30000/1001`` / ``29.97`` / None 解析成 float；失败返回 0。"""
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text or text in ("N/A", "0/0"):
        return 0.0
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def safe_stem(path) -> str:
    """把源文件名压成 Windows 可用的短前缀，保留中文。"""
    stem = Path(path).stem or "proxy"
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
    stem = re.sub(r"\s+", "_", stem).strip("._")
    return (stem or "proxy")[:48]


def _clean(value, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def color_params(video) -> dict[str, str]:
    """从源视频信息得到 setparams 的四个色彩参数。

    源没有标记时按 SDR/bt709/tv 处理；源有 HDR/宽色域标记时保留，
    避免把 HDR 素材硬写成 bt709 造成预览偏色。
    """
    primaries = _clean(getattr(video, "color_primaries", ""), _VALID_PRIMARIES, "bt709")
    trc = _clean(getattr(video, "color_transfer", ""), _VALID_TRC, "bt709")
    space = _clean(getattr(video, "color_space", ""), _VALID_SPACE, "bt709")
    color_range = "pc" if str(getattr(video, "color_range", "") or "").lower() == "pc" else "tv"
    return {
        "color_primaries": primaries,
        "color_trc": trc,
        "colorspace": space,
        "range": color_range,
    }


def color_filter(video) -> str:
    params = color_params(video)
    return (
        "setparams=color_primaries={color_primaries}:color_trc={color_trc}:"
        "colorspace={colorspace}:range={range}"
    ).format(**params)


def scale_filter(target_height: int) -> str:
    # -2 保持显示比例并自动取偶；目标高度由 media 校验。
    return f"scale=-2:{int(target_height)}:flags=lanczos"


def proxy_suffix(target_height: int) -> str:
    return f"_{int(target_height)}p.mp4"


def normalize_source(path) -> str:
    """统一绝对路径；Windows 比较时用 normcase。"""
    return os.path.abspath(os.fspath(path))
