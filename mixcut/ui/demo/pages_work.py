# -*- coding: utf-8 -*-
"""工作台：大预览 + 素材预览条（左右滑动选择）；不参考源码工作台。"""

from PySide6.QtCore import Qt, QRectF, Signal, QUrl
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QSlider,
    QVBoxLayout, QWidget,
)

from ... import preview_proxy
from . import mock, theme as T
from .ui_charts import PreviewSurface
from .ui_kit import Card, IconButton, SoftButton, Thumb, draw_icon, thumb_pixmap


class _StageButton(IconButton):
    """视频舞台右下角的悬浮按钮：深色半透明底 + 白色图标。"""

    def __init__(self, parent=None):
        super().__init__("maximize", 34, parent, tooltip="全屏预览（P）")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 145) if not self._hover else QColor(0, 0, 0, 210))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
        draw_icon(p, self.icon, QRectF(self.rect()), QColor("#FFFFFF"), 1.6)
        p.end()


class PreviewStage(QWidget):
    """视频舞台：画面铺满，右下角悬浮全屏按钮；全屏的就是这个容器本身，
    因此退出全屏时画面不会被搬走。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:#000000;border-radius:8px;")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.fs_btn = _StageButton(self)
        self.fs_btn.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.place_button()

    def place_button(self):
        m = 14
        self.fs_btn.move(max(0, self.width() - self.fs_btn.width() - m),
                         max(0, self.height() - self.fs_btn.height() - m))
        self.fs_btn.raise_()

    def set_fullscreen_state(self, full: bool):
        self.fs_btn.icon = "restore" if full else "maximize"
        self.fs_btn.setToolTip("退出全屏（P / Esc）" if full else "全屏预览（P）")
        self.fs_btn.update()



class StripTile(QWidget):
    clicked = Signal(str)

    def __init__(self, clip, parent=None):
        super().__init__(parent)
        self.clip = clip
        self.selected = False
        self.hover = False
        self.setFixedSize(176, 126)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        self.hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.clip["id"])
            e.accept()
        super().mousePressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = T.CARD_HI if (self.hover or self.selected) else T.CARD
        p.setPen(QPen(T.c("#4B3F7A") if self.selected else T.c(T.LINE), 1))
        p.setBrush(T.c(fill))
        p.drawRoundedRect(r, 10, 10)

        img = QRectF(r.left() + 7, r.top() + 7, r.width() - 14, 88)
        path = QPainterPath()
        path.addRoundedRect(img, 7, 7)
        p.save()
        p.setClipPath(path)
        p.drawPixmap(img.toRect(), thumb_pixmap(
            self.clip.get("thumb_path") or mock.THUMBS[self.clip["thumb"]],
            int(img.width()), int(img.height())))
        p.restore()

        p.setPen(T.c(T.TEXT))
        p.setFont(T.font(11, QFont.Weight.DemiBold))
        p.drawText(QRectF(img.left() + 2, img.bottom() + 7, img.width() - 42, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.clip["name"])
        p.setPen(T.c(T.TEXT_3))
        p.setFont(T.font(9, mono=True))
        p.drawText(QRectF(img.right() - 40, img.bottom() + 7, 40, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self.clip["dur"])
        p.end()


class StripScroll(QScrollArea):
    """鼠标滚轮横向滑动素材预览条。"""

    def wheelEvent(self, e):
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - e.angleDelta().y())
        e.accept()


class WorkbenchPage(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAGE_PAD, 18, T.PAGE_PAD, 22)
        root.setSpacing(16)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        h = QLabel("素材预览")
        h.setFont(T.font(30, QFont.Weight.DemiBold))
        titles.addWidget(h)
        head.addLayout(titles, 1)
        self.btn_import = SoftButton("导入素材", "secondary", "import")
        head.addWidget(self.btn_import)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(18)
        root.addLayout(body, 1)

        # 预览：视频放进 PreviewStage，全屏时放大的是这个容器本身
        self.preview_card = Card(padding=14, spacing=12)
        self.stage = PreviewStage()
        real_video = getattr(win, "video_widget", None)
        if real_video is not None:
            self.preview = None
            self.stage.lay.addWidget(real_video, 1)
        else:
            self.preview = PreviewSurface()
            self.stage.lay.addWidget(self.preview, 1)
        self.preview_card.lay.addWidget(self.stage, 1)
        self.stage.fs_btn.clicked.connect(win._toggle_video_fullscreen)

        self.preview_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body.addWidget(self.preview_card, 1)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.play_btn = SoftButton("", "ghost", "play", height=34, radius=17)
        self.play_btn.setFixedWidth(34)
        self.pause_btn = SoftButton("", "ghost", "pause", height=34, radius=17)
        self.pause_btn.setFixedWidth(34)
        self.stop_btn = SoftButton("", "ghost", "stop", height=34, radius=17)
        self.stop_btn.setFixedWidth(34)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.pause_btn)
        controls.addWidget(self.stop_btn)
        self.time_label = QLabel("00:00:00:00")
        self.time_label.setFont(T.font(11, mono=True))
        self.time_label.setStyleSheet("color:%s;" % T.TEXT_2)
        controls.addWidget(self.time_label)
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, 1000)
        self.scrub.setValue(0)
        self.scrub.setFixedHeight(16)
        controls.addWidget(self.scrub, 1)
        self.dur_label = QLabel("00:34:00")
        self.dur_label.setFont(T.font(11, mono=True))
        self.dur_label.setStyleSheet("color:%s;" % T.TEXT_3)
        controls.addWidget(self.dur_label)

        # 预览代理：默认关闭（播原片）；可选 720 / 960 / 1280 宽
        self.proxy_box = QComboBox()
        for _label, _w in (("代理关闭", 0), ("代理 720p", 720),
                           ("代理 960p", 960), ("代理 1280p", 1280)):
            self.proxy_box.addItem(_label, _w)
        self.proxy_box.setFixedWidth(116)
        self.proxy_box.setToolTip(
            "预览代理：4K/10bit/422 素材软解很慢，开启后后台生成低码率副本用于预览，导出仍用原片")
        _idx = self.proxy_box.findData(preview_proxy.get_width())
        self.proxy_box.setCurrentIndex(max(0, _idx))
        self.proxy_box.currentIndexChanged.connect(self._on_proxy_changed)
        controls.addWidget(self.proxy_box)

        self.proxy_label = QLabel("")
        self.proxy_label.setFont(T.font(10, mono=True))
        self.proxy_label.setStyleSheet("color:%s;" % T.TEXT_3)
        controls.addWidget(self.proxy_label)

        self.preview_card.lay.addLayout(controls)

        meta = QHBoxLayout()
        meta.setSpacing(18)
        self.meta_name = QLabel("暂无素材")
        self.meta_name.setFont(T.font(12, QFont.Weight.DemiBold, mono=True))
        self.meta_name.setStyleSheet("color:%s;" % T.TEXT)
        meta.addWidget(self.meta_name)
        self.meta_chips = []
        for txt in ("分辨率待读取", "编码待读取", "帧率待读取", "色深待读取"):
            chip = QLabel(txt)
            chip.setFont(T.font(10, mono=True))
            chip.setStyleSheet("color:%s;" % T.TEXT_3)
            meta.addWidget(chip)
            self.meta_chips.append(chip)
        meta.addStretch(1)
        self.frame_state = QLabel("预览待命")
        self.frame_state.setFont(T.font(11))
        self.frame_state.setStyleSheet("color:%s;" % T.TEXT_3)
        meta.addWidget(self.frame_state)
        self.preview_card.lay.addLayout(meta)

        # 底部素材预览条
        strip = Card(padding=14, spacing=10)
        strip.setFixedHeight(192)
        root.addWidget(strip)
        top = QHBoxLayout()
        top.setSpacing(10)
        t1 = QLabel("素材预览")
        t1.setFont(T.font(13, QFont.Weight.DemiBold))
        top.addWidget(t1)
        self.strip_count = QLabel("0 条素材 · 点击缩略图切换")
        self.strip_count.setFont(T.font(10))
        self.strip_count.setStyleSheet("color:%s;" % T.TEXT_3)
        top.addWidget(self.strip_count)
        top.addStretch(1)
        self.prev_btn = SoftButton("‹", "ghost", height=28, radius=8)
        self.prev_btn.setFixedWidth(32)
        self.next_btn = SoftButton("›", "ghost", height=28, radius=8)
        self.next_btn.setFixedWidth(32)
        top.addWidget(self.prev_btn)
        top.addWidget(self.next_btn)
        strip.lay.addLayout(top)

        self.scroll = StripScroll()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setFixedHeight(136)
        holder = QWidget()
        self.strip_lay = QHBoxLayout(holder)
        self.strip_lay.setContentsMargins(0, 0, 0, 0)
        self.strip_lay.setSpacing(10)
        self.scroll.setWidget(holder)
        strip.lay.addWidget(self.scroll)

        self.tiles = {}
        for clip in mock.CLIPS:
            tile = StripTile(clip)
            tile.clicked.connect(self._select)
            self.tiles[clip["id"]] = tile
            self.strip_lay.addWidget(tile)
        self.strip_lay.addStretch(1)

        self.btn_import.clicked.connect(self.win._topbar_import)
        self.play_btn.clicked.connect(self._toggle_play)
        self.pause_btn.clicked.connect(
            lambda: self.win._user_pause() if hasattr(self.win, "_user_pause")
            else (self.win.player.pause()
                  if getattr(self.win, "player", None) else None))
        self.stop_btn.clicked.connect(self._stop)
        self.scrub.sliderMoved.connect(
            lambda v: self.win.player.setPosition(int(v)) if getattr(self.win, "player", None) else None)
        player = getattr(self.win, "player", None)
        self._player_bound = False
        self.attach_player()
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))
        self.win.controller.stateChanged.connect(self.refresh)
        self.refresh()

    def attach_player(self):
        """player 在 demo 页之后才创建，这里补上播放进度信号。"""
        player = getattr(self.win, "player", None)
        if player is None or self._player_bound:
            return
        player.durationChanged.connect(
            lambda d: self.scrub.setRange(0, max(1, int(d))))
        player.positionChanged.connect(self._sync_scrub)
        self._player_bound = True

    # ---- 预览代理 ----
    def _on_proxy_changed(self, _index):
        width = self.proxy_box.currentData()
        setter = getattr(self.win, "set_preview_proxy_width", None)
        if callable(setter):
            setter(width)
        else:
            preview_proxy.set_width(width)

    def set_proxy_status(self, text):
        self.proxy_label.setText(text or "")

    def on_shown(self):
        """进入工作台时把预览同步成当前素材，避免显示上一个素材的画面。"""
        self.refresh()
        cur = self.win.controller.current_clip()
        path = cur.get("path")
        if not path:
            return
        win = self.win
        if getattr(win, "_preview_path", None) == path:
            return
        if hasattr(win, "show_clip_preview"):
            win.show_clip_preview(path)

    def _toggle_play(self):
        player = getattr(self.win, "player", None)
        if player is None:
            return
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if hasattr(self.win, "_user_pause"):
                self.win._user_pause()
            else:
                player.pause()
        elif hasattr(self.win, "_user_play"):
            self.win._user_play()
        else:
            player.play()

    def is_active(self):
        """素材预览页当前是不是真正显示在界面上。"""
        try:
            return bool(self.isVisible())
        except RuntimeError:
            return False

    def hideEvent(self, e):
        # 切走页面时画面已经看不见了，声音也必须停，不能只在后台响。
        stopper = getattr(self.win, "stop_preview_playback", None)
        if callable(stopper):
            stopper()
        super().hideEvent(e)

    def _sync_scrub(self, pos):
        if not self.scrub.isSliderDown():
            self.scrub.setValue(int(pos))

    def rebuild_strip(self):
        while self.strip_lay.count():
            item = self.strip_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.tiles = {}
        for clip in mock.CLIPS:
            tile = StripTile(clip)
            tile.clicked.connect(self._select)
            self.tiles[clip["id"]] = tile
            self.strip_lay.addWidget(tile)
        self.strip_lay.addStretch(1)
        self.refresh()

    def _stop(self):
        self.scrub.setValue(0)
        stopper = getattr(self.win, "_user_stop", None)
        if callable(stopper):
            stopper()
        elif getattr(self.win, "player", None) is not None:
            self.win.player.stop()
        self.win.controller.log("素材预览 · 停止播放并回到起点")
    def _select(self, cid):
        clip = next((c for c in mock.CLIPS if c["id"] == cid), None)
        if clip and clip.get("path") and getattr(self.win, "player", None) is not None:
            # 走宿主的统一入口：设源 + 预热首帧（静音、停住），避免切过去还是黑屏。
            # 单击素材只定位首帧，出声播放必须由用户点播放/双击触发。
            stopper = getattr(self.win, "stop_preview_playback", None)
            if callable(stopper):
                stopper()
            if hasattr(self.win, "show_clip_preview"):
                self.win.show_clip_preview(clip["path"])
            else:
                self.win.player.setSource(QUrl.fromLocalFile(clip["path"]))
        self.win.controller.select_clip(cid)

    def _step(self, delta):
        c = self.win.controller
        clips = [x for x in mock.CLIPS if x["id"] not in c.removed]
        ids = [x["id"] for x in clips]
        if not ids:
            return
        try:
            idx = ids.index(c.current_clip()["id"])
        except ValueError:
            idx = 0
        nxt = max(0, min(len(ids) - 1, idx + delta))
        self._select(ids[nxt])
        self._ensure_visible(ids[nxt])

    def _ensure_visible(self, cid):
        tile = self.tiles.get(cid)
        if tile:
            self.scroll.ensureWidgetVisible(tile, 12, 0)

    def refresh(self):
        c = self.win.controller
        cur = c.current_clip()
        thumb = cur.get("thumb_path") or mock.THUMBS[cur["thumb"]]
        if self.preview is not None:
            self.preview.set_clip(thumb)
        self.meta_name.setText(cur["name"])
        self.dur_label.setText(cur["dur"])
        w, h = cur.get("width") or 0, cur.get("height") or 0
        codec = str(cur.get("codec") or "").upper()
        codec = {"H264": "H.264", "HEVC": "H.265", "PRORES": "ProRes"}.get(codec, codec)
        fps = cur.get("fps") or 0
        depth = cur.get("bit_depth") or 0
        self.meta_chips[0].setText("%d×%d" % (w, h) if w and h else "分辨率待读取")
        self.meta_chips[1].setText(codec or "编码待读取")
        self.meta_chips[2].setText(("%.2f fps" % float(fps)).rstrip("0").rstrip(".") if fps else "帧率待读取")
        self.meta_chips[3].setText("%s-bit" % depth if depth else "色深待读取")
        for cid, tile in self.tiles.items():
            tile.selected = cid == cur["id"]
            tile.setVisible(cid not in c.removed)
            tile.update()
        total = c.visible_count()
        self.strip_count.setText("%d 条素材 · 点击缩略图切换" % total)
        self.time_label.setText(c.timecode)
        self.scrub.setValue(int(c.progress * 1000))
