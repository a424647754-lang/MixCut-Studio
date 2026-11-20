# -*- coding: utf-8 -*-
"""素材库：Finder 式缩略图网格，悬停/选中反馈，筛选与搜索。"""

from PySide6.QtCore import (
    QEasingCurve, Property, QPoint, QPropertyAnimation, QRectF, QMimeData, Qt,
    Signal, QTimer,
)
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from . import mock, theme as T
from .ui_kit import Card, Chip, Dot, RollingNumber, SoftButton, Thumb, draw_icon, paint_card


class DropFolderButton(QPushButton):
    """左侧文件夹栏的一行：既能点击切换，也能接收素材拖入。"""

    clipsDropped = Signal(str, list)   # (文件夹 id, [素材路径])

    def __init__(self, folder_id, text, parent=None):
        super().__init__(text, parent)
        self.folder_id = str(folder_id)
        self._drag_hot = False
        self.setAcceptDrops(True)

    @staticmethod
    def _paths(event):
        data = event.mimeData().data(ClipCard.MIME_TYPE)
        if not data:
            return []
        try:
            return [p for p in bytes(data).decode("utf-8").split("\n") if p]
        except Exception:
            return []

    def dragEnterEvent(self, e):
        if self.folder_id in ("all", "root") or not self._paths(e):
            e.ignore()
            return
        self._drag_hot = True
        self.setStyleSheet(self.styleSheet() + ";border:1px solid %s;" % T.ACCENT)
        e.setDropAction(Qt.DropAction.MoveAction)
        e.accept()

    def dragMoveEvent(self, e):
        if self.folder_id not in ("all", "root") and self._paths(e):
            e.setDropAction(Qt.DropAction.MoveAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        if self._drag_hot:
            self._drag_hot = False
            self.setStyleSheet(self.styleSheet().replace(
                ";border:1px solid %s;" % T.ACCENT, ""))
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        paths = self._paths(e)
        self._drag_hot = False
        self.setStyleSheet(self.styleSheet().replace(
            ";border:1px solid %s;" % T.ACCENT, ""))
        if not paths or self.folder_id in ("all", "root"):
            e.ignore()
            return
        win = self.window()
        sel = getattr(win, "_drag_selected_paths", None) or []
        if sel and set(paths) == set(sel):
            paths = list(sel)
        self.clipsDropped.emit(self.folder_id, paths)
        e.setDropAction(Qt.DropAction.MoveAction)
        e.accept()


class FolderCard(QWidget):
    """素材库顶层的文件夹卡片：大文件夹轮廓 + 内含素材缩略图。"""

    clicked = Signal(str)
    menuRequested = Signal(str, object)
    clipsDropped = Signal(str, list)   # (文件夹 id, [素材路径])

    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.count = 0
        self.hover = 0.0
        self._drag_hot = False
        self.setFixedHeight(238)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._ha = QPropertyAnimation(self, b"hoverT", self)
        self._ha.setDuration(150)
        self._ha.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---- 接收素材拖入 ----
    @staticmethod
    def _dropped_paths(event):
        mime = event.mimeData()
        data = mime.data(ClipCard.MIME_TYPE)
        if data:
            try:
                return [p for p in bytes(data).decode("utf-8").split("\n") if p]
            except Exception:
                return []
        return []

    def dragEnterEvent(self, e):
        if self._dropped_paths(e):
            self._drag_hot = True
            e.setDropAction(Qt.DropAction.MoveAction)
            e.accept()
            self.update()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._dropped_paths(e):
            e.setDropAction(Qt.DropAction.MoveAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._drag_hot = False
        self.update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        paths = self._dropped_paths(e)
        self._drag_hot = False
        self.update()
        if not paths:
            e.ignore()
            return
        sel = getattr(self.window(), "_drag_selected_paths", None) or []
        if sel and set(paths) == set(sel):
            paths = list(sel)
        self.clipsDropped.emit(self.folder["id"], paths)
        e.setDropAction(Qt.DropAction.MoveAction)
        e.accept()

    def get_hover(self):
        return self.hover

    def set_hover(self, v):
        self.hover = float(v)
        self.update()

    hoverT = Property(float, get_hover, set_hover)

    def set_count(self, n):
        self.count = int(n)
        self.update()

    def enterEvent(self, e):
        self._ha.stop()
        self._ha.setStartValue(self.hover)
        self._ha.setEndValue(1.0)
        self._ha.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._ha.stop()
        self._ha.setStartValue(self.hover)
        self._ha.setEndValue(0.0)
        self._ha.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.menuRequested.emit(self.folder["id"], e.globalPosition().toPoint())
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.folder["id"])
            e.accept()
            return
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        self.menuRequested.emit(self.folder["id"], e.globalPos())
        e.accept()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # 拖着素材悬停在文件夹上时高亮边框，给用户“松手会放进去”的反馈。
        if self._drag_hot:
            p.setPen(QPen(QColor(T.ACCENT), 2))
            p.setBrush(QColor(T.CARD_HI))
        else:
            p.setPen(QPen(QColor("#3A3D47" if self.hover > 0.5 else T.LINE), 1))
            p.setBrush(QColor(T.CARD_HI if self.hover > 0.3 else T.CARD))
        p.drawRoundedRect(r, 14, 14)

        # 文件夹轮廓
        body = QRectF(r.left() + 18, r.top() + 42, r.width() - 36, 118)
        tab = QRectF(body.left(), body.top() - 10, min(96, body.width() * 0.42), 22)
        p.setPen(QPen(QColor("#3F424C"), 1))
        p.setBrush(QColor("#262832"))
        p.drawRoundedRect(tab, 6, 6)
        p.setBrush(QColor("#2B2D38"))
        p.drawRoundedRect(body, 10, 10)

        # 文件夹内的素材缩略图
        base = self.folder.get("folder_path", self.folder["name"])
        clips = [c for c in mock.CLIPS
                 if not c.get("_removed")
                 and (c.get("folder_path") or c.get("folder") or "") == base]
        from .ui_kit import thumb_pixmap
        inner = body.adjusted(10, 14, -10, -10)
        if clips:
            for i, clip in enumerate(clips[:3]):
                tw = min(96, (inner.width() - 16) / 2.2)
                th = tw * 0.62
                x = inner.left() + i * 16
                y = inner.top() + i * 12
                tile = QRectF(x, y, tw, th)
                path = QPainterPath()
                path.addRoundedRect(tile, 6, 6)
                p.save()
                p.setClipPath(path)
                p.drawPixmap(int(tile.left()), int(tile.top()),
                             thumb_pixmap(clip.get("thumb_path") or mock.THUMBS[clip["thumb"]],
                                          int(tw), int(th)))
                p.restore()
                p.setPen(QPen(QColor(255, 255, 255, 34), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(tile, 6, 6)
        else:
            p.setFont(T.font(11))
            p.setPen(QColor(T.TEXT_3))
            p.drawText(inner, Qt.AlignmentFlag.AlignCenter, "空文件夹")

        # 名称与计数
        x = r.left() + 14
        p.setFont(T.font(15, QFont.Weight.DemiBold))
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(x, r.bottom() - 64, r.width() - 28, 24),
                   Qt.AlignmentFlag.AlignVCenter, self.folder["name"])
        p.setFont(T.font(11))
        p.setPen(QColor(T.TEXT_3))
        p.drawText(QRectF(x, r.bottom() - 36, r.width() - 28, 18),
                   Qt.AlignmentFlag.AlignVCenter, "文件夹 · %d 条素材" % self.count)


class ClipCard(QWidget):
    clicked = Signal(str)
    menuRequested = Signal(str, object)

    # 拖动素材时携带的 MIME 类型：内容是换行分隔的素材真实路径。
    MIME_TYPE = "application/x-mixcut-clip-paths"

    def __init__(self, clip, parent=None):
        super().__init__(parent)
        self.clip = clip
        self.selected = False
        self.hover = 0.0
        # 拖动起点与「取当前拖拽路径」的回调，由 LibraryPage 注入。
        self._drag_start = None
        self.drag_paths_provider = None
        self.setFixedHeight(238)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._ha = QPropertyAnimation(self, b"hoverT", self)
        self._ha.setDuration(150)
        self._ha.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_hover(self):
        return self.hover

    def set_hover(self, v):
        self.hover = float(v)
        self.update()

    hoverT = Property(float, get_hover, set_hover)

    def set_selected(self, on: bool):
        self.selected = on
        self.update()

    def set_status(self, text: str, color: str):
        self.status = (text, color)
        self.update()

    def enterEvent(self, e):
        self._ha.stop()
        self._ha.setStartValue(self.hover)
        self._ha.setEndValue(1.0)
        self._ha.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._ha.stop()
        self._ha.setStartValue(self.hover)
        self._ha.setEndValue(0.0)
        self._ha.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.menuRequested.emit(self.clip["id"], e.globalPosition().toPoint())
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.position().toPoint()
            self.clicked.emit(self.clip["id"])
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e):
        """按住已选素材拖走：多选时一次带走整组。

        以前素材库根本没有实现拖动（只有 mousePressEvent），所以完全拖不动。
        """
        if (self._drag_start is None
                or not (e.buttons() & Qt.MouseButton.LeftButton)):
            super().mouseMoveEvent(e)
            return
        moved = (e.position().toPoint() - self._drag_start).manhattanLength()
        if moved < QApplication.startDragDistance():
            super().mouseMoveEvent(e)
            return
        paths = None
        provider = self.drag_paths_provider
        if callable(provider):
            try:
                paths = provider(self.clip["id"])
            except Exception:
                paths = None
        if not paths:
            path = self.clip.get("path")
            paths = [path] if path else []
        self._drag_start = None
        paths = [p for p in paths if p]
        if not paths:
            return
        mime = QMimeData()
        mime.setData(self.MIME_TYPE,
                     "\n".join(paths).encode("utf-8"))
        mime.setText("\n".join(paths))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(e.position().toPoint())
        drag.exec(Qt.DropAction.MoveAction)

    def contextMenuEvent(self, e):
        self.menuRequested.emit(self.clip["id"], e.globalPos())
        e.accept()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = QColor(T.CARD_HI if self.hover > 0.3 else T.CARD)
        border = QColor("#4B3F7A") if self.selected else QColor("#3A3D47" if self.hover > 0.5 else T.LINE)
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, 14, 14)

        thumb_rect = QRectF(r.left() + 8, r.top() + 8, r.width() - 16, 150)
        path = QPainterPath()
        path.addRoundedRect(thumb_rect, 9, 9)
        p.setClipPath(path)
        from .ui_kit import thumb_pixmap
        p.drawPixmap(int(thumb_rect.left()), int(thumb_rect.top()),
                     thumb_pixmap(self.clip.get("thumb_path") or mock.THUMBS[self.clip["thumb"]],
                                  int(thumb_rect.width()), int(thumb_rect.height())))
        # 底部轻渐暗
        p.fillRect(thumb_rect, QColor(0, 0, 0, 0))
        p.setClipping(False)

        # 时长
        p.setFont(T.font(10, QFont.Weight.DemiBold, mono=True))
        dur = self.clip["dur"]
        fm = p.fontMetrics()
        dw = fm.horizontalAdvance(dur)
        badge = QRectF(thumb_rect.right() - dw - 18, thumb_rect.bottom() - 22, dw + 12, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(badge, 5, 5)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, dur)

        # 选中圆点
        circ = QRectF(thumb_rect.left() + 8, thumb_rect.top() + 8, 20, 20)
        p.setBrush(QColor(T.ACCENT_BG) if self.selected else QColor(20, 20, 24, 150))
        p.setPen(QPen(QColor("#6B5AA8") if self.selected else QColor(255, 255, 255, 60), 1))
        p.drawEllipse(circ)
        if self.selected:
            draw_icon(p, "check", circ.adjusted(4, 4, -4, -4), QColor("#FFFFFF"), 1.8)

        # 文本
        x = r.left() + 12
        p.setFont(T.font(13, QFont.Weight.DemiBold))
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(x, thumb_rect.bottom() + 10, r.width() - 24, 18),
                   Qt.AlignmentFlag.AlignVCenter, self.clip["name"])
        p.setFont(T.font(11))
        p.setPen(QColor(T.TEXT_3))
        p.drawText(QRectF(x, thumb_rect.bottom() + 30, r.width() - 24, 16),
                   Qt.AlignmentFlag.AlignVCenter,
                   "分辨率待分析 · 未读取镜头数")
        # 状态
        st_text, st_color = getattr(self, "status", ("未分析", T.TEXT_3))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(st_color))
        p.drawEllipse(QRectF(x, r.bottom() - 25, 6, 6))
        p.setFont(T.font(11))
        p.setPen(QColor(T.TEXT_2))
        p.drawText(QRectF(x + 11, r.bottom() - 31, r.width() - 40, 18),
                   Qt.AlignmentFlag.AlignVCenter, st_text)
        p.end()


class LibraryPage(QWidget):
    clipClicked = Signal(str)

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.cards = {}
        self.active_folder = "all"
        self._shown_ids = {c["id"] for c in mock.CLIPS}
        self._folder_shown = []
        self._anchor_id = None
        self.folders = [
            {"id": "all", "name": "全部素材", "parent": None, "depth": 0},
        ]
        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAGE_PAD, 18, T.PAGE_PAD, 16)
        root.setSpacing(14)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        h = QLabel("素材库")
        h.setFont(T.font(30, QFont.Weight.DemiBold))
        root_name = next((f["name"] for f in self.folders if f["id"] == "root"), "")
        s = QLabel(("0 条素材 · 尚未导入" if not root_name else
                    "0 条素材 · 本地目录 D:\\素材\\" + root_name))
        s.setFont(T.font(12))
        s.setStyleSheet("color:%s;" % T.TEXT_3)
        self.subtitle = s
        titles.addWidget(h)
        titles.addWidget(s)
        head.addLayout(titles, 1)
        root.addLayout(head)

        # 源码素材库动作条
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.btn_refresh = SoftButton("刷新素材", "ghost", height=30, radius=8)
        self.btn_add = SoftButton("添加视频", "secondary", height=30, radius=8)
        self.btn_scan = SoftButton("扫描文件夹", "secondary", height=30, radius=8)
        self.btn_remove = SoftButton("移除选中", "ghost", height=30, radius=8)
        self.btn_check = SoftButton("检查素材", "ghost", height=30, radius=8)
        self.btn_clear_checked = SoftButton("取消已选素材", "ghost", height=30, radius=8)
        for b in (self.btn_refresh, self.btn_add, self.btn_scan,
                  self.btn_remove, self.btn_check, self.btn_clear_checked):
            actions.addWidget(b)
        actions.addStretch(1)
        root.addLayout(actions)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索素材名称或编号")
        self.search.setFixedWidth(220)
        self.search.setFixedHeight(34)
        bar.addWidget(self.search)
        self.chips = {}
        for i, name in enumerate(("全部", "已分析", "未分析", "已移除")):
            chip = Chip(name)
            chip.setChecked(i == 0)
            chip.clicked.connect(lambda _=False, n=name: self._filter(n))
            self.chips[name] = chip
            bar.addWidget(chip)
        self.sort = QComboBox()
        self.sort.addItem("按名称", "name")
        self.sort.addItem("按时长", "dur")
        self.sort.addItem("按质量", "quality")
        self.sort.setFixedWidth(92)
        self.sort.setFixedHeight(30)
        bar.addWidget(self.sort)
        self.min_dur = QComboBox()
        self.min_dur.addItem("时长不限", 0)
        for sec in (3, 5, 10, 30, 60):
            self.min_dur.addItem("≥ %ds" % sec, sec)
        self.min_dur.setFixedWidth(88)
        self.min_dur.setFixedHeight(30)
        bar.addWidget(self.min_dur)
        self.view_mode = QComboBox()
        self.view_mode.addItem("网格", 4)
        self.view_mode.addItem("列表", 2)
        self.view_mode.setFixedWidth(74)
        self.view_mode.setFixedHeight(30)
        bar.addWidget(self.view_mode)
        bar.addStretch(1)
        self.count_label = QLabel("已选 0 / 0")
        self.count_label.setFont(T.font(11))
        self.count_label.setStyleSheet("color:%s;" % T.TEXT_3)
        bar.addWidget(self.count_label)
        self.btn_analyze = SoftButton("分析选中", "secondary", "spark", height=32)
        self.btn_clear = SoftButton("清除", "ghost", height=32)
        bar.addWidget(self.btn_analyze)
        bar.addWidget(self.btn_clear)
        root.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)

        side = Card(padding=12, spacing=6)
        self.folder_panel = side
        side.setMinimumWidth(180)
        head = QHBoxLayout()
        head.setSpacing(6)
        ft = QLabel("文件夹")
        ft.setFont(T.font(13, QFont.Weight.DemiBold))
        head.addWidget(ft)
        head.addStretch(1)
        self.btn_back = SoftButton("← 返回", "ghost", height=26, radius=7, min_width=64)
        head.addWidget(self.btn_back)
        side.lay.addLayout(head)
        fs = QLabel("按目录分类管理素材")
        fs.setFont(T.font(10))
        fs.setStyleSheet("color:%s;" % T.TEXT_3)
        side.lay.addWidget(fs)
        side.lay.addSpacing(4)
        self.folder_group = QButtonGroup(self)
        self.folder_group.setExclusive(True)
        self.folder_buttons = {}
        self.folder_counts = {}
        self.folder_lay = QVBoxLayout()
        self.folder_lay.setContentsMargins(0, 0, 0, 0)
        self.folder_lay.setSpacing(2)
        for f in self.folders:
            self._add_folder_row(f)
        side.lay.addLayout(self.folder_lay)
        self.btn_new_folder = SoftButton("新建分类", "ghost", height=28)
        self.btn_new_folder.clicked.connect(self._new_category)
        self.btn_rename = SoftButton("重命名", "ghost", height=28)
        self.btn_rename.setEnabled(False)
        self.btn_rename.clicked.connect(self._rename_folder)
        side.lay.addStretch(1)
        self.btn_move = SoftButton("移入当前分类", "secondary", height=30)
        self.btn_move.setToolTip("把已勾选的素材移动到左侧当前选中的分类文件夹")
        self.btn_move.clicked.connect(self._move_selected)
        side.lay.addWidget(self.btn_move)
        fr = QHBoxLayout()
        fr.setSpacing(6)
        fr.addWidget(self.btn_new_folder, 1)
        fr.addWidget(self.btn_rename, 1)
        side.lay.addLayout(fr)
        self.folder_buttons["all"].setChecked(True)
        self._update_folder_counts()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(0, 4, 6, 4)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        self.cols = 4
        self.folder_cards = {}
        fi = 0
        for f in self.folders:
            if f["id"] in ("all", "root"):
                continue
            fc = FolderCard(f)
            fc.clicked.connect(self._select_folder)
            fc.menuRequested.connect(self._folder_menu)
            fc.clipsDropped.connect(self._on_clips_dropped)
            self.folder_cards[f["id"]] = fc
            self.grid.addWidget(fc, fi // self.cols, fi % self.cols)
            fi += 1
        for idx, clip in enumerate(mock.CLIPS):
            card = ClipCard(clip)
            card.clicked.connect(self._toggle)
            card.menuRequested.connect(self._clip_menu)
            card.drag_paths_provider = self._drag_paths_for
            self.cards[clip["id"]] = card
            pos = fi + idx
            self.grid.addWidget(card, pos // self.cols, pos % self.cols)
        for col in range(4):
            self.grid.setColumnStretch(col, 1)
        self.grid.setRowStretch(self.grid.rowCount(), 1)
        self.empty_tip = None
        self._ensure_empty_tip()
        scroll.setWidget(holder)
        self.scroll = scroll

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("FolderSplitter")
        self.splitter.setHandleWidth(8)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(side)
        self.splitter.addWidget(scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([210, 1000])
        handle = self.splitter.handle(1)
        handle.setToolTip("左右拖动调整文件夹栏宽度（最大到「检查素材」按钮下方）")
        handle.setCursor(Qt.CursorShape.SplitHCursor)
        self._cols_timer = QTimer(self)
        self._cols_timer.setSingleShot(True)
        self._cols_timer.timeout.connect(self._fit_columns)
        self.splitter.splitterMoved.connect(lambda *_: self._cols_timer.start(140))
        body.addWidget(self.splitter, 1)
        self._apply_search()
        self._reflow(self._shown_ids)

        self.footer = QLabel("提示：单击选中/取消；Shift 连选、Ctrl 点选可多选。")
        self.footer.setFont(T.font(11))
        self.footer.setStyleSheet("color:%s;" % T.TEXT_3)
        root.addWidget(self.footer)

        self.btn_import = self.btn_add
        self.btn_import.clicked.connect(self._add_files)
        self.btn_refresh.clicked.connect(self._refresh)
        self.btn_back.clicked.connect(self._back)
        self.btn_scan.clicked.connect(self._scan_folder)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_check.clicked.connect(self._check_materials)
        self.btn_clear_checked.clicked.connect(self._clear_checked)
        self.btn_analyze.clicked.connect(self._analyze_selected)
        self.btn_clear.clicked.connect(lambda: win.controller.clear_selection())
        self.search.textChanged.connect(self._reposition)
        self.sort.currentIndexChanged.connect(self._reposition)
        self.min_dur.currentIndexChanged.connect(self._reposition)
        self.view_mode.currentIndexChanged.connect(self._change_view)
        win.controller.stateChanged.connect(self.refresh)

    def apply_backend_snapshot(self, snap, preserve_selection=True):
        """把旧后台素材树同步成新的文件夹卡片 + 单条视频卡片。

        preserve_selection=True 时按“路径”保留刚勾选的素材；只有工程打开这种
        明确要换上下文的场景才传 False，避免刷新/移动文件夹就清空勾选。
        """
        c = self.win.controller
        self._reset_analysis_scope()
        prev_active = getattr(self, "active_folder", "all")
        prev_active_name = next((f["name"] for f in self.folders
                                 if f["id"] == prev_active), "")
        prev_active_path = next((f.get("folder_path", "") for f in self.folders
                                 if f["id"] == prev_active), None)
        prev_sel_paths = {cl.get("path") for cl in mock.CLIPS
                          if cl.get("id") in getattr(c, "selected", set())
                          and cl.get("path")}
        prev_current_path = next((cl.get("path") for cl in mock.CLIPS
                                  if cl.get("id") == getattr(c, "_current", None)
                                  and cl.get("path")), "")
        prev_removed_paths = {cl.get("path") for cl in mock.CLIPS
                              if cl.get("id") in getattr(c, "removed", set())
                              and cl.get("path")}
        # 刷新会重建 clip dict，标签/缩略图/探测到的元数据按路径从旧清单带过来
        prev_meta = {cl.get("path"): cl for cl in mock.CLIPS if cl.get("path")}
        if not hasattr(c, "removed_clips"):
            c.removed_clips = []

        folders = snap.get("folders") or []
        clips = list(snap.get("clips") or [])
        for clip in clips:
            old = prev_meta.get(clip.get("path"))
            if not old:
                continue
            for key in ("tags", "thumb_path", "dur", "source_duration",
                        "width", "height", "codec", "fps", "pix_fmt",
                        "bit_depth", "file_size", "quality"):
                if not clip.get(key) and old.get(key):
                    clip[key] = old[key]
        root_name = snap.get("root_name") or "素材库"

        # 已移除的素材不在旧树里，但“已移除”筛选需要继续展示它们
        known_paths = {cl.get("path") for cl in clips if cl.get("path")}
        for old_clip in list(getattr(c, "removed_clips", []) or []):
            path = old_clip.get("path")
            if path and path not in known_paths:
                item = dict(old_clip)
                item["_removed"] = True
                clips.append(item)
                known_paths.add(path)

        self.folders = [{"id": "all", "name": "全部素材", "parent": None,
                         "depth": 0, "folder_path": None}]
        for f in folders:
            self.folders.append({
                "id": f["id"], "name": f["name"],
                "parent": f.get("parent") or "all",
                "depth": int(f.get("depth") or 1),
                "folder_path": f.get("folder_path", f["name"]),
            })
        mock.CLIPS[:] = clips

        # 清空文件夹行、文件夹卡片、视频卡片
        for btn in list(getattr(self, "folder_buttons", {}).values()):
            try:
                self.folder_group.removeButton(btn)
            except Exception:
                pass
            btn.setParent(None)
            btn.deleteLater()
        self.folder_buttons = {}
        self.folder_counts = {}
        while self.folder_lay.count():
            item = self.folder_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for w in list(getattr(self, "folder_cards", {}).values()):
            w.setParent(None)
            w.deleteLater()
        self.folder_cards = {}
        for w in list(getattr(self, "cards", {}).values()):
            w.setParent(None)
            w.deleteLater()
        self.cards = {}
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.empty_tip = None

        for f in self.folders:
            self._add_folder_row(f)
        fi = 0
        for f in self.folders:
            if f["id"] in ("all", "root"):
                continue
            fc = FolderCard(f)
            fc.clicked.connect(self._select_folder)
            fc.menuRequested.connect(self._folder_menu)
            fc.clipsDropped.connect(self._on_clips_dropped)
            self.folder_cards[f["id"]] = fc
            self.grid.addWidget(fc, fi // self.cols, fi % self.cols)
            fi += 1
        for idx, clip in enumerate(mock.CLIPS):
            card = ClipCard(clip)
            card.clicked.connect(self._toggle)
            card.menuRequested.connect(self._clip_menu)
            card.drag_paths_provider = self._drag_paths_for
            self.cards[clip["id"]] = card
            pos = fi + idx
            self.grid.addWidget(card, pos // self.cols, pos % self.cols)

        c.imported = bool(clips)
        if preserve_selection:
            c.selected = {cl["id"] for cl in mock.CLIPS
                          if cl.get("path") in prev_sel_paths}
            c.removed = {cl["id"] for cl in mock.CLIPS
                         if cl.get("path") in prev_removed_paths
                         or cl.get("_removed")}
            c._current = next((cl["id"] for cl in mock.CLIPS
                               if cl.get("path") == prev_current_path),
                              mock.CLIPS[0]["id"] if mock.CLIPS else "")
        else:
            c.selected.clear()
            c.removed = {cl["id"] for cl in mock.CLIPS if cl.get("_removed")}
            c._current = mock.CLIPS[0]["id"] if mock.CLIPS else ""
        c.analyzed = False
        c.stateChanged.emit()
        if clips:
            self.subtitle.setText("%d 条素材 · %s" % (len([cl for cl in mock.CLIPS
                                                        if not cl.get("_removed")]), root_name))
        else:
            self.subtitle.setText("0 条素材 · 尚未导入")
        target = "all"
        if prev_active != "all":
            target = next((f["id"] for f in self.folders
                           if prev_active_path is not None
                           and f.get("folder_path") == prev_active_path), "")
            if not target:
                target = next((f["id"] for f in self.folders
                               if f["name"] == prev_active_name), "all")
        self.active_folder = target
        if target in self.folder_buttons:
            self.folder_buttons[target].setChecked(True)
        self.btn_rename.setEnabled(target != "all")
        self._update_folder_counts()
        self._apply_search()
        self._ensure_empty_tip()
        self._reflow(self._shown_ids)
        self._note("后台素材已同步：%d 个文件夹 / %d 条视频。" % (
            max(0, len(self.folders) - 1),
            len([cl for cl in mock.CLIPS if not cl.get("_removed")])))

    def _ensure_empty_tip(self):
        if mock.CLIPS or len(self.folders) > 1:
            if self.empty_tip is not None:
                self.empty_tip.setVisible(False)
            return
        if self.empty_tip is None:
            self.empty_tip = QLabel("素材库为空\n点击「添加视频」或「扫描文件夹」导入真实素材")
            self.empty_tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.empty_tip.setFont(T.font(12))
            self.empty_tip.setStyleSheet("color:%s; padding:60px;" % T.TEXT_3)
            self.empty_tip.setWordWrap(True)
            self.grid.addWidget(self.empty_tip, 0, 0, 1, 4)
        self.empty_tip.setVisible(True)

    def _add_folder_row(self, f):
        # 左侧文件夹栏每一行都可接收拖入（DropFolderButton），
        # 「全部素材」只做导航，不接收拖放。
        btn = DropFolderButton(f["id"], f["name"])
        btn.setObjectName("FolderRow")
        btn.setCheckable(True)
        btn.setFixedHeight(30)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("text-align:left; padding-left:%dpx;" % (8 + f["depth"] * 12))
        btn.clicked.connect(lambda _=False, fid=f["id"]: self._select_folder(fid))
        btn.clipsDropped.connect(self._on_clips_dropped)
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, fid=f["id"], b=btn: self._folder_menu(fid, b.mapToGlobal(pos)))
        self.folder_group.addButton(btn)
        self.folder_lay.addWidget(btn)
        self.folder_buttons[f["id"]] = btn
        self.folder_counts[f["id"]] = 0

    # ---- 拖拽支持 ----
    def _drag_paths_for(self, cid):
        """拖动某张卡片时应该带走的路径：已多选就带走整组。"""
        c = self.win.controller
        if cid in getattr(c, "selected", set()) and len(c.selected) > 1:
            paths = [cl.get("path") for cl in mock.CLIPS
                     if cl["id"] in c.selected and cl.get("path")
                     and not cl.get("_removed")]
            if paths:
                self.win._drag_selected_paths = paths
                return paths
        clip = next((cl for cl in mock.CLIPS if cl["id"] == cid), None)
        path = (clip or {}).get("path")
        self.win._drag_selected_paths = [path] if path else []
        return [path] if path else []

    def _on_clips_dropped(self, folder_id, paths):
        """把拖进来的素材移进目标文件夹。"""
        folder = next((f for f in self.folders if f["id"] == folder_id), None)
        if folder is None or not paths:
            self._note("拖放未执行：找不到目标文件夹。")
            return
        ref = folder.get("folder_path") or folder["name"]
        b = self.win.backend
        if not getattr(b, "ready", False):
            self._note("后台素材库尚未加载，无法移动素材。")
            return
        moved = b.move_paths(list(paths), ref)
        self.win.reload_library()
        if moved:
            self._note("已拖入「%s」：%d 条素材。" % (folder["name"], moved))
            self.win.controller.log(
                "素材库 · 拖动 %d 条素材移入「%s」" % (moved, folder["name"]))
        else:
            self._note("拖放未执行：这些素材可能已经在「%s」里。"
                       % folder["name"])

    def _folder_count(self, fid):
        if fid in ("all", "root"):
            if fid == "all":
                return len([c for c in mock.CLIPS
                            if not c.get("_removed")
                            and not (c.get("folder_path") or c.get("folder") or "")
                            and c["id"] not in self.win.controller.removed])
            return len([c for c in mock.CLIPS
                        if not c.get("_removed")
                        and c["id"] not in self.win.controller.removed])
        folder = next((f for f in self.folders if f["id"] == fid), None)
        if not folder:
            return 0
        base = folder.get("folder_path") or folder.get("name") or ""
        return len([c for c in mock.CLIPS
                    if not c.get("_removed")
                    and (c.get("folder_path") or "") == base
                    and c["id"] not in self.win.controller.removed])

    def _update_folder_counts(self):
        for f in self.folders:
            btn = self.folder_buttons.get(f["id"])
            if btn:
                btn.setText("%s  %d" % (f["name"], self._folder_count(f["id"])))
            fc = getattr(self, "folder_cards", {}).get(f["id"])
            if fc:
                fc.set_count(self._folder_count(f["id"]))

    def _select_folder(self, fid):
        self.active_folder = fid
        self._reset_analysis_scope()
        btn = self.folder_buttons.get(fid)
        if btn:
            btn.setChecked(True)
        self.btn_rename.setEnabled(fid != "all")
        self._apply_search()
        self._reflow(self._shown_ids)
        name = next((f["name"] for f in self.folders if f["id"] == fid), "全部素材")
        self._note("当前文件夹：%s" % name)

    def _note(self, text):
        self.footer.setText(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_sync_timer", None) is None:
            self._sync_timer = QTimer(self)
            self._sync_timer.setSingleShot(True)
            self._sync_timer.timeout.connect(self._sync_folder_bounds)
        self._sync_timer.start(30)
        if hasattr(self, "_cols_timer"):
            self._cols_timer.start(120)

    def _sync_folder_bounds(self):
        if not hasattr(self, "splitter") or not self.btn_check.isVisible():
            return
        right = self.btn_check.mapTo(self, QPoint(self.btn_check.width(), 0)).x()
        left = self.folder_panel.mapTo(self, QPoint(0, 0)).x()
        maxw = max(240, right - left)
        if abs(maxw - getattr(self, "_max_folder_w", 0)) < 2:
            return
        self._max_folder_w = maxw
        self.folder_panel.setMaximumWidth(maxw)
        if self.folder_panel.width() > maxw:
            self.splitter.setSizes([maxw, max(320, self.splitter.width() - maxw - 8)])

    def _fit_columns(self):
        if self.view_mode.currentData() != 4 or not hasattr(self, "splitter"):
            return
        avail = self.splitter.width() - self.folder_panel.width() - 14
        cols = 4 if avail >= 1000 else 3 if avail >= 760 else 2
        if cols != self.cols:
            self.cols = cols
            self._reflow(self._shown_ids)

    def _prepare_backend(self):
        b = self.win.backend
        if not b.ready and not b.load_error:
            self._note("首次调用后台，正在加载旧版引擎（约 4 秒）…")
            QApplication.processEvents()

    def _add_files(self):
        b = self.win.backend
        if b.load_error and not b.ready:
            self.win.controller.log("后台桥不可用，添加视频未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频素材", "",
            "视频 (*.mp4 *.mov *.mkv *.m4v *.avi *.webm)")
        if not paths:
            self._note("未选择视频，素材库未变化。")
            return
        self._prepare_backend()
        added = b.add_files_from_paths(paths)
        if added:
            self.win.reload_library()
            self._note("已导入 %d 条真实视频，素材库已刷新。" % added)
        else:
            self._note("没有新增视频（可能已存在或格式不支持）。")

    def _scan_folder(self):
        b = self.win.backend
        if b.load_error and not b.ready:
            self.win.controller.log("后台桥不可用，扫描文件夹未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        folder = QFileDialog.getExistingDirectory(self, "选择素材文件夹")
        if not folder:
            self._note("未选择文件夹，素材库未变化。")
            return
        self._prepare_backend()
        added = b.add_folder_path(folder)
        self.win.reload_library()
        self._note("已扫描文件夹：新增 %d 条视频，并保留子目录结构。" % added)

    def _refresh(self):
        b = self.win.backend
        if b.ready or not b.load_error:
            b.refresh_materials()
            if b.ready:
                self.win.reload_library()
            self.win.controller.log("素材库 · 刷新素材卡片与缩略图（后台）")
            self._note("已刷新后台素材列表与缩略图缓存。")
            return
        self.win.controller.log("后台桥不可用，刷新未执行：" + b.load_error.splitlines()[-1])
        self._note("后台桥不可用，未能刷新素材。")

    def _back(self):
        f = next((x for x in self.folders if x["id"] == self.active_folder), None)
        parent = f.get("parent") if f else None
        if parent:
            self._select_folder(parent)
        else:
            self.search.clear()
            self._filter("全部")
            self._select_folder("all")

    def _folder_menu(self, fid, global_pos):
        if fid in ("all", "root"):
            return
        folder = next((f for f in self.folders if f["id"] == fid), None)
        if not folder:
            return
        menu = QMenu(self)
        act_rename = menu.addAction("重命名此文件夹")
        act_delete = menu.addAction("删除此文件夹")
        chosen = menu.exec(global_pos)
        if chosen == act_rename:
            old = self.active_folder
            self.active_folder = fid
            self._rename_folder()
            self.active_folder = old
        elif chosen == act_delete:
            ok = QMessageBox.question(
                self, "删除文件夹",
                "确定删除文件夹「%s」吗？文件夹里的素材会回到全部素材，磁盘文件不会被删除。" % folder["name"])
            if ok == QMessageBox.StandardButton.Yes:
                ref = folder.get("folder_path") or folder["name"]
                if self.win.backend.delete_folder(ref):
                    self.win.reload_library()
                    self._note("已删除文件夹「%s」，里面的素材已回到全部素材。" % folder["name"])
                else:
                    self._note("删除文件夹未执行。")

    def _clip_menu(self, cid, global_pos):
        clip = next((c for c in mock.CLIPS if c["id"] == cid), None)
        if not clip:
            return
        # 右键作用对象在菜单弹出前一次性快照：
        # 已多选 → 整组；只点这一张 → 只它自己。
        # 以前这里写死 self._move_one(path)，所以多选时也只移动一条。
        c = self.win.controller
        if cid in c.selected and len(c.selected) > 1:
            target_paths = [cl.get("path") for cl in mock.CLIPS
                            if cl["id"] in c.selected and cl.get("path")
                            and not cl.get("_removed")]
        else:
            target_paths = [clip.get("path")] if clip.get("path") else []
        target_paths = [p for p in target_paths if p]
        tip = ("（%d 条）" % len(target_paths)) if len(target_paths) > 1 else ""

        menu = QMenu(self)
        move_menu = menu.addMenu("移动到文件夹" + tip)
        for f in self.folders:
            if f["id"] in ("all", "root"):
                continue
            ref = f.get("folder_path") or f["name"]
            indent = "　" * max(0, int(f.get("depth") or 1) - 1)
            act = move_menu.addAction("📁 " + indent + f["name"])
            act.triggered.connect(
                lambda _c=False, ref=ref, name=f["name"],
                paths=list(target_paths): self._move_many(paths, ref, name))
        if cid in c.selected:
            act_check = menu.addAction("取消勾选此素材")
        else:
            act_check = menu.addAction("✓ 勾选加入混剪")
        act_check.triggered.connect(lambda: c.toggle_selection(cid))
        menu.addSeparator()
        act_tags = menu.addAction("编辑标签")
        act_tags.triggered.connect(lambda: self._edit_tags(clip))
        menu.addSeparator()
        act_remove = menu.addAction("从素材库移除")
        act_remove.triggered.connect(lambda: self._remove_one(clip.get("path")))
        self._exec_menu(menu, global_pos)

    def _exec_menu(self, menu, pos):
        """弹出右键菜单（单独抽出，离屏测试可替换，避免模态阻塞）。"""
        return menu.exec(pos)

    def _move_many(self, paths, folder_ref, folder_name=""):
        """把一组素材移进文件夹（右键菜单多选时用）。"""
        paths = [p for p in (paths or []) if p]
        if not paths:
            self._note("没有可移动的素材。")
            return
        b = self.win.backend
        if not getattr(b, "ready", False):
            self._note("后台素材库尚未加载，无法移动素材。")
            return
        moved = b.move_paths(paths, folder_ref)
        self.win.reload_library()
        if moved:
            self._note("已移动到「%s」：%d 条素材。" % (
                folder_name or folder_ref, moved))
            self.win.controller.log(
                "素材库 · 移动 %d 条素材到「%s」" % (moved, folder_name or folder_ref))
        else:
            self._note("移动未执行：这些素材可能已经在「%s」里。"
                       % (folder_name or folder_ref))

    def _edit_tags(self, clip):
        tags = clip.get("tags") or []
        text, ok = QInputDialog.getText(
            self, "编辑标签", "标签（用逗号分隔，留空即清除）：",
            text=", ".join(str(t) for t in tags if t))
        if not ok:
            return
        clip["tags"] = [t.strip() for t in text.replace("，", ",").split(",")
                        if t.strip()]
        self._note("已更新标签：%s" % ("、".join(clip["tags"]) or "（无）"))
        self.refresh()

    def _move_one(self, path, folder_ref, folder_name=""):
        if not path:
            return
        if self.win.backend.move_paths([path], folder_ref):
            self.win.reload_library()
            self._note("已移动到「%s」。" % (folder_name or folder_ref))
        else:
            self._note("移动未执行。")

    def _remember_removed(self, paths):
        """把被移除素材的快照留在 controller，供“已移除”筛选和工程保存使用。"""
        c = self.win.controller
        if not hasattr(c, "removed_clips"):
            c.removed_clips = []
        known = {x.get("path") for x in c.removed_clips}
        for clip in mock.CLIPS:
            path = clip.get("path")
            if path and path in paths and path not in known:
                item = dict(clip)
                item["_removed"] = True
                c.removed_clips.append(item)
                known.add(path)

    def _remove_one(self, path):
        if not path:
            return
        removed = self.win.backend.remove_paths([path])
        if removed:
            self._remember_removed({path})
            self.win.controller.selected.discard(
                next((c["id"] for c in mock.CLIPS if c.get("path") == path), ""))
            self.win.reload_library()
            self._note("已从素材库移除，可在「已移除」筛选里查看。")
        else:
            self._note("移除未执行。")

    def _new_category(self):
        b = self.win.backend
        if b.load_error and not b.ready:
            self.win.controller.log("后台桥不可用，新建文件夹未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        text, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not (ok and text.strip()):
            self._note("已取消新建文件夹。")
            return
        self._prepare_backend()
        if b.create_folder_named(text.strip()):
            self.win.reload_library()
            self._note("已创建文件夹「%s」。" % text.strip())
        else:
            self._note("新建文件夹未成功。")

    def _rename_folder(self, new_name=None):
        f = next((x for x in self.folders if x["id"] == self.active_folder), None)
        if not f or f["id"] == "all":
            self._note("请先选中一个文件夹，再重命名。")
            return
        if new_name is None:
            text, ok = QInputDialog.getText(self, "重命名文件夹", "新名称：", text=f["name"])
            if not (ok and text.strip()):
                return
            new_name = text.strip()
        if not new_name or new_name == f["name"]:
            return
        old_name = f["name"]
        old_ref = f.get("folder_path") or old_name
        b = self.win.backend
        if b.ready and f["id"] != "root" and b.rename_folder(old_ref, new_name):
            self.win.controller.log("素材库 · 文件夹「%s」重命名为「%s」（后台）" % (
                old_name, new_name))
            self.win.reload_library()
            target = next((x["id"] for x in self.folders
                           if x["name"] == new_name), "")
            if target:
                self._select_folder(target)
            self._note("已重命名为「%s」。" % new_name)
            return
        self.win.controller.log("素材库 · 文件夹重命名失败，请确认后台素材库已加载")
        self._note("重命名未执行。")

    def _move_selected(self):
        f = next((x for x in self.folders if x["id"] == self.active_folder), None)
        if not f or f["id"] in ("all", "root"):
            self._note("请先选中一个分类文件夹，再移入已选素材。")
            return
        c = self.win.controller
        if not c.selected:
            self._note("当前没有已选素材。")
            return
        b = self.win.backend
        if not b.ready:
            self._note("后台素材库尚未加载，无法移动素材。")
            return
        paths = self.win.selected_paths()
        if not paths:
            self._note("当前选中的素材没有真实文件路径，无法移动。")
            return
        moved = b.move_paths(paths, f.get("folder_path") or f["name"])
        self.win.reload_library()
        self.win.controller.log("素材库 · 将 %d 条素材移入「%s」（后台）" % (moved, f["name"]))
        self._note("已移入「%s」：%d 条素材。" % (f["name"], moved))

    def _analyze_selected(self):
        paths = self.win.active_paths()
        if not paths or self.win.backend.load_error:
            self.win.controller.log("素材库 · 没有真实素材，无法分析选中")
            self._note("请先导入素材并选中后再分析。")
            return

        # 一站式分析设置：分析模式 + 判废规则一次选完。
        # 设置跨轮次保留，弹窗里带出上一次的选择。
        page = getattr(self.win, "page_analyze", None)
        if page is not None:
            from mixcut.ui.demo.verdict_dialog import VerdictDialog
            sample_count = 0
            try:
                sample_count = len(page._cv_ratios())
            except Exception:
                pass
            dlg = VerdictDialog(
                getattr(page, "verdict_config", None), self.win,
                sample_count=sample_count)
            if dlg.exec() != QDialog.Accepted:
                self._note("已取消分析。")
                return
            cfg = dlg.result_config()
            page.verdict_config = cfg
            # 弹窗里选的分析模式同步到分析页的 mode_box，
            # 这样 start_real_analysis（读 mode_box.currentIndex）拿到的就是这里的选择。
            #
            # 关键：不能走 page.set_mode()——它内部会 setCurrentIndex 并触发
            # _on_mode_changed，而那个槽在「已有结果」时会立刻 _launch()
            # 重跑一轮分析，等于用户还没点开始就先跑了一次。
            # 这里直接改下拉框并断信号，只同步界面状态，不触发任何重跑。
            try:
                key = cfg.analysis_mode
                mode_box = getattr(page, "mode_box", None)
                if mode_box is not None:
                    idx = mode_box.findData(key)
                    if idx >= 0:
                        mode_box.blockSignals(True)
                        mode_box.setCurrentIndex(idx)
                        mode_box.blockSignals(False)
                page._mode_key = key
                try:
                    from mixcut.ui.analyze_page import ANALYSIS_MODES
                    page.mode = ANALYSIS_MODES[key]
                    desc = getattr(page, "mode_desc", None)
                    if desc is not None:
                        desc.setText(ANALYSIS_MODES[key]["desc"])
                except Exception:
                    pass
            except Exception:
                pass
            self.win.controller.log(
                "分析设置 · 模式：%s；判废：%s；差帧占比≥%d%%；质量分<%d"
                % (cfg.mode_text(), cfg.summary(),
                   round(cfg.bad_ratio * 100), round(cfg.min_score)))

        self.win.go_page("analysis")
        self.win.start_real_analysis()

    def _clear_checked(self):
        self._reset_analysis_scope()
        self.win.controller.clear_selection()
        b = self.win.backend
        if b.ready:
            b.clear_checked()
        self._note("已取消已选素材。")

    def _remove_selected(self):
        c = self.win.controller
        if not c.selected:
            self._note("请先点选要移除的素材。")
            return
        b = self.win.backend
        if b.ready:
            paths = self.win.selected_paths()
            if paths:
                removed = b.remove_paths(paths)
                if removed:
                    self._remember_removed(set(paths))
                self.win.reload_library()
                self.win.controller.log("素材库 · 从后台移除 %d 条选中素材" % removed)
                self._note("已移除 %d 条素材，可在「已移除」筛选里查看。" % removed)
                return
        self.win.controller.log("素材库 · 移除未执行，后台素材库未加载")
        self._note("后台素材库未加载，无法移除素材。")

    def _check_materials(self):
        b = self.win.backend
        paths = self.win.active_paths()
        if not paths:
            self.win.controller.log("素材库 · 没有真实素材，检查未执行")
            self._note("请先导入素材后再检查。")
            return
        if b.load_error and not b.ready:
            self.win.controller.log("后台桥不可用，检查素材未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        result = b.check_materials()
        total = result.get("total", 0)
        invalid = result.get("invalid", [])
        if invalid:
            QMessageBox.critical(
                self, "检查失败",
                "发现 %d 个无效/损坏的素材文件：\n%s" % (
                    len(invalid), "\n".join(invalid[:10])))
            self.win.controller.log("素材库 · 检查失败：%d 个无效文件" % len(invalid))
            self._note("检查失败：%d 个文件不可读或为空。" % len(invalid))
        else:
            QMessageBox.information(
                self, "检查通过",
                "共 %d 个素材全部有效。\n请点「确定使用」锁定素材。" % total)
            self.win.controller.log("素材库 · 检查通过：%d 个素材有效" % total)
            self._note("检查通过：%d 个素材全部有效。" % total)

    def _confirm_use(self):
        b = self.win.backend
        paths = self.win.active_paths()
        if not paths:
            self._note("素材库为空，请先导入素材。")
            return
        if b.load_error and not b.ready:
            self.win.controller.log("后台桥不可用，确定使用未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        if b.confirm_materials(paths):
            QMessageBox.information(self, "锁定成功", "素材已确认")
            self.win.controller.log("素材库 · 确定使用：%d 条真实素材已锁定" % len(paths))
            self._note("已确定使用当前素材，可前往「分析中心」。")
        else:
            QMessageBox.warning(self, "提示", "存在无效文件，请先检查素材。")
            self._note("素材确认失败，请先检查有效文件。")

    def _confirm_mix(self):
        b = self.win.backend
        paths = self.win.active_paths()
        if not paths:
            self._note("素材库为空，请先导入素材。")
            return
        if b.load_error and not b.ready:
            self.win.controller.log("后台桥不可用，确认混剪素材未执行：" +
                                    b.load_error.splitlines()[-1])
            return
        if b.confirm_materials(paths):
            QMessageBox.information(self, "锁定成功", "素材已确认")
            self.win.controller.log("素材库 · 确认混剪素材：%d 条真实素材已锁定" % len(paths))
            self._note("混剪素材已确认，进入「混剪参数」后可启动智能混剪。")
        else:
            QMessageBox.warning(self, "提示", "存在无效文件，请先检查素材。")
            self._note("素材确认失败，请先检查有效文件。")

    def _change_view(self, _idx):
        self.cols = int(self.view_mode.currentData() or 4)
        self._reposition()

    def _reposition(self):
        self._apply_search()
        self._reflow(self._shown_ids)

    @staticmethod
    def _dur_seconds(clip):
        try:
            text = str(clip.get("dur", "0")).strip().rstrip("s")
            if ":" in text:
                parts = [float(x) for x in text.split(":")]
                total = 0.0
                for part in parts:
                    total = total * 60 + part
                return total
            return float(text)
        except (TypeError, ValueError):
            return 0.0

    def _ordered_clips(self):
        key = self.sort.currentData() or "name"
        if key == "dur":
            return sorted(mock.CLIPS, key=self._dur_seconds)
        if key == "quality":
            return sorted(mock.CLIPS, key=lambda x: x.get("quality", 0), reverse=True)
        return sorted(mock.CLIPS, key=lambda x: x["name"].lower())

    def _reflow(self, shown_ids):
        ordered = self._ordered_clips()
        shown_clips = [self.cards[c["id"]] for c in ordered if c["id"] in shown_ids]
        rest_clips = [self.cards[c["id"]] for c in ordered if c["id"] not in shown_ids]
        visible_folders = [self.folder_cards[fid] for fid in self._folder_shown]
        rest_folders = [fc for fid, fc in self.folder_cards.items()
                        if fid not in self._folder_shown]
        widgets = visible_folders + shown_clips + rest_folders + rest_clips
        for idx, w in enumerate(widgets):
            self.grid.addWidget(w, idx // self.cols, idx % self.cols)
        for col in range(4):
            self.grid.setColumnStretch(col, 1 if col < self.cols else 0)

    def _filter(self, name):
        for k, chip in self.chips.items():
            chip.setChecked(k == name)
            chip.update()
        self._apply_search()
        self._reflow(self._shown_ids)

    def _clip_in_folder(self, clip, fid):
        """素材是否属于文件夹 fid（含子目录）。

        · all ：素材库根目录下、没有归入任何文件夹的素材（已归类的不再出现）
        · root：整个素材库（含全部子目录）
        · p:xx：该目录及其子目录
        """
        fp = str(clip.get("folder_path") or "").strip().strip("/")
        if fid in ("", None):
            return not fp
        if fid == "all":
            return not fp
        if fid == "root":
            return True
        folder = next((f for f in self.folders if f["id"] == fid), None)
        if folder is None:
            return False
        base = str(folder.get("folder_path") or "").strip().strip("/")
        if not base:
            return True
        return fp == base or fp.startswith(base + "/")

    def _apply_search(self):
        text = self.search.text().strip().lower()
        mode = next((k for k, chip in self.chips.items() if chip.isChecked()), "全部")
        min_sec = float(self.min_dur.currentData() or 0)
        c = self.win.controller
        show_folders = not text and mode == "全部" and min_sec == 0
        folder_shown = []
        for fid, fc in self.folder_cards.items():
            f = next((x for x in self.folders if x["id"] == fid), None)
            show = bool(show_folders and f and self.active_folder != "root" and (
                f["parent"] == self.active_folder or
                (self.active_folder == "all" and f["parent"] in ("root", "all"))))
            fc.setVisible(show)
            if show:
                folder_shown.append(fid)
        self._folder_shown = folder_shown
        shown = set()
        for clip in mock.CLIPS:
            card = self.cards[clip["id"]]
            show = True
            if text and text not in clip["name"].lower():
                show = False
            if self._dur_seconds(clip) < min_sec:
                show = False
            if mode == "已移除":
                show = show and clip["id"] in c.removed
            elif clip["id"] in c.removed:
                show = False
            elif mode == "已分析":
                show = show and bool(clip.get("analyzed") or c.analyzed_all)
            elif mode == "未分析":
                show = show and not bool(clip.get("analyzed") or c.analyzed_all)
            if show and show_folders:
                show = self._clip_in_folder(clip, self.active_folder)
            if show:
                shown.add(clip["id"])
            card.setVisible(show)
        self._shown_ids = shown
        self._folder_shown = folder_shown
        denom = len(shown)
        if show_folders:
            denom = self._folder_count(self.active_folder)
        self.count_label.setText("已选 %d / %d" % (len(c.selected), denom))
        self._update_folder_counts()

    def _reset_analysis_scope(self):
        """兼容旧 MainWindow：没有分析范围接口时静默跳过。"""
        setter = getattr(self.win, "set_analysis_scope", None)
        if callable(setter):
            setter(None)

    def visible_paths(self):
        """当前文件夹/筛选/搜索下可见的真实素材路径，作为分析范围。"""
        c = self.win.controller
        out = []
        for clip in self._ordered_clips():
            cid = clip["id"]
            if cid not in self._shown_ids or cid in c.removed:
                continue
            path = clip.get("path")
            if path and path not in out:
                out.append(path)
        return out

    def _visible_order(self):
        """当前筛选/排序下实际可见的素材顺序，供 Shift 连选使用。"""
        return [clip["id"] for clip in self._ordered_clips()
                if clip["id"] in self._shown_ids]

    def _select_range(self, cid, additive=False):
        """从锚点到当前卡片选中连续区间；additive=True 时并入原选区。"""
        c = self.win.controller
        self._reset_analysis_scope()
        ordered = self._visible_order()
        if cid not in ordered:
            return
        anchor = self._anchor_id
        if anchor not in ordered:
            anchor = getattr(c, "_current", None)
        if anchor not in ordered:
            anchor = cid
        start, end = sorted((ordered.index(anchor), ordered.index(cid)))
        ids = set(ordered[start:end + 1])
        win = getattr(c, "win", None)
        if win is not None and hasattr(win, "_push_history"):
            win._push_history()
        if additive:
            c.selected.update(ids)
        else:
            c.selected = ids
        c._current = cid
        c.stateChanged.emit()

    def _toggle(self, cid):
        mods = QApplication.keyboardModifiers()
        c = self.win.controller
        self._reset_analysis_scope()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self._select_range(
                cid, additive=bool(mods & Qt.KeyboardModifier.ControlModifier))
        elif (cid in getattr(c, "selected", set())
              and len(c.selected) > 1):
            # 已多选时按住其中一张：这是拖动/整组操作的起点，
            # 不能把它切换掉，否则拖动会丢这一条，用户看到的就是“只带走一个”。
            c._current = cid
            c.stateChanged.emit()
        else:
            c.toggle_selection(cid)
        self._anchor_id = cid

    def refresh(self):
        c = self.win.controller
        for clip in mock.CLIPS:
            card = self.cards[clip["id"]]
            card.set_selected(clip["id"] in c.selected)
            if clip["id"] in c.removed:
                card.set_status("已移除 · 无效片段", T.ERR)
            elif c.analyzed_all:
                card.set_status("已分析", T.OK)
            else:
                card.set_status("未分析", T.TEXT_3)
        root_name = next((f["name"] for f in self.folders if f["id"] == "root"), "")
        if root_name:
            self.subtitle.setText("%d 条素材 · %s" % (c.visible_count(), root_name))
        elif mock.CLIPS:
            self.subtitle.setText("%d 条素材 · 本地素材库" % c.visible_count())
        else:
            self.subtitle.setText("0 条素材 · 尚未导入")
        self.count_label.setText("已选 %d / %d" % (len(c.selected), c.visible_count()))
        self.btn_rename.setEnabled(bool(self.active_folder != "all"))
        self._apply_search()
