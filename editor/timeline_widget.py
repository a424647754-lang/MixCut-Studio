"""多轨时间线控件：绘制轨道/片段/播放头，支持点击、拖动、修剪、刀片、缩放。

本阶段补全（相对第一阶段骨架）：
  - 轨道头可点击控件：显示/隐藏、静音、锁定（无需菜单）
  - 从媒体库拖放素材到指定轨道（自定义 MIME）
  - 片段可跨轨道拖动（同类型轨道之间）
  - 复制 / 剪切 / 粘贴、全选、按帧微移
  - 标记的添加、跳转、删除
  - 播放头跟随（自动横向滚动）
"""

from __future__ import annotations

import copy

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygon, QPolygonF
from PySide6.QtWidgets import QAbstractScrollArea, QMenu

from . import Clip, Marker, Project, Track, format_timecode, snap_to_frame, new_id
from . import ui_theme as T
from .media_panel import ASSET_MIME
from .model import (
    LambdaCommand,
    TrackEditCommand,
    TrackListCommand,
    TrackStateCommand,
    overwrite_layout,
    snapshot_clips,
    snapshot_tracks,
    split_track,
)

HEADER_W = 132
RULER_H = 26
GAP = 2
HEADER_BTN = 18
HEADER_BTN_GAP = 3

# 时间线配色取自全局设计令牌，保证与其它面板视觉一致
COL_BG = QColor(T.TL_BG)
COL_LANE = QColor(T.TL_LANE)
COL_LANE_ALT = QColor(T.TL_LANE_ALT)
COL_HEADER = QColor(T.TL_HEADER)
COL_BORDER = QColor(T.BORDER)
COL_TEXT = QColor(T.TEXT)
COL_TEXT_DIM = QColor(T.TEXT_DIM)
COL_VIDEO = QColor(T.TL_CLIP_VIDEO)
COL_AUDIO = QColor(T.TL_CLIP_AUDIO)
COL_SELECT = QColor(T.ACCENT)
COL_PLAYHEAD = QColor(T.TL_PLAYHEAD)
COL_WAVE = QColor(T.TL_WAVE)
COL_WARN = QColor(T.WARN)
COL_TARGET = QColor(T.ACCENT_DIM)
COL_MUTED = QColor(T.TEXT_MUTED)

TRACK_HEIGHTS = (48, 64, 96)


class TimelineWidget(QAbstractScrollArea):
    """时间线。像素 <-> 时间：x = HEADER_W + (ms * pps / 1000) - scroll。"""

    playhead_changed = Signal(int)
    selection_changed = Signal(list)
    status = Signal(str)
    project_edited = Signal()
    track_changed = Signal()
    source_open_requested = Signal(str)
    interaction_changed = Signal(bool)   # 拖动/修剪进行中（自动保存据此冻结，B-30）
    zoom_changed = Signal(float)         # 缩放比例变化（编辑页读数跟随，B-41）

    def __init__(self, project: Project, undo, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.undo = undo
        self.playhead = 0
        self.pixels_per_sec = 100.0
        self._selection: list[str] = []
        self._thumbnails: dict[str, QPixmap] = {}
        self._waveforms: dict[str, list[float]] = {}
        self.snap_enabled = True
        self.blade_mode = False
        self.follow_playhead = True
        self._clipboard: list[dict] = []

        self._drag = None          # None | 'playhead' | 'move' | 'trim_in' | 'trim_out'
        self._drag_start_pt = QPoint()
        self._drag_clip_id = ""
        self._drag_orig: tuple[int, int, int] | None = None
        self._drag_before: list[Clip] | None = None   # 按下时的深拷贝快照（B-01）
        self._live: tuple[int, int, int] | None = None
        self._drag_target_index: int | None = None
        self._drop_preview: tuple[int, int] | None = None
        self._hover_header: tuple[int, str] | None = None

        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.horizontalScrollBar().setSingleStep(40)
        self.verticalScrollBar().setSingleStep(20)
        self.project_edited.connect(self.refresh)

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------
    def set_project(self, project: Project) -> None:
        """换工程（装载/新建）：清掉选择、游标、缓存，并**通知**下游。

        这是"重新装载"语义。编辑后的刷新请用 `rebind_project`，
        否则每次编辑都会把用户的选择、播放头和缩略图缓存一起抹掉（B-05）。
        """
        had_selection = bool(self._selection)
        self.project = project
        self._normalize_track_order()
        self._selection.clear()
        self._thumbnails.clear()
        self._waveforms.clear()
        self.playhead = 0
        self._drag = None
        self._drag_orig = None
        self._drag_before = None
        self._live = None
        self._drag_target_index = None
        self.refresh()
        if had_selection:
            # 清态必须发信号，否则 Inspector/调色页会停在上一个工程的内容
            self.selection_changed.emit([])

    def rebind_project(self, project: Project) -> None:
        """同一工程对象的**视图刷新**：保留选择、播放头与缩略图/波形缓存。

        与 `set_project` 的唯一区别是"不清态"；已被删除的片段会从选择里剔除，
        并只在集合真的变化时发 `selection_changed`。
        """
        self.project = project
        # 刷新路径**不得**重排轨道或改轨道名：曾在这里做归一化，导致每次编辑后
        # 轨道顺序/命名被静默改写（B-28）。归一化只在装载时做一次（set_project）。
        alive = {clip.id for track in project.tracks for clip in track.clips}
        before = list(self._selection)
        kept = [clip_id for clip_id in before if clip_id in alive]
        self._selection = kept
        self.playhead = max(0, min(self.playhead, project.duration()))
        self._drag = None
        self._drag_orig = None
        self._drag_before = None
        self._live = None
        self._drag_target_index = None
        self.refresh()
        if kept != before:
            self.selection_changed.emit(list(kept))

    def _normalize_track_order(self) -> None:
        """保证视频轨都在音频轨之上（老工程/手工构造的工程可能顺序错乱）。"""
        videos = [t for t in self.project.tracks if t.kind != "audio"]
        audios = [t for t in self.project.tracks if t.kind == "audio"]
        ordered = videos + audios
        if ordered != self.project.tracks:
            self.project.tracks = ordered
            self._renumber_tracks()

    def set_thumbnails(self, thumbs: dict[str, QPixmap]) -> None:
        self._thumbnails = thumbs
        self.viewport().update()

    def set_waveforms(self, waves: dict[str, list[float]]) -> None:
        self._waveforms = waves
        self.viewport().update()

    def set_playhead(self, ms: int, notify: bool = True) -> None:
        ms = max(0, int(ms))
        if ms == self.playhead:
            return
        self.playhead = ms
        if self.follow_playhead:
            self._ensure_visible(ms)
        self.viewport().update()
        if notify:
            self.playhead_changed.emit(ms)

    def set_follow_playhead(self, enabled: bool) -> None:
        self.follow_playhead = bool(enabled)

    def _ensure_visible(self, ms: int) -> None:
        x = self.x_for_time(ms)
        view_w = self.viewport().width()
        if x < HEADER_W + 20:
            delta = x - (HEADER_W + 20)
            self.horizontalScrollBar().setValue(
                max(0, self.horizontalScrollBar().value() + delta))
        elif x > view_w - 20:
            delta = x - (view_w - 20)
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() + delta)

    def selection(self) -> list[str]:
        return list(self._selection)

    def selected_clips(self) -> list[Clip]:
        result = []
        for clip_id in self._selection:
            clip = self.project.locate(clip_id)[1]
            if clip is not None:
                result.append(clip)
        return result

    def select(self, ids: list[str]) -> None:
        self._selection = list(ids)
        self.viewport().update()
        self.selection_changed.emit(self._selection)

    def select_all(self) -> None:
        ids = [clip.id for track in self.project.tracks if not track.locked
               for clip in track.clips]
        self.select(ids)

    def clear_selection(self) -> None:
        self.select([])

    def refresh(self) -> None:
        self._update_scrollbars()
        self.viewport().update()

    def zoom_in(self) -> None:
        self._zoom(self.pixels_per_sec * 1.25)

    def zoom_out(self) -> None:
        self._zoom(self.pixels_per_sec / 1.25)

    def zoom_fit(self) -> None:
        duration = max(1000, self.project.duration())
        avail = max(80, self.viewport().width() - HEADER_W - 20)
        self._zoom(avail * 1000.0 / duration)
        self.horizontalScrollBar().setValue(0)

    def _zoom(self, pps: float) -> None:
        self.pixels_per_sec = max(4.0, min(4000.0, pps))
        self.refresh()
        self.zoom_changed.emit(self.pixels_per_sec)

    # ------------------------------------------------------------------
    # 几何换算
    # ------------------------------------------------------------------
    def x_for_time(self, ms: int) -> int:
        return HEADER_W + int(ms * self.pixels_per_sec / 1000.0) - self.horizontalScrollBar().value()

    def time_for_x(self, x: int) -> int:
        rel = x - HEADER_W + self.horizontalScrollBar().value()
        return max(0, int(rel * 1000.0 / self.pixels_per_sec))

    def _visual_indices(self) -> list[int]:
        """时间线视觉顺序（自上而下）。

        视频块在上、音频块在下；视频块内部**倒序**：列表里越靠后 = 层级越高
        = 画在越上面。这样界面层级与合成顺序（基底 → 逐层叠加）一致，
        V1 是基底轨、显示在视频块最底部（B-06）。
        """
        videos = [i for i, t in enumerate(self.project.tracks) if t.kind != "audio"]
        audios = [i for i, t in enumerate(self.project.tracks) if t.kind == "audio"]
        return list(reversed(videos)) + audios

    def track_y(self, index: int) -> int:
        y = RULER_H + GAP - self.verticalScrollBar().value()
        for i in self._visual_indices():
            if i == index:
                return y
            y += self.project.tracks[i].height + GAP
        return y

    def track_at(self, y: int) -> int:
        if y < RULER_H:
            return -1
        cursor = RULER_H + GAP - self.verticalScrollBar().value()
        for i in self._visual_indices():
            height = self.project.tracks[i].height
            if cursor <= y < cursor + height:
                return i
            cursor += height + GAP
        return -1

    def clip_rect(self, track_index: int, clip: Clip) -> QRect:
        y = self.track_y(track_index)
        h = self.project.tracks[track_index].height
        left = self.x_for_time(clip.start)
        right = self.x_for_time(clip.end)
        return QRect(left, y + 1, max(3, right - left), max(6, h - 2))

    def _update_scrollbars(self) -> None:
        duration = self.project.duration()
        content_w = int(duration * self.pixels_per_sec / 1000.0) + 400
        view_w = max(0, self.viewport().width() - HEADER_W)
        self.horizontalScrollBar().setRange(0, max(0, content_w - view_w))
        self.horizontalScrollBar().setPageStep(view_w)

        content_h = RULER_H + GAP + sum(t.height + GAP for t in self.project.tracks)
        self.verticalScrollBar().setRange(0, max(0, content_h - self.viewport().height()))
        self.verticalScrollBar().setPageStep(self.viewport().height())

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), COL_BG)
        self._paint_ruler(painter)
        self._paint_tracks(painter)
        self._paint_drop_preview(painter)

        x = self.x_for_time(self.playhead)
        if x >= HEADER_W:
            painter.setPen(QPen(COL_PLAYHEAD, 1.5))
            painter.drawLine(x, 0, x, self.viewport().height())
            painter.setBrush(COL_PLAYHEAD)
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(QPolygon([QPoint(x - 6, 0), QPoint(x + 6, 0), QPoint(x, 10)]))

        if not self.project.tracks:
            painter.setPen(COL_TEXT_DIM)
            painter.drawText(self.viewport().rect(), Qt.AlignCenter, "时间线为空")

    def _paint_ruler(self, painter: QPainter) -> None:
        rect = QRect(0, 0, self.viewport().width(), RULER_H)
        painter.fillRect(rect, QColor(T.TL_RULER))
        painter.setPen(QPen(COL_BORDER, 1))
        painter.drawLine(0, RULER_H - 1, rect.width(), RULER_H - 1)

        fps = self.project.fps_fraction
        # 选择刻度间隔，保证标签间距 >= 90 像素
        steps = [100, 250, 500, 1000, 2000, 5000, 10000, 15000, 30000,
                 60000, 120000, 300000, 600000]
        step = steps[-1]
        for candidate in steps:
            if candidate * self.pixels_per_sec / 1000.0 >= 90:
                step = candidate
                break

        start = self.time_for_x(HEADER_W)
        end = self.time_for_x(self.viewport().width())
        first = (start // step) * step
        painter.setFont(QFont("Consolas", 8))
        t = first
        while t <= end:
            x = self.x_for_time(t)
            if x >= HEADER_W:
                painter.setPen(COL_BORDER)
                painter.drawLine(x, RULER_H - 8, x, RULER_H)
                painter.setPen(COL_TEXT_DIM)
                painter.drawText(x + 3, RULER_H - 14, 100, 13,
                                 Qt.AlignLeft | Qt.AlignVCenter,
                                 format_timecode(t, fps))
            t += step

        painter.fillRect(QRect(0, 0, HEADER_W, RULER_H), COL_HEADER)
        painter.setPen(COL_TEXT_DIM)
        painter.drawText(8, 0, HEADER_W - 12, RULER_H, Qt.AlignLeft | Qt.AlignVCenter,
                         f"{float(fps):.2f} fps")

        # 标记
        for marker in self.project.markers:
            x = self.x_for_time(marker.time)
            if HEADER_W <= x <= rect.width():
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(T.TL_MARKER))
                painter.drawPolygon(QPolygon([
                    QPoint(x, RULER_H - 12), QPoint(x + 7, RULER_H - 12), QPoint(x + 7, 4),
                ]))
                painter.setPen(COL_TEXT)
                painter.drawText(x + 9, 1, 70, 11, Qt.AlignLeft | Qt.AlignVCenter, marker.label)

    def _paint_tracks(self, painter: QPainter) -> None:
        width = self.viewport().width()
        height = self.viewport().height()
        for index, track in enumerate(self.project.tracks):
            y = self.track_y(index)
            h = track.height
            if y + h < RULER_H or y > height:
                continue

            lane = COL_LANE if index % 2 == 0 else COL_LANE_ALT
            painter.fillRect(QRect(HEADER_W, y, width - HEADER_W, h), lane)
            painter.fillRect(QRect(0, y, HEADER_W, h), COL_HEADER)
            painter.setPen(COL_BORDER)
            painter.drawLine(0, y + h, width, y + h)
            painter.drawLine(HEADER_W - 1, y, HEADER_W - 1, y + h)

            # 轨道类型色条
            painter.setPen(Qt.NoPen)
            painter.setBrush(COL_VIDEO if track.kind == "video" else COL_AUDIO)
            painter.drawRect(0, y, 3, h)

            toggled = track.hidden if track.kind == "video" else track.muted
            painter.setPen(COL_TEXT_DIM if toggled else COL_TEXT)
            painter.setFont(QFont(T.resolve_families(T.FONT_UI_FAMILIES)[0],
                                  9, QFont.Weight.Bold))
            painter.drawText(9, y + 4, HEADER_W - 18, 16,
                             Qt.AlignLeft | Qt.AlignVCenter, track.name)

            self._paint_header_buttons(painter, index)

            for clip in track.clips:
                box = self.clip_rect(index, clip)
                if box.right() < HEADER_W or box.left() > width:
                    continue
                visible = QRect(max(box.left(), HEADER_W), box.top(),
                                box.right() - max(box.left(), HEADER_W), box.height())
                self._paint_clip(painter, visible, track, clip)

        # 拖动到其它轨道时的目标提示
        if self._drag == "move" and self._drag_target_index is not None:
            y = self.track_y(self._drag_target_index)
            h = self.project.tracks[self._drag_target_index].height
            painter.setPen(QPen(COL_TARGET, 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRect(HEADER_W, y + 1, width - HEADER_W, h - 2))

    def _header_button_rects(self, index: int) -> dict[str, QRect]:
        track = self.project.tracks[index]
        y = self.track_y(index)
        bottom = y + track.height - HEADER_BTN - 5
        rects: dict[str, QRect] = {}
        x = HEADER_W - 6 - HEADER_BTN
        rects["lock"] = QRect(x, bottom, HEADER_BTN, HEADER_BTN)
        x -= HEADER_BTN + HEADER_BTN_GAP
        rects["toggle"] = QRect(x, bottom, HEADER_BTN, HEADER_BTN)
        return rects

    def _paint_header_buttons(self, painter: QPainter, index: int) -> None:
        track = self.project.tracks[index]
        rects = self._header_button_rects(index)
        toggled = track.hidden if track.kind == "video" else track.muted

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        # 开关按钮（视频=显示/隐藏，音频=静音）
        self._paint_icon(painter, rects["toggle"],
                         "eye" if track.kind == "video" else "mute",
                         COL_WARN if toggled else COL_TEXT_DIM)
        self._paint_icon(painter, rects["lock"], "lock",
                         COL_WARN if track.locked else COL_MUTED)
        painter.restore()

        if self._hover_header == (index, "toggle"):
            painter.setPen(QPen(COL_BORDER, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rects["toggle"])
        elif self._hover_header == (index, "lock"):
            painter.setPen(QPen(COL_BORDER, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rects["lock"])

    def _paint_icon(self, painter: QPainter, rect: QRect, kind: str, color: QColor) -> None:
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(Qt.NoBrush)
        cx = rect.center().x()
        cy = rect.center().y()
        side = rect.width() - 6
        if kind == "eye":
            painter.drawEllipse(QRect(cx - side // 2, cy - side // 3, side, side // 2 * 1))
        elif kind == "mute":
            # 喇叭轮廓 + 声波（静音时用斜线划掉）
            painter.drawLine(cx - side // 2, cy - 3, cx - side // 4, cy - 3)
            painter.drawLine(cx - side // 4, cy - 3, cx + 2, cy - 7)
            painter.drawLine(cx + 2, cy - 7, cx + 2, cy + 7)
            painter.drawLine(cx + 2, cy + 7, cx - side // 4, cy + 3)
            painter.drawLine(cx - side // 4, cy + 3, cx - side // 2, cy + 3)
            painter.drawLine(cx - side // 2, cy + 3, cx - side // 2, cy - 3)
            painter.drawArc(QRect(cx, cy - side // 2, side // 2, side), -60 * 16, 120 * 16)
        elif kind == "lock":
            painter.drawRect(QRect(cx - side // 3, cy - 1, side // 2, side // 2))
            painter.drawArc(QRect(cx - side // 5, cy - side // 2, side // 2, side // 2),
                            0, 180 * 16)

    def _paint_drop_preview(self, painter: QPainter) -> None:
        if self._drop_preview is None:
            return
        index, ms = self._drop_preview
        if index < 0 or index >= len(self.project.tracks):
            return
        x = self.x_for_time(ms)
        y = self.track_y(index)
        h = self.project.tracks[index].height
        painter.setPen(QPen(COL_SELECT, 1, Qt.DashLine))
        painter.setBrush(QColor(59, 130, 246, 40))
        painter.drawRect(QRect(x, y + 1, 3, h - 2))
        painter.setPen(QPen(COL_SELECT, 1, Qt.DotLine))
        painter.drawRect(QRect(HEADER_W, y + 1, self.viewport().width() - HEADER_W, h - 2))

    def _paint_clip(self, painter: QPainter, box: QRect, track: Track, clip: Clip) -> None:
        base = COL_VIDEO if track.kind == "video" else COL_AUDIO
        if not clip.enabled:
            base = base.darker(170)
        if clip.id in self._selection:
            base = base.lighter(140)

        painter.setPen(Qt.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(box, 3, 3)

        if clip.id in self._selection:
            painter.setPen(QPen(COL_SELECT, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box, 3, 3)

        if track.kind == "video":
            thumb = self._thumbnails.get(clip.asset_id)
            if thumb is not None and not thumb.isNull():
                painter.setClipRect(box)
                tile_w = max(24, thumb.width())
                x = box.left()
                while x < box.right():
                    painter.drawPixmap(x, box.top(), thumb.scaledToHeight(box.height(),
                                                                         Qt.SmoothTransformation))
                    x += tile_w
                painter.setClipping(False)
        else:
            peaks = self._waveforms.get(clip.asset_id)
            if peaks:
                painter.setClipRect(box)
                painter.setPen(QPen(COL_WAVE, 1))
                center = box.center().y()
                span = max(1, clip.source_span)
                for px in range(box.left(), box.right()):
                    ms_in_clip = (px - box.left()) * span // max(1, box.width())
                    index = int((clip.source_in + ms_in_clip) * len(peaks) // max(1, span))
                    index = max(0, min(len(peaks) - 1, index))
                    amp = peaks[index] * (box.height() / 2 - 1)
                    painter.drawLine(px, int(center - amp), px, int(center + amp))
                painter.setClipping(False)

        painter.setPen(COL_TEXT)
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        label = clip.name or "片段"
        painter.drawText(box.adjusted(5, 2, -4, 0), Qt.AlignLeft | Qt.AlignTop,
                         painter.fontMetrics().elidedText(label, Qt.ElideRight,
                                                          max(10, box.width() - 10)))

        # 状态角标：变速 / 调色 / 禁用
        badges = []
        if abs(clip.speed - 1.0) > 1e-3:
            badges.append(f"{clip.speed:.2f}×")
        if clip.is_graded:
            badges.append("调色")
        if not clip.enabled:
            badges.append("停用")
        if badges and box.width() > 60:
            painter.setPen(QColor(T.TEXT_STRONG))
            painter.drawText(box.adjusted(4, 0, -4, -2), Qt.AlignLeft | Qt.AlignBottom,
                             " · ".join(badges))

    # ------------------------------------------------------------------
    # 鼠标
    # ------------------------------------------------------------------
    def _hit(self, pos: QPoint) -> tuple[int, Clip | None, bool, bool]:
        if pos.y() < RULER_H:
            return -1, None, False, False
        if pos.x() < HEADER_W:
            return -1, None, False, False
        index = self.track_at(pos.y())
        if index < 0:
            return -1, None, False, False
        track = self.project.tracks[index]
        for clip in track.clips:
            box = self.clip_rect(index, clip)
            if box.contains(pos):
                return (index, clip,
                        pos.x() - box.left() <= 6,
                        box.right() - pos.x() <= 6)
        return index, None, False, False

    def _header_hit(self, pos: QPoint) -> tuple[int, str] | None:
        if pos.x() >= HEADER_W or pos.y() < RULER_H:
            return None
        index = self.track_at(pos.y())
        if index < 0:
            return None
        for key, rect in self._header_button_rects(index).items():
            if rect.contains(pos):
                return index, key
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if pos.y() < RULER_H:
            self.set_playhead(self.time_for_x(pos.x()))
            self._drag = "playhead"
            return

        header = self._header_hit(pos)
        if header is not None:
            self._toggle_track_flag(header[0], header[1])
            return

        index, clip, left_edge, right_edge = self._hit(pos)
        if index < 0:
            return
        track = self.project.tracks[index]

        if clip is None:
            if not (event.modifiers() & Qt.ShiftModifier):
                self.select([])
            return

        if track.locked:
            self.status.emit("轨道已锁定")
            return

        if self.blade_mode:
            self._blade(track, snap_to_frame(self._snap(self.time_for_x(pos.x())),
                                            self.project.fps_fraction))
            return

        if event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
            ids = list(self._selection)
            if clip.id in ids:
                ids.remove(clip.id)
            else:
                ids.append(clip.id)
            self.select(ids)
            return

        if clip.id not in self._selection:
            self.select([clip.id])

        self._drag_clip_id = clip.id
        self._drag_orig = (clip.start, clip.duration, clip.source_in)
        self._drag_before = snapshot_clips(track.clips)   # 必须在任何就地修改之前
        self._live = None
        self._drag_target_index = index
        self._drag_start_pt = pos
        self._drag = "trim_in" if left_edge else ("trim_out" if right_edge else "move")
        self.interaction_changed.emit(True)   # 拖动期间冻结自动保存（B-30）

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._drag is None:
            header = self._header_hit(pos)
            if header != self._hover_header:
                self._hover_header = header
                self.viewport().update()
            _, _, left_edge, right_edge = self._hit(pos)
            if left_edge or right_edge:
                self.viewport().setCursor(Qt.SplitHCursor)
            elif pos.y() < RULER_H:
                self.viewport().setCursor(Qt.SizeHorCursor)
            elif pos.x() < HEADER_W:
                self.viewport().setCursor(Qt.PointingHandCursor if header else Qt.ArrowCursor)
            else:
                self.viewport().setCursor(Qt.CrossCursor if self.blade_mode else Qt.ArrowCursor)
            return

        if self._drag == "playhead":
            self.set_playhead(self._snap(self.time_for_x(pos.x())))
            return

        track, clip = self.project.locate(self._drag_clip_id)
        if clip is None or self._drag_orig is None:
            return
        orig_start, orig_dur, orig_in = self._drag_orig
        delta = self.time_for_x(pos.x()) - self.time_for_x(self._drag_start_pt.x())

        if self._drag == "move":
            new_start = self._snap(max(0, orig_start + delta))
            self._live = (new_start, orig_dur, orig_in)
            self._drag_target_index = self._compatible_track_index(pos.y(), track.kind)
            self.status.emit(f"移动 {new_start - orig_start:+d} ms")
        elif self._drag == "trim_in":
            new_start = self._snap(max(0, orig_start + delta))
            # 修剪是"阻挡式"的：不能吃掉相邻片段，最多贴到它的尾部
            prev_end = max((c.end for c in track.clips
                            if c.id != clip.id and c.end <= orig_start), default=0)
            new_start = max(new_start, prev_end)
            new_dur = orig_dur - (new_start - orig_start)
            if new_dur < 50:
                return
            shift = new_start - orig_start
            self._live = (new_start, new_dur, max(0, orig_in + int(shift * clip.speed)))
        else:  # trim_out
            new_end = self._snap(orig_start + orig_dur + delta)
            # 同理：右边缘不能越过下一个片段的头部
            next_start = min((c.start for c in track.clips
                              if c.id != clip.id and c.start >= orig_start + orig_dur),
                             default=None)
            if next_start is not None:
                new_end = min(new_end, next_start)
            new_dur = new_end - orig_start
            if new_dur < 50:
                return
            self._live = (orig_start, new_dur, orig_in)

        # 拖动过程中直接改模型做实时预览（release 时统一记入撤销栈）
        clip.start, clip.duration, clip.source_in = self._live
        track.clips.sort(key=lambda c: c.start)
        self.viewport().update()

    def _compatible_track_index(self, y: int, kind: str) -> int | None:
        index = self.track_at(y)
        if index < 0:
            return None
        track = self.project.tracks[index]
        if track.kind != kind or track.locked:
            return None
        return index

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        mode = self._drag
        self._drag = None
        if mode in ("move", "trim_in", "trim_out"):
            self.interaction_changed.emit(False)
        if (mode in ("move", "trim_in", "trim_out")
                and self._drag_orig is not None and self._live is not None):
            track, clip = self.project.locate(self._drag_clip_id)
            if clip is not None and self._live != self._drag_orig:
                applied = (clip.start, clip.duration, clip.source_in)
                if mode == "move" and self._drag_target_index is not None:
                    source_index = self.project.tracks.index(track) \
                        if track in self.project.tracks else -1
                    if (self._drag_target_index != source_index
                            and 0 <= self._drag_target_index < len(self.project.tracks)):
                        self._finish_cross_track_move(track, clip, applied,
                                                      self._drag_target_index)
                        self._drag_orig = None
                        self._drag_before = None
                        self._live = None
                        self._drag_target_index = None
                        self.refresh()
                        return
                # 快照必须取"拖动前"状态：_drag_before 在按下时已深拷贝，
                # 兜底路径用 _drag_orig 把被拖片段还原后再深拷贝。
                before = self._drag_before
                if before is None:
                    before = self._before_from_drag_orig(track, clip)
                # 覆盖式落位：与其它片段重叠时切分/移除被覆盖部分，
                # 不允许同轨出现未定义重叠（B-10）
                clip.start, clip.duration, clip.source_in = applied
                others = [c for c in track.clips if c.id != clip.id]
                track.clips = overwrite_layout(others, clip)
                track.clips.sort(key=lambda c: c.start)
                command = TrackEditCommand(self.project, "移动/修剪片段")
                command.record(track.id, before)
                self.undo.push(command)
                self.project_edited.emit()
        self._drag_orig = None
        self._drag_before = None
        self._live = None
        self._drag_target_index = None
        self.refresh()

    def _before_from_drag_orig(self, track: Track, clip: Clip) -> list[Clip]:
        """无按下快照时（程序化调用/异常路径），用拖动起点还原出独立快照。"""
        snap = snapshot_clips(track.clips)
        for item in snap:
            if item.id == clip.id and self._drag_orig is not None:
                item.start, item.duration, item.source_in = self._drag_orig
                break
        return snap

    def _finish_cross_track_move(self, source_track: Track, clip: Clip,
                                 applied: tuple[int, int, int], target_index: int) -> None:
        target = self.project.tracks[target_index]
        # 源轨快照用按下时的深拷贝（此刻 clip 已被实时预览改到 applied 位置）；
        # 目标轨此刻还没被改动，直接深拷贝即可。
        before_source = self._drag_before
        if before_source is None:
            before_source = self._before_from_drag_orig(source_track, clip)
        before_target = snapshot_clips(target.clips)

        moved = Clip.from_dict(clip.to_dict())
        moved.id = new_id()
        moved.start, moved.duration, moved.source_in = applied
        source_track.clips.remove(clip)

        after_source = sorted(source_track.clips, key=lambda c: c.start)
        after_target = overwrite_layout(target.clips, moved)
        source_track.clips = after_source
        target.clips = after_target

        command = TrackEditCommand(self.project, "移动到其它轨道")
        command.record(source_track.id, before_source, after_source)
        command.record(target.id, before_target, after_target)
        self.undo.push(command)
        self.select([moved.id])
        self.project_edited.emit()
        self.status.emit(f"已移动到 {target.name}")

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        index = self.track_at(pos.y())
        if pos.x() < HEADER_W and index >= 0:
            track = self.project.tracks[index]
            heights = list(TRACK_HEIGHTS)
            current = track.height
            next_height = heights[(heights.index(current) + 1) % len(heights)] \
                if current in heights else heights[0]
            before = TrackStateCommand.snapshot(track)
            track.height = next_height
            self._push_track_state(track, before)
            return
        index, clip, _, _ = self._hit(pos)
        if clip is not None:
            self.source_open_requested.emit(clip.asset_id)
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        step = self.project.fps_fraction
        frame_ms = max(1, round(1000 * step.denominator / step.numerator))
        if event.key() == Qt.Key_Left:
            self.set_playhead(self.playhead - frame_ms)
        elif event.key() == Qt.Key_Right:
            self.set_playhead(self.playhead + frame_ms)
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        pos = event.pos()
        if pos.y() < RULER_H:
            self._ruler_menu(event.globalPos())
            return
        if pos.x() < HEADER_W:
            index = self.track_at(pos.y())
            if index >= 0:
                self._track_menu(index, event.globalPos())
            return

        index, clip, _, _ = self._hit(pos)
        menu = QMenu(self)
        if clip is not None:
            if clip.id not in self._selection:
                self.select([clip.id])
            menu.addAction("在源监视器中打开",
                           lambda: self.source_open_requested.emit(clip.asset_id))
            menu.addAction("复制", self.copy_selection)
            menu.addAction("剪切", self.cut_selection)
            menu.addSeparator()
            menu.addAction("在此切割", self.split_at_playhead)
            menu.addAction("波纹删除", self.ripple_delete_selected)
            menu.addAction("删除（留空）", self.delete_selected)
            menu.addSeparator()
        menu.addAction("添加标记", self.add_marker_at_playhead)
        menu.addAction("缩放到适应", self.zoom_fit)
        menu.exec(event.globalPos())

    def _ruler_menu(self, global_pos) -> None:
        menu = QMenu(self)
        menu.addAction("添加标记 (M)", self.add_marker_at_playhead)
        menu.addAction("清除播放头处的标记", self.remove_marker_at_playhead)
        menu.addAction("清除全部标记", self.clear_markers)
        menu.addSeparator()
        menu.addAction("缩放到适应", self.zoom_fit)
        menu.exec(global_pos)

    def _track_menu(self, index: int, global_pos) -> None:
        track = self.project.tracks[index]
        menu = QMenu(self)
        if track.kind == "video":
            menu.addAction("隐藏轨道" if not track.hidden else "显示轨道",
                           lambda: self._toggle_track_flag(index, "toggle"))
        else:
            menu.addAction("静音轨道" if not track.muted else "取消静音",
                           lambda: self._toggle_track_flag(index, "toggle"))
        menu.addAction("锁定轨道" if not track.locked else "解锁轨道",
                       lambda: self._toggle_track_flag(index, "lock"))
        menu.addSeparator()
        sub = menu.addMenu("轨道高度")
        for height in TRACK_HEIGHTS:
            sub.addAction(f"{height} px", lambda h=height: self.set_track_height(track.id, h))
        menu.addSeparator()
        menu.addAction("在该轨道上方新建同类轨道",
                       lambda: self.add_track_near(track.id, above=True))
        menu.addAction("删除该轨道", lambda: self.remove_track(track.id))
        menu.exec(global_pos)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.AltModifier:
            anchor_time = self.time_for_x(event.position().toPoint().x())
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            self._zoom(self.pixels_per_sec * factor)
            x = self.x_for_time(anchor_time)
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() + (x - event.position().toPoint().x()))
            return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_scrollbars()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_header is not None:
            self._hover_header = None
            self.viewport().update()
        super().leaveEvent(event)

    # ------------------------------------------------------------------
    # 轨道开关
    # ------------------------------------------------------------------
    def _toggle_track_flag(self, index: int, key: str) -> None:
        track = self.project.tracks[index]
        before = TrackStateCommand.snapshot(track)
        if key == "lock":
            track.locked = not track.locked
            self.status.emit(f"{track.name}：{'已锁定' if track.locked else '已解锁'}")
        elif key == "toggle":
            if track.kind == "video":
                track.hidden = not track.hidden
                self.status.emit(f"{track.name}：{'已隐藏' if track.hidden else '已显示'}")
            else:
                track.muted = not track.muted
                self.status.emit(f"{track.name}：{'已静音' if track.muted else '已取消静音'}")
        self._push_track_state(track, before)
        self.viewport().update()

    def _push_track_state(self, track: Track, before: dict) -> None:
        command = TrackStateCommand(self.project, "修改轨道开关")
        command.record(track.id, before, TrackStateCommand.snapshot(track))
        if not command.empty:
            self.undo.push(command)
        self.track_changed.emit()
        self.project_edited.emit()

    def set_track_height(self, track_id: str, height: int) -> None:
        track = self.project.track(track_id)
        if track is None:
            return
        before = TrackStateCommand.snapshot(track)
        track.height = int(height)
        self._push_track_state(track, before)

    def add_track_near(self, track_id: str, above: bool = False) -> None:
        track = self.project.track(track_id)
        kind = track.kind if track else "video"
        before = snapshot_tracks(self.project)
        new_track = self.project.add_track(kind)
        # add_track 追加到末尾；这里把它挪到锚点相邻位置。
        # 列表里越靠后层级越高，因此"上方(above)"= 插到锚点之后。
        self.project.tracks.remove(new_track)
        anchor = self.project.track(track_id)
        position = self.project.tracks.index(anchor) + 1 if anchor else len(self.project.tracks)
        self.project.tracks.insert(position if above else max(0, position - 1), new_track)
        self._renumber_tracks()
        self._push_track_list(before, "新增轨道")
        self.track_changed.emit()
        self.project_edited.emit()

    def remove_track(self, track_id: str) -> None:
        track = self.project.track(track_id)
        if track is None:
            return
        if track.clips:
            self.status.emit("轨道上还有片段，先清空再删除")
            return
        if sum(1 for t in self.project.tracks if t.kind == track.kind) <= 1:
            self.status.emit("至少保留一条同类轨道")
            return
        before = snapshot_tracks(self.project)
        self.project.tracks.remove(track)
        self._renumber_tracks()
        self._push_track_list(before, "删除轨道")
        self.track_changed.emit()
        self.project_edited.emit()

    def ensure_track(self, kind: str) -> Track:
        """公开入口：同类轨一条都没有时建一条，且可撤销（B-27）。"""
        return self._ensure_track(kind)

    def _push_track_list(self, before: list[dict], label: str) -> None:
        """轨道结构变更入撤销栈（B-27）。"""
        command = TrackListCommand(self.project, label)
        command.record(before)
        if not command.empty:
            self.undo.push(command)

    def _renumber_tracks(self) -> None:
        """只给还没有名字的轨道补名字，**不改名已有轨道**（B-06）。

        旧实现按顺序重排 V1/V2…，新增或删除轨道会把既有名字整体搬家，
        用户无法靠名字认出轨道，也让"基底轨"身份漂移。
        """
        counter = {"video": 0, "audio": 0}
        existing = set()
        for track in self.project.tracks:
            counter[track.kind] += 1
            existing.add(track.name)
        for track in self.project.tracks:
            if track.name:
                continue
            prefix = "V" if track.kind == "video" else "A"
            number = counter[track.kind]
            while f"{prefix}{number}" in existing:
                number += 1
            track.name = f"{prefix}{number}"
            existing.add(track.name)

    # ------------------------------------------------------------------
    # 编辑操作
    # ------------------------------------------------------------------
    def _snap(self, ms: int) -> int:
        fps = self.project.fps_fraction
        snapped = snap_to_frame(ms, fps)
        if not self.snap_enabled:
            return max(0, ms)
        tolerance = max(4, round(1000 * fps.denominator / fps.numerator) * 4)
        raw: list[int] = [0, self.playhead, self.project.duration()]
        for track in self.project.tracks:
            for clip in track.clips:
                raw.extend((clip.start, clip.end))
        raw.extend(m.start for m in self.project.markers)
        # 候选（片段边缘/标记/播放头）本身可能不在帧边界（旧工程、导入数据），
        # 比较前统一量化，否则吸附结果会被非帧对齐的边缘改写（B-26）
        candidates = [snap_to_frame(value, fps) for value in raw]
        best = snapped
        best_dist = tolerance + 1
        for candidate in candidates:
            dist = abs(candidate - ms)
            if dist <= tolerance and dist < best_dist:
                best, best_dist = candidate, dist
        return max(0, best)

    def _blade(self, track: Track, at: int) -> None:
        if track.locked:
            return
        clip = track.clip_at(at)
        if clip is None:
            self.status.emit("此处没有片段")
            return
        # 必须深拷贝：split_track 原地修改 track.clips 里的对象
        before = [Clip.from_dict(c.to_dict()) for c in track.clips]
        split_track(track, at)
        if track.clips != before:
            command = TrackEditCommand(self.project, "切割")
            command.record(track.id, before)
            self.undo.push(command)
            self.project_edited.emit()
            self.status.emit("已切割")

    def split_at_playhead(self) -> None:
        # 切割点必须落在整帧上，否则左右两段时长都不是整帧（B-26）
        at = snap_to_frame(self._snap(self.playhead), self.project.fps_fraction)
        command = TrackEditCommand(self.project, "在播放头切割")
        for track in self.project.tracks:
            if track.locked or track.hidden:
                continue
            if track.clip_at(at) is None:
                continue
            before = [Clip.from_dict(c.to_dict()) for c in track.clips]
            split_track(track, at)
            command.record(track.id, before)
        if not command.empty:
            self.undo.push(command)
            self.project_edited.emit()
            self.status.emit("已在播放头切割")
        else:
            self.status.emit("播放头处没有可切割的片段")

    def delete_selected(self, ripple: bool = False) -> None:
        if not self._selection:
            return
        selected = set(self._selection)
        command = TrackEditCommand(self.project, "波纹删除" if ripple else "删除")
        for track in self.project.tracks:
            if track.locked:
                continue
            targets = [c for c in track.clips if c.id in selected]
            if not targets:
                continue
            before = [Clip.from_dict(c.to_dict()) for c in track.clips]
            after = [Clip.from_dict(c.to_dict())
                     for c in track.clips if c.id not in selected]
            if ripple:
                # 分段位移：每个片段只闭合**位于它之前**的那些被删区间，
                # 不能按"总删除长度"统一前移（非连续选择会过移并压出重叠，B-09）
                spans = sorted((c.start, c.end) for c in targets)
                for clip in after:
                    shift = sum(end - start for start, end in spans if start < clip.start)
                    if shift:
                        clip.start = max(0, clip.start - shift)
            track.clips = sorted(after, key=lambda c: c.start)
            command.record(track.id, before)
        if not command.empty:
            self.undo.push(command)
            self._selection.clear()
            self.selection_changed.emit([])
            self.project_edited.emit()
            self.status.emit("已删除" + ("（闭合间隙）" if ripple else ""))

    def ripple_delete_selected(self) -> None:
        self.delete_selected(ripple=True)

    def copy_selection(self) -> None:
        clips = self.selected_clips()
        if not clips:
            return
        self._clipboard = [c.to_dict() for c in clips]
        self.status.emit(f"已复制 {len(clips)} 个片段")

    def cut_selection(self) -> None:
        self.copy_selection()
        self.delete_selected()

    # 粘贴可识别的音频扩展名（与素材库口径一致）
    AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg")

    def _clip_kind(self, clip: Clip) -> str:
        path = (clip.path or "").lower()
        return "audio" if path.endswith(self.AUDIO_SUFFIXES) else "video"

    def _ensure_track(self, kind: str) -> Track:
        """同类轨道全没了才被动建轨；同样进撤销栈（B-27）。"""
        before = snapshot_tracks(self.project)
        track = self.project.add_track(kind)
        self._renumber_tracks()
        self._push_track_list(before, "新增轨道")
        self.track_changed.emit()
        return track

    def _target_track(self, kind: str) -> Track:
        track = next((t for t in self.project.tracks if t.kind == kind and not t.locked),
                     None)
        if track is None:
            track = self._ensure_track(kind)
        return track

    def paste(self) -> None:
        """粘贴 = **插入**语义：不销毁既有片段，插入点之后的内容右移（B-29）。"""
        if not self._clipboard:
            self.status.emit("剪贴板为空")
            return
        origin = min(int(c.get("start", 0)) for c in self._clipboard)
        offset = self.playhead - origin
        groups: dict[str, list[Clip]] = {}
        for data in self._clipboard:
            payload = dict(data)
            payload["id"] = new_id()
            payload["start"] = max(0, int(data.get("start", 0)) + offset)
            clip = Clip.from_dict(payload)
            groups.setdefault(self._clip_kind(clip), []).append(clip)

        command = TrackEditCommand(self.project, "粘贴片段")
        touched: dict[str, list[Clip]] = {}
        created: list[str] = []
        for kind, clips in groups.items():
            clips.sort(key=lambda c: c.start)
            track = self._target_track(kind)
            self._insert_payloads(track, clips, "insert", touched)
            created.extend(clip.id for clip in clips)
        if created:
            for track_id, before in touched.items():
                command.record(track_id, before)
            self.undo.push(command)
            self.select(created)
            self.project_edited.emit()
            self.status.emit(f"已粘贴 {len(created)} 个片段（插入）")

    def nudge_selection(self, delta_ms: int) -> None:
        clips = self.selected_clips()
        if not clips:
            return
        command = TrackEditCommand(self.project, "移动片段")
        for clip in clips:
            track, _ = self.project.locate(clip.id)
            if track is None or track.locked:
                continue
            before = [Clip.from_dict(c.to_dict()) for c in track.clips]
            clip.start = max(0, clip.start + delta_ms)
            track.clips.sort(key=lambda c: c.start)
            command.record(track.id, before)
        if not command.empty:
            self.undo.push(command)
            self.project_edited.emit()

    def insert_clip(self, asset_id: str, track_kind: str, at: int,
                    source_in: int = 0, duration: int | None = None) -> Clip | None:
        """把素材放到指定类型轨道的 at 位置（覆盖式插入）。"""
        track = next(
            (t for t in self.project.tracks if t.kind == track_kind and not t.locked),
            None,
        )
        if track is None:
            track = self._ensure_track(track_kind)
        return self.insert_clip_on_track(asset_id, track.id, at,
                                         source_in=source_in, duration=duration)

    def _make_clip(self, asset_id: str, at: int,
                   source_in: int = 0, duration: int | None = None) -> Clip | None:
        """按素材构造一个时间线片段（不落轨、不入栈）。"""
        asset = self.project.asset(asset_id)
        if asset is None:
            return None
        source_in = max(0, source_in)
        if duration:
            length = int(duration)
        elif asset.kind == "image" or asset.duration <= 0:
            length = 5000
        else:
            length = max(500, asset.duration - source_in)
        return Clip(
            name=asset.name,
            asset_id=asset.id,
            path=asset.path,
            start=max(0, int(at)),
            source_in=source_in,
            duration=max(1, int(length)),
        )

    def _insert_payloads(self, track: Track, clips: list[Clip],
                         mode: str, touched: dict[str, list[Clip]]) -> None:
        """把一组片段放进轨道；撤销快照交给调用方收集。

        mode="overwrite"：覆盖式落位，与既有片段重叠时切分/移除被覆盖部分；
        mode="insert"：插入式，插入点之后的既有内容整体右移，跨过插入点的
        片段被切成头/尾两段（粘贴默认语义，B-29）。
        """
        if track.id not in touched:
            touched[track.id] = snapshot_clips(track.clips)
        if mode == "insert" and clips:
            begin = min(c.start for c in clips)
            span = max(c.end for c in clips) - begin
            kept: list[Clip] = []
            for existing in track.clips:
                if existing.end <= begin:
                    kept.append(existing)
                    continue
                if existing.start >= begin:
                    existing.start += span
                    kept.append(existing)
                    continue
                # 跨过插入点：头部保留原位，尾部整体右移，保持素材连续性
                original_end = existing.end
                head = existing
                head.duration = begin - existing.start
                tail = Clip.from_dict(existing.to_dict())
                tail.id = new_id()
                tail.start = begin + span          # 紧接在粘贴内容之后
                tail.source_in = existing.source_in + int(
                    (begin - existing.start) * existing.speed)
                tail.duration = original_end - begin
                kept.extend([head, tail])
            track.clips = kept + list(clips)
        else:
            # overwrite_layout 返回全新列表且不修改传入对象，无需先做浅拷贝
            layout = track.clips
            for clip in clips:
                layout = overwrite_layout(layout, clip)
            track.clips = layout
        track.clips.sort(key=lambda c: c.start)
    def insert_clip_on_track(self, asset_id: str, track_id: str, at: int,
                             source_in: int = 0, duration: int | None = None) -> Clip | None:
        """插入到指定轨道（拖放与混剪都走这里）。"""
        asset = self.project.asset(asset_id)
        if asset is None:
            return None
        track = self.project.track(track_id)
        if track is None or track.locked:
            return None

        clip = self._make_clip(asset_id, at, source_in=source_in, duration=duration)
        if clip is None:
            return None

        before = snapshot_clips(track.clips)
        track.clips = overwrite_layout(track.clips, clip)
        command = TrackEditCommand(self.project, "插入片段")
        command.record(track.id, before)
        self.undo.push(command)
        self.select([clip.id])
        self.project_edited.emit()
        return self.project.locate(clip.id)[1]

    def replace_track_clips(self, track_id: str, clips: list[Clip],
                            label: str = "混剪成片") -> bool:
        """整条轨道替换（混剪用，可撤销）。"""
        track = self.project.track(track_id)
        if track is None or track.locked:
            return False
        before = [Clip.from_dict(c.to_dict()) for c in track.clips]
        track.clips = sorted((Clip.from_dict(c.to_dict()) for c in clips),
                             key=lambda c: c.start)
        command = TrackEditCommand(self.project, label)
        command.record(track.id, before)
        self.undo.push(command)
        self.select([])
        self.project_edited.emit()
        return True

    def insert_many(self, asset_ids: list[str], track_kind: str, at: int,
                    track_id: str | None = None) -> list[Clip]:
        """按顺序批量插入（拖放多个素材时使用）。

        - `track_id` 指定落点轨道：拖放命中哪条轨就放哪条（B-S1，旧实现丢掉
          `track`，永远落到第一条同类轨）。
        - 整批只产生**一条**撤销命令（B-29）。
        """
        created: list[Clip] = []
        cursor = int(at)
        command = TrackEditCommand(self.project, "批量插入")
        touched: dict[str, list[Clip]] = {}
        for asset_id in asset_ids:
            track = self.project.track(track_id) if track_id else None
            if track is None or track.locked or track.kind != track_kind:
                track = self._target_track(track_kind)
            clip = self._make_clip(asset_id, cursor)
            if clip is None:
                continue
            self._insert_payloads(track, [clip], "overwrite", touched)
            placed = self.project.locate(clip.id)[1]
            created.append(placed if placed is not None else clip)
            cursor = cursor + clip.duration
        if created:
            for tid, before in touched.items():
                command.record(tid, before)
            self.undo.push(command)
            self.select([c.id for c in created])
            self.project_edited.emit()
        return created

    def add_track(self, kind: str) -> None:
        before = snapshot_tracks(self.project)
        track = self.project.add_track(kind)
        # 视频轨：新轨进入**最上层**（列表末尾的视频位置），基底轨与已有轨道
        # 的名字都不变；新音频轨追加到音频组末尾。不再整体重排命名（B-06）。
        tracks = self.project.tracks
        if kind == "video":
            tracks.remove(track)
            last_video = max((i for i, t in enumerate(tracks) if t.kind == "video"),
                             default=-1)
            tracks.insert(last_video + 1, track)
        self._push_track_list(before, "新增轨道")
        self.track_changed.emit()
        self.project_edited.emit()

    # ------------------------------------------------------------------
    # 素材引用完整性（B-11）
    # ------------------------------------------------------------------
    def asset_references(self, asset_id: str) -> list[tuple[Track, Clip]]:
        """返回所有引用该素材的时间线片段（含媒体库中已被移除的悬空引用）。"""
        return [(track, clip) for track in self.project.tracks
                for clip in track.clips if clip.asset_id == asset_id]

    def remove_asset_and_clips(self, asset_id: str,
                               label: str = "删除素材及其时间线片段") -> int:
        """级联删除某素材在时间线上的全部片段**和媒体库记录**（一条可撤销命令）。

        必须一起删：只删片段的话，撤销时片段回来了、素材记录却没了，
        会留下悬空引用（B-11 的另一半）。
        """
        tracks = [t for t in self.project.tracks
                  if any(c.asset_id == asset_id for c in t.clips)]
        asset = self.project.asset(asset_id)
        if not tracks and asset is None:
            return 0

        snapshots = [(track.id, snapshot_clips(track.clips)) for track in tracks]
        asset_index = next((i for i, a in enumerate(self.project.assets)
                            if a.id == asset_id), -1)
        removed = sum(1 for track in tracks for c in track.clips
                      if c.asset_id == asset_id)

        def do() -> None:
            for track in self.project.tracks:
                track.clips = [c for c in track.clips if c.asset_id != asset_id]
            self.project.assets = [a for a in self.project.assets if a.id != asset_id]

        def undo() -> None:
            if asset is not None:
                position = asset_index if 0 <= asset_index <= len(self.project.assets) \
                    else len(self.project.assets)
                self.project.assets.insert(position, asset)
            for track_id, clips in snapshots:
                track = self.project.track(track_id)
                if track is not None:
                    track.clips = snapshot_clips(clips)

        command = LambdaCommand(label, do, undo)
        self.undo.push(command)      # push 会立即 redo() 执行 do()
        self.select([])
        self.project_edited.emit()
        self.track_changed.emit()
        return removed

    # ------------------------------------------------------------------
    # 标记
    # ------------------------------------------------------------------
    def add_marker_at_playhead(self) -> None:
        index = len(self.project.markers) + 1
        self.project.markers.append(
            Marker(time=self.playhead, label=f"标记 {index}"))
        self._sort_markers()
        self.status.emit(f"已添加标记 {index}")
        self.project_edited.emit()

    def remove_marker_at_playhead(self) -> None:
        tolerance = max(4, int(1000 / max(1, float(self.project.fps_fraction))))
        victims = [m for m in self.project.markers if abs(m.time - self.playhead) <= tolerance]
        if not victims:
            self.status.emit("播放头处没有标记")
            return
        for marker in victims:
            self.project.markers.remove(marker)
        self._sort_markers()
        self.project_edited.emit()

    def clear_markers(self) -> None:
        if not self.project.markers:
            return
        self.project.markers.clear()
        self.project_edited.emit()
        self.status.emit("已清除全部标记")

    def goto_next_marker(self) -> None:
        candidates = [m.time for m in self.project.markers if m.time > self.playhead]
        if not candidates:
            self.status.emit("没有更靠后的标记")
            return
        self.set_playhead(min(candidates))

    def goto_prev_marker(self) -> None:
        candidates = [m.time for m in self.project.markers if m.time < self.playhead]
        if not candidates:
            self.status.emit("没有更靠前的标记")
            return
        self.set_playhead(max(candidates))

    def _sort_markers(self) -> None:
        self.project.markers.sort(key=lambda m: m.time)

    def _add_marker(self) -> None:
        self.add_marker_at_playhead()

    # ------------------------------------------------------------------
    # 拖放（媒体库 -> 时间线）
    # ------------------------------------------------------------------
    def viewportEvent(self, event) -> bool:  # noqa: N802
        etype = event.type()
        if etype in (QEvent.DragEnter, QEvent.DragMove):
            if event.mimeData().hasFormat(ASSET_MIME):
                ids = self._mime_asset_ids(event.mimeData())
                if ids:
                    event.acceptProposedAction()
                    self._update_drop_preview(event.position().toPoint(), ids)
                    return True
            event.ignore()
            return True
        if etype == QEvent.DragLeave:
            self._drop_preview = None
            self.viewport().update()
            return True
        if etype == QEvent.Drop:
            if event.mimeData().hasFormat(ASSET_MIME):
                ids = self._mime_asset_ids(event.mimeData())
                self._drop_preview = None
                self._drop_assets(ids, event.position().toPoint())
                event.acceptProposedAction()
                self.viewport().update()
                return True
        return super().viewportEvent(event)

    @staticmethod
    def _mime_asset_ids(mime) -> list[str]:
        raw = bytes(mime.data(ASSET_MIME)).decode("utf-8", errors="ignore")
        return [part for part in raw.split(",") if part]

    def _kind_for_asset(self, asset_id: str) -> str:
        asset = self.project.asset(asset_id)
        if asset is not None:
            return "audio" if asset.kind == "audio" else "video"
        from .media_panel import MediaPanel  # noqa: F401 - 仅用于类型提示
        return "video"

    def _update_drop_preview(self, pos: QPoint, ids: list[str]) -> None:
        kind = self._kind_for_asset(ids[0])
        index = self.track_at(pos.y())
        if index < 0 or self.project.tracks[index].kind != kind:
            index = next((i for i, t in enumerate(self.project.tracks)
                          if t.kind == kind and not t.locked), -1)
        if index < 0:
            self._drop_preview = None
        else:
            self._drop_preview = (index, self._snap(self.time_for_x(pos.x())))
        self.viewport().update()

    def _drop_assets(self, ids: list[str], pos: QPoint) -> None:
        if not ids:
            return
        kind = self._kind_for_asset(ids[0])
        index = self.track_at(pos.y())
        track = None
        if index >= 0 and self.project.tracks[index].kind == kind \
                and not self.project.tracks[index].locked:
            track = self.project.tracks[index]
        if track is None:
            track = next((t for t in self.project.tracks
                          if t.kind == kind and not t.locked), None)
        if track is None:
            track = self._ensure_track(kind)
        at = self._snap(self.time_for_x(pos.x()))
        # 拖放命中哪条同类轨就放哪条，不要丢掉 track 再让 insert_many 重新挑（B-S1）
        created = self.insert_many(ids, kind, at, track_id=track.id)
        if created:
            self.status.emit(f"已插入 {len(created)} 个素材到 {track.name}")
