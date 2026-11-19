# -*- coding: utf-8 -*-
"""验证页面合并与确认后跳转。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import main as M  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


app = QApplication([])
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

win = M.MainWindow()
win.show()
app.processEvents()

# 1 导航只剩四项，且没有“素材库”
items = [k for k in win.shell.sidebar.items]
labels = [win.shell.sidebar.items[k].text() for k in items]
check("1 导航只有 工作台/混剪配置/批量任务/设置",
      items == ["workbench", "mix", "tasks", "settings"], str(labels))
check("1b 已删除“素材库”导航项", "素材库" not in labels)

# 2 工作台 = 素材库 + 预览
check("2 工作台承载素材库页面",
      win.shell._pages["workbench"] is not None
      and win.page_library.parent() is not None)
slots = win.page_library.preview_slot.count()
check("2b 素材库右侧是预览槽位且有播放器", slots == 1, f"{slots} 个控件")
check("2c 素材概况已删除", not hasattr(win, "workbench_summary"))

# 3 确认素材后自动跳到混剪配置
video = os.path.abspath("_short_video.mp4")
win.add_paths_to_tree([video])
app.processEvents()
win._navigate("workbench")
app.processEvents()
win.confirm_materials()
app.processEvents()
check("3 点确认素材后自动切到混剪配置页",
      win.shell.nav.current() == "mix", win.shell.nav.current())

win.close()
win.deleteLater()
app.processEvents()

print()
if FAILED:
    print("失败:", ", ".join(FAILED))
    sys.exit(1)
print("全部通过")
sys.exit(0)
