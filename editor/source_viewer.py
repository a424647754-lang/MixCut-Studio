"""源监视器（Source Viewer）：素材入出点标记与插入时间线。

第二阶段能力的落地版：独立播放器 + 入点/出点 + 插入到播放头或末尾。
图片素材按静帧处理，默认 5 秒可用时长。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
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

from . import IMAGE_EXTS, MediaAsset, format_timecode, frame_duration_ms
from . import ui_theme as T
from .playback import PreviewCanvas
from .player_panel import ViewerPanel

IMAGE_DEFAULT_MS = 5000


class SourceViewerDialog(QDialog):
    """素材预览：可在素材内设入/出点，然后插入时间线。"""

    insert_requested = Signal(str, int, int, bool)   # asset_id, source_in, duration, append

    def __init__(self, asset: MediaAsset, fps, parent=None) -> None:
        super().__init__(parent)
        self.asset = asset
        self.fps = fps
        self.in_ms = 0
        self.out_ms = asset.duration or (IMAGE_DEFAULT_MS if asset.kind == "image" else 0)

        self.setWindowTitle(f"源监视器 — {asset.name}")
        self.resize(960, 660)
        self.setModal(False)

        self.canvas = PreviewCanvas()
        self.viewer = ViewerPanel("Source", self.canvas)
        self.viewer.transport.play_toggled.connect(self._on_play_toggled)
        self.viewer.transport.step_requested.connect(self._on_step)
        self.viewer.transport.volume_changed.connect(self._on_volume)
        self.viewer.transport.fit_requested.connect(self._cycle_zoom)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.viewer, 1)
        layout.addWidget(self._build_bottom())

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.5)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.canvas.video)
        self.player.positionChanged.connect(self._on_position)
        self._seeking = False

        self._load()
        self._update_labels()

    # ------------------------------------------------------------------
    def _build_bottom(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("TransportBar")
        panel.setFixedHeight(78)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(6)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(1, self.out_ms))
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.valueChanged.connect(self._on_slider_value)
        outer.addWidget(self.slider)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.range_label = QLabel("")
        self.range_label.setObjectName("Muted")
        row.addWidget(self.range_label)
        row.addStretch(1)

        for text, slot in (("标记入点 I", self._mark_in), ("标记出点 O", self._mark_out),
                           ("重置入出点", self._reset_range)):
            button = QPushButton(text)
            button.setObjectName("Flat")
            button.clicked.connect(slot)
            row.addWidget(button)

        self.btn_insert = QPushButton("插入到播放头")
        self.btn_insert.setObjectName("Primary")
        self.btn_insert.clicked.connect(lambda: self._emit_insert(False))
        row.addWidget(self.btn_insert)

        self.btn_append = QPushButton("追加到末尾")
        self.btn_append.clicked.connect(lambda: self._emit_insert(True))
        row.addWidget(self.btn_append)

        close = QPushButton("关闭")
        close.setObjectName("Flat")
        close.clicked.connect(self.close)
        row.addWidget(close)

        outer.addLayout(row)
        return panel

    # ------------------------------------------------------------------
    def _duration(self) -> int:
        if self.asset.kind == "image":
            return self.out_ms or IMAGE_DEFAULT_MS
        return max(1, self.asset.duration)

    def _is_image(self) -> bool:
        return (self.asset.kind == "image"
                or Path(self.asset.path).suffix.lower() in IMAGE_EXTS)

    def _load(self) -> None:
        self.slider.setRange(0, max(1, self._duration()))
        if self._is_image():
            self.canvas.show_still(self.asset.path, self.asset.name)
        else:
            self.canvas.show_video()
            self.player.setSource(QUrl.fromLocalFile(self.asset.path))
            self.player.pause()

    # ------------------------------------------------------------------
    def _on_play_toggled(self, playing: bool) -> None:
        if self._is_image():
            # 静帧没有播放概念：按钮只做视觉反馈
            self.viewer.transport.set_playing(False)
            return
        if playing:
            if self.player.position() >= self.out_ms:
                self.player.setPosition(self.in_ms)
            self.player.play()
        else:
            self.player.pause()

    def _on_step(self, direction: int) -> None:
        step = frame_duration_ms(self.fps)
        target = max(0, min(self._duration(), self._current_position() + direction * step))
        self._seek(target)

    def _on_volume(self, value: int) -> None:
        self.audio.setVolume(max(0.0, min(1.0, value / 100.0)))

    def _cycle_zoom(self) -> None:
        zoom = self.canvas.zoom
        next_zoom = 0.0 if zoom <= 0 else (0.5 if zoom >= 1.95 else (1.0 if zoom <= 1.05 else 2.0))
        self.canvas.set_zoom(next_zoom)
        self.viewer.set_scale_text("适应" if next_zoom == 0 else f"{int(next_zoom * 100)}%")

    def _current_position(self) -> int:
        return int(self.slider.value())

    def _seek(self, ms: int) -> None:
        self._seeking = True
        self.slider.setValue(int(ms))
        self._seeking = False
        if not self._is_image():
            self.player.setPosition(int(ms))

    def _on_position(self, ms: int) -> None:
        if self._seeking:
            return
        self.slider.blockSignals(True)
        self.slider.setValue(int(ms))
        self.slider.blockSignals(False)
        self.viewer.transport.set_timecode(format_timecode(ms, self.fps))
        if self.player.playbackState() == QMediaPlayer.PlayingState and ms >= self.out_ms:
            self.player.pause()
            self.viewer.transport.set_playing(False)

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_released(self) -> None:
        self._seeking = False
        self._seek(self.slider.value())

    def _on_slider_value(self, value: int) -> None:
        self.viewer.transport.set_timecode(format_timecode(value, self.fps))
        if not self._seeking and not self._is_image():
            self.player.setPosition(int(value))

    # ------------------------------------------------------------------
    def _mark_in(self) -> None:
        self.in_ms = min(self._current_position(), max(0, self.out_ms - 1))
        self._update_labels()

    def _mark_out(self) -> None:
        self.out_ms = max(self._current_position(), self.in_ms + 1)
        self._update_labels()

    def _reset_range(self) -> None:
        self.in_ms = 0
        self.out_ms = self._duration()
        self._update_labels()

    def _update_labels(self) -> None:
        self.range_label.setText(
            f"入点 {format_timecode(self.in_ms, self.fps)}   "
            f"出点 {format_timecode(self.out_ms, self.fps)}   "
            f"时长 {format_timecode(max(1, self.out_ms - self.in_ms), self.fps)}   "
            f"素材 {format_timecode(self.asset.duration, self.fps)}")
        self.viewer.transport.set_duration(format_timecode(self.asset.duration, self.fps))

    def _emit_insert(self, append: bool) -> None:
        length = max(1, self.out_ms - self.in_ms)
        if not self._is_image() and self.asset.duration and self.in_ms >= self.asset.duration:
            return
        self.insert_requested.emit(self.asset.id, self.in_ms, length, append)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.player.stop()
        super().closeEvent(event)
