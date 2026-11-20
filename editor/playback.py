"""时间线播放引擎：跨片段连续播放。

设计要点：
- 用**单调时钟**推进时间线位置，媒体播放器只负责"当前片段的声音与画面"；
  每帧把播放器真实位置与期望位置比较，超过容差就重新对齐，避免长片漂移。
- 图片素材按静帧显示，不占用播放器；音频轨用独立播放器按轨并发。
- 引擎只读工程数据，不写任何东西；播放头由时间线控件接收 position_changed。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from . import IMAGE_EXTS, Clip, Project
from . import ui_theme as T
from .composite import grade_tag, layer_stack, make_frame_provider, render_composite
from .limits import preview_gain
from .media import _CREATE_NO_WINDOW, find_tool, grade_filters, native_frame

# 期望位置与播放器实际位置相差超过该值就重新 seek
DRIFT_TOLERANCE_MS = 220
TICK_MS = 33
POSTER_CACHE_LIMIT = 64      # 抽帧缓存条目上限（B-20）
POSTER_FILE_LIMIT = 40       # 抽帧文件保留个数（B-19）


# ---------------------------------------------------------------------------
# 画布：视频层 + 静帧层 + 调色近似层
# ---------------------------------------------------------------------------
class PreviewCanvas(QWidget):
    """Program / Source 共用画布。

    三层叠加：QVideoWidget（视频）→ QLabel（图片/静帧）→ 调色近似叠加层。
    静帧层同时承担"无画面"提示，避免额外控件。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.setObjectName("PreviewCanvas")
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(f"#PreviewCanvas {{ background:{T.BG_VIEWER}; }}")

        self.video = QVideoWidget(self)
        self.video.setAspectRatioMode(Qt.KeepAspectRatio)
        self.video.setStyleSheet(f"background:{T.BG_VIEWER};")

        self.still = QLabel(self)
        self.still.setAlignment(Qt.AlignCenter)
        self.still.setStyleSheet(
            f"background:{T.BG_VIEWER}; color:{T.TEXT_MUTED}; font-size:{T.FONT_SIZE_SMALL}pt;")

        self.tint = QWidget(self)
        self.tint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.tint.setStyleSheet("background: transparent;")
        self.tint.hide()

        self._source: QPixmap | None = None
        self._cache: dict[str, QPixmap] = {}
        self._mode = "empty"           # empty | still | video
        self._still_path = ""
        self._zoom = 0.0               # 0 = 适应
        self.show_empty("无画面")

    # -- 状态切换 ------------------------------------------------------
    def show_empty(self, text: str = "无画面") -> None:
        self._mode = "empty"
        self._still_path = ""
        self._source = None
        self.still.setPixmap(QPixmap())
        self.still.setText(text)
        self.still.setStyleSheet(
            f"background:{T.BG_VIEWER}; color:{T.TEXT_MUTED}; font-size:{T.FONT_SIZE_SMALL}pt;")
        self.video.hide()
        self.still.show()
        self._relayout()

    def invalidate_still(self, path: str) -> None:
        """让某张静帧的缓存失效（文件被同名覆盖时必须调，B-39 对外接口）。

        调用方不再直接碰画布内部字典；若当前正显示该文件，顺带重新读盘，
        避免"文件换了、画面还是旧的"。
        """
        self._cache.pop(path, None)
        if self._mode == "still" and self._still_path == path:
            self._still_path = ""
            self.show_still(path)

    def show_still(self, path: str, caption: str = "") -> None:
        if self._mode == "still" and self._still_path == path:
            return
        self._mode = "still"
        self._still_path = path
        pixmap = self._cache.get(path)
        if pixmap is None:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self._cache[path] = pixmap
        self._source = None if pixmap.isNull() else pixmap
        self.still.setStyleSheet(f"background:{T.BG_VIEWER}; color:{T.TEXT_DIM};")
        self.still.setText("" if self._source is not None else (caption or "图片无法读取"))
        # QVideoWidget 是原生窗口控件，兄弟控件盖不住它，必须显式隐藏
        self.video.hide()
        self.still.show()
        self._relayout()

    def show_video(self) -> None:
        if self._mode == "video":
            return
        self._mode = "video"
        self._still_path = ""
        self._source = None
        self.still.hide()
        self.video.show()
        self._relayout()

    def show_composite(self, image) -> None:  # noqa: ANN001 - QImage
        """显示多轨合成结果（B-04）。

        QVideoWidget 是原生窗口，兄弟控件盖不住它，所以合成模式必须显式隐藏视频层。
        """
        if image is None or image.isNull():
            self.show_empty("合成失败")
            return
        self._mode = "composite"
        self._still_path = ""
        self._source = QPixmap.fromImage(image)
        self.still.setText("")
        self.still.setStyleSheet(f"background:{T.BG_VIEWER}; color:{T.TEXT_DIM};")
        self.video.hide()
        self.still.show()
        self._relayout()

    # -- 缩放 ----------------------------------------------------------
    def set_zoom(self, zoom: float) -> None:
        """0 = 适应；其它值按比例放大/缩小画面（相对适应尺寸）。"""
        self._zoom = float(zoom)
        self._relayout()

    @property
    def zoom(self) -> float:
        return self._zoom

    # -- 调色近似预览 ---------------------------------------------------
    def apply_grade_preview(self, grade: dict | None) -> None:
        """用半透明叠加近似显示亮度/色温/色调，精确结果以暂停帧与导出为准。

        播放中走 QMediaPlayer 原始画面，没法逐帧跑 ffmpeg，所以这里只做叠加
        近似；但必须覆盖 Camera Raw 的曝光与色调，否则用户拉曝光时播放画面
        毫无反应（项目 13 验收发现项）。
        """
        if not grade:
            self.tint.hide()
            return
        brightness = float(grade.get("brightness", 0.0) or 0.0)
        exposure = float(grade.get("exposure", 0.0) or 0.0)
        temperature = float(grade.get("temperature", 0.0) or 0.0)
        tint = float(grade.get("tint", 0.0) or 0.0)
        # 1EV 的观感约等于 brightness 的 0.5 档，仅作叠加近似
        lift = max(-1.0, min(1.0, brightness + exposure * 0.5))
        if abs(lift) < 1e-4 and abs(temperature) < 1e-4 and abs(tint) < 1e-3:
            self.tint.hide()
            return
        if lift >= 0:
            color = QColor(255, 255, 255, int(min(1.0, lift) * 80))
        else:
            color = QColor(0, 0, 0, int(min(1.0, -lift) * 80))
        if abs(temperature) > 1e-4:
            warm = QColor(255, 156, 74, int(min(1.0, abs(temperature)) * 46))
            cool = QColor(64, 140, 255, int(min(1.0, abs(temperature)) * 46))
            color = warm if temperature > 0 else cool
        elif abs(tint) > 1e-3:
            # 色调：正偏绿、负偏品红，与 colorchannelmixer 的绿通道增益同向
            green = QColor(80, 200, 110, int(min(50.0, abs(tint)) / 50.0 * 40))
            magenta = QColor(205, 90, 190, int(min(50.0, abs(tint)) / 50.0 * 40))
            color = green if tint > 0 else magenta
        self.tint.setStyleSheet(
            f"background: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});")
        self.tint.show()
        self._relayout()

    # -- 几何 ----------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        rect = self.rect()
        self.tint.setGeometry(rect)
        if self._zoom <= 0:
            self.video.setGeometry(rect)
        else:
            width = max(1, int(rect.width() * self._zoom))
            height = max(1, int(rect.height() * self._zoom))
            self.video.setGeometry(
                (rect.width() - width) // 2, (rect.height() - height) // 2, width, height)
        self.still.setGeometry(rect)
        if self._source is not None and self._mode in ("still", "composite"):
            scaled = self._source.scaled(
                rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.still.setPixmap(scaled)
        self.tint.raise_()


# ---------------------------------------------------------------------------
# 音频轨声部
# ---------------------------------------------------------------------------
class _Voice:
    """一条音频轨对应一个播放器，切换片段时只重设源。"""

    def __init__(self, parent: QObject) -> None:
        self.player = QMediaPlayer(parent)
        self.output = QAudioOutput(parent)
        self.player.setAudioOutput(self.output)
        self.clip_id = ""
        self.path = ""

    def stop(self) -> None:
        """彻底释放：停播 + 断开输出 + 解绑父对象，避免换工程后越积越多（B-21）。"""
        try:
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.setAudioOutput(None)
            self.player.setParent(None)
            self.player.deleteLater()
            self.output.setParent(None)
            self.output.deleteLater()
        except RuntimeError:      # 底层 C++ 对象已被销毁
            pass
        self.clip_id = ""
        self.path = ""

    def pause(self) -> None:
        self.player.pause()


# ---------------------------------------------------------------------------
# 暂停抽帧：让 PROGRAM 在暂停时也有画面
# ---------------------------------------------------------------------------
class _PosterWorker(QThread):
    ready = Signal(int, str)

    def __init__(self, token: int, path: str, at_ms: int, out_path: Path,
                 grade: dict | None = None) -> None:
        super().__init__()
        self.token = token
        self.path = path
        self.at_ms = max(0, int(at_ms))
        self.out_path = Path(out_path)
        self.grade = dict(grade) if grade else None

    def run(self) -> None:  # noqa: D401
        if not self.path:
            return
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.grade:
            # 有调色时用 ffmpeg 精确渲染（曝光→eq→白平衡→锐化，见 grade_filters）
            filters = grade_filters(self.grade)
            filters.append("scale=1280:-2")
            ffmpeg = find_tool("ffmpeg")
            if ffmpeg:
                try:
                    subprocess.run(
                        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                         "-ss", f"{self.at_ms / 1000:.3f}", "-i", self.path,
                         "-frames:v", "1", "-vf", ",".join(filters),
                         "-q:v", "4", str(self.out_path)],
                        timeout=30, creationflags=_CREATE_NO_WINDOW,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except (OSError, subprocess.SubprocessError):
                    return
                if self.out_path.is_file() and self.out_path.stat().st_size > 0:
                    self.ready.emit(self.token, str(self.out_path))
            return
        # 原生工具优先（Media Foundation 抽帧），失败再退回 ffmpeg
        if native_frame(self.path, str(self.out_path), self.at_ms, 1280):
            self.ready.emit(self.token, str(self.out_path))
            return
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            return
        try:
            subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-ss", f"{self.at_ms / 1000:.3f}", "-i", self.path,
                 "-frames:v", "1", "-vf", "scale=1280:-2",
                 "-q:v", "4", str(self.out_path)],
                timeout=30, creationflags=_CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return
        if self.out_path.is_file() and self.out_path.stat().st_size > 0:
            self.ready.emit(self.token, str(self.out_path))


# ---------------------------------------------------------------------------
# 播放引擎
# ---------------------------------------------------------------------------
class PlaybackEngine(QObject):
    position_changed = Signal(int)
    playing_changed = Signal(bool)
    clip_changed = Signal(object)
    status = Signal(str)
    perf = Signal(str)

    def __init__(self, project: Project, canvas: PreviewCanvas | None = None,
                 parent=None, poster_dir: Path | None = None) -> None:
        super().__init__(parent)
        self._project = project
        self.canvas = canvas
        self._poster_dir = Path(poster_dir) if poster_dir else None

        self.video_player = QMediaPlayer(self)
        self.video_audio = QAudioOutput(self)
        self.video_audio.setVolume(0.8)
        self.video_player.setAudioOutput(self.video_audio)
        if canvas is not None:
            self.video_player.setVideoOutput(canvas.video)

        self._voices: dict[str, _Voice] = {}
        self._pending: dict[int, int] = {}
        self._video_clip_id = ""
        self._position = 0
        self._playing = False
        self._master_volume = 0.8
        self._loop = False
        self._anchor_pos = 0
        self._anchor_time = 0.0
        self._frame_count = 0
        self._stat_time = time.monotonic()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

        self.video_player.mediaStatusChanged.connect(self._on_media_status)
        self.video_player.errorOccurred.connect(self._on_video_error)
        self._grade_clip_id = ""

        # 暂停抽帧
        self._poster_token = 0
        self._poster_workers: list[_PosterWorker] = []
        self._poster_target: tuple[str, str, int, dict | None] | None = None
        self._poster_cache: dict[str, str] = {}   # (clip,ms,grade)->jpg 路径，避免拖动时反复重渲
        self._poster_timer = QTimer(self)
        self._poster_timer.setSingleShot(True)
        self._poster_timer.setInterval(220)
        self._poster_timer.timeout.connect(self._start_poster)

        # 多轨合成预览（B-04）
        self._composite_cache: dict[tuple, QImage] = {}
        self._composite_provider = None
        self._overlay_hint_shown = False

    # -- 工程 / 音量 ----------------------------------------------------
    def set_project(self, project: Project) -> None:
        self.pause()
        self._project = project
        self._position = 0
        self._video_clip_id = ""
        # 声部按 track.id 建键；换工程必须整组释放，否则反复开工程会无限增长（B-21）
        for voice in list(self._voices.values()):
            voice.stop()
        self._voices.clear()
        self.video_player.stop()
        self._grade_clip_id = ""
        self._composite_cache.clear()
        self._composite_provider = None
        self.set_grade(None)

    def set_master_volume(self, value: float) -> None:
        self._master_volume = max(0.0, min(1.0, float(value)))
        self._apply_volumes()

    def set_loop(self, enabled: bool) -> None:
        self._loop = bool(enabled)

    # -- 位置 ----------------------------------------------------------
    @property
    def position(self) -> int:
        return self._position

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(self) -> None:
        if self._playing:
            return
        if self._project.duration() <= 0:
            self.status.emit("时间线为空，无法播放")
            return
        if self._position >= self._project.duration():
            self._position = 0
        self._playing = True
        self._anchor_pos = self._position
        self._anchor_time = time.monotonic()
        self._frame_count = 0
        self._stat_time = self._anchor_time
        self._timer.start()
        self._sync(self._position, force=True)
        self.playing_changed.emit(True)

    def pause(self) -> None:
        was_playing = self._playing
        self._playing = False
        self._timer.stop()
        self.video_player.pause()
        for voice in list(self._voices.values()):
            voice.pause()
        if was_playing:
            self.playing_changed.emit(False)
        # 暂停后必须按当前位置重算一帧：否则"播放中 / 拖动 / 暂停"三段画面不一致（B-07）
        if self._project.duration() > 0:
            self._sync(self._position, force=True)

    def toggle(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def set_position(self, ms: int, preview: bool = True) -> None:
        ms = max(0, int(ms))
        self._position = ms
        if self._playing:
            self._anchor_pos = ms
            self._anchor_time = time.monotonic()
        if preview:
            self._sync(ms, force=False)

    def shutdown(self) -> None:
        self._timer.stop()
        self._poster_timer.stop()
        self._playing = False
        for worker in list(self._poster_workers):
            worker.wait(2000)
        self._poster_workers.clear()
        for voice in self._voices.values():
            voice.stop()
        self._voices.clear()
        self.video_player.stop()
        self.video_player.setSource(QUrl())

    # -- 时钟循环 ------------------------------------------------------
    def _tick(self) -> None:
        if not self._playing:
            return
        elapsed = int((time.monotonic() - self._anchor_time) * 1000)
        target = self._anchor_pos + elapsed
        total = self._project.duration()
        if total <= 0:
            self.pause()
            return
        if target >= total:
            if self._loop:
                self._anchor_pos = 0
                self._anchor_time = time.monotonic()
                self._position = 0
                self.position_changed.emit(0)
                self._sync(0, force=True)
                return
            self._position = total
            self.position_changed.emit(total)
            self.pause()
            self.status.emit("播放结束")
            return

        self._position = target
        self.position_changed.emit(target)
        self._sync(target)
        self._frame_count += 1
        self._report_perf()

    def _report_perf(self) -> None:
        now = time.monotonic()
        span = now - self._stat_time
        if span < 1.0:
            return
        fps = self._frame_count / span
        state = self.video_player.playbackState()
        note = "视频 正常" if state == QMediaPlayer.PlayingState else "视频 空闲"
        self.perf.emit(f"{fps:4.1f} Hz · {note}")
        self._frame_count = 0
        self._stat_time = now

    # -- 片段解析 ------------------------------------------------------
    def _video_clip_at(self, pos: int) -> Clip | None:
        result: Clip | None = None
        for track in self._project.video_tracks():
            if track.hidden:
                continue
            clip = track.clip_at(pos)
            if clip is not None and clip.enabled:
                result = clip     # 后面的轨道离观众更近
        return result

    def _audio_clips_at(self, pos: int) -> list[Clip]:
        result: list[Clip] = []
        for track in self._project.audio_tracks():
            if track.muted:
                continue
            clip = track.clip_at(pos)
            if clip is not None and clip.enabled:
                result.append(clip)
        return result

    # -- 同步 ----------------------------------------------------------
    def _sync(self, pos: int, force: bool = False) -> None:
        self._sync_video(pos, force)
        if self._playing:
            self._sync_audio(pos)
        elif force:
            self._sync_audio(pos)

    def _sync_video(self, pos: int, force: bool = False) -> None:
        # 多轨合成（B-04）：暂停/拖动时按"底→顶"逐层叠加，结果与导出层叠一致。
        # 播放中仍以 QMediaPlayer 的基底画面为准（叠层实时合成需要逐帧解码，
        # 本机 FFmpeg CLI 抽帧成本无法支撑实时），此差异在状态栏明确提示。
        layers = layer_stack(self._project, pos)
        if len(layers) > 1 and not self._playing:
            self._show_composite(pos, layers)
            return
        if len(layers) > 1 and self._playing and not self._overlay_hint_shown:
            self._overlay_hint_shown = True
            self.status.emit("播放中叠层以基底为准，暂停后显示完整多轨合成")

        clip = layers[-1] if layers else None
        if clip is None:
            if self._video_clip_id:
                self._release_video()
            if self.canvas is not None:
                self.canvas.show_empty()
            self._video_clip_id = ""
            self.clip_changed.emit(None)
            return

        asset = self._project.asset(clip.asset_id)
        is_image = (asset is not None and asset.kind == "image") or (
            Path(clip.path).suffix.lower() in IMAGE_EXTS)
        if is_image:
            if self._video_clip_id and self._video_clip_id != clip.id:
                self._release_video()
            if self.canvas is not None:
                self.canvas.show_still(clip.path, clip.name)
            self._set_grade_for(clip)
            self._video_clip_id = clip.id
            self.clip_changed.emit(clip)
            return

        if clip.id != self._video_clip_id or force:
            was_same = clip.id == self._video_clip_id
            self._load_video(clip, force=force or not was_same)
        if self.canvas is not None:
            self.canvas.show_video()
        if self._playing:
            # 播放中：QMediaPlayer 走原始画面，用半透明叠加近似调色
            self._set_grade_for(clip)
        else:
            # 暂停：定格帧会由 ffmpeg 精确渲染调色，先撤掉叠加避免调两层
            self._grade_clip_id = clip.id
            self.set_grade(None)

        expected = clip.source_time(pos)
        player = self.video_player
        actual = player.position()
        if self._playing and player.playbackState() != QMediaPlayer.PlayingState:
            player.play()
        if force or abs(actual - expected) > DRIFT_TOLERANCE_MS:
            if id(player) not in self._pending:
                player.setPosition(expected)
        if self._playing:
            self._poster_timer.stop()
        else:
            self._schedule_poster(clip, expected)

    # -- 暂停抽帧 ------------------------------------------------------
    def _show_composite(self, pos: int, layers: list[Clip]) -> None:
        """暂停/拖动时的多轨合成画面。"""
        top = layers[-1]
        if self._video_clip_id:
            self._release_video()
            self._video_clip_id = ""
        self._set_grade_for(top)
        image = self._composite_image(pos, layers)
        if self.canvas is not None and image is not None and not image.isNull():
            self.canvas.show_composite(image)
        self.clip_changed.emit(top)

    def _composite_image(self, pos: int, layers: list[Clip]) -> QImage | None:
        size = (max(1, self._project.width), max(1, self._project.height))
        if self._composite_provider is None:
            self._composite_provider = make_frame_provider(
                self._project, size, self._poster_dir)
        key = (tuple((clip.id, int(pos // 50) * 50,
                      grade_tag(clip.grade() if clip.is_graded else None))
                     for clip in layers), size)
        cached = self._composite_cache.get(key)
        if cached is not None:
            return cached
        image = render_composite(self._project, layers, pos, size,
                                 self._composite_provider)
        if len(self._composite_cache) > 48:
            self._composite_cache.clear()
        self._composite_cache[key] = image
        return image

    def _poster_key(self, clip_id: str, at_ms: int, grade: dict | None) -> str:
        g = tuple(sorted((grade or {}).items()))
        return f"{clip_id}|{at_ms}|{g}"

    def _schedule_poster(self, clip: Clip, source_ms: int) -> None:
        if self.canvas is None or self._poster_dir is None:
            return
        grade = clip.grade() if clip.is_graded else None
        # 同一帧（~50ms 精度）已有缓存就直接用，不重复起 ffmpeg 线程
        key = self._poster_key(clip.id, int(source_ms // 50) * 50, grade)
        cached = self._poster_cache.get(key)
        if cached and Path(cached).is_file():
            self.canvas.invalidate_still(cached)
            self.canvas.show_still(cached)
            return
        self._poster_target = (clip.id, clip.path, int(source_ms), grade)
        self._poster_timer.start()

    def _start_poster(self) -> None:
        target = self._poster_target
        if target is None or self._playing or self.canvas is None or self._poster_dir is None:
            return
        clip_id, path, at_ms, grade = target
        self._poster_token += 1
        # 文件名必须每帧唯一，否则 token%40 会在 40 帧后复用同名文件盖掉旧帧（B-19）
        out_path = self._poster_dir / f"poster_{self._poster_token:06d}.jpg"
        # 写入键与 `_schedule_poster` 的查表键必须同一量化口径，否则缓存永不命中（B-08）
        self._poster_started = (self._poster_key(clip_id, int(at_ms // 50) * 50, grade),
                                str(out_path))
        self._prune_poster_files()
        worker = _PosterWorker(self._poster_token, path, at_ms, out_path, grade)
        worker.ready.connect(self._on_poster)
        worker.finished.connect(lambda w=worker: self._prune_posters())
        self._poster_workers.append(worker)
        worker.start()

    def _prune_poster_files(self) -> None:
        """只保留最近 40 个抽帧文件，避免缓存目录无限膨胀。"""
        if self._poster_dir is None:
            return
        keep_from = self._poster_token - POSTER_FILE_LIMIT
        for item in self._poster_dir.glob("poster_*.jpg"):
            stem = item.stem.split("_")[-1]
            if stem.isdigit() and int(stem) < keep_from:
                try:
                    item.unlink()
                except OSError:
                    pass

    def _prune_posters(self) -> None:
        for worker in list(self._poster_workers):
            if not worker.isRunning():
                self._poster_workers.remove(worker)

    def _remember_poster(self, key: str, path: str) -> None:
        """写入抽帧缓存，超出上限先丢最旧的（B-20）。"""
        self._poster_cache.pop(key, None)
        self._poster_cache[key] = path
        while len(self._poster_cache) > POSTER_CACHE_LIMIT:
            self._poster_cache.pop(next(iter(self._poster_cache)), None)

    def _on_poster(self, token: int, path: str) -> None:
        if self._playing or self.canvas is None:
            return
        if self._poster_token - token > 2:
            return
        self.canvas.invalidate_still(path)   # 同名文件会被覆盖，缓存必须失效
        self.canvas.show_still(path)
        started = getattr(self, "_poster_started", None)
        if started:
            self._remember_poster(started[0], path)

    def _load_video(self, clip: Clip, force: bool = False) -> None:
        self._video_clip_id = clip.id
        self._pending[id(self.video_player)] = clip.source_time(self._position)
        if force or self.video_player.source().toLocalFile() != clip.path:
            self.video_player.setSource(QUrl.fromLocalFile(clip.path))
        volume = self._clip_gain(clip)
        self.video_audio.setVolume(volume)
        # 变速预览：QMediaPlayer 支持 playbackRate（0.1~8）
        try:
            self.video_player.setPlaybackRate(max(0.1, min(4.0, clip.speed)))
        except (AttributeError, TypeError):
            pass
        if self._playing:
            self.video_player.play()

    def _release_video(self) -> None:
        self.video_player.stop()
        self.video_player.setSource(QUrl())

    def _sync_audio(self, pos: int) -> None:
        for track in self._project.audio_tracks():
            voice = self._voices.get(track.id)
            clip = None if track.muted else track.clip_at(pos)
            if clip is not None and not clip.enabled:
                clip = None
            if clip is None:
                if voice is not None and voice.clip_id:
                    voice.player.pause()
                    voice.clip_id = ""
                continue
            if voice is None:
                voice = _Voice(self)
                self._voices[track.id] = voice
                voice.player.mediaStatusChanged.connect(self._on_media_status)
            if voice.clip_id != clip.id or voice.path != clip.path:
                voice.clip_id = clip.id
                voice.path = clip.path
                voice.output.setVolume(self._clip_gain(clip))
                self._pending[id(voice.player)] = clip.source_time(pos)
                voice.player.setSource(QUrl.fromLocalFile(clip.path))
                if self._playing:
                    voice.player.play()
                continue
            voice.output.setVolume(self._clip_gain(clip))
            expected = clip.source_time(pos)
            if self._playing and voice.player.playbackState() != QMediaPlayer.PlayingState:
                voice.player.play()
            if abs(voice.player.position() - expected) > DRIFT_TOLERANCE_MS:
                if id(voice.player) not in self._pending:
                    voice.player.setPosition(expected)

    def _clip_gain(self, clip: Clip) -> float:
        # 值域与上限来自 limits 单点定义；QAudioOutput 无法超过 1.0（B-15/B-22）
        return preview_gain(self._master_volume, clip.volume)

    def _apply_volumes(self) -> None:
        clip = self._video_clip_at(self._position)
        self.video_audio.setVolume(self._clip_gain(clip) if clip else self._master_volume)
        for track in self._project.audio_tracks():
            voice = self._voices.get(track.id)
            if voice is None:
                continue
            clip_at = track.clip_at(self._position)
            try:
                voice.output.setVolume(
                    self._clip_gain(clip_at) if clip_at else self._master_volume)
            except RuntimeError:      # 声部已释放（换工程后）
                continue

    # -- 播放器回调 ----------------------------------------------------
    def _on_media_status(self, status) -> None:
        player = self.sender()
        if player is None:
            return
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            target = self._pending.pop(id(player), None)
            if target is not None:
                player.setPosition(int(max(0, target)))
                if self._playing:
                    player.play()

    def _on_video_error(self, *_args) -> None:
        # 出错的 player 必须清掉 _pending，否则该 player 之后的 setPosition 永远被跳过（B-18）
        player = self.sender()
        for pid in {id(self.video_player), id(player) if player is not None else None}:
            if pid is not None:
                self._pending.pop(pid, None)
        self.status.emit("视频解码错误，已跳过该片段")

    # -- 调色预览 ------------------------------------------------------
    def set_grade(self, grade: dict | None) -> None:
        if self.canvas is None:
            return
        self.canvas.apply_grade_preview(grade)

    def _set_grade_for(self, clip: Clip) -> None:
        """只在切换片段时刷新调色叠加层，避免每帧重设样式。"""
        if clip.id == self._grade_clip_id:
            return
        self._grade_clip_id = clip.id
        self.set_grade(clip.grade() if clip.is_graded else None)
