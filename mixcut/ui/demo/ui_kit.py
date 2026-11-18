# -*- coding: utf-8 -*-
"""基础控件：图标、按钮、卡片、侧边导航、胶囊、缩略图、数字滚动。"""

import os

from PySide6.QtCore import (
    Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from . import prefs, theme as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THUMB_DIR = os.path.join(ROOT, "media", "thumbs")
_THUMB_CACHE = {}


def thumb_pixmap(name: str, w: int, h: int) -> QPixmap:
    key = (name, w, h)
    if key in _THUMB_CACHE:
        return _THUMB_CACHE[key]
    path = os.path.join(THUMB_DIR, name)
    pm = QPixmap(path)
    if pm.isNull():
        pm = QPixmap(w, h)
        pm.fill(QColor(T.CARD_HI))
    else:
        pm = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                       Qt.TransformationMode.SmoothTransformation)
        pm = pm.copy((pm.width() - w) // 2, (pm.height() - h) // 2, w, h)
    _THUMB_CACHE[key] = pm
    return pm


def draw_icon(p: QPainter, name: str, rect: QRectF, color: QColor, weight: float = 1.6):
    """线性图标，统一 24 网格。"""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QPen(color, weight))
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    u = min(w, h) / 24.0
    cx, cy = x + w / 2, y + h / 2

    def pt(px, py):
        return QPointF(cx + (px - 12) * u, cy + (py - 12) * u)

    def r(px, py, pw, ph):
        return QRectF(cx + (px - 12) * u, cy + (py - 12) * u, pw * u, ph * u)

    if name == "home":
        p.drawPolyline([pt(4, 11), pt(12, 4.5), pt(20, 11)])
        p.drawPolyline([pt(6.5, 10.5), pt(6.5, 19.5), pt(17.5, 19.5), pt(17.5, 10.5)])
        p.drawLine(pt(11, 19.5), pt(11, 14.5))
        p.drawLine(pt(13, 19.5), pt(13, 14.5))
    elif name == "library":
        p.drawRoundedRect(r(4, 6, 4, 14), 1.2, 1.2)
        p.drawRoundedRect(r(9.5, 4, 4, 16), 1.2, 1.2)
        p.drawRoundedRect(r(15, 6, 4, 14), 1.2, 1.2)
    elif name == "spark":
        path = QPainterPath()
        path.moveTo(pt(12, 3.5))
        path.lineTo(pt(13.9, 9.4))
        path.lineTo(pt(20, 12))
        path.lineTo(pt(13.9, 14.6))
        path.lineTo(pt(12, 20.5))
        path.lineTo(pt(10.1, 14.6))
        path.lineTo(pt(4, 12))
        path.lineTo(pt(10.1, 9.4))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "flow":
        p.drawRoundedRect(r(3.5, 4.5, 6.5, 6.5), 1.5, 1.5)
        p.drawRoundedRect(r(14, 4.5, 6.5, 6.5), 1.5, 1.5)
        p.drawRoundedRect(r(8.5, 14, 6.5, 6.5), 1.5, 1.5)
        p.drawLine(pt(10, 10.8), pt(10, 13.8))
        p.drawLine(pt(10, 13.8), pt(8.5, 13.8))
        p.drawLine(pt(16.5, 11), pt(16.5, 13.8))
        p.drawLine(pt(16.5, 13.8), pt(15, 13.8))
    elif name == "settings":
        p.drawEllipse(r(8.5, 8.5, 7, 7))
        for ang in range(0, 360, 90):
            p.save()
            p.translate(cx, cy)
            p.rotate(ang + 45)
            p.drawLine(QPointF(0, -8.4 * u), QPointF(0, -10.6 * u))
            p.restore()
    elif name == "sliders":
        for i, py in enumerate((7, 12, 17)):
            p.drawLine(pt(4, py), pt(20, py))
            px = (8, 15, 11)[i]
            p.setBrush(color)
            p.drawEllipse(QPointF(cx + (px - 12) * u, cy + (py - 12) * u), 2.0 * u, 2.0 * u)
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "import":
        p.drawLine(pt(12, 4), pt(12, 14))
        p.drawPolyline([pt(8, 10.5), pt(12, 14.5), pt(16, 10.5)])
        p.drawPolyline([pt(5, 15.5), pt(5, 19), pt(19, 19), pt(19, 15.5)])
    elif name == "play":
        path = QPainterPath()
        path.moveTo(pt(8.5, 5.5))
        path.lineTo(pt(18.5, 12))
        path.lineTo(pt(8.5, 18.5))
        path.closeSubpath()
        p.setBrush(color)
        p.drawPath(path)
    elif name == "pause":
        p.setBrush(color)
        p.drawRoundedRect(r(8, 6, 2.4, 12), 1, 1)
        p.drawRoundedRect(r(13.6, 6, 2.4, 12), 1, 1)
    elif name == "stop":
        p.setBrush(color)
        p.drawRoundedRect(r(7.5, 7.5, 9, 9), 1.2, 1.2)
    elif name == "search":
        p.drawEllipse(r(5, 5, 11, 11))
        p.drawLine(pt(15.5, 15.5), pt(19.5, 19.5))
    elif name == "folder":
        p.drawPolyline([pt(3.5, 7), pt(10, 7), pt(11.5, 9), pt(20.5, 9)])
        p.drawPolyline([pt(3.5, 7), pt(3.5, 18.5), pt(20.5, 18.5), pt(20.5, 9)])
    elif name == "check":
        p.drawPolyline([pt(5, 12.5), pt(10, 17), pt(19, 7)])
    elif name == "close":
        p.drawLine(pt(6.5, 6.5), pt(17.5, 17.5))
        p.drawLine(pt(17.5, 6.5), pt(6.5, 17.5))
    elif name == "chevron":
        p.drawPolyline([pt(9, 6.5), pt(14.5, 12), pt(9, 17.5)])
    elif name == "grid":
        for px, py in ((5, 5), (13.5, 5), (5, 13.5), (13.5, 13.5)):
            p.drawRoundedRect(r(px, py, 5.5, 5.5), 1.2, 1.2)
    elif name == "list":
        for py in (7, 12, 17):
            p.drawEllipse(r(4.5, py - 1, 2, 2))
            p.drawLine(pt(9, py), pt(19, py))
    elif name == "minus":
        p.drawLine(pt(5, 12), pt(19, 12))
    elif name == "maximize":
        p.drawRoundedRect(r(5.5, 5.5, 13, 13), 1.2, 1.2)
    elif name == "restore":
        p.drawRoundedRect(r(8, 4.5, 11.5, 11.5), 1.2, 1.2)
        p.drawRoundedRect(r(4.5, 8, 11.5, 11.5), 1.2, 1.2)
    p.restore()


def paint_card(p: QPainter, rect: QRectF, radius: int = T.R_CARD,
               fill: str = T.CARD, border: str = T.LINE, alpha: int = 255):
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QPen(T.c(border), 1))
    p.setBrush(T.c(fill, alpha))
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


class Card(QFrame):
    def __init__(self, parent=None, radius: int = T.R_CARD, fill: str = T.CARD,
                 border: str = T.LINE, padding: int = 20, spacing: int = 14):
        super().__init__(parent)
        self._radius = radius
        self._fill = fill
        self._border = border
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(spacing)

    def paintEvent(self, _e):
        p = QPainter(self)
        paint_card(p, QRectF(self.rect()), self._radius, self._fill, self._border)
        p.end()


class SoftButton(QAbstractButton):
    """白底主按钮 / 次级按钮 / 幽灵按钮，按下 0.97 缩放。"""

    def __init__(self, text: str, kind: str = "primary", icon: str = "", parent=None,
                 height: int = 36, radius: int = T.R_CTRL, min_width: int = 0):
        super().__init__(parent)
        self.setText(text)
        self.kind = kind
        self.icon = icon
        self._scale = 1.0
        self._hover = 0.0
        self._radius = radius
        self.setFixedHeight(height)
        pad = 30 + (18 if icon else 0)
        estimate = pad + len(text) * 14
        self.setMinimumWidth(max(min_width, estimate, 72 if text else height))
        if min_width:
            self.setMinimumWidth(min_width)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._anim = QPropertyAnimation(self, b"scale", self)
        self._anim.setDuration(110)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(140)
        self._hover_anim.valueChanged.connect(self._on_hover)

    def _on_hover(self, v):
        self._hover = float(v)
        self.update()

    def enterEvent(self, e):
        self._hover_anim.stop()
        if not prefs.motion("button"):
            self._hover = 1.0
            self.update()
            super().enterEvent(e)
            return
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_anim.stop()
        if not prefs.motion("button"):
            self._hover = 0.0
            self.update()
            super().leaveEvent(e)
            return
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(e)

    def get_scale(self):
        return self._scale

    def set_scale(self, v):
        self._scale = v
        self.update()

    scale = Property(float, get_scale, set_scale)

    def mousePressEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(0.97)
        self._anim.start()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self._scale, self._scale)
        p.translate(-self.width() / 2, -self.height() / 2)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        if self.kind == "primary":
            bg = QColor("#FFFFFF") if not self._hover else QColor("#EDEFF3")
            fg = QColor(T.TEXT_INV)
            border = QColor("#FFFFFF")
        elif self.kind == "secondary":
            bg = QColor(T.CARD_HI).lighter(100 + int(6 * self._hover))
            fg = QColor(T.TEXT)
            border = QColor(T.LINE_HI)
        elif self.kind == "accent":
            bg = QColor(T.ACCENT_BG).lighter(100 + int(8 * self._hover))
            fg = QColor("#EAE4FF")
            border = QColor(T.ACCENT_LINE)
        else:  # ghost
            bg = QColor(255, 255, 255, int(10 + 10 * self._hover))
            fg = QColor(T.TEXT_2 if not self.isEnabled() else T.TEXT)
            border = QColor(255, 255, 255, int(18 + 10 * self._hover))

        if not self.isEnabled():
            bg = QColor(T.CARD)
            fg = QColor(T.TEXT_3)
            border = QColor(T.LINE_SOFT)

        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, self._radius, self._radius)

        f = T.font(13, QFont.Weight.DemiBold)
        p.setFont(f)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(self.text())
        iw = 16 if self.icon else 0
        gap = 7 if self.icon else 0
        x = (self.width() - (tw + iw + gap)) / 2
        if self.icon:
            draw_icon(p, self.icon, QRectF(x, (self.height() - 16) / 2, 16, 16), fg, 1.4)
        p.setPen(fg)
        p.drawText(QRectF(x + iw + gap, 0, tw + 4, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        if self.hasFocus():
            p.setPen(QPen(T.c(T.ACCENT, 190), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), self._radius - 1, self._radius - 1)
        p.end()


class IconButton(QAbstractButton):
    def __init__(self, icon: str, size: int = 32, parent=None, tooltip: str = "", danger: bool = False):
        super().__init__(parent)
        self.icon = icon
        self._size = size
        self._hover = False
        self._danger = danger
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(T.c("#3A2226") if self._danger else T.c(T.CARD_HI))
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        if self._danger and self._hover:
            col = T.c("#E58B84")
        else:
            col = T.c(T.TEXT if self._hover else T.TEXT_2)
        draw_icon(p, self.icon, QRectF(self.rect()), col, 1.5)
        p.end()


class NavItem(QAbstractButton):
    def __init__(self, key: str, label: str, icon: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.icon = icon
        self.setText(label)
        self.setCheckable(True)
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = 0.0
        self._ha = QVariantAnimation(self)
        self._ha.setDuration(130)
        self._ha.valueChanged.connect(self._hv)
        self._active_t = 0.0
        self._aa = QVariantAnimation(self)
        self._aa.setDuration(160)
        self._aa.valueChanged.connect(self._av)

    def _hv(self, v):
        self._hover = float(v)
        self.update()

    def _av(self, v):
        self._active_t = float(v)
        self.update()

    def set_active(self, on: bool, animate: bool = True):
        if not animate:
            self._active_t = 1.0 if on else 0.0
            self.update()
            return
        self._aa.stop()
        self._aa.setStartValue(self._active_t)
        self._aa.setEndValue(1.0 if on else 0.0)
        self._aa.start()

    def enterEvent(self, e):
        self._ha.stop()
        self._ha.setStartValue(self._hover)
        self._ha.setEndValue(1.0)
        self._ha.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._ha.stop()
        self._ha.setStartValue(self._hover)
        self._ha.setEndValue(0.0)
        self._ha.start()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0, 0, 0, 0)
        if self._active_t > 0:
            p.setPen(QPen(T.c(T.ACCENT_LINE, int(160 * self._active_t)), 1))
            p.setBrush(T.c(T.ACCENT_BG, int(235 * self._active_t)))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
        elif self._hover > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(T.c(T.CARD_HI, int(180 * self._hover)))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
        fg = T.c("#F2EEFF" if self._active_t > 0.5 else (T.TEXT if self._hover > 0.5 else T.TEXT_2))
        draw_icon(p, self.icon, QRectF(14, (self.height() - 17) / 2, 17, 17), fg, 1.5)
        p.setFont(T.font(13, QFont.Weight.Medium))
        p.setPen(fg)
        p.drawText(QRectF(42, 0, self.width() - 52, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class TrafficLights(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 14)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colors = ["#F26A5F", "#F2C14E", "#55C57A"]
        for i, col in enumerate(colors):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            p.drawEllipse(QPointF(7 + i * 18, 7), 5.5, 5.5)
        p.end()


class Chip(QAbstractButton):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self._hover = False
        fm = self.fontMetrics()
        self.setFixedWidth(max(64, fm.horizontalAdvance(text) + 26))

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked():
            p.setPen(QPen(T.c(T.ACCENT_LINE), 1))
            p.setBrush(T.c(T.ACCENT_BG))
            fg = QColor("#EFE9FF")
        else:
            p.setPen(QPen(T.c(T.LINE_HI if self._hover else T.LINE), 1))
            p.setBrush(T.c(T.CARD_HI if self._hover else T.CARD))
            fg = QColor(T.TEXT_2)
        p.drawRoundedRect(r, T.R_CHIP, T.R_CHIP)
        p.setPen(fg)
        p.setFont(T.font(12))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class Dot(QWidget):
    def __init__(self, color: str = T.OK, size: int = 8, parent=None):
        super().__init__(parent)
        self._color = color
        self._alpha = 255
        self.setFixedSize(size, size)

    def set_color(self, color: str):
        self._color = color
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.c(self._color, self._alpha))
        p.drawEllipse(QRectF(self.rect()))
        p.end()


class Thumb(QWidget):
    """视频缩略图，封面裁切 + 圆角 + 可选时长角标。"""

    def __init__(self, name: str, radius: int = 10, parent=None,
                 duration: str = "", overlay: bool = True):
        super().__init__(parent)
        self.name = name
        self.radius = radius
        self.duration = duration
        self.overlay = overlay
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(90)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        p.setClipPath(path)
        pm = thumb_pixmap(self.name, int(rect.width()), int(rect.height()))
        p.drawPixmap(0, 0, pm)
        # 底部渐暗，保证角标可读
        if self.overlay and self.duration:
            g = QLinearGradient(QPointF(0, rect.height() * 0.7), QPointF(0, rect.height()))
            g.setColorAt(0, T.c("#000000", 0))
            g.setColorAt(1, T.c("#000000", 160))
            p.fillRect(rect, QBrush(g))
            p.setFont(T.font(11, QFont.Weight.DemiBold, mono=True))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(self.duration)
            badge = QRectF(rect.right() - tw - 20, rect.bottom() - 24, tw + 12, 17)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(T.c("#000000", 150))
            p.drawRoundedRect(badge, 5, 5)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, self.duration)
        p.end()


class RollingNumber(QLabel):
    def __init__(self, value: int = 0, suffix: str = "", parent=None):
        super().__init__(parent)
        self._value = value
        self._display = float(value)
        self.suffix = suffix
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._tick)
        self.set_value(value, animate=False)

    def _tick(self, v):
        self._display = float(v)
        self.setText("%d%s" % (int(round(self._display)), self.suffix))

    def set_value(self, v: int, animate: bool = True):
        self._value = int(v)
        if not animate:
            self._display = float(v)
            self.setText("%d%s" % (v, self.suffix))
            return
        self._anim.stop()
        self._anim.setStartValue(self._display)
        self._anim.setEndValue(float(v))
        self._anim.start()

    def value(self):
        return self._value
