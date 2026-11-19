# -*- coding: utf-8 -*-
"""MixCut 桌面端的外壳层工具包。

从 main.py 拆分出来的「与其他业务无关」的部分：
常量、纯函数、可复用的基础控件。

依赖约束
--------
本包**只依赖 PySide6 与标准库**，不导入 mixcut 的任何业务模块
（analysis / workers / mixbuilder / filter_engine …），也不导入 main.py。
这样任何模块都能安全导入，不会产生循环依赖。
"""
from .constants import (  # noqa: F401
    VIDEO_EXTS,
    MIX_RESOLUTION_PRESETS,
    MIX_DEFAULT_RESOLUTION_INDEX,
    MIX_FPS_OPTIONS,
    MIX_DEFAULT_FPS_INDEX,
    open_folder,
    _fmt_ms,
    _human_size,
    app_base_dir,
    resolve_app_icon,
)
from .widgets import (  # noqa: F401
    AspectRatioWidget,
    WheelGuard,
    UndoableTreeWidget,
)

__all__ = [
    "VIDEO_EXTS",
    "MIX_RESOLUTION_PRESETS",
    "MIX_DEFAULT_RESOLUTION_INDEX",
    "MIX_FPS_OPTIONS",
    "MIX_DEFAULT_FPS_INDEX",
    "open_folder",
    "_fmt_ms",
    "_human_size",
    "app_base_dir",
    "resolve_app_icon",
    "AspectRatioWidget",
    "WheelGuard",
    "UndoableTreeWidget",
]
