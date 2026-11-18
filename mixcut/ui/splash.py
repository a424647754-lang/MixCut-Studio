# -*- coding: utf-8 -*-
"""MixCut 启动界面：品牌背景图 + 底部真实加载进度条。"""

import os

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")

# 打包版启动图池：三张照片随机播放（源码运行不受影响，仍用品牌图）
SPLASH_IMAGES = ("splash_build.png", "splash_a.jpg", "splash_b.jpg")


def _splash_bg_path():
    """打包版从三张照片里随机挑一张；源码运行固定用品牌图，不动源文件里的启动图。"""
    import random
    import sys

    if getattr(sys, "frozen", False):
        candidates = [os.path.join(ASSETS_DIR, name) for name in SPLASH_IMAGES]
        available = [path for path in candidates if os.path.exists(path)]
        if available:
            return random.choice(available)
        return ""
    path = os.path.join(ASSETS_DIR, "splash_main.png")
    return path if os.path.exists(path) else ""


class _SplashBar(QWidget):
    """底部真实加载进度条：圆角轨道 + 渐变填充，数值变化带短动画。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._value = 0.0
        self._shown = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, value):
        self._shown = float(value)
        self.update()

    def setValue(self, value):
        value = max(0.0, min(100.0, float(value)))
        self._value = value
        self._anim.stop()
        self._anim.setStartValue(self._shown)
        self._anim.setEndValue(value)
        self._anim.start()

    def value(self):
        return self._value

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        radius = r.height() / 2.0

        # 轨道
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawRoundedRect(r, radius, radius)

        # 填充
        w = r.width() * (self._shown / 100.0)
        if w > 0.5:
            fill = QRectF(r.left(), r.top(), max(w, r.height()), r.height())
            grad = QLinearGradient(fill.left(), 0, fill.right(), 0)
            grad.setColorAt(0.0, QColor("#7C6BFF"))
            grad.setColorAt(0.65, QColor("#5C88FF"))
            grad.setColorAt(1.0, QColor("#7CD8FF"))
            p.setBrush(grad)
            p.drawRoundedRect(fill, radius, radius)
        p.end()


class SplashScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.SplashScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(960, 540)

        bg_path = _splash_bg_path()
        self._bg = QPixmap(bg_path) if bg_path else None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addStretch(1)

        bottom = QWidget(self)
        bottom.setStyleSheet("background:transparent;")
        blay = QVBoxLayout(bottom)
        blay.setContentsMargins(56, 0, 56, 26)
        blay.setSpacing(9)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.status_label = QLabel("正在启动…")
        self.status_label.setStyleSheet(
            "color:#B9C1D0;font-size:12px;letter-spacing:2px;background:transparent;")
        self.pct_label = QLabel("0%")
        self.pct_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.pct_label.setStyleSheet(
            "color:#E6EBF5;font-size:12px;background:transparent;")
        row.addWidget(self.status_label)
        row.addStretch(1)
        row.addWidget(self.pct_label)
        blay.addLayout(row)

        self.bar = _SplashBar(bottom)
        blay.addWidget(self.bar)
        root.addWidget(bottom)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        p.setClipPath(path)
        if self._bg is not None and not self._bg.isNull():
            scaled = self._bg.scaled(self.size(),
                                     Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(0, 0, scaled)
        else:
            p.fillRect(self.rect(), QColor("#0B0C10"))

        # 底部压暗，保证进度条与文字在任何背景上都能看清
        grad = QLinearGradient(0, self.height() - 150, 0, self.height())
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 150))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRect(QRectF(0, self.height() - 150, self.width(), 150))

        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()

    def set_status(self, text, percent):
        self.status_label.setText(str(text))
        pct = int(round(float(percent)))
        pct = max(0, min(100, pct))
        self.pct_label.setText("%d%%" % pct)
        self.bar.setValue(pct)

    def finish(self):
        self.close()
