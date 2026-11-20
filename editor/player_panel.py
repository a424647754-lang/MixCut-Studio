"""播放器面板：Program / Source 视图（第一阶段实现 Program 视图框架）。

设计要点：
- 只负责画面显示与传输控制，不直接操作工程数据；
- 通过信号把用户意图交给 MainWindow，便于第四阶段接入时间线联动；
- 图标全部程序化绘制，无位图依赖，保证任意 DPI 下清晰。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import ui_theme as T


# ---------------------------------------------------------------------------
# 程序化图标
# ---------------------------------------------------------------------------
def glyph_icon(kind: str, size: int = 16, color: str = T.TEXT_DIM) -> QIcon:
    """绘制线性图标：play / pause / step_back / step_forward / fullscreen / fit。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color), max(1.0, size / 12.0))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    s = float(size)
    if kind == "play":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(QPolygonF([
            QPointF(s * 0.30, s * 0.20),
            QPointF(s * 0.30, s * 0.80),
            QPointF(s * 0.80, s * 0.50),
        ]))
    elif kind == "pause":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawRect(QRectF(s * 0.32, s * 0.22, s * 0.12, s * 0.56))
        painter.drawRect(QRectF(s * 0.56, s * 0.22, s * 0.12, s * 0.56))
    elif kind == "step_back":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawRect(QRectF(s * 0.26, s * 0.22, s * 0.10, s * 0.56))
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([
            QPointF(s * 0.72, s * 0.22),
            QPointF(s * 0.72, s * 0.78),
            QPointF(s * 0.38, s * 0.50),
        ]))
    elif kind == "step_forward":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawRect(QRectF(s * 0.64, s * 0.22, s * 0.10, s * 0.56))
        painter.drawPolygon(QPolygonF([
            QPointF(s * 0.28, s * 0.22),
            QPointF(s * 0.28, s * 0.78),
            QPointF(s * 0.62, s * 0.50),
        ]))
    elif kind == "fullscreen":
        painter.drawLine(QPointF(s * 0.22, s * 0.38), QPointF(s * 0.22, s * 0.22))
        painter.drawLine(QPointF(s * 0.22, s * 0.22), QPointF(s * 0.38, s * 0.22))
        painter.drawLine(QPointF(s * 0.78, s * 0.38), QPointF(s * 0.78, s * 0.22))
        painter.drawLine(QPointF(s * 0.78, s * 0.22), QPointF(s * 0.62, s * 0.22))
        painter.drawLine(QPointF(s * 0.22, s * 0.62), QPointF(s * 0.22, s * 0.78))
        painter.drawLine(QPointF(s * 0.22, s * 0.78), QPointF(s * 0.38, s * 0.78))
        painter.drawLine(QPointF(s * 0.78, s * 0.62), QPointF(s * 0.78, s * 0.78))
        painter.drawLine(QPointF(s * 0.78, s * 0.78), QPointF(s * 0.62, s * 0.78))
    elif kind == "fit":
        painter.drawRect(QRectF(s * 0.20, s * 0.24, s * 0.60, s * 0.52))
        painter.drawRect(QRectF(s * 0.34, s * 0.36, s * 0.32, s * 0.28))
    else:
        painter.drawEllipse(QRectF(s * 0.3, s * 0.3, s * 0.4, s * 0.4))

    painter.end()
    return QIcon(pixmap)


# ---------------------------------------------------------------------------
# 面板标题栏
# ---------------------------------------------------------------------------
class PanelHeader(QWidget):
    """统一的面板标题：左侧 2px 强调线 + 大写标题 + 右侧可挂控件。"""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PanelHeader")
        self.setFixedHeight(T.PANEL_HEADER_HEIGHT)
        # QWidget 子类必须显式声明 styled background，否则 QSS 背景不生效
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#PanelHeader {{ background: {T.BG_PANEL_ALT};"
            f" border-bottom: {T.BORDER_W}px solid {T.BORDER}; }}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(8)

        accent = QWidget()
        accent.setFixedWidth(2)
        accent.setStyleSheet(f"background:{T.ACCENT};")
        accent.setFixedHeight(14)
        layout.addWidget(accent)

        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("PanelTitle")
        layout.addWidget(self.title_label)

        layout.addStretch(1)
        self._layout = layout

    def add_right(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)


# ---------------------------------------------------------------------------
# 传输条
# ---------------------------------------------------------------------------
class TransportBar(QWidget):
    """播放控制：逐帧、播放/暂停、时间码、音量、适配、全屏、性能监测。"""

    play_toggled = Signal(bool)     # True = 请求播放
    step_requested = Signal(int)    # -1 上一帧 / +1 下一帧
    volume_changed = Signal(int)    # 0-100
    fullscreen_requested = Signal()
    fit_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TransportBar")
        self.setFixedHeight(T.TRANSPORT_HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(4)

        self.btn_step_back = self._tool_button("step_back", "上一帧（←）")
        self.btn_step_back.clicked.connect(lambda: self.step_requested.emit(-1))
        layout.addWidget(self.btn_step_back)

        self.btn_play = self._tool_button("play", "播放 / 暂停（空格）")
        self.btn_play.setCheckable(True)
        self.btn_play.toggled.connect(self._on_play_toggled)
        layout.addWidget(self.btn_play)

        self.btn_step_forward = self._tool_button("step_forward", "下一帧（→）")
        self.btn_step_forward.clicked.connect(lambda: self.step_requested.emit(1))
        layout.addWidget(self.btn_step_forward)

        layout.addSpacing(10)

        self.timecode = QLabel("00:00:00:00")
        self.timecode.setObjectName("Timecode")
        self.timecode.setMinimumWidth(0)
        layout.addWidget(self.timecode)

        self.duration = QLabel("00:00:00:00")
        self.duration.setObjectName("Muted")
        self.duration.setMinimumWidth(0)
        self.duration.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.duration.hide()          # 时长在状态栏与视图标题里已显示，这里只留接口
        layout.addWidget(self.duration)

        layout.addStretch(1)

        vol_label = QLabel("VOL")
        vol_label.setObjectName("FieldLabel")
        layout.addWidget(vol_label)

        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(64)
        self.volume.setToolTip("音量")
        self.volume.valueChanged.connect(self.volume_changed.emit)
        layout.addWidget(self.volume)

        layout.addSpacing(8)

        self.btn_fit = self._tool_button("fit", "画面适配")
        self.btn_fit.clicked.connect(self.fit_requested.emit)
        layout.addWidget(self.btn_fit)

        self.btn_fullscreen = self._tool_button("fullscreen", "全屏预览（F11）")
        self.btn_fullscreen.clicked.connect(self.fullscreen_requested.emit)
        layout.addWidget(self.btn_fullscreen)

        layout.addSpacing(10)

        self.perf_label = QLabel("—")
        self.perf_label.setObjectName("Muted")
        self.perf_label.setMinimumWidth(0)
        self.perf_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.perf_label.setToolTip("播放性能监测")
        layout.addWidget(self.perf_label, 1)

    # ------------------------------------------------------------------
    def _tool_button(self, kind: str, tooltip: str) -> QPushButton:
        button = QPushButton()
        button.setObjectName("Flat")
        button.setIcon(glyph_icon(kind, 16))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(28, 28)
        button.setToolTip(tooltip)
        button.setFlat(True)
        return button

    def _on_play_toggled(self, checked: bool) -> None:
        self.btn_play.setIcon(glyph_icon("pause" if checked else "play", 16))
        self.play_toggled.emit(checked)

    # ------------------------------------------------------------------
    def set_timecode(self, text: str) -> None:
        self.timecode.setText(text)

    def set_duration(self, text: str) -> None:
        self.duration.setText(text)

    def set_playing(self, playing: bool) -> None:
        if self.btn_play.isChecked() != playing:
            self.btn_play.blockSignals(True)
            self.btn_play.setChecked(playing)
            self.btn_play.setIcon(glyph_icon("pause" if playing else "play", 16))
            self.btn_play.blockSignals(False)

    def set_perf(self, text: str) -> None:
        self.perf_label.setText(text)


# ---------------------------------------------------------------------------
# 视图面板（Program / Source 共用）
# ---------------------------------------------------------------------------
class ViewerPanel(QWidget):
    """画面视图：标题栏 + 画布 + 传输条。

    画布由外部注入（QVideoWidget 或占位控件），便于第二阶段复用给 Source Viewer。
    """

    def __init__(self, title: str = "Program", canvas: QWidget | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerRoot")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = PanelHeader(title)
        self.scale_label = QLabel("适应")
        self.scale_label.setObjectName("Muted")
        self.header.add_right(self.scale_label)
        layout.addWidget(self.header)

        self.canvas_host = QWidget()
        self.canvas_host.setObjectName("ViewerRoot")
        self.canvas_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._host_layout = QVBoxLayout(self.canvas_host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self._host_layout.setSpacing(0)
        if canvas is not None:
            self._host_layout.addWidget(canvas)
        layout.addWidget(self.canvas_host, 1)

        self.transport = TransportBar()
        layout.addWidget(self.transport)

    def set_canvas(self, canvas: QWidget) -> None:
        while self._host_layout.count():
            item = self._host_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self._host_layout.addWidget(canvas)

    def set_title(self, title: str) -> None:
        self.header.title_label.setText(title.upper())

    def set_scale_text(self, text: str) -> None:
        self.scale_label.setText(text)
