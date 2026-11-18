# -*- coding: utf-8 -*-
"""FFmpeg 转码 + 进度 + 取消 + ffprobe 校验 + 原子落盘。"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import media
from .encoders import EncoderCapabilities, EncoderSpec
from .spec import (
    AUDIO_BITRATE,
    PROXY_PIX_FMT,
    SHORT_GOP,
    color_filter,
    scale_filter,
)

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
STDERR_LINES = 30


@dataclass
class Attempt:
    encoder: str
    variant: str = "base"
    ok: bool = False
    error: str = ""
    elapsed_ms: int = 0


@dataclass
class TranscodeResult:
    ok: bool = False
    cancelled: bool = False
    output: str = ""
    encoder: str = ""
    expected_width: int = 0
    expected_height: int = 0
    error: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    validation: str = ""


class ProxyTranscoder:
    def __init__(self, ffmpeg: str | None, ffprobe: str | None,
                 capabilities: EncoderCapabilities, index,
                 threads: int | None = None, env: dict[str, str] | None = None):
        self.ffmpeg = ffmpeg or ""
        self.ffprobe = ffprobe or ""
        self.capabilities = capabilities
        self.index = index
        self.env = dict(os.environ if env is None else env)
        self.threads = int(threads or self._default_threads())

    @staticmethod
    def _default_threads() -> int:
        return max(1, min(8, (os.cpu_count() or 4) // 2))

    # ---- 接管已有代理 ----

    def adopt(self, source: str, target_height: int) -> str:
        if not self.ffprobe:
            return ""
        try:
            info = media.probe_media(source, self.ffprobe)
        except Exception:
            return ""
        if not info.video:
            return ""

        def validate(path: str, height: int) -> bool:
            try:
                return media.validate_proxy(info, path, height, self.ffprobe).ok
            except Exception:
                return False

        try:
            return self.index.adopt(source, target_height, validate)
        except Exception:
            return ""

    # ---- 生成 ----

    def transcode(self, source: str, out_path: str, target_height: int,
                  cancel_event: threading.Event, progress_cb=None) -> TranscodeResult:
        progress_cb = progress_cb or (lambda pct: None)
        if cancel_event.is_set():
            return TranscodeResult(cancelled=True, error="已取消")
        if not self.ffmpeg or not self.ffprobe:
            return TranscodeResult(error="找不到 ffmpeg/ffprobe")

        try:
            info = media.probe_media(source, self.ffprobe)
        except Exception as exc:
            return TranscodeResult(error=f"读取源文件失败: {exc}")
        if not info.video or info.video.width <= 0 or info.video.height <= 0:
            return TranscodeResult(error="源文件没有可用视频流")

        expected_w, expected_h, _ = media.expected_output(info, target_height)
        if expected_h <= 0:
            return TranscodeResult(error="无法计算代理尺寸")

        try:
            candidates = self.capabilities.candidates()
        except Exception as exc:
            return TranscodeResult(error=f"编码器探测失败: {exc}")
        if not candidates:
            return TranscodeResult(error="没有可用的 HEVC 10-bit 编码器")

        out = Path(out_path)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return TranscodeResult(error=f"代理目录不可写: {exc}", expected_width=expected_w,
                                   expected_height=expected_h)

        attempts: list[Attempt] = []
        for spec in candidates:
            if cancel_event.is_set():
                return TranscodeResult(cancelled=True, error="已取消", attempts=attempts,
                                       expected_width=expected_w, expected_height=expected_h)
            # 1) 先按原帧率直通；老 ffmpeg 不认识 -fps_mode 时退回 -vsync 0。
            for index, fps_args in enumerate((["-fps_mode", "passthrough"], ["-vsync", "0"])):
                tmp = out.with_name(out.name + f".{uuid.uuid4().hex[:8]}.part.mp4")
                started = time.time()
                cmd = self._command(spec, info, source, tmp, target_height, fps_args)
                try:
                    rc, err, cancelled = self._run(
                        cmd, info.duration, cancel_event, progress_cb,
                    )
                except Exception as exc:
                    attempts.append(Attempt(spec.name, spec.variant, False, f"启动 ffmpeg 失败: {exc}", 0))
                    self._safe_unlink(tmp)
                    break
                elapsed = int((time.time() - started) * 1000)
                if cancelled:
                    self._safe_unlink(tmp)
                    return TranscodeResult(cancelled=True, error="已取消", attempts=attempts,
                                           expected_width=expected_w, expected_height=expected_h)
                if rc != 0:
                    attempt = Attempt(spec.name, spec.variant, False, err, elapsed)
                    attempts.append(attempt)
                    self._safe_unlink(tmp)
                    fps_option_error = any(
                        token in err.lower() for token in ("fps_mode", "vsync", "unrecognized option")
                    )
                    if index == 0 and fps_option_error:
                        # 明确是 fps 选项，用 -vsync 0 再试一次
                        continue
                    break
                if not tmp.is_file() or tmp.stat().st_size <= 1024:
                    attempts.append(Attempt(spec.name, spec.variant, False, "输出文件为空", elapsed))
                    self._safe_unlink(tmp)
                    break

                validation = media.validate_proxy(info, str(tmp), target_height, self.ffprobe)
                if not validation.ok:
                    attempts.append(Attempt(
                        spec.name, spec.variant, False,
                        "输出校验失败: " + validation.message(), elapsed,
                    ))
                    self._safe_unlink(tmp)
                    # 校验失败通常是编码器能力/参数问题，换下一个编码器
                    break

                try:
                    os.replace(str(tmp), str(out))
                except OSError as exc:
                    self._safe_unlink(tmp)
                    attempts.append(Attempt(spec.name, spec.variant, False, f"落盘失败: {exc}", elapsed))
                    break
                progress_cb(100)
                return TranscodeResult(
                    ok=True, output=str(out), encoder=spec.name,
                    expected_width=validation.expected_width,
                    expected_height=validation.expected_height,
                    attempts=attempts, validation="ok",
                )
            # 如果第一个 fps 变体因选项失败并已 continue，会在下一轮尝试；否则结束该编码器
        return TranscodeResult(
            error=self._error_text(attempts),
            attempts=attempts,
            expected_width=expected_w,
            expected_height=expected_h,
        )

    # ---- 命令 ----

    def _command(self, spec: EncoderSpec, info, source: str, tmp: Path,
                 target_height: int, fps_args: list[str]) -> list[str]:
        target_h = media.expected_output(info, target_height)[2]
        vf = ",".join([
            scale_filter(target_h),
            f"format={PROXY_PIX_FMT}",
            color_filter(info.video),
        ])
        if spec.filter_suffix:
            vf += spec.filter_suffix

        cmd = [
            self.ffmpeg, "-hide_banner", "-nostats", "-y", "-nostdin",
            "-loglevel", "error",
            "-threads", str(self.threads), "-filter_threads", str(self.threads),
        ]
        cmd += list(spec.init_args)
        cmd += ["-i", source, "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf]
        cmd += fps_args
        cmd += ["-threads", str(self.threads), "-c:v", spec.name]
        cmd += spec.args()
        cmd += [
            "-profile:v", "main10", "-pix_fmt", "p010le", "-tag:v", "hvc1",
            "-g", str(SHORT_GOP),
            "-c:a", "aac", "-b:a", AUDIO_BITRATE,
            "-movflags", "+faststart", "-map_metadata", "-1",
            "-progress", "pipe:1", "-nostats",
            str(tmp),
        ]
        return cmd

    # ---- 子进程 ----

    def _run(self, cmd: list[str], duration: float, cancel_event: threading.Event,
             progress_cb) -> tuple[int, str, bool]:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="ignore", bufsize=1,
            creationflags=NO_WINDOW,
        )
        stderr_tail: deque[str] = deque(maxlen=STDERR_LINES)
        finished = threading.Event()
        last_pct = -1

        def read_stderr():
            try:
                for line in proc.stderr or []:
                    text = line.strip()
                    if text:
                        stderr_tail.append(text)
            except Exception:
                pass

        def watch_cancel():
            while not finished.wait(0.2):
                if cancel_event.is_set():
                    self._terminate(proc)
                    return

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        watcher = threading.Thread(target=watch_cancel, daemon=True)
        stderr_thread.start()
        watcher.start()

        rc = 1
        try:
            for line in proc.stdout or []:
                if cancel_event.is_set():
                    self._terminate(proc)
                    break
                key, sep, value = line.strip().partition("=")
                if not sep:
                    continue
                if key in ("out_time_us", "out_time_ms") and duration > 0:
                    try:
                        seconds = float(value) / 1_000_000.0
                    except ValueError:
                        continue
                    pct = min(99, max(0, int(seconds / duration * 100)))
                    if pct != last_pct:
                        last_pct = pct
                        progress_cb(pct)
                elif key == "progress" and value.strip() == "end":
                    progress_cb(100)
            try:
                rc = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._terminate(proc)
                rc = proc.wait(timeout=10)
        finally:
            finished.set()
            try:
                stderr_thread.join(timeout=3)
            except Exception:
                pass
            try:
                watcher.join(timeout=3)
            except Exception:
                pass
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe is not None:
                        pipe.close()
                except Exception:
                    pass

        err = "\n".join(stderr_tail)
        return (int(rc), err, cancel_event.is_set())

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.kill()
        except OSError:
            pass
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=NO_WINDOW, timeout=10,
                )
            except Exception:
                pass

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _error_text(attempts: list[Attempt]) -> str:
        if not attempts:
            return "所有编码器均失败"
        lines = []
        for attempt in attempts[-4:]:
            text = (attempt.error or "")
            lines.append(f"{attempt.encoder}: {text[:220]}")
        return " | ".join(lines)
