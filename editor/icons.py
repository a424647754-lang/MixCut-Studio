"""程序化线性图标集。

所有图标用 QPainter 现画，不依赖位图资源：任何 DPI 下都清晰，也方便
按主题色着色。坐标一律用 0~1 的归一化值，再乘以尺寸。

命名沿用达芬奇的界面语言（媒体/剪辑/调色/导出、刀片、吸附、标记、链接）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

from . import ui_theme as T


def _stroke(painter: QPainter, size: float, color: str) -> QPen:
    pen = QPen(QColor(color), max(1.0, size / 14.0))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _draw(painter: QPainter, kind: str, s: float, color: str) -> None:
    pen = _stroke(painter, s, color)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    def rect(x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(x * s, y * s, w * s, h * s)

    def poly(points: list[tuple[float, float]]) -> QPolygonF:
        return QPolygonF([QPointF(x * s, y * s) for x, y in points])

    def filled(points: list[tuple[float, float]]) -> None:
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(poly(points))

    if kind == "play":
        filled([(0.32, 0.20), (0.32, 0.80), (0.80, 0.50)])
    elif kind == "pause":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawRect(rect(0.32, 0.22, 0.12, 0.56))
        painter.drawRect(rect(0.56, 0.22, 0.12, 0.56))
    elif kind == "step_back":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawRect(rect(0.24, 0.22, 0.09, 0.56))
        painter.drawPolygon(poly([(0.74, 0.24), (0.74, 0.76), (0.38, 0.50)]))
    elif kind == "step_forward":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawRect(rect(0.67, 0.22, 0.09, 0.56))
        painter.drawPolygon(poly([(0.26, 0.24), (0.26, 0.76), (0.62, 0.50)]))
    elif kind in ("loop", "cycle"):
        painter.drawArc(rect(0.22, 0.22, 0.56, 0.56), 30 * 16, 300 * 16)
        filled([(0.62, 0.16), (0.76, 0.30), (0.58, 0.34)])
    elif kind == "fullscreen":
        painter.drawPolyline(poly([(0.32, 0.20), (0.20, 0.20), (0.20, 0.32)]))
        painter.drawPolyline(poly([(0.68, 0.20), (0.80, 0.20), (0.80, 0.32)]))
        painter.drawPolyline(poly([(0.32, 0.80), (0.20, 0.80), (0.20, 0.68)]))
        painter.drawPolyline(poly([(0.68, 0.80), (0.80, 0.80), (0.80, 0.68)]))
    elif kind == "fit":
        painter.drawRect(rect(0.20, 0.26, 0.60, 0.48))
        painter.drawLine(QPointF(0.50 * s, 0.26 * s), QPointF(0.50 * s, 0.74 * s))
    elif kind == "media":
        painter.drawRect(rect(0.18, 0.28, 0.64, 0.44))
        painter.drawRect(rect(0.18, 0.28, 0.08, 0.44))
    elif kind == "edit":
        painter.drawLine(QPointF(0.26 * s, 0.76 * s), QPointF(0.74 * s, 0.24 * s))
        painter.drawLine(QPointF(0.20 * s, 0.80 * s), QPointF(0.30 * s, 0.70 * s))
        painter.drawLine(QPointF(0.20 * s, 0.80 * s), QPointF(0.20 * s, 0.68 * s))
    elif kind in ("color", "palette"):
        painter.drawEllipse(rect(0.24, 0.24, 0.52, 0.52))
        painter.drawEllipse(rect(0.40, 0.40, 0.20, 0.20))
    elif kind in ("deliver", "export", "upload"):
        painter.drawLine(QPointF(0.50 * s, 0.70 * s), QPointF(0.50 * s, 0.22 * s))
        painter.drawPolyline(poly([(0.34, 0.38), (0.50, 0.22), (0.66, 0.38)]))
        painter.drawLine(QPointF(0.24 * s, 0.62 * s), QPointF(0.24 * s, 0.80 * s))
        painter.drawLine(QPointF(0.24 * s, 0.80 * s), QPointF(0.76 * s, 0.80 * s))
        painter.drawLine(QPointF(0.76 * s, 0.62 * s), QPointF(0.76 * s, 0.80 * s))
    elif kind in ("import", "download", "add"):
        painter.drawLine(QPointF(0.50 * s, 0.22 * s), QPointF(0.50 * s, 0.66 * s))
        painter.drawPolyline(poly([(0.34, 0.52), (0.50, 0.68), (0.66, 0.52)]))
        painter.drawLine(QPointF(0.24 * s, 0.62 * s), QPointF(0.24 * s, 0.80 * s))
        painter.drawLine(QPointF(0.24 * s, 0.80 * s), QPointF(0.76 * s, 0.80 * s))
        painter.drawLine(QPointF(0.76 * s, 0.62 * s), QPointF(0.76 * s, 0.80 * s))
    elif kind == "folder":
        painter.drawPolyline(poly([(0.18, 0.74), (0.18, 0.30), (0.42, 0.30),
                                   (0.48, 0.38), (0.82, 0.38), (0.82, 0.74),
                                   (0.18, 0.74)]))
    elif kind in ("pointer", "select"):
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(poly([(0.34, 0.18), (0.34, 0.76), (0.48, 0.60),
                                  (0.60, 0.82), (0.70, 0.76), (0.58, 0.56),
                                  (0.74, 0.54)]))
    elif kind == "razor":
        painter.drawLine(QPointF(0.44 * s, 0.18 * s), QPointF(0.44 * s, 0.82 * s))
        painter.drawRect(rect(0.44, 0.50, 0.34, 0.08))
    elif kind == "magnet":
        painter.drawArc(rect(0.24, 0.30, 0.52, 0.52), 0, 180 * 16)
        painter.drawLine(QPointF(0.24 * s, 0.56 * s), QPointF(0.24 * s, 0.76 * s))
        painter.drawLine(QPointF(0.76 * s, 0.56 * s), QPointF(0.76 * s, 0.76 * s))
    elif kind == "marker":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(poly([(0.30, 0.24), (0.72, 0.24), (0.54, 0.50),
                                  (0.72, 0.76), (0.30, 0.76)]))
    elif kind in ("link", "chain"):
        painter.drawEllipse(rect(0.24, 0.40, 0.34, 0.22))
        painter.drawEllipse(rect(0.44, 0.40, 0.34, 0.22))
    elif kind == "eye":
        painter.drawPolyline(poly([(0.18, 0.50), (0.34, 0.32), (0.66, 0.32),
                                   (0.82, 0.50), (0.66, 0.68), (0.34, 0.68),
                                   (0.18, 0.50)]))
        painter.drawEllipse(rect(0.42, 0.42, 0.16, 0.16))
    elif kind == "eye_off":
        painter.drawPolyline(poly([(0.18, 0.50), (0.34, 0.32), (0.66, 0.32),
                                   (0.82, 0.50)]))
        painter.drawLine(QPointF(0.24 * s, 0.76 * s), QPointF(0.76 * s, 0.24 * s))
    elif kind in ("mute", "sound"):
        painter.drawPolygon(poly([(0.28, 0.40), (0.40, 0.40), (0.54, 0.26),
                                  (0.54, 0.74), (0.40, 0.60), (0.28, 0.60)]))
        if kind == "mute":
            painter.drawLine(QPointF(0.62 * s, 0.40 * s), QPointF(0.78 * s, 0.60 * s))
            painter.drawLine(QPointF(0.78 * s, 0.40 * s), QPointF(0.62 * s, 0.60 * s))
        else:
            painter.drawArc(rect(0.56, 0.34, 0.26, 0.32), -60 * 16, 120 * 16)
    elif kind in ("lock", "unlock"):
        painter.drawRect(rect(0.32, 0.48, 0.36, 0.30))
        painter.drawArc(rect(0.38, 0.24, 0.24, 0.30), 0, 180 * 16)
    elif kind == "plus":
        painter.drawLine(QPointF(0.50 * s, 0.26 * s), QPointF(0.50 * s, 0.74 * s))
        painter.drawLine(QPointF(0.26 * s, 0.50 * s), QPointF(0.74 * s, 0.50 * s))
    elif kind == "minus":
        painter.drawLine(QPointF(0.26 * s, 0.50 * s), QPointF(0.74 * s, 0.50 * s))
    elif kind in ("undo", "redo"):
        painter.drawArc(rect(0.24, 0.30, 0.44, 0.40), 150 * 16, 240 * 16)
        if kind == "undo":
            filled([(0.20, 0.38), (0.36, 0.30), (0.34, 0.48)])
        else:
            filled([(0.74, 0.38), (0.58, 0.30), (0.60, 0.48)])
    elif kind == "gear":
        painter.drawEllipse(rect(0.30, 0.30, 0.40, 0.40))
        for angle in range(0, 360, 45):
            import math

            cx, cy = 0.50 * s, 0.50 * s
            dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            painter.drawLine(QPointF(cx + dx * 0.24 * s, cy + dy * 0.24 * s),
                             QPointF(cx + dx * 0.34 * s, cy + dy * 0.34 * s))
    elif kind in ("chevron_left", "chevron_right"):
        if kind == "chevron_left":
            painter.drawPolyline(poly([(0.58, 0.24), (0.36, 0.50), (0.58, 0.76)]))
        else:
            painter.drawPolyline(poly([(0.42, 0.24), (0.64, 0.50), (0.42, 0.76)]))
    elif kind == "close":
        painter.drawLine(QPointF(0.30 * s, 0.30 * s), QPointF(0.70 * s, 0.70 * s))
        painter.drawLine(QPointF(0.70 * s, 0.30 * s), QPointF(0.30 * s, 0.70 * s))
    elif kind == "save":
        painter.drawRect(rect(0.24, 0.24, 0.52, 0.52))
        painter.drawRect(rect(0.36, 0.24, 0.28, 0.16))
        painter.drawRect(rect(0.34, 0.52, 0.32, 0.24))
    elif kind in ("source", "program"):
        painter.drawRect(rect(0.20, 0.28, 0.60, 0.44))
        if kind == "source":
            painter.drawLine(QPointF(0.20 * s, 0.36 * s), QPointF(0.80 * s, 0.36 * s))
    else:
        painter.drawEllipse(rect(0.30, 0.30, 0.40, 0.40))


def icon(kind: str, size: int = 16, color: str = T.TEXT_DIM) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    _draw(painter, kind, float(size), color)
    painter.end()
    return QIcon(pixmap)


def glyph_pixmap(kind: str, size: int = 16, color: str = T.TEXT_DIM) -> QPixmap:
    return icon(kind, size, color).pixmap(size, size)


__all__ = ["icon", "glyph_pixmap"]
