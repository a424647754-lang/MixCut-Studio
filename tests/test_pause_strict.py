# -*- coding: utf-8 -*-
"""单独验证“暂停真的停住、继续真的会继续”（用足够长、进度可见推进的任务）。"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication  # noqa: E402

from mixcut import ffmpeg_tools as ft   # noqa: E402
from mixcut import workers as W         # noqa: E402
from mixcut.analysis_cache import AnalysisCache  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def main():
    app = QApplication.instance() or QApplication([])
    work = Path(tempfile.mkdtemp(prefix="mixcut_pause_"))
    try:
        clips = []
        for i in range(10):
            p = work / f"c{i}.mp4"
            subprocess.run(
                [ft.find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i",
                 "testsrc2=size=320x180:rate=25:duration=5",
                 "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", str(p)], check=True, timeout=120)
            clips.append(str(p))

        seen = []

        def prog(v, _m):
            seen.append(v)

        # 多个工程 + 长目标，让装配阶段持续一段时间
        worker = W.MixBuildWorker(
            clips, {"width": 1080, "height": 1920, "fps": 30}, {},
            dict(target_duration=2000.0, duration_tolerance=0.0,
                 clip_min=0.2, clip_max=0.6, reuse=True, project_count=3),
            {"enabled": False}, {"enabled": False},
            analysis_cache=AnalysisCache())
        worker.progress.connect(prog)
        worker.start()

        t0 = time.time()
        while time.time() - t0 < 120:
            app.processEvents()
            time.sleep(0.02)
            if len(seen) > 20:
                break
        check("任务进入装配阶段", len(seen) > 20, f"{len(seen)} 次进度")

        worker.pause()
        app.processEvents()
        time.sleep(0.35)
        app.processEvents()
        before = len(seen)
        time.sleep(1.2)
        app.processEvents()
        during = len(seen) - before
        check("暂停期间没有新的进度推进", during == 0, f"新增 {during} 次")

        worker.resume()
        app.processEvents()
        time.sleep(1.2)
        app.processEvents()
        after = len(seen) - before
        check("继续后进度重新推进", after > 0, f"新增 {after} 次")

        worker.shutdown()
        worker.wait(8000)
        check("结束后线程退出", not worker.isRunning())

    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILED:
        print("失败:", ", ".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
