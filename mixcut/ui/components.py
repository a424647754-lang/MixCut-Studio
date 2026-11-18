# -*- coding: utf-8 -*-
"""
MixCut · 可复用界面组件

只做外观与交互，不持有业务状态：
  IconButton / Card / SectionCard（可折叠）/ StatusBadge / LabeledField
  LogPanel（暂停滚动、复制、按级别筛选）
"""

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QGuiApplication, QFont, QPixmap
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QToolButton, QPlainTextEdit, QComboBox, QSizePolicy, QGridLayout,
)

from . import theme


# ---------------------------------------------------------
# 基础部件
# ---------------------------------------------------------

class Hairline(QFrame):
    """1px 分隔线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


class Card(QFrame):
    """普通卡片容器。"""

    def __init__(self, parent=None, padding=14):
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(padding, padding, padding, padding)
        self.layout.setSpacing(10)


class StatusBadge(QLabel):
    """状态徽标：idle / running / success / warning / error。"""

    COLORS = {
        "idle": ("#3A404D", theme.COLORS["muted"]),
        "running": (theme.COLORS["accent_dim"], theme.COLORS["accent"]),
        "success": ("#1E3D2E", theme.COLORS["success"]),
        "warning": ("#3D311D", theme.COLORS["warning"]),
        "error": ("#43232A", theme.COLORS["danger"]),
        "canceled": ("#33363E", theme.COLORS["muted"]),
    }

    def __init__(self, text="", level="idle", parent=None):
        super().__init__(text, parent)
        self.set_size(level)

    def set_size(self, level):
        bg, fg = self.COLORS.get(level, self.COLORS["idle"])
        self.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:9px;"
            f"padding:2px 9px;font-size:{theme.FONT_SIZE_SMALL}px;font-weight:600;"
        )
        self.setAlignment(Qt.AlignCenter)


class SectionCard(Card):
    """可折叠的参数卡片（Linear 式分组）。"""

    def __init__(self, title, subtitle="", parent=None, collapsible=True):
        super().__init__(parent)
        self._collapsible = collapsible
        self._expanded = True
        self._title = title

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("Title")
        head.addWidget(self.title_label)
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("Faint")
            head.addWidget(self.subtitle_label)
        head.addStretch(1)

        self.toggle_btn = None
        if collapsible:
            self.toggle_btn = QToolButton()
            self.toggle_btn.setText("▾")
            self.toggle_btn.setCursor(Qt.PointingHandCursor)
            self.toggle_btn.setStyleSheet(
                "QToolButton{background:transparent;border:none;color:%s;"
                "font-size:14px;}" % theme.COLORS["muted"])
            self.toggle_btn.clicked.connect(self.toggle)
            head.addWidget(self.toggle_btn)
        self.layout.addLayout(head)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 4, 0, 0)
        self.body_layout.setSpacing(8)
        self.layout.addWidget(self.body)

    def add_row(self, widget):
        self.body_layout.addWidget(widget)
        return widget

    def add_layout(self, lay):
        self.body_layout.addLayout(lay)

    def toggle(self):
        self._expanded = not self._expanded
        self.body.setVisible(self._expanded)
        if self.toggle_btn:
            self.toggle_btn.setText("▾" if self._expanded else "▸")

    def collapse(self):
        if self._expanded:
            self.toggle()


class LabeledField(QWidget):
    """一行参数：左侧名称与说明，右侧控件。"""

    def __init__(self, label, widget, hint="", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(10)
        name = QLabel(label)
        name.setMinimumWidth(96)
        row.addWidget(name)
        row.addWidget(widget, 1)
        lay.addLayout(row)
        if hint:
            tip = QLabel(hint)
            tip.setObjectName("Faint")
            tip.setWordWrap(True)
            tip.setStyleSheet(f"padding-left:106px;font-size:{theme.FONT_SIZE_SMALL}px;")
            lay.addWidget(tip)


# ---------------------------------------------------------
# 实时日志面板
# ---------------------------------------------------------

class LogPanel(QWidget):
    """真实日志的展示面板：自动滚动可暂停、可复制、可按级别筛选。

    只显示后端通过 add() 传进来的消息，不生成任何内容。
    """

    LEVELS = ("全部", "普通", "警告", "错误")

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.title = QLabel("运行日志")
        self.title.setObjectName("SectionTitle")
        bar.addWidget(self.title)
        bar.addStretch(1)

        self.filter_box = QComboBox()
        self.filter_box.addItems(self.LEVELS)
        self.filter_box.setFixedWidth(84)
        self.filter_box.currentIndexChanged.connect(self._reapply)
        bar.addWidget(self.filter_box)

        self.autoscroll_btn = QToolButton()
        self.autoscroll_btn.setText("自动滚动")
        self.autoscroll_btn.setCheckable(True)
        self.autoscroll_btn.setChecked(True)
        self.autoscroll_btn.setStyleSheet(
            "QToolButton{background:transparent;border:1px solid %s;border-radius:5px;"
            "padding:3px 8px;color:%s;font-size:%dpx;}"
            "QToolButton:checked{color:%s;border-color:%s;}"
            % (theme.COLORS["border"], theme.COLORS["muted"],
               theme.FONT_SIZE_SMALL, theme.COLORS["accent"],
               theme.COLORS["accent"]))
        bar.addWidget(self.autoscroll_btn)

        self.copy_btn = QToolButton()
        self.copy_btn.setText("复制")
        self.copy_btn.setStyleSheet(
            "QToolButton{background:transparent;border:1px solid %s;border-radius:5px;"
            "padding:3px 8px;color:%s;font-size:%dpx;}"
            % (theme.COLORS["border"], theme.COLORS["muted"],
               theme.FONT_SIZE_SMALL))
        self.copy_btn.clicked.connect(self.copy_all)
        bar.addWidget(self.copy_btn)

        self.clear_btn = QToolButton()
        self.clear_btn.setText("清空")
        self.clear_btn.setStyleSheet(self.copy_btn.styleSheet())
        self.clear_btn.clicked.connect(self.clear)
        bar.addWidget(self.clear_btn)
        lay.addLayout(bar)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        mono = QFont("Cascadia Mono")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self.view.setFont(mono)
        self.view.setStyleSheet(
            f"background:{theme.COLORS['bg']};border:1px solid "
            f"{theme.COLORS['border_soft']};border-radius:6px;"
            f"color:{theme.COLORS['text']};padding:6px;")
        lay.addWidget(self.view, 1)

        self._entries = []   # [(level, text)]

    # ---- 数据 ----
    def add(self, message, level="info"):
        level = level if level in ("info", "warn", "error") else "info"
        self._entries.append((level, str(message)))
        if self._match(level):
            self._append_line(level, str(message))

    def _match(self, level):
        idx = self.filter_box.currentIndex()
        return idx == 0 or (idx == 1 and level == "info") \
            or (idx == 2 and level == "warn") or (idx == 3 and level == "error")

    def _append_line(self, level, text):
        color = {"info": theme.COLORS["text"],
                 "warn": theme.COLORS["warning"],
                 "error": theme.COLORS["danger"]}[level]
        prefix = {"info": "", "warn": "[警告] ", "error": "[错误] "}[level]
        self.view.appendHtml(
            f'<span style="color:{color}">{prefix}{_escape(text)}</span>')
        if self.autoscroll_btn.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _reapply(self):
        self.view.clear()
        for level, text in self._entries:
            if self._match(level):
                self._append_line(level, text)

    # ---- 操作 ----
    def copy_all(self):
        text = "\n".join(
            ("[警告] " if lv == "warn" else "[错误] " if lv == "error" else "")
            + t for lv, t in self._entries)
        QGuiApplication.clipboard().setText(text)

    def clear(self):
        self._entries.clear()
        self.view.clear()

    def text(self):
        return self.view.toPlainText()

    def count(self, level=None):
        if level is None:
            return len(self._entries)
        return sum(1 for lv, _t in self._entries if lv == level)


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>"))


# ---------------------------------------------------------
# 小工具
# ---------------------------------------------------------

ICON_SIZE = QSize(16, 16)


def make_button(text, kind="default", icon_text="", tooltip=""):
    """统一风格的按钮。kind: default / primary / ghost / danger"""
    btn = QPushButton(f"{icon_text} {text}".strip() if icon_text else text)
    if kind == "primary":
        btn.setObjectName("Primary")
    elif kind == "ghost":
        btn.setObjectName("Ghost")
    elif kind == "danger":
        btn.setObjectName("Danger")
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.PointingHandCursor)
    return btn


class Dot(QWidget):
    """运行状态圆点（真实状态驱动颜色）。"""

    def __init__(self, level="success", parent=None):
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self.set_level(level)

    def set_level(self, level):
        color = {"success": theme.COLORS["success"],
                 "warning": theme.COLORS["warning"],
                 "error": theme.COLORS["danger"],
                 "idle": theme.COLORS["faint"]}.get(level,
                                                    theme.COLORS["faint"])
        self.setStyleSheet(f"background:{color};border-radius:4px;")


def debounce(widget, ms, slot):
    """一个轻量的输入防抖（筛选框用）。"""
    timer = QTimer(widget)
    timer.setSingleShot(True)
    timer.setInterval(ms)
    timer.timeout.connect(slot)
    return timer


class TagMultiSelect(QFrame):
    """可多选的关键词标签组：点击切换选中，选中项通过信号抛出。

    用于转场黑名单：选中的关键词命中对应素材标签时，该切点只硬切。
    """

    selection_changed = Signal(list)

    def __init__(self, keywords, columns=4, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        self._buttons = {}
        for i, kw in enumerate(keywords):
            btn = QPushButton(kw)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:transparent;color:%s;border:1px solid %s;"
                "border-radius:5px;padding:5px 10px;font-size:12px;}"
                "QPushButton:hover{border-color:%s;}"
                "QPushButton:checked{background:%s;color:#101113;"
                "border-color:%s;font-weight:600;}"
                % (theme.COLORS["muted"], theme.COLORS["border"],
                   theme.COLORS["muted"], theme.COLORS["accent"],
                   theme.COLORS["accent"]))
            btn.clicked.connect(self._emit)
            grid.addWidget(btn, i // columns, i % columns)
            self._buttons[kw] = btn

    def selected(self):
        return [kw for kw, b in self._buttons.items() if b.isChecked()]

    def set_selected(self, kws):
        target = set(kws)
        for kw, b in self._buttons.items():
            b.setChecked(kw in target)

    def _emit(self):
        self.selection_changed.emit(self.selected())


class ScaledImageLabel(QWidget):
    """自适应图片预览：持有原始 QPixmap，窗口缩放时按图片比例等比居中缩放。

    跟素材库 AspectRatioWidget 一样的思路：全屏、拖动窗口都会跟着变，
    不会固定在一个尺寸。用于分析页的分析帧预览。
    """

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self._pix = None
        self.setStyleSheet(
            "background:%s;border:1px solid %s;border-radius:6px;"
            % (theme.COLORS["bg"], theme.COLORS["border_soft"]))

        self._placeholder = QLabel(placeholder, self)
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(
            "border:none;color:%s;font-size:12px;"
            % theme.COLORS["faint"])

        self._image_label = QLabel(self)
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("border:none;background:transparent;")
        self._image_label.hide()

    def set_image(self, pixmap):
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._pix = pixmap
            self._placeholder.hide()
            self._image_label.show()
            self._update()
        else:
            self.clear_image()

    def set_placeholder_text(self, text):
        self._placeholder.setText(text)

    def clear_image(self):
        self._pix = None
        self._image_label.clear()
        self._image_label.hide()
        self._placeholder.show()

    def resizeEvent(self, event):
        self._update()
        super().resizeEvent(event)

    def _update(self):
        cw, ch = self.width(), self.height()
        if cw <= 0 or ch <= 0:
            return
        if self._pix is not None and not self._pix.isNull():
            scaled = self._pix.scaled(
                cw, ch, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._image_label.setGeometry(0, 0, cw, ch)
            self._image_label.setPixmap(scaled)
        else:
            self._placeholder.setGeometry(0, 0, cw, ch)
