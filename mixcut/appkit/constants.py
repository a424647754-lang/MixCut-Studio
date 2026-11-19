# -*- coding: utf-8 -*-
"""MixCut 桌面端的基础常量与共用工具。

从 main.py 原样搬出，保持语义与取值完全不变：
这些常量原先定义在 main.py 顶部，被 MainWindow 与多个方法引用。

本模块**不导入任何 mixcut 内部模块**，也不导入 main.py，
处于依赖图最底层，任何模块都可以安全地导入它。
"""
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------
# 素材文件扩展名
# ---------------------------------------------------------
# 素材库、拖拽、扫描文件夹等入口统一用这份白名单判定“这是不是视频”。
# 注意：原实现是 set（非 tuple），成员判断与迭代顺序语义保持原样。
VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"
}

# ---------------------------------------------------------
# 混剪输出规格选项
# ---------------------------------------------------------
# 智能混剪页：清晰度（360p → 8K）与帧率（24 → 120）
# 竖屏 9:16 画布，尺寸/帧率决定剪映草稿的导出规格。
# 每项是 (下拉框显示文本, 宽, 高)，索引与下拉项一一对应。
MIX_RESOLUTION_PRESETS = [
    ("360p（360 × 640）", 360, 640),
    ("480p（480 × 854）", 480, 854),
    ("540p（540 × 960）", 540, 960),
    ("720p（720 × 1280）", 720, 1280),
    ("1080p（1080 × 1920）", 1080, 1920),
    ("2K（1440 × 2560）", 1440, 2560),
    ("4K（2160 × 3840）", 2160, 3840),
    ("8K（4320 × 7680）", 4320, 7680),
]
MIX_DEFAULT_RESOLUTION_INDEX = 4          # 默认 1080p

MIX_FPS_OPTIONS = [24, 25, 30, 48, 50, 60, 100, 120]
MIX_DEFAULT_FPS_INDEX = 2                 # 默认 30 帧


# ---------------------------------------------------------
# 工具函数
# ---------------------------------------------------------

def open_folder(path):
    """用系统默认方式打开文件夹（资源管理器 / Finder / xdg-open）。"""
    path = str(path)

    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    except Exception as e:
        print("打开文件夹失败:", e)


def _fmt_ms(ms):
    """毫秒格式化成 mm:ss（超过 1 小时自动 h:mm:ss）。"""
    try:
        total = int(ms or 0)
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    secs, ms = divmod(total, 1000)
    mins, secs = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def _human_size(num):
    """字节数格式化成 B / KB / MB / GB / TB。"""
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024


def app_base_dir(base_file):
    """打包后取 exe 所在目录，源码运行取**传入文件**所在目录。

    注意：原实现在 main.py 里用 ``Path(__file__).resolve().parent``，
    也就是项目根目录。搬出 main.py 后 ``__file__`` 会指向 mixcut/appkit，
    语义就变了。因此改为显式接收调用方文件路径，由 main.py 传入自己的
    ``__file__``，行为与原实现完全一致。

    Args:
        base_file: 调用方模块的 ``__file__``（源码运行时用于定位项目根）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(base_file).resolve().parent


def resolve_app_icon(base_file):
    """定位应用图标，兼容源码运行 / onedir 顶层 / _internal 布局。

    Args:
        base_file: 调用方模块的 ``__file__``，语义同 :func:`app_base_dir`。
    """
    base = app_base_dir(base_file)
    candidates = [
        base / "mixcut.ico",
        base / "_internal" / "mixcut.ico",
        Path(base_file).resolve().parent / "mixcut.ico",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None
