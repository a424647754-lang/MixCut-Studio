"""调色页（独立页面）— 对标达芬奇 Color 页。

结构：
  顶栏：画廊 | LUT库 | 媒体池 | 片段        快捷导出/时间线/节点/特效库/光箱
  主区：左=大预览器（含走带）  右=片段缩略图墙
  工具行：窗口/限定器/HDR/魔棒/追踪/降噪/模糊/发光…
  底栏：左=Camera Raw 风格参数  中=色轮 Lift/Gamma/Gain + 曲线  右=示波器（4 模式）
所有真实调色仍走 ffmpeg（eq + colorbalance），预览即导出效果。
"""

from __future__ import annotations

import math
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import Clip, format_timecode
from . import ui_theme as T
from .media import find_tool, grade_filters
from .player_panel import PanelHeader

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

PRESETS = {
    "中性": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "temperature": 0.0},
    "暖调": {"brightness": 0.02, "contrast": 1.05, "saturation": 1.12, "temperature": 0.35},
    "冷调": {"brightness": 0.0, "contrast": 1.05, "saturation": 0.95, "temperature": -0.35},
    "高对比": {"brightness": -0.02, "contrast": 1.35, "saturation": 1.08, "temperature": 0.0},
    "低饱和": {"brightness": 0.02, "contrast": 1.0, "saturation": 0.55, "temperature": 0.0},
    "电影感": {"brightness": -0.05, "contrast": 1.18, "saturation": 0.88, "temperature": -0.12},
}

# Camera Raw 面板（B-42）：四个旋钮 → grade 字段的换算口径，单点定义。
#   色温 1000..12000K，以 6500K 为中性；每 5500K 对应 temperature ±1.0
#   色调 -50..50 直接进 tint；曝光 -4..4 EV 直接进 exposure；锐度 0..100 直接进 sharpness
KELVIN_MIN, KELVIN_MAX, KELVIN_NEUTRAL = 1000.0, 12000.0, 6500.0
RAW_NEUTRAL = {"色温": KELVIN_NEUTRAL, "色调": 0.0, "曝光": 0.0, "锐度": 0.0}
GRADE_NEUTRAL = {
    "brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "temperature": 0.0,
    "exposure": 0.0, "tint": 0.0, "sharpness": 0.0,
}
RAW_KEYS = ("temperature", "exposure", "tint", "sharpness")


def kelvin_to_temperature(kelvin: float) -> float:
    """色温 K → temperature(-1..1)：偏暖(≥6500K)为正，偏冷为负。"""
    span = KELVIN_MAX - KELVIN_NEUTRAL      # 5500 与 KELVIN_NEUTRAL-KELVIN_MIN 相等
    return max(-1.0, min(1.0, (float(kelvin) - KELVIN_NEUTRAL) / span))


def temperature_to_kelvin(temperature: float) -> float:
    """temperature(-1..1) → 色温 K（与上面互逆）。"""
    span = KELVIN_MAX - KELVIN_NEUTRAL
    return max(KELVIN_MIN, min(KELVIN_MAX, KELVIN_NEUTRAL + float(temperature) * span))


# ===========================================================================
# 色轮（Lift / Gamma / Gain 通用）
# ===========================================================================
class ColorWheel(QWidget):
    """达芬奇式色轮：外圈色相环 + 可拖圆点 + 下方主亮度滑块。"""

    changed = Signal()

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.angle = 0.0          # 色相角（度）
        self.radius = 0.0         # 0~1 饱和度
        self.master = 0.0         # -1~1 主亮度
        self.setMinimumSize(150, 190)
        self._drag = False

    def set_values(self, angle: float, radius: float, master: float) -> None:
        self.angle, self.radius, self.master = angle, radius, master
        self.update()

    def reset(self) -> None:
        self.angle, self.radius, self.master = 0.0, 0.0, 0.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(T.BG_PANEL))
        side = min(self.width(), self.height() - 46)
        cx, cy = self.width() / 2, side / 2 + 6
        outer = side / 2 - 4
        inner = outer * 0.72

        rect = QRectF(cx - outer, cy - outer, outer * 2, outer * 2)
        for i in range(36):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor.fromHsv(i * 10, 220, 220))
            p.drawPie(rect, int((90 - (i + 1) * 10) * 16), int(10 * 16))
        p.setBrush(QColor(T.BG_PANEL))
        p.drawEllipse(QPointF(cx, cy), inner, inner)
        p.setPen(QPen(QColor(T.BORDER_SUBTLE), 1))
        p.drawLine(int(cx - inner), int(cy), int(cx + inner), int(cy))
        p.drawLine(int(cx), int(cy - inner), int(cx), int(cy + inner))

        r = self.radius * inner
        px = cx + r * math.cos(math.radians(self.angle))
        py = cy - r * math.sin(math.radians(self.angle))
        p.setBrush(QColor(T.TEXT_STRONG))
        p.setPen(QPen(QColor(T.BG_APP), 1))
        p.drawEllipse(QPointF(px, py), 5, 5)

        p.setPen(QColor(T.TEXT_DIM))
        p.drawText(QRectF(0, 0, self.width(), 16), Qt.AlignCenter, self.title)
        bar_y = side + 14
        p.setPen(QColor(T.TEXT_MUTED))
        p.drawText(4, bar_y + 12, "主")
        track = QRectF(20, bar_y + 4, self.width() - 40, 8)
        p.fillRect(track, QColor(T.BG_INPUT))
        knob_x = track.left() + (self.master + 1) / 2 * track.width()
        p.fillRect(QRectF(knob_x - 2, track.top() - 2, 4, track.height() + 4),
                   QColor(T.ACCENT))

    def _wheel_center(self) -> tuple[float, float, float]:
        side = min(self.width(), self.height() - 46)
        return self.width() / 2, side / 2 + 6, (side / 2 - 4) * 0.72

    def mousePressEvent(self, event) -> None:  # noqa: N802
        cx, cy, inner = self._wheel_center()
        dx, dy = event.position().x() - cx, event.position().y() - cy
        if math.hypot(dx, dy) <= inner:
            self._drag = True
            self._update_from_mouse(dx, dy, inner)
        elif event.position().y() > self.height() - 32:
            self._drag = True
            self._update_master(event.position().x())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._drag:
            return
        cx, cy, inner = self._wheel_center()
        if event.position().y() > self.height() - 32:
            self._update_master(event.position().x())
        else:
            self._update_from_mouse(event.position().x() - cx,
                                    event.position().y() - cy, inner)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = False

    def _update_from_mouse(self, dx: float, dy: float, inner: float) -> None:
        self.radius = min(1.0, math.hypot(dx, dy) / max(1.0, inner))
        self.angle = (math.degrees(math.atan2(-dy, dx)) + 360) % 360
        self.update()
        self.changed.emit()

    def _update_master(self, x: float) -> None:
        left, width = 20, self.width() - 40
        self.master = max(-1.0, min(1.0, ((x - left) / width) * 2 - 1))
        self.update()
        self.changed.emit()


# ===========================================================================
# 曲线编辑器
# ===========================================================================
class CurveEditor(QWidget):
    """可拖控制点的曲线（默认对角两点，点击空白加点）。"""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.points = [QPointF(0, 1), QPointF(1, 0)]
        self.setMinimumHeight(150)
        self._drag_idx = -1

    def reset(self) -> None:
        self.points = [QPointF(0, 1), QPointF(1, 0)]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BG_VIEWER))
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(T.BORDER_SUBTLE), 1))
        for i in range(1, 4):
            x, y = w * i / 4, h * i / 4
            p.drawLine(int(x), 0, int(x), h)
            p.drawLine(0, int(y), w, int(y))
        p.setPen(QPen(QColor(T.ACCENT), 2))
        prev = None
        for pt in self.points:
            x, y = pt.x() * w, pt.y() * h
            if prev is not None:
                p.drawLine(prev, QPointF(x, y))
            prev = QPointF(x, y)
        p.setBrush(QColor(T.ACCENT))
        p.setPen(Qt.NoPen)
        for pt in self.points:
            p.drawRect(QRectF(pt.x() * w - 3, pt.y() * h - 3, 6, 6))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        for i, pt in enumerate(self.points):
            if math.hypot(event.position().x() - pt.x() * self.width(),
                          event.position().y() - pt.y() * self.height()) < 10:
                self._drag_idx = i
                return
        nx = event.position().x() / self.width()
        ny = event.position().y() / self.height()
        self.points.append(QPointF(nx, ny))
        self.points.sort(key=lambda q: q.x())
        self._drag_idx = next(i for i, q in enumerate(self.points)
                              if abs(q.x() - nx) < 1e-9)
        self.update()
        self.changed.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_idx < 0:
            return
        nx = max(0.0, min(1.0, event.position().x() / self.width()))
        ny = max(0.0, min(1.0, event.position().y() / self.height()))
        if 0 < self._drag_idx < len(self.points) - 1:
            self.points[self._drag_idx] = QPointF(nx, ny)
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_idx = -1


# ===========================================================================
# 示波器（4 模式）
# ===========================================================================
class ScopeWidget(QWidget):
    """示波器：波形 / 分量图 / 矢量图 / 直方图。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mode = "波形"
        self._image: QImage | None = None
        self.setMinimumHeight(180)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BG_VIEWER))
        if self._image is None or self._image.isNull():
            p.setPen(QColor(T.TEXT_MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "无信号")
            return
        w, h = self.width(), self.height()
        if self.mode == "矢量图":
            self._paint_vectorscope(p, w, h)
        elif self.mode == "分量图":
            self._paint_parade(p, w, h)
        elif self.mode == "直方图":
            self._paint_histogram(p, w, h)
        else:
            self._paint_waveform(p, w, h)

    def _paint_waveform(self, p: QPainter, w: int, h: int) -> None:
        small = self._image.scaled(120, 68, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        p.setPen(Qt.NoPen)
        for x in range(small.width()):
            for y in range(small.height()):
                c = small.pixelColor(x, y)
                lum = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
                p.setBrush(QColor(120, 200, 140, 60))
                p.drawRect(int(x / small.width() * w),
                           int((1 - lum / 255) * h), 2, 2)

    def _paint_parade(self, p: QPainter, w: int, h: int) -> None:
        small = self._image.scaled(90, 60, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        colors = (QColor(220, 90, 90), QColor(90, 210, 120), QColor(90, 140, 230))
        seg = w / 3
        for ch in range(3):
            p.setPen(Qt.NoPen)
            p.setBrush(colors[ch])
            for x in range(small.width()):
                vals = [small.pixelColor(x, y).getRgb()[ch] for y in range(small.height())]
                peak = max(vals)
                p.drawRect(int(ch * seg + x / small.width() * seg),
                           int((1 - peak / 255) * h), 2, 2)

    def _paint_histogram(self, p: QPainter, w: int, h: int) -> None:
        small = self._image.scaled(128, 72, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        bins = [[0] * 32 for _ in range(3)]
        for x in range(small.width()):
            for y in range(small.height()):
                c = small.pixelColor(x, y)
                for ch in range(3):
                    bins[ch][min(31, c.getRgb()[ch] // 8)] += 1
        peak = max(max(b) for b in bins)
        colors = (QColor(220, 90, 90), QColor(90, 210, 120), QColor(90, 140, 230))
        for ch in range(3):
            p.setPen(QPen(colors[ch], 1))
            prev = None
            for i, v in enumerate(bins[ch]):
                x = i / 31 * w
                y = h - v / peak * h
                if prev is not None:
                    p.drawLine(prev, QPointF(x, y))
                prev = QPointF(x, y)

    def _paint_vectorscope(self, p: QPainter, w: int, h: int) -> None:
        cx, cy = w / 2, h / 2
        r = min(cx, cy) - 8
        p.setPen(QPen(QColor(T.BORDER_SUBTLE), 1))
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.drawLine(int(cx - r), int(cy), int(cx + r), int(cy))
        p.drawLine(int(cx), int(cy - r), int(cx), int(cy + r))
        small = self._image.scaled(80, 45, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        p.setPen(Qt.NoPen)
        for x in range(small.width()):
            for y in range(small.height()):
                c = small.pixelColor(x, y)
                u = (c.red() - c.green()) / 255
                v = (c.blue() - c.green()) / 255
                p.setBrush(QColor(200, 200, 120, 50))
                p.drawRect(int(cx + u * r) - 1, int(cy - v * r) - 1, 2, 2)


# ===========================================================================
# ffmpeg 渲染线程
# ===========================================================================
class _Renderer(QThread):
    done = Signal(int, object, str)

    def __init__(self, token: int, path: str, position_ms: int, grade: dict,
                 width: int, height: int, out_path: Path) -> None:
        super().__init__()
        self.token = token
        self.path = path
        self.position_ms = max(0, int(position_ms))
        self.grade = dict(grade)
        self.width, self.height = max(2, width), max(2, height)
        self.out_path = out_path

    def _chain(self) -> str:
        chain = grade_filters(self.grade)
        chain.append(f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease")
        chain.append(f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2")
        return ",".join(chain)

    def run(self) -> None:  # noqa: D401
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            self.done.emit(self.token, None, "未找到 ffmpeg")
            return
        suffix = Path(self.path).suffix.lower()
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}:
            cmd += ["-i", self.path]
        else:
            cmd += ["-ss", f"{self.position_ms / 1000:.3f}", "-i", self.path]
        cmd += ["-frames:v", "1", "-vf", self._chain(), "-q:v", "3", str(self.out_path)]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           creationflags=_CREATE_NO_WINDOW, timeout=25)
        except (OSError, subprocess.SubprocessError) as exc:
            self.done.emit(self.token, None, f"渲染失败：{exc}")
            return
        image = QImage(str(self.out_path)) if self.out_path.exists() else QImage()
        self.done.emit(self.token, None if image.isNull() else image, "")


# ===========================================================================
# 主页面
# ===========================================================================
class ColorPage(QWidget):
    grade_preview = Signal(dict)
    grade_apply = Signal(dict)
    grade_reset = Signal()
    clip_activated = Signal(str)
    status = Signal(str)

    def __init__(self, cache_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.fps: Fraction = Fraction(30, 1)
        self.width, self.height = 960, 540
        self._clip: Clip | None = None
        self._position = 0
        self._token = 0
        self._renderer: _Renderer | None = None
        self._workers: list[_Renderer] = []
        self._pending_render = False
        self._image: QImage | None = None
        self._show_original = False
        self._loading = False
        self._nodes: list[dict] = [dict(PRESETS["中性"])]
        self._active_node = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())

        main_split = QSplitter(Qt.Horizontal)
        main_split.setChildrenCollapsible(False)
        main_split.setHandleWidth(2)
        main_split.addWidget(self._build_viewer())
        main_split.addWidget(self._build_clip_wall())
        main_split.setSizes([1280, 480])
        root.addWidget(main_split, 1)

        root.addWidget(self._build_tool_row())

        bottom_split = QSplitter(Qt.Horizontal)
        bottom_split.setChildrenCollapsible(False)
        bottom_split.setHandleWidth(2)
        bottom_split.addWidget(self._build_raw_panel())
        bottom_split.addWidget(self._build_wheels_panel())
        bottom_split.addWidget(self._build_scope_panel())
        bottom_split.setSizes([280, 520, 420])
        bottom_split.setMaximumHeight(300)
        root.addWidget(bottom_split)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(260)
        self._debounce.timeout.connect(self.render)
        self._page_active = False       # 只有"调色"页在前台才允许起 ffmpeg 抽帧（B-16）

    # ------------------------------------------------------------------
    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("PanelRoot")
        bar.setFixedHeight(38)
        bar.setStyleSheet(f"#PanelRoot {{ background:{T.BG_PANEL};"
                          f" border-bottom:1px solid {T.BORDER}; }}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(2)
        for label in ("画廊", "LUT 库", "媒体池", "片段"):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("Chip")
            b.setFixedHeight(26)
            if label == "片段":
                b.setChecked(True)
            lay.addWidget(b)
        lay.addStretch(1)
        for label in ("快捷导出", "时间线", "节点", "特效库", "光箱"):
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setFixedHeight(26)
            lay.addWidget(b)
        return bar

    # ------------------------------------------------------------------
    def _build_viewer(self) -> QWidget:
        box = QWidget()
        box.setObjectName("ViewerRoot")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        header = PanelHeader("预览 · VIEWER")
        self.clip_label = QLabel("未选择片段")
        self.clip_label.setObjectName("Muted")
        header.add_right(self.clip_label)
        v.addWidget(header)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet(f"background:{T.BG_VIEWER}; color:{T.TEXT_MUTED};")
        self.preview.setText("选择片段后可在此看到真实调色结果")
        v.addWidget(self.preview, 1)

        tr = QWidget()
        tr.setObjectName("TransportBar")
        tr.setFixedHeight(36)
        tl = QHBoxLayout(tr)
        tl.setContentsMargins(8, 0, 8, 0)
        tl.setSpacing(6)
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setObjectName("Chip")
        self.btn_prev.clicked.connect(lambda: self.step_clip(-1))
        self.btn_next = QPushButton("▶")
        self.btn_next.setObjectName("Chip")
        self.btn_next.clicked.connect(lambda: self.step_clip(1))
        tl.addWidget(self.btn_prev)
        tl.addWidget(self.btn_next)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1)
        self.slider.valueChanged.connect(self._on_slider)
        tl.addWidget(self.slider, 1)
        self.timecode = QLabel("00:00:00:00")
        self.timecode.setObjectName("Timecode")
        tl.addWidget(self.timecode)
        v.addWidget(tr)
        return box

    # ------------------------------------------------------------------
    def _build_clip_wall(self) -> QWidget:
        box = QWidget()
        box.setObjectName("PanelRoot")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(PanelHeader("片段 · CLIPS"))
        self.strip = QListWidget()
        self.strip.setViewMode(QListWidget.IconMode)
        self.strip.setIconSize(QSize(128, 72))
        self.strip.setGridSize(QSize(140, 104))
        self.strip.setResizeMode(QListWidget.Adjust)
        self.strip.setMovement(QListWidget.Static)
        self.strip.setSpacing(4)
        self.strip.setWordWrap(True)
        self.strip.setSelectionMode(QListWidget.SingleSelection)
        self.strip.itemSelectionChanged.connect(self._on_strip_selection)
        v.addWidget(self.strip, 1)
        self.strip_hint = QLabel("时间线上还没有片段")
        self.strip_hint.setObjectName("Muted")
        self.strip_hint.setAlignment(Qt.AlignCenter)
        v.addWidget(self.strip_hint)
        return box

    # ------------------------------------------------------------------
    def _build_tool_row(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("PanelRoot")
        bar.setFixedHeight(34)
        bar.setStyleSheet(f"#PanelRoot {{ background:{T.BG_PANEL};"
                          f" border-top:1px solid {T.BORDER};"
                          f" border-bottom:1px solid {T.BORDER}; }}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(4)
        for label in ("窗口", "限定器", "HDR", "魔棒", "曲线", "水滴", "六边",
                      "吸管", "圆环", "十字", "图片", "胶片", "3D"):
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setFixedSize(40, 26)
            lay.addWidget(b)
        lay.addStretch(1)
        return bar

    # ------------------------------------------------------------------
    def _build_raw_panel(self) -> QWidget:
        box = QWidget()
        box.setObjectName("PanelRoot")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(PanelHeader("Camera Raw"))
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.setSpacing(6)
        self.raw_spins: dict[str, QDoubleSpinBox] = {}
        for key, title, lo, hi, step in (
            ("色温", "色温", 1000, 12000, 10),
            ("色调", "色调", -50, 50, 0.5),
            ("曝光", "曝光", -4, 4, 0.1),
            ("锐度", "锐度", 0, 100, 1),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(title))
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setValue(RAW_NEUTRAL[key])
            s.setKeyboardTracking(False)     # 避免每敲一个数字就重渲染一次
            s.valueChanged.connect(self._on_raw_changed)
            row.addWidget(s, 1)
            self.raw_spins[key] = s
            bl.addLayout(row)
        bl.addStretch(1)
        v.addWidget(body, 1)
        return box

    # ------------------------------------------------------------------
    def _build_wheels_panel(self) -> QWidget:
        box = QWidget()
        box.setObjectName("PanelRoot")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(PanelHeader("一级校色 · PRIMARY"))

        node_bar = QWidget()
        nl = QHBoxLayout(node_bar)
        nl.setContentsMargins(8, 4, 8, 4)
        nl.setSpacing(4)
        self._node_row = nl
        self._node_buttons: list[QPushButton] = []
        self._rebuild_node_buttons()
        v.addWidget(node_bar)

        tabs = QTabWidget()
        wheel_tab = QWidget()
        wl = QHBoxLayout(wheel_tab)
        wl.setContentsMargins(4, 4, 4, 4)
        self.wheels: dict[str, ColorWheel] = {}
        for name in ("Lift", "Gamma", "Gain"):
            cw = ColorWheel(name)
            cw.changed.connect(self._on_wheel_changed)
            self.wheels[name] = cw
            wl.addWidget(cw)
        tabs.addTab(wheel_tab, "色轮")

        curve_tab = QWidget()
        cl = QVBoxLayout(curve_tab)
        cl.setContentsMargins(4, 4, 4, 4)
        self.curve_editor = CurveEditor()
        cl.addWidget(self.curve_editor)
        tabs.addTab(curve_tab, "曲线")
        v.addWidget(tabs, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_compare = QPushButton("对比原图")
        self.btn_compare.setObjectName("Chip")
        self.btn_compare.pressed.connect(lambda: self._set_original(True))
        self.btn_compare.released.connect(lambda: self._set_original(False))
        btn_row.addWidget(self.btn_compare)
        self.btn_apply = QPushButton("应用")
        self.btn_apply.setObjectName("Primary")
        self.btn_apply.clicked.connect(lambda: self.grade_apply.emit(self.values()))
        btn_row.addWidget(self.btn_apply)
        self.btn_reset = QPushButton("重置")
        self.btn_reset.setObjectName("Chip")
        self.btn_reset.clicked.connect(self.reset)
        btn_row.addWidget(self.btn_reset)
        v.addLayout(btn_row)

        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        return box

    # ------------------------------------------------------------------
    def _build_scope_panel(self) -> QWidget:
        box = QWidget()
        box.setObjectName("PanelRoot")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(PanelHeader("示波器 · SCOPES"))
        mode_row = QWidget()
        ml = QHBoxLayout(mode_row)
        ml.setContentsMargins(6, 4, 6, 4)
        ml.setSpacing(4)
        for mode in ("波形", "分量图", "矢量图", "直方图"):
            b = QPushButton(mode)
            b.setObjectName("Chip")
            b.setCheckable(True)
            if mode == "波形":
                b.setChecked(True)
            b.clicked.connect(lambda _=0, m=mode: self.scope.set_mode(m))
            ml.addWidget(b)
        v.addWidget(mode_row)
        self.scope = ScopeWidget()
        v.addWidget(self.scope, 1)
        return box

    # ------------------------------------------------------------------
    # 节点
    # ------------------------------------------------------------------
    def _rebuild_node_buttons(self) -> None:
        for b in self._node_buttons:
            b.setParent(None)
        self._node_buttons.clear()
        for i in range(len(self._nodes)):
            b = QPushButton(f"节点 {i + 1}")
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(i == self._active_node)
            b.clicked.connect(lambda _=0, idx=i: self._select_node(idx))
            self._node_row.addWidget(b)
            self._node_buttons.append(b)
        add = QPushButton("+")
        add.setObjectName("Chip")
        add.setFixedWidth(30)
        add.clicked.connect(self._add_node)
        self._node_row.addWidget(add)
        self._node_row.addStretch(1)

    def _add_node(self) -> None:
        self._nodes.append(dict(PRESETS["中性"]))
        self._active_node = len(self._nodes) - 1
        self._rebuild_node_buttons()
        self._reset_wheels()
        self._debounce.start()

    def _select_node(self, index: int) -> None:
        if index == self._active_node:
            return
        self._active_node = index
        for i, b in enumerate(self._node_buttons):
            b.setChecked(i == index)

    def _reset_wheels(self) -> None:
        for cw in self.wheels.values():
            cw.reset()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def set_project(self, fps: Fraction, width: int, height: int) -> None:
        self.fps, self.width, self.height = fps, max(2, width), max(2, height)

    def set_clips(self, clips: list[tuple[Clip, QPixmap | None, str]]) -> None:
        self.strip.blockSignals(True)
        self.strip.clear()
        for clip, thumb, track_name in clips:
            item = QListWidgetItem(f"{track_name} · {clip.name}")
            if thumb is not None and not thumb.isNull():
                item.setIcon(thumb.scaled(128, 72, Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation))
            item.setData(Qt.UserRole, clip.id)
            self.strip.addItem(item)
        self.strip.blockSignals(False)
        self.strip_hint.setText("时间线上还没有片段" if not clips else
                                f"{len(clips)} 个片段 · 点击切换")

    def set_active_clip(self, clip: Clip | None, track_name: str = "") -> None:
        self._clip = clip
        self._loading = True
        if clip is None:
            self.clip_label.setText("未选择片段")
            self.preview.setText("选择片段后可在此看到真实调色结果")
            self._loading = False
            return
        self.clip_label.setText(f"{track_name} · {clip.name}")
        node = dict(clip.grade())
        # RAW 四键由 Camera Raw 面板负责，节点里清零，避免两处相加算两遍
        for key in RAW_KEYS:
            node[key] = 0.0
        self._nodes = [node]
        self._active_node = 0
        self._rebuild_node_buttons()
        self._reset_wheels()
        self._load_raw(clip.grade())
        self.slider.setRange(0, max(1, clip.duration))
        self._position = clip.start
        self.slider.setValue(0)
        self.hint.setText("调色只作用于当前片段；导出的画面与这里一致。")
        self._loading = False
        self.render()

    def set_position(self, timeline_ms: int) -> None:
        if self._clip is None:
            return
        self._position = max(self._clip.start, min(self._clip.end - 1, timeline_ms))
        self.slider.blockSignals(True)
        self.slider.setValue(self._position - self._clip.start)
        self.slider.blockSignals(False)
        self.timecode.setText(format_timecode(self._position, self.fps))
        self._debounce.start()

    @property
    def active_clip(self) -> Clip | None:
        return self._clip

    # ------------------------------------------------------------------
    # 色轮 -> 4 参数 grade
    # ------------------------------------------------------------------
    def _on_wheel_changed(self) -> None:
        if self._loading:
            return
        self._debounce.start()

    def _wheel_grade(self) -> dict:
        g = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "temperature": 0.0}
        for name, weight in (("Lift", 0.5), ("Gamma", 0.7), ("Gain", 1.0)):
            cw = self.wheels[name]
            g["brightness"] += cw.master * 0.25 * weight
            g["temperature"] += math.cos(math.radians(cw.angle)) * cw.radius * 0.3 * weight
            g["saturation"] *= 1.0 + cw.radius * 0.25 * weight
        g["contrast"] = 1.0 + self.wheels["Gamma"].radius * 0.15
        return g

    def values(self) -> dict:
        wheel = self._wheel_grade()
        merged = dict(GRADE_NEUTRAL)
        for node in self._nodes:
            merged["brightness"] += node.get("brightness", 0.0)
            merged["temperature"] += node.get("temperature", 0.0)
            merged["contrast"] *= node.get("contrast", 1.0)
            merged["saturation"] *= node.get("saturation", 1.0)
            merged["exposure"] += node.get("exposure", 0.0)
            merged["tint"] += node.get("tint", 0.0)
            merged["sharpness"] += node.get("sharpness", 0.0)
        merged["brightness"] += wheel["brightness"]
        merged["temperature"] += wheel["temperature"]
        merged["contrast"] *= wheel["contrast"]
        merged["saturation"] *= wheel["saturation"]
        # Camera Raw 面板（B-42）：四个旋钮在此并入，预览与导出走同一份 values()
        raw = self._raw_grade()
        merged["temperature"] += raw["temperature"]
        merged["exposure"] += raw["exposure"]
        merged["tint"] += raw["tint"]
        merged["sharpness"] = max(0.0, min(100.0, merged["sharpness"] + raw["sharpness"]))
        merged["temperature"] = max(-1.0, min(1.0, merged["temperature"]))
        merged["exposure"] = max(-4.0, min(4.0, merged["exposure"]))
        merged["tint"] = max(-50.0, min(50.0, merged["tint"]))
        merged["brightness"] = max(-1.0, min(1.0, merged["brightness"]))
        merged["contrast"] = max(0.0, min(3.0, merged["contrast"]))
        merged["saturation"] = max(0.0, min(3.0, merged["saturation"]))
        return merged

    def _raw_grade(self) -> dict:
        """Camera Raw 四旋钮 → grade 字段（temperature 走色温换算）。"""
        spins = getattr(self, "raw_spins", {})
        if not spins:
            return {"temperature": 0.0, "exposure": 0.0, "tint": 0.0, "sharpness": 0.0}
        return {
            "temperature": kelvin_to_temperature(spins["色温"].value()),
            "exposure": spins["曝光"].value(),
            "tint": spins["色调"].value(),
            "sharpness": spins["锐度"].value(),
        }

    def _load_raw(self, grade: dict) -> None:
        """把片段已存的调色值回填到四旋钮（不触发信号，避免自激）。"""
        spins = getattr(self, "raw_spins", {})
        if not spins:
            return
        was = self._loading
        self._loading = True
        try:
            spins["色温"].setValue(temperature_to_kelvin(float(grade.get("temperature", 0.0) or 0.0)))
            spins["色调"].setValue(float(grade.get("tint", 0.0) or 0.0))
            spins["曝光"].setValue(float(grade.get("exposure", 0.0) or 0.0))
            spins["锐度"].setValue(float(grade.get("sharpness", 0.0) or 0.0))
        finally:
            self._loading = was

    def _on_raw_changed(self, *_args) -> None:
        if self._loading:
            return
        if getattr(self, "_debounce", None) is None:
            return
        self._debounce.start()

    def reset(self) -> None:
        self._nodes = [dict(PRESETS["中性"])]
        self._active_node = 0
        self._rebuild_node_buttons()
        self._reset_wheels()
        self._load_raw({})
        self.curve_editor.reset()
        self.grade_reset.emit()
        self.render()

    def _set_original(self, show: bool) -> None:
        self._show_original = show
        self._display()

    def _on_slider(self, value: int) -> None:
        if self._clip is None:
            return
        self._position = self._clip.start + int(value)
        self.timecode.setText(format_timecode(self._position, self.fps))
        self._debounce.start()

    def _on_strip_selection(self) -> None:
        items = self.strip.selectedItems()
        if items:
            self.clip_activated.emit(items[0].data(Qt.UserRole))

    def step_clip(self, direction: int) -> None:
        row = self.strip.currentRow()
        target = max(0, min(self.strip.count() - 1, row + direction))
        self.strip.setCurrentRow(target)

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _source_position(self) -> int:
        return 0 if self._clip is None else self._clip.source_time(self._position)

    def set_page_active(self, active: bool) -> None:
        """调色页是否在前台。不可见时一律不起抽帧线程，切回来再补渲染（B-16）。"""
        active = bool(active)
        if active == self._page_active:
            return
        self._page_active = active
        if active and self._pending_render and self._clip is not None:
            self._pending_render = False
            self._debounce.start()      # 延后一拍，让页面切换自身的刷新先落地

    def render(self, force: bool = False) -> None:
        clip = self._clip
        if clip is None or not clip.path or not Path(clip.path).exists():
            self.preview.setText("片段文件缺失，无法预览")
            return
        if not self._page_active and not force:
            # 页面不可见：只记一个"需要重画"，不启动 ffmpeg（切回调色页时补）
            self._pending_render = True
            return
        if self._renderer is not None and self._renderer.isRunning():
            self._pending_render = True
            return
        self._token += 1
        grade = {} if self._show_original else self.values()
        out_path = self.cache_dir / f"grade_{self._token}.jpg"
        r = _Renderer(self._token, clip.path, self._source_position(),
                      grade, self.width, self.height, out_path)
        r.done.connect(lambda token, image, msg, rr=r:
                       self._on_rendered(token, image, msg, rr))
        self._renderer = r
        self._workers.append(r)
        r.start()
        self.status.emit("正在渲染调色预览…")

    def _on_rendered(self, token: int, image: object, message: str,
                     renderer: "_Renderer") -> None:
        if renderer in self._workers:
            self._workers.remove(renderer)
        if token != self._token:
            return
        if message:
            self.status.emit(message)
            self.preview.setText(message)
            return
        self._image = image  # type: ignore[assignment]
        self.scope.set_image(self._image)
        self._display()
        self.status.emit("调色预览已更新")
        if self._pending_render:
            self._pending_render = False
            self._debounce.start()

    def _display(self) -> None:
        if self._image is None or self._image.isNull():
            return
        pixmap = QPixmap.fromImage(self._image).scaled(
            self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pixmap)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._display()

    def shutdown(self) -> None:
        for w in list(self._workers):
            w.wait(3000)
        self._workers.clear()


__all__ = ["ColorPage", "PRESETS"]
