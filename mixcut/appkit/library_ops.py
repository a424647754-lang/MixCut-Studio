# -*- coding: utf-8 -*-
"""素材库操作相关的方法（从 main.py 原样搬出）。

包含素材树的增删改、素材确认、素材选择器刷新等 14 个方法。

拆分方式说明
------------
采用 Mixin：方法仍然挂在 MainWindow 上，所有 ``self.xxx()`` 调用点零改动，
初始化顺序、信号槽连接、撤销历史行为完全不变。

宿主契约
--------
这批方法高度依赖宿主（MainWindow）已有的状态与方法，必须由宿主提供：

**控件属性**（由 ``_build_ui`` 创建）
    ``self.tree``           —— UndoableTreeWidget 素材树（数据承载者）
    ``self.count_label``    —— 素材计数标签
    ``self.detail_box``     —— 详情面板
    ``self.confirm_btn``    —— 确定使用按钮
    ``self.mix_selector``   —— 混剪素材选择器
    ``self.mix_start_btn``  —— 开始混剪按钮
    ``self.player``         —— 预览播放器

**状态属性**
    ``self.material_confirmed`` —— 素材是否已确认
    ``self._confirmed_paths``   —— 本轮已确认的素材路径

**宿主方法**（仍定义在 MainWindow 或其它 Mixin 上）
    ``self.log`` / ``self._navigate`` / ``self._refresh_topbar`` /
    ``self._refresh_library_cards``

依赖方向：本模块只导入 PySide6、标准库与 ffmpeg_tools，
不导入 main.py，也不导入其它业务模块，不存在循环导入。
"""
import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QInputDialog, QFileDialog, QMessageBox, QTreeWidgetItem,
    QTreeWidgetItemIterator,
)

from mixcut import ffmpeg_tools as ft

from .constants import VIDEO_EXTS, _human_size


class LibraryOpsMixin:
    """素材管理与确认。"""

    # =================================================
    # 素材管理
    # =================================================

    def invalidate_materials(self):

        self.material_confirmed = False
        self._confirmed_paths = []

        self.confirm_btn.setText(
            "🔒 确定使用（未确认）"
        )

        self.confirm_btn.setStyleSheet(
            "background:#d48a38;color:white;"
        )

        # 素材变了，之前同步到智能混剪的勾选清单也必须一起清掉，
        # 否则会一直用旧素材列表混剪，且界面上看不到任何变化。
        selector = getattr(self, "mix_selector", None)
        if selector is not None:
            try:
                selector.clear()
            except Exception:
                pass

        # 新界面：素材卡片回到“未分析”，避免显示过期分析结果
        page = getattr(self, "page_library", None)
        if page is not None:
            try:
                page.clear_analysis()
            except Exception:
                pass

        self.log(
            "素材发生变化，请重新检查并确认。"
        )

    def create_virtual_folder(self):

        text, ok = QInputDialog.getText(
            self,
            "新建分类",
            "请输入分类名称："
        )

        if ok and text.strip():

            # 分类节点靠“没有路径”与素材节点区分；路径绝对不能与素材路径
            # 撞上，同时去掉回车等会让树显示错行的控制字符。
            name = " ".join(str(text).split()).strip()
            if not name:
                return

            self.tree.record_state()

            category = QTreeWidgetItem(self.tree, [name, ""])
            # 新建后：选中、展开、允许直接改名，用户能立刻看到并往里放素材
            category.setFlags(category.flags() | Qt.ItemIsEditable)
            category.setChildIndicatorPolicy(
                QTreeWidgetItem.ShowIndicator
            )
            self.tree.setCurrentItem(category)
            self.tree.scrollToItem(category)
            category.setExpanded(True)

            self.invalidate_materials()

            self.update_count()
            self._refresh_library_cards()

            self.log(f"已新建分类：{name}（右键素材可“移动到分类”，或把视频拖到这个分类上）")

    def add_paths_to_tree(
        self,
        paths,
        parent_item=None,
        record_history=True
    ):

        if record_history:
            self.tree.record_state()

        added = 0

        existing = set(
            self.get_all_video_paths()
        )

        for path in paths:

            p = Path(path)

            if (
                p.is_file()
                and p.suffix.lower() in VIDEO_EXTS
            ):

                full_path = str(p.resolve())

                if full_path in existing:
                    continue

                item = QTreeWidgetItem([
                    p.name,
                    str(p.parent)
                ])

                item.setData(
                    1,
                    Qt.UserRole,
                    full_path
                )

                if parent_item:

                    parent_item.addChild(
                        item
                    )

                else:

                    self.tree.addTopLevelItem(
                        item
                    )

                existing.add(full_path)

                added += 1

        if parent_item:

            parent_item.setExpanded(
                True
            )

        self.update_count()

        if added:

            self.invalidate_materials()

        # 新界面：素材库卡片必须立刻跟上（否则导入后卡片一直是空的）
        self._refresh_library_cards()

        self.log(
            f"成功导入 {added} 个视频素材。"
        )

    def add_files(self):

        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频素材",
            "",
            "视频 (*.mp4 *.mov *.mkv *.m4v *.avi *.webm)"
        )

        if paths:

            current = self.tree.currentItem()

            parent = (
                current
                if current and not current.data(1, Qt.UserRole)
                else None
            )

            self.add_paths_to_tree(
                paths,
                parent
            )

            self._refresh_library_cards()

            # 新界面：导入后把用户带回素材库看结果（不改变导入逻辑本身）
            navigate = getattr(self, "_navigate", None)
            if callable(navigate):
                navigate("workbench")

    def add_folder(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "选择素材文件夹"
        )

        if not folder:
            return

        folder_path = Path(folder).resolve()

        self.tree.record_state()

        root_item = QTreeWidgetItem(
            self.tree,
            [folder_path.name, ""]
        )

        # 按相对路径建子文件夹层级，视频挂到对应子文件夹下
        existing = set(self.get_all_video_paths())
        dir_nodes = {folder_path: root_item}  # Path -> QTreeWidgetItem

        for p in sorted(folder_path.rglob("*")):
            if p.is_dir():
                rel = p.relative_to(folder_path)
                parent_path = p.parent
                parent_node = dir_nodes[parent_path]
                child = QTreeWidgetItem(parent_node, [p.name, ""])
                dir_nodes[p] = child
            elif p.suffix.lower() in VIDEO_EXTS:
                full = str(p)
                if full in existing:
                    continue
                rel = p.relative_to(folder_path)
                parent_path = p.parent
                parent_node = dir_nodes[parent_path]
                item = QTreeWidgetItem([p.name, str(p.parent)])
                item.setData(1, Qt.UserRole, full)
                parent_node.addChild(item)

        # 新界面：扫描完成后切到素材库查看
        self._refresh_library_cards()
        navigate = getattr(self, "_navigate", None)
        if callable(navigate):
            navigate("workbench")

        self.log(f"已扫描文件夹：{folder_path.name}（保留子目录结构）")

    def remove_selected(self):
        page = getattr(self, "page_library", None)
        selected = list(self.tree.selectedItems())

        # tree 被隐藏时（新界面入口），从素材库页取真实目标：
        # 文件夹 tile → 整个文件夹；否则点选/勾选的素材路径。
        if not selected and page is not None:
            getter = getattr(page, "removal_targets", None)
            folder_item, paths = (None, [])
            if callable(getter):
                folder_item, paths = getter()
            if folder_item is not None:
                selected.append(folder_item)
            for path in paths:
                it = QTreeWidgetItemIterator(self.tree)
                while it.value():
                    if it.value().data(1, Qt.UserRole) == path:
                        selected.append(it.value())
                        break
                    it += 1

        if not selected:
            QMessageBox.information(
                self, "提示",
                "请先在素材库选中要移除的素材（点一下素材卡片，或勾选左上角方框）。"
            )
            return

        self.tree.record_state()

        root = self.tree.invisibleRootItem()

        selected_set = set(selected)

        removed_paths = []

        for item in selected:
            stored = item.data(1, Qt.UserRole)
            if isinstance(stored, str):
                removed_paths.append(stored)
            else:
                # 文件夹：只收集这棵子树里的视频（QTreeWidgetItemIterator(item)
                # 会越过子树继续往下走，这里必须自己递归，避免误删别人的路径）
                stack = [item]
                while stack:
                    node = stack.pop()
                    sub = node.data(1, Qt.UserRole)
                    if isinstance(sub, str):
                        removed_paths.append(sub)
                    stack.extend(node.child(i) for i in range(node.childCount()))

        for item in selected:

            parent = item.parent() or root

            # 避免同时选择父子节点时重复操作

            ancestor = parent

            skip = False

            while ancestor and ancestor != root:

                if ancestor in selected_set:
                    skip = True
                    break

                ancestor = ancestor.parent()

            if not skip:

                parent.removeChild(
                    item
                )

        self.update_count()

        self.invalidate_materials()

        # 清掉素材库里对已移除素材的选中/勾选残留，避免"素材 0 / 已选 N"
        if page is not None:
            purge = getattr(page, "purge_paths", None)
            if callable(purge):
                purge(removed_paths)
            # 当前正在浏览的文件夹被移除时，退回根目录，避免留在已销毁节点下
            cur = getattr(page, "_current_dir", None)
            while cur is not None:
                if cur in selected_set:
                    page._current_dir = None
                    break
                cur = cur.parent()

        # 新界面：删除后卡片同步移除
        self._refresh_library_cards()

        if page is not None:
            refresh_boxes = getattr(page, "_refresh_checkboxes", None)
            if callable(refresh_boxes):
                refresh_boxes()

        if removed_paths:
            self.log(f"已从素材库移除 {len(removed_paths)} 个素材")

    def update_count(self):

        count = len(
            self.get_all_video_paths()
        )

        self.count_label.setText(
            f"{count} 个素材"
        )
        # 新界面顶部/卡片跟着真实数量刷新
        self._refresh_topbar()

    def get_all_video_paths(self):

        paths = []

        iterator = QTreeWidgetItemIterator(self.tree)

        while iterator.value():

            item = iterator.value()

            # 素材路径存放在第 1 列（列 0 是显示名称）

            path = item.data(1, Qt.UserRole)

            if (
                isinstance(path, str)
                and os.path.isfile(path)
                and Path(path).suffix.lower() in VIDEO_EXTS
            ):

                paths.append(path)

            iterator += 1

        return paths

    # =================================================
    # 素材检查 / 确认 / 预览
    # =================================================

    def on_tree_item_clicked(self, item, column):

        file_path = item.data(1, Qt.UserRole)

        if file_path and Path(file_path).is_file():

            self.player.setSource(
                QUrl.fromLocalFile(file_path)
            )

            self.update_detail_panel(file_path)

            self.log(
                f"预览加载: {Path(file_path).name}"
            )

    def update_detail_panel(self, file_path):
        """根据当前预览的视频刷新右下角详细参数。"""
        try:
            details = ft.probe_details(file_path)
            self.detail_box.setPlainText(
                self._format_details(details)
            )
        except Exception as e:
            self.detail_box.setPlainText(
                f"无法读取参数：{e}"
            )

    def _format_details(self, d):
        lines = []
        lines.append(f"文件：{Path(d['path']).name}")
        size = d.get("file_size")
        if size:
            lines.append(f"大小：{_human_size(size)}")

        fmt = d.get("format") or {}
        if fmt.get("duration") is not None:
            lines.append(f"时长：{fmt['duration']:.3f} 秒")
        if fmt.get("bit_rate"):
            lines.append(f"总码率：{fmt['bit_rate'] // 1000} kbps")
        if fmt.get("format_name"):
            lines.append(f"容器：{fmt['format_name']}")

        video = d.get("video")
        if video:
            lines.append("")
            lines.append("【视频流】")
            lines.append(
                f"分辨率：{video['width']} × {video['height']}"
            )
            if video.get("fps"):
                lines.append(f"帧率：{video['fps']} fps")
            if video.get("codec"):
                lines.append(f"编码：{video['codec']}")
            if video.get("pix_fmt"):
                lines.append(f"像素格式：{video['pix_fmt']}")
            if video.get("bit_rate"):
                lines.append(f"视频码率：{video['bit_rate'] // 1000} kbps")
            if video.get("nb_frames"):
                lines.append(f"总帧数：{video['nb_frames']}")

        audio = d.get("audio")
        if audio:
            lines.append("")
            lines.append("【音频流】")
            if audio.get("codec"):
                lines.append(f"编码：{audio['codec']}")
            if audio.get("channels"):
                lines.append(f"声道数：{audio['channels']}")
            if audio.get("sample_rate"):
                lines.append(f"采样率：{audio['sample_rate']} Hz")
            if audio.get("bit_rate"):
                lines.append(f"音频码率：{audio['bit_rate'] // 1000} kbps")
        else:
            lines.append("")
            lines.append("【音频流】无")

        return "\n".join(lines)

    def check_materials(self):

        paths = self.get_all_video_paths()

        if not paths:

            QMessageBox.warning(
                self,
                "提示",
                "素材库为空。"
            )

            return

        invalid_files = []

        for p in paths:
            try:
                if not Path(p).is_file() or os.path.getsize(p) <= 0:
                    invalid_files.append(Path(p).name)
            except OSError:
                invalid_files.append(Path(p).name)

        if invalid_files:

            QMessageBox.critical(
                self,
                "检查失败",
                f"发现 {len(invalid_files)} 个无效/损坏的素材文件：\n"
                + "\n".join(invalid_files[:10])
            )

            self.invalidate_materials()

        else:

            QMessageBox.information(
                self,
                "检查通过",
                f"共 {len(paths)} 个素材全部有效。\n"
                "请点“✓ 确认混剪素材”按钮锁定素材。"
            )

            self._refresh_topbar()
            self.log(
                f"检查通过：共 {len(paths)} 个素材有效，请点“确认混剪素材”按钮锁定。"
            )

    def confirm_materials(self):

        # 只取素材库里勾选的素材。
        # 勾选状态现在由宿主持有的 MaterialSelection 统一持有（解耦第2步），
        # 不再依赖旧素材库页的私有字段。
        raw = list(getattr(self, "selection", None).checked_paths()
                   if getattr(self, "selection", None) is not None else [])

        # 勾选集可能残留已经从素材库移除的路径：只认当前素材库里真实存在的素材
        lib_paths = set(self.get_all_video_paths())
        paths = [p for p in raw if p in lib_paths]
        if getattr(self, "selection", None) is not None:
            self.selection.set_checked(paths)
        # 旧页若在显示，它的勾选框与计数标签需要跟着同步（纯展示）
        page = getattr(self, "page_library", None)
        if page is not None:
            refresh = getattr(page, "_refresh_checkboxes", None)
            if callable(refresh):
                refresh()
            label = getattr(page, "sel_label", None)
            if label is not None:
                label.setText("已选 0")

        if not paths:
            QMessageBox.warning(
                self,
                "提示",
                ("勾选的素材已不在素材库（可能已被移除），请重新勾选后再确认。"
                 if raw else
                 "请先在素材库勾选要加入混剪的素材（点素材左上角的方框）。")
            )
            return

        for p in paths:
            try:
                is_invalid = not Path(p).is_file() or os.path.getsize(p) <= 0
            except OSError:
                is_invalid = True

            if is_invalid:
                QMessageBox.critical(
                    self,
                    "强制确认失败",
                    "存在无效文件，请先检查素材。"
                )

                self.invalidate_materials()

                return

        self.material_confirmed = True
        self._confirmed_paths = paths

        self.confirm_btn.setText(
            "已确定"
        )

        self.confirm_btn.setStyleSheet(
            "background:#2b7a4b;color:white;font-weight:bold;"
        )

        self.log(
            f"素材已确认锁定：{len(paths)} 个有效视频。"
        )

        # 重新确认视为全新开始：恢复此前在各框架里删除的素材
        selector = getattr(self, "mix_selector", None)
        if selector is not None:
            selector.reset_removed()

        self.refresh_selectors()

        QMessageBox.information(
            self,
            "锁定成功",
            "素材已确认"
        )

        # 确认后直接进入混剪配置页（不再让用户自己找入口）
        self.mix_start_btn.setEnabled(True)
        self._navigate("mix")

    def refresh_selectors(self, *args):
        """把已确认素材同步到智能混剪选择框架（保留已勾选状态）。

        分析页有结果时，按右键标记的"加入/不加入"设置勾选；
        没分析过则保持全选。
        """
        if not self.material_confirmed:
            return
        checked = None
        ap = getattr(self, "page_analyze", None)
        if ap is not None:
            if getattr(ap, "_results", None):
                checked = ap.mix_pool_paths()
            else:
                checked = ap.included_paths()
        if checked is not None:
            self.mix_selector.populate(self._confirmed_paths, checked)
        else:
            self.mix_selector.populate(self._confirmed_paths)
        # 新界面：顶部工具栏与素材库卡片的真实计数
        self._refresh_topbar()
        page = getattr(self, "page_library", None)
        if page is not None:
            page.refresh_cards()
