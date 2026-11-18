# -*- coding: utf-8 -*-
"""代理任务服务：单 worker、优先级、暂停/恢复、取消、失败恢复、状态机。"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .cache import ProxyIndex, fingerprint, key_for
from .transcoder import TranscodeResult


QUEUED = "queued"
RUNNING = "running"
READY = "ready"
FAILED = "failed"
CANCELLED = "cancelled"
PAUSED = "paused"


@dataclass
class Job:
    source: str
    key: str
    height: int
    priority: int
    seq: int
    token: int
    fingerprint: str
    out_path: str
    status: str = QUEUED
    pct: int = 0
    proxy: str = ""
    error: str = ""
    encoder: str = ""
    attempts: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    last_emit: float = 0.0
    last_pct: int = -1

    def public(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "height": self.height,
            "status": self.status,
            "pct": self.pct,
            "proxy": self.proxy,
            "error": self.error,
            "encoder": self.encoder,
            "attempts": list(self.attempts),
        }


@dataclass
class WorkItem:
    source: str
    key: str
    height: int
    priority: int
    seq: int
    token: int
    out_path: str


class ProxyService:
    def __init__(self, index: ProxyIndex, transcode: Callable[..., TranscodeResult],
                 adopt_fn: Callable[[str, int], str] | None = None,
                 height_provider: Callable[[], int] | None = None,
                 max_bytes: int = 50 * 1024 ** 3,
                 max_age_days: int = 14):
        self.index = index
        self.transcode = transcode
        self.adopt_fn = adopt_fn
        self.height_provider = height_provider or (lambda: 0)
        self.max_bytes = int(max_bytes)
        self.max_age_days = max(1, min(14, int(max_age_days)))

        self._cond = threading.Condition(threading.RLock())
        self._queue: list[WorkItem] = []
        self._jobs: dict[str, Job] = {}
        self._listeners: list[Callable[..., Any]] = []
        self._active: WorkItem | None = None
        self._active_cancel: threading.Event | None = None
        self._current = ""
        self._done = 0
        self._total = 0
        self._seq = 0
        self._token = 0
        self._thread: threading.Thread | None = None
        self._paused = False
        self._stop = False
        self._cancel_epoch = 0
        self._recovered = False
        self._last_prune = 0.0

    # ---- 订阅 ----

    def add_listener(self, callback: Callable[..., Any]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[..., Any]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _state(self) -> tuple[int, int]:
        return (self._done, self._total)

    def _emit(self, source: str, pct: int, proxy: str = "", error: str = "",
              force: bool = False) -> None:
        with self._cond:
            done, total = self._state()
            listeners = list(self._listeners)
        if not listeners:
            return
        for callback in listeners:
            try:
                callback(source, int(pct), proxy, error, done, total)
            except TypeError:
                try:
                    callback(source, int(pct), proxy, error)
                except Exception:
                    pass
            except Exception:
                pass

    # ---- 入队 ----

    def start(self) -> None:
        with self._cond:
            if self._thread and self._thread.is_alive():
                return
            self._stop = False
            self._thread = threading.Thread(target=self._worker, name="mixcut-proxy", daemon=True)
            self._thread.start()

    def enqueue(self, paths: list[str], height: int, priority: int = 1,
                retry_failed: bool = True) -> int:
        try:
            height = int(height)
        except (TypeError, ValueError):
            return 0
        if height <= 0:
            return 0
        added = 0
        with self._cond:
            for raw in paths or []:
                if not raw:
                    continue
                source = os.path.abspath(os.fspath(raw))
                if not os.path.isfile(source):
                    continue
                key = key_for(source)
                fp = fingerprint(source, height)
                existing = self._jobs.get(key)
                if existing and existing.height == height and existing.fingerprint == fp:
                    if existing.status in (READY,) and self.index.ready_path(source, height):
                        continue
                    if existing.status in (QUEUED, RUNNING):
                        existing.priority = min(existing.priority, int(priority))
                        if existing.status == QUEUED:
                            for item in self._queue:
                                if item.key == key:
                                    item.priority = existing.priority
                        continue
                    if existing.status == FAILED and not retry_failed:
                        continue
                elif existing is not None:
                    # 高度/源身份变了：旧任务作废
                    existing.status = CANCELLED
                    self._queue = [item for item in self._queue if item.key != key]

                if self.index.ready_path(source, height):
                    continue
                if existing and existing.token:
                    self._token += 1
                else:
                    self._token += 1
                token = self._token
                self._seq += 1
                job = Job(
                    source=source, key=key, height=height, priority=int(priority),
                    seq=self._seq, token=token, fingerprint=fp,
                    out_path=str(self.index.proxy_path(source, height)),
                )
                self._jobs[key] = job
                self._queue.append(WorkItem(source, key, height, int(priority),
                                            self._seq, token, job.out_path))
                self._total += 1
                added += 1
            if added:
                self._cond.notify_all()
                self.start()
        return added

    def request(self, source: str, height: int, priority: int = 0) -> int:
        return self.enqueue([source], height, priority=priority, retry_failed=False)

    def request_all(self, paths: list[str], height: int, priority: int = 1,
                    retry_failed: bool = True) -> int:
        return self.enqueue(paths, height, priority=priority, retry_failed=retry_failed)

    # ---- 控制 ----

    def pause(self) -> None:
        with self._cond:
            self._paused = True
            active_cancel = self._active_cancel
        if active_cancel is not None:
            active_cancel.set()
        self._emit("", 0, force=True)

    def resume(self) -> None:
        with self._cond:
            self._paused = False
            self._cond.notify_all()
        self.start()

    def cancel(self) -> None:
        with self._cond:
            self._cancel_epoch += 1
            queued = list(self._queue)
            self._queue.clear()
            for item in queued:
                job = self._jobs.get(item.key)
                if job and job.token == item.token:
                    job.status = CANCELLED
                    job.error = "已取消"
            active_cancel = self._active_cancel
            self._paused = False
            self._total = self._done
            self._cond.notify_all()
        if active_cancel is not None:
            active_cancel.set()

    def retry_failed(self, height: int | None = None, priority: int = 1) -> int:
        sources: list[str] = []
        with self._cond:
            for job in self._jobs.values():
                if job.status == FAILED:
                    if height is None or int(height) == job.height:
                        sources.append(job.source)
            if height is None and not sources:
                for entry in self.index.all_entries().values():
                    if entry.get("status") == FAILED and os.path.isfile(entry.get("source") or ""):
                        sources.append(entry["source"])
        if not sources:
            return 0
        target = int(height or 0)
        if target:
            return self.enqueue(sources, target, priority=priority, retry_failed=True)
        # 混合高度：按每条 index 记录的高度重排
        added = 0
        for source in sources:
            entry = self.index.get(source) or {}
            h = int(entry.get("target_height") or 0)
            if h:
                added += self.enqueue([source], h, priority=priority, retry_failed=True)
        return added

    def shutdown(self) -> None:
        with self._cond:
            self._stop = True
            active_cancel = self._active_cancel
            self._cond.notify_all()
        if active_cancel is not None:
            active_cancel.set()
        thread = self._thread
        if thread:
            thread.join(timeout=5)

    # ---- 查询 ----

    def cached_proxy(self, source: str, height: int) -> str:
        return self.index.ready_path(os.path.abspath(os.fspath(source)), int(height))

    def status(self, source: str) -> dict[str, Any]:
        key = key_for(source)
        with self._cond:
            job = self._jobs.get(key)
            if job:
                return job.public()
        entry = self.index.get(source)
        if not entry:
            return {"source": os.path.abspath(os.fspath(source)), "status": "none", "pct": 0}
        return {
            "source": entry.get("source", source),
            "height": entry.get("target_height", 0),
            "status": entry.get("status", "none"),
            "pct": 100 if entry.get("status") == READY else 0,
            "proxy": entry.get("proxy", ""),
            "error": entry.get("last_error", ""),
            "encoder": entry.get("encoder", ""),
            "attempts": [],
        }

    def all_status(self) -> dict[str, dict[str, Any]]:
        with self._cond:
            return {key: job.public() for key, job in self._jobs.items()}

    def queue_state(self) -> dict[str, Any]:
        with self._cond:
            failed = sum(1 for j in self._jobs.values() if j.status == FAILED)
            return {
                "done": self._done,
                "total": self._total,
                "current": self._current,
                "paused": self._paused,
                "queued": len(self._queue),
                "failed": failed,
            }

    # ---- worker ----

    def _worker(self) -> None:
        self._maybe_prune()
        self._recover_pending()
        while True:
            with self._cond:
                while not self._stop and (self._paused or not self._queue):
                    self._cond.wait(0.5)
                if self._stop:
                    break
                if not self._queue:
                    continue
                item = min(self._queue, key=lambda w: (w.priority, w.seq))
                self._queue.remove(item)
                job = self._jobs.get(item.key)
                if not job or job.token != item.token or job.status != QUEUED:
                    continue
                job.status = RUNNING
                job.started_at = time.time()
                job.last_emit = 0.0
                job.last_pct = -1
                cancel = threading.Event()
                self._active = item
                self._active_cancel = cancel
                self._current = item.source
                epoch = self._cancel_epoch
            self._emit(item.source, 0, force=True)
            try:
                self.index.mark(
                    item.source, RUNNING,
                    target_height=int(item.height),
                    fingerprint=fingerprint(item.source, item.height),
                    proxy="",
                    started_at=time.time(),
                )
            except Exception:
                pass

            adopted = ""
            if self.adopt_fn:
                try:
                    adopted = self.adopt_fn(item.source, item.height) or ""
                except Exception:
                    adopted = ""
            if adopted:
                self._finish_ready(item, adopted, encoder="adopted", attempts=[])
                continue

            try:
                result = self.transcode(
                    item.source, item.out_path, item.height, cancel,
                    lambda pct: self._on_progress(item, pct),
                )
            except Exception as exc:
                result = TranscodeResult(error=f"代理任务异常: {exc}")

            if result.cancelled:
                with self._cond:
                    paused_now = self._paused
                    cancelled_all = epoch != self._cancel_epoch
                if paused_now and not cancelled_all:
                    self._requeue(item)
                else:
                    self._finish_cancelled(item)
                continue

            try:
                if result.ok:
                    self._finish_ready(item, result.output, result.encoder, result.attempts)
                else:
                    self._finish_failed(item, result.error, result.attempts)
            except Exception as exc:
                self._finish_failed(item, f"代理收尾失败: {exc}", [])

    def _on_progress(self, item: WorkItem, pct: int) -> None:
        pct = max(0, min(99, int(pct)))
        now = time.time()
        should_emit = False
        with self._cond:
            job = self._jobs.get(item.key)
            if not job or job.token != item.token or job.status != RUNNING:
                return
            if pct > job.pct:
                job.pct = pct
            if pct >= 100 or pct - job.last_pct >= 3 or (now - job.last_emit) >= 0.25:
                job.last_pct = pct
                job.last_emit = now
                should_emit = True
        if should_emit:
            self._emit(item.source, pct)

    def _finish_ready(self, item: WorkItem, path: str, encoder: str,
                      attempts: list) -> None:
        attempt_text = [a.encoder if hasattr(a, "encoder") else str(a) for a in (attempts or [])]
        try:
            self.index.upsert(
                item.source,
                status=READY,
                spec_version=self.index_entry_version(),
                fingerprint=fingerprint(item.source, item.height),
                target_height=int(item.height),
                proxy=str(path),
                encoder=encoder,
                pix_fmt="yuv420p10le",
                profile="Main 10",
                last_error="",
                validated_at=time.time(),
            )
        except Exception:
            pass
        with self._cond:
            job = self._jobs.get(item.key)
            if job and job.token == item.token:
                job.status = READY
                job.pct = 100
                job.proxy = str(path)
                job.encoder = encoder
                job.error = ""
                job.attempts = attempt_text
                job.finished_at = time.time()
                self._done += 1
            self._active = None
            self._active_cancel = None
            self._current = ""
            self._cond.notify_all()
        self._emit(item.source, 100, str(path), "", force=True)
        self._maybe_prune()

    def _finish_failed(self, item: WorkItem, error: str, attempts: list) -> None:
        attempt_text = [a.encoder if hasattr(a, "encoder") else str(a) for a in (attempts or [])]
        try:
            self.index.mark(
                item.source, FAILED,
                target_height=int(item.height),
                proxy="",
                last_error=(error or "")[:500],
                attempts=attempt_text,
                failed_at=time.time(),
            )
        except Exception:
            pass
        with self._cond:
            job = self._jobs.get(item.key)
            if job and job.token == item.token:
                job.status = FAILED
                job.pct = 0
                job.error = (error or "")[:500]
                job.attempts = attempt_text
                job.finished_at = time.time()
                self._done += 1
            self._active = None
            self._active_cancel = None
            self._current = ""
            self._cond.notify_all()
        self._emit(item.source, 0, "", job_error(error), force=True)
        self._maybe_prune()

    def _finish_cancelled(self, item: WorkItem) -> None:
        try:
            self.index.mark(item.source, CANCELLED, target_height=int(item.height), proxy="")
        except Exception:
            pass
        with self._cond:
            job = self._jobs.get(item.key)
            if job and job.token == item.token:
                job.status = CANCELLED
                job.pct = 0
                job.error = "已取消"
                job.finished_at = time.time()
            self._active = None
            self._active_cancel = None
            self._current = ""
            self._total = self._done
            self._cond.notify_all()

    def _requeue(self, item: WorkItem) -> None:
        with self._cond:
            job = self._jobs.get(item.key)
            if job and job.token == item.token:
                job.status = QUEUED
                job.pct = 0
                job.started_at = 0.0
                self._queue.append(item)
            self._active = None
            self._active_cancel = None
            self._current = ""
            self._cond.notify_all()

    def _recover_pending(self) -> None:
        with self._cond:
            if self._recovered:
                return
            self._recovered = True
        height = int(self.height_provider() or 0)
        if height <= 0:
            return
        sources = []
        for entry in self.index.all_entries().values():
            if entry.get("status") not in (QUEUED, RUNNING):
                continue
            if int(entry.get("target_height") or 0) != height:
                continue
            source = entry.get("source") or ""
            if source and os.path.isfile(source):
                sources.append(source)
        if sources:
            self.enqueue(sources, height, priority=1, retry_failed=True)

    def _maybe_prune(self) -> None:
        now = time.time()
        with self._cond:
            if self._queue:
                return
            if now - self._last_prune < 300:
                return
            self._last_prune = now
            protected = []
            if self._active:
                protected.append(self._active.out_path)
        try:
            self.index.prune(self.max_bytes, self.max_age_days, protected=set(protected))
        except Exception:
            pass

    @staticmethod
    def index_entry_version() -> str:
        from .spec import PROXY_SPEC_VERSION
        return PROXY_SPEC_VERSION


def job_error(error: str) -> str:
    text = (error or "").replace("\n", " ").strip()
    return text[:240] or "代理生成失败"
