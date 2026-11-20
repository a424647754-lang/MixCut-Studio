"""主窗口：达芬奇式页面结构（媒体 / 剪辑 / 调色 / 导出）。

原则
----
1. 剪辑页只做剪辑：不放调色控件；调色是独立页面，有自己的真实画面预览。
2. 媒体池、源监视器常驻界面（不弹窗），与达芬奇 Edit 页一致。
3. 所有工程修改都走 UndoStack，撤销/重做、自动保存、导出共用同一份数据。
4. 视觉只用 ui_theme 令牌：无渐变、无大圆角、无阴影。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QGuiApplication, QIcon, QKeySequence,
                           QShortcut)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import Clip, Project, format_timecode, frame_duration_ms, write_text_atomic
from . import ui_theme as T
from .limits import clamp_opacity, clamp_speed, clamp_volume, volume_needs_render_warning
from .color_page import ColorPage
from .deliver_page import DeliverPage
from .edit_page import EditPage
from .icons import icon
from .inspector import InspectorPanel
from .media import build_export_command_full, find_tool, parse_progress_ms, tools_available
from .media_panel import MediaPanel
from .mixer import plan_mix
from .model import ClipPropertyCommand, TrackEditCommand, UndoStack
from .panels import MixPanel
from .playback import PlaybackEngine, PreviewCanvas
from .player_panel import ViewerPanel
from .source_panel import SourcePanel
from .timeline_widget import TimelineWidget

WORKSPACES = ("媒体", "剪辑", "调色", "导出")
PAGE_KEYS = {"媒体": "media", "剪辑": "edit", "调色": "color", "导出": "deliver"}
AUTOSAVE_MS = 20000
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / "MixCutEditor"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
class ExportWorker(QProcess):
    """调用 ffmpeg 导出，并把 -stats 输出解析成百分比。"""

    progress = Signal(int, str)
    finished_ok = Signal(str)
    failed = Signal(str)
    cancelled = Signal()          # 取消不是失败（B-36）

    def __init__(self, command: list[str], output: str, duration_ms: int = 0,
                 parent=None) -> None:
        super().__init__(parent)
        self.setProcessChannelMode(QProcess.MergedChannels)
        self.command = command
        self.output = output
        self.duration_ms = max(1, int(duration_ms))
        self._cancelled = False
        self._reported = False
        self._start_time = 0.0
        self.setTextModeEnabled(True)
        self.readyReadStandardOutput.connect(self._read)
        self.finished.connect(self._done)
        self.errorOccurred.connect(self._error)

    def start_export(self) -> None:
        self._start_time = time.time()
        self.start(self.command[0], self.command[1:])

    def cancel(self) -> None:
        self._cancelled = True
        self.kill()

    def _read(self) -> None:
        chunk = bytes(self.readAllStandardOutput()).decode("utf-8", "replace")
        for raw in chunk.replace("\r", "\n").split("\n"):
            line = raw.strip()
            if not line:
                continue
            position = parse_progress_ms(line)
            if position is not None:
                percent = int(min(99, position * 100 / self.duration_ms))
                elapsed = max(0.1, time.time() - self._start_time)
                remaining = max(0, int(self.duration_ms - position) / 1000.0)
                speed = position / 1000.0 / elapsed if elapsed > 0 else 0.0
                eta = int(remaining / speed) if speed > 0.3 else 0
                note = f"{percent}% · 已渲染 {position} ms / {self.duration_ms} ms"
                if eta:
                    note += f" · 预计剩余 {eta} s"
                self.progress.emit(percent, note)
            else:
                self.progress.emit(-1, line)

    def _done(self, exit_code: int, _status) -> None:
        if self._reported:
            return                        # 取消后 finished 与 error 可能都到，只报一次
        self._reported = True
        if self._cancelled:
            self.cancelled.emit()
            return
        if exit_code == 0 and Path(self.output).exists():
            self.finished_ok.emit(self.output)
        else:
            self.failed.emit(f"ffmpeg 退出码 {exit_code}")

    def _error(self, _error) -> None:
        """进程根本没起来（路径/权限/参数问题），必须报出来，否则界面永远等。"""
        if self._reported:
            return
        self._reported = True
        if self._cancelled:
            self.cancelled.emit()
            return
        self.failed.emit(f"启动导出进程失败：{self.errorString()}")


class ExportDialog(QDialog):
    """独立导出对话框（保留旧入口用）。"""

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        from .panels import ExportPanel

        self.setWindowTitle("导出")
        self.resize(560, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = ExportPanel(self)
        self.panel.set_project(project)
        layout.addWidget(self.panel, 1)
        self.panel.export_requested.connect(self._accept)

    def _accept(self, options: dict) -> None:
        self._options = options
        self.accept()

    def settings_dict(self) -> dict:
        return dict(self._options)


# ---------------------------------------------------------------------------
# 工程设置
# ---------------------------------------------------------------------------
class ProjectSettingsDialog(QDialog):
    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("工程设置")
        self.resize(420, 260)
        self.project = project

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self.name_edit = QLineEdit(project.name)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(160, 7680)
        self.width_spin.setValue(project.width)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(120, 4320)
        self.height_spin.setValue(project.height)
        from . import FPS_PRESETS

        self.fps_combo = QComboBox()
        for label, _fps in FPS_PRESETS:
            self.fps_combo.addItem(label)
        current = project.fps
        for index, (_label, fps) in enumerate(FPS_PRESETS):
            if f"{fps.numerator}/{fps.denominator}" == current:
                self.fps_combo.setCurrentIndex(index)
                break

        for text, widget in (("工程名称", self.name_edit), ("宽度", self.width_spin),
                             ("高度", self.height_spin), ("帧率", self.fps_combo)):
            row = QHBoxLayout()
            label = QLabel(text)
            label.setFixedWidth(72)
            row.addWidget(label)
            row.addWidget(widget, 1)
            layout.addLayout(row)

        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def apply(self) -> None:
        from . import fps_to_str

        self.project.name = self.name_edit.text().strip() or "未命名工程"
        self.project.width = self.width_spin.value()
        self.project.height = self.height_spin.value()
        index = max(0, self.fps_combo.currentIndex())
        from . import FPS_PRESETS

        self.project.fps = fps_to_str(FPS_PRESETS[index][1])


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, project_path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.data_dir = app_data_dir()
        self.project = Project()
        self.project.path = ""
        if project_path:
            try:
                self.project = Project.load(project_path)
            except Exception:  # noqa: BLE001 - 坏工程不应阻断启动
                self.project = Project()
        self.undo = UndoStack()
        self.playhead = 0
        # recent.json 的唯一写入方（B-31）：以前这里写裸字符串列表、
        # 项目管理器写 dict 列表，两边互相覆盖。
        from .project_manager import RecentProjects
        self.recent = RecentProjects(self.data_dir / "recent.json")
        self._interacting = False      # 时间线拖动/修剪进行中 → 冻结自动保存（B-30）
        self._worker: ExportWorker | None = None
        # 导出状态机：IDLE → RUNNING → CANCELLING → IDLE（B-14）
        self._export_state = "IDLE"
        self._export_output = ""
        self._can_write_cache: dict[str, bool] = {}   # 目录可写性只测一次（B-46）
        self._current_page = "剪辑"
        # 防止"选择 → 调色页活动片段 → 选择"这条信号环递归
        self._syncing_selection = False

        self.setWindowTitle(f"{T.APP_NAME} — {self.project.name}")
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        if available is not None:
            width = min(1720, max(1220, available.width() - 80))
            height = min(1000, max(720, available.height() - 90))
        else:
            width, height = 1600, 950
        self.resize(width, height)
        if available is not None:
            center = available.center()
            self.move(center.x() - width // 2, center.y() - height // 2)
        icon_path = Path(__file__).resolve().parent.parent / "mixcut图标.jpg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_widgets()
        self._build_ui()
        self._build_actions()
        self._install_timeline_keys()
        self._wire()
        self.status_bar_build()

        self.apply_workspace("剪辑")
        self._refresh_all()
        self._update_titles()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_MS)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        QTimer.singleShot(320, self._maybe_recover)

    # ------------------------------------------------------------------
    # 组件
    # ------------------------------------------------------------------
    def _cache(self, name: str) -> Path:
        path = self.cache_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _build_widgets(self) -> None:
        # ffmpeg 写缓存统一放在项目内 _cache：某些受限环境不允许它写到
        # APPDATA，放在项目目录可以避免缩略图/调色预览静默失败。
        self.cache_dir = Path(__file__).resolve().parent.parent / "_cache"
        self.media = MediaPanel(self.cache_dir / "thumbs")
        self.media.set_assets(list(self.project.assets))

        self.source = SourcePanel(self.project.fps_fraction,
                                  poster_dir=self._cache("frames"))
        self.program_canvas = PreviewCanvas()
        self.program = ViewerPanel("PROGRAM", self.program_canvas)
        self.playback = PlaybackEngine(self.project, self.program_canvas, self,
                                       poster_dir=self._cache("frames"))
        self.inspector = InspectorPanel()

        self.timeline = TimelineWidget(self.project, self.undo)
        self.timeline.setMinimumHeight(150)

        self.color_page = ColorPage(self.cache_dir / "grade")
        self.deliver_page = DeliverPage()
        self.deliver_page.set_project(self.project)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("PageRoot")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_page_bar())

        self.pages = QStackedWidget()
        self.media_page = self._build_media_page()
        self.edit_page = EditPage()
        self.pages.addWidget(self.media_page)
        self.pages.addWidget(self.edit_page)
        self.pages.addWidget(self.color_page)
        self.pages.addWidget(self.deliver_page)
        layout.addWidget(self.pages, 1)

        self.setCentralWidget(central)
        self.menuBar().setFixedHeight(T.MENUBAR_HEIGHT)

    def _build_page_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("PageBar")
        bar.setFixedHeight(T.PAGEBAR_HEIGHT)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 10, 0)
        layout.setSpacing(0)

        self._page_group = QButtonGroup(self)
        self._page_group.setExclusive(True)
        self._page_buttons: dict[str, QPushButton] = {}
        for name in WORKSPACES:
            button = QPushButton(name)
            button.setObjectName("Page")
            button.setCheckable(True)
            button.setFixedHeight(T.PAGEBAR_HEIGHT)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, n=name: self.apply_workspace(n))
            self._page_group.addButton(button)
            self._page_buttons[name] = button
            layout.addWidget(button)

        layout.addStretch(1)
        self.page_hint = QLabel("Ctrl+Z 撤销 · Space 播放 · Ctrl+K 切割 · M 标记")
        self.page_hint.setObjectName("PageHint")
        layout.addWidget(self.page_hint)

        self.btn_import = QPushButton("导入素材")
        self.btn_import.setObjectName("Chip")
        self.btn_import.setIcon(icon("import", 13, T.TEXT_DIM))
        self.btn_import.setToolTip("导入媒体文件（Ctrl+I）")
        self.btn_import.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.btn_import)

        self.btn_export = QPushButton("导出成片")
        self.btn_export.setObjectName("Chip")
        self.btn_export.setIcon(icon("deliver", 13, T.TEXT_DIM))
        self.btn_export.setToolTip("进入导出页（Ctrl+Shift+E）")
        self.btn_export.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.btn_export)
        return bar

    def _build_media_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.media_host = QWidget()
        self.media_host.setObjectName("PanelRoot")
        self.media_host.setMinimumWidth(320)
        self.media_host.setMaximumWidth(460)
        self._media_host_layout = QVBoxLayout(self.media_host)
        self._media_host_layout.setContentsMargins(0, 0, 0, 0)
        self._media_host_layout.setSpacing(0)

        self.source_host = QWidget()
        self.source_host.setObjectName("PageRoot")
        self._source_host_layout = QVBoxLayout(self.source_host)
        self._source_host_layout.setContentsMargins(0, 0, 0, 0)
        self._source_host_layout.setSpacing(0)

        layout.addWidget(self.media_host, 0)
        layout.addWidget(self.source_host, 1)
        return page

    # ------------------------------------------------------------------
    def status_bar_build(self) -> None:
        bar = self.statusBar()
        bar.setFixedHeight(T.STATUSBAR_HEIGHT)
        self.tc_status = QLabel("00:00:00:00")
        self.tc_status.setObjectName("Timecode")
        self.project_status = QLabel("")
        self.render_status = QLabel("就绪")
        self.duration_status = QLabel("00:00:00:00")
        for widget in (self.tc_status, self.project_status, self.render_status,
                       self.duration_status):
            widget.setMinimumHeight(T.STATUSBAR_HEIGHT - 2)
            bar.addPermanentWidget(widget)

    # ------------------------------------------------------------------
    # 页面
    # ------------------------------------------------------------------
    def apply_workspace(self, name: str) -> None:
        if name == "混剪":
            self.open_mix_dialog()
            name = self._current_page
            self._page_buttons[name].setChecked(True)
            return
        if name not in WORKSPACES:
            name = "剪辑"
        self._current_page = name
        self._page_buttons[name].setChecked(True)
        index = WORKSPACES.index(name)
        self.pages.setCurrentIndex(index)
        self._place_shared_widgets(name)
        # 调色页不在前台时不允许起 ffmpeg 抽帧（B-16）
        self.color_page.set_page_active(name == "调色")

        if name == "调色":
            self.playback.pause()
            self.program.transport.set_playing(False)
            self._refresh_color_page()
        if name == "导出":
            self.deliver_page.refresh()

    def _place_shared_widgets(self, name: str) -> None:
        """媒体池与源监视器在「媒体」和「剪辑」页之间共用同一个实例。"""
        if name == "媒体":
            if self.media.parent() is not self.media_host:
                self.media.setParent(None)
                self._media_host_layout.addWidget(self.media)
            if self.source.parent() is not self.source_host:
                self.source.setParent(None)
                self._source_host_layout.addWidget(self.source)
            return

        if name == "剪辑":
            if self.media.parent() is not self.edit_page.media_slot:
                self.media.setParent(None)
                self.edit_page.set_media(self.media)
            if self.source.parent() is not self.edit_page._source_host:
                self.source.setParent(None)
                self.edit_page.set_source(self.source)
            self.edit_page.set_program(self.program)
            self.edit_page.set_inspector(self.inspector)
            self.edit_page.set_timeline(self.timeline)

    def reset_layout(self) -> None:
        self.edit_page.body.setSizes([T.MEDIA_PANEL_WIDTH, 1020, T.INSPECTOR_WIDTH])
        self.edit_page.center_split.setSizes([560, 340])
        self.apply_workspace("剪辑")

    def _toggle_media_panel(self, checked: bool) -> None:
        self.media.setVisible(checked)
        self._media_toggle.setText("隐藏素材面板" if not checked else "显示素材面板")
        # 真正收起左侧槽位：折叠 splitter 第 0 段并去掉宽度约束，不再留白
        slot = self.edit_page.media_slot
        if checked:
            slot.setMinimumWidth(T.PANEL_LEFT_MIN)
            slot.setMaximumWidth(T.MEDIA_PANEL_WIDTH)
            self.edit_page.body.setSizes([T.MEDIA_PANEL_WIDTH, 1020, T.INSPECTOR_WIDTH])
        else:
            slot.setMinimumWidth(0)
            slot.setMaximumWidth(0)
            self.edit_page.body.setSizes([0, 1020, T.INSPECTOR_WIDTH])
        self.edit_page.body.refresh()

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------
    def _wire(self) -> None:
        self.media.status.connect(self.message)
        self.media.insert_requested.connect(self._insert_asset)
        self.media.asset_imported.connect(self._on_asset_imported)
        self.media.asset_changed.connect(self._on_asset_changed)
        self.media.remove_requested.connect(self._remove_asset)
        self.media.open_source_requested.connect(self.open_source)
        self.media.selection_changed.connect(self._on_media_selection)
        self.media.thumbnail_updated.connect(self._on_thumbnail)
        self.media.waveform_updated.connect(self._on_waveform)

        self.source.insert_requested.connect(self._on_source_insert)

        self.playback.position_changed.connect(self._on_engine_position)
        self.playback.playing_changed.connect(self._on_playing)
        self.playback.status.connect(self.message)
        self.playback.perf.connect(self.program.transport.set_perf)

        self.program.transport.play_toggled.connect(self._on_transport_play)
        self.program.transport.step_requested.connect(self._on_step)
        self.program.transport.volume_changed.connect(
            lambda value: self.playback.set_master_volume(value / 100.0))
        self.program.transport.fit_requested.connect(self._fit_viewer)
        self.program.transport.fullscreen_requested.connect(self._toggle_fullscreen)

        self.timeline.playhead_changed.connect(self.seek_to)
        self.timeline.selection_changed.connect(self._on_selection)
        self.timeline.project_edited.connect(self._on_edited)
        self.timeline.status.connect(self.message)
        self.timeline.track_changed.connect(self._on_track_changed)
        self.timeline.source_open_requested.connect(self.open_source)
        self.timeline.interaction_changed.connect(self._on_interaction_changed)
        self.timeline.zoom_changed.connect(self._on_zoom_changed)

        self.inspector.changed.connect(self._apply_clip_props)
        self.inspector.action_requested.connect(self._on_inspector_action)

        self.color_page.clip_activated.connect(self._activate_color_clip)
        self.color_page.grade_apply.connect(self._apply_grade)
        self.color_page.grade_reset.connect(self._reset_grade)
        self.color_page.status.connect(self.message)

        self.deliver_page.export_requested.connect(self._export_with_options)
        self.deliver_page.cancel_requested.connect(self._cancel_export)
        self.deliver_page.reveal_requested.connect(self._reveal_path)

        self.btn_import.clicked.connect(self.media.choose_files)
        self.btn_export.clicked.connect(lambda: self.apply_workspace("导出"))

        self.edit_page.btn_tool_select.toggled.connect(self._on_tool_mode)
        self.edit_page.btn_tool_razor.toggled.connect(self._on_tool_mode)
        self.edit_page.btn_snap.toggled.connect(self._on_snap)
        self.edit_page.btn_blade.clicked.connect(self.timeline.split_at_playhead)
        self.edit_page.btn_delete.clicked.connect(lambda: self.timeline.delete_selected(False))
        self.edit_page.btn_ripple.clicked.connect(self.timeline.ripple_delete_selected)
        self.edit_page.btn_marker.clicked.connect(self.timeline.add_marker_at_playhead)
        self.edit_page.btn_track_add.clicked.connect(lambda: self.timeline.add_track("video"))
        self.edit_page.btn_track_add_audio.clicked.connect(
            lambda: self.timeline.add_track("audio"))
        self.edit_page.btn_zoom_in.clicked.connect(self.timeline.zoom_in)
        self.edit_page.btn_zoom_out.clicked.connect(self.timeline.zoom_out)
        self.edit_page.btn_zoom_fit.clicked.connect(self.timeline.zoom_fit)

    def _on_tool_mode(self, checked: bool) -> None:
        sender = self.sender()
        if sender is self.edit_page.btn_tool_razor and checked:
            self.edit_page.btn_tool_select.setChecked(False)
        elif sender is self.edit_page.btn_tool_select and checked:
            self.edit_page.btn_tool_razor.setChecked(False)
        elif not checked and not (self.edit_page.btn_tool_select.isChecked()
                                  or self.edit_page.btn_tool_razor.isChecked()):
            self.edit_page.btn_tool_select.setChecked(True)

    def _on_snap(self, checked: bool) -> None:
        self.timeline.snap_enabled = bool(checked)

    # ------------------------------------------------------------------
    # 菜单 / 快捷键
    # ------------------------------------------------------------------
    def _text_has_focus(self) -> bool:
        """焦点在可编辑文本/数值输入里时，裸键与编辑键必须让给输入框（B-13）。

        这些 action 用的是 WindowShortcut，会先于控件 keyPress 命中，
        所以在触发时按焦点判断，而不是改快捷键上下文（改上下文会让菜单失效）。
        """
        from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QLineEdit,
                                       QPlainTextEdit, QTextEdit)

        focus = QApplication.focusWidget()
        return isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))

    def _guard(self, slot):
        def run(*_args):
            if self._text_has_focus():
                return
            slot()
        return run

    def _act(self, menu: QMenu, text: str, shortcut, slot, icon_name: str | None = None) -> QAction:
        action = QAction(text, self)
        if icon_name:
            action.setIcon(icon(icon_name, 16, T.TEXT_DIM))
        if shortcut:
            action.setShortcut(QKeySequence(shortcut) if isinstance(shortcut, str) else shortcut)
            action.setShortcutContext(Qt.WindowShortcut)
        action.triggered.connect(lambda _=False: slot())
        menu.addAction(action)
        self.addAction(action)
        return action

    def _install_timeline_keys(self) -> None:
        """时间线获得焦点时可用：方向键逐帧、Shift+方向键十帧、Delete 删除。

        挂在时间线控件上（WidgetWithChildrenShortcut），避免抢走 Inspector
        数字输入框里的方向键。
        """
        pairs = [
            ("Left", lambda: self._on_step(-1)),
            ("Right", lambda: self._on_step(1)),
            ("Shift+Left", lambda: self._on_step(-10)),
            ("Shift+Right", lambda: self._on_step(10)),
            ("Del", lambda: self.timeline.delete_selected(False)),
            ("Shift+Del", self.timeline.ripple_delete_selected),
            # Alt+方向键：把选中片段前后挪一帧（走命令层，可撤销）
            ("Alt+Left", lambda: self.timeline.nudge_selection(-self._frame_ms())),
            ("Alt+Right", lambda: self.timeline.nudge_selection(self._frame_ms())),
        ]
        self._timeline_keys = []
        for keys, slot in pairs:
            shortcut = QShortcut(QKeySequence(keys), self.timeline)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            self._timeline_keys.append(shortcut)

    def _build_actions(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("文件")
        self._act(file_menu, "项目管理…", None, self._open_project_manager)
        file_menu.addSeparator()
        self._act(file_menu, "新建工程", "Ctrl+N", self.new_project)
        self._act(file_menu, "打开工程…", "Ctrl+O", self.open_project)
        self._act(file_menu, "保存", "Ctrl+S", self.save_project)
        self._act(file_menu, "另存为…", "Ctrl+Shift+S", self.save_project_as)
        file_menu.addSeparator()
        self._act(file_menu, "导入素材…", "Ctrl+I", self.media.choose_files)
        self._act(file_menu, "导出成片…", "Ctrl+Shift+E", lambda: self.apply_workspace("导出"))
        file_menu.addSeparator()
        self._act(file_menu, "工程设置…", None, self.project_settings)
        self._act(file_menu, "退出", None, self.close)

        edit_menu = bar.addMenu("编辑")
        self._act(edit_menu, "撤销", "Ctrl+Z", self._guard(self.do_undo))
        self._act(edit_menu, "重做", "Ctrl+Shift+Z", self._guard(self.do_redo))
        edit_menu.addSeparator()
        self._act(edit_menu, "剪切", "Ctrl+X", self._guard(self.timeline.cut_selection))
        self._act(edit_menu, "复制", "Ctrl+C", self._guard(self.timeline.copy_selection))
        self._act(edit_menu, "粘贴", "Ctrl+V", self._guard(self.timeline.paste))
        self._act(edit_menu, "全选", "Ctrl+A", self._guard(self.timeline.select_all))
        edit_menu.addSeparator()
        self._act(edit_menu, "在播放头切割", "Ctrl+K", self.timeline.split_at_playhead)
        self._act(edit_menu, "删除片段", "Del",
                  self._guard(lambda: self.timeline.delete_selected(False)))
        self._act(edit_menu, "波纹删除", "Shift+Del",
                  self._guard(self.timeline.ripple_delete_selected))
        self._act(edit_menu, "添加标记", "M",
                  self._guard(self.timeline.add_marker_at_playhead))
        self._act(edit_menu, "插入到播放头", None, self._insert_selected_asset)
        edit_menu.addSeparator()
        self._act(edit_menu, "一键混剪…", None, self.open_mix_dialog)

        view_menu = bar.addMenu("视图")
        for index, name in enumerate(WORKSPACES, start=1):
            self._act(view_menu, f"{name}页", f"Ctrl+{index}",
                      lambda n=name: self.apply_workspace(n))
        view_menu.addSeparator()
        self._media_toggle = QAction("显示素材面板", self)
        self._media_toggle.setCheckable(True)
        self._media_toggle.setChecked(True)
        self._media_toggle.triggered.connect(self._toggle_media_panel)
        view_menu.addAction(self._media_toggle)
        self._act(view_menu, "重置布局", None, self.reset_layout)
        self._act(view_menu, "全屏预览", "F11", self._toggle_fullscreen)

        play_menu = bar.addMenu("播放")
        self._act(play_menu, "播放 / 暂停", "Space", self._guard(self.toggle_play))
        self._act(play_menu, "上一帧", None, lambda: self._on_step(-1))
        self._act(play_menu, "下一帧", None, lambda: self._on_step(1))
        self._act(play_menu, "跳到开头", "Home", self._guard(lambda: self.seek_to(0)))
        self._act(play_menu, "跳到结尾", "End",
                  self._guard(lambda: self.seek_to(self.project.duration())))
        play_menu.addSeparator()
        self._loop_action = QAction("循环播放", self)
        self._loop_action.setCheckable(True)
        self._loop_action.setShortcut(QKeySequence("Shift+L"))
        self._loop_action.setShortcutContext(Qt.WindowShortcut)
        self._loop_action.toggled.connect(self._on_loop)
        play_menu.addAction(self._loop_action)
        self.addAction(self._loop_action)

        help_menu = bar.addMenu("帮助")
        self._act(help_menu, "快捷键说明", "F1", self.show_help)

    # ------------------------------------------------------------------
    # 播放
    # ------------------------------------------------------------------
    def toggle_play(self) -> None:
        if self.playback.is_playing:
            self.playback.pause()
        else:
            self._start_playback()

    def _start_playback(self) -> None:
        if self.project.duration() <= 0:
            self.message("时间线为空，先拖入素材")
            return
        if self.playhead >= self.project.duration():
            self.seek_to(0)
        self.playback.set_position(self.playhead)
        self.playback.play()

    def _on_transport_play(self, playing: bool) -> None:
        if playing:
            self._start_playback()
        else:
            self.playback.pause()

    def _on_playing(self, playing: bool) -> None:
        self.program.transport.set_playing(playing)
        self.render_status.setText("播放中" if playing else "已暂停")

    def _on_loop(self, enabled: bool) -> None:
        self.playback.set_loop(bool(enabled))
        self.message("循环播放已开启" if enabled else "循环播放已关闭")

    def _frame_ms(self) -> int:
        """一帧对应的毫秒（绝对步进用，四舍五入到整毫秒）。"""
        return frame_duration_ms(self.project.fps_fraction)

    def _on_step(self, direction: int) -> None:
        self.playback.pause()
        step = frame_duration_ms(self.project.fps_fraction)
        self.seek_to(max(0, min(self.project.duration(), self.playhead + direction * step)))

    def _on_engine_position(self, ms: int) -> None:
        self.playhead = int(ms)
        if self.timeline.playhead != self.playhead:
            self.timeline.set_playhead(self.playhead, notify=False)
        self._sync_timecode(self.playhead)

    def _sync_timecode(self, ms: int) -> None:
        fps = self.project.fps_fraction
        text = format_timecode(ms, fps)
        self.program.transport.set_timecode(text)
        self.program.transport.set_duration(format_timecode(self.project.duration(), fps))
        self.tc_status.setText(text)
        self.duration_status.setText(f"时长 {format_timecode(self.project.duration(), fps)}")
        if self.color_page.active_clip is not None and self._current_page == "调色":
            self.color_page.set_position(ms)

    def seek_to(self, ms: int) -> None:
        self.playhead = max(0, int(ms))
        self.playback.set_position(self.playhead)
        if self.timeline.playhead != self.playhead:
            self.timeline.set_playhead(self.playhead, notify=False)
        self._sync_timecode(self.playhead)

    def _fit_viewer(self) -> None:
        self.program_canvas.set_zoom(0.0)
        self.program.set_scale_text("适应")

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ------------------------------------------------------------------
    # 素材
    # ------------------------------------------------------------------
    def _insert_selected_asset(self) -> None:
        assets = self.media.selected_assets()
        if not assets:
            self.message("先在素材库里选一个素材")
            return
        self._insert_asset(assets[0].id)

    def _asset_kind(self, asset_id: str) -> str:
        asset = self.project.asset(asset_id)
        if asset is None:
            return "video"
        return "audio" if asset.kind == "audio" else "video"

    def _insert_asset(self, asset_id: str) -> None:
        clip = self.timeline.insert_clip(asset_id, self._asset_kind(asset_id), self.playhead)
        if clip is not None:
            self.message(f"已插入 {clip.name}")
            self.timeline.select(clip.id)          # 插入后选中，Inspector 立即有内容
            # 插入后立刻同步一次画面，否则暂停状态下的 PROGRAM 只会显示"无画面"
            self.playback.set_position(self.timeline.playhead, preview=True)
            self._on_selection([clip.id])

    def _on_asset_imported(self, asset) -> None:
        self.project.add_asset(asset)
        self.media.set_assets(list(self.project.assets))
        if self.source.asset is None:
            self.source.open_asset(asset)   # 导入即可在 SOURCE 里看到画面
        self._update_titles()

    def _on_asset_changed(self, asset, old_path: str) -> None:
        current = self.project.asset(asset.id)
        if current is not None:
            current.path = asset.path
            current.name = asset.name
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.asset_id == asset.id:
                    clip.path = asset.path
        self.timeline.refresh()

    def _remove_asset(self, asset_id: str) -> None:
        # B-11：删除素材前必须检查时间线引用，只允许"无引用"或"级联删除且可撤销"
        refs = self.timeline.asset_references(asset_id)
        if refs:
            tracks = {track.name for track, _clip in refs}
            answer = QMessageBox.question(
                self, "移除素材",
                f"该素材在时间线上被 {len(refs)} 个片段引用"
                f"（{'、'.join(sorted(tracks))}）。\n\n"
                "选择「是」：同时删除这些片段（可用 Ctrl+Z 撤销）。\n"
                "选择「取消」：不做任何改动。",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                self.message("已取消：素材仍被时间线引用")
                return
            removed = self.timeline.remove_asset_and_clips(asset_id)
            self.message(f"已删除 {removed} 个引用片段，可 Ctrl+Z 撤销")
            self.media.set_assets(list(self.project.assets))
            return
        self.project.assets = [a for a in self.project.assets if a.id != asset_id]
        self.media.set_assets(list(self.project.assets))
        self.message("已从媒体库移除")

    def _on_media_selection(self, asset_ids: list[str]) -> None:
        if not asset_ids:
            return
        asset = self.project.asset(asset_ids[0])
        if asset is not None and self._current_page == "媒体":
            self.source.open_asset(asset)

    def open_source(self, asset_id: str) -> None:
        asset = self.project.asset(asset_id)
        if asset is None:
            return
        self.source.set_fps(self.project.fps_fraction)
        self.source.open_asset(asset)
        if self._current_page not in ("媒体", "剪辑"):
            self.apply_workspace("剪辑")

    def _on_source_insert(self, asset_id: str, source_in: int, duration: int, append: bool) -> None:
        at = self.project.duration() if append else self.playhead
        clip = self.timeline.insert_clip(asset_id, self._asset_kind(asset_id), at,
                                        source_in=source_in, duration=duration)
        if clip is not None:
            self.message(f"已插入 {clip.name}")

    def _on_thumbnail(self, asset_id: str, pixmap) -> None:
        self.timeline.set_thumbnails(dict(self.media.thumbnails))
        if self._current_page == "调色":
            self._refresh_color_page()

    def _on_waveform(self, asset_id: str, peaks) -> None:
        self.timeline.set_waveforms(dict(self.media.waveforms))

    # ------------------------------------------------------------------
    # 选择 / 属性
    # ------------------------------------------------------------------
    def _selected_clip(self) -> tuple[object, Clip | None]:
        for clip_id in self.timeline.selection():
            track, clip = self.project.locate(clip_id)
            if clip is not None:
                return track, clip
        return None, None

    def _on_selection(self, ids: list[str]) -> None:
        track, clip = self._selected_clip()
        if clip is None:
            self.inspector.set_clip(None)
            return
        asset = self.project.asset(clip.asset_id)
        self.inspector.set_clip(clip, getattr(track, "name", ""),
                                asset.name if asset else clip.path)
        self.inspector.set_timecodes(self.project.fps_fraction)
        # 调色页的活动片段跟随时间线选择（只在该片段确实变了时才切换，
        # 避免每次刷新都跳一次，也不与调色页自己的点击互通成环）
        if self._current_page == "调色" and not self._syncing_selection:
            active = self.color_page.active_clip
            if active is None or active.id != clip.id:
                self._syncing_selection = True
                try:
                    self._activate_color_clip(clip.id)
                finally:
                    self._syncing_selection = False

    def _apply_clip_props(self, label: str, updates: dict) -> None:
        targets: list[Clip] = []
        for clip_id in self.timeline.selection():
            _track, found = self.project.locate(clip_id)
            if found is not None:
                targets.append(found)
        if not targets:
            return
        updates = dict(updates)

        # 时间/几何字段只允许单选编辑：多选时统一写入会让多个片段叠在同一时间点（B-34）
        time_keys = {"start", "duration", "source_in"}
        if len(targets) > 1 and (time_keys & set(updates)):
            kept = {k: v for k, v in updates.items() if k not in time_keys}
            self.message("开始/时长/源入点需单选编辑；已把播放类属性应用到全部选中片段")
            updates = kept
            if not updates:
                return

        updates = self._normalize_clip_updates(targets[0], updates)

        # 一个 Command 里记录多个片段 → 多选修改仍是"一步"撤销
        command = ClipPropertyCommand(self.project, label or "修改片段属性")
        for clip in targets:
            before = {key: getattr(clip, key) for key in updates}
            command.record(clip.id, before, dict(updates))
        if not command.empty:
            self.undo.push(command)
        self.timeline.refresh()
        self._on_selection(list(self.timeline.selection()))
        self.seek_to(self.playhead)
        self._update_titles()
        if "volume" in updates and volume_needs_render_warning(updates["volume"]):
            self.message("音量 > 1.0：预览最多 100%，增益只在成片中生效")

    def _normalize_clip_updates(self, clip: Clip, updates: dict) -> dict:
        """命令层归一：值域单点 + 素材越界校验（B-15 / B-33）。"""
        result = dict(updates)
        project = self.project
        if "start" in result:
            result["start"] = max(0, int(result["start"]))
        if "duration" in result:
            result["duration"] = max(frame_duration_ms(project.fps_fraction),
                                     int(result["duration"]))
        if "speed" in result:
            result["speed"] = clamp_speed(result["speed"])
        if "volume" in result:
            result["volume"] = clamp_volume(result["volume"])
        if "opacity" in result:
            result["opacity"] = clamp_opacity(result["opacity"])
        if "source_in" in result:
            result["source_in"] = max(0, int(result["source_in"]))

        # 素材长度越界：source_in + duration*speed 不得超出素材
        asset = project.asset(clip.asset_id) if clip.asset_id else None
        asset_duration = int(getattr(asset, "duration", 0) or 0)
        if asset_duration > 0:
            speed = float(result.get("speed", clip.speed))
            duration = int(result.get("duration", clip.duration))
            source_in = int(result.get("source_in", clip.source_in))
            span = int(round(duration * speed))
            if source_in + span > asset_duration:
                source_in = max(0, asset_duration - span)
                if span > asset_duration:      # 变速后比素材还长：先压时长
                    duration = int(asset_duration / max(0.1, speed))
                    span = int(round(duration * speed))
                    source_in = max(0, asset_duration - span)
                    result["duration"] = max(
                        frame_duration_ms(project.fps_fraction), duration)
                if "source_in" in result:
                    result["source_in"] = source_in
                elif source_in != clip.source_in:
                    result["source_in"] = source_in
                self.message("源入点/时长超出素材长度，已自动收进素材范围")
        return result

    def _on_inspector_action(self, key: str) -> None:
        if key == "split":
            self.timeline.split_at_playhead()
        elif key == "delete":
            self.timeline.delete_selected(False)
        elif key == "ripple_delete":
            self.timeline.ripple_delete_selected()

    # ------------------------------------------------------------------
    # 编辑回调
    # ------------------------------------------------------------------
    def _on_edited(self) -> None:
        # 注意：不能调 playback.set_project()——它会把播放位置/音频跳回 0。
        # 工程对象没变，只是片段列表变了，刷新后在当前播放头位置重同步即可。
        self._refresh_all()
        self.playback.set_position(self.playhead, preview=True)

    def _on_track_changed(self) -> None:
        self.timeline.refresh()
        self._update_titles()

    def do_undo(self) -> None:
        if self.undo.undo():
            self._refresh_all()
            self.message("已撤销")

    def do_redo(self) -> None:
        if self.undo.redo():
            self._refresh_all()
            self.message("已重做")

    def _refresh_all(self) -> None:
        # 只刷新视图，不"重新装载工程"：set_project 会清掉选择/播放头/缓存，
        # 那正是"编辑后 Inspector 不刷新、Del 失灵、播放头画回起点"的成因（B-05）。
        self.timeline.rebind_project(self.project)
        self.timeline.refresh()
        self.deliver_page.set_project(self.project)
        # 选择态的唯一真源是时间线：这里把（可能已被替换成新对象的）片段
        # 按 id 重新推给 Inspector / 调色页，避免面板持有过期 Clip。
        self._on_selection(list(self.timeline.selection()))
        self._refresh_color_page()
        if self.timeline.playhead != self.playhead:
            self.timeline.set_playhead(self.playhead, notify=False)
        self._sync_timecode(self.playhead)
        self._update_titles()

    def _update_titles(self) -> None:
        name = self.project.name
        if self.undo.dirty:
            name += " *"
        self.setWindowTitle(f"{T.APP_NAME} — {name}")
        self.project_status.setText(
            f"{name} · {self.project.width}×{self.project.height} · "
            f"{float(self.project.fps_fraction):g} fps")
        self.program.transport.set_duration(
            format_timecode(self.project.duration(), self.project.fps_fraction))
        self.tc_status.setText(format_timecode(self.playhead, self.project.fps_fraction))
        self.duration_status.setText(
            f"时长 {format_timecode(self.project.duration(), self.project.fps_fraction)}")

    def message(self, text: str) -> None:
        # 只走状态栏的临时消息，避免和右侧常驻"播放中/已暂停"挤在一起重复显示
        if hasattr(self, "statusBar"):
            self.statusBar().showMessage(text, 4000)

    # ------------------------------------------------------------------
    # 调色
    # ------------------------------------------------------------------
    def _refresh_color_page(self) -> None:
        self.color_page.set_project(self.project.fps_fraction,
                                    self.project.width, self.project.height)
        entries = []
        for track in self.project.video_tracks():
            for clip in sorted(track.clips, key=lambda c: c.start):
                if not clip.path:
                    continue
                thumb = self.media.thumbnails.get(clip.asset_id)
                entries.append((clip, thumb, track.name))
        self.color_page.set_clips(entries)

        active = self.color_page.active_clip
        if active is not None:
            _track, found = self.project.locate(active.id)
            if found is not None:
                self.color_page.set_active_clip(found, self._track_name(found))
                return
        track, clip = self._selected_clip()
        if clip is None and entries:
            clip = entries[0][0]
            track = self.project.locate(clip.id)[0]
        self.color_page.set_active_clip(clip, getattr(track, "name", ""))

    def _track_name(self, clip: Clip) -> str:
        track, _clip = self.project.locate(clip.id)
        return getattr(track, "name", "")

    def _activate_color_clip(self, clip_id: str) -> None:
        track, clip = self.project.locate(clip_id)
        if clip is None:
            return
        # 先设活动片段再选中时间线：否则 select() 触发 _on_selection 时
        # 调色页的活动片段还是旧值，会再次回到这里（递归）
        self.color_page.set_active_clip(clip, getattr(track, "name", ""))
        self.timeline.select([clip_id])
        self.seek_to(clip.start)

    def _apply_grade(self, grade: dict) -> None:
        clip = self.color_page.active_clip
        if clip is None:
            self.message("先选一个片段")
            return
        before = {key: getattr(clip, key) for key in grade}
        command = ClipPropertyCommand(self.project, "调色")
        command.record(clip.id, before, dict(grade))
        if not command.empty:
            self.undo.push(command)
        self.timeline.refresh()
        self.color_page.set_active_clip(clip, self._track_name(clip))
        # 剪辑页 PROGRAM 也立刻刷新成带调色的定格帧，避免"调完看不到效果"
        self.playback.set_position(self.playhead, preview=True)
        self.message("调色已应用")

    def _reset_grade(self) -> None:
        clip = self.color_page.active_clip
        if clip is None:
            return
        neutral = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0,
                   "temperature": 0.0, "exposure": 0.0, "tint": 0.0,
                   "sharpness": 0.0}
        before = {key: getattr(clip, key) for key in neutral}
        command = ClipPropertyCommand(self.project, "重置调色")
        command.record(clip.id, before, neutral)
        if not command.empty:
            self.undo.push(command)
        self.timeline.refresh()
        self.color_page.set_active_clip(clip, self._track_name(clip))
        self.playback.set_position(self.playhead, preview=True)
        self.message("调色已重置")

    # ------------------------------------------------------------------
    # 混剪
    # ------------------------------------------------------------------
    def open_mix_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("一键混剪")
        dialog.resize(420, 520)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        panel = MixPanel(dialog)
        layout.addWidget(panel)
        panel.set_available(len(self.project.assets),
                            len(self.media.selected_assets()))

        def run(options: dict) -> None:
            self._on_mix_requested(options)
            dialog.accept()

        panel.mix_requested.connect(run)
        dialog.exec()

    def _on_mix_requested(self, options: dict) -> None:
        from .mixer import append_offset, build_clips

        assets = self.media.assets_for_mix(options.get("selected_only", False))
        if not assets:
            self.message("媒体库为空，无法混剪")
            return
        plan = plan_mix(assets, int(options.get("total_ms", 30000)),
                        int(options.get("segment_ms", 3000)),
                        order=options.get("order", "sequence"))
        if not plan:
            self.message("混剪计划为空")
            return
        track = self.project.first_track("video") or self.timeline.ensure_track("video")
        start = append_offset(self.project, track.id) if options.get("append") else self.playhead
        clips = build_clips(plan, start)
        self.timeline.replace_track_clips(track.id, clips, "混剪成片")
        self.message(f"混剪完成：{len(clips)} 段")

    # ------------------------------------------------------------------
    # 工程
    # ------------------------------------------------------------------
    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        self.project = Project()
        self.undo.clear()
        self.playhead = 0
        self._reload_project()

    def load_project_path(self, path: str) -> bool:
        try:
            project = Project.load(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开工程", f"无法读取工程：{exc}")
            return False
        self.project = project
        self.undo.clear()
        self.playhead = 0
        self._reload_project()
        self._remember_recent(path)
        return True

    def _reload_project(self) -> None:
        self.playback.set_project(self.project)
        self.timeline.set_project(self.project)
        self.media.set_assets(list(self.project.assets))
        self.source.set_fps(self.project.fps_fraction)
        self.source.close_asset()
        self.playback.shutdown()
        self._refresh_all()
        self.apply_workspace(self._current_page)

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开工程", "", "MixCut 工程 (*.mixcut)")
        if path:
            self.load_project_path(path)

    def _open_project_manager(self) -> None:
        from .project_manager import ProjectManagerDialog
        dialog = ProjectManagerDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        path = dialog.selected_path
        if not path:
            return
        if not self._confirm_discard():
            return
        self.load_project_path(path)

    def save_project(self) -> None:
        if not self.project.path:
            self.save_project_as()
            return
        try:
            self.project.save(self.project.path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.undo.mark_clean()
        self._remember_recent(self.project.path)
        self._update_titles()
        self.message("工程已保存")

    def save_project_as(self) -> None:
        default = self.project.path or str(Path.home() / f"{self.project.name}.mixcut")
        path, _ = QFileDialog.getSaveFileName(self, "另存为", default, "MixCut 工程 (*.mixcut)")
        if not path:
            return
        if not path.endswith(".mixcut"):
            path += ".mixcut"
        self.project.path = path
        self.save_project()

    def project_settings(self) -> None:
        dialog = ProjectSettingsDialog(self.project, self)
        if dialog.exec() == QDialog.Accepted:
            dialog.apply()
            self._refresh_all()
            self.message("工程设置已更新")

    def _confirm_discard(self) -> bool:
        if not self.undo.dirty:
            return True
        answer = QMessageBox.question(
            self, "未保存的修改", "当前工程有未保存的修改，要先保存吗？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Save:
            self.save_project()
            return not self.undo.dirty
        return answer == QMessageBox.Discard

    def _remember_recent(self, path: str) -> None:
        """最近工程只由 `RecentProjects` 一个写入方维护（B-31）。"""
        from .project_manager import RecentProjects

        store = getattr(self, "recent", None)
        if store is None or Path(store.path) != self.data_dir / "recent.json":
            store = self.recent = RecentProjects(self.data_dir / "recent.json")
        try:
            store.add(path, getattr(self, "project", None))
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 自动保存 / 恢复
    # ------------------------------------------------------------------
    def _on_interaction_changed(self, interacting: bool) -> None:
        self._interacting = bool(interacting)

    def _on_zoom_changed(self, pixels_per_sec: float) -> None:
        """编辑页缩放读数跟随实际比例（B-41）。"""
        self.edit_page.zoom_label.setText(f"{int(round(pixels_per_sec))}%")

    def _autosave(self) -> None:
        if not self.undo.dirty:
            return
        if self._interacting:
            return   # 拖动/修剪中间态不入恢复文件（B-30）
        try:
            target = self.data_dir / "recovery.autosave"
            write_text_atomic(target,
                              json.dumps(self.project.to_dict(), ensure_ascii=False))
            self.autosaved_at = time.time()
        except OSError:
            pass

    def _maybe_recover(self) -> None:
        if os.environ.get("MIXCUT_NO_RECOVERY"):
            return   # 自动化测试用：避免恢复对话框阻塞脚本
        target = self.data_dir / "recovery.autosave"
        if not target.exists():
            return
        try:
            age = time.time() - target.stat().st_mtime
        except OSError:
            return
        if age > 60 * 60 * 24:
            target.unlink(missing_ok=True)
            return
        if QMessageBox.question(self, "恢复工程",
                                "发现上次异常退出时的自动保存，是否恢复？") != QMessageBox.Yes:
            target.unlink(missing_ok=True)   # 用户不要就别每次启动都问
            return
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            self.project = Project.from_dict(data)
            self.undo.clear()
            self.undo.dirty = True
            self._reload_project()
            self.message("已从自动保存恢复")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    @staticmethod
    def _clip_exportable(clip) -> bool:
        """画面与声音共用的**同一套**引用判定（B-11）。

        路径为空、文件不存在一律视为不可导出；禁用片段按"空隙"处理，
        由导出层补黑场，而不是被 concat 压缩掉。
        """
        try:
            return bool(clip.enabled and clip.path and Path(clip.path).exists())
        except OSError:
            return False

    def _clip_has_audio(self, clip) -> bool:
        asset = self.project.asset(clip.asset_id)
        if asset is None:
            # 素材记录缺失（悬空引用）：文件在就按声音轨意图保留，避免"画面在、声音丢"
            return True
        return bool(asset.has_audio)

    def _collect_export_data(self, include_audio: bool):
        # 合成顺序：第 0 条是基底（底层），其后按层级往上叠（B-06）
        layer_tracks = [t for t in self.project.layer_video_tracks() if not t.hidden]
        base_track = layer_tracks[0] if layer_tracks else None

        video_segments: list[tuple[str, int, int, int]] = []
        grades: list[dict] = []
        speeds: list[float] = []
        if base_track is not None:
            for clip in sorted(base_track.clips, key=lambda c: c.start):
                if self._clip_exportable(clip):
                    video_segments.append(
                        (clip.path, clip.source_in, clip.duration, clip.start))
                    grades.append(clip.grade() if clip.is_graded else {})
                    speeds.append(clip.speed)

        overlays: list[dict] = []
        for track in layer_tracks[1:]:
            for clip in sorted(track.clips, key=lambda c: c.start):
                if not self._clip_exportable(clip):
                    continue
                if clip.opacity <= 0.01:
                    continue
                overlays.append({
                    "path": clip.path,
                    "source_in": clip.source_in,
                    "duration": clip.duration,
                    "start": clip.start,
                    "speed": clip.speed,
                    "opacity": clip.opacity,
                    "grade": clip.grade() if clip.is_graded else {},
                })

        audio_segments: list[tuple[str, int, int, int]] = []
        volumes: list[float] = []
        if include_audio:
            # 视频片段自带的音轨也要混进来，否则导出的片子没有声音
            for track in layer_tracks:
                for clip in sorted(track.clips, key=lambda c: c.start):
                    if not self._clip_exportable(clip) or not self._clip_has_audio(clip):
                        continue
                    audio_segments.append(
                        (clip.path, clip.source_in, clip.duration, clip.start))
                    volumes.append(clip.volume)
            for track in self.project.audio_tracks():
                if track.muted:
                    continue
                for clip in sorted(track.clips, key=lambda c: c.start):
                    if self._clip_exportable(clip):
                        audio_segments.append(
                            (clip.path, clip.source_in, clip.duration, clip.start))
                        volumes.append(clip.volume)
        return video_segments, audio_segments, grades, speeds, volumes, overlays

    # ------------------------------------------------------------------
    def _ffmpeg_can_write(self, directory: Path) -> bool:
        """实测这台机器上这份静态 ffmpeg 能不能往 directory 里写文件。

        该 ffmpeg 在某些目录（桌面根、APPDATA、TEMP 等）会直接 Permission denied，
        只能写项目目录内。导出前先测一下，失败就自动改写到项目 exports/ 下。
        结论按目录缓存：同一目录只真写一次测试 MP4（B-46）。
        """
        from .media import find_tool, _CREATE_NO_WINDOW
        key = str(directory.resolve()) if directory.exists() else str(directory)
        cached = self._can_write_cache.get(key)
        if cached is not None:
            return cached
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            return False
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._can_write_cache[key] = False
            return False
        probe = directory / "_write_test.mp4"
        try:
            if probe.exists():
                probe.unlink()
        except OSError:
            pass
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.05",
                 "-frames:v", "1", str(probe)],
                timeout=15, creationflags=_CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            ok = result.returncode == 0 and probe.is_file() and probe.stat().st_size > 0
            if probe.exists():
                try:
                    probe.unlink()
                except OSError:
                    pass
            self._can_write_cache[key] = ok
            return ok
        except (OSError, subprocess.SubprocessError):
            self._can_write_cache[key] = False
            return False

    def _unique_output_path(self, path: Path) -> Path:
        """同名成片不静默覆盖：已存在就加 _1、_2…（B-35）"""
        if not path.exists():
            return path
        for index in range(1, 1000):
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists():
                return candidate
        return path.with_name(f"{path.stem}_{int(time.time())}{path.suffix}")

    def _resolve_export_path(self, output: str) -> tuple[str, str]:
        """返回 (最终路径, 提示)。若所选目录 ffmpeg 写不进去，改写到项目 exports/。"""
        out = Path(output)
        if not out.is_absolute():
            out = Path(self.cache_dir).parent / out
        if self._ffmpeg_can_write(out.parent):
            final = self._unique_output_path(out)
            note = "" if final == out else f"同名成片已存在，改存为：{final.name}"
            return str(final), note
        root = Path(self.cache_dir).parent
        exports = root / "exports"
        final = self._unique_output_path(exports / out.name)
        note = (f"原目录 ffmpeg 无写入权限，已改存到：{final}")
        return str(final), note

    def _export_with_options(self, options: dict) -> None:
        ok, message = tools_available()
        if not ok:
            QMessageBox.critical(self, "缺少 FFmpeg", message)
            return
        if self.project.duration() <= 0:
            QMessageBox.information(self, "导出", "时间线为空，先导入素材并拖到时间线。")
            return
        if self._export_state != "IDLE":
            # 单任务：正在导出/正在取消时拒绝新请求，不并发起第二个 ffmpeg（B-14）
            self.message("已有导出任务在进行，请等它结束或先取消。")
            return

        options["output"], note = self._resolve_export_path(options["output"])
        if note:
            self.message(note)

        video_segments, audio_segments, grades, speeds, volumes, overlays = \
            self._collect_export_data(bool(options.get("audio", True)))
        if not video_segments and not overlays and not audio_segments:
            QMessageBox.information(self, "导出", "没有可导出的片段（素材可能已丢失）。")
            return

        command = build_export_command_full(
            video_segments, audio_segments, options["output"],
            width=options["width"], height=options["height"],
            fps=self.project.fps_fraction, duration=self.project.duration(),
            crf=options["crf"], prores=options["prores"],
            include_audio=options.get("audio", True) and bool(audio_segments),
            grades=grades, speeds=speeds, volumes=volumes, overlays=overlays,
        )
        self._run_export(command, options["output"], self.project.duration())

    def _run_export(self, command: list[str], output: str, duration_ms: int = 0) -> None:
        if self._export_state != "IDLE":
            self.message("已有导出任务在进行，请等它结束或先取消。")
            return
        self.apply_workspace("导出")
        self.deliver_page.start()
        self.deliver_page.append_log(" ".join(command))
        worker = ExportWorker(command, output, duration_ms, self)
        self._worker = worker
        self._export_state = "RUNNING"
        self._export_output = output
        worker.progress.connect(self._on_export_progress)
        worker.finished_ok.connect(self._on_export_done)
        worker.failed.connect(self._on_export_failed)
        worker.cancelled.connect(self._on_export_cancelled)
        worker.start_export()

    def _finish_export_task(self) -> None:
        """统一收尾：回收进程对象、状态回 IDLE（B-47）"""
        worker = self._worker
        self._worker = None
        self._export_state = "IDLE"
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def _on_export_progress(self, percent: int, text: str) -> None:
        if percent >= 0:
            self.deliver_page.set_progress(percent, text)
        else:
            self.deliver_page.append_log(text)

    def _on_export_done(self, path: str) -> None:
        self.deliver_page.finish(path)
        self.message(f"导出完成：{path}")
        self._finish_export_task()

    def _on_export_failed(self, message: str) -> None:
        self.deliver_page.fail(message)
        self.message(f"导出失败：{message}")
        self._finish_export_task()

    def _on_export_cancelled(self) -> None:
        # 取消 ≠ 失败：清掉半成品，界面显示"已取消"（B-36）
        output = self._export_output
        if output:
            try:
                Path(output).unlink(missing_ok=True)
            except OSError:
                pass
        self.deliver_page.cancelled()
        self.message("导出已取消（未生成成片）")
        self._finish_export_task()

    def _cancel_export(self) -> None:
        if self._worker is None or self._export_state != "RUNNING":
            return
        self._export_state = "CANCELLING"
        self._worker.cancel()
        self.deliver_page.set_cancelling()
        self.message("正在取消导出…")

    def _reveal_path(self, path: str) -> None:
        target = Path(path)
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(target)],
                                 creationflags=_CREATE_NO_WINDOW)
            else:
                subprocess.Popen(["xdg-open", str(target.parent)])
        except OSError as exc:
            self.message(f"无法打开目录：{exc}")

    # ------------------------------------------------------------------
    def show_help(self) -> None:
        QMessageBox.information(
            self, "快捷键",
            "【播放与导航】\n"
            "  Space 播放/暂停 · Home 跳到开头 · End 跳到结尾\n"
            "  ←/→ 逐帧 · Shift+←/→ 十帧 · Shift+L 循环播放\n"
            "【时间线编辑】\n"
            "  Ctrl+K 在播放头切割 · Del 删除 · Shift+Del 波纹删除\n"
            "  M 加标记 · Ctrl+A 全选 · Ctrl+C/X/V 复制/剪切/粘贴\n"
            "  Ctrl+Z 撤销 · Ctrl+Shift+Z 重做\n"
            "【文件与页面】\n"
            "  Ctrl+I 导入素材 · Ctrl+S 保存 · Ctrl+Shift+E 导出\n"
            "  Ctrl+1..4 切换页面（媒体/剪辑/调色/导出）· F11 全屏")

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._confirm_discard():
            event.ignore()
            return
        if self._worker is not None:
            self._worker.cancel()
            # 取消后必须等进程真正退出，否则窗口析构时线程/管道被硬拆会崩（B-17）
            try:
                self._worker.waitForFinished(3000)
            except RuntimeError:
                pass
            self._finish_export_task()
        self._autosave_timer.stop()
        self.source.shutdown()
        self.media.shutdown()
        self.color_page.shutdown()
        self.playback.shutdown()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    def run(self) -> int:
        self.show()
        return int(QApplication.instance().exec())


def run(project_path: str | None = None) -> int:
    from .app import create_app

    app = create_app()
    window = MainWindow(project_path)
    window.show()
    return int(app.exec())
