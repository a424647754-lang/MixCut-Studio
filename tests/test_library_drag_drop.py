# -*- coding: utf-8 -*-
"""素材库：拖拽移入文件夹 + 右键多选移动 回归测试（离屏，真实旧树）。"""
import os
import sys

try:
    import faulthandler
    faulthandler.dump_traceback_later(150, exit=True)
except Exception:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MIXCUT_NO_RECOVERY", "1")
os.environ.pop("MIXCUT_LEGACY_UI", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

app = QApplication.instance() or QApplication(sys.argv)

import main as MAIN  # noqa: E402
from mixcut.ui.demo import mock  # noqa: E402
from mixcut.ui.demo.pages_library import ClipCard, DropFolderButton  # noqa: E402

FAILS = []


def check(cond, label, extra=""):
    if cond:
        print("PASS  %s" % label, flush=True)
    else:
        print("FAIL  %s %s" % (label, extra), flush=True)
        FAILS.append(label)


win = MAIN.MainWindow()
app.processEvents()
lib = win.demo_library
tree = win.tree

# ---- 造真实旧树：根容器 + 分类「甲」+ 3 个视频 ----
tree.blockSignals(True)
tree.clear()
root_node = QTreeWidgetItem(tree, ["素材库", ""])
folder = QTreeWidgetItem(root_node, ["甲", ""])
paths = []
for name in ("a.mp4", "b.mp4", "c.mp4"):
    p = os.path.join(os.environ.get("TEMP", "."), "mixcut_t_%s" % name)
    item = QTreeWidgetItem([name, os.path.dirname(p)])
    item.setData(1, Qt.ItemDataRole.UserRole, p)
    root_node.addChild(item)
    paths.append(p)
tree.blockSignals(False)
win.reload_library(preserve_selection=False)
app.processEvents()

check(len(mock.CLIPS) == 3, "素材库载入 3 条", len(mock.CLIPS))
check(len(lib.cards) == 3, "网格有 3 张卡片", len(lib.cards))

# ---- 1. 拖拽：单条 ----
cids = [c["id"] for c in mock.CLIPS]
card = lib.cards[cids[0]]
captured = {}
from mixcut.ui.demo import pages_library as PL  # noqa: E402
orig_exec = PL.QDrag.exec


def fake_exec(self, *a, **k):
    md = self.mimeData()
    captured["paths"] = bytes(md.data(ClipCard.MIME_TYPE)).decode("utf-8").split("\n")
    return Qt.DropAction.MoveAction


PL.QDrag.exec = fake_exec
try:
    card._drag_start = QPoint(5, 5)
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(120, 120),
                     card.mapToGlobal(QPoint(120, 120)),
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    card.mouseMoveEvent(ev)
finally:
    PL.QDrag.exec = orig_exec
check(captured.get("paths") == [paths[0]], "单条拖动携带 1 条路径",
      captured.get("paths"))

# ---- 2. 拖拽：多选带走整组 ----
win.controller.selected = set(cids[:2])
win.controller.stateChanged.emit()
app.processEvents()
card2 = lib.cards[cids[0]]
check(cids[0] in win.controller.selected, "多选后点选不会把已选卡片取消",
      sorted(win.controller.selected))
captured.clear()
PL.QDrag.exec = fake_exec
try:
    card2._drag_start = QPoint(5, 5)
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(120, 120),
                     card2.mapToGlobal(QPoint(120, 120)),
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    card2.mouseMoveEvent(ev)
finally:
    PL.QDrag.exec = orig_exec
dragged = captured.get("paths") or []
check(len(dragged) == 2, "多选拖动带走整组 2 条", dragged)
check(set(dragged) == set(paths[:2]), "带走的正是选中的那 2 条", dragged)


# ---- 3. 拖进文件夹行（DropFolderButton 接收） ----
class _Drop:
    def __init__(self, mime):
        self._mime = mime
        self._accepted = False

    def mimeData(self):
        return self._mime

    def setDropAction(self, a):
        pass

    def accept(self):
        self._accepted = True

    def ignore(self):
        self._accepted = False

    def __getattr__(self, name):
        return lambda *a, **k: None


mime = QMimeData()
mime.setData(ClipCard.MIME_TYPE, "\n".join(dragged).encode("utf-8"))
win._drag_selected_paths = list(dragged)
row_btn = lib.folder_buttons.get("p:甲")
check(isinstance(row_btn, DropFolderButton), "左侧文件夹行可接收拖放",
      type(row_btn).__name__)
if row_btn is not None:
    row_btn.dragEnterEvent(_Drop(mime))
    row_btn.dropEvent(_Drop(mime))
    app.processEvents()
    in_folder = [c for c in mock.CLIPS
                 if (c.get("folder_path") or "") == "甲"]
    check(len(in_folder) == 2, "拖入文件夹后后台有 2 条在「甲」", len(in_folder))
    lib._select_folder("all")
    app.processEvents()
    check(len(lib._shown_ids) == 1, "「全部素材」只剩 1 条（拖入的不再显示）",
          sorted(lib._shown_ids))

# ---- 4. 右键多选移动 ----
win.controller.selected = set(cids)
win.controller.stateChanged.emit()
app.processEvents()
final_paths = [c["path"] for c in mock.CLIPS if c["path"]]
seen = []
lib._exec_menu = lambda menu, pos: (seen.append(menu), None)[1]
try:
    lib._clip_menu(cids[0], QPoint(0, 0))
finally:
    pass
check(bool(seen), "右键菜单弹出")
move_menu = None
for act in seen[-1].actions():
    if act.text().startswith("移动到文件夹"):
        move_menu = act.menu()
        check("3" in act.text(), "右键菜单标注作用 3 条", act.text())
check(move_menu is not None, "有「移动到文件夹」子菜单")
lib._move_many(final_paths, "甲", "甲")
app.processEvents()
in_folder2 = [c for c in mock.CLIPS if (c.get("folder_path") or "") == "甲"]
check(len(in_folder2) == 3, "右键多选移动后 3 条都在「甲」", len(in_folder2))

print()
if FAILS:
    print("FAILED %d:" % len(FAILS), FAILS, flush=True)
    os._exit(1)
print("ALL PASS", flush=True)
os._exit(0)
