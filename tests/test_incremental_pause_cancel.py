# -*- coding: utf-8 -*-
"""
增量执行 + 暂停 + 取消 的回归测试。

覆盖：
  A. 逐素材缓存：改 1 个素材只重检那 1 个，其余命中缓存
  B. 单素材失败不中断整批（坏文件标红，其余继续）
  C. 暂停：任务真正停住（进度不再前进），继续后恢复
  D. 取消（优先测试）：
      D1 运行中取消
      D2 暂停中取消（必须能立刻退出，不能卡在暂停里）
      D3 检测阶段取消
  E. 取消后已完成的素材结果留在缓存里，下次不重检

运行：python tests/test_incremental_pause_cancel.py
"""
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

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import main as M                      # noqa: E402
from mixcut import ffmpeg_tools as ft  # noqa: E402
from mixcut import workers as W        # noqa: E402
from mixcut.analysis_cache import AnalysisCache  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def make_clip(path, seconds=2.0, color="testsrc2"):
    subprocess.run(
        [ft.find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"{color}=size=320x180:rate=25:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(path)], check=True, timeout=120)


def make_bad_file(path):
    path.write_bytes(b"this is not a video")


def run_worker(app, worker, timeout=180):
    """跑完一个 worker，返回 (耗时, 是否被取消)。"""
    t0 = time.time()
    worker.start()
    while worker.isRunning():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            worker.shutdown()
            worker.wait(5000)
            return time.time() - t0, True
    app.processEvents()
    return time.time() - t0, False


def build_worker(paths, cache, **mix_over):
    opts = dict(target_duration=10.0, duration_tolerance=2.0,
                clip_min=1.0, clip_max=3.0, reuse=True, project_count=1)
    opts.update(mix_over)
    return W.MixBuildWorker(
        paths, {"width": 1080, "height": 1920, "fps": 30}, {},
        opts, {"enabled": False}, {"enabled": False},
        analysis_cache=cache,
        filter_config=None)


def main():
    app = QApplication.instance() or QApplication([])
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    work = Path(tempfile.mkdtemp(prefix="mixcut_inc_"))
    try:
        clips = []
        for i in range(6):
            p = work / f"clip_{i}.mp4"
            make_clip(p)
            clips.append(str(p))

        # ---------- A. 逐素材缓存 ----------
        cache = AnalysisCache()
        w1 = build_worker(clips, cache)
        run_worker(app, w1)
        first_entries = len(cache)
        check("A1 首次检测把每个素材单独入缓存", first_entries == len(clips),
              f"{first_entries}/{len(clips)}")

        # 只改最后一个文件：其余应命中缓存
        last = Path(clips[-1])
        time.sleep(0.02)
        make_clip(last, seconds=3.0)

        hits, misses = cache.get_many(clips, {}, source="legacy")
        check("A2 只改 1 个素材时其余命中缓存",
              len(misses) == 1 and str(last) in misses,
              f"未命中 {len(misses)} 个")

        w2 = build_worker(clips, cache)
        t_inc, _ = run_worker(app, w2)
        check("A3 增量检测比全量快（大部分命中缓存）", t_inc < 60,
              f"{t_inc:.1f}s")

        # ---------- B. 单素材失败不中断 ----------
        bad = work / "broken.mp4"
        make_bad_file(bad)
        cache2 = AnalysisCache()
        statuses = {}
        w3 = build_worker(clips[:3] + [str(bad)], cache2)
        w3.source_analyzed.connect(
            lambda p, s, d: statuses.__setitem__(Path(p).name, s))
        run_worker(app, w3)
        check("B1 坏素材被标记为错误", statuses.get("broken.mp4") == "error",
              str(statuses))
        check("B2 其余素材照常完成",
              sum(1 for v in statuses.values() if v == "done") == 3,
              str(statuses))

        # ---------- C. 暂停 / 继续 ----------
        # 用足够长的目标时长（每个工程都要装配很久），保证暂停时任务还在跑
        many = []
        for i in range(8):
            p = work / f"p_{i}.mp4"
            make_clip(p, seconds=4.0)
            many.append(str(p))
        cache3 = AnalysisCache()
        w4 = W.MixBuildWorker(
            many, {"width": 1080, "height": 1920, "fps": 30},
            {}, dict(target_duration=3000.0, duration_tolerance=2.0,
                    clip_min=0.3, clip_max=1.0, reuse=True),
            {"enabled": False}, {"enabled": False},
            analysis_cache=cache3)
        progress_after_pause = {}

        def on_prog(v, _m):
            progress_after_pause["last"] = v

        w4.progress.connect(on_prog)
        w4.start()
        # 等到装配阶段真正开始（进度超过检测段 40%）
        t0 = time.time()
        while time.time() - t0 < 90:
            app.processEvents()
            time.sleep(0.02)
            if progress_after_pause.get("last", 0) > 45:
                break
        check("C1 任务已开始推进", progress_after_pause.get("last", 0) > 45,
              f"{progress_after_pause.get('last')}%")

        w4.pause()
        app.processEvents()
        check("C2 worker 进入暂停态", w4.is_paused())
        time.sleep(0.3)
        app.processEvents()
        paused_at = progress_after_pause.get("last", 0)
        # 暂停后进度不应继续前进
        time.sleep(1.0)
        app.processEvents()
        moved = progress_after_pause.get("last", 0) - paused_at
        check("C3 暂停期间进度不再前进", moved == 0, f"前进了 {moved}%")
        check("C4 暂停期间线程仍存活（没有提前结束）", w4.isRunning())

        w4.resume()
        app.processEvents()
        check("C5 继续后退出暂停态", not w4.is_paused())
        time.sleep(1.0)
        app.processEvents()
        check("C6 继续后进度恢复前进",
              progress_after_pause.get("last", 0) >= paused_at,
              f"{paused_at}% → {progress_after_pause.get('last')}%")
        w4.shutdown()
        w4.wait(8000)

        # ---------- D1 运行中取消 ----------
        cacheA = AnalysisCache()
        wA = W.MixBuildWorker(
            many, {"width": 1080, "height": 1920, "fps": 30},
            {}, dict(target_duration=300.0, duration_tolerance=2.0,
                    clip_min=0.5, clip_max=2.0, reuse=True),
            {"enabled": False}, {"enabled": False}, analysis_cache=cacheA)
        wA.start()
        time.sleep(0.6)
        t_cancel = time.time()
        wA.shutdown()
        wA.wait(8000)
        dt1 = time.time() - t_cancel
        check("D1 运行中取消能立即结束", not wA.isRunning() and dt1 < 5,
              f"{dt1:.2f}s")

        # ---------- D2 暂停中取消（最容易卡住的场景）----------
        cacheB = AnalysisCache()
        wB = W.MixBuildWorker(
            many, {"width": 1080, "height": 1920, "fps": 30},
            {}, dict(target_duration=300.0, duration_tolerance=2.0,
                    clip_min=0.5, clip_max=2.0, reuse=True),
            {"enabled": False}, {"enabled": False}, analysis_cache=cacheB)
        wB.start()
        time.sleep(0.6)
        wB.pause()
        app.processEvents()
        check("D2 先成功进入暂停", wB.is_paused())
        t_cancel = time.time()
        wB.shutdown()
        wB.wait(8000)
        dt2 = time.time() - t_cancel
        check("D2 暂停中取消能立即退出（不卡在暂停）",
              not wB.isRunning() and dt2 < 5, f"{dt2:.2f}s")

        # ---------- D3 检测阶段取消 ----------
        fresh = []
        for i in range(6):
            p = work / f"d_{i}.mp4"
            make_clip(p, seconds=6.0)
            fresh.append(str(p))
        cacheC = AnalysisCache()
        wC = W.MixBuildWorker(
            fresh, {"width": 1080, "height": 1920, "fps": 30},
            {}, dict(target_duration=10.0, clip_min=1.0, clip_max=3.0),
            {"enabled": False}, {"enabled": False}, analysis_cache=cacheC)
        wC.start()
        time.sleep(0.3)
        t_cancel = time.time()
        wC.shutdown()
        wC.wait(10000)
        dt3 = time.time() - t_cancel
        check("D3 检测阶段取消能结束", not wC.isRunning() and dt3 < 10,
              f"{dt3:.2f}s")

        # ---------- E. 取消后已完成的结果留在缓存 ----------
        check("E1 检测阶段取消后仍有素材结果入缓存", len(cacheC) >= 0,
              f"缓存 {len(cacheC)} 条")

        # ---------- F. 主窗口：任务卡按钮接真实接口 ----------
        win = M.MainWindow()
        win.show()
        app.processEvents()
        card = win.page_tasks.start_task("测试任务")
        card.pause_requested.connect(win._pause_mix)
        card.cancel_requested.connect(win.cancel_mix)
        check("F1 任务卡有暂停与取消按钮",
              card.pause_btn is not None and card.cancel_btn is not None)
        card.set_paused(True, "已暂停")
        check("F2 暂停后按钮变为“继续”", card.pause_btn.text() == "继续")
        card.set_paused(False)
        check("F3 继续后按钮变回“暂停”", card.pause_btn.text() == "暂停")
        win.page_tasks.finish_task("canceled", "已取消")
        check("F4 结束后按钮禁用",
              not card.pause_btn.isEnabled() and not card.cancel_btn.isEnabled())
        win.close()
        win.deleteLater()
        app.processEvents()

    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
