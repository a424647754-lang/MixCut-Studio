# -*- coding: utf-8 -*-
"""
界面级回归（离屏运行，不弹窗）：走真实 MainWindow → 后台线程 → 剪映草稿落盘。

覆盖：
  · 多工程生成：每个草稿随机排列不同、都能落盘
  · 缓存复用：第二次生成不重复检测
  · 中途取消：按钮恢复、不生成半成品草稿
  · 素材清单失效：加素材后混合选择框立即清空
  · 错误路径：failed 信号后按钮也能恢复（不再卡在运行中）

运行：python tests/test_ui_regression.py
"""

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess  # noqa: E402

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import main as M  # noqa: E402
from mixcut import ffmpeg_tools as ft  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def make_clip(path, color):
    ff = ft.find_ffmpeg()
    subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi",
         "-i", f"testsrc2=size=320x180:rate=25:duration=8,"
               f"drawbox=x=0:y=0:w=320:h=180:color={color}@0.9:t=fill:"
               f"enable=between(t\\,3\\,4)",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=8",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, timeout=120)


def wait_worker(app, win, timeout=180):
    t0 = time.time()
    while win.mix_worker is not None or win.mix_start_btn.isEnabled() is False:
        app.processEvents()
        time.sleep(0.02)
        if time.time() - t0 > timeout:
            return False
    app.processEvents()
    return True


def run(workdir):
    app = QApplication.instance() or QApplication([])
    # 弹窗一律自动确认，测试不阻塞
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)

    clip_a = Path(workdir) / "素材 A 中文.mp4"
    clip_b = Path(workdir) / "素材B.mp4"
    make_clip(clip_a, "black")
    make_clip(clip_b, "black")

    out = Path(workdir) / "drafts"
    out.mkdir()

    win = M.MainWindow()
    win.show()
    win.add_paths_to_tree([str(clip_a), str(clip_b)])
    win.material_confirmed = True
    win.refresh_selectors()
    check("选择框列出素材", win.mix_selector.list.count() == 2,
          f"{win.mix_selector.list.count()} 行")

    # —— 素材清单失效（B10）——
    clip_c = Path(workdir) / "素材C.mp4"
    make_clip(clip_c, "black")
    win.add_paths_to_tree([str(clip_c)])
    check("加素材后选择框立即清空（不再用旧清单混剪）",
          win.mix_selector.list.count() == 0
          and win.material_confirmed is False)
    win.confirm_materials()
    check("重新确认后恢复为 3 个素材",
          win.mix_selector.list.count() == 3)

    # —— 多工程生成（B2）——
    win.mix_target.setValue(16)
    win.mix_tolerance.setValue(2)
    win.mix_clip_min.setValue(2)
    win.mix_clip_max.setValue(4)
    win.mix_clip_count.setValue(2)
    win.mix_draft_dir.setText(str(out))
    win.mix_beat_sync.setChecked(False)
    win.mix_ducking.setChecked(False)
    win.start_mix()
    check("任务已启动", wait_worker(app, win))
    drafts = sorted(p.name for p in out.iterdir() if p.is_dir())
    check("生成 2 个草稿", len(drafts) == 2, str(drafts))

    contents = []
    for name in drafts:
        f = out / name / "draft_content.json"
        if f.exists():
            import json
            data = json.loads(f.read_text(encoding="utf-8"))
            segs = data["tracks"][0]["segments"]
            contents.append(tuple((s["source_timerange"]["start"],
                                   s["source_timerange"]["duration"])
                                  for s in segs))
    check("两个草稿的片段排列不同（随机生效）",
          len(contents) == 2 and contents[0] != contents[1])
    check("起始按钮已恢复", win.mix_start_btn.isEnabled())

    # —— 缓存复用（B7/B8 联动）——
    log_before = win.mix_log.toPlainText()
    win.mix_clip_count.setValue(1)
    win.mix_target.setValue(10)
    win.start_mix()
    wait_worker(app, win)
    new_log = win.mix_log.toPlainText()
    check("第二次生成命中缓存（不重复检测）",
          "缓存结果" in new_log and "复用" in new_log
          and "缓存结果" not in log_before)

    # —— 关掉画面检测后缓存必须失效（B7/B8）——
    win.mix_video_detect.setChecked(False)
    win.mix_target.setValue(8)
    win.start_mix()
    wait_worker(app, win)
    check("检测开关变化后重新检测（缓存失效）",
          "缓存结果" not in win.mix_log.toPlainText())
    win.mix_video_detect.setChecked(True)

    # —— 错误路径：失败后按钮也要恢复（B3）——
    win.mix_clip_min.setValue(5)
    win.mix_clip_max.setValue(6)
    win.mix_target.setValue(60)
    win.mix_tolerance.setValue(0)
    win.mix_reuse.setChecked(False)      # 素材不够 → 明确报错
    win.start_mix()
    check("素材不足时报错后按钮恢复（不卡在运行中）",
          wait_worker(app, win) and win.mix_start_btn.isEnabled())
    win.mix_reuse.setChecked(True)

    # 收尾：先关窗口让 QThread 正常结束，再让 Qt 释放全部控件。
    # 否则主窗口被析构时子控件已先行释放，进程退出可能崩溃（退出码异常）。
    win.close()
    win.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, 0)
    app.processEvents()
    return win


if __name__ == "__main__":
    workdir = tempfile.mkdtemp(prefix="mixcut_ui_")
    try:
        run(workdir)
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        sys.exit(1)
    print("全部通过")
    sys.exit(0)
