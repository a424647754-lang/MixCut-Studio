# -*- coding: utf-8 -*-
"""数据可视化与流程控件：细进度、镜头带、评分条、相似对比、流水线节点、预览播放器。"""

import math

from PySide6.QtCore import (
    Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme as T
from .ui_kit import draw_icon, paint_card, thumb_pixmap


class ThinBar(QWidget):
    def __init__(self, value: float = 0.0, color: str = "#DDE0E6", height: int = 4, parent=None):
        super().__init__(parent)
        self._target = value
        self._value = value
        self._color = color
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(520)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._value = float(v)
        self.update()

    def set_value(self, v: float, animate: bool = True):
        v = max(0.0, min(1.0, v))
        self._target = v
        if not animate:
            self._value = v
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(v)
        self._anim.start()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.c("#30333B"))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if self._value > 0:
            fg = QRectF(r.left(), r.top(), max(r.height(), r.width() * self._value), r.height())
            p.setBrush(T.c(self._color))
            p.drawRoundedRect(fg, r.height() / 2, r.height() / 2)
        p.end()


class MetricRow(QWidget):
    def __init__(self, label: str, value: float, suffix: str = "", fmt: str = "{:.2f}",
                 color: str = "#DDE0E6", parent=None):
        super().__init__(parent)
        self._label = label
        self._suffix = suffix
        self._fmt = fmt
        self.setFixedHeight(38)
        self.bar = ThinBar(value, color, 4)
        self.value_label = QLabel(fmt.format(value) + suffix)
        self.value_label.setFont(T.font(12, QFont.Weight.DemiBold, mono=True))
        self.value_label.setStyleSheet("color:%s;" % T.TEXT_2)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setFixedWidth(52)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setFont(T.font(12))
        name.setStyleSheet("color:%s;" % T.TEXT_2)
        top.addWidget(name, 1)
        top.addWidget(self.value_label)
        lay.addLayout(top)
        lay.addWidget(self.bar)

    def set_value(self, v: float, animate: bool = True):
        self.bar.set_value(v, animate)
        self.value_label.setText(self._fmt.format(v) + self._suffix)


class ShotTimeline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments = []
        self._lit = 0
        self._playhead = 0.0
        self.setFixedHeight(64)

    def set_lit(self, n: int):
        self._lit = max(0, min(len(self.segments), n))
        self.update()

    def set_playhead(self, v: float):
        self._playhead = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0, 8, 0, -8)
        gap = 4
        total = sum(self.segments)
        if not self.segments or total <= 0:
            p.setPen(QPen(T.c("#3A3D45"), 1))
            p.drawLine(rect.topLeft(), rect.topRight())
            p.end()
            return
        x = rect.left()
        for i, seg in enumerate(self.segments):
            w = (rect.width() - gap * (len(self.segments) - 1)) * seg / total
            r = QRectF(x, rect.top(), w, rect.height())
            if i < self._lit:
                p.setBrush(T.c("#E6E8EE"))
                p.setPen(Qt.PenStyle.NoPen)
            else:
                p.setBrush(T.c("#2E3138"))
                p.setPen(QPen(T.c("#3A3D45"), 1))
            p.drawRoundedRect(r, 3, 3)
            x += w + gap
        if self._playhead > 0:
            px = rect.left() + rect.width() * self._playhead
            p.setPen(QPen(T.c("#A78BFA"), 1.4))
            p.drawLine(QPointF(px, rect.top() - 4), QPointF(px, rect.bottom() + 4))
        p.end()


class ScoreRow(QWidget):
    def __init__(self, name: str, score: float, parent=None):
        super().__init__(parent)
        self._name = name
        self._score = score
        self.base = score
        self.setFixedHeight(34)
        self.bar = ThinBar(score, "#CFD3DB", 3)

    def paintEvent(self, _e):
        super().paintEvent(_e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(T.font(12))
        p.setPen(T.c(T.TEXT_2))
        p.drawText(QRectF(0, 0, 74, self.height()), Qt.AlignmentFlag.AlignVCenter, self._name)
        self.bar.setGeometry(82, self.height() // 2 - 2, max(20, self.width() - 140), 4)
        p.setFont(T.font(11, QFont.Weight.DemiBold, mono=True))
        p.setPen(T.c(T.TEXT))
        p.drawText(QRectF(self.width() - 50, 0, 50, self.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "%d%%" % int(self._score * 100))
        p.end()

    def set_score(self, s: float):
        self._score = s
        self.bar.set_value(s)
        self.update()


class SimilarRow(QWidget):
    def __init__(self, a: str, b: str, score: float, parent=None):
        super().__init__(parent)
        self._a, self._b, self._score = a, b, score
        self.setFixedHeight(52)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h = 36
        for i, name in enumerate((self._a, self._b)):
            x = i * 70
            pm = thumb_pixmap(name, 64, h)
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, 8, 64, h), 6, 6)
            p.setClipPath(path)
            p.drawPixmap(x, 8, pm)
            p.setClipping(False)
            p.setPen(QPen(T.c("#3A3D45"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(x + 0.5, 8.5, 63, h - 1), 6, 6)
        p.setFont(T.font(11, mono=True))
        p.setPen(T.c(T.TEXT_3))
        p.drawText(QRectF(146, 8, 80, 18), Qt.AlignmentFlag.AlignVCenter, "相似度")
        p.setFont(T.font(13, QFont.Weight.DemiBold, mono=True))
        p.setPen(T.c(T.TEXT))
        p.drawText(QRectF(146, 26, 70, 20), Qt.AlignmentFlag.AlignVCenter,
                   "%.2f" % self._score if self._score > 0 else "—")
        p.setPen(Qt.PenStyle.NoPen)
        dup = self._score >= 0.7
        has = self._score > 0
        col = T.WARN if dup else (T.OK if has else T.TEXT_3)
        p.setBrush(T.c(col, 28))
        p.drawRoundedRect(QRectF(max(220, self.width() - 76), 17, 66, 22), 6, 6)
        p.setFont(T.font(11))
        p.setPen(T.c(col))
        p.drawText(QRectF(max(220, self.width() - 76), 17, 66, 22), Qt.AlignmentFlag.AlignCenter,
                   "疑似重复" if dup else ("正常" if has else "无数据"))
        p.end()


class PipelineStrip(QWidget):
    """六阶段流水线：节点 + 连接线 + 进度。"""
    stageClicked = __import__("PySide6.QtCore", fromlist=["Signal"]).Signal(int)

    def __init__(self, stages, parent=None):
        super().__init__(parent)
        self.stages = stages
        self._progress = 0.0
        self._active = -1
        self._done = 0
        self.setFixedHeight(104)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(600)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_progress(self):
        return self._progress

    def set_progress(self, v):
        self._progress = max(0.0, min(1.0, float(v)))
        self.update()

    progress = Property(float, get_progress, set_progress)

    def set_state(self, progress: float, active: int, done: int, animate: bool = True):
        self._active = active
        self._done = done
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._progress)
            self._anim.setEndValue(progress)
            self._anim.start()
        else:
            self.set_progress(progress)
        self.update()

    def mousePressEvent(self, e):
        idx = self._node_at(e.position().x())
        if idx >= 0:
            self.stageClicked.emit(idx)
        super().mousePressEvent(e)

    def _node_at(self, x):
        n = len(self.stages)
        step = self.width() / n
        return int(x // step) if 0 <= x < self.width() else -1

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        n = len(self.stages)
        step = self.width() / n
        cy = 44
        line_y = cy
        # 连接线
        p.setPen(QPen(T.c("#2E3138"), 2))
        p.drawLine(QPointF(step / 2, line_y), QPointF(self.width() - step / 2, line_y))
        if self._progress > 0:
            p.setPen(QPen(T.c("#E7E9EE"), 2))
            p.drawLine(QPointF(step / 2, line_y),
                       QPointF(step / 2 + (self.width() - step) * self._progress, line_y))
        for i, (key, label, icon) in enumerate(self.stages):
            cx = step * i + step / 2
            state = "done" if i < self._done else ("active" if i == self._active else "idle")
            if state == "active":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(T.c(T.ACCENT, 34))
                p.drawEllipse(QPointF(cx, cy), 21, 21)
                p.setBrush(T.c("#0F1014"))
                p.setPen(QPen(T.c("#EDE9FF"), 1.4))
            elif state == "done":
                p.setBrush(T.c("#1E2026"))
                p.setPen(QPen(T.c("#4A4E58"), 1))
            else:
                p.setBrush(T.c("#1E2026"))
                p.setPen(QPen(T.c("#33363E"), 1))
            p.drawEllipse(QPointF(cx, cy), 15, 15)
            col = QColor("#EDE9FF") if state == "active" else (QColor(T.TEXT_2) if state == "done" else QColor("#5E626C"))
            draw_icon(p, icon, QRectF(cx - 7, cy - 7, 14, 14), col, 1.4)
            p.setFont(T.font(11, QFont.Weight.Medium))
            p.setPen(QColor(T.TEXT) if state != "idle" else QColor("#666A74"))
            p.drawText(QRectF(cx - step / 2, cy + 22, step, 18), Qt.AlignmentFlag.AlignCenter, label)
            p.setFont(T.font(10, mono=True))
            p.setPen(T.c(T.TEXT_3))
            p.drawText(QRectF(cx - step / 2, cy + 40, step, 14), Qt.AlignmentFlag.AlignCenter,
                       ["待启动", "检测中", "编码中", "过滤中", "编排中", "生成中"][i] if i == self._active
                       else ("完成" if i < self._done else "—"))
        p.end()


class PreviewSurface(QWidget):
    """大预览：干净画面 + 圆角 + 细描边，不叠加扫描/识别框等特效。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.name = "01_海边黄昏.jpg"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(320)

    def set_clip(self, name: str):
        self.name = name
        self.update()

    def set_scan(self, v: float):
        pass

    def set_boxes(self, v: float):
        pass

    def set_glow(self, v: float):
        pass

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.setClipPath(path)
        p.fillRect(r, T.c("#0B0C0F"))
        w = min(r.width(), r.height() * 16 / 9)
        h = w * 9 / 16
        fr = QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h)
        pm = thumb_pixmap(self.name, int(fr.width()), int(fr.height()))
        p.drawPixmap(int(fr.left()), int(fr.top()), pm)
        p.setClipping(False)
        p.setPen(QPen(T.c("#33363E"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        p.end()


class ComposeBar(QWidget):
    """成片结构：13 个片段的编排条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segs = [3.0, 2.0, 2.4, 3.6, 1.8, 2.2, 2.8, 3.2, 2.0, 2.6, 3.0, 2.2, 1.3]
        self.setFixedHeight(88)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        total = sum(self.segs)
        gap = 4
        per_row = 7
        rows = 2
        usable_w = self.width() - gap * (per_row - 1)
        for i, seg in enumerate(self.segs):
            row = i // per_row
            idx = i % per_row
            count = per_row if row == 0 else len(self.segs) - per_row
            row_w = usable_w - gap * (per_row - count)
            x = 0
            for k in range(idx):
                x += (row_w - gap * (count - 1)) * self.segs[row * per_row + k] / sum(self.segs[row * per_row: row * per_row + count]) + gap
            w = (row_w - gap * (count - 1)) * seg / sum(self.segs[row * per_row: row * per_row + count])
            y = row * 46
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(T.c("#C9CDD6"))
            p.drawRoundedRect(QRectF(x, y, w, 26), 4, 4)
            p.setFont(T.font(10, QFont.Weight.DemiBold, mono=True))
            p.setPen(T.c("#2A2D33"))
            p.drawText(QRectF(x, y, w, 26), Qt.AlignmentFlag.AlignCenter, "%02d" % (i + 1))
        p.end()

class WaveBars(QWidget):
    """微数据波形，仅作局部装饰。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bars = [0.3, 0.55, 0.4, 0.75, 0.62, 0.9, 0.58, 0.42, 0.66, 0.5, 0.36, 0.7, 0.48, 0.8, 0.44, 0.6]
        self._phase = 0
        self.setFixedHeight(46)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        n = len(self.bars)
        gap = 3
        bw = max(2, (self.width() - gap * (n - 1)) / n)
        for i, v in enumerate(self.bars):
            h = 6 + (self.height() - 10) * v
            x = i * (bw + gap)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(T.c("#5B5F6A"))
            p.drawRoundedRect(QRectF(x, self.height() - h, bw, h), 1.5, 1.5)
        p.end()
