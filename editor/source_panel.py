"""源监视器（内嵌面板版）。

达芬奇的做法是「源监视器常驻界面」，而不是弹窗。这里做成可嵌入的
SourcePanel：剪辑页右上角常驻，双击素材即可预览并设入出点；
同时保留 SourceViewerDialog 作为弹窗入口（旧接口不破坏）。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import IMAGE_EXTS, MediaAsset, format_timecode, frame_duration_ms, snap_to_frame
from . import ui_theme as T
from .icons import icon
from .playback import PreviewCanvas, _PosterWorker
from .player_panel import ViewerPanel

IMAGE_DEFAULT_MS = 5000


class SourcePanel(QWidget):
    """素材预览 + 入出点 + 插入时间线（常驻面板）。"""

    insert_requested = Signal(str, int, int, bool)   # asset_id, source_in, duration, append
    closed = Signal()

    def __init__(self, fps: Fraction = Fraction(30, 1), parent=None,
                 poster_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.fps = fps
        self.asset: MediaAsset | None = None
        self.in_ms = 0
        self.out_ms = 0
        self._seeking = False
        self._poster_dir = poster_dir
        self._poster_token = 0
        self._poster_workers: list[_PosterWorker] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.canvas = PreviewCanvas()
        self.viewer = ViewerPanel("SOURCE", self.canvas)
        self.viewer.transport.perf_label.hide()   # 性能读数只留给 PROGRAM
        self.viewer.transport.play_toggled.connect(self._on_play_toggled)
        self.viewer.transport.step_requested.connect(self._on_step)
        self.viewer.transport.volume_changed.connect(self._on_volume)
        self.viewer.transport.fit_requested.connect(self._cycle_zoom)
        layout.addWidget(self.viewer, 1)
        layout.addWidget(self._build_bottom())

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.5)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.canvas.video)
        self.player.positionChanged.connect(self._on_position)

        self.canvas.show_empty("双击素材即可在这里预览")

        self._poster_at = 0
        self._poster_timer = QTimer(self)
        self._poster_timer.setSingleShot(True)
        self._poster_timer.setInterval(150)
        self._poster_timer.timeout.connect(self._start_poster)

    # -- 源画面抽帧（暂停时也有画面） -----------------------------------
    def _schedule_poster(self, at_ms: int) -> None:
        if self._poster_dir is None or self.asset is None or self._is_image():
            return
        self._poster_at = max(0, int(at_ms))
        self._poster_timer.start()

    def _start_poster(self) -> None:
        if (self.asset is None or self._poster_dir is None or self._is_image()
                or self.player.playbackState() == QMediaPlayer.PlayingState):
            return
        self._poster_token += 1
        out_path = self._poster_dir / f"source_{self._poster_token % 12}.jpg"
        worker = _PosterWorker(self._poster_token, self.asset.path, self._poster_at, out_path)
        worker.ready.connect(self._on_poster)
        worker.finished.connect(lambda w=worker: self._drop_poster(w))
        self._poster_workers.append(worker)
        worker.start()

    def _drop_poster(self, worker: "_PosterWorker") -> None:
        if worker in self._poster_workers:
            self._poster_workers.remove(worker)

    def _on_poster(self, token: int, path: str) -> None:
        if token != self._poster_token or self._is_image():
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            return
        self.canvas.invalidate_still(path)
        self.canvas.show_still(path)

    def shutdown(self) -> None:
        self._poster_timer.stop()
        for worker in list(self._poster_workers):
            worker.wait(2000)
        self._poster_workers.clear()

    # ------------------------------------------------------------------
    def _build_bottom(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("TransportBar")
        panel.setFixedHeight(72)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(4)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(lambda: self._seek(self.slider.value()))
        self.slider.valueChanged.connect(self._on_slider_value)
        outer.addWidget(self.slider)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.range_label = QLabel("未载入素材")
        self.range_label.setObjectName("Muted")
        self.range_label.setMinimumWidth(0)
        row.addWidget(self.range_label, 1)

        # 底部操作条要够紧凑：窗口缩小到 1366 宽时不能把时间线挤出去，
        # 所以标记/重置/追加用小图标按钮 + 悬浮提示，只有"插入"带文字。
        self.btn_mark_in = self._icon_button("marker", "标记入点 (I)", self._mark_in, T.ACCENT)
        self.btn_mark_out = self._icon_button("marker", "标记出点 (O)", self._mark_out, T.OK)
        self.btn_reset = self._icon_button("undo", "重置入出点", self._reset_range)
        self.btn_insert = QPushButton("插入")
        self.btn_insert.setObjectName("Primary")
        self.btn_insert.setToolTip("把入点到出点插入播放头位置")
        self.btn_insert.clicked.connect(lambda: self._emit_insert(False))
        self.btn_append = self._icon_button("plus", "追加到时间线末尾", lambda: self._emit_insert(True))

        for widget in (self.btn_mark_in, self.btn_mark_out, self.btn_reset,
                       self.btn_insert, self.btn_append):
            row.addWidget(widget)

        outer.addLayout(row)
        return panel

    def _icon_button(self, kind: str, tooltip: str, slot, color: str = T.TEXT_DIM) -> QPushButton:
        button = QPushButton()
        button.setObjectName("IconButton")
        button.setFixedSize(26, 24)
        button.setIcon(icon(kind, 14, color))
        button.setIconSize(QSize(14, 14))
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _=False: slot())
        return button

    # ------------------------------------------------------------------
    def set_fps(self, fps: Fraction) -> None:
        self.fps = fps
        self._update_labels()

    def open_asset(self, asset: MediaAsset) -> None:
        self.asset = asset
        self.in_ms = 0
        self.out_ms = asset.duration or (IMAGE_DEFAULT_MS if asset.kind == "image" else 0)
        self.viewer.set_title(f"SOURCE · {asset.name}")
        self.player.stop()
        self.slider.setRange(0, max(1, self._duration()))
        self.slider.setValue(0)
        self.player.setPosition(0)

        if self._is_image():
            self.player.setSource(QUrl())
            self.canvas.show_still(asset.path, asset.name)
        elif asset.kind == "audio":
            self.player.setSource(QUrl.fromLocalFile(asset.path))
            self.canvas.show_empty("音频素材 · 无画面")
        else:
            self.player.setSource(QUrl.fromLocalFile(asset.path))
            self.player.pause()
            self.canvas.show_empty("正在载入画面…")
            self._schedule_poster(0)
        self._update_labels()

    def close_asset(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        # 立刻作废在飞的抽帧：否则上一素材的帧会在关闭后落盘并画到 SOURCE（B-45）
        self._poster_token += 1
        self.asset = None
        self.viewer.set_title("SOURCE")
        self.canvas.show_empty("双击素材即可在这里预览")
        self.range_label.setText("未载入素材")
        self.slider.setRange(0, 1)

    # ------------------------------------------------------------------
    def _duration(self) -> int:
        if self.asset is None:
            return 0
        if self.asset.kind == "image":
            return self.out_ms or IMAGE_DEFAULT_MS
        return max(1, self.asset.duration)

    def _is_image(self) -> bool:
        return bool(self.asset) and (self.asset.kind == "image"
                                     or Path(self.asset.path).suffix.lower() in IMAGE_EXTS)

    def _on_play_toggled(self, playing: bool) -> None:
        if self.asset is None or self._is_image():
            self.viewer.transport.set_playing(False)
            return
        if playing:
            if self.player.position() >= self.out_ms > 0:
                self._seek(self.in_ms)
            self._poster_timer.stop()
            self.canvas.show_video()
            self.player.play()
        else:
            self.player.pause()
            self._schedule_poster(self.player.position())

    def _on_step(self, direction: int) -> None:
        step = frame_duration_ms(self.fps)
        self._seek(max(0, min(self._duration(), self.slider.value() + direction * step)))

    def _on_volume(self, value: int) -> None:
        self.audio.setVolume(max(0.0, min(1.0, value / 100.0)))

    def _cycle_zoom(self) -> None:
        zoom = self.canvas.zoom
        next_zoom = 0.0 if zoom <= 0 else (0.5 if zoom >= 1.95 else 2.0)
        self.canvas.set_zoom(next_zoom)
        self.viewer.set_scale_text("适应" if next_zoom == 0 else f"{int(next_zoom * 100)}%")

    def _seek(self, ms: int) -> None:
        if self.asset is None:
            return
        self._seeking = True
        self.slider.setValue(int(ms))
        self._seeking = False
        if not self._is_image():
            self.player.setPosition(int(ms))
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self.canvas.show_video()
            self._schedule_poster(int(ms))

    def _on_position(self, ms: int) -> None:
        if self._seeking:
            return
        self.slider.blockSignals(True)
        self.slider.setValue(int(ms))
        self.slider.blockSignals(False)
        self.viewer.transport.set_timecode(format_timecode(ms, self.fps))
        if (self.player.playbackState() == QMediaPlayer.PlayingState
                and self.out_ms > 0 and ms >= self.out_ms):
            self.player.pause()
            self.viewer.transport.set_playing(False)

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_value(self, value: int) -> None:
        self.viewer.transport.set_timecode(format_timecode(value, self.fps))
        if self.asset is not None and not self._is_image():
            self.player.setPosition(int(value))

    # ------------------------------------------------------------------
    def _mark_in(self) -> None:
        # 入点必须落在整帧上，否则插入时间线的片段长度不是帧整数（B-37）
        value = snap_to_frame(self.slider.value(), self.fps)
        self.in_ms = max(0, min(value, max(0, self.out_ms - 1)))
        self._update_labels()

    def _mark_out(self) -> None:
        value = snap_to_frame(self.slider.value(), self.fps)
        if self._is_image():
            self.out_ms = max(value, self.in_ms + 1)
        else:
            limit = max(self._duration(), self.in_ms + 1)
            self.out_ms = max(self.in_ms + 1, min(value, limit))
        self._update_labels()

    def _reset_range(self) -> None:
        self.in_ms = 0
        self.out_ms = self._duration()
        self._update_labels()

    def _update_labels(self) -> None:
        if self.asset is None:
            return
        span = max(1, self.out_ms - self.in_ms)
        self.range_label.setText(
            f"入 {format_timecode(self.in_ms, self.fps)}   "
            f"出 {format_timecode(self.out_ms, self.fps)}   "
            f"段 {format_timecode(span, self.fps)}   "
            f"源 {format_timecode(self.asset.duration, self.fps)}")
        self.viewer.transport.set_duration(format_timecode(self.asset.duration, self.fps))

    def _emit_insert(self, append: bool) -> None:
        if self.asset is None:
            return
        length = max(1, self.out_ms - self.in_ms)
        if not self._is_image() and self.asset.duration and self.in_ms >= self.asset.duration:
            return
        self.insert_requested.emit(self.asset.id, self.in_ms, length, append)


class SourceViewerDialog(QDialog):
    """弹窗版源监视器（保留旧接口：viewer / canvas / btn_insert / in_ms 等）。"""

    insert_requested = Signal(str, int, int, bool)

    def __init__(self, asset: MediaAsset, fps=Fraction(30, 1), parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"源监视器 — {asset.name}")
        self.resize(1024, 700)
        self.setModal(False)

        self.panel = SourcePanel(fps, self)
        self.panel.insert_requested.connect(self.insert_requested.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.panel, 1)

        row = QHBoxLayout()
        row.setContentsMargins(12, 0, 12, 10)
        row.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("Chip")
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)

        self.panel.open_asset(asset)

    # -- 旧接口代理 -----------------------------------------------------
    @property
    def viewer(self):
        return self.panel.viewer

    @property
    def canvas(self):
        return self.panel.canvas

    @property
    def btn_insert(self):
        return self.panel.btn_insert

    @property
    def btn_append(self):
        return self.panel.btn_append

    @property
    def slider(self):
        return self.panel.slider

    @property
    def range_label(self):
        return self.panel.range_label

    @property
    def in_ms(self) -> int:
        return self.panel.in_ms

    @property
    def out_ms(self) -> int:
        return self.panel.out_ms

    def closeEvent(self, event) -> None:  # noqa: N802
        self.panel.close_asset()
        super().closeEvent(event)
