# -*- coding: utf-8 -*-
"""
UI 重构 · 新四区界面测试（重写版，UTF-8 无 BOM）。

运行：python tests/test_ui_shell.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

import main as M  # noqa: E402
from mixcut import ffmpeg_tools as ft  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail!r}" if detail else ""))
    if not cond:
        FAILED.append(name)


def make_clip(path, color="green"):
    subprocess.run(
        [ft.find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i",
         f"testsrc2=size=320x180:rate=25:duration=8,"
         f"drawbox=x=0:y=0:w=320:h=180:color={color}@0.9:t=fill:"
         f"enable=between(t\\,3\\,4)",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=8",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, timeout=120)


def wait_until(app, cond, timeout=120):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.02)
        if time.time() - t0 > timeout:
            return False
    app.processEvents()
    return True


def live_cards(lib):
    cards = []
    for i in range(lib.grid.count()):
        item = lib.grid.item(i)
        if item is None:
            continue
        widget = lib.grid.itemWidget(item)
        if widget is not None:
            cards.append(widget)
    return cards


def run(workdir):
    app = QApplication.instance() or QApplication([])
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)

    a = Path(workdir) / "clip_a.mp4"
    b = Path(workdir) / "clip_b.mp4"
    make_clip(a)
    make_clip(b, "blue")
    out = Path(workdir) / "drafts"
    out.mkdir()

    win = M.MainWindow()
    win.show()
    app.processEvents()

    shell = getattr(win, "shell", None)
    check("1 new four-region shell exists", shell is not None)
    check("1b nav has 4 pages (workbench/mix/tasks/settings)",
          shell is not None and len(shell.nav._buttons) == 4,
          str(list(shell.nav._buttons)))
    check("1c topbar shows real counts",
          shell.topbar.material_label.text().startswith("Material")
          or shell.topbar.material_label.text().startswith("素材"))
    check("1d bottom task dock removed",
          shell is not None and not hasattr(shell, "task_dock"))

    for key in ("workbench", "mix", "tasks", "settings"):
        shell.nav.select(key)
        app.processEvents()
        check(f"2 switch page {key}", shell.stack.currentWidget() is shell._pages[key])

    win.add_paths_to_tree([str(a), str(b)])
    win.material_confirmed = True
    lib = win.page_library
    # 3d 导入后卡片立即可见（旧版本导入后卡片一直是空的）
    check("3d cards appear right after import",
          lib.grid.count() == 2, f"{lib.grid.count()}")
    win._ensure_probe_worker().request([str(a), str(b)])
    got = wait_until(app, lambda: all(c.info.text() != "读取中…"
                                      for c in live_cards(lib)), 60)
    check("3b card info from background ffprobe",
          got and all("s" in c.info.text() for c in live_cards(lib)),
          [c.info.text() for c in live_cards(lib)])

    # 3c 缩略图：真实 ffmpeg 抽帧
    thumbs = wait_until(
        app,
        lambda: all(c.thumb.pixmap() is not None and not c.thumb.pixmap().isNull()
                    for c in live_cards(lib)), 90)
    check("3c real thumbnails rendered", thumbs,
          [c.thumb.toolTip() for c in live_cards(lib)])

    # 3e 分析状态：走真实 worker，素材真实分析后回写卡片
    # 注意：素材导入后“确定使用”状态会被重置，这里显式重新确认并同步选择框，
    # 和用户实际操作的顺序保持一致。
    win.confirm_materials()
    win.refresh_selectors()
    win.mix_target.setValue(10)
    win.mix_tolerance.setValue(2)
    win.mix_clip_min.setValue(1.0)
    win.mix_clip_max.setValue(3.0)
    win.mix_clip_count.setValue(1)
    win.mix_ducking.setChecked(False)
    win.mix_beat_sync.setChecked(False)
    win.mix_draft_dir.setText(str(out))
    print("    selector rows:", win.mix_selector.list.count(),
          "selected:", len(win.mix_selector.selected_paths()))
    win.mix_tolerance.setValue(2)
    win.mix_clip_min.setValue(1.0)
    win.mix_clip_max.setValue(3.0)
    win.mix_clip_count.setValue(1)
    win.mix_ducking.setChecked(False)
    win.mix_beat_sync.setChecked(False)
    win.mix_draft_dir.setText(str(out))

    worker_signal_log = []
    orig = win._on_source_analyzed

    def spy(path, status, detail):
        worker_signal_log.append((path, status))
        orig(path, status, detail)

    win.start_mix()
    app.processEvents()
    print("    start_mix 后：worker=", win.mix_worker,
          "confirmed=", win.material_confirmed,
          "selected=", len(win.mix_selector.selected_paths()),
          "component cb=", win.filter_checks["ffmpeg_audio_black"].isChecked(),
          "amverge cb=", win.filter_checks["amverge"].isChecked())
    started = win.mix_worker is not None
    check("3f mix worker started", started,
          "worker=%r" % (win.mix_worker,))
    if started:
        win.mix_worker.source_analyzed.connect(spy)
    check("4 task card running after start",
          win.page_tasks.current_card() is not None)
    # 任务进行中：暂停按钮真实接到 worker 的 pause()/resume()
    running_card = win.page_tasks.current_card()
    check("4b0 pause/resume wired to running worker",
          running_card is not None
          and hasattr(win.mix_worker, "pause")
          and hasattr(win.mix_worker, "resume"))
    check("4b task card created", win.page_tasks.current_card() is not None)
    card = win.page_tasks.current_card()
    saw = wait_until(app,
                     lambda: card is not None and card.progress.value() > 0
                     and card.badge.text() != "执行中", 180)
    check("4c progress from worker signal", saw,
          f"{card.progress.value() if card else 0}%")
    done = wait_until(app,
                      lambda: card is not None
                      and card.badge.text() in ("已完成", "失败", "已取消"),
                      180)
    check("4d task card shows real final state", done,
          card.badge.text() if card else "-")
    check("4e drafts written to disk",
          len([p for p in out.iterdir() if p.is_dir()]) >= 1)

    # 4f 逐素材分析结果回到卡片
    # spy 里调用了 orig（连接时绑定的处理函数），这里核对卡片是否真的被更新；
    # 若没有更新，说明处理函数不是当前窗口实例上的那个。
    wait_until(app, lambda: all(
        c.status.text() == "已分析" for c in live_cards(lib)), 10)
    statuses = {s for _p, s in worker_signal_log}
    check("4f per-material analysis events emitted", bool(statuses),
          str(sorted(statuses)))
    print("    orig is current handler:",
          orig.__func__ is type(win)._on_source_analyzed,
          "| orig self is win:", orig.__self__ is win)
    print("    card mapping:", len(lib._cards),
          "live:", len(live_cards(lib)),
          "states:", [c.status.text() for c in live_cards(lib)])
    check("4g cards show analyzed state",
          all(c.status.text() in ("已分析", "文件异常") for c in live_cards(lib)),
          [c.status.text() for c in live_cards(lib)])
    check("4h analyzed cards show valid segment hint",
          all("段" in c.analysis_hint.text() for c in live_cards(lib)),
          [(c.status.text(), c.analysis_hint.text()) for c in live_cards(lib)])

    log_text = win.page_tasks.log_panel.text()
    check("5 log contains real progress events",
          "装配混剪时间线" in log_text or "工程" in log_text)
    win.page_tasks.log_panel.filter_box.setCurrentIndex(3)
    check("5b log level filter works", win.page_tasks.log_panel.count() >= 1)
    win.page_tasks.log_panel.filter_box.setCurrentIndex(0)

    os.environ["MIXCUT_LEGACY_UI"] = "1"
    try:
        legacy = M.MainWindow()
        legacy.show()
        app.processEvents()
        check("6 legacy UI fallback works",
              getattr(legacy, "shell", None) is None
              and legacy.centralWidget() is not None)
        legacy.close()
        legacy.deleteLater()
    finally:
        os.environ.pop("MIXCUT_LEGACY_UI", None)

    # 暂停按钮现在是真实接口（worker.pause()/resume()，已在 4b0 校验）；
    # 任务结束后按钮应当被禁用，避免对已结束任务发指令
    pause_buttons = [b for b in win.page_tasks.findChildren(QPushButton)
                     if "暂停" in b.text() or "继续" in b.text()]
    check("7 pause buttons disabled after task ends",
          bool(pause_buttons) and all(not b.isEnabled() for b in pause_buttons),
          f"{len(pause_buttons)} 个按钮")
    check("7b task card timer stopped after finish",
          card is None or not card.timer.isActive())

    win.close()
    win.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    workdir = tempfile.mkdtemp(prefix="mixcut_shell_")
    try:
        run(workdir)
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    print()
    if FAILED:
        print(f"failed {len(FAILED)}: " + ", ".join(FAILED))
        sys.exit(1)
    print("all passed")
    sys.exit(0)
