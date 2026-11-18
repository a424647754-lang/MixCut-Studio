# -*- coding: utf-8 -*-
"""输出页：导出工程列表与打开目录。"""

import os
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QFrame,
)

from . import theme
from .components import Card


class OutputPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("输出")
        title.setObjectName("Hero")
        head.addWidget(title)
        head.addStretch(1)
        self.open_btn = QPushButton("打开输出文件夹")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.COLORS['panel_alt']}; color:{theme.COLORS['text']};"
            f"border:1px solid {theme.COLORS['border']}; padding:6px 14px; border-radius:3px; }}"
            f"QPushButton:hover {{ background:{theme.COLORS['hover']}; }}")
        self.open_btn.clicked.connect(self._open_folder)
        head.addWidget(self.open_btn)
        lay.addLayout(head)

        list_card = Card(padding=10)
        self.list = QListWidget()
        self.list.setStyleSheet("background:transparent;border:none;")
        list_card.layout.addWidget(self.list, 1)
        lay.addWidget(list_card, 1)

        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _open_folder(self):
        paths = getattr(self.win, "_last_results", []) or []
        if paths:
            p = paths[0].get("draft_path", "")
            folder = os.path.dirname(p) if p else ""
            if folder and os.path.isdir(folder):
                os.startfile(folder)

    def refresh(self):
        results = getattr(self.win, "_last_results", None) or []
        self.list.clear()
        if not results:
            item = QListWidgetItem("暂无导出记录")
            item.setForeground(QColor(theme.COLORS['faint']))
            self.list.addItem(item)
            return
        for r in results:
            name = r.get("draft_name", "未命名") if isinstance(r, dict) else str(r)
            path = r.get("draft_path", "") if isinstance(r, dict) else ""
            item = QListWidgetItem(f"{name}　·　{path}")
            self.list.addItem(item)
