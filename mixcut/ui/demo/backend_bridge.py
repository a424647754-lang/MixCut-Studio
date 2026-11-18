# -*- coding: utf-8 -*-
"""旧后台桥：按需加载父目录正式源码的 MainWindow，只借用它的处理方法。

原则：
- 父目录的 main.py / mixcut 只读，不在本文件修改；
- 旧窗口只作为不可见后台对象，不显示、不接替新 UI；
- 新 UI 的按钮把输入同步到旧控件，再调用旧方法，最后把结果同步回新 UI。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QTreeWidgetItem, QTreeWidgetItemIterator

PARENT_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = Path(__file__).resolve().parents[1]

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}


def _fmt_duration(seconds):
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return "%.1fs" % seconds
    return "%d:%02d" % (int(seconds // 60), int(seconds % 60))


class BackendBridge(QObject):
    """新 UI 与旧 MainWindow 之间唯一的适配层。"""

    def __init__(self, win, legacy=None):
        super().__init__(win)
        self.win = win
        # 嵌入旧工程时直接复用当前 MainWindow，不再二次加载 main.py。
        self._legacy = legacy
        self._legacy_module = None
        self._load_error = ""
        self._mix_log_seen = 0
        self._last_snapshot = {"folders": [], "clips": [], "root_name": "素材库"}
        self._poll = QTimer(self)
        self._poll.setInterval(700)
        self._poll.timeout.connect(self._poll_mix)
        self._mix_active = False

    # ---------------------------------------------------------
    # 加载
    # ---------------------------------------------------------
    @property
    def ready(self):
        return self._legacy is not None

    @property
    def load_error(self):
        return self._load_error

    def ensure(self):
        if self._legacy is not None:
            return self._legacy
        if self._load_error:
            return None
        try:
            if str(PARENT_DIR) not in sys.path:
                sys.path.insert(0, str(PARENT_DIR))
            os.environ["MIXCUT_NO_RECOVERY"] = "1"
            old_flag = os.environ.get("MIXCUT_LEGACY_UI")
            os.environ["MIXCUT_LEGACY_UI"] = "1"
            try:
                spec = importlib.util.spec_from_file_location(
                    "mixcut_legacy_main_for_shell", PARENT_DIR / "main.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            finally:
                if old_flag is None:
                    os.environ.pop("MIXCUT_LEGACY_UI", None)
                else:
                    os.environ["MIXCUT_LEGACY_UI"] = old_flag
            self._legacy_module = module
            win = module.MainWindow()
            win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            win.hide()
            # 旧窗口只是后台；它注册的全局快捷键不能抢占新 UI。
            for name in ("space_shortcut", "esc_shortcut", "p_shortcut"):
                sc = getattr(win, name, None)
                if sc is not None:
                    sc.setEnabled(False)
            self._legacy = win
            self._mix_log_seen = 0
            self._normalize_initial_state()
            return win
        except Exception:
            self._load_error = traceback.format_exc()
            self.win.controller.log("后台桥加载失败：%s" % self._load_error.splitlines()[-1])
            return None

    def shutdown(self):
        """新窗口关闭时先停掉后台轮询与旧窗口，避免 Qt 退出清理时崩溃。"""
        self._poll.stop()
        old = self._legacy
        self._legacy = None
        if old is None:
            return
        try:
            analyzer = getattr(getattr(old, "page_analyze", None), "_worker", None)
            if analyzer is not None and analyzer.isRunning():
                try:
                    analyzer.stop()
                except Exception:
                    pass
                analyzer.wait(3000)
        except Exception:
            pass
        try:
            worker = getattr(old, "mix_worker", None)
            if worker is not None and worker.isRunning():
                try:
                    old.cancel_mix()
                except Exception:
                    pass
                worker.wait(3000)
        except Exception:
            pass
        try:
            old.hide()
        except Exception:
            pass
        try:
            old.deleteLater()
        except Exception:
            pass

    def _normalize_initial_state(self):
        """旧工程初始为空；把新 UI 现有模拟素材转成旧树，保证入口按钮有上下文。"""
        # 新 UI 模拟素材没有真实文件路径，不能伪造进旧树，否则检查素材会失败。
        # 因此这里只确保旧素材库页的勾选集合存在。
        page = getattr(self._legacy, "page_library", None)
        if page is not None and not hasattr(page, "_checked_paths"):
            page._checked_paths = set()

    # ---------------------------------------------------------
    # 素材库
    # ---------------------------------------------------------
    def snapshot(self):
        """把旧树同步成“文件夹相对路径 + 素材”的清单。

        关键点：素材用 folder_path（如「子目录A/更深」）归属目录，而不是只记
        文件夹显示名。这样同名文件夹不会被合并，新 UI 也能区分嵌套子目录。
        """
        old = self.ensure()
        if old is None:
            return self._last_snapshot
        folders = []
        clips = []
        root_name = "素材库"
        try:
            tree = old.tree
            top_count = tree.topLevelItemCount()
            idx = 0

            def add_folder(node, rel_path, parent_id, depth):
                name = (node.text(0) or "未命名文件夹").strip() or "未命名文件夹"
                folder_id = "root" if not rel_path else "p:" + rel_path
                if rel_path:
                    folders.append({"id": folder_id, "name": name,
                                    "parent": parent_id, "depth": depth,
                                    "folder_path": rel_path})
                else:
                    folders.insert(0, {"id": "root", "name": name,
                                       "parent": "all", "depth": 1,
                                       "folder_path": ""})

                for ci in range(node.childCount()):
                    child = node.child(ci)
                    child_data = child.data(1, Qt.ItemDataRole.UserRole)
                    if isinstance(child_data, str) and child_data:
                        nonlocal idx
                        idx += 1
                        clips.append(self._clip_from_item(
                            child, child_data, name if rel_path else "", idx,
                            rel_path))
                        continue
                    if child.childCount() or child_data is None:
                        child_rel = (rel_path + "/" + child.text(0).strip()
                                     if rel_path else child.text(0).strip())
                        add_folder(child, child_rel, folder_id, depth + 1)

            for i in range(top_count):
                top = tree.topLevelItem(i)
                if top is None:
                    continue
                top_data = top.data(1, Qt.ItemDataRole.UserRole)
                if isinstance(top_data, str) and top_data:
                    # 顶层散装视频（不在文件夹里的）单独作为单条素材
                    if top_data.lower().endswith(tuple(_VIDEO_EXTS)):
                        idx += 1
                        clips.append(self._clip_from_item(top, top_data, "", idx, ""))
                    continue
                name = (top.text(0) or "未命名文件夹").strip() or "未命名文件夹"
                if i == 0 and not folders:
                    root_name = name
                    add_folder(top, "", "all", 0)
                else:
                    add_folder(top, name, "all", 1)
            # 没有根目录节点时仍给一个默认根
            if not folders:
                folders = [{"id": "root", "name": root_name,
                            "parent": "all", "depth": 1, "folder_path": ""}]
        except Exception:
            self.win.controller.log("读取旧素材树失败：%s" % traceback.format_exc().splitlines()[-1])

        snap = {"folders": folders, "clips": clips, "root_name": root_name}
        self._last_snapshot = snap
        return snap

    def _clip_from_item(self, node, path, folder, idx, folder_path=""):
        duration = "—"
        probe = getattr(self._legacy, "_probe_cache", {}) or {}
        info = probe.get(path) or {}
        fmt = info.get("format") or {}
        if fmt.get("duration"):
            duration = _fmt_duration(fmt["duration"])
        name = node.text(0) or Path(path).name
        meta = (getattr(getattr(self._legacy, "page_library", None), "_meta", {}) or {}).get(path, {})
        video = info.get("video") or {}
        width = video.get("width") or meta.get("width") or 0
        height = video.get("height") or meta.get("height") or 0
        bitrate = fmt.get("bit_rate") or video.get("bit_rate") or 0
        quality = round((width * height / 2073600.0) * 80 + float(bitrate or 0) / 1000000.0, 1)
        return {
            "id": "r%d" % idx,
            "name": name,
            "thumb": (idx - 1) % 6,
            "thumb_path": meta.get("thumb_path") or "",
            "dur": duration,
            "shots": 0,
            "folder": folder,
            "folder_path": folder_path,
            "quality": quality,
            "analyzed": str(meta.get("analysis_status") or "") in ("done", "cached"),
            "motion": 0,
            "stability": 0,
            "tags": [],
            "path": path,
            "width": video.get("width") or meta.get("width") or 0,
            "height": video.get("height") or meta.get("height") or 0,
            "codec": video.get("codec") or "",
            "fps": video.get("fps") or "",
            "pix_fmt": video.get("pix_fmt") or "",
            "bit_depth": (10 if "10" in str(video.get("pix_fmt") or "") else
                          12 if "12" in str(video.get("pix_fmt") or "") else
                          8 if video.get("pix_fmt") else ""),
            "file_size": info.get("file_size") or meta.get("file_size") or 0,
        }

    def has_real_media(self):
        old = self.ensure()
        if old is None:
            return False
        try:
            return bool(old.get_all_video_paths())
        except Exception:
            return False

    def _mark_history(self):
        """破坏性素材操作前先记一份快照，供 Ctrl+Z 撤回。"""
        win = getattr(self, "win", None)
        push = getattr(win, "_push_history", None)
        if callable(push):
            push()

    def add_files_from_paths(self, paths):
        """新 UI 先弹文件框，再把真实路径交给旧树，避免旧隐藏窗口弹框。"""
        old = self.ensure()
        if old is None or not paths:
            return 0
        self._mark_history()
        try:
            before = len(old.get_all_video_paths())
            old.add_paths_to_tree(list(paths), None)
            return max(0, len(old.get_all_video_paths()) - before)
        except Exception:
            self.win.controller.log("导入视频失败：%s" %
                                    traceback.format_exc().splitlines()[-1])
            return 0

    def add_folder_path(self, folder):
        """新 UI 先选目录，再按旧逻辑保留子目录结构写入旧树。"""
        old = self.ensure()
        if old is None or not folder:
            return 0
        self._mark_history()
        try:
            root_path = Path(folder).resolve()
            before = len(old.get_all_video_paths())
            old.tree.record_state()
            root_item = QTreeWidgetItem(old.tree, [root_path.name, ""])
            existing = set(old.get_all_video_paths())
            dir_nodes = {root_path: root_item}
            for p in sorted(root_path.rglob("*")):
                if p.is_dir():
                    parent_node = dir_nodes.get(p.parent)
                    if parent_node is None:
                        continue
                    child = QTreeWidgetItem(parent_node, [p.name, ""])
                    dir_nodes[p] = child
                elif p.suffix.lower() in _VIDEO_EXTS:
                    full = str(p)
                    if full in existing:
                        continue
                    parent_node = dir_nodes.get(p.parent)
                    if parent_node is None:
                        continue
                    item = QTreeWidgetItem([p.name, str(p.parent)])
                    item.setData(1, Qt.ItemDataRole.UserRole, full)
                    parent_node.addChild(item)
            old._refresh_library_cards()
            return max(0, len(old.get_all_video_paths()) - before)
        except Exception:
            self.win.controller.log("扫描文件夹失败：%s" %
                                    traceback.format_exc().splitlines()[-1])
            return 0

    def add_files(self):
        old = self.ensure()
        if old is None:
            return False
        old.add_files()
        return bool(old.get_all_video_paths())

    def add_folder(self):
        old = self.ensure()
        if old is None:
            return False
        old.add_folder()
        return bool(old.get_all_video_paths())

    def refresh_materials(self):
        old = self.ensure()
        if old is None:
            return
        old._refresh_materials_now()

    def create_folder(self):
        old = self.ensure()
        if old is None:
            return False
        before = self._folder_names()
        old.create_virtual_folder()
        return self._folder_names() != before

    def create_folder_named(self, name):
        """新 UI 输入名称后直接建顶层空分类，避开旧隐藏窗口的输入框。"""
        old = self.ensure()
        if old is None or not name:
            return False
        self._mark_history()
        try:
            old.tree.record_state()
            # 同名文件夹会让 snapshot() 里的「p:<相对路径>」撞车，移动时就会
            # 串到另一个同名目录；这里统一改成不重复的名字。
            name = self._unique_folder_name(old, name)
            item = QTreeWidgetItem(old.tree, [name, ""])
            old.tree.setCurrentItem(item)
            old.tree.expandItem(item)
            old._refresh_library_cards()
            return True
        except Exception:
            self.win.controller.log("新建文件夹失败：%s" %
                                    traceback.format_exc().splitlines()[-1])
            return False

    @staticmethod
    def _unique_folder_name(old, name):
        used = set()
        try:
            for i in range(old.tree.topLevelItemCount()):
                node = old.tree.topLevelItem(i)
                if node is not None and not node.data(1, Qt.ItemDataRole.UserRole):
                    used.add((node.text(0) or "").strip())
        except Exception:
            return name
        name = str(name).strip() or "新建文件夹"
        if name not in used:
            return name
        i = 2
        while "%s (%d)" % (name, i) in used:
            i += 1
        return "%s (%d)" % (name, i)

    def delete_folder(self, name):
        old = self.ensure()
        if old is None or not name:
            return False
        tree = getattr(old, "tree", None)
        page = getattr(old, "page_library", None)
        if tree is None or page is None:
            return False
        item = self._find_folder_item(name)
        if item is None or item is self._root_folder_item():
            return False
        self._mark_history()
        try:
            # 先把整棵子目录里的视频移到顶层散装位置
            videos = []
            stack = [item]
            while stack:
                node = stack.pop()
                for ci in range(node.childCount()):
                    child = node.child(ci)
                    child_data = child.data(1, Qt.ItemDataRole.UserRole)
                    if (isinstance(child_data, str) and child_data
                            and child_data.lower().endswith(tuple(_VIDEO_EXTS))):
                        videos.append(child)
                    else:
                        stack.append(child)
            for video in videos:
                parent = video.parent()
                if parent is None:
                    continue
                parent.removeChild(video)
                tree.addTopLevelItem(video)
            # 再摘掉文件夹本身；子目录随文件夹一起消失
            parent = item.parent()
            if parent is None:
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
            else:
                parent.removeChild(item)
            old.update_count()
            old.invalidate_materials()
            old._refresh_library_cards()
            return True
        except Exception:
            self.win.controller.log("删除文件夹失败：%s" %
                                    traceback.format_exc().splitlines()[-1])
            return False

    def rename_folder(self, old_name, new_name):
        old = self.ensure()
        if old is None or not old_name or not new_name or old_name == new_name:
            return False
        try:
            item = self._find_folder_item(old_name)
            if item is None:
                return False
            self._mark_history()
            item.setText(0, new_name)
            return True
        except Exception:
            return False

    def move_paths(self, paths, dest_name):
        old = self.ensure()
        if old is None or not dest_name:
            return 0
        dest = self._find_folder_item(dest_name)
        if dest is None:
            return 0
        self._mark_history()
        moved = 0
        for path in paths:
            item = self._find_video_item(path)
            if item is None:
                continue
            parent = item.parent()
            if parent is dest:
                continue
            if parent is None:
                old.tree.takeTopLevelItem(old.tree.indexOfTopLevelItem(item))
            else:
                parent.removeChild(item)
            dest.addChild(item)
            dest.setExpanded(True)
            moved += 1
        if moved:
            old.update_count()
            old.invalidate_materials()
            old._refresh_library_cards()
        return moved

    def remove_paths(self, paths):
        old = self.ensure()
        if old is None:
            return 0
        if not paths:
            return 0
        self._mark_history()
        old.tree.clearSelection()
        selected = 0
        for path in paths:
            item = self._find_video_item(path)
            if item is not None:
                item.setSelected(True)
                selected += 1
        if not selected:
            return 0
        old.remove_selected()
        return selected

    def check_materials(self):
        """检查全部真实素材，不弹旧窗口的隐藏提示框。"""
        old = self.ensure()
        if old is None:
            return {"total": 0, "invalid": []}
        paths = old.get_all_video_paths()
        invalid = []
        for p in paths:
            try:
                if not Path(p).is_file() or os.path.getsize(p) <= 0:
                    invalid.append(Path(p).name)
            except OSError:
                invalid.append(Path(p).name)
        if invalid:
            old.invalidate_materials()
        return {"total": len(paths), "invalid": invalid}

    def confirm_materials(self, paths):
        """按新 UI 当前素材锁定，不弹旧窗口的隐藏提示框。"""
        old = self.ensure()
        if old is None:
            return False
        page = getattr(old, "page_library", None)
        lib_paths = set(old.get_all_video_paths())
        paths = [p for p in (paths or []) if p in lib_paths]
        if not paths:
            return False
        for p in paths:
            try:
                if not Path(p).is_file() or os.path.getsize(p) <= 0:
                    old.invalidate_materials()
                    return False
            except OSError:
                old.invalidate_materials()
                return False
        if page is not None:
            page._checked_paths = set(paths)
            refresh = getattr(page, "_refresh_checkboxes", None)
            if callable(refresh):
                refresh()
        old.material_confirmed = True
        old._confirmed_paths = list(paths)
        selector = getattr(old, "mix_selector", None)
        if selector is not None:
            try:
                selector.reset_removed()
            except Exception:
                pass
        old.refresh_selectors()
        return True

    def clear_checked(self):
        old = self.ensure()
        page = getattr(old, "page_library", None) if old else None
        if page is not None:
            page._checked_paths = set()
            refresh = getattr(page, "_refresh_checkboxes", None)
            if callable(refresh):
                refresh()

    def _root_folder_item(self):
        """旧树的第一个顶层目录就是“素材库”根容器。"""
        old = self.ensure()
        if old is None:
            return None
        for i in range(old.tree.topLevelItemCount()):
            item = old.tree.topLevelItem(i)
            if item.data(1, Qt.ItemDataRole.UserRole) is None:
                return item
        return None

    def _iter_folders(self):
        """遍历所有目录，返回 (item, 相对路径, 名称)；根容器路径为空。"""
        old = self.ensure()
        if old is None:
            return []

        def walk(node, rel):
            name = (node.text(0) or "").strip()
            yield node, rel, name
            for ci in range(node.childCount()):
                child = node.child(ci)
                if child.data(1, Qt.ItemDataRole.UserRole) is not None:
                    continue
                child_rel = (rel + "/" + (child.text(0) or "").strip()
                             if rel else (child.text(0) or "").strip())
                yield from walk(child, child_rel)

        result = []
        for i in range(old.tree.topLevelItemCount()):
            item = old.tree.topLevelItem(i)
            if item.data(1, Qt.ItemDataRole.UserRole) is not None:
                continue
            rel = "" if i == 0 else (item.text(0) or "").strip()
            result.extend(walk(item, rel))
        return result

    def _find_folder_item(self, ref):
        """按相对路径（优先）或显示名找目录，同名目录不再互相混淆。"""
        folders = self._iter_folders()
        ref = str(ref or "")
        for item, rel, _name in folders:
            if ref in ("root", "") and rel == "":
                return item
            if rel and rel == ref:
                return item
        for item, rel, name in folders:
            if name == ref:
                return item
        return None

    def _folder_names(self):
        old = self.ensure()
        if old is None:
            return []
        names = []
        for i in range(old.tree.topLevelItemCount()):
            item = old.tree.topLevelItem(i)
            if item.data(1, Qt.ItemDataRole.UserRole) is None:
                names.append(item.text(0))
        return names

    def _find_video_item(self, path):
        old = self.ensure()
        if old is None:
            return None
        stack = [old.tree.topLevelItem(i) for i in range(old.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            if item.data(1, Qt.ItemDataRole.UserRole) == path:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    # ---------------------------------------------------------
    # 参数
    # ---------------------------------------------------------
    def sync_params(self, page):
        old = self.ensure()
        if old is None:
            return
        try:
            # 新 UI 的清晰度/帧率有横竖屏区分，旧预设只有竖屏尺寸，
            # 因此直接把画布尺寸交给引擎覆盖，不再拿索引去硬套旧预设。
            width, height, fps = page.canvas_size()
            old._mix_canvas_override = (width, height, fps)
            # 旧控件的显示值仍对齐到最接近的预设，便于旧页/工具提示自洽
            index = min(range(old.mix_fps.count()),
                        key=lambda i: abs(old.mix_fps.itemData(i) - fps))
            old.mix_fps.setCurrentIndex(index)
            old.mix_target.setValue(page.target.value())
            old.mix_tolerance.setValue(page.tolerance.value())
            old.mix_clip_count.setValue(page.count.value())
            old.mix_clip_min.setValue(page.clip_min.value())
            old.mix_clip_max.setValue(page.clip_max.value())
            old.mix_diversity.setCurrentIndex(page.diversity.currentIndex())
            old.mix_rhythm.setCurrentIndex(page.rhythm.currentIndex())
            old.mix_version.setCurrentIndex(page.version.currentIndex())
            old.mix_beat_step.setCurrentIndex(page.beat_step.currentIndex())
            old.mix_shuffle.setChecked(page.t_shuffle.toggle.isChecked())
            old.mix_reuse.setChecked(page.t_reuse.toggle.isChecked())
            old.mix_sentence_snap.setChecked(page.t_sentence.toggle.isChecked())
            old.mix_audio_dedup.setChecked(page.t_dedup.toggle.isChecked())
            old.mix_openclip.setChecked(page.t_oclip.toggle.isChecked())
            old.mix_draft_dir.setText(page.draft_dir.text().strip())
            old.music_mode.setCurrentIndex(page.bgm_mode.currentIndex())
            old.mix_bgm.setText(page.bgm.text().strip())
            old.music_folder.setText(page.music_dir.text().strip())
            old.mix_base_vol.setValue(page.vol_normal.value())
            old.mix_duck_vol.setValue(page.vol_duck.value())
            old.mix_beat_sync.setChecked(page.t_beat.toggle.isChecked())
            old.mix_ducking.setChecked(page.t_duck.toggle.isChecked())
            old.mix_trans_enable.setChecked(page.t_trans.toggle.isChecked())
            old.mix_short.setValue(page.trans_short.value())
            old.mix_long.setValue(page.trans_long.value())
            old.mix_dissolve_dur.setValue(page.trans_dissolve.value())
            old.mix_blacklist.setText(",".join(sorted(page.blacklist.selected)))
            old.mix_video_detect.setChecked(page.bad_pic.toggle.isChecked())
            old.mix_audio_detect.setChecked(page.bad_audio.toggle.isChecked())
            checks = getattr(old, "filter_checks", {})
            for key, box in page.comp_boxes.items():
                if key in checks:
                    checks[key].setChecked(box.isChecked())
            # 硬件加速：GPU 不可用时回落到 CPU，避免把无 CUDA 的机器设成 GPU
            gpu_radio = getattr(old, "filter_gpu_radio", None)
            if gpu_radio is not None and page.hw_gpu.isChecked() and gpu_radio.isEnabled():
                gpu_radio.setChecked(True)
            else:
                cpu_radio = getattr(old, "filter_cpu_radio", None)
                if cpu_radio is not None:
                    cpu_radio.setChecked(True)
            for key, box in getattr(old, "detect_threshold_boxes", {}).items():
                if key == "black_min_duration":
                    box.setValue(page.black_min.value())
                elif key == "freeze_min_duration":
                    box.setValue(page.freeze_min.value())
                elif key == "silence_min_duration":
                    box.setValue(page.silence_min.value())
        except Exception:
            self.win.controller.log("参数同步到旧后台失败：%s" %
                                    traceback.format_exc().splitlines()[-1])

    def filter_preset(self, name):
        old = self.ensure()
        if old is None:
            return
        try:
            import mixcut.filter_engine as fe
            preset = fe.preset_fast_batch() if name == "快速批量" else fe.preset_fine_qc()
            old._apply_filter_preset(preset)
        except Exception:
            self.win.controller.log("筛选预设同步失败：%s" %
                                    traceback.format_exc().splitlines()[-1])

    def force_self_check(self, show_dialog=False):
        old = self.ensure()
        if old is None:
            return False
        return bool(old.run_force_self_check(show_dialog=show_dialog))

    def set_bgm_path(self, path):
        old = self.ensure()
        if old is None or not path:
            return False
        old.mix_bgm.setText(path)
        try:
            old._on_music_mode()
        except Exception:
            pass
        return True

    def set_music_folder(self, folder):
        old = self.ensure()
        if old is None or not folder:
            return False
        old.music_folder.setText(folder)
        return True

    def set_draft_dir(self, folder):
        old = self.ensure()
        if old is None or not folder:
            return False
        old.mix_draft_dir.setText(folder)
        return True

    def choose_bgm(self):
        old = self.ensure()
        if old is None:
            return ""
        old.choose_bgm()
        return old.mix_bgm.text().strip()

    def choose_music_folder(self):
        old = self.ensure()
        if old is None:
            return ""
        old.choose_music_folder()
        return old.music_folder.text().strip()

    def choose_draft_dir(self):
        old = self.ensure()
        if old is None:
            return ""
        old.choose_draft_dir()
        return old.mix_draft_dir.text().strip()

    # ---------------------------------------------------------
    # 分析
    # ---------------------------------------------------------
    def set_confirmed_paths(self, paths):
        old = self.ensure()
        if old is None:
            return False
        if not paths:
            return False
        old._confirmed_paths = list(paths)
        old.material_confirmed = True
        return True

    def start_analysis(self, paths, mode_index=1):
        old = self.ensure()
        if old is None or not self.set_confirmed_paths(paths):
            return False
        page = getattr(old, "page_analyze", None)
        if page is None:
            return False
        try:
            page.mode_box.setCurrentIndex(min(2, max(0, int(mode_index))))
            page._start_analysis()
            return page._worker is not None
        except Exception:
            self.win.controller.log("启动分析失败：%s" %
                                    traceback.format_exc().splitlines()[-1])
            return False

    def stop_analysis(self):
        old = self.ensure()
        page = getattr(old, "page_analyze", None) if old else None
        if page is not None:
            page._stop_analysis()

    def analysis_state(self):
        old = self.ensure()
        page = getattr(old, "page_analyze", None) if old else None
        if page is None:
            return {}
        items = list(getattr(page, "_results", []) or [])
        summary = {}
        segments = []
        if items:
            score = sum(float(x.get("score", 0) or 0) for x in items) / len(items)
            bad_ratio = sum(float(x.get("bad_frame_ratio", 0) or 0) for x in items) / len(items)
            included = sum(1 for x in items if x.get("included"))
            paths = {x.get("path") for x in items if x.get("path")}
            summary = {
                "segments": len(items),
                "paths": len(paths),
                "quality": round(score, 1),
                "bad_ratio": round(bad_ratio * 100, 1),
                "stable": round(max(0.0, 100.0 - bad_ratio * 100.0), 1),
                "valid": round(included * 100.0 / len(items), 1),
            }
            for x in items[:80]:
                segments.append({
                    "start": float(x.get("seg_start", 0) or 0),
                    "end": float(x.get("seg_end", 0) or 0),
                    "score": float(x.get("score", 0) or 0),
                    "bad": bool(x.get("is_bad")),
                    "included": bool(x.get("included")),
                })
        return {
            "running": bool(page._worker is not None and page._worker.isRunning()),
            "progress": int(page.progress_bar.value()),
            "label": page.progress_label.text(),
            "results": len(items),
            "stats": (page.stat_total._value_label.text(),
                      page.stat_analyzed._value_label.text(),
                      page.stat_duration._value_label.text(),
                      page.stat_bad._value_label.text()),
            "summary": summary,
            "segments": segments,
            "pool": page.pool_state() if hasattr(page, "pool_state") else {},
        }

    def _pool_page(self):
        old = self.ensure()
        return getattr(old, "page_analyze", None) if old else None

    def pool_set_member(self, path, included):
        page = self._pool_page()
        if page is None or not hasattr(page, "set_mix_member"):
            return False
        page.set_mix_member(path, bool(included))
        return True

    def pool_create_folder(self, name):
        page = self._pool_page()
        if page is None or not hasattr(page, "create_mix_folder"):
            return ""
        return page.create_mix_folder(name)

    def pool_assign_folder(self, paths, folder_id):
        page = self._pool_page()
        if page is None or not hasattr(page, "assign_mix_folder"):
            return False
        return page.assign_mix_folder(list(paths or []), folder_id)

    def pool_set_folder_checked(self, folder_id, checked):
        page = self._pool_page()
        if page is None or not hasattr(page, "set_mix_folder_checked"):
            return False
        return page.set_mix_folder_checked(folder_id, bool(checked))


    # ---------------------------------------------------------
    # 混剪
    # ---------------------------------------------------------
    def start_mix(self, paths, params_page=None):
        old = self.ensure()
        if old is None:
            return False
        if params_page is not None:
            self.sync_params(params_page)
        self.set_confirmed_paths(paths)
        page_analyze = getattr(old, "page_analyze", None)
        if page_analyze is not None and not getattr(page_analyze, "_results", None):
            self.win.controller.log("开始混剪 · 缺少分析结果，请先在 AI 分析页完成分析")
            return False
        try:
            old.refresh_selectors()
            selector = old.mix_selector
            if hasattr(selector, "selected_paths") and not selector.selected_paths():
                if hasattr(selector, "set_all_checked"):
                    selector.set_all_checked(True)
            old.start_mix()
            worker = getattr(old, "mix_worker", None)
            started = worker is not None and worker.isRunning()
            if started:
                self._mix_active = True
                self._mix_log_seen = 0
                self._poll.start()
            return started
        except Exception:
            self.win.controller.log("启动混剪失败：%s" %
                                    traceback.format_exc().splitlines()[-1])
            return False

    def cancel_mix(self):
        old = self.ensure()
        if old is not None:
            old.cancel_mix()
        self._poll.stop()

    def toggle_pause(self):
        old = self.ensure()
        if old is None:
            return False
        worker = getattr(old, "mix_worker", None)
        if worker is None or not worker.isRunning():
            return False
        if getattr(worker, "is_paused", lambda: False)():
            old._resume_mix()
            return True
        old._pause_mix()
        return True

    def _poll_mix(self):
        old = self._legacy
        if old is None:
            self._poll.stop()
            return
        c = self.win.controller
        worker = getattr(old, "mix_worker", None)
        running = worker is not None and worker.isRunning()
        try:
            pct = int(old.mix_progress.value())
        except Exception:
            pct = 0
        self._sync_mix_log(old)
        if running:
            c.active_stage = min(5, max(0, pct // 17))
            c.done_stages = min(6, pct // 17)
            c.progress = min(0.99, pct / 100.0)
            c.status_text = "运行中"
            c.paused = bool(getattr(worker, "is_paused", lambda: False)())
            c.stateChanged.emit()
        else:
            if self._mix_active:
                self._mix_active = False
                self._poll.stop()
                results = getattr(old, "_last_results", None) or []
                if results:
                    c.done_stages = 6
                    c.progress = 1.0
                    c.status_text = "已完成"
                    c.drafted = True
                    path = results[0].get("draft_path", "")
                    c.log("草稿已生成：%s" % path)
                    self.win.tasks.out_path.setText(path or "D:\\MixCut_Drafts")
                else:
                    c.status_text = "已取消" if getattr(old, "_mix_canceled", False) else "未完成"
                    c.active_stage = -1
                    c.done_stages = min(c.done_stages, 5)
                    c.progress = min(c.progress, 0.99)
                c.stateChanged.emit()

    def _sync_mix_log(self, old):
        try:
            text = old.mix_log.toPlainText()
        except Exception:
            return
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) < self._mix_log_seen:
            self._mix_log_seen = 0
        for line in lines[self._mix_log_seen:]:
            self.win.controller.log("后台 · " + line)
        self._mix_log_seen = len(lines)

    def open_output(self):
        old = self.ensure()
        if old is None:
            return False
        results = getattr(old, "_last_results", None) or []
        path = ""
        if results:
            path = results[0].get("draft_path", "")
            if path and Path(path).exists() and Path(path).is_file():
                path = str(Path(path).parent)
        if not path:
            path = old.mix_draft_dir.text().strip()
        if not path or not Path(path).exists():
            self.win.controller.log("输出目录尚未生成：%s" % (path or "未设置"))
            return False
        old_module = self._legacy_module
        opener = getattr(old_module, "open_folder", None) if old_module else None
        if callable(opener):
            opener(path)
        else:
            os.startfile(path)  # noqa: S606 - Windows shell open
        self.win.controller.log("已打开输出目录：%s" % path)
        return True

    # ---------------------------------------------------------
    # 设置 / 引擎
    # ---------------------------------------------------------
    def save_project(self):
        """保留旧入口兼容；新 UI 用 save_project_to 传入新窗口选择的路径。"""
        return self.save_project_to("")

    def save_project_to(self, path):
        old = self.ensure()
        if old is None or not path:
            return False
        original = QFileDialog.getSaveFileName
        try:
            QFileDialog.getSaveFileName = staticmethod(
                lambda *args, **kwargs: (path, ""))
            return bool(old.save_project())
        except Exception:
            self.win.controller.log("保存工程失败：%s" %
                                    traceback.format_exc().splitlines()[-1])
            return False
        finally:
            QFileDialog.getSaveFileName = original

    def open_project(self):
        """保留旧入口兼容；新 UI 用 open_project_from 传入新窗口选择的路径。"""
        return self.open_project_from("")

    def open_project_from(self, path):
        old = self.ensure()
        if old is None or not path:
            return False
        original = QFileDialog.getOpenFileName
        try:
            QFileDialog.getOpenFileName = staticmethod(
                lambda *args, **kwargs: (path, ""))
            result = old.open_project()
            return bool(result)
        except Exception:
            self.win.controller.log("打开工程失败：%s" %
                                    traceback.format_exc().splitlines()[-1])
            return False
        finally:
            QFileDialog.getOpenFileName = original

    def refresh_engine(self):
        old = self.ensure()
        if old is None:
            return ""
        old.refresh_engine_status()
        return old.engine_status.text()

    def open_engine_doc(self):
        old = self.ensure()
        if old is not None:
            old.open_engine_doc()

    def open_engine_cache(self):
        old = self.ensure()
        if old is not None:
            old.open_engine_cache()

    def reload_engine(self):
        """真正重新加载 OpenCLIP 模型（force=True），不是只探一条素材。"""
        try:
            from mixcut import openclip_embed as oce
            info = oce.load_model(force=True)
            self.win.controller.log("MIX 引擎 · OpenCLIP 模型重载：%s" %
                                    ("就绪" if info.get("available") else
                                     info.get("reason", "不可用")))
            return bool(info.get("available"))
        except Exception:
            self.win.controller.log("重新加载模型失败：%s" %
                                    traceback.format_exc().splitlines()[-1])
            return False
