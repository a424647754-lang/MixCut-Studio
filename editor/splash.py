"""启动画面：Logo + 版本 + 初始化状态 + 加载进度。

独立无边框窗口，纯绘制实现，不依赖位图资源。
启动完成后由 app.py 关闭并进入项目管理器。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from . import ui_theme as T

_WIDTH = 620
_HEIGHT = 360


class SplashScreen(QWidget):
    """启动画面。调用 set_status() 更新进度，finish() 关闭。"""

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(_WIDTH, _HEIGHT)
        self.setWindowTitle(T.APP_NAME)

        self._status = "正在初始化…"
        self._progress = 0
        self._done = False

        self._center_on_screen()

    # ------------------------------------------------------------------
    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self.move(
            geometry.center().x() - self.width() // 2,
            geometry.center().y() - self.height() // 2,
        )

    # ------------------------------------------------------------------
    def set_status(self, text: str, progress: int | None = None) -> None:
        """更新状态文字与进度（0-100）。"""
        self._status = text
        if progress is not None:
            self._progress = max(0, min(100, int(progress)))
        self.repaint()
        QApplication.processEvents()

    def set_progress(self, value: int) -> None:
        self._progress = max(0, min(100, int(value)))
        self.repaint()
        QApplication.processEvents()

    def finish(self, target=None, duration_ms: int = 260) -> None:
        """淡出后关闭；若给了 target（主窗口/对话框）则先显示它。"""
        if self._done:
            return
        self._done = True
        self.set_status("就绪", 100)
        if target is not None:
            target.show()
        QTimer.singleShot(duration_ms, self.close)

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(T.BG_APP))

        # 外框
        painter.setPen(QPen(QColor(T.BORDER), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        # Logo
        logo_rect = QRectF(48, 64, 96, 96)
        T.paint_logo(painter, logo_rect, T.ACCENT, T.TEXT_MUTED)

        # 主标题
        painter.setPen(QColor(T.TEXT_STRONG))
        painter.setFont(T.ui_font(26, bold=True))
        painter.drawText(
            QRectF(168, 62, _WIDTH - 200, 46),
            Qt.AlignLeft | Qt.AlignVCenter,
            T.APP_NAME,
        )

        # 副标题
        painter.setPen(QColor(T.TEXT_DIM))
        painter.setFont(T.ui_font(9))
        painter.drawText(
            QRectF(170, 104, _WIDTH - 200, 24),
            Qt.AlignLeft | Qt.AlignVCenter,
            "PROFESSIONAL NON-LINEAR EDITOR",
        )

        # 分隔线
        painter.setPen(QPen(QColor(T.BORDER), 1))
        painter.drawLine(48, 196, _WIDTH - 48, 196)

        # 版本
        painter.setPen(QColor(T.TEXT_MUTED))
        painter.setFont(T.mono_font(9))
        painter.drawText(
            QRectF(48, 210, 200, 20),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"v{T.APP_VERSION}",
        )

        # 状态文字
        painter.setPen(QColor(T.TEXT_DIM))
        painter.setFont(T.ui_font(9))
        painter.drawText(
            QRectF(48, 210, _WIDTH - 96, 20),
            Qt.AlignRight | Qt.AlignVCenter,
            self._status,
        )

        # 进度条（自绘，6px 高）
        bar_rect = QRectF(48, 244, _WIDTH - 96, 3)
        painter.fillRect(bar_rect, QColor(T.BORDER))
        filled = bar_rect.width() * self._progress / 100.0
        if filled > 0:
            painter.fillRect(QRectF(bar_rect.left(), bar_rect.top(), filled, bar_rect.height()),
                             QColor(T.ACCENT))

        # 百分比
        painter.setPen(QColor(T.TEXT_MUTED))
        painter.setFont(T.mono_font(8))
        painter.drawText(
            QRectF(48, 254, _WIDTH - 96, 18),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{self._progress}%",
        )

        # 底部版权行
        painter.setPen(QPen(QColor(T.BORDER_SUBTLE), 1))
        painter.drawLine(48, 300, _WIDTH - 48, 300)
        painter.setPen(QColor(T.TEXT_MUTED))
        painter.setFont(T.ui_font(8))
        painter.drawText(
            QRectF(48, 308, _WIDTH - 96, 20),
            Qt.AlignLeft | Qt.AlignVCenter,
            "MixCut Studio · 剪辑器",
        )
        painter.drawText(
            QRectF(48, 308, _WIDTH - 96, 20),
            Qt.AlignRight | Qt.AlignVCenter,
            "正在载入组件…",
        )
