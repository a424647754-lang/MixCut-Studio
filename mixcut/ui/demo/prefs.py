# -*- coding: utf-8 -*-
"""界面偏好持久化：主题、强调色、动效开关、草稿目录。"""

import json
import os

from ...app_paths import config_path

_FILE = "ui.json"
_CACHE = None

DEFAULT = {
    "palette": "石墨深色",
    "accent": "紫色",
    "motion": {"page": True, "button": True, "number": True, "reduce_follow": True},
    "draft_dir": "",
}


def load() -> dict:
    data = json.loads(json.dumps(DEFAULT))
    try:
        raw = json.loads(config_path(_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if isinstance(raw, dict):
        if raw.get("palette") in ("石墨深色", "深空黑", "暖灰"):
            data["palette"] = raw["palette"]
        if raw.get("accent") in ("白色", "紫色", "蓝色", "绿色"):
            data["accent"] = raw["accent"]
        motion = raw.get("motion")
        if isinstance(motion, dict):
            for key in data["motion"]:
                if isinstance(motion.get(key), bool):
                    data["motion"][key] = motion[key]
        if isinstance(raw.get("draft_dir"), str):
            data["draft_dir"] = raw["draft_dir"]
    return data


def save(data: dict):
    global _CACHE
    _CACHE = data
    path = config_path(_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------
# 动效开关（控件绘制时高频读取，缓存一份）
# ---------------------------------------------------------

_CACHE = None


def data() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = load()
    return _CACHE


def reload() -> dict:
    global _CACHE
    _CACHE = load()
    return _CACHE


def _system_reduced() -> bool:
    """Windows「设置 → 辅助功能 → 动画效果」关闭时返回 True。"""
    try:
        import ctypes
        anim = ctypes.c_int(0)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(anim), 0)
        return bool(ok) and not bool(anim.value)
    except Exception:
        return False


def motion(key: str) -> bool:
    """页面切换/按钮反馈/数字滚动是否启用；开了「跟随系统」就叠加系统设置。"""
    cfg = data().get("motion", {})
    if not cfg.get(key, True):
        return False
    if cfg.get("reduce_follow", True) and _system_reduced():
        return False
    return True
