"""项目管理器：新建 / 打开 / 最近项目 / 缩略图 / 备份 / 删除 / 项目设置。

最近项目表（recent.json）与项目文件本身分离：
删除列表记录不会删除磁盘上的工程文件，除非用户明确确认。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import Project, fps_from_str
from . import ui_theme as T

RECENT_FILENAME = "recent.json"
THUMB_DIRNAME = "project_thumbs"
BACKUP_DIRNAME = "backups"
THUMB_W, THUMB_H = 208, 117      # 16:9


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
def editor_home() -> Path:
    import os

    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home())
    path = Path(base) / "MixCutEditor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumb_dir() -> Path:
    path = editor_home() / THUMB_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_dir() -> Path:
    path = editor_home() / BACKUP_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 最近项目
# ---------------------------------------------------------------------------
class RecentProjects:
    """近期工程列表（JSON 持久化）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (editor_home() / RECENT_FILENAME)
        self._entries: list[dict] = []
        self.load()

    # -- 读写 ---------------------------------------------------------
    def load(self) -> None:
        self._entries = []
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, list):
            return
        for item in data:
            entry = self.normalize_entry(item)
            if entry is not None:
                self._entries.append(entry)

    @staticmethod
    def normalize_entry(item) -> dict | None:
        """把任意历史写法统一成 dict 条目（B-31 的一次性迁移）。

        旧版主窗口写过裸字符串列表，项目管理器写过 dict 列表；两种写法
        互相覆盖导致条目丢失。这里读取时兼容，写入时只写 dict。
        """
        if isinstance(item, str) and item.strip():
            path = str(Path(item).resolve())
            return {
                "path": path,
                "name": Path(path).stem,
                "width": 1920,
                "height": 1080,
                "fps": "30/1",
                "updated": "",
                "thumb": RecentProjects._thumb_path(path),
            }
        if isinstance(item, dict) and item.get("path"):
            entry = dict(item)
            entry["path"] = str(Path(entry["path"]).resolve())
            entry.setdefault("name", Path(entry["path"]).stem)
            return entry
        return None

    def save(self) -> None:
        try:
            from . import write_text_atomic
            write_text_atomic(
                self.path, json.dumps(self._entries, ensure_ascii=False, indent=2)
            )
        except OSError:
            pass

    # -- 操作 ---------------------------------------------------------
    def entries(self) -> list[dict]:
        return list(self._entries)

    def add(self, project_path: str, project: Project | None = None) -> None:
        path = str(Path(project_path).resolve())
        entry = {
            "path": path,
            "name": project.name if project else Path(path).stem,
            "width": project.width if project else 1920,
            "height": project.height if project else 1080,
            "fps": project.fps if project else "30/1",
            "updated": datetime.now().isoformat(timespec="seconds"),
            "thumb": self._thumb_path(path),
        }
        # 去重后置顶
        self._entries = [e for e in self._entries if e.get("path") != path]
        self._entries.insert(0, entry)
        self._entries = self._entries[:40]
        self.save()

    def touch(self, project_path: str) -> None:
        """已在列表里则刷新时间并置顶。"""
        path = str(Path(project_path).resolve())
        found = next((e for e in self._entries if e.get("path") == path), None)
        if found is None:
            return
        found["updated"] = datetime.now().isoformat(timespec="seconds")
        self._entries = [found] + [e for e in self._entries if e.get("path") != path]
        self.save()

    def remove(self, project_path: str) -> None:
        path = str(Path(project_path).resolve())
        self._entries = [e for e in self._entries if e.get("path") != path]
        self.save()

    def get(self, project_path: str) -> dict | None:
        path = str(Path(project_path).resolve())
        return next((e for e in self._entries if e.get("path") == path), None)

    # -- 缩略图 -------------------------------------------------------
    @staticmethod
    def _thumb_path(project_path: str) -> str:
        digest = hashlib.md5(project_path.encode("utf-8")).hexdigest()[:16]
        return str(thumb_dir() / f"{digest}.png")

    def ensure_thumbnail(self, entry: dict) -> str:
        """返回缩略图路径；不存在则生成占位缩略图。"""
        target = Path(entry.get("thumb") or self._thumb_path(entry["path"]))
        if target.exists():
            return str(target)
        pixmap = render_project_thumbnail(
            entry.get("name", "未命名"),
            entry.get("width", 1920),
            entry.get("height", 1080),
            entry.get("fps", "30/1"),
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            pixmap.save(str(target), "PNG")
        except OSError:
            return ""
        return str(target)


def render_project_thumbnail(name: str, width: int, height: int, fps: str) -> QPixmap:
    """生成工程占位缩略图：深底 + 规格文字 + 边框（无渐变）。"""
    pixmap = QPixmap(THUMB_W, THUMB_H)
    pixmap.fill(QColor(T.BG_VIEWER))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # 内嵌 16:9 取景框
    painter.setPen(QColor(T.BORDER))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(0, 0, THUMB_W - 1, THUMB_H - 1)

    # 中央 Logo 水印
    logo_side = 44
    T.paint_logo(
        painter,
        QRectF((THUMB_W - logo_side) / 2, (THUMB_H - logo_side) / 2 - 6, logo_side, logo_side),
        T.BORDER_STRONG,
        T.BORDER,
    )

    painter.setPen(QColor(T.TEXT_DIM))
    painter.setFont(T.ui_font(7))
    painter.drawText(
        pixmap.rect().adjusted(8, 0, -8, -8),
        Qt.AlignLeft | Qt.AlignBottom,
        f"{width}×{height}",
    )
    painter.setPen(QColor(T.TEXT_MUTED))
    painter.drawText(
        pixmap.rect().adjusted(8, 0, -8, -8),
        Qt.AlignRight | Qt.AlignBottom,
        frame_rate_text(fps),
    )
    painter.end()
    return pixmap


def frame_rate_text(fps: str) -> str:
    value = float(fps_from_str(fps))
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return f"{text} fps"


# ---------------------------------------------------------------------------
# 项目设置对话框（新建 / 修改共用）
# ---------------------------------------------------------------------------
RESOLUTION_PRESETS = [
    ("1920 × 1080  横屏 Full HD", 1920, 1080),
    ("1080 × 1920  竖屏", 1080, 1920),
    ("3840 × 2160  横屏 UHD", 3840, 2160),
    ("2160 × 3840  竖屏 4K", 2160, 3840),
    ("1440 × 1080  横屏", 1440, 1080),
    ("1080 × 1080  方形", 1080, 1080),
    ("1280 × 720   横屏 HD", 1280, 720),
    ("自定义", 0, 0),
]

FPS_CHOICES = ["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"]


class ProjectSettingsDialog(QDialog):
    """工程设置：名称、分辨率、帧率。"""

    def __init__(self, project: Project | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("工程设置")
        self.setMinimumWidth(460)
        self._project = project

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("工程设置" if project else "新建工程")
        title.setFont(T.ui_font(T.FONT_SIZE_HEADLINE, bold=True))
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(project.name if project else "未命名工程")
        form.addRow("工程名称", self.name_edit)

        self.resolution_combo = QComboBox()
        for label, _w, _h in RESOLUTION_PRESETS:
            self.resolution_combo.addItem(label)
        form.addRow("分辨率", self.resolution_combo)

        size_row = QWidget()
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(6)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(64, 16384)
        self.width_spin.setSingleStep(2)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(64, 16384)
        self.height_spin.setSingleStep(2)
        size_layout.addWidget(self.width_spin)
        size_layout.addWidget(QLabel("×"))
        size_layout.addWidget(self.height_spin)
        size_layout.addStretch(1)
        form.addRow("宽 × 高", size_row)

        self.fps_combo = QComboBox()
        self.fps_combo.addItems(FPS_CHOICES)
        form.addRow("帧率", self.fps_combo)

        layout.addLayout(form)

        hint = QLabel("分辨率与帧率决定导出规格；导入素材不会改变此设置。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 初值
        if project:
            self._select_resolution(project.width, project.height)
        else:
            self.resolution_combo.setCurrentIndex(0)
        self.resolution_combo.currentIndexChanged.connect(self._on_preset_changed)
        self._on_preset_changed()

    # ------------------------------------------------------------------
    def _on_preset_changed(self) -> None:
        index = self.resolution_combo.currentIndex()
        _label, width, height = RESOLUTION_PRESETS[index]
        if width and height:
            self.width_spin.setValue(width)
            self.height_spin.setValue(height)

    def _select_resolution(self, width: int, height: int) -> None:
        for index, (_label, w, h) in enumerate(RESOLUTION_PRESETS):
            if (w, h) == (width, height):
                self.resolution_combo.setCurrentIndex(index)
                self.width_spin.setValue(width)
                self.height_spin.setValue(height)
                break
        else:
            self.resolution_combo.setCurrentIndex(len(RESOLUTION_PRESETS) - 1)
            self.width_spin.setValue(width)
            self.height_spin.setValue(height)

    # ------------------------------------------------------------------
    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip() or "未命名工程",
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "fps": self.fps_combo.currentText(),
        }

    def apply_to(self, project: Project) -> None:
        values = self.values()
        project.name = values["name"]
        project.width = values["width"]
        project.height = values["height"]
        project.fps = fps_to_fraction_text(values["fps"])


def fps_to_fraction_text(text: str) -> str:
    from . import FPS_PRESETS

    for label, fraction in FPS_PRESETS:
        if label == text:
            return f"{fraction.numerator}/{fraction.denominator}"
    value = fps_from_str(text)
    return f"{value.numerator}/{value.denominator}"


# ---------------------------------------------------------------------------
# 项目管理器
# ---------------------------------------------------------------------------
class ProjectManagerDialog(QDialog):
    """启动后进入的项目管理器。

    通过 `selected_action` / `selected_path` 告知调用方结果：
        action = "new" | "open" | "recent" | "quit"
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{T.APP_NAME} — 项目管理器")
        self.resize(980, 620)
        self.recent = RecentProjects()

        self.selected_action = "quit"
        self.selected_path = ""

        self._build_ui()
        self._reload_recent()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_content(), 1)

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(250)
        side.setObjectName("PanelRoot")
        side.setStyleSheet(f"#PanelRoot {{ background:{T.BG_PANEL};"
                           f" border-right:1px solid {T.BORDER}; }}")

        layout = QVBoxLayout(side)
        layout.setContentsMargins(20, 24, 20, 20)
        layout.setSpacing(10)

        logo = QLabel()
        logo.setFixedSize(52, 52)
        logo.setPixmap(self._render_logo_pixmap(52))
        layout.addWidget(logo)

        name = QLabel(T.APP_NAME)
        name.setFont(T.ui_font(15, bold=True))
        layout.addWidget(name)

        sub = QLabel(f"V{T.APP_VERSION}  ·  项目管理器")
        sub.setObjectName("Muted")
        layout.addWidget(sub)

        layout.addSpacing(18)

        self.btn_new = QPushButton("新建项目")
        self.btn_new.setObjectName("Primary")
        self.btn_new.setMinimumHeight(34)
        self.btn_new.clicked.connect(self._on_new)
        layout.addWidget(self.btn_new)

        self.btn_open = QPushButton("打开项目…")
        self.btn_open.setMinimumHeight(30)
        self.btn_open.clicked.connect(self._on_open)
        layout.addWidget(self.btn_open)

        layout.addSpacing(6)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{T.BORDER};")
        layout.addWidget(line)
        layout.addSpacing(6)

        self.btn_backup = QPushButton("备份选中项目")
        self.btn_backup.setObjectName("Flat")
        self.btn_backup.clicked.connect(self._on_backup)
        layout.addWidget(self.btn_backup)

        self.btn_settings = QPushButton("项目设置…")
        self.btn_settings.setObjectName("Flat")
        self.btn_settings.clicked.connect(self._on_settings)
        layout.addWidget(self.btn_settings)

        self.btn_delete = QPushButton("从列表移除")
        self.btn_delete.setObjectName("Flat")
        self.btn_delete.clicked.connect(self._on_delete)
        layout.addWidget(self.btn_delete)

        layout.addStretch(1)

        self.btn_quit = QPushButton("退出")
        self.btn_quit.setObjectName("Flat")
        self.btn_quit.clicked.connect(self.reject)
        layout.addWidget(self.btn_quit)

        return side

    def _build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("最近项目")
        title.setFont(T.ui_font(T.FONT_SIZE_TITLE, bold=True))
        header.addWidget(title)
        header.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(THUMB_W, THUMB_H))
        self.list.setGridSize(QSize(THUMB_W + 24, THUMB_H + 54))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSpacing(6)
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.itemDoubleClicked.connect(self._on_item_activated)
        self.list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.list, 1)

        self.empty_hint = QLabel(
            "还没有最近项目。\n点击左侧「新建项目」开始，或用「打开项目…」载入已有工程。"
        )
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setObjectName("Muted")
        layout.addWidget(self.empty_hint, 1)

        footer = QLabel(
            "提示：双击项目缩略图即可打开。列表仅记录工程路径，不会移动或删除你的文件。"
        )
        footer.setObjectName("Muted")
        layout.addWidget(footer)

        return content

    @staticmethod
    def _render_logo_pixmap(size: int) -> QPixmap:
        from PySide6.QtCore import QRectF

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        T.paint_logo(painter, QRectF(2, 2, size - 4, size - 4), T.ACCENT, T.TEXT_MUTED)
        painter.end()
        return pixmap

    # ------------------------------------------------------------------
    # 列表
    # ------------------------------------------------------------------
    def _reload_recent(self) -> None:
        self.list.clear()
        entries = [e for e in self.recent.entries() if Path(e["path"]).exists()]
        # 文件已丢失的记录不展示，但保留在列表里以便用户知道
        missing = [e for e in self.recent.entries() if not Path(e["path"]).exists()]

        for entry in entries + missing:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, entry["path"])
            exists = Path(entry["path"]).exists()
            text = entry.get("name", Path(entry["path"]).stem)
            updated = entry.get("updated", "")[:16].replace("T", " ")
            if exists:
                item.setText(f"{text}\n{updated}")
                thumb = self.recent.ensure_thumbnail(entry)
                if thumb:
                    item.setIcon(QIcon(thumb))
                item.setToolTip(f"{entry['path']}\n{entry.get('width')}×{entry.get('height')}"
                                f" · {frame_rate_text(entry.get('fps', '30/1'))}")
            else:
                item.setText(f"{text}\n（文件缺失）")
                item.setForeground(QColor(T.ERROR))
                item.setToolTip(f"找不到文件：{entry['path']}\n可从列表移除，不会删除任何文件")
            self.list.addItem(item)

        has_entries = self.list.count() > 0
        self.list.setVisible(has_entries)
        self.empty_hint.setVisible(not has_entries)
        self.count_label.setText(f"{len(entries)} 个可用 · {len(missing)} 个缺失"
                                 if missing else f"{len(entries)} 个项目")
        self._update_buttons()

    def _current_entry(self) -> dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return self.recent.get(item.data(Qt.UserRole))

    def _current_path(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else ""

    def _update_buttons(self) -> None:
        has_selection = self.list.currentItem() is not None
        for button in (self.btn_backup, self.btn_settings, self.btn_delete):
            button.setEnabled(has_selection)

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def _on_new(self) -> None:
        dialog = ProjectSettingsDialog(None, self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        default_name = values["name"].replace("/", "_").replace("\\", "_")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存新工程", str(Path.home() / f"{default_name}.mixcut"),
            "MixCut 工程 (*.mixcut)")
        if not path:
            return
        if not path.lower().endswith(".mixcut"):
            path += ".mixcut"

        project = Project()
        project.name = values["name"]
        project.width = values["width"]
        project.height = values["height"]
        project.fps = fps_to_fraction_text(values["fps"])
        try:
            project.save(path)
        except OSError as exc:
            QMessageBox.critical(self, "新建失败", f"无法写入工程文件：\n{exc}")
            return

        self.recent.add(path, project)
        self.recent.ensure_thumbnail(self.recent.get(path) or {})
        self.selected_action = "new"
        self.selected_path = path
        self.accept()

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", str(Path.home()), "MixCut 工程 (*.mixcut *.json)")
        if not path:
            return
        self._open_path(path)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        self._open_path(item.data(Qt.UserRole))

    def _open_path(self, path: str) -> None:
        if not Path(path).exists():
            QMessageBox.warning(self, "文件缺失",
                                f"找不到工程文件：\n{path}\n\n"
                                "它可能被移动或删除了。可从列表移除该记录。")
            return
        try:
            project = Project.load(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "打开失败", f"{path}\n\n{exc}")
            return

        self.recent.add(path, project)
        self.recent.ensure_thumbnail(self.recent.get(path) or {})
        self.selected_action = "recent"
        self.selected_path = path
        self.accept()

    def _on_backup(self) -> None:
        path = self._current_path()
        if not path or not Path(path).exists():
            QMessageBox.information(self, "备份", "请先选择一个可用的项目。")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = backup_dir() / f"{Path(path).stem}_{stamp}.mixcut"
        try:
            shutil.copy2(path, target)
        except OSError as exc:
            QMessageBox.critical(self, "备份失败", str(exc))
            return
        QMessageBox.information(self, "备份完成",
                                f"已备份到：\n{target}")

    def _on_settings(self) -> None:
        path = self._current_path()
        if not path or not Path(path).exists():
            QMessageBox.information(self, "项目设置", "请先选择一个可用的项目。")
            return
        try:
            project = Project.load(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "读取失败", str(exc))
            return

        dialog = ProjectSettingsDialog(project, self)
        if dialog.exec() != QDialog.Accepted:
            return
        dialog.apply_to(project)
        try:
            project.save(path)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.recent.add(path, project)
        self.recent.ensure_thumbnail(self.recent.get(path) or {})
        self._reload_recent()
        QMessageBox.information(self, "已保存", "项目设置已更新。")

    def _on_delete(self) -> None:
        path = self._current_path()
        if not path:
            return
        exists = Path(path).exists()
        if exists:
            answer = QMessageBox.question(
                self, "移除项目",
                f"要如何处理这个项目？\n\n{path}\n\n"
                "「是」：仅从最近列表移除（磁盘文件保留）\n"
                "「否」：同时删除磁盘上的工程文件（不可恢复）\n"
                "「取消」：什么都不做",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes)
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.No:
                confirm = QMessageBox.warning(
                    self, "确认删除文件",
                    f"即将永久删除工程文件：\n{path}\n\n确定吗？",
                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
                if confirm != QMessageBox.Yes:
                    return
                try:
                    Path(path).unlink()
                except OSError as exc:
                    QMessageBox.critical(self, "删除失败", str(exc))
                    return
        self.recent.remove(path)
        self._reload_recent()

    def reject(self) -> None:  # noqa: D401
        self.selected_action = "quit"
        super().reject()
