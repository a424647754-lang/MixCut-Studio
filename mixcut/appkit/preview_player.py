# -*- coding: utf-8 -*-
"""预览播放相关的方法（从 main.py 原样搬出）。

包含 QMediaPlayer 初始化、首帧预热、播放/暂停/停止、全屏预览、
预览代理接入以及 QApplication 级事件过滤器，共 20 个方法。

拆分方式说明
------------
Mixin：方法仍挂在 MainWindow 上，``self.player`` 的信号连接、
``installEventFilter(self)`` 注册的对象、初始化顺序全部不变。

风险提示：本模块涉及 QMediaPlayer 状态机与事件过滤器，
是全项目最容易出问题的一块。搬运时**未改动任何一行逻辑**。

宿主契约
--------
**控件属性**（由 ``_build_ui`` / ``_build_player_card`` 创建）
    ``self.video_widget``  ``self.play_btn``  ``self.pause_btn``
    ``self.stop_btn``      ``self.seek_slider``（可选）
    ``self.demo_workbench`` ``self.controller``

**状态属性**
    ``self.player``  ``self.audio_output``
    ``self._preview_path``          ``self._preview_pending``
    ``self._prime_pending``         ``self._prime_timer``
    ``self._user_play_requested``   ``self._proxy_listener_added``

**宿主方法**（定义在主类或其它 Mixin 上）
    ``self.log``  ``self._on_position``  ``self._on_duration``
    ``self._on_seek``  ``self._probe_paths``
    ``self._current_preview_target``  ``self._fullscreen_preview``
    ``self._enter_preview_fullscreen``  ``self._exit_preview_fullscreen``

依赖方向：只导入 PySide6、标准库与 mixcut.preview_proxy，
不导入 main.py，不导入 mixcut.ui。
"""
from PySide6.QtCore import Qt, QUrl, QEvent, QTimer
from PySide6.QtWidgets import (
    QApplication, QTextEdit, QLineEdit, QPlainTextEdit, QAbstractSpinBox,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget  # noqa: F401


class PreviewPlayerMixin:
    """素材预览播放器。"""

    # =================================================
    # 播放器
    # =================================================

    def _init_player(self):

        self.player = QMediaPlayer()

        self.audio_output = QAudioOutput()

        self.player.setAudioOutput(
            self.audio_output
        )

        self.player.setVideoOutput(
            self.video_widget
        )

        self.play_btn.clicked.connect(
            self._user_play
        )

        self.pause_btn.clicked.connect(
            self._user_pause
        )

        self.stop_btn.clicked.connect(
            self._user_stop
        )

        self.player.errorOccurred.connect(
            self.on_player_error
        )

        # 进度条信号连接（player 创建后立刻连，不能放在 eventFilter 里）
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_preview_media_status)
        # seek_slider 只在播放器卡片里创建（新界面由 _build_player_card 建）。
        seek_slider = getattr(self, "seek_slider", None)
        if seek_slider is not None:
            seek_slider.sliderReleased.connect(self._on_seek)

        # demo 工作台首次进入时载入当前素材并预热首帧，避免预览区一直黑屏。
        if hasattr(self, "demo_workbench"):
            self.demo_workbench.attach_player()
            cur = self.controller.current_clip()
            if cur.get("path"):
                self.show_clip_preview(cur["path"])

        # 给 video_widget 装事件过滤器：全屏时也能收到 Esc / 空格
        self.video_widget.installEventFilter(self)

    # ---- 预览首帧预热 ----

    def show_clip_preview(self, path):
        """切换预览源并预热首帧。

        QMediaPlayer 只有真正播放过一次，才会把解码帧交给 QVideoWidget；
        单纯 setSource + pause 会一直停在黑屏。这里用“播放极短一段再暂停”的方式
        让首帧落到预览控件上，再把位置回到 0。预热全程静音，结束后停在暂停态，
        不会出声、也不会在后台继续播放。
        预览代理开启时，缓存在就用代理文件；没有则先播原片并后台生成。
        """
        if not path or getattr(self, "player", None) is None:
            return
        # 换素材等于一次新的预览请求：先取消上一次的预热收尾，状态从暂停开始。
        self._user_play_requested = False
        self._prime_pending = False
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._preview_path = path
        self._ensure_proxy_listener()
        try:
            from mixcut import preview_proxy
            src = preview_proxy.resolve(path)
            if preview_proxy.get_width() and src == path:
                self._set_proxy_status("代理生成中…（先播原片）")
            elif src != path:
                self._set_proxy_status("代理已启用")
            else:
                self._set_proxy_status("")
        except Exception:
            src = path
        self._apply_preview_source(src, keep_position=False)

    def _ensure_proxy_listener(self):
        if getattr(self, "_proxy_listener_added", False):
            return
        try:
            from mixcut import preview_proxy
            preview_proxy.add_listener(self._on_proxy_event)
            self._proxy_listener_added = True
        except Exception:
            pass

    def _set_proxy_status(self, text):
        wb = getattr(self, "demo_workbench", None)
        setter = getattr(wb, "set_proxy_status", None)
        if callable(setter):
            setter(text)

    def _kick_proxy_queue(self):
        """开启代理后，把素材库里的全部视频一次性排队生成，不用一条条点。"""
        try:
            from mixcut import preview_proxy
            width = preview_proxy.get_width()
            if not width:
                return 0
            paths = [p for p in (self._probe_paths() or []) if p]
            n = preview_proxy.request_all(paths)
        except Exception:
            return 0
        if n:
            self._set_proxy_status("已排队 %d 条素材生成代理…" % n)
        return n

    def _on_proxy_event(self, src, pct, proxy="", error="", done=0, total=0):
        """预览代理进度回调：队列整体进度 + 当前预览素材完成时切到代理文件。"""
        ahead = " · %d/%d" % (done, total) if total > 1 else ""
        if proxy and src == getattr(self, "_preview_path", ""):
            self._apply_preview_source(proxy, keep_position=True)
            self._set_proxy_status("代理已启用%s" % ahead)
            return
        if error:
            if src == getattr(self, "_preview_path", ""):
                self._set_proxy_status("代理失败，继续用原片")
            return
        if pct:
            self._set_proxy_status("代理生成中 %d%%%s" % (pct, ahead))

    def _apply_preview_source(self, src, keep_position=False):
        player = getattr(self, "player", None)
        if player is None:
            return
        pos = player.position() if keep_position else 0
        playing = player.playbackState() == QMediaPlayer.PlayingState
        self._preview_pending = (pos, playing) if keep_position else None
        player.setSource(QUrl.fromLocalFile(src))
        if not keep_position:
            self._prime_first_frame()

    def _on_preview_media_status(self, status):
        pending = getattr(self, "_preview_pending", None)
        if status == QMediaPlayer.MediaStatus.LoadedMedia and pending:
            self._preview_pending = None
            pos, playing = pending
            if pos:
                self.player.setPosition(pos)
            if playing:
                self.player.play()

    def set_preview_proxy_width(self, width):
        """切换代理开关/分辨率：全部素材排队生成，当前预览立即换源（保留位置）。"""
        try:
            from mixcut import preview_proxy
            width = preview_proxy.set_width(width)
        except Exception:
            return
        cur = getattr(self, "_preview_path", "")
        if not width:
            preview_proxy.cancel()
            if cur:
                self._apply_preview_source(cur, keep_position=True)
            self._set_proxy_status("")
            return
        self._ensure_proxy_listener()
        self._kick_proxy_queue()
        if not cur:
            return
        src = preview_proxy.resolve(cur)
        self._apply_preview_source(src, keep_position=True)
        if src == cur:
            self._set_proxy_status("代理生成中…")

    def _prime_first_frame(self):
        """让预览显示第一帧，但绝不留在“播放中”状态。

        以前这里是 play() 后 180ms 暂停：解码稍慢、或中途又换了素材，定时器
        错过收尾，播放器就会一直在后台播放（还带声音）。现在固定为：
        静音 → 播很短一瞬准备解码 → 到点强制暂停回 0 帧 → 解除静音；
        并用 _prime_pending 标记保证只有一个收尾逻辑在跑。
        """
        player = getattr(self, "player", None)
        if player is None:
            return
        self._set_audio_muted(True)
        if getattr(self, "_prime_timer", None) is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._end_prime)
            self._prime_timer = timer
        timer = self._prime_timer
        timer.stop()
        self._prime_pending = True
        player.play()
        timer.start(180)

    def _preview_visible(self):
        """素材预览页是否真正在界面上：不可见时绝不允许出声播放。"""
        page = getattr(self, "demo_workbench", None)
        if page is None:
            return False
        checker = getattr(page, "is_active", None)
        if callable(checker):
            try:
                return bool(checker())
            except RuntimeError:
                return False
        try:
            return bool(page.isVisible())
        except RuntimeError:
            return False

    def _end_prime(self):
        if not getattr(self, "_prime_pending", False):
            return
        self._prime_pending = False
        player = getattr(self, "player", None)
        if player is None:
            return
        # 收尾必须“无条件停住”：哪怕解码没跟上也只在暂停态，
        # 否则素材预览页就会在后台一直响。
        if player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            player.pause()
        player.setPosition(0)
        # 预热只在可见时允许短暂出声；不可见时保持静音。
        keep_sound = bool(getattr(self, "_user_play_requested", False)) \
            and self._preview_visible()
        self._set_audio_muted(not keep_sound)

    def _set_audio_muted(self, muted):
        audio = getattr(self, "audio_output", None)
        if audio is None:
            return
        try:
            audio.setMuted(bool(muted))
        except Exception:
            pass

    def _user_play(self):
        """用户主动播放：先取消预热定时器，避免刚点播放就被预热逻辑暂停。"""
        if not self._preview_visible():
            # 预览页没显示时点了播放也不出声（播放器藏在后台最容易“莫名其妙有声音”）。
            self.stop_preview_playback()
            return
        self._user_play_requested = True
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._prime_pending = False
        self._set_audio_muted(False)
        if getattr(self, "player", None) is not None:
            self.player.play()

    def _user_pause(self):
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._prime_pending = False
        self._user_play_requested = False
        if getattr(self, "player", None) is not None:
            self.player.pause()

    def _user_stop(self):
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._prime_pending = False
        self._user_play_requested = False
        if getattr(self, "player", None) is not None:
            self.player.stop()
        self._set_audio_muted(False)

    def stop_preview_playback(self):
        """选素材 / 切页 / 打开工程时调用：停掉预览播放并静音。

        用户点的是“素材库里的一个素材”，不该让预览器在后台出声播放。
        """
        self._user_play_requested = False
        timer = getattr(self, "_prime_timer", None)
        if timer is not None:
            timer.stop()
        self._prime_pending = False
        player = getattr(self, "player", None)
        if player is not None:
            try:
                if player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
                    player.pause()
            except Exception:
                pass
        # 停止/暂停后一律静音：避免暂停状态下音频通道仍在输出最后一段。
        self._set_audio_muted(True)

    def _typing_focus(self):
        """焦点在文本输入里时，不要抢 P 键。"""
        from PySide6.QtWidgets import QTextEdit
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        try:
            if event.type() == QEvent.KeyPress:
                key = event.key()
                # 1) 预览已全屏：Esc / P 都退出。全屏是独立窗口，
                #    所以这个过滤器装在 QApplication 上，谁拿到焦点都能收到。
                if self._fullscreen_preview() is not None:
                    if key in (Qt.Key_Escape, Qt.Key_P):
                        self._exit_preview_fullscreen()
                        return True
                # 2) 没全屏：P 在当前预览页进入全屏（素材预览 / AI 分析）
                if key == Qt.Key_P and not self._typing_focus():
                    target = self._current_preview_target()
                    if target is not None:
                        self._enter_preview_fullscreen(target)
                        return True
                if obj is getattr(self, "video_widget", None):
                    if key == Qt.Key_Space:
                        if self.player.playbackState() == QMediaPlayer.PlayingState:
                            self.player.pause()
                        else:
                            self.player.play()
                        return True
        except RuntimeError:
            # 退出时控件可能已被 Qt 释放，事件过滤器静默跳过
            pass
        return super().eventFilter(obj, event)

    def on_player_error(self, error, message):

        self.log(
            f"播放器错误：{message}"
        )
