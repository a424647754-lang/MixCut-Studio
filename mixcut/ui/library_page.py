# -*- coding: utf-8 -*-
"""
MixCut · 素材库页面（Lightroom 风格）

布局：
  顶部：导入/搜索/排序/筛选
  中间 QSplitter：
    左：分类树（窄）
    中：缩略图网格（紧凑，缩略图在上、文件名在下）
    右：预览大图 + Metadata 属性列表
  底部：状态栏（总数/选中数/总大小）
"""

import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QUrl, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QListWidget, QListWidgetItem, QFrame,
    QSplitter, QAbstractItemView, QPlainTextEdit, QSizePolicy,
    QTreeWidgetItemIterator, QCheckBox,
)

from . import theme
from .components import Card, Hairline, make_button, StatusBadge


VIDEO_SUFFIX = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}


def human_size(num):
    try:
        size = float(num)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return (f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}")
        size /= 1024
    return "—"


class FolderTile(QFrame):
    """网格里的文件夹 tile：双击进入，接受素材拖入。"""

    double_clicked = Signal(object)  # emit folder tree item
    clicked = Signal(object)  # emit folder tree item（单击选中，供「移除选中」用）
    dropped = Signal(object, list)  # emit (folder_item, [paths])
    checked_changed = Signal(object, bool)  # (folder_item, checked)

    def __init__(self, folder_item, parent=None):
        super().__init__(parent)
        self.folder_item = folder_item
        self.setFixedSize(168, 138)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 14, 10, 10)
        lay.setSpacing(6)

        # 左上角勾选框
        self.checkbox = QCheckBox(self)
        self.checkbox.setGeometry(4, 4, 22, 22)
        self.checkbox.setStyleSheet(
            "QCheckBox::indicator { width:18px; height:18px; }"
            "QCheckBox::indicator:unchecked { background:#22252A; border:1px solid #686D76; border-radius:3px; }"
            "QCheckBox::indicator:checked { background:#FFFFFF; border:2px solid #FFFFFF; border-radius:3px; }")
        self.checkbox.stateChanged.connect(
            lambda s: self.checked_changed.emit(self.folder_item, bool(s)))

        icon = QLabel("📁")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size:40px;background:transparent;border:none;")
        lay.addWidget(icon)

        name = QLabel(folder_item.text(0))
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        name.setStyleSheet(
            f"color:{theme.COLORS['text']};font-size:12px;font-weight:600;"
            f"background:transparent;border:none;")
        lay.addWidget(name)

        count = QLabel("")
        count.setAlignment(Qt.AlignCenter)
        count.setStyleSheet(
            f"color:{theme.COLORS['faint']};font-size:10px;background:transparent;border:none;")
        lay.addWidget(count)
        self.count_label = count
        self.setStyleSheet("background:transparent;border:none;")

    def mousePressEvent(self, event):
        # tile 是盖在 grid item 上的子控件，grid.itemClicked 收不到这里的点击，
        # 所以单击选中必须自己发信号，否则「移除选中」永远不知道用户选了文件夹。
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.folder_item)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(self.folder_item)
        super().mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-mixcut-paths"):
            event.acceptProposedAction()
            self.setStyleSheet(
                f"background:{theme.COLORS['panel_alt']};border-radius:2px;")

    def dragLeaveEvent(self, event):
        self.setStyleSheet("background:transparent;border:none;")

    def dropEvent(self, event):
        data = event.mimeData().data("application/x-mixcut-paths")
        paths = [p for p in data.data().decode().split("\n") if p]
        self.dropped.emit(self.folder_item, paths)
        self.setStyleSheet("background:transparent;border:none;")
        event.acceptProposedAction()


class MaterialCard(QFrame):
    """Lightroom 风格紧凑卡片：缩略图在上，文件名在下。"""

    clicked = Signal(str)
    activated = Signal(str)
    checked_changed = Signal(str, bool)  # (path, checked)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setObjectName("MaterialCell")
        self.path = path
        self.duration = None
        self.pixels = 0
        self.file_size = 0
        self.analysis_status = "unanalyzed"
        self._selected = False
        self.setAcceptDrops(False)

        self.setFixedSize(168, 138)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)

        # 缩略图区（16:9）
        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setFixedSize(164, 92)
        self.thumb.setStyleSheet(
            f"background:{theme.COLORS['panel_alt']};"
            f"border:1px solid {theme.COLORS['border_soft']};"
            f"border-radius:2px;")
        lay.addWidget(self.thumb)

        # 左上角勾选框（叠在缩略图上）
        self.checkbox = QCheckBox(self)
        self.checkbox.setGeometry(4, 4, 22, 22)
        self.checkbox.setStyleSheet(
            "QCheckBox::indicator { width:18px; height:18px; }"
            "QCheckBox::indicator:unchecked { background:#22252A; border:1px solid #686D76; border-radius:3px; }"
            "QCheckBox::indicator:checked { background:#FFFFFF; border:2px solid #FFFFFF; border-radius:3px; }")
        self.checkbox.setCursor(Qt.PointingHandCursor)
        self.checkbox.stateChanged.connect(self._on_check_toggled)

        # 文件名
        self.name = QLabel(Path(path).name)
        self.name.setToolTip(path)
        self.name.setStyleSheet(
            f"color:{theme.COLORS['muted']};font-size:11px;"
            f"padding-left:2px;")
        self.name.setWordWrap(False)
        lay.addWidget(self.name)

        # 状态小字
        self.status = QLabel("")
        self.status.setStyleSheet(
            f"color:{theme.COLORS['faint']};font-size:10px;padding-left:2px;")
        lay.addWidget(self.status)

    def _on_check_toggled(self, state):
        self.checked_changed.emit(self.path, bool(state))

    def set_checked_state(self, on):
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(on)
        self.checkbox.blockSignals(False)

    def set_thumbnail(self, image_path):
        pix = QPixmap(str(image_path)) if image_path else QPixmap()
        if pix.isNull():
            self.thumb.setStyleSheet(
                f"background:{theme.COLORS['panel_alt']};"
                f"border:1px solid {theme.COLORS['border_soft']};")
            self.thumb.setText("—")
            return
        self.thumb.setText("")
        self.thumb.setStyleSheet(
            f"background:transparent;border:1px solid {theme.COLORS['border_soft']};"
            f"border-radius:2px;")
        self.thumb.setPixmap(
            pix.scaled(164, 92, Qt.KeepAspectRatioByExpanding,
                       Qt.SmoothTransformation))

    def set_probe(self, details):
        fmt = (details or {}).get("format") or {}
        video = (details or {}).get("video") or {}
        dur = fmt.get("duration")
        try:
            self.file_size = int((details or {}).get("file_size") or 0)
        except (TypeError, ValueError):
            self.file_size = 0
        try:
            self.duration = float(dur) if dur else None
        except (TypeError, ValueError):
            self.duration = None
        try:
            self.pixels = int(video.get("width") or 0) * int(video.get("height") or 0)
        except (TypeError, ValueError):
            self.pixels = 0
        res = f"{video['width']}×{video['height']}" if video.get("width") else ""
        parts = []
        if self.duration:
            parts.append(f"{self.duration:.0f}s")
        if res:
            parts.append(res)
        self.status.setText(" · ".join(parts) or "—")

    def set_probe_error(self, text):
        self.status.setText("读取失败")

    def set_analysis(self, status, hint=""):
        self.analysis_status = status
        if status in ("done", "analyzed", "cached"):
            self.status.setStyleSheet(
                f"color:{theme.COLORS['success']};font-size:10px;padding-left:2px;")
        elif status == "analyzing":
            self.status.setStyleSheet(
                f"color:{theme.COLORS['warning']};font-size:10px;padding-left:2px;")
        elif status == "error":
            self.status.setStyleSheet(
                f"color:{theme.COLORS['danger']};font-size:10px;padding-left:2px;")

    def apply_meta(self, meta):
        if not meta:
            return
        self.duration = meta.get("duration")
        self.pixels = meta.get("pixels", 0) or 0
        self.file_size = meta.get("file_size", 0) or 0
        if meta.get("probe_done"):
            parts = []
            if self.duration:
                parts.append(f"{self.duration:.0f}s")
            if meta.get("width") and meta.get("height"):
                parts.append(f"{meta['width']}×{meta['height']}")
            self.status.setText(" · ".join(parts) or "—")
        if meta.get("thumb_path"):
            self.set_thumbnail(meta["thumb_path"])
        if meta.get("analysis_status"):
            self.set_analysis(meta["analysis_status"])

    def set_checked(self, checked):
        self._selected = checked
        if checked:
            self.setStyleSheet(
                f"#MaterialCell {{ background:{theme.COLORS['selected']};"
                f"border:2px solid {theme.COLORS['accent']};border-radius:2px; }}")
        else:
            self.setStyleSheet(
                f"#MaterialCell {{ background:transparent;"
                f"border:1px solid transparent;border-radius:2px; }}")

    def mousePressEvent(self, event):
        self._drag_start = event.pos()
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.path)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # 拖拽开始：把所有选中的素材路径放到 mime data
        if not hasattr(self, "_drag_start"):
            return
        if (event.buttons() & Qt.LeftButton) and \
           (event.pos() - self._drag_start).manhattanLength() > 12:
            from PySide6.QtCore import QMimeData, QDrag
            drag = QDrag(self)
            mime = QMimeData()
            # 收集当前所有选中的卡片路径
            win = self.window()
            paths = []
            grid = getattr(win, "page_library", None) or getattr(win, "library", None)
            # 简化：只拖当前这张卡
            paths.append(self.path)
            mime.setData("application/x-mixcut-paths", "\n".join(paths).encode())
            drag.setMimeData(mime)
            drag.exec(Qt.MoveAction)
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.activated.emit(self.path)
        super().mouseDoubleClickEvent(event)


class MetadataPanel(QFrame):
    """右侧元数据面板：文件名 + 属性列表。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(4)

        title = QLabel("元数据")
        title.setObjectName("SectionTitle")
        lay.addWidget(title)

        self.rows = {}
        for key in ("文件名", "时长", "分辨率", "大小", "路径"):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            k = QLabel(key)
            k.setObjectName("Faint")
            k.setFixedWidth(50)
            v = QLabel("—")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            rl.addWidget(k)
            rl.addWidget(v, 1)
            lay.addWidget(row)
            self.rows[key] = v

        lay.addStretch(1)

    def show_file(self, path, card=None):
        self.rows["文件名"].setText(Path(path).name)
        self.rows["路径"].setText(path)
        if card is not None:
            try:
                if card.duration:
                    self.rows["时长"].setText(f"{card.duration:.1f} 秒")
                else:
                    self.rows["时长"].setText("—")
                w = int((card.pixels) ** 0.5) if card.pixels else 0
                if card.pixels:
                    # 从 meta 里拿真实宽高
                    self.rows["分辨率"].setText("—")
                else:
                    self.rows["分辨率"].setText("—")
                self.rows["大小"].setText(human_size(card.file_size))
            except RuntimeError:
                pass


class LibraryPage(QWidget):
    """Lightroom 风格素材库。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._cards = {}
        self._card_order = []
        self._selected_path = None
        self._selected_paths = set()
        self._selected_folder_item = None  # 选中的文件夹 tile 对应的树节点
        self._checked_paths = set()  # 勾选的素材（用于混剪）
        self._grid_list_view = None
        self._meta = {}
        self._current_dir = None  # None=根目录；否则是 QTreeWidgetItem

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 6)
        lay.setSpacing(8)

        # ---- 路径导航条（返回 + 当前位置）----
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.back_btn = make_button("← 返回", kind="ghost")
        self.back_btn.clicked.connect(self._go_back)
        nav.addWidget(self.back_btn)
        self.path_label = QLabel("全部素材")
        self.path_label.setObjectName("Muted")
        nav.addWidget(self.path_label)
        nav.addStretch(1)
        lay.addLayout(nav)

        # ---- 工具条 ----
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.add_files_btn = make_button("添加视频", icon_text="＋")
        self.add_folder_btn = make_button("扫描文件夹")
        self.add_cat_btn = make_button("新建分类")
        self.remove_btn = make_button("移除选中", kind="danger")
        self.check_btn = make_button("检查素材", kind="ghost")
        self.clear_checked_btn = make_button("取消已选素材", kind="ghost")
        self.clear_checked_btn.setToolTip("把已勾选的素材全部取消勾选（勾选框恢复未选状态）")
        for b in (self.add_files_btn, self.add_folder_btn,
                  self.add_cat_btn, self.remove_btn, self.check_btn,
                  self.clear_checked_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        # ---- 筛选行 ----
        fbar = QHBoxLayout()
        fbar.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("按名称筛选…")
        self.search.setFixedWidth(180)
        fbar.addWidget(self.search)
        self.sort_box = QComboBox()
        self.sort_box.addItems(["按名称", "按时长", "按分辨率", "按大小"])
        self.sort_box.setFixedWidth(100)
        fbar.addWidget(self.sort_box)
        self.min_dur = QComboBox()
        self.min_dur.addItem("时长不限", 0)
        for sec in (3, 5, 10, 30, 60):
            self.min_dur.addItem(f"≥ {sec}s", sec)
        self.min_dur.setFixedWidth(90)
        fbar.addWidget(self.min_dur)
        # 分析状态筛选：全部 / 已分析 / 未分析
        self.analysis_filter = "all"
        self.filter_buttons = {}
        for key, label in (("all", "全部"), ("analyzed", "已分析"),
                           ("unanalyzed", "未分析")):
            btn = make_button(label, kind="ghost")
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda _c=False, k=key: self._set_analysis_filter(k))
            self.filter_buttons[key] = btn
            fbar.addWidget(btn)
        self.view_mode = QComboBox()
        self.view_mode.addItems(["网格", "列表"])
        self.view_mode.setFixedWidth(80)
        fbar.addWidget(self.view_mode)
        fbar.addStretch(1)
        lay.addLayout(fbar)

        # ---- 两栏：中网格 + 右预览 ----
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)

        # 中：缩略图网格
        center = QFrame()
        center.setObjectName("Panel")
        cl = QVBoxLayout(center)
        cl.setContentsMargins(4, 4, 4, 4)
        cl.setSpacing(0)
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setSpacing(6)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setUniformItemSizes(True)
        self.grid.setFrameShape(QFrame.NoFrame)
        self.grid.setAcceptDrops(True)
        self.grid.setDragDropMode(QAbstractItemView.DropOnly)
        self.grid.setMouseTracking(True)
        # 框选/Shift选：同步高亮
        self.grid.itemSelectionChanged.connect(self._on_grid_selection_changed)
        cl.addWidget(self.grid, 1)
        split.addWidget(center)

        # 右：只留预览（元数据由 shell.inspector 显示）
        right = QFrame()
        right.setObjectName("Panel")
        right.setMinimumWidth(280)
        right.setMaximumWidth(420)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.setSpacing(6)
        rh = QLabel("预览")
        rh.setObjectName("SectionTitle")
        rl.addWidget(rh)
        # 醒目的确认混剪素材按钮
        self.confirm_mix_btn = make_button("✓ 确认混剪素材", kind="primary")
        self.confirm_mix_btn.setMinimumHeight(40)
        self.confirm_mix_btn.setStyleSheet(
            "QPushButton { background:#F4F5F7; color:#101114; font-weight:700;"
            "border:none; border-radius:10px; padding:8px; font-size:13px; }"
            "QPushButton:hover { background:#E9EAF0; }"
            "QPushButton:pressed { background:#A7AAB4; }")
        self.confirm_mix_btn.clicked.connect(lambda: _call(self.win, "confirm_materials"))
        rl.addWidget(self.confirm_mix_btn)
        self.preview_slot = QVBoxLayout()
        self.preview_slot.setContentsMargins(0, 0, 0, 0)
        self.preview_slot.setSpacing(4)
        rl.addLayout(self.preview_slot, 1)
        split.addWidget(right)

        split.setSizes([900, 320])
        lay.addWidget(split, 1)

        # ---- 底部状态栏 ----
        status = QHBoxLayout()
        status.setSpacing(12)
        self.count_label = QLabel("0 个素材")
        self.count_label.setObjectName("Faint")
        status.addWidget(self.count_label)
        self.sel_label = QLabel("已选 0")
        self.sel_label.setObjectName("Faint")
        status.addWidget(self.sel_label)
        self.size_label = QLabel("")
        self.size_label.setObjectName("Faint")
        status.addWidget(self.size_label)
        status.addStretch(1)
        lay.addLayout(status)

        # ---- 信号 ----
        self.add_files_btn.clicked.connect(lambda: _call(win, "add_files"))
        self.add_folder_btn.clicked.connect(lambda: _call(win, "add_folder"))
        self.add_cat_btn.clicked.connect(lambda: _call(win, "create_virtual_folder"))
        self.remove_btn.clicked.connect(lambda: _call(win, "remove_selected"))
        self.check_btn.clicked.connect(lambda: _call(win, "check_materials"))
        self.clear_checked_btn.clicked.connect(self.clear_checked)
        self.search.textChanged.connect(lambda *_: self._apply_filter())
        self.sort_box.currentIndexChanged.connect(lambda *_: self._apply_filter())
        self.min_dur.currentIndexChanged.connect(lambda *_: self._apply_filter())
        self.view_mode.currentIndexChanged.connect(self._switch_view)
        self.grid.itemClicked.connect(self._on_item_clicked)
        self.grid.itemActivated.connect(self._on_item_activated)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._show_context_menu)
        if getattr(win, "tree", None) is not None:
            win.tree.itemClicked.connect(lambda item, _c: self._on_tree_clicked(item))

        self.refresh_cards()

    def refresh_cards(self):
        paths = list(self.win.get_all_video_paths()) \
            if hasattr(self.win, "get_all_video_paths") else []
        for gone in set(self._cards) - set(paths):
            self._cards.pop(gone, None)
        for path in paths:
            if path not in self._cards:
                card = MaterialCard(path)
                card.clicked.connect(self._on_card_clicked)
                card.activated.connect(self._on_card_activated)
                self._cards[path] = card
        self._card_order = paths
        self._apply_filter()
        self._probe_all()
        self.update_count(len(paths))

    def _live_card(self, path):
        card = self._cards.get(path)
        if card is None:
            return None
        try:
            _ = card.name.text()
        except RuntimeError:
            self._cards.pop(path, None)
            return None
        return card

    def _discard_card(self, card):
        for path, item in list(self._cards.items()):
            if item is card:
                self._cards.pop(path, None)
                return

    def _safe_cards(self):
        return [c for p in list(self._cards) if (c := self._live_card(p)) is not None]

    def update_count(self, total):
        self.count_label.setText(f"{total} 个素材")

    def _apply_filter(self):
        text = self.search.text().strip().lower()
        min_sec = self.min_dur.currentData() or 0
        visible = []
        for path in self._card_order:
            card = self._cards.get(path)
            if card is None:
                continue
            if text and text not in Path(path).name.lower():
                continue
            if min_sec and card.duration is not None and card.duration < min_sec:
                continue
            visible.append(path)
        self._rebuild_grid(visible)

    def _rebuild_grid(self, paths):
        sort_mode = self.sort_box.currentIndex()
        rows = []
        for path in paths:
            card = self._cards.get(path)
            if card is None:
                continue
            if sort_mode == 1:
                key = (-(card.duration or 0.0),)
            elif sort_mode == 2:
                key = (-(card.pixels or 0),)
            elif sort_mode == 3:
                key = (-(card.file_size or 0),)
            else:
                key = (Path(path).name.lower(),)
            rows.append((key, path))
        rows.sort(key=lambda kv: kv[0])
        ordered = [path for _key, path in rows]

        current = [self.grid.item(i).data(Qt.UserRole)
                   for i in range(self.grid.count())
                   if self.grid.item(i) is not None]
        list_view = self.view_mode.currentText() == "列表"
        # 目录切换时强制重建，不能靠 ordered==current 判断
        dir_key = id(self._current_dir) if self._current_dir is not None else 0
        if (ordered == current and self._grid_list_view == list_view
                and getattr(self, "_last_dir_key", -1) == dir_key):
            return
        self._last_dir_key = dir_key

        self.grid.clear()
        self._cards.clear()
        self._grid_list_view = list_view

        cur = self._current_dir
        tree = getattr(self.win, "tree", None)

        # 收集当前目录下的：子文件夹 + 直接素材路径
        sub_folders = []
        material_paths = []
        if tree is not None:
            parent_item = cur if cur is not None else tree.invisibleRootItem()
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                p = child.data(1, Qt.UserRole)
                if p:  # 有路径=素材
                    material_paths.append(p)
                else:  # 无路径=文件夹
                    sub_folders.append(child)

        # 1) 子文件夹 tile
        for child in sub_folders:
            tile = FolderTile(child)
            tile.clicked.connect(self._on_folder_tile_clicked)
            tile.double_clicked.connect(self._enter_folder)
            tile.dropped.connect(self._on_folder_dropped)
            tile.checked_changed.connect(self._on_folder_checked)
            cnt = self._count_materials(child)
            tile.count_label.setText(f"{cnt} 个素材")
            item = QListWidgetItem()
            item.setData(Qt.UserRole, f"__folder_{id(child)}")
            item.setSizeHint(QSize(170, 140))
            self.grid.addItem(item)
            self.grid.setItemWidget(item, tile)

        # 2) 当前目录下的素材卡片（应用搜索/排序/时长筛选）
        text = self.search.text().strip().lower()
        min_sec = self.min_dur.currentData() or 0
        sort_mode = self.sort_box.currentIndex()

        filtered = []
        for path in material_paths:
            if text and text not in Path(path).name.lower():
                continue
            filtered.append(path)

        # 排序
        def sort_key(path):
            card = self._cards.get(path)
            dur = getattr(card, "duration", None) or 0
            pixels = getattr(card, "pixels", 0) or 0
            size = getattr(card, "file_size", 0) or 0
            if sort_mode == 1:
                return (-dur,)
            elif sort_mode == 2:
                return (-pixels,)
            elif sort_mode == 3:
                return (-size,)
            return (Path(path).name.lower(),)

        filtered.sort(key=sort_key)

        for path in filtered:
            card = MaterialCard(path)
            card.clicked.connect(self._on_card_clicked)
            card.activated.connect(self._on_card_activated)
            card.checked_changed.connect(self._on_card_checked)
            card.apply_meta(self._meta.get(path, {}))
            card.set_checked_state(path in self._checked_paths)
            self._cards[path] = card
            item = QListWidgetItem()
            item.setData(Qt.UserRole, path)
            item.setSizeHint(QSize(170, 140))
            self.grid.addItem(item)
            self.grid.setItemWidget(item, card)

        # 更新路径标签和返回按钮
        if cur is None:
            self.path_label.setText("全部素材")
            self.back_btn.setEnabled(False)
        else:
            names = []
            node = cur
            while node is not None:
                names.append(node.text(0))
                node = node.parent()
                if node is None or node is tree.invisibleRootItem():
                    break
            self.path_label.setText(" / ".join(reversed(names)))
            self.back_btn.setEnabled(True)

        # 「已选 N」始终按勾选集显示，切目录也不会失准
        self._sync_selection_labels()

    def _switch_view(self):
        if self.view_mode.currentText() == "列表":
            self.grid.setViewMode(QListWidget.ListMode)
            self.grid.setFlow(QListWidget.TopToBottom)
        else:
            self.grid.setViewMode(QListWidget.IconMode)
            self.grid.setFlow(QListWidget.LeftToRight)
        self._apply_filter()

    def _probe_all(self):
        w = self.window()
        worker = getattr(w, "probe_worker", None)
        if worker is None:
            return
        pending = [p for p in self._cards
                   if (self._live_card(p) is not None
                       and self._cards[p].status.text() in ("", "—", "读取中…"))]
        if pending:
            worker.request(pending)

    def set_probe_result(self, path, details, error=None):
        meta = self._meta.setdefault(path, {})
        if not error and details:
            fmt = details.get("format") or {}
            video = details.get("video") or {}
            meta["probe_done"] = True
            try:
                meta["duration"] = float(fmt.get("duration") or 0) or None
            except (TypeError, ValueError):
                meta["duration"] = None
            meta["width"] = video.get("width")
            meta["height"] = video.get("height")
            try:
                meta["file_size"] = int(fmt.get("size") or 0)
            except (TypeError, ValueError):
                pass
            meta["pixels"] = int(meta.get("width") or 0) * int(meta.get("height") or 0)
        card = self._live_card(path)
        if card is not None:
            try:
                if error:
                    card.set_probe_error(str(error))
                else:
                    card.set_probe(details)
            except RuntimeError:
                self._discard_card(card)
        self._apply_filter()

    def set_thumbnail(self, path, image_path):
        self._meta.setdefault(path, {})["thumb_path"] = image_path
        card = self._live_card(path)
        if card is None:
            return
        try:
            card.set_thumbnail(image_path)
        except RuntimeError:
            self._discard_card(card)

    def set_analysis_status(self, path, status, detail=None):
        meta = self._meta.setdefault(path, {})
        meta["analysis_status"] = status
        card = self._live_card(path)
        if card is None:
            return
        try:
            card.set_analysis(status)
        except RuntimeError:
            self._discard_card(card)

    def reset_analysis(self, paths):
        for path in paths:
            card = self._live_card(path)
            if card is not None:
                try:
                    card.set_analysis("analyzing")
                except RuntimeError:
                    self._discard_card(card)

    def clear_analysis(self):
        for card in self._safe_cards():
            try:
                card.set_analysis("unanalyzed")
            except RuntimeError:
                self._discard_card(card)

    def selected_paths(self):
        """返回 grid 里所有选中的素材路径（支持 Ctrl/Shift 多选）。"""
        paths = []
        for item in self.grid.selectedItems():
            data = item.data(Qt.UserRole)
            if isinstance(data, str) and not data.startswith("__folder_"):
                paths.append(data)
        if not paths and self._selected_path:
            paths.append(self._selected_path)
        return paths

    def removal_targets(self):
        """「移除选中」要处理的目标：文件夹 tile 优先，其次点选/勾选的素材。

        返回 (folder_item, paths)。folder_item 非 None 表示整棵文件夹子树要移除；
        否则 paths 为视频路径列表。选择为空时回退到勾选集，避免"勾了却提示没选"。
        """
        folder = getattr(self, "_selected_folder_item", None)
        if folder is not None:
            try:
                _ = folder.text(0)
            except RuntimeError:
                # 树节点已被销毁，按没有选中处理
                self._selected_folder_item = None
                folder = None
        if folder is not None:
            return folder, []

        paths = set(self._selected_paths or set())
        if self._selected_path:
            paths.add(self._selected_path)
        paths = {p for p in paths
                 if isinstance(p, str) and not p.startswith("__folder_")}
        if not paths:
            paths = {p for p in (self._checked_paths or set())
                     if isinstance(p, str) and not p.startswith("__folder_")}
        return None, [p for p in paths]

    def purge_paths(self, paths):
        """素材被移除后同步清掉选中/勾选状态，避免出现"素材 0 / 已选 N"。"""
        gone = set(paths or [])
        if not gone:
            return
        self._checked_paths -= gone
        self._selected_paths -= gone
        if self._selected_path in gone:
            self._selected_path = None
        self._selected_folder_item = None
        self._sync_selection_labels()
        self.size_label.setText("")

    def _on_card_clicked(self, path):
        self._select(path, preview=True)

    def _on_folder_tile_clicked(self, folder_item):
        """点文件夹 tile = 选中该文件夹；「移除选中」会把整棵文件夹移除。"""
        self._selected_folder_item = folder_item
        self._selected_path = None
        self._selected_paths = set()
        self._sync_selection_labels()
        self.size_label.setText("")
        # 视觉反馈：被选中的文件夹 tile 加高亮，其余素材取消选中
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            w = self.grid.itemWidget(it)
            if w is None:
                continue
            data = it.data(Qt.UserRole)
            if isinstance(data, str) and data.startswith("__folder_"):
                current = getattr(w, "folder_item", None) is folder_item
                w.setStyleSheet(
                    (f"background:{theme.COLORS['selected']};"
                     f"border:1px solid {theme.COLORS['accent']};border-radius:4px;")
                    if current else "background:transparent;border:none;")
            else:
                it.setSelected(False)

    def _on_card_activated(self, path):
        # 双击：设置源并播放
        self._select(path, preview=False)
        if hasattr(self.win, "player"):
            self.win.player.setSource(QUrl.fromLocalFile(path))
            play = getattr(self.win, "play_btn", None)
            if play is not None:
                play.click()

    def _on_item_clicked(self, item):
        data = item.data(Qt.UserRole)
        if isinstance(data, str) and data.startswith("__folder_"):
            # 点文件夹 tile：记录它，供"移除选中"整棵移除
            widget = self.grid.itemWidget(item)
            self._selected_folder_item = getattr(widget, "folder_item", None)
            self._selected_path = None
            self._selected_paths = set()
            return
        self._selected_folder_item = None
        self._select(data, preview=True)

    def _on_item_activated(self, item):
        data = item.data(Qt.UserRole)
        if isinstance(data, str) and data.startswith("__folder_"):
            return
        self._select(data, preview=True)

    def _on_grid_selection_changed(self):
        # 框选/Shift选：从 grid 选中的 items 同步高亮
        paths = set()
        for it in self.grid.selectedItems():
            p = it.data(Qt.UserRole)
            if isinstance(p, str) and not p.startswith("__folder_"):
                paths.add(p)
        if paths:
            self._selected_paths = paths
        # 更新高亮（不覆盖空选中）
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            p = it.data(Qt.UserRole)
            if not isinstance(p, str) or p.startswith("__folder_"):
                continue
            w = self.grid.itemWidget(it)
            if w is not None:
                try:
                    w.set_checked(p in self._selected_paths)
                except RuntimeError:
                    pass

    def _sync_selection_labels(self):
        """「已选 N」只能等于勾选集大小（= 真正参与混剪的数量）。

        之前这里用的是“点选高亮”的 _selected_paths，而点勾选框不会改变它，
        于是勾了 4 个却显示已选 2。
        """
        if getattr(self, "sel_label", None) is not None:
            self.sel_label.setText(f"已选 {len(self._checked_paths)}")
        win = getattr(self, "win", None)
        if win is not None:
            refresh = getattr(win, "_refresh_topbar", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    pass

    def _on_card_checked(self, path, on):
        if on:
            self._checked_paths.add(path)
        else:
            self._checked_paths.discard(path)
        self._sync_selection_labels()

    def _on_folder_checked(self, folder_item, on):
        # 勾选文件夹 = 递归勾选/取消里面所有视频
        self._set_folder_recursive(folder_item, on)
        self._refresh_checkboxes()
        self._sync_selection_labels()

    def _set_folder_recursive(self, folder_item, on):
        for i in range(folder_item.childCount()):
            child = folder_item.child(i)
            p = child.data(1, Qt.UserRole)
            if p:
                if on:
                    self._checked_paths.add(p)
                else:
                    self._checked_paths.discard(p)
            else:
                self._set_folder_recursive(child, on)

    def _refresh_checkboxes(self):
        # 刷新当前目录所有 tile 的勾选框状态
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            w = self.grid.itemWidget(it)
            p = it.data(Qt.UserRole)
            if w is None:
                continue
            if isinstance(p, str) and p.startswith("__folder_"):
                # 文件夹 tile：检查里面是否所有视频都勾选了
                child = getattr(w, "folder_item", None)
                if child is not None:
                    all_paths = self._collect_folder_paths(child)
                    if all_paths:
                        checked = all(pth in self._checked_paths for pth in all_paths)
                        w.checkbox.blockSignals(True)
                        w.checkbox.setChecked(checked)
                        w.checkbox.blockSignals(False)
            elif isinstance(p, str):
                w.set_checked_state(p in self._checked_paths)

    def _collect_folder_paths(self, folder_item):
        result = []
        for i in range(folder_item.childCount()):
            child = folder_item.child(i)
            p = child.data(1, Qt.UserRole)
            if p:
                result.append(p)
            else:
                result.extend(self._collect_folder_paths(child))
        return result

    def clear_checked(self):
        """取消所有已勾选的素材：勾选框恢复未选，「已选」归零。"""
        self._checked_paths.clear()
        self._selected_paths = set()
        self._selected_path = None
        self._selected_folder_item = None
        self._refresh_checkboxes()
        # 文件夹 tile 的勾选框单独复位（_refresh_checkboxes 只在其内部全勾选时才会勾）
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            w = self.grid.itemWidget(it)
            if w is None:
                continue
            p = it.data(Qt.UserRole)
            if isinstance(p, str) and p.startswith("__folder_"):
                box = getattr(w, "checkbox", None)
                if box is not None:
                    box.blockSignals(True)
                    box.setChecked(False)
                    box.blockSignals(False)
        self.size_label.setText("")
        self._sync_selection_labels()
        # 已经锁定过的素材在新勾选前必须重新确认，否则会出现“已选 0 但能直接混剪”
        win = getattr(self, "win", None)
        if win is not None and getattr(win, "material_confirmed", False):
            invalidate = getattr(win, "invalidate_materials", None)
            if callable(invalidate):
                invalidate()

    def _check_selected(self):
        # 把当前高亮选中的素材都勾选上
        for p in self._selected_paths:
            self._checked_paths.add(p)
        # 刷新勾选状态显示
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            w = self.grid.itemWidget(it)
            p = it.data(Qt.UserRole)
            if isinstance(p, str) and not p.startswith("__folder_") and w is not None:
                w.set_checked_state(p in self._checked_paths)
        self._sync_selection_labels()

    def _select(self, path, preview=False):
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QApplication
        # 选中视频即放弃之前选中的文件夹 tile
        self._selected_folder_item = None
        modifiers = QApplication.keyboardModifiers()
        ctrl = bool(modifiers & _Qt.ControlModifier)
        shift = bool(modifiers & _Qt.ShiftModifier)
        if ctrl:
            # Ctrl+点选：追加/取消
            if path in self._selected_paths:
                self._selected_paths.discard(path)
            else:
                self._selected_paths.add(path)
        elif shift:
            # Shift+点选：追加
            self._selected_paths.add(path)
        else:
            # 普通点选：替换
            self._selected_paths = {path}
        self._selected_path = path
        # 同步 grid 选中状态，避免和 itemSelectionChanged 打架
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            p = it.data(Qt.UserRole)
            if not isinstance(p, str) or p.startswith("__folder_"):
                it.setSelected(False)
                continue
            it.setSelected(p in self._selected_paths)
            w = self.grid.itemWidget(it)
            if w is not None:
                try:
                    w.set_checked(p in self._selected_paths)
                except RuntimeError:
                    pass
        # 点素材不自动播放，避免卡顿；用户手动点播放按钮
        if hasattr(self.win, "update_detail_panel"):
            self.win.update_detail_panel(path)
        # 右侧属性面板显示元数据
        shell = getattr(self.win, "shell", None)
        if shell is not None:
            card = self._live_card(path)
            # 优先从卡片取，取不到从 _meta 缓存取
            dur = None
            size = None
            if card is not None:
                dur = card.duration
                size = card.file_size
            if not dur and path in self._meta:
                dur = self._meta[path].get("duration")
            if not size and path in self._meta:
                size = self._meta[path].get("size")
            try:
                if not size:
                    import os
                    size = os.path.getsize(path)
            except OSError:
                size = 0
            dur_str = f"{dur:.1f} 秒" if dur else "—"
            size_str = human_size(size) if size else "—"
            shell.inspector.set_info(
                f"<b style='color:{theme.COLORS['text']}'>{Path(path).name}</b>"
                f"<br><br>"
                f"<span style='color:{theme.COLORS['muted']}'>"
                f"时长：{dur_str}<br>"
                f"大小：{size_str}<br>"
                f"<br>"
                f"<span style='color:{theme.COLORS['faint']};font-size:11px;'>"
                f"{path}</span></span>")
        # 底部状态栏：已选数只按勾选集算，避免点选数与勾选数打架
        self._sync_selection_labels()
        self.size_label.setText(f"· {human_size(card.file_size) if card else 0}")

    def _on_tree_clicked(self, item):
        path = item.data(1, Qt.UserRole)
        if not path:
            return
        for idx in range(self.grid.count()):
            it = self.grid.item(idx)
            if it.data(Qt.UserRole) == path:
                self.grid.setCurrentItem(it)
                break
        self._select(path, preview=False)

    def _find_tree_node(self, path):
        tree = getattr(self.win, "tree", None)
        if tree is None:
            return None
        it = QTreeWidgetItemIterator(tree)
        while it.value():
            if it.value().data(1, Qt.UserRole) == path:
                return it.value()
            it += 1
        return None

    def _count_materials(self, folder_item):
        cnt = 0
        for i in range(folder_item.childCount()):
            child = folder_item.child(i)
            if child.data(1, Qt.UserRole):
                cnt += 1
            else:
                cnt += self._count_materials(child)
        return cnt

    def _enter_folder(self, folder_item):
        self._current_dir = folder_item
        self._rebuild_grid([])

    def _go_back(self):
        if self._current_dir is None:
            return
        parent = self._current_dir.parent()
        # 返回时取消勾选当前目录下所有素材
        for i in range(self._current_dir.childCount()):
            child = self._current_dir.child(i)
            p = child.data(1, Qt.UserRole)
            if p:
                self._checked_paths.discard(p)
            else:
                for sub in self._collect_folder_paths(child):
                    self._checked_paths.discard(sub)
        # parent() 对顶层节点返回 invisibleRootItem，它再往上 parent() 是 None
        if parent is None or parent.parent() is None:
            self._current_dir = None
        else:
            self._current_dir = parent
        self.update_count(len(self._checked_paths))
        self._rebuild_grid([])

    def _on_folder_dropped(self, folder_item, paths):
        tree = getattr(self.win, "tree", None)
        if tree is None:
            return
        tree.record_state()
        for path in paths:
            node = self._find_tree_node(path)
            if node is not None:
                (node.parent() or tree.invisibleRootItem()).removeChild(node)
                folder_item.addChild(node)
        folder_item.setExpanded(True)
        self.win.log(f"已把 {len(paths)} 个素材移到「{folder_item.text(0)}」")
        # 移动后如果当前目录指针失效，回到根目录
        if self._current_dir is not None:
            try:
                _ = self._current_dir.text(0)
            except RuntimeError:
                self._current_dir = None
        self.refresh_cards()
        # 可见的是 demo 素材库（旧树快照）；不同步的话素材还显示在原文件夹。
        sync = getattr(self.win, "reload_library", None)
        if callable(sync):
            sync(preserve_selection=True)

    def _show_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        item = self.grid.itemAt(pos)
        if item is None:
            # 右键空白处：新建文件夹
            new_act = menu.addAction("新建文件夹")
            new_act.triggered.connect(lambda: _call(self.win, "create_virtual_folder"))
            menu.exec(self.grid.viewport().mapToGlobal(pos))
            return
        data = item.data(Qt.UserRole)
        is_folder = isinstance(data, str) and data.startswith("__folder_")
        tree = getattr(self.win, "tree", None)

        if is_folder:
            # 右键文件夹 tile：移动此文件夹到...
            folder_tile = self.grid.itemWidget(item)
            folder_item = getattr(folder_tile, "folder_item", None)
            if folder_item is not None:
                move_menu = menu.addMenu("移动此文件夹到...")
                if tree is not None:
                    it = QTreeWidgetItemIterator(tree)
                    while it.value():
                        node = it.value()
                        path_data = node.data(1, Qt.UserRole)
                        if path_data:
                            it += 1
                            continue
                        # 不能移到自己或自己的子文件夹里
                        if node is folder_item:
                            it += 1
                            continue
                        act = move_menu.addAction("📁 " + node.text(0))
                        act.triggered.connect(
                            lambda _c, n=node, src=folder_item: self._move_folder_to(src, n))
                        it += 1
                menu.addSeparator()
                rename_act = menu.addAction("重命名此文件夹")
                rename_act.triggered.connect(lambda: self._rename_folder_item(folder_item))
                del_act = menu.addAction("删除此文件夹")
                del_act.triggered.connect(lambda: self._delete_folder(folder_item))
                menu.exec(self.grid.viewport().mapToGlobal(pos))
                return

        # 右键素材卡片：移动选中素材到...
        move_menu = menu.addMenu("移动到...")
        if tree is not None:
            it = QTreeWidgetItemIterator(tree)
            while it.value():
                node = it.value()
                path_data = node.data(1, Qt.UserRole)
                if path_data:
                    it += 1
                    continue
                act = move_menu.addAction("📁 " + node.text(0))
                act.triggered.connect(
                    lambda _checked, n=node: self._move_selected_to(n))
                it += 1
        # 勾选当前选中的素材加入混剪
        check_act = menu.addAction("✓ 勾选加入混剪")
        check_act.triggered.connect(self._check_selected)
        menu.addSeparator()
        delete_act = menu.addAction("从素材库移除")
        delete_act.triggered.connect(lambda: _call(self.win, "remove_selected"))
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def _rename_folder_item(self, folder_item):
        from PySide6.QtWidgets import QInputDialog
        tree = getattr(self.win, "tree", None)
        if tree is None or folder_item is None:
            return
        old_name = folder_item.text(0)
        text, ok = QInputDialog.getText(self, "重命名分类", "分类名称：", text=old_name)
        text = (text or "").strip()
        if not ok or not text or text == old_name:
            return
        tree.record_state()
        folder_item.setText(0, text)
        self.win.log(f"已将文件夹「{old_name}」重命名为「{text}」")
        self._apply_filter()

    def _move_folder_to(self, src_folder, dest_folder):
        tree = getattr(self.win, "tree", None)
        if tree is None or src_folder is dest_folder:
            return
        # 不能移到自己的子文件夹里
        child = dest_folder
        while child is not None:
            if child is src_folder:
                self.win.log("不能把文件夹移到它自己的子文件夹里")
                return
            child = child.parent()
        tree.record_state()
        (src_folder.parent() or tree.invisibleRootItem()).removeChild(src_folder)
        dest_folder.addChild(src_folder)
        dest_folder.setExpanded(True)
        self.win.log(f"已把文件夹移到「{dest_folder.text(0)}」")
        if self._current_dir is not None:
            try:
                _ = self._current_dir.text(0)
            except RuntimeError:
                self._current_dir = None
        self.refresh_cards()

    def _delete_folder(self, folder_item):
        tree = getattr(self.win, "tree", None)
        if tree is None:
            return
        tree.record_state()
        (folder_item.parent() or tree.invisibleRootItem()).removeChild(folder_item)
        self.win.log(f"已删除文件夹「{folder_item.text(0)}」")
        if self._current_dir is folder_item:
            self._current_dir = None
        self.refresh_cards()

    def _move_selected_to(self, folder_item):
        paths = [self._selected_path] if self._selected_path else []
        if not paths:
            return
        tree = getattr(self.win, "tree", None)
        if tree is None:
            return
        tree.record_state()
        for path in paths:
            node = self._find_tree_node(path)
            if node is not None:
                (node.parent() or tree.invisibleRootItem()).removeChild(node)
                folder_item.addChild(node)
        self.win.log(f"已把 {len(paths)} 个素材移到「{folder_item.text(0)}」")
        if self._current_dir is not None:
            try:
                _ = self._current_dir.text(0)
            except RuntimeError:
                self._current_dir = None
        self.refresh_cards()
        # 同上：同步可见的 demo 素材库，避免移动后仍显示在原文件夹。
        sync = getattr(self.win, "reload_library", None)
        if callable(sync):
            sync(preserve_selection=True)

    def on_shown(self):
        self.refresh_cards()

    # =================================================
    # 勾选状态：转接到宿主共享的 MaterialSelection
    # =================================================
    # 解耦第2步：勾选状态原本是 LibraryPage 的私有字段 _checked_paths，
    # 却被 library_ops / shell_nav / project_state / backend_bridge
    # 四个模块从外部读写，导致新界面无法脱离旧页实例。
    # 现在把状态放到宿主持有的 MaterialSelection 上，本类通过
    # property 转发——类内所有 self._checked_paths.xxx() 的写法语义不变。
    #
    # 注意：这里按需取宿主实例而不缓存，因为 LibraryPage 构造期间
    # 就会有外部代码通过窗口读勾选状态。

    def _resolve_selection(self):
        """取得宿主（MainWindow）持有的 MaterialSelection；没有就自建。"""
        sel = getattr(self.win, "selection", None)
        if sel is None:
            from mixcut.appkit.selection import MaterialSelection
            sel = MaterialSelection()
            try:
                self.win.selection = sel
            except Exception:
                pass
        return sel

    @property
    def _checked_paths(self):
        return self._resolve_selection()

    @_checked_paths.setter
    def _checked_paths(self, value):
        # 外部赋值（如 `page._checked_paths = set(paths)`）走这里，
        # 变成对共享对象的整体替换，保证两边看到同一份状态。
        self._resolve_selection().set_checked(value)


def _call(win, method, *args):
    fn = getattr(win, method, None)
    if callable(fn):
        fn(*args)
