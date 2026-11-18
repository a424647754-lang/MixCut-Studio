# -*- coding: utf-8 -*-
"""代理缓存：JSON 索引、指纹、版本失效、接管与自动清理。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .spec import PROXY_SPEC_VERSION, normalize_source, proxy_suffix, safe_stem

INDEX_VERSION = 1
MIN_PROXY_BYTES = 1024


def key_for(source: str) -> str:
    return os.path.normcase(normalize_source(source))


def fingerprint(source: str, target_height: int, version: str = PROXY_SPEC_VERSION) -> str:
    """源身份 + 目标规格 + 规格版本；任一变化都换缓存键。"""
    path = normalize_source(source)
    try:
        st = os.stat(path)
        identity = f"{os.path.normcase(path)}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        identity = f"{os.path.normcase(path)}|0|0"
    raw = f"{identity}|{int(target_height)}|{version}|hevc-main10|aac192"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:32]


def proxy_filename(source: str, target_height: int, fp: str) -> str:
    return f"{safe_stem(source)}_{fp[:16]}{proxy_suffix(target_height)}"


def _now() -> float:
    return time.time()


class ProxyIndex:
    def __init__(self, cache_dir: Path, index_path: Path | None = None):
        self.cache_dir = Path(cache_dir)
        self.index_path = Path(index_path) if index_path else self.cache_dir / "index.json"
        self._entries: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._lock = threading.RLock()
        self.load()

    # ---- 读写 ----

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        entries = data.get("entries")
        if isinstance(entries, dict):
            for key, entry in entries.items():
                if isinstance(entry, dict):
                    self._entries[key] = entry

    def _save(self) -> None:
        data = {
            "index_version": INDEX_VERSION,
            "spec_version": PROXY_SPEC_VERSION,
            "updated_at": _now(),
            "entries": self._entries,
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_name(self.index_path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.index_path)
        except OSError:
            pass

    # ---- 查询 ----

    def get(self, source: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key_for(source))
            return dict(entry) if entry else None

    def all_entries(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._entries.items()}

    def ready_path(self, source: str, target_height: int) -> str:
        entry = self.get(source)
        if not entry or entry.get("status") != "ready":
            return ""
        if entry.get("spec_version") != PROXY_SPEC_VERSION:
            return ""
        if int(entry.get("target_height") or 0) != int(target_height):
            return ""
        if entry.get("fingerprint") != fingerprint(source, target_height):
            return ""
        path = entry.get("proxy") or ""
        try:
            if path and os.path.isfile(path) and os.path.getsize(path) > MIN_PROXY_BYTES:
                return path
        except OSError:
            pass
        return ""

    def proxy_path(self, source: str, target_height: int) -> Path:
        fp = fingerprint(source, target_height)
        return self.cache_dir / proxy_filename(source, target_height, fp)

    # ---- 更新 ----

    def upsert(self, source: str, **fields) -> dict[str, Any]:
        key = key_for(source)
        with self._lock:
            entry = dict(self._entries.get(key) or {})
            entry.update(fields)
            entry["source"] = normalize_source(source)
            entry["key"] = key
            entry["updated_at"] = _now()
            self._entries[key] = entry
            self._save()
            return dict(entry)

    def mark(self, source: str, status: str, **fields) -> dict[str, Any]:
        return self.upsert(source, status=status, **fields)

    def remove(self, source: str, delete_file: bool = False) -> None:
        key = key_for(source)
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry and delete_file:
                path = entry.get("proxy") or ""
                try:
                    if path:
                        os.remove(path)
                except OSError:
                    pass
            self._save()

    # ---- 接管 ----

    def adopt(self, source: str, target_height: int,
              validate: Callable[[str, int], bool]) -> str:
        """在缓存目录里找同源、同高度的有效代理并接管。

        只认当前指纹对应的文件名，避免同名不同目录的素材互相串用；
        ``validate(proxy_path, target_height)`` 仍然会用 ffprobe 校验真实输出，
        旧 H.264 代理会校验失败，不会被误接管。
        """
        if not self.cache_dir.is_dir():
            return ""
        fp = fingerprint(source, target_height)
        expected = self.cache_dir / proxy_filename(source, target_height, fp)
        candidates = [expected] if expected.is_file() else []
        for item in candidates:
            try:
                if item.stat().st_size <= MIN_PROXY_BYTES:
                    continue
            except OSError:
                continue
            if not validate(str(item), target_height):
                continue
            entry = self.upsert(
                source,
                status="ready",
                spec_version=PROXY_SPEC_VERSION,
                fingerprint=fp,
                target_height=int(target_height),
                proxy=str(item),
                codec="hevc",
                profile="Main 10",
                pix_fmt="yuv420p10le",
                adopted=True,
                created_at=item.stat().st_mtime,
            )
            return entry.get("proxy", "")
        return ""

    # ---- 清理 ----

    def prune(self, max_bytes: int, max_age_days: int, protected: set[str] | None = None) -> dict[str, int]:
        protected = {os.path.abspath(p) for p in (protected or set())}
        max_age = max(1, min(14, int(max_age_days))) * 24 * 3600
        now = _now()
        deleted = 0
        freed = 0
        errors = 0

        # 1) 超龄代理
        for key, entry in list(self._entries.items()):
            path = entry.get("proxy") or ""
            if not path or os.path.abspath(path) in protected:
                continue
            try:
                age = now - os.path.getmtime(path)
                if age > max_age:
                    if os.path.isfile(path):
                        os.remove(path)
                        deleted += 1
                    self._entries.pop(key, None)
            except OSError:
                errors += 1

        # 2) 容量上限：按最后修改时间从旧到新删
        live = []
        for key, entry in list(self._entries.items()):
            path = entry.get("proxy") or ""
            if not path or os.path.abspath(path) in protected:
                continue
            try:
                size = os.path.getsize(path)
                mtime = os.path.getmtime(path)
            except OSError:
                self._entries.pop(key, None)
                continue
            live.append((mtime, key, path, size))
        total = sum(item[3] for item in live)
        for mtime, key, path, size in sorted(live):
            if total <= max_bytes:
                break
            try:
                os.remove(path)
                total -= size
                deleted += 1
                freed += size
                self._entries.pop(key, None)
            except OSError:
                errors += 1

        # 3) 未索引的旧代理与残留 .part
        try:
            for item in self.cache_dir.iterdir():
                if not item.is_file():
                    continue
                if os.path.abspath(item) in protected:
                    continue
                if item.name == self.index_path.name:
                    continue
                try:
                    age = now - item.stat().st_mtime
                except OSError:
                    continue
                if item.name.endswith(".part.mp4"):
                    if age > 24 * 3600:
                        item.unlink(missing_ok=True)
                        deleted += 1
                    continue
                if item.suffix.lower() != ".mp4":
                    continue
                if age > max_age:
                    try:
                        item.unlink()
                        deleted += 1
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1

        if deleted:
            self._save()
        return {"deleted": deleted, "freed_bytes": freed, "errors": errors}


def index_size(entries: dict[str, dict[str, Any]]) -> int:
    total = 0
    for entry in entries.values():
        path = entry.get("proxy") or ""
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
    return total
