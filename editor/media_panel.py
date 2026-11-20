"""媒体库面板：导入、浏览、筛选、重链、拖拽到时间线。

相对第一阶段新增（本阶段补全的功能）：
  - 缩略图 / 列表两种视图切换
  - 类型筛选（视频 / 音频 / 图片）与三种排序
  - 真正可用的拖拽（自定义 MIME，供时间线接收）
  - 右键菜单：源监视器、插入、资源管理器定位、重新链接、重新扫描、移除、属性
  - 离线（文件缺失）检测与重链后回填
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import MediaAsset, is_media_file, normalize_path
from . import ui_theme as T
from .media import extract_thumbnail, extract_waveform, probe

THUMB_SIZE = 96
LIST_ICON = 28
ASSET_MIME = "application/x-mixcut-assets"
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class ProbeWorker(QThread):
    """后台探测 + 缩略图/波形提取，避免界面卡顿。

    注意：QPixmap 只能在 GUI 线程创建，因此这里跨线程传的是 QImage，
    由 _on_thumbnail() 在主线程转成 QPixmap。
    """

    asset_ready = Signal(object)            # MediaAsset
    thumbnail_ready = Signal(str, object)   # asset_id, QImage
    waveform_ready = Signal(str, object)    # asset_id, list[float]

    def __init__(self, paths: list[str], cache_dir: Path, replace: bool = False) -> None:
        super().__init__()
        self.paths = paths
        self.cache_dir = cache_dir
        self.replace = replace

    def run(self) -> None:  # noqa: D401
        for path in self.paths:
            asset = probe(path)
            if asset is None:
                asset = MediaAsset(path=path)
            asset.replace = self.replace       # type: ignore[attr-defined]
            self.asset_ready.emit(asset)

            stem = Path(path).stem[:40].replace(" ", "_")
            # hash() 每次进程启动都会随机化，必须用稳定摘要做缓存键
            digest = hashlib.md5(str(path).encode("utf-8")).hexdigest()[:10]
            thumb_path = self.cache_dir / f"{stem}_{digest}.jpg"
            if not thumb_path.exists():
                extract_thumbnail(path, str(thumb_path))
            if thumb_path.exists():
                image = QImage(str(thumb_path))
                if not image.isNull():
                    self.thumbnail_ready.emit(asset.id, image)
            if asset.has_audio:
                peaks = extract_waveform(path)
                if peaks:
                    self.waveform_ready.emit(asset.id, peaks)


class MediaPanel(QWidget):
    status = Signal(str)
    insert_requested = Signal(str)      # asset_id
    asset_imported = Signal(object)     # MediaAsset（新增素材时发出）
    asset_changed = Signal(object, str) # MediaAsset, 旧路径（重链/重扫描后）
    open_source_requested = Signal(str) # asset_id
    remove_requested = Signal(str)      # asset_id
    selection_changed = Signal(list)    # asset_id 列表
    thumbnail_updated = Signal(str, object)   # asset_id, QPixmap
    waveform_updated = Signal(str, object)    # asset_id, peaks

    def __init__(self, cache_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.worker: ProbeWorker | None = None
        self._import_queue: list[tuple[list[str], bool]] = []
        self.thumbnails: dict[str, QPixmap] = {}
        self.waveforms: dict[str, list[float]] = {}
        self.assets: list[MediaAsset] = []

        self._view_mode = "grid"
        self._type_filter = "全部"
        self._sort_mode = "导入顺序"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.btn_files = QPushButton("导入素材")
        self.btn_folder = QPushButton("导入文件夹")
        row.addWidget(self.btn_files)
        row.addWidget(self.btn_folder)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self.type_combo = QComboBox()
        self.type_combo.addItems(["全部", "视频", "音频", "图片"])
        self.type_combo.setToolTip("按素材类型筛选")
        row2.addWidget(self.type_combo, 1)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["导入顺序", "名称", "时长"])
        self.sort_combo.setToolTip("素材排序方式")
        row2.addWidget(self.sort_combo, 1)

        self.btn_view = QPushButton("列表")
        self.btn_view.setObjectName("Flat")
        self.btn_view.setToolTip("在缩略图与列表视图之间切换")
        self.btn_view.setFixedWidth(44)
        row2.addWidget(self.btn_view)
        layout.addLayout(row2)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索素材…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setDragEnabled(True)
        self.list.setDragDropMode(QAbstractItemView.DragOnly)
        self.list.setWordWrap(True)
        layout.addWidget(self.list, 1)

        self.count_label = QLabel("0 个素材")
        self.count_label.setObjectName("Muted")
        layout.addWidget(self.count_label)

        self.btn_files.clicked.connect(self.choose_files)
        self.btn_folder.clicked.connect(self.choose_folder)
        self.btn_view.clicked.connect(self.toggle_view)
        self.type_combo.currentTextChanged.connect(self._on_type_filter)
        self.sort_combo.currentTextChanged.connect(self._on_sort)
        self.list.itemDoubleClicked.connect(self._on_double_clicked)
        self.list.itemSelectionChanged.connect(self._on_selection)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        self.search.textChanged.connect(self._apply_filter)

        self._apply_view_mode()

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------
    def choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "导入素材", "",
            "媒体文件 (*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.mxf *.wmv *.flv "
            "*.wav *.mp3 *.flac *.m4a *.aac *.ogg "
            "*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*.*)")
        if files:
            self.import_paths(files)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择素材文件夹")
        if not folder:
            return
        paths = [str(p) for p in Path(folder).rglob("*") if p.is_file() and is_media_file(str(p))]
        if not paths:
            self.status.emit("该文件夹没有可导入的素材")
            return
        self.import_paths(paths)

    def import_paths(self, paths: list[str], replace: bool = False) -> None:
        paths = [p for p in paths if is_media_file(p) and Path(p).is_file()]
        if not paths:
            return
        if self.worker is not None and self.worker.isRunning():
            # 绝不在 GUI 线程 wait()：旧任务还在跑就排队，等它 finished 再续跑（B-12）
            self._import_queue.append((paths, replace))
            self.status.emit(
                f"已有探测任务在进行，已排队 {len(paths)} 个素材（队列 {len(self._import_queue)} 批）…")
            return
        self._start_import(paths, replace)

    def _start_import(self, paths: list[str], replace: bool = False) -> None:
        worker = ProbeWorker(paths, self.cache_dir, replace=replace)
        self.worker = worker
        worker.asset_ready.connect(self._on_asset)
        worker.thumbnail_ready.connect(self._on_thumbnail)
        worker.waveform_ready.connect(self._on_waveform)
        finished = getattr(worker, "finished", None)
        if finished is not None:
            finished.connect(lambda w=worker: self._on_import_finished(w))
        worker.start()
        self.status.emit(f"正在{'重新扫描' if replace else '导入'} {len(paths)} 个素材…")

    def _on_import_finished(self, worker) -> None:
        if worker is not self.worker:
            return                      # 已被更新的任务取代，交给新任务自己收尾
        worker.deleteLater()
        self.worker = None
        if self._import_queue:
            paths, replace = self._import_queue.pop(0)
            self._start_import(paths, replace)

    def shutdown(self) -> None:
        """关闭窗口前等待探测线程结束，避免线程被析构导致崩溃。"""
        self._import_queue.clear()
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(3000)

    def set_assets(self, assets: list[MediaAsset]) -> None:
        self.assets = list(assets)
        self._rebuild()

    # ------------------------------------------------------------------
    # 视图 / 筛选 / 排序
    # ------------------------------------------------------------------
    def toggle_view(self) -> None:
        self._view_mode = "list" if self._view_mode == "grid" else "grid"
        self._apply_view_mode()

    def _apply_view_mode(self) -> None:
        if self._view_mode == "grid":
            self.list.setViewMode(QListWidget.IconMode)
            self.list.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
            self.list.setGridSize(QSize(THUMB_SIZE + 26, THUMB_SIZE + 42))
            self.list.setResizeMode(QListWidget.Adjust)
            self.btn_view.setText("列表")
        else:
            self.list.setViewMode(QListWidget.ListMode)
            self.list.setIconSize(QSize(LIST_ICON, LIST_ICON))
            self.list.setGridSize(QSize())
            self.list.setResizeMode(QListWidget.Adjust)
            self.btn_view.setText("缩略图")
        self._rebuild()

    def _on_type_filter(self, text: str) -> None:
        self._type_filter = text
        self._apply_filter(self.search.text())

    def _on_sort(self, text: str) -> None:
        self._sort_mode = text
        self._rebuild()

    def _type_matches(self, asset: MediaAsset) -> bool:
        mapping = {"视频": "video", "音频": "audio", "图片": "image"}
        wanted = mapping.get(self._type_filter)
        return wanted is None or asset.kind == wanted

    def _ordered_assets(self) -> list[MediaAsset]:
        if self._sort_mode == "名称":
            return sorted(self.assets, key=lambda a: a.name.lower())
        if self._sort_mode == "时长":
            return sorted(self.assets, key=lambda a: a.duration, reverse=True)
        return list(self.assets)

    def _rebuild(self) -> None:
        """按当前筛选/排序重建列表项（保留缩略图缓存）。"""
        self.list.blockSignals(True)
        self.list.clear()
        for asset in self._ordered_assets():
            # 注意：这里不能走 _add_item 的"已在 self.assets 中则跳过"检查，
            # 否则重建时会因为资产本来就在列表里而一条都加不上。
            self._create_item(asset)
        self.list.blockSignals(False)
        self._apply_filter(self.search.text())
        self._update_count()

    # ------------------------------------------------------------------
    # 选中
    # ------------------------------------------------------------------
    def selected_assets(self) -> list[MediaAsset]:
        ids = [item.data(Qt.UserRole) for item in self.list.selectedItems()]
        return [a for a in self.assets if a.id in ids]

    def selected_asset(self) -> MediaAsset | None:
        items = self.list.selectedItems()
        if not items:
            return None
        asset_id = items[0].data(Qt.UserRole)
        return next((a for a in self.assets if a.id == asset_id), None)

    def _on_selection(self) -> None:
        self.selection_changed.emit([a.id for a in self.selected_assets()])

    # ------------------------------------------------------------------
    # 后台结果
    # ------------------------------------------------------------------
    def _on_asset(self, asset: MediaAsset) -> None:
        replace = bool(getattr(asset, "replace", False))
        target = normalize_path(asset.path)
        existing = next((a for a in self.assets
                         if normalize_path(a.path) == target or a.id == asset.id), None)
        if existing is not None:
            if not replace:
                return
            old_path = existing.path
            existing.duration = asset.duration
            existing.width = asset.width
            existing.height = asset.height
            existing.fps = asset.fps
            existing.kind = asset.kind
            existing.has_video = asset.has_video
            existing.has_audio = asset.has_audio
            existing.codec = asset.codec
            self._refresh_item(existing)
            self.asset_changed.emit(existing, old_path)
            self.status.emit(f"已更新：{existing.name}")
            return
        self._add_item(asset, rebuild=True)
        self.asset_imported.emit(asset)
        self.status.emit(f"已导入：{asset.name}")

    def _add_item(self, asset: MediaAsset, rebuild: bool = False) -> None:
        if any(a.id == asset.id for a in self.assets):
            return
        self.assets.append(asset)
        self._create_item(asset)
        if rebuild:
            self._rebuild()

    def _create_item(self, asset: MediaAsset) -> None:
        item = QListWidgetItem()
        item.setData(Qt.UserRole, asset.id)
        self._fill_item(item, asset)
        self.list.addItem(item)

    def _fill_item(self, item: QListWidgetItem, asset: MediaAsset) -> None:
        exists = asset.exists
        label = self._label_text(asset)
        item.setText(label)
        item.setToolTip(f"{asset.path}\n{asset.width}×{asset.height} · {asset.codec or '未知编码'}")
        item.setIcon(self._icon_for(asset))
        if not exists:
            item.setForeground(QColor(T.ERROR))
        else:
            item.setForeground(QColor(T.TEXT))
        item.setSizeHint(QSize(0, LIST_ICON + 12) if self._view_mode == "list"
                         else QSize(THUMB_SIZE + 26, THUMB_SIZE + 42))

    def _refresh_item(self, asset: MediaAsset) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) == asset.id:
                self._fill_item(item, asset)
                break
        self._update_count()

    def _label_text(self, asset: MediaAsset) -> str:
        if self._view_mode == "list":
            kind = {"video": "视频", "audio": "音频", "image": "图片"}.get(asset.kind, "未知")
            size = ""
            if asset.width and asset.height:
                size = f"{asset.width}×{asset.height}"
            text = f"{asset.name}\n{kind} · {self._duration_text(asset)}"
            if size:
                text += f" · {size}"
            if not asset.exists:
                text += "  （文件缺失）"
            return text
        if not asset.exists:
            return f"{asset.name}\n（文件缺失）"
        return f"{asset.name}\n{self._duration_text(asset)}"

    def _icon_for(self, asset: MediaAsset) -> QIcon:
        pixmap = self.thumbnails.get(asset.id)
        if pixmap is not None and not pixmap.isNull():
            return QIcon(pixmap)
        placeholder = QPixmap(THUMB_SIZE, THUMB_SIZE)
        placeholder.fill(QColor(T.BG_PANEL_ALT))
        return QIcon(placeholder)

    @staticmethod
    def _duration_text(asset: MediaAsset) -> str:
        seconds = max(0, asset.duration) // 1000
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _on_thumbnail(self, asset_id: str, image: QImage) -> None:
        # 主线程里才能安全创建 QPixmap
        pixmap = QPixmap.fromImage(image)
        self.thumbnails[asset_id] = pixmap
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) == asset_id:
                item.setIcon(QIcon(pixmap))
                break
        self.thumbnail_updated.emit(asset_id, pixmap)

    def _on_waveform(self, asset_id: str, peaks: list[float]) -> None:
        self.waveforms[asset_id] = peaks
        self.waveform_updated.emit(asset_id, peaks)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        self.insert_requested.emit(item.data(Qt.UserRole))

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            asset = next((a for a in self.assets if a.id == item.data(Qt.UserRole)), None)
            hidden = bool(needle) and needle not in item.text().lower()
            if asset is not None and not self._type_matches(asset):
                hidden = True
            item.setHidden(hidden)

    def _update_count(self) -> None:
        missing = sum(1 for a in self.assets if not a.exists)
        text = f"{len(self.assets)} 个素材"
        if missing:
            text += f" · {missing} 个文件缺失"
        self.count_label.setText(text)

    # -- 拖拽 ----------------------------------------------------------
    def startDrag(self, supported_actions) -> None:  # noqa: N802
        """把选中的素材 id 写进自定义 MIME，时间线据此接收。"""
        assets = self.selected_assets()
        if not assets:
            return
        mime = QMimeData()
        mime.setData(ASSET_MIME, ",".join(a.id for a in assets).encode("utf-8"))
        mime.setText("\n".join(a.path for a in assets))
        drag = QDrag(self.list)
        drag.setMimeData(mime)
        icon = self.thumbnails.get(assets[0].id)
        if icon is not None and not icon.isNull():
            drag.setPixmap(icon.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.exec(Qt.CopyAction)

    # -- 右键菜单 ------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is not None and not item.isSelected():
            self.list.setCurrentItem(item)
        asset = self.selected_asset()
        menu = QMenu(self)
        if asset is not None:
            menu.addAction("插入到播放头", lambda: self.insert_requested.emit(asset.id))
            menu.addAction("在源监视器中打开", lambda: self.open_source_requested.emit(asset.id))
            menu.addSeparator()
            reveal = menu.addAction("在资源管理器中显示", lambda: self._reveal(asset))
            reveal.setEnabled(asset.exists)
            menu.addAction("重新链接…", lambda: self._relink(asset))
            menu.addAction("重新扫描素材", self.rescan)
            menu.addAction("素材属性…", lambda: self._show_properties(asset))
            menu.addSeparator()
            menu.addAction("从媒体库移除", lambda: self.remove_requested.emit(asset.id))
        else:
            menu.addAction("导入素材…", self.choose_files)
            menu.addAction("导入文件夹…", self.choose_folder)
            menu.addAction("重新扫描素材", self.rescan)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _reveal(self, asset: MediaAsset) -> None:
        path = Path(asset.path)
        if not path.exists():
            self.status.emit("文件不存在，无法定位")
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)],
                                 creationflags=_CREATE_NO_WINDOW)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except OSError as exc:
            self.status.emit(f"无法打开资源管理器：{exc}")

    def _relink(self, asset: MediaAsset) -> None:
        suffix = Path(asset.path).suffix
        path, _ = QFileDialog.getOpenFileName(
            self, f"重新链接：{asset.name}", str(Path(asset.path).parent),
            f"同类型素材 (*{suffix});;所有文件 (*.*)")
        if not path:
            return
        old_path = asset.path
        asset.path = str(Path(path))
        asset.name = Path(path).name
        self._refresh_item(asset)
        self.asset_changed.emit(asset, old_path)
        self.import_paths([str(path)], replace=True)
        self.status.emit(f"已重新链接：{asset.name}")

    def rescan(self) -> None:
        """重新探测全部素材：刷新时长/规格，并重新标记缺失文件。"""
        paths = [a.path for a in self.assets if a.path]
        if not paths:
            self.status.emit("媒体库为空")
            return
        for asset in self.assets:
            self._refresh_item(asset)
        self.import_paths(paths, replace=True)

    def _show_properties(self, asset: MediaAsset) -> None:
        exists = "存在" if asset.exists else "缺失"
        QMessageBox.information(
            self, "素材属性",
            f"<b>{asset.name}</b><br><br>"
            f"路径：{asset.path}<br>"
            f"状态：{exists}<br>"
            f"类型：{asset.kind}<br>"
            f"时长：{self._duration_text(asset)}（{asset.duration} ms）<br>"
            f"分辨率：{asset.width}×{asset.height}<br>"
            f"帧率：{asset.fps}<br>"
            f"编码：{asset.codec or '未知'}<br>"
            f"音频流：{'有' if asset.has_audio else '无'}")

    # ------------------------------------------------------------------
    def assets_for_mix(self, selected_only: bool = False) -> list[MediaAsset]:
        if selected_only:
            return self.selected_assets()
        return list(self.assets)
