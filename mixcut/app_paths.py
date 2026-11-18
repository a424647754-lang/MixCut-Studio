# -*- coding: utf-8 -*-
"""应用目录：源码运行用工程根目录，打包后用户装到哪就用哪；不可写时退回 LOCALAPPDATA。

用户要求：缓存与草稿不要由后台统计，直接存在 MixCut 源文件夹 / 安装目录里。
"""

import os
import sys
from pathlib import Path

_FALLBACK_ROOT = Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")) / "MixCut"


def app_dir() -> Path:
    """exe 所在目录（打包）/ 工程根目录（源码）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """只读资源目录：打包版在 _internal，源码版在工程根。"""
    if getattr(sys, "frozen", False):
        bundled = getattr(sys, "_MEIPASS", None)
        if bundled:
            return Path(bundled)
        internal = Path(sys.executable).resolve().parent / "_internal"
        return internal if internal.is_dir() else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _pick(name: str, fallback_name: str) -> Path:
    preferred = app_dir() / name
    if _writable(preferred):
        return preferred
    # 装在 Program Files 时普通用户没写权限，退回用户目录，功能不失效
    fallback = _FALLBACK_ROOT / fallback_name
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def cache_dir() -> Path:
    return _pick("cache", "cache")


def config_dir() -> Path:
    return _pick("config", "config")


def cache_path(*parts: str) -> Path:
    path = cache_dir().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def config_path(name: str) -> Path:
    return config_dir() / name


# ---------------------------------------------------------
# 剪映草稿目录探测
# ---------------------------------------------------------

def jianying_draft_candidates():
    local = Path(os.environ.get("LOCALAPPDATA") or "")
    appdata = Path(os.environ.get("APPDATA") or "")
    home = Path(os.path.expanduser("~"))
    roots = [
        local / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
        local / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
        appdata / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
        home / "Documents" / "JianyingPro Drafts",
        home / "Documents" / "剪映草稿",
        Path("D:/JianyingPro Drafts"),
        Path("D:/剪映草稿"),
    ]
    seen = []
    for path in roots:
        if path and path not in seen:
            seen.append(path)
    return seen


def detect_draft_dir() -> str:
    for path in jianying_draft_candidates():
        try:
            if path.is_dir() and any(path.iterdir()):
                return str(path)
        except OSError:
            continue
    return ""
