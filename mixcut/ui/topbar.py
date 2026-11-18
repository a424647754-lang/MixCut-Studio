# -*- coding: utf-8 -*-
"""
MixCut · 顶部工具栏

只显示真实数据：工作区名、项目名、素材总数、已选素材数。
“刷新”会重新统计素材树；“设置”跳到设置页。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import theme


class TopBar(QFrame):
    refresh_requested = Signal()
    settings_requested = Signal()
    save_requested = Signal()
    open_requested = Signal()
    project_renamed = Signal(str)

    def __init__(self, workspace="素材与混剪", project="未命名项目",
                 parent=None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(theme.TOPBAR_HEIGHT)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 8, 22, 8)
        lay.setSpacing(12)

        # demo 风格：左侧面包屑
        brand = QLabel("工作台")
        brand.setStyleSheet("font-size:13px;font-weight:600;")
        lay.addWidget(brand)
        sep = QLabel("/")
        sep.setObjectName("Faint")
        lay.addWidget(sep)
        sub = QLabel("草稿 01")
        sub.setObjectName("Faint")
        lay.addWidget(sub)

        lay.addStretch(1)

        # 素材统计
        self.material_label = QLabel("素材 0")
        self.material_label.setObjectName("Muted")
        lay.addWidget(self.material_label)
        self.selected_label = QLabel("已选 0")
        self.selected_label.setObjectName("Muted")
        lay.addWidget(self.selected_label)

        self.refresh_btn = QPushButton("刷新素材")
        self.refresh_btn.setObjectName("Ghost")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        lay.addWidget(self.refresh_btn)

        self.open_btn = QPushButton("打开工程")
        self.open_btn.setObjectName("Ghost")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self.open_requested.emit)
        lay.addWidget(self.open_btn)

        self.save_btn = QPushButton("保存工程")
        self.save_btn.setObjectName("Ghost")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self.save_requested.emit)
        lay.addWidget(self.save_btn)

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("Ghost")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        lay.addWidget(self.settings_btn)

    def set_project(self, name):
        # 顶栏标题已按要求移除，保留接口兼容外部调用
        return

    def set_workspace(self, name):
        return

    def set_counts(self, total, selected):
        self.material_label.setText(f"素材 {total}")
        self.selected_label.setText(f"已选 {selected}")
