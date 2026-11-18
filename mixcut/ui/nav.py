# -*- coding: utf-8 -*-
"""
MixCut · 左侧导航栏

按 UI 重构需求只放真实存在的页面：
  素材库 / 超级混剪 / 分析 / 任务中心 / MIX引擎 / 设置
不再放“历史记录”“输出”等当前不用的入口。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
)

from . import theme
from .components import Dot

# 顺序即左侧导航的显示顺序（用户指定）：
# 素材库 → 超级混剪 → 分析 → 任务中心 → MIX引擎 → 设置
NAV_ITEMS = [
    ("workbench", "素材库", "▦"),
    ("mix", "超级混剪", "▶"),
    ("analyze", "分析", "◎"),
    ("tasks", "任务中心", "☰"),
    ("engine", "MIX引擎", "◆"),
    ("settings", "设置", "⚙"),
]


class NavBar(QFrame):
    """固定左侧导航；可折叠成全图标宽度。"""

    page_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._collapsed = False
        self._buttons = {}
        self._current = None

        self.setFixedWidth(theme.NAV_WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 16, 12, 12)
        lay.setSpacing(4)

        # 品牌
        brand_row = QHBoxLayout()
        brand_row.setSpacing(8)
        self.logo = QLabel("M")
        self.logo.setFixedSize(28, 28)
        self.logo.setAlignment(Qt.AlignCenter)
        self.logo.setStyleSheet(
            f"background:{theme.COLORS['text']};color:{theme.COLORS['bg_deep']};"
            f"border-radius:8px;font-weight:700;font-size:15px;")
        brand_row.addWidget(self.logo)
        self.brand = QLabel("MixCut Studio")
        self.brand.setStyleSheet("font-size:16px;font-weight:600;")
        brand_row.addWidget(self.brand)
        brand_row.addStretch(1)
        self.collapse_btn = QToolButton()
        self.collapse_btn.setText("«")
        self.collapse_btn.setToolTip("折叠导航栏")
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.setStyleSheet(
            "QToolButton{background:transparent;border:none;color:%s;font-size:14px;}"
            % theme.COLORS["muted"])
        self.collapse_btn.clicked.connect(self.toggle_collapsed)
        brand_row.addWidget(self.collapse_btn)
        lay.addLayout(brand_row)
        lay.addSpacing(14)

        # 导航项
        for key, label, icon in NAV_ITEMS:
            btn = QPushButton(icon if self._collapsed else f"{icon}   {label}")
            btn.setObjectName("NavItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(label)
            btn.setFixedHeight(36)
            btn.clicked.connect(lambda _c=False, k=key: self.select(k))
            self._buttons[key] = btn
            lay.addWidget(btn)

        lay.addStretch(1)

        # 底部：版本与运行状态
        self.status_row = QHBoxLayout()
        self.status_row.setSpacing(6)
        self.state_dot = Dot("success")
        self.status_row.addWidget(self.state_dot)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("Faint")
        self.status_row.addWidget(self.status_label)
        self.status_row.addStretch(1)
        lay.addLayout(self.status_row)

        self.version_label = QLabel(f"MixCut Studio · v{_app_version()}")
        self.version_label.setObjectName("Faint")
        lay.addWidget(self.version_label)

    # ---- 导航 ----
    def select(self, key):
        if key not in self._buttons:
            return
        self._current = key
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)
        self.page_changed.emit(key)

    def current(self):
        return self._current

    # ---- 折叠 ----
    def toggle_collapsed(self):
        self._collapsed = not self._collapsed
        self.setFixedWidth(theme.NAV_WIDTH_COLLAPSED if self._collapsed
                           else theme.NAV_WIDTH)
        self.brand.setVisible(not self._collapsed)
        self.version_label.setVisible(not self._collapsed)
        self.status_label.setVisible(not self._collapsed)
        for key, btn in self._buttons.items():
            item = next((x for x in NAV_ITEMS if x[0] == key), None)
            if item:
                btn.setText(item[2] if self._collapsed else f"{item[2]}   {item[1]}")
                btn.setToolTip(item[1])
        self.collapse_btn.setText("»" if self._collapsed else "«")

    # ---- 状态 ----
    def set_status(self, text, level="success"):
        self.status_label.setText(text)
        self.state_dot.set_level(level)


def _app_version():
    """版本号来自包信息，读不到时显示 0.0.0（不写死假版本）。"""
    try:
        from importlib.metadata import version, PackageNotFoundError
        for name in ("mixcut", "MixCutStudio", "mixcut-studio"):
            try:
                return version(name)
            except PackageNotFoundError:
                continue
    except Exception:
        pass
    return "0.0.0"
