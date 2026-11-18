# -*- coding: utf-8 -*-
"""把 demo 的页面布局嵌进旧工程；后台仍由旧 MainWindow 原生对象处理。"""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget,
)

from .demo import prefs
from .demo.window import Sidebar, TopBar


class DemoShell(QWidget):
    def __init__(self, win, pages, parent=None):
        super().__init__(parent)
        self.win = win
        self.pages = dict(pages)
        self.current = ""
        self._fade = None
        self.setObjectName("Root")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar(win)
        self.nav = self.sidebar
        root.addWidget(self.sidebar)

        center = QWidget()
        center.setObjectName("Content")
        lay = QVBoxLayout(center)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.topbar = TopBar(win)
        lay.addWidget(self.topbar)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        for page in self.pages.values():
            if page is not None:
                self.stack.addWidget(page)

        root.addWidget(center, 1)

    def show_page(self, key, animate=True):
        page = self.pages.get(key)
        if page is None:
            return
        self.current = key
        self.stack.setCurrentWidget(page)
        for k, item in getattr(self.sidebar, "items", {}).items():
            item.set_active(k == key)
        hook = getattr(page, "on_shown", None)
        if callable(hook):
            hook()
        if animate and prefs.motion("page"):
            self._fade_in(page)

    def _fade_in(self, page):
        """页面切换淡入 180ms（设置里关掉「页面切换」就直切）。"""
        try:
            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(180)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def _done():
                page.setGraphicsEffect(None)

            anim.finished.connect(_done)
            self._fade = anim
            anim.start()
        except Exception:
            page.setGraphicsEffect(None)

    def register_page(self, key, page):
        self.pages[key] = page
        self.stack.addWidget(page)
