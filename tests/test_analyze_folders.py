# -*- coding: utf-8 -*-
"""分析页 / 素材库回归测试（离屏，不依赖真实视频）。

覆盖 2026-11-18 用户报告的逐项问题：
  A. 拖进文件夹后，在「全部素材」里不能再看到已归类片段
  B. 拖入 / 右键加入文件夹不自动打开目标文件夹
  C. 新建文件夹、移入、进入、返回全部可用，计数同步
  D. Shift / Ctrl 多选，多选后拖动带走整组
  E. 移出混剪池只影响选中的片段（80 片段不再只剩 32）
  F. 右键片段有「加入文件夹」，右键文件夹有删除
  G. 删除文件夹提示文案
  H. 拖进文件夹的片段是否仍在混剪池、是否会进入混剪
"""
import os
import sys

# 诊断用：卡住 90 秒就打印全部线程栈并退出，便于定位离屏测试里的阻塞点。
try:
    import faulthandler
    faulthandler.dump_traceback_later(90, exit=True)
except Exception:
    pass

# Windows 控制台默认 GBK，中文断言里出现特殊字符会写不出去；
# 统一改成 UTF-8，保证 PASS/FAIL 一定落盘。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MIXCUT_NO_RECOVERY", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QMimeData, QPoint
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)

from mixcut.ui.analyze_page import AnalyzePage

FAILS = []


def check(cond, label, extra=""):
    if cond:
        print("PASS  %s" % label, flush=True)
    else:
        print("FAIL  %s %s" % (label, extra), flush=True)
        FAILS.append(label)


class FakeWin:
    def __init__(self):
        self._confirmed_paths = []
        self._probe_cache = {}
        self.selectors = 0

    def _demo_clip(self, path):
        return None

    def refresh_selectors(self):
        self.selectors += 1


win = FakeWin()
print("step: building page", flush=True)
page = AnalyzePage(win)
print("step: page built", flush=True)
page._auto_preview = False
page.resize(1500, 950)
page.show()
app.processEvents()

# ---- 造 80 个片段（4 个素材 × 20 段） ----
items = []
for f in range(4):
    path = r"D:\素材\clip%d.mp4" % f
    for s in range(20):
        items.append({
            "path": path, "name": "clip%d.mp4" % f, "seg_index": s,
            "seg_start": s * 5.0, "seg_end": s * 5.0 + 5.0,
            "duration": 5.0, "score": 80.0, "is_bad": False,
            "included": True, "source_duration": 100.0,
            "frag_id": "f%d-%d" % (f, s),
        })
page.load_results(items, finished=True)
app.processEvents()
check(len(page._results) == 80, "载入 80 个片段", len(page._results))
check(len(page.pool_fragment_ids()) == 80, "默认 80 个片段全部在混剪池",
      len(page.pool_fragment_ids()))
check(len(page.cards) == 80, "「全部素材」显示 80 张卡片", len(page.cards))

# ---- C. 新建文件夹 ----
import mixcut.ui.analyze_page as AP

_orig_input = AP.QInputDialog.getText
AP.QInputDialog.getText = staticmethod(lambda *a, **k: ("A组", True))
page._pool_new_folder()
AP.QInputDialog.getText = _orig_input
app.processEvents()
folders = page._pool_folders
check(len(folders) == 1 and folders[0]["name"] == "A组", "新建文件夹 A组", folders)
check(page._current_folder_id == "__all__",
      "新建文件夹不自动进入（仍停在全部素材）", page._current_folder_id)

# ---- A / B / H. 拖 3 个片段到左侧文件夹行（模拟真实拖放） ----
drag_ids = [page._frag_id(page._results[0]), page._frag_id(page._results[1]),
            page._frag_id(page._results[2])]
mime = QMimeData()
mime.setData("application/x-mixcut-frag-ids",
             ",".join(drag_ids).encode("utf-8"))
row_item = page.folder_list.item(1)
row_pt = page.folder_list.visualItemRect(row_item).center()
page.folder_list.dropEvent = None  # 防止误用；直接走信号和投递逻辑


class _Drop:
    def __init__(self, mime, pos):
        self._mime = mime
        self._pos = pos

    def __getattr__(self, name):
        # 测试桩：任何未实现的 QDropEvent 接口都当无操作处理
        return lambda *a, **k: None

    def mimeData(self):
        return self._mime

    def position(self):
        return self._pos

    def acceptProposedAction(self):
        pass

    def accept(self):
        pass

    def ignore(self):
        pass


from PySide6.QtCore import QPointF
from mixcut.ui.analyze_page import FolderList
FolderList.dropEvent(page.folder_list, _Drop(mime, QPointF(row_pt)))
app.processEvents()
check(page._current_folder_id == "__all__", "拖入文件夹不自动打开目标文件夹",
      page._current_folder_id)
shown = {page._frag_id(page._results[r]) for r in page._rows_in_current_folder()}
check(not (set(drag_ids) & shown),
      "拖进文件夹的片段不再出现在「全部素材」", sorted(set(drag_ids) & shown))
check(len(page.cards) == 77, "全部素材剩 77 张卡片", len(page.cards))
left_row_text = page.folder_list.item(1).text()
check("3" in left_row_text, "左侧文件夹栏计数同步为 3", left_row_text)
counts = page.pool_state()["folders"][0]["count"]
check(counts == 3, "文件夹计数同步为 3", counts)
check(set(drag_ids) <= page.pool_fragment_ids(),
      "拖进文件夹的片段仍在混剪池（自动留在池里）",
      len(page.pool_fragment_ids()))
check(len(page.pool_fragment_ids()) == 80, "池内片段仍是 80",
      len(page.pool_fragment_ids()))

# 混剪是否真的拿到这些片段
pool_now = page.fragment_pool(pool_only=True)
check(len(pool_now) == 80 and set(drag_ids) <= {f["frag_id"] for f in pool_now},
      "fragment_pool(pool_only) 里包含文件夹内片段", len(pool_now))

# ---- 进入 / 返回 ----
page._enter_folder(folders[0]["id"])
app.processEvents()
check(len(page.cards) == 3, "进入文件夹只显示 3 张卡片", len(page.cards))
check(page.folder_back_btn.isEnabled(), "进入文件夹后「返回」可用")
page._enter_folder("__all__")
app.processEvents()
check(len(page.cards) == 77, "返回全部素材恢复 77 张卡片", len(page.cards))
check(not page.folder_back_btn.isEnabled(), "全部素材时「返回」禁用")

# 左侧文件夹行点击进入
page.folder_list.setCurrentRow(1)
app.processEvents()
check(page._current_folder_id == folders[0]["id"],
      "左侧文件夹栏点击可进入", page._current_folder_id)
page.folder_list.setCurrentRow(0)
app.processEvents()
check(page._current_folder_id == "__all__", "左侧「全部素材」行可返回",
      page._current_folder_id)

# ---- 新需求：拖入的片段排在文件夹最前面；文件夹永远置顶且顺序固定 ----
AP.QInputDialog.getText = staticmethod(lambda *a, **k: ("B组", True))
page._pool_new_folder()
AP.QInputDialog.getText = staticmethod(lambda *a, **k: ("C组", True))
page._pool_new_folder()
AP.QInputDialog.getText = _orig_input
app.processEvents()
# 先看文件夹栏顺序：全部素材 + A组 + B组 + C组，和创建顺序一致
names = [page.folder_list.item(i).text().split()[0]
         for i in range(page.folder_list.count())]
check(names[0] == "全部素材" and names[1:] == ["A组", "B组", "C组"],
      "文件夹按创建顺序固定（全部素材在最上面）", names)
# B组、C组都是空的，也必须在原位、不能沉底
texts = [page.folder_list.item(i).text() for i in range(page.folder_list.count())]
check("B组" in texts[2] and "C组" in texts[3],
      "空文件夹仍固定在顶部原位置，不沉底", texts)

fid_a = folders[0]["id"]
later = [page._frag_id(page._results[50]), page._frag_id(page._results[51])]
page.move_frags_to_folder(later, fid_a)
app.processEvents()
page._enter_folder(fid_a)
app.processEvents()
top_ids = [page._frag_id(page._results[r]) for r in page._rows_in_current_folder_ordered()]
check(top_ids[:2] == later, "刚拖进文件夹的片段排在最前面", top_ids[:3])
earlier = [page._frag_id(page._results[0])]
page.move_frags_to_folder(earlier, fid_a)
app.processEvents()
page._enter_folder(fid_a)
app.processEvents()
top_ids = [page._frag_id(page._results[r]) for r in page._rows_in_current_folder_ordered()]
check(top_ids[:1] == earlier, "再拖入的片段置顶到最前（旧的不动）", top_ids[:3])

# 之后新建了 B组 / C组，A组可能不再是索引 0：统一按名字取 id，避免测试自身错位。
def _fid(name):
    return next(f["id"] for f in page._pool_folders if f["name"] == name)


# ---- D. 多选 + 多选拖动 ----
# 注意：此处要在「全部素材」里操作，已归类片段不再出现在这里，
# 所以按当前实际可见的卡片来取索引。
page._enter_folder("__all__")
app.processEvents()
rows = sorted(page.cards)
check(len(rows) >= 13, "「全部素材」里还有足够多未归类片段做多选", len(rows))
page._on_card_clicked(rows[5], Qt.KeyboardModifier.NoModifier)
page._on_card_clicked(rows[9], Qt.KeyboardModifier.ShiftModifier)
check(len(page._selected_rows) == 5, "Shift 连选 5 张", len(page._selected_rows))
ids = page._drag_ids_for_row(rows[5])
check(len(ids) == 5, "多选后拖任意一张都带走整组 5 个", len(ids))
# 拖动时重新点击某张卡（无修饰键）不应丢掉整组
page._on_card_clicked(rows[6], Qt.KeyboardModifier.NoModifier)
check(len(page._selected_rows) == 5, "按住已选卡片开始拖动不会塌成单选",
      len(page._selected_rows))
page._on_card_clicked(rows[12], Qt.KeyboardModifier.ControlModifier)
check(len(page._selected_rows) == 6, "Ctrl 点选追加", len(page._selected_rows))
page._on_card_clicked(rows[12], Qt.KeyboardModifier.ControlModifier)
check(len(page._selected_rows) == 5, "Ctrl 再点取消该片段",
      len(page._selected_rows))

# ---- 取消选择：工具栏按钮 + 点击网格空白处 ----
check(page.pool_clear_btn.isEnabled(), "有选择时「取消选择」按钮可用")
cleared = page.clear_selection()
check(cleared == 5 and not page._selected_rows, "「取消选择」清空全部选择", cleared)
check(not page.pool_clear_btn.isEnabled(), "没有选择时「取消选择」按钮置灰")
check(not any(c.selected for c in page.cards.values()),
      "清空后卡片不再显示选中态")

# 模拟点网格空白处
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtCore import QPointF, QEvent  # noqa: E402


def _click_blank(target):
    """找一个确实没有任何卡片覆盖的位置再点。"""
    covered = []
    for widget in list(page.cards.values()) + list(page.folder_tiles.values()):
        try:
            covered.append(widget.geometry())
        except RuntimeError:
            continue
    vp = target.rect()
    for y in range(vp.bottom() - 5, vp.top(), -20):
        for x in range(vp.left() + 5, vp.right(), 20):
            pt = QPoint(x, y)
            if not any(r.contains(pt) for r in covered):
                pos = QPointF(pt)
                ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos,
                                 target.mapToGlobal(pt),
                                 Qt.MouseButton.LeftButton,
                                 Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier)
                return page.eventFilter(target, ev)
    return None


page._on_card_clicked(rows[5], Qt.KeyboardModifier.NoModifier)
page._on_card_clicked(rows[6], Qt.KeyboardModifier.ShiftModifier)
check(len(page._selected_rows) == 2, "再次选 2 张", len(page._selected_rows))
check(_click_blank(page.grid_scroll.viewport()),
      "点击网格空白处会处理该事件")
check(not page._selected_rows, "点击网格空白处取消全部选择",
      page._selected_rows)
check(not any(c.selected for c in page.cards.values()),
      "点空白后卡片不再显示选中态")

# 多选拖动 → 文件夹


def _count_of(name):
    return next(int(f.get("count", 0)) for f in page.pool_state()["folders"]
                if f["name"] == name)


page._on_card_clicked(rows[5], Qt.KeyboardModifier.NoModifier)
page._on_card_clicked(rows[6], Qt.KeyboardModifier.ShiftModifier)
sel_ids = [page._frag_id(page._results[r]) for r in sorted(page._selected_rows)]
a_before = _count_of("A组")
page._move_frags_to_folder(page._drag_ids_for_row(rows[5]), _fid("A组"))
app.processEvents()

check(_count_of("A组") == a_before + len(sel_ids),
      "多选移入文件夹计数 = 原有 + 选中数",
      "%d -> %d (+%d)" % (a_before, _count_of("A组"), len(sel_ids)))

# ---- E. 移出混剪池只影响选中的：模拟「随便点一个片段 + 点移出」 ----
page._enter_folder("__all__")
app.processEvents()
rows = sorted(page.cards)
page._on_card_clicked(rows[0], Qt.KeyboardModifier.NoModifier)
before = len(page.pool_fragment_ids())
page._pool_remove_selected()
after = len(page.pool_fragment_ids())
check(after == before - 1, "移出混剪池只去掉点选的那 1 个片段",
      "%d -> %d" % (before, after))

# ---- 复选框「池」只改自己 ----
page._enter_folder("__all__")
app.processEvents()
pool_before = len(page.pool_fragment_ids())
page._on_card_pool_checked(sorted(page.cards)[0], True)
check(len(page.pool_fragment_ids()) == pool_before + 1,
      "「池」勾选只加回 1 个", len(page.pool_fragment_ids()))

# ---- F. 右键菜单：加入文件夹 / 删除文件夹 ----
seen_menus = []


def _cap_exec(menu, pos):
    seen_menus.append(menu)
    return None


page._exec_menu = _cap_exec


def _titles(menu):
    out = []
    for act in menu.actions():
        sub = act.menu()
        if sub is not None:
            out.append("+" + act.text() + ":" + "/".join(
                x.text() for x in sub.actions() if not x.isSeparator()))
        else:
            out.append(act.text())
    return out


page._on_card_menu(sorted(page.cards)[3], QPoint(0, 0))
card_menu = [m for m in seen_menus
             if any("加入文件夹" == a.text() for a in m.actions())]
check(bool(card_menu), "片段右键菜单有「加入文件夹」",
      _titles(seen_menus[-1]) if seen_menus else "no menu")
if card_menu:
    sub = [a.menu() for a in card_menu[-1].actions()
           if a.text() == "加入文件夹"][0]
    check(any(x.text() == "A组" for x in sub.actions()),
          "「加入文件夹」子菜单列出已有文件夹",
          [x.text() for x in sub.actions()])

seen_menus.clear()
page._show_folder_menu(_fid("A组"), QPoint(0, 0))
folder_menu = seen_menus[-1]
check(any("删除文件夹" in a.text() for a in folder_menu.actions()),
      "右键文件夹菜单有「删除文件夹」", _titles(folder_menu))

# ---- C. 右键「加入文件夹」真正生效 ----
page._on_card_clicked(sorted(page.cards)[3], Qt.KeyboardModifier.NoModifier)
target_fid = page._frag_id(page._results[sorted(page.cards)[3]])
page._move_frags_to_folder([target_fid], _fid("A组"))
check(page.folder_of_frag(target_fid) == _fid("A组"),
      "「加入文件夹」把片段归入目标文件夹", page.folder_of_frag(target_fid))
check(page._current_folder_id == "__all__", "右键加入文件夹不切视图",
      page._current_folder_id)

# ---- G. 删除文件夹文案 ----
asked = {}


def _fake_q(parent, title, text, *a, **k):
    asked["title"] = title
    asked["text"] = text
    return QMessageBox.StandardButton.No


_orig_q = QMessageBox.question
QMessageBox.question = staticmethod(_fake_q)
page._delete_folder_confirm(_fid("A组"))
QMessageBox.question = _orig_q
check("片段会回到已分析库，不会删除分析结果" in asked.get("text", ""),
      "删除文件夹提示文案正确", asked.get("text"))
check(len(page._pool_folders) == 3, "选「否」时不会删除文件夹",
      len(page._pool_folders))

QMessageBox.question = staticmethod(
    lambda *a, **k: QMessageBox.StandardButton.Yes)
page._delete_folder_confirm(_fid("A组"))
QMessageBox.question = _orig_q
check(len(page._pool_folders) == 2, "只删掉 A组，B组/C组仍在",
      [f["name"] for f in page._pool_folders])
check(len(page._results) == 80, "删除文件夹不删分析结果", len(page._results))
check(len(page.cards) == len(page._rows_in_current_folder()),
      "片段回到已分析库（全部素材全部可见）", len(page.cards))

# ---- 文件夹卡片位置：必须在网格第一行，不随片段数量漂移 ----
page._enter_folder("__all__")
app.processEvents()
page._sync_folder_tiles()
page._relayout_cards()
app.processEvents()
check(len(page.folder_tiles) == 2, "「全部素材」下显示全部文件夹卡片",
      len(page.folder_tiles))


def _grid_positions():
    """直接读页面的排列计划：文件夹卡片必须在第 0 行，片段在其下方。"""
    plan = page.layout_plan()
    tile_rows = [r for kind, _i, r, _c in plan if kind == "folder"]
    frag_rows = [r for kind, _i, r, _c in plan if kind == "frag"]
    return tile_rows, frag_rows


tile_rows, frag_rows = _grid_positions()
check(tile_rows and max(tile_rows) == 0,
      "文件夹卡片都在网格第 0 行（顶部固定）", tile_rows)
check(not frag_rows or all(r >= 1 for r in frag_rows),
      "片段卡片都在文件夹卡片下面", sorted(set(frag_rows))[:3])
check(len(page.folder_tiles) == len(tile_rows),
      "网格里的文件夹卡片数量与排列计划一致",
      (len(page.folder_tiles), len(tile_rows)))

# 把「全部素材」里的片段全部归类后，文件夹卡片仍在第一行，不掉到底部
all_unfiled = [page._frag_id(page._results[r])
               for r in page._rows_in_current_folder()]
page.move_frags_to_folder(all_unfiled, _fid("B组"))
app.processEvents()
page._enter_folder("__all__")
app.processEvents()
check(len(page._rows_in_current_folder()) == 0,
      "全部素材已清空（片段都进了文件夹）", len(page._rows_in_current_folder()))
check(page.grid_scroll.isVisibleTo(page), "没有片段时网格仍显示（文件夹卡片还在）")
row_pos, _frag = _grid_positions()
check(row_pos and max(row_pos) == 0, "片段全归类后文件夹卡片仍在第一行", row_pos)

# 关键混合场景：文件夹卡片 + 片段同时存在时，文件夹必须占满第一行、
# 片段从下一行开始，不能插到文件夹前面。
page._enter_folder(_fid("B组"))
app.processEvents()
mixed = [page._frag_id(page._results[r])
         for r in page._rows_in_current_folder()[:4]]
# 把几条从 B组 放回“全部素材”（清掉归类）
for fid in mixed:
    page._pool_folder_of.pop(fid, None)
page._enter_folder("__all__")
page._relayout_cards()
app.processEvents()
tile_rows, frag_rows2 = _grid_positions()
check(bool(frag_rows2), "混合场景下「全部素材」有片段可测", len(frag_rows2))
check(bool(tile_rows) and bool(frag_rows2)
      and max(tile_rows) < min(frag_rows2),
      "文件夹占第一行、片段从下一行开始",
      (tile_rows, sorted(set(frag_rows2))))

# 进入具体文件夹时收起文件夹卡片，返回后恢复
page._enter_folder(_fid("B组"))
app.processEvents()
check(not page.folder_tiles, "进入具体文件夹后不显示文件夹卡片",
      list(page.folder_tiles))
page._enter_folder("__all__")
app.processEvents()
check(len(page.folder_tiles) == 2, "返回全部素材后文件夹卡片恢复",
      len(page.folder_tiles))

# ---- 在文件夹里新建文件夹：不嵌套、不改名、停在原视图 ----
page._enter_folder(_fid("B组"))
app.processEvents()
AP.QInputDialog.getText = staticmethod(lambda *a, **k: ("C组", True))
page._pool_new_folder()
AP.QInputDialog.getText = _orig_input
app.processEvents()
check(len([f for f in page._pool_folders if f["name"] == "C组"]) == 1,
      "同名文件夹只保留一个（不会嵌套出第二个）",
      [f["name"] for f in page._pool_folders])
check(all(not f.get("parent") for f in page._pool_folders),
      "所有文件夹都是平级（没有父子嵌套）",
      [(f["name"], f.get("parent")) for f in page._pool_folders])
check(page._current_folder_id == _fid("B组"),
      "在文件夹里新建后仍停在原文件夹，不会被塞进新建的文件夹",
      page._current_folder_id)

# 新建的文件夹同时出现在左侧栏与网格，且在固定位置
texts = [page.folder_list.item(i).text() for i in range(page.folder_list.count())]
check(any("C组" in t for t in texts), "新建文件夹立刻出现在左侧文件夹栏", texts)

print()
if FAILS:
    print("FAILED %d:" % len(FAILS), FAILS)
    sys.exit(1)
print("ALL PASS")
# 离屏环境 Qt 退出时析构视频控件会崩，这里直接结束进程，避免假的非零退出码。
os._exit(0)
