# -*- coding: utf-8 -*-
"""主窗口外观相关的方法（从 main.py 原样搬出）。

包含无边框窗口的边缘缩放条、窗口尺寸对齐、以及旧界面的 16:9 比例锁定 /
最大化转全屏行为。

拆分方式说明
------------
采用 **Mixin** 而不是独立对象：这些方法原本都是 MainWindow 的成员，
调用方式全部是 ``self.xxx()``。做成 Mixin 后方法仍然挂在 MainWindow 上，
所有调用点零改动，信号槽与初始化顺序也完全不变。

Mixin 只通过 ``self`` 访问宿主状态（``windowHandle`` / ``use_new_ui`` /
``_ratio_guard`` 等），不导入任何业务模块，也不导入 main.py。

宿主契约
--------
需要宿主（MainWindow）在 ``__init__`` 里提供：

* ``self._ratio_guard``   —— 旧界面 16:9 比例锁定时的重入保护
* ``self.use_new_ui``     —— 是否使用新界面（新界面不锁比例、不转全屏）

由本 Mixin 创建的状态：

* ``self._resize_zones``  —— 八个边缘缩放条，仅在 ``_build_resize_zones`` 后存在
"""
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtWidgets import QWidget, QApplication


class WindowChromeMixin:
    """无边框窗口外观：边缘缩放、尺寸对齐、比例锁定。"""

    # =================================================
    # 新界面（四区布局）接入
    # =================================================

    def _build_resize_zones(self):
        """无边框窗口的边缘缩放条：四边 + 四角，拖哪边缩哪边。"""
        from functools import partial
        specs = [
            ("top", Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            ("bottom", Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            ("left", Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            ("right", Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            ("tl", Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
             Qt.CursorShape.SizeFDiagCursor),
            ("tr", Qt.Edge.TopEdge | Qt.Edge.RightEdge,
             Qt.CursorShape.SizeBDiagCursor),
            ("bl", Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
             Qt.CursorShape.SizeBDiagCursor),
            ("br", Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
             Qt.CursorShape.SizeFDiagCursor),
        ]
        self._resize_zones = {}
        for key, edge, cursor in specs:
            zone = QWidget(self)
            zone.setObjectName("ResizeZone")
            zone.setCursor(cursor)
            zone.mousePressEvent = partial(self._start_system_resize, edge)
            zone.raise_()
            self._resize_zones[key] = zone
        self._layout_resize_zones()

    def _start_system_resize(self, edge, event=None):
        handle = self.windowHandle()
        if handle is not None:
            handle.startSystemResize(edge)
        if event is not None:
            event.accept()

    def _layout_resize_zones(self):
        zones = getattr(self, "_resize_zones", None)
        if not zones:
            return
        m = 6
        w, h = self.width(), self.height()
        maximized = self.isMaximized() or self.isFullScreen()
        for zone in zones.values():
            zone.setVisible(not maximized)
        if maximized:
            return
        zones["top"].setGeometry(m, 0, max(0, w - 2 * m), m)
        zones["bottom"].setGeometry(m, h - m, max(0, w - 2 * m), m)
        zones["left"].setGeometry(0, m, m, max(0, h - 2 * m))
        zones["right"].setGeometry(w - m, m, m, max(0, h - 2 * m))
        zones["tl"].setGeometry(0, 0, m, m)
        zones["tr"].setGeometry(w - m, 0, m, m)
        zones["bl"].setGeometry(0, h - m, m, m)
        zones["br"].setGeometry(w - m, h - m, m, m)
        for zone in zones.values():
            zone.raise_()

    def _apply_demo_window_size(self):
        """对齐 demo 工程的窗口尺寸：1600x1000，最小 1200x780。

        直接照搬 demo 的尺寸；屏幕放不下时按可用区域收缩（留出边框与任务栏），
        并把窗口居中，避免一打开就挤压或跑到屏幕外。
        """
        target_w, target_h = 1600, 1000
        min_w, min_h = 1200, 780

        # 隐藏原生标题栏：和 demo 一样用无边框窗口，标题栏由 demo 顶栏承担
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowTitle("MixCut Studio")

        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(target_w, max(960, avail.width() - 20))
            target_h = min(target_h, max(540, avail.height() - 24))
            min_w = min(min_w, target_w)
            min_h = min(min_h, target_h)
            self.setMinimumSize(min_w, min_h)
            self.resize(target_w, target_h)
            self.move(avail.x() + (avail.width() - target_w) // 2,
                      avail.y() + (avail.height() - target_h) // 2)
            return

        self.setMinimumSize(min_w, min_h)
        self.resize(target_w, target_h)

    # =================================================
    # 窗口状态事件
    # =================================================

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_resize_zones()
        # 新界面是四区工作台，不强制 16:9
        if getattr(self, "use_new_ui", False):
            return
        # 最大化/全屏时不强制比例，交给系统
        if self.isMaximized() or self.isFullScreen() or self._ratio_guard:
            return
        target_h = round(self.width() * 9 / 16)
        if abs(target_h - self.height()) > 1:
            self._ratio_guard = True
            self.resize(self.width(), target_h)
            self._ratio_guard = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if getattr(self, "use_new_ui", False):
            return
        # 点最大化/双击标题栏时进入真·全屏：无边框铺满整屏，
        # 系统会自动收起底部任务栏（普通最大化做不到这一点）。
        if event.type() == QEvent.Type.WindowStateChange \
                and self.isMaximized() and not self.isFullScreen():
            QTimer.singleShot(0, self.showFullScreen)
