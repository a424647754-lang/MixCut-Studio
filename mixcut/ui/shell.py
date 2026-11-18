# -*- coding: utf-8 -*-
"""
MixCut · 主窗口框架（第二阶段）

结构（DaVinci 风格）：
  ┌──────────────────────────────────────────────────────┐
  │ 顶栏：Logo / 项目名 / ... / 工作区 / 设置 / 窗口控制    │
  ├──────┬─────────────────────────────┬───────────────┤
  │ 导航  │ 中央工作区（QSplitter 可调）  │ 属性栏(可折叠)  │
  │ 210px│  QStackedWidget              │  260px        │
  │      ├─────────────────────────────┤               │
  │      │ 底部任务中心（任务数/状态/日志）│               │
  └──────┴─────────────────────────────┴───────────────┘

所有分隔条可拖拽，尺寸可记忆。不保存业务数据。
"""

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QStackedWidget, QWidget, QPushButton,
    QSplitter, QFrame, QToolButton, QSizePolicy,
)

from . import theme
from .nav import NavBar
from .topbar import TopBar


class PlaceholderPage(QWidget):
    """尚未实现的功能页：占位，不伪造业务内容。"""

    def __init__(self, title, note="该模块正在规划中，当前版本暂未开放。", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("Hero")
        lay.addWidget(t)
        hint = QLabel(note)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)


class StatusBar(QFrame):
    """底部任务中心：任务数 + 状态 + 日志入口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BottomBar")
        self.setFixedHeight(28)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(16)

        self.task_label = QLabel("任务中心  ·  0")
        self.task_label.setObjectName("Faint")
        lay.addWidget(self.task_label)

        lay.addStretch(1)

        self.state_label = QLabel("就绪")
        self.state_label.setObjectName("Muted")
        lay.addWidget(self.state_label)

        self.log_btn = QPushButton("日志")
        self.log_btn.setObjectName("Ghost")
        self.log_btn.setCursor(Qt.PointingHandCursor)
        self.log_btn.setFixedHeight(20)
        self.log_btn.setStyleSheet("padding:2px 10px;font-size:11px;")
        lay.addWidget(self.log_btn)

    def set_task_count(self, n):
        self.task_label.setText(f"任务中心  ·  {n}")

    def set_state(self, text):
        self.state_label.setText(text.upper())


class InspectorPanel(QFrame):
    """右侧属性栏：显示选中对象详情，可折叠。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Inspector")
        self.setMinimumWidth(200)
        self.setMaximumWidth(360)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("属性")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.toggle_btn = QToolButton()
        self.toggle_btn.setText("»")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setStyleSheet(
            "QToolButton{background:transparent;border:none;color:%s;}"
            % theme.COLORS["muted"])
        header.addWidget(self.toggle_btn)
        lay.addLayout(header)

        self.body = QLabel("未选中对象")
        self.body.setObjectName("Faint")
        self.body.setWordWrap(True)
        lay.addWidget(self.body)
        lay.addStretch(1)

    def set_info(self, text):
        self.body.setText(text or "未选中对象")


class ShellWindow(QWidget):
    """工作站骨架。业务通过 set_* / signal 注入。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle("MixCut Studio")
        self.resize(1280, 720)
        self.setMinimumSize(960, 540)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 水平 QSplitter：导航 | 中央 | 属性栏
        self.h_split = QSplitter(Qt.Horizontal)
        self.h_split.setHandleWidth(1)
        self.h_split.setStyleSheet(
            f"QSplitter::handle{{background:{theme.COLORS['border_soft']};}}")

        # 左侧导航
        self.nav = NavBar()
        self.h_split.addWidget(self.nav)

        # 中央列：顶栏 +（工作区/底栏）垂直拆分
        center = QWidget()
        center_lay = QVBoxLayout(center)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(0)

        self.topbar = TopBar()
        center_lay.addWidget(self.topbar)

        self.v_split = QSplitter(Qt.Vertical)
        self.v_split.setHandleWidth(0)
        self.v_split.setStyleSheet(
            f"QSplitter::handle{{background:{theme.COLORS['border_soft']};}}")

        # 中央工作区
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background:{theme.COLORS['bg']};")
        self._pages = {}
        self.v_split.addWidget(self.stack)

        center_lay.addWidget(self.v_split, 1)
        self.h_split.addWidget(center)

        # 右侧属性栏 / 底部任务条保留对象以兼容旧调用，但 demo 风格不再占用界面。
        self.inspector = InspectorPanel()
        self.inspector.hide()
        self.bottombar = StatusBar()
        self.bottombar.hide()

        # 初始比例：导航 200 / 中央占满
        self.h_split.setSizes([200, 1000])

        # 属性栏折叠（旧接口保留；当前不显示属性栏）
        self._inspector_collapsed = False
        self.inspector.toggle_btn.clicked.connect(self.toggle_inspector)

        root.addWidget(self.h_split)

        self.nav.page_changed.connect(self.show_page)
        self.topbar.refresh_requested.connect(self._on_refresh)

    def toggle_inspector(self):
        # 旧属性栏已移出布局，保留空实现避免旧调用报错。
        return

    # 由 MainWindow 注入真实处理函数
    def bind_actions(self, refresh=None, settings=None, save=None, open=None):
        self._refresh_action = refresh
        self._settings_action = settings
        self._save_action = save
        self._open_action = open
        if settings is None:
            self.topbar.settings_btn.clicked.connect(
                lambda: self.nav.select("settings"))
        else:
            self.topbar.settings_btn.clicked.connect(
                lambda: self._call(self._settings_action))
        if save is not None:
            self.topbar.save_requested.connect(lambda: self._call(save))
        if open is not None:
            self.topbar.open_requested.connect(lambda: self._call(open))

    @staticmethod
    def _call(fn):
        if callable(fn):
            return fn()

    def _on_refresh(self):
        fn = getattr(self, "_refresh_action", None)
        if callable(fn):
            fn()

    # ---- 页面注册与切换 ----
    def register_page(self, key, widget):
        self._pages[key] = widget
        self.stack.addWidget(widget)

    def show_page(self, key):
        if key not in self._pages:
            return
        self.stack.setCurrentWidget(self._pages[key])
        page = self._pages[key]
        hook = getattr(page, "on_shown", None)
        if callable(hook):
            hook()

        # demo 外壳不显示右侧属性栏，中央区域始终占满。
        return

    # ---- 对外状态接口 ----
    def set_task_count(self, n):
        self.bottombar.set_task_count(n)

    def set_state(self, text):
        self.bottombar.set_state(text)
