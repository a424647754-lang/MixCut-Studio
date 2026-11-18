# -*- coding: utf-8 -*-
"""验证拖放入口：外部文件拖到树/分类上会走 add_paths_to_tree。"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QMimeData, QPoint, QUrl, Qt  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QMessageBox, QInputDialog,
)

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
win._navigate("library")
app.processEvents()

QInputDialog.getText = staticmethod(lambda *a, **k: ("素材A类", True))
win.create_virtual_folder()
app.processEvents()
root = win.tree.invisibleRootItem()
cat = root.child(0)
check("拖放前置：分类存在", cat is not None and cat.text(0) == "素材A类")

work = Path(tempfile.mkdtemp(prefix="mixcut_drop_"))
video = work / "drop_test.mp4"
video.write_bytes(b"x")

mime = QMimeData()
mime.setUrls([QUrl.fromLocalFile(str(video))])

# 拖到分类节点的位置
rect = win.tree.visualItemRect(cat)
pos = rect.center()
event = QDropEvent(QPoint(pos), Qt.CopyAction, mime,
                   Qt.LeftButton, Qt.NoModifier)
win.tree.dropEvent(event)
app.processEvents()

paths = win.get_all_video_paths()
check("拖入的素材被导入", str(video.resolve()) in paths
      or str(video) in paths, str(paths))
check("素材挂在分类节点下", cat.childCount() == 1, cat.childCount())

# 正常收尾，避免进程退出时 Qt 对象析构崩溃
win.close()
win.deleteLater()
app.processEvents()
app.sendPostedEvents(None, 0)
app.processEvents()

if FAILED:
    print("失败:", ", ".join(FAILED))
    sys.exit(1)
print("全部通过")
sys.exit(0)
