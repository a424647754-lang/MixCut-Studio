# -*- coding: utf-8 -*-
"""MixCut · 预览代理（兼容门面）

对外 API 与旧版完全一致；内部改用 ``mixcut.proxy`` 的 HEVC Main10 代理系统。
旧配置里的 720/960/1280 现在表示**目标高度**，0 仍表示关闭并回退原片。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import ffmpeg_tools as ft
from .app_paths import cache_dir, config_path
from .proxy.cache import ProxyIndex, fingerprint
from .proxy.encoders import EncoderCapabilities
from .proxy.service import ProxyService
from .proxy.spec import WIDTH_CHOICES
from .proxy.transcoder import ProxyTranscoder

DEFAULT_MAX_BYTES = 50 * 1024 ** 3
DEFAULT_MAX_AGE_DAYS = 14

# 缓存与配置都落在 MixCut 源文件夹 / 安装目录（不可写时 app_paths 自动退回用户目录）
_CFG_FILE = config_path("preview_proxy.json")
_CACHE_DIR = cache_dir() / "proxy"

_LOCK = threading.RLock()
_LISTENERS: list = []
_INDEX: ProxyIndex | None = None
_SERVICE: ProxyService | None = None
_CAPS: EncoderCapabilities | None = None
_TRANSCODER: ProxyTranscoder | None = None


# ---------------------------------------------------------
# 配置
# ---------------------------------------------------------

def _read_config() -> dict:
    try:
        data = json.loads(_CFG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_config(data: dict) -> None:
    try:
        _CFG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CFG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def get_width() -> int:
    data = _read_config()
    try:
        width = int(data.get("width", 0))
    except (TypeError, ValueError):
        return 0
    return width if width in WIDTH_CHOICES else 0


def set_width(width: int) -> int:
    try:
        width = int(width)
    except (TypeError, ValueError):
        width = 0
    if width not in WIDTH_CHOICES:
        width = 0
    old = get_width()
    data = _read_config()
    data["width"] = width
    if not data.get("max_bytes"):
        data["max_bytes"] = DEFAULT_MAX_BYTES
    if not data.get("max_age_days"):
        data["max_age_days"] = DEFAULT_MAX_AGE_DAYS
    _write_config(data)
    if old != width:
        # 高度档变了，旧队列失去意义；新档由调用方重新 request_all。
        cancel()
    return width


def max_bytes() -> int:
    try:
        return int(_read_config().get("max_bytes") or DEFAULT_MAX_BYTES)
    except (TypeError, ValueError):
        return DEFAULT_MAX_BYTES


def max_age_days() -> int:
    try:
        return max(1, min(14, int(_read_config().get("max_age_days") or DEFAULT_MAX_AGE_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_DAYS


# ---------------------------------------------------------
# 服务初始化
# ---------------------------------------------------------

def _get_index() -> ProxyIndex:
    global _INDEX
    with _LOCK:
        if _INDEX is None:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _INDEX = ProxyIndex(_CACHE_DIR)
        return _INDEX


def _build_service() -> ProxyService | None:
    global _SERVICE, _CAPS, _TRANSCODER
    with _LOCK:
        if _SERVICE is not None:
            return _SERVICE
        ffmpeg = ft.find_ffmpeg()
        ffprobe = ft.find_ffprobe()
        if not ffmpeg or not ffprobe:
            return None
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        caps = EncoderCapabilities(ffmpeg, _CACHE_DIR / "encoder_caps.json")
        transcoder = ProxyTranscoder(ffmpeg, ffprobe, caps, _get_index())
        service = ProxyService(
            _get_index(), transcoder.transcode, adopt_fn=transcoder.adopt,
            height_provider=get_width,
            max_bytes=max_bytes(), max_age_days=max_age_days(),
        )
        service.add_listener(_forward_event)
        _CAPS = caps
        _TRANSCODER = transcoder
        _SERVICE = service
        return service


def _get_service() -> ProxyService | None:
    return _build_service()


def _forward_event(src, pct, proxy, error, done, total) -> None:
    with _LOCK:
        listeners = list(_LISTENERS)
    for cb in listeners:
        try:
            cb(src, pct, proxy, error, done, total)
        except TypeError:
            try:
                cb(src, pct, proxy, error)
            except Exception:
                pass
        except Exception:
            pass


# ---------------------------------------------------------
# 缓存
# ---------------------------------------------------------

def _fingerprint(src: str, width: int) -> str:
    return fingerprint(src, int(width))


def proxy_path(src: str, width: int) -> Path:
    return _get_index().proxy_path(src, int(width))


def cached_proxy(src: str, width: int | None = None) -> str:
    width = get_width() if width is None else int(width)
    if not width:
        return ""
    try:
        return _get_index().ready_path(src, width)
    except Exception:
        return ""


def resolve(src: str, width: int | None = None) -> str:
    """预览该用哪个文件：有代理用代理，否则原片（并顺手排队生成）。"""
    proxy = cached_proxy(src, width)
    if proxy:
        return proxy
    request(src, width)
    return src


# ---------------------------------------------------------
# 队列 / 订阅
# ---------------------------------------------------------

def add_listener(callback) -> None:
    """callback(src, pct, proxy_or_empty, error, done, total)。"""
    with _LOCK:
        if callback not in _LISTENERS:
            _LISTENERS.append(callback)


def remove_listener(callback) -> None:
    with _LOCK:
        if callback in _LISTENERS:
            _LISTENERS.remove(callback)


def request(src: str, width: int | None = None) -> int:
    return request_all([src], width)


def request_all(paths, width: int | None = None) -> int:
    """把若干素材加入代理队列（已缓存/已在队列的跳过），后台串行生成。"""
    width = get_width() if width is None else int(width)
    if not width:
        return 0
    service = _get_service()
    if service is None:
        for src in (paths or []):
            if src:
                _forward_event(src, 0, "", "找不到 ffmpeg/ffprobe", 0, 0)
        return 0
    return service.request_all(list(paths or []), width, priority=1, retry_failed=True)


def queue_state() -> dict:
    service = _SERVICE
    if service is None:
        return {"done": 0, "total": 0, "current": "", "paused": False, "queued": 0, "failed": 0}
    return service.queue_state()


def cancel() -> None:
    service = _SERVICE
    if service is not None:
        service.cancel()


def pause() -> None:
    service = _get_service()
    if service is not None:
        service.pause()


def resume() -> None:
    service = _get_service()
    if service is not None:
        service.resume()


def retry_failed(width: int | None = None) -> int:
    service = _get_service()
    if service is None:
        return 0
    return service.retry_failed(get_width() if width is None else int(width))


def status(src: str) -> dict:
    service = _SERVICE
    if service is not None:
        return service.status(src)
    try:
        entry = _get_index().get(src) or {}
    except Exception:
        entry = {}
    return {
        "source": src,
        "status": entry.get("status", "none"),
        "pct": 100 if entry.get("status") == "ready" else 0,
        "proxy": entry.get("proxy", ""),
        "error": entry.get("last_error", ""),
    }


# ---------------------------------------------------------
# 维护 / 诊断
# ---------------------------------------------------------

def prune(protected=None) -> dict:
    return _get_index().prune(max_bytes(), max_age_days(), protected=set(protected or []))


def adopt_existing(paths, width: int | None = None) -> dict:
    """启动时接管磁盘上已有的有效代理；旧 H.264 会校验失败不被接管。"""
    width = get_width() if width is None else int(width)
    result = {"adopted": 0, "skipped": 0}
    if not width:
        result["skipped"] = len(list(paths or []))
        return result
    transcoder = _TRANSCODER
    if transcoder is None:
        service = _get_service()
        transcoder = _TRANSCODER
    if transcoder is None:
        result["skipped"] = len(list(paths or []))
        return result
    for src in (paths or []):
        if not src or not os.path.isfile(src):
            result["skipped"] += 1
            continue
        if cached_proxy(src, width):
            result["adopted"] += 1
            continue
        path = transcoder.adopt(src, width)
        if path:
            result["adopted"] += 1
        else:
            result["skipped"] += 1
    return result


def encoder_details() -> dict:
    caps = _CAPS
    if caps is None:
        _get_service()
        caps = _CAPS
    if caps is None:
        return {"available": {}, "probes": {}, "ffmpeg": ""}
    return caps.details()


def available_encoders() -> list[str]:
    caps = _CAPS
    if caps is None:
        _get_service()
        caps = _CAPS
    if caps is None:
        return []
    return caps.available_names()


def compat_info() -> dict:
    """给打包/自检用的简短状态。"""
    return {
        "width": get_width(),
        "cache_dir": str(_CACHE_DIR),
        "config": str(_CFG_FILE),
        "ffmpeg": ft.find_ffmpeg(),
        "ffprobe": ft.find_ffprobe(),
        "service": _SERVICE is not None,
    }
