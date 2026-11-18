# -*- coding: utf-8 -*-
"""MixCut 代理系统回归测试（脚本式 unittest，不依赖 PySide6）。

用法：
    python tests/test_proxy_system.py
    MIXCUT_PROXY_INTEGRATION=1 python tests/test_proxy_system.py   # 额外跑真实 4K 转码
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mixcut.proxy.cache import ProxyIndex, fingerprint, proxy_filename  # noqa: E402
from mixcut.proxy.encoders import SPECS, EncoderCapabilities, EncoderSpec, SPEC_BY_KEY  # noqa: E402
from mixcut.proxy.service import ProxyService, CANCELLED, FAILED, READY  # noqa: E402
from mixcut.proxy.spec import (  # noqa: E402
    PROXY_SPEC_VERSION,
    even,
    safe_stem,
    target_dimensions,
    target_height_for,
)
from mixcut.proxy.transcoder import TranscodeResult  # noqa: E402


def touch(path: Path, size: int = 2048) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def wait_until(condition, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


class SpecTests(unittest.TestCase):
    def test_even_floor(self):
        self.assertEqual(even(9.9), 8)
        self.assertEqual(even(3), 2)
        self.assertEqual(even(0), 2)

    def test_target_height_no_upscale(self):
        self.assertEqual(target_height_for(2160, 960), 960)
        self.assertEqual(target_height_for(540, 960), 540)
        self.assertEqual(target_height_for(1080, 0), 0)

    def test_target_dimensions(self):
        self.assertEqual(target_dimensions(3840, 2160, 960), (1706, 960))
        self.assertEqual(target_dimensions(1920, 1080, 960), (1706, 960))
        self.assertEqual(target_dimensions(1280, 720, 960), (1280, 720))
        self.assertEqual(target_dimensions(1080, 1920, 960), (540, 960))
        self.assertEqual(target_dimensions(1920, 1080, 960, rotation=90), (540, 960))
        self.assertEqual(target_dimensions(640, 360, 960), (640, 360))
        self.assertEqual(target_dimensions(0, 0, 960), (0, 0))

    def test_safe_stem_keeps_chinese(self):
        self.assertEqual(safe_stem("/a/测试 素材?.mov"), "测试_素材")


class FingerprintTests(unittest.TestCase):
    def test_stable_and_height_changes(self):
        with tempfile.TemporaryDirectory() as td:
            src = touch(Path(td) / "a.mov")
            fp1 = fingerprint(str(src), 960)
            fp2 = fingerprint(str(src), 960)
            self.assertEqual(fp1, fp2)
            self.assertNotEqual(fp1, fingerprint(str(src), 720))
            self.assertNotEqual(fp1, fingerprint(str(src), 960, version="2"))
            os.utime(src, (time.time() + 5, time.time() + 5))
            self.assertNotEqual(fp1, fingerprint(str(src), 960))
            self.assertIn("960p", proxy_filename(str(src), 960, fp1))
            self.assertIn("a_", proxy_filename(str(src), 960, fp1))


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.src = touch(self.root / "素材.mov")
        self.cache = self.root / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.index = ProxyIndex(self.cache)

    def tearDown(self):
        self.td.cleanup()

    def _ready(self, spec_version=PROXY_SPEC_VERSION):
        proxy = touch(self.cache / proxy_filename(str(self.src), 960, fingerprint(str(self.src), 960)))
        self.index.upsert(
            str(self.src), status="ready", spec_version=spec_version,
            fingerprint=fingerprint(str(self.src), 960), target_height=960,
            proxy=str(proxy),
        )
        return proxy

    def test_ready_and_version_invalidation(self):
        proxy = self._ready()
        self.assertEqual(self.index.ready_path(str(self.src), 960), str(proxy))
        self.index.upsert(str(self.src), status="ready", spec_version="2",
                          fingerprint=fingerprint(str(self.src), 960), target_height=960,
                          proxy=str(proxy))
        self.assertEqual(self.index.ready_path(str(self.src), 960), "")
        self.assertEqual(self.index.ready_path(str(self.src), 720), "")

    def test_missing_or_tiny_file(self):
        proxy = self._ready()
        proxy.unlink()
        self.assertEqual(self.index.ready_path(str(self.src), 960), "")
        proxy = self._ready()
        proxy.write_bytes(b"x")
        self.assertEqual(self.index.ready_path(str(self.src), 960), "")

    def test_atomic_persist_and_reload(self):
        self._ready()
        reloaded = ProxyIndex(self.cache)
        self.assertEqual(reloaded.ready_path(str(self.src), 960),
                         self.index.ready_path(str(self.src), 960))
        self.assertTrue((self.cache / "index.json").is_file())

    def test_adopt_rejects_old_h264(self):
        old = touch(self.cache / f"{safe_stem(str(self.src))}_old_960p.mp4")
        self.assertEqual(self.index.adopt(str(self.src), 960, lambda p, h: True), "")
        good = touch(self.cache / proxy_filename(str(self.src), 960, fingerprint(str(self.src), 960)))
        self.assertEqual(self.index.adopt(str(self.src), 960, lambda p, h: False), "")
        self.assertEqual(self.index.adopt(str(self.src), 960, lambda p, h: True), str(good))
        self.assertTrue(self.index.ready_path(str(self.src), 960))
        self.assertTrue(self.index.get(str(self.src))["adopted"])

    def test_prune_age_and_protected(self):
        a = self._ready()
        b = touch(self.cache / "other_960p.mp4")
        self.index.upsert(str(self.root / "other.mov"), status="ready",
                          spec_version=PROXY_SPEC_VERSION, target_height=960,
                          fingerprint="x", proxy=str(b))
        old = time.time() - 3 * 86400
        for path in (a, b):
            os.utime(path, (old, old))
        result = self.index.prune(max_bytes=10 ** 12, max_age_days=1, protected={str(a)})
        self.assertTrue(a.exists())
        self.assertFalse(b.exists())
        self.assertGreaterEqual(result["deleted"], 1)

    def test_prune_capacity_lru(self):
        a = touch(self.cache / "a_960p.mp4", 4096)
        b = touch(self.cache / "b_960p.mp4", 4096)
        os.utime(a, (time.time() - 100, time.time() - 100))
        os.utime(b, (time.time() - 10, time.time() - 10))
        self.index.upsert(str(self.root / "a.mov"), status="ready",
                          spec_version=PROXY_SPEC_VERSION, target_height=960,
                          fingerprint="a", proxy=str(a))
        self.index.upsert(str(self.root / "b.mov"), status="ready",
                          spec_version=PROXY_SPEC_VERSION, target_height=960,
                          fingerprint="b", proxy=str(b))
        self.index.prune(max_bytes=4096, max_age_days=14)
        self.assertFalse(a.exists())
        self.assertTrue(b.exists())


class EncoderTests(unittest.TestCase):
    def test_forced_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            caps = EncoderCapabilities("", Path(td) / "caps.json", env={"MIXCUT_PROXY_ENCODER": "libx265"})
            self.assertEqual([s.name for s in caps.candidates()], ["libx265"])

    def test_qsv_hwupload_first(self):
        qsv = [s for s in SPECS if s.name == "hevc_qsv"]
        self.assertEqual(qsv[0].variant, "qsv_hw")

    def test_disable_hardware(self):
        with tempfile.TemporaryDirectory() as td:
            caps = EncoderCapabilities("", Path(td) / "caps.json",
                                       env={"MIXCUT_PROXY_DISABLE_HW": "1"})
            # 不跑真实探测时没有可用编码器，这里只验证候选不会硬编
            self.assertFalse(any(s.name in ("hevc_nvenc", "hevc_qsv", "hevc_amf")
                                 for s in caps.candidates()))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.cache = self.root / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.sources = []
        for name in ("a.mov", "b.mov", "c.mov"):
            self.sources.append(touch(self.root / name))
        self.calls = []
        self.events = []
        self.lock = threading.Lock()

    def tearDown(self):
        self.td.cleanup()

    def _service(self, fake):
        index = ProxyIndex(self.cache)
        service = ProxyService(index, fake, height_provider=lambda: 960)

        def listener(src, pct, proxy, error, done, total):
            with self.lock:
                self.events.append((src, pct, proxy, error, done, total))

        service.add_listener(listener)
        return service

    def _fake_ok(self, src, out, height, cancel, progress):
        with self.lock:
            self.calls.append(src)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"x" * 4096)
        progress(50)
        return TranscodeResult(ok=True, output=str(out), encoder="fake")

    def test_priority(self):
        service = self._service(self._fake_ok)
        service.pause()
        service.request_all([str(self.sources[0])], 960, priority=5)
        service.request_all([str(self.sources[1])], 960, priority=1)
        service.resume()
        self.assertTrue(wait_until(lambda: service.queue_state()["done"] == 2))
        self.assertEqual(self.calls[0], str(self.sources[1]))
        self.assertEqual(service.queue_state()["total"], 2)
        service.shutdown()

    def test_failure_and_retry(self):
        def fake(src, out, height, cancel, progress):
            return TranscodeResult(error="编码器全失败")

        service = self._service(fake)
        service.request_all([str(self.sources[0])], 960)
        self.assertTrue(wait_until(lambda: service.status(str(self.sources[0]))["status"] == FAILED))
        self.assertEqual(service.queue_state()["done"], 1)
        self.assertTrue(any(e[3] for e in self.events))

        def ok(src, out, height, cancel, progress):
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(b"x" * 4096)
            return TranscodeResult(ok=True, output=str(out), encoder="fake")

        service.transcode = ok
        self.assertEqual(service.retry_failed(960), 1)
        self.assertTrue(wait_until(lambda: service.status(str(self.sources[0]))["status"] == READY))
        service.shutdown()

    def test_cancel(self):
        def slow(src, out, height, cancel, progress):
            while not cancel.is_set():
                time.sleep(0.02)
            return TranscodeResult(cancelled=True)

        service = self._service(slow)
        service.request_all([str(self.sources[0])], 960)
        self.assertTrue(wait_until(lambda: service.status(str(self.sources[0]))["status"] == "running"))
        service.cancel()
        self.assertTrue(wait_until(lambda: service.status(str(self.sources[0]))["status"] == CANCELLED))
        self.assertEqual(service.queue_state()["total"], service.queue_state()["done"])
        service.shutdown()

    def test_pause_resume_requeue(self):
        count = {"n": 0}

        def fake(src, out, height, cancel, progress):
            count["n"] += 1
            if count["n"] == 1:
                while not cancel.is_set():
                    time.sleep(0.02)
                return TranscodeResult(cancelled=True)
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(b"x" * 4096)
            return TranscodeResult(ok=True, output=str(out), encoder="fake")

        service = self._service(fake)
        service.request_all([str(self.sources[0])], 960)
        self.assertTrue(wait_until(lambda: service.status(str(self.sources[0]))["status"] == "running"))
        service.pause()
        self.assertTrue(wait_until(lambda: service.status(str(self.sources[0]))["status"] == "queued"))
        service.resume()
        self.assertTrue(wait_until(lambda: service.status(str(self.sources[0]))["status"] == READY))
        self.assertEqual(count["n"], 2)
        service.shutdown()

    def test_no_duplicate_queue(self):
        service = self._service(self._fake_ok)
        service.pause()
        first = service.request_all([str(self.sources[0])], 960)
        second = service.request_all([str(self.sources[0])], 960)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(service.queue_state()["total"], 1)
        service.resume()
        self.assertTrue(wait_until(lambda: service.queue_state()["done"] == 1))
        service.shutdown()


class FacadeTests(unittest.TestCase):
    def test_facade_import_without_qt(self):
        from mixcut import preview_proxy
        self.assertTrue(hasattr(preview_proxy, "resolve"))
        self.assertTrue(hasattr(preview_proxy, "request_all"))
        self.assertEqual(preview_proxy.get_width() in (0, 720, 960, 1280), True)


@unittest.skipUnless(os.environ.get("MIXCUT_PROXY_INTEGRATION") == "1",
                     "设置 MIXCUT_PROXY_INTEGRATION=1 才跑真实 4K 转码")
class IntegrationTests(unittest.TestCase):
    @staticmethod
    def _make_source(path: Path, size="3840x2160", rate="30", duration="2") -> bool:
        import subprocess
        from mixcut import ffmpeg_tools as ft
        ffmpeg = ft.find_ffmpeg()
        cmd = [
            ffmpeg, "-hide_banner", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
            "-c:a", "pcm_s16le", "-shortest", str(path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="ignore")
        return proc.returncode == 0

    def test_real_4k_prores_to_960p_hevc(self):
        from mixcut import ffmpeg_tools as ft
        from mixcut.proxy import media
        from mixcut.proxy.transcoder import ProxyTranscoder

        ffmpeg = ft.find_ffmpeg()
        ffprobe = ft.find_ffprobe()
        if not ffmpeg or not ffprobe:
            self.skipTest("找不到项目自带 ffmpeg/ffprobe")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "4k_prores.mov"
            self.assertTrue(self._make_source(source), "生成 4K ProRes 失败")

            info = media.probe_media(str(source), ffprobe)
            self.assertEqual((info.video.width, info.video.height), (3840, 2160))
            self.assertEqual(info.video.codec, "prores")
            self.assertEqual(info.video.pix_fmt, "yuv422p10le")

            cache = root / "cache"
            index = ProxyIndex(cache)
            caps = EncoderCapabilities(ffmpeg, cache / "caps.json")
            transcoder = ProxyTranscoder(ffmpeg, ffprobe, caps, index)
            out = root / "proxy_960p.mp4"
            result = transcoder.transcode(str(source), str(out), 960, threading.Event())
            self.assertTrue(result.ok, result.error)
            self.assertTrue(out.is_file())
            check = media.validate_proxy(info, str(out), 960, ffprobe)
            self.assertTrue(check.ok, check.message())
            out_info = media.probe_media(str(out), ffprobe)
            self.assertEqual(out_info.video.codec, "hevc")
            self.assertEqual(out_info.video.height, 960)
            self.assertEqual(out_info.video.pix_fmt, "yuv420p10le")
            self.assertAlmostEqual(out_info.video.fps, 30.0, delta=0.1)
            self.assertIsNotNone(out_info.audio)
            self.assertEqual(out_info.audio.codec, "aac")

    def test_60fps_preserved(self):
        from mixcut import ffmpeg_tools as ft
        from mixcut.proxy.transcoder import ProxyTranscoder

        ffmpeg, ffprobe = ft.find_ffmpeg(), ft.find_ffprobe()
        if not ffmpeg or not ffprobe:
            self.skipTest("找不到项目自带 ffmpeg/ffprobe")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "60fps.mov"
            self.assertTrue(self._make_source(source, "1920x1080", "60000/1001", "1.5"))
            cache = root / "cache"
            caps = EncoderCapabilities(ffmpeg, cache / "caps.json",
                                       env={"MIXCUT_PROXY_DISABLE_HW": "1"})
            transcoder = ProxyTranscoder(ffmpeg, ffprobe, caps, ProxyIndex(cache))
            out = root / "out.mp4"
            result = transcoder.transcode(str(source), str(out), 960, threading.Event())
            self.assertTrue(result.ok, result.error)
            from mixcut.proxy import media
            out_info = media.probe_media(str(out), ffprobe)
            self.assertAlmostEqual(out_info.video.fps, 60000 / 1001, delta=0.3)
            self.assertEqual(out_info.video.height, 960)

    def test_encoder_failure_falls_back(self):
        from mixcut import ffmpeg_tools as ft
        from mixcut.proxy.transcoder import ProxyTranscoder

        ffmpeg, ffprobe = ft.find_ffmpeg(), ft.find_ffprobe()
        if not ffmpeg or not ffprobe:
            self.skipTest("找不到项目自带 ffmpeg/ffprobe")

        class BadNvencCaps:
            def candidates(self):
                bad = EncoderSpec(
                    "hevc_nvenc", "bad", encode_args=("-preset", "definitely_not_a_preset"),
                )
                return [bad, SPEC_BY_KEY[("libx265", "base")]]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "720p.mov"
            self.assertTrue(self._make_source(source, "1280x720", "30", "1"))
            out = root / "fallback.mp4"
            transcoder = ProxyTranscoder(ffmpeg, ffprobe, BadNvencCaps(), ProxyIndex(root / "cache"))
            result = transcoder.transcode(str(source), str(out), 960, threading.Event())
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.encoder, "libx265")
            self.assertTrue(any(a.encoder == "hevc_nvenc" and not a.ok for a in result.attempts))
            self.assertTrue(out.is_file())

    def test_real_cancel_stops_ffmpeg_and_cleans_part(self):
        from mixcut import ffmpeg_tools as ft
        from mixcut.proxy.transcoder import ProxyTranscoder

        ffmpeg, ffprobe = ft.find_ffmpeg(), ft.find_ffprobe()
        if not ffmpeg or not ffprobe:
            self.skipTest("找不到项目自带 ffmpeg/ffprobe")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "slow.mov"
            out = root / "out.mp4"
            self.assertTrue(self._make_source(source, "1920x1080", "30", "8"))
            caps = EncoderCapabilities(ffmpeg, root / "caps.json",
                                       env={"MIXCUT_PROXY_ENCODER": "libx265"})
            transcoder = ProxyTranscoder(ffmpeg, ffprobe, caps, ProxyIndex(root / "cache"))
            cancel = threading.Event()
            progress = []
            holder = {}

            def worker():
                holder["result"] = transcoder.transcode(
                    str(source), str(out), 960, cancel,
                    lambda pct: progress.append(pct),
                )

            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(wait_until(lambda: bool(progress) or "result" in holder, timeout=20))
            cancel.set()
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive())
            self.assertTrue(holder["result"].cancelled)
            self.assertFalse(out.exists())
            self.assertEqual(list(root.glob("*.part.mp4")), [])

    def test_facade_end_to_end_in_temp_paths(self):
        from mixcut import ffmpeg_tools as ft
        from mixcut import preview_proxy
        from mixcut.proxy import media

        ffprobe = ft.find_ffprobe()
        if not ffprobe:
            self.skipTest("找不到项目自带 ffprobe")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "素材.mov"
            self.assertTrue(self._make_source(source, "1280x720", "30", "1"))

            preview_proxy._CFG_FILE = root / "preview_proxy.json"
            preview_proxy._CACHE_DIR = root / "cache"
            preview_proxy._INDEX = None
            preview_proxy._SERVICE = None
            preview_proxy._CAPS = None
            preview_proxy._TRANSCODER = None
            preview_proxy.set_width(960)
            self.assertEqual(preview_proxy.get_width(), 960)

            events = []
            preview_proxy.add_listener(lambda s, p, proxy, err, d, t: events.append((p, proxy, err, d, t)))
            self.assertEqual(preview_proxy.request_all([str(source)]), 1)
            self.assertTrue(wait_until(lambda: bool(preview_proxy.cached_proxy(str(source))), timeout=60))
            proxy = preview_proxy.cached_proxy(str(source))
            self.assertTrue(Path(proxy).is_file())
            self.assertEqual(preview_proxy.resolve(str(source)), proxy)
            info = media.probe_media(str(source), ffprobe)
            check = media.validate_proxy(info, proxy, 960, ffprobe)
            self.assertTrue(check.ok, check.message())
            self.assertTrue(any(e[1] == proxy for e in events))
            self.assertEqual(preview_proxy.queue_state()["done"], 1)
            preview_proxy.cancel()


if __name__ == "__main__":
    unittest.main(verbosity=2)
