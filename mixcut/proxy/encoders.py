# -*- coding: utf-8 -*-
"""HEVC 10-bit 编码器能力探测。

ffmpeg 的 ``-encoders`` 无论机器有没有 GPU 都会列出 nvenc/qsv/amf，
所以必须对每个硬件编码器做真实的 2 帧 10-bit 试编码；结果按 ffmpeg
二进制身份缓存，失败自动降级到下一个编码器。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
PROBE_FRAMES = 2
PROBE_SIZE = "256x144"
CACHE_TTL_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True)
class EncoderSpec:
    name: str
    label: str
    variant: str = "base"
    encode_args: tuple[str, ...] = ()
    probe_args: tuple[str, ...] = ()
    init_args: tuple[str, ...] = ()
    filter_suffix: str = ""

    def args(self) -> list[str]:
        return list(self.encode_args)


def _specs() -> list[EncoderSpec]:
    nvenc = EncoderSpec(
        "hevc_nvenc", "NVIDIA NVENC", encode_args=(
            "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "26", "-b:v", "0",
        ), probe_args=("-preset", "p5"),
    )
    qsv_hw = EncoderSpec(
        "hevc_qsv", "Intel Quick Sync (hwupload)", variant="qsv_hw",
        encode_args=("-preset", "medium", "-global_quality", "26"),
        init_args=("-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"),
        filter_suffix=",hwupload=extra_hw_frames=64",
    )
    qsv_sw = EncoderSpec(
        "hevc_qsv", "Intel Quick Sync", variant="qsv_sw",
        encode_args=("-preset", "medium", "-global_quality", "26"),
    )
    amf = EncoderSpec(
        "hevc_amf", "AMD AMF",
        encode_args=("-quality", "balanced", "-b:v", "8M", "-maxrate", "12M", "-bufsize", "16M"),
        probe_args=("-quality", "balanced"),
    )
    x265 = EncoderSpec(
        "libx265", "libx265 (软件)", encode_args=(
            "-preset", "fast", "-crf", "26",
            "-x265-params", "log-level=error:keyint=12:min-keyint=12:scenecut=0",
        ), probe_args=("-preset", "fast", "-x265-params", "log-level=error"),
    )
    return [nvenc, qsv_hw, qsv_sw, amf, x265]


SPECS = _specs()
SPEC_BY_KEY = {(s.name, s.variant): s for s in SPECS}
HARDWARE_NAMES = {"hevc_nvenc", "hevc_qsv", "hevc_amf"}


def _identity(ffmpeg: str) -> dict[str, Any]:
    try:
        st = os.stat(ffmpeg)
        return {"path": os.path.abspath(ffmpeg), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    except OSError:
        return {"path": os.path.abspath(ffmpeg), "size": 0, "mtime_ns": 0}


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


@dataclass
class ProbeResult:
    ok: bool
    error: str = ""
    elapsed_ms: int = 0
    variant: str = "base"


class EncoderCapabilities:
    def __init__(self, ffmpeg: str | None, cache_file: Path, env: dict[str, str] | None = None):
        self.ffmpeg = ffmpeg or ""
        self.cache_file = Path(cache_file)
        self.env = dict(os.environ if env is None else env)
        self._lock = None
        self._available: dict[str, EncoderSpec] = {}
        self._probe_details: dict[str, dict[str, Any]] = {}
        self._loaded = False

    # ---- 探测 ----

    def _probe(self, spec: EncoderSpec) -> ProbeResult:
        if not self.ffmpeg:
            return ProbeResult(False, "找不到 ffmpeg")
        cmd = [self.ffmpeg, "-hide_banner", "-v", "error", "-nostdin"]
        cmd += list(spec.init_args)
        cmd += [
            "-f", "lavfi", "-i", f"color=c=black:s={PROBE_SIZE}:r=30:d=0.1",
            "-frames:v", str(PROBE_FRAMES),
            "-vf", "format=yuv420p10le" + spec.filter_suffix,
            "-c:v", spec.name, "-profile:v", "main10", "-pix_fmt", "p010le",
        ]
        cmd += list(spec.probe_args)
        cmd += ["-f", "null", "-"]
        started = time.time()
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="ignore",
                creationflags=NO_WINDOW, timeout=25,
            )
        except Exception as exc:
            return ProbeResult(False, str(exc), int((time.time() - started) * 1000), spec.variant)
        elapsed = int((time.time() - started) * 1000)
        if proc.returncode == 0:
            return ProbeResult(True, "", elapsed, spec.variant)
        tail = " ".join((proc.stderr or "").strip().splitlines()[-4:])[-400:]
        return ProbeResult(False, tail or f"exit {proc.returncode}", elapsed, spec.variant)

    def _load_cache(self) -> bool:
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if data.get("identity") != _identity(self.ffmpeg):
            return False
        if time.time() - float(data.get("probed_at", 0)) > CACHE_TTL_SECONDS:
            return False
        available = data.get("available") or {}
        specs = []
        for key, item in available.items():
            if not item.get("ok"):
                continue
            if "::" in key:
                name, variant = key.split("::", 1)
            else:
                name, variant = key, "base"
            spec = SPEC_BY_KEY.get((name, variant))
            if spec:
                specs.append(spec)
        if not specs:
            return False
        self._available = {s.name: s for s in specs}
        self._probe_details = dict(available)
        self._loaded = True
        return True

    def _save_cache(self) -> None:
        _atomic_json(self.cache_file, {
            "version": 1,
            "identity": _identity(self.ffmpeg),
            "probed_at": time.time(),
            "available": self._probe_details,
        })

    def probe_all(self) -> dict[str, EncoderSpec]:
        if self._loaded:
            return dict(self._available)
        self._loaded = True
        if self._load_cache():
            return dict(self._available)

        self._available = {}
        self._probe_details = {}
        for spec in SPECS:
            key = f"{spec.name}::{spec.variant}"
            result = self._probe(spec)
            self._probe_details[key] = {
                "ok": result.ok, "error": result.error,
                "elapsed_ms": result.elapsed_ms,
            }
            if result.ok and spec.name not in self._available:
                # nvenc/amf 只有一个变体；qsv 优先 hwupload，其次软件帧。
                self._available[spec.name] = spec
        self._save_cache()
        return dict(self._available)

    # ---- 选择 ----

    def candidates(self) -> list[EncoderSpec]:
        forced = (self.env.get("MIXCUT_PROXY_ENCODER") or "").strip()
        if forced:
            for spec in SPECS:
                if spec.name == forced:
                    return [spec]
            return [EncoderSpec(forced, forced)]
        available = self.probe_all()
        disable_hw = (self.env.get("MIXCUT_PROXY_DISABLE_HW") or "").strip().lower() in ("1", "true", "yes", "on")
        order = ["hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265"]
        result = []
        for name in order:
            if disable_hw and name in HARDWARE_NAMES:
                continue
            spec = available.get(name)
            if spec:
                result.append(spec)
        return result

    def available_names(self) -> list[str]:
        return [s.name for s in self.candidates()]

    def details(self) -> dict[str, Any]:
        self.probe_all()
        return {
            "ffmpeg": self.ffmpeg,
            "available": {
                name: {"label": spec.label, "variant": spec.variant}
                for name, spec in self._available.items()
            },
            "probes": dict(self._probe_details),
        }
