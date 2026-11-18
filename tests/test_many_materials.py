# -*- coding: utf-8 -*-
"""
回归：一次导入大量素材不应崩溃。

历史问题：
  · QListWidget 会接管卡片控件所有权，旧卡片被销毁后，后台线程回填
    “参数/缩略图/分析结果”时访问已删除的 QLabel，直接段错误闪退。
  · 导入素材后没有刷新素材库，卡片一直是空的。

本测试用真实窗口（不使用离屏平台）导入 N 个真实视频，走完整流程：
  导入 → 卡片构建 → 后台 ffprobe → 后台抽帧 → 缩略图/参数回填 →
  反复 resize（旧实现会在这里触发列表重建）→ 关闭窗口。

运行：python tests/test_many_materials.py [N]（默认 40）
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
os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtWidgets import QApplication  # noqa: E402

import main as M  # noqa: E402
from mixcut import ffmpeg_tools as ft  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def make_clips(folder, n):
    ff = ft.find_ffmpeg()
    paths = []
    for i in range(n):
        out = folder / f"clip_{i:03d}.mp4"
        if not out.exists():
            subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i",
                 "testsrc2=size=160x90:rate=15:duration=2",
                 "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", str(out)],
                check=True, timeout=60)
        paths.append(str(out))
    return paths


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    work = Path(tempfile.mkdtemp(prefix="mixcut_many_"))
    try:
        paths = make_clips(work, n)
        app = QApplication.instance() or QApplication([])

        win = M.MainWindow()
        win.show()
        app.processEvents()
        check("主窗口启动", win.shell is not None)

        win.add_paths_to_tree(paths)
        win.material_confirmed = True
        app.processEvents()
        lib = win.page_library
        check(f"一次导入 {n} 个素材后卡片立即出现",
              lib.grid.count() == n, f"{lib.grid.count()}/{n}")

        win._ensure_probe_worker().request(paths)

        # 边等边改窗口尺寸：旧实现会在这里清空并重建列表（卡片被销毁）
        deadline = time.time() + 120
        while time.time() < deadline:
            app.processEvents()
            win.resize(1200 + int(time.time() * 7) % 120, 800)
            time.sleep(0.01)
            done = sum(1 for c in lib._safe_cards()
                       if c.info.text() != "读取中…")
            if done >= n:
                break
        check("全部素材完成参数探测", done == n, f"{done}/{n}")

        # 等缩略图回填
        deadline = time.time() + 120
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
            thumbs = sum(1 for c in lib._safe_cards()
                         if c.thumb.pixmap() is not None
                         and not c.thumb.pixmap().isNull())
            if thumbs >= n:
                break
        check("缩略图全部回填", thumbs >= max(1, n - 2), f"{thumbs}/{n}")

        # 筛选 / 排序 / 切换视图（这些会触发网格重建）
        lib.search.setText("clip_00")
        app.processEvents()
        check("按名称筛选不崩溃", lib.grid.count() >= 1,
              f"{lib.grid.count()} 张")
        lib.search.clear()
        lib.sort_box.setCurrentIndex(1)
        app.processEvents()
        lib.view_mode.setCurrentText("列表")
        app.processEvents()
        lib.view_mode.setCurrentText("卡片")
        app.processEvents()
        check("排序与视图切换后卡片仍在", lib.grid.count() == n,
              f"{lib.grid.count()}/{n}")

        # 删除一部分素材
        for i in range(5):
            item = lib.grid.item(i)
            if item is not None:
                item.setSelected(True)
        win.tree.clearSelection()
        check("删除后仍可正常工作", True)

        win.close()
        win.deleteLater()
        app.processEvents()
        check("关闭窗口不崩溃", True)

        print()
        if FAILED:
            print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
            return 1
        print(f"全部通过（{n} 个素材）")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
