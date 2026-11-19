# -*- coding: utf-8 -*-
"""项目状态与撤销/重做（从 main.py 原样搬出）。

第一批：撤销与重做机制
    _tree_state / _history_state / _history_sig / _push_history /
    undo_step / redo_step / _apply_history_state

第二批（Phase 5-B）会继续把项目存档相关方法搬进来。

拆分方式说明
------------
Mixin：方法仍挂在 MainWindow 上，``_undo_stack`` / ``_redo_stack``
仍是宿主实例属性，懒初始化逻辑不变。

宿主契约
--------
**状态属性**
    ``self._undo_stack``  ``self._redo_stack``  ``self._history_busy``
    ``self._params_seen``  ``self._known_paths``  ``self._confirmed_paths``
    ``self._project_path``  ``self._saved_state_hash``

**可控件 / 子系统**
    ``self.tree``  ``self.controller``  ``self.shell``
    ``self.demo_library``  ``self.demo_params``  ``self.demo_tasks``
    ``self.demo_workbench``  ``self.page_analyze``
    ``self.material_confirmed``  ``self.player``  ``self.audio_output``
    ``self.aspect_combo``

**宿主方法**
    ``self.log``  ``self._refresh_topbar``  ``self._project_params``
    ``self.get_all_video_paths``  ``self.reload_library``
    ``self._probe_paths``  ``self._sync_material_state``
    ``self.selected_paths``  ``self._demo_filters_state``
    ``self.set_preview_proxy_width``

依赖方向：只导入 PySide6、标准库与 mixcut 的少量非 UI 模块。
不导入 main.py，不导入 mixcut.ui。
"""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QFileDialog


class ProjectStateMixin:
    """项目状态快照与撤销/重做。"""

    # =================================================
    # 撤回 / 还原（快照式：素材结构 + 勾选 + 确认 + 参数）
    # =================================================
    def _tree_state(self):
        """旧素材树的完整快照（文本 + 路径 + 父子关系），撤回时原样还原。"""
        try:
            old = self.backend.ensure()
        except Exception:
            old = None
        tree = getattr(old, "tree", None)
        if tree is None or not hasattr(tree, "serialize_tree"):
            return None
        return tree.serialize_tree(tree.invisibleRootItem())

    def _history_state(self):
        from mixcut.ui import demo
        clips = [{"path": c.get("path") or "",
                  "folder": c.get("folder") or "全部素材",
                  "folder_path": c.get("folder_path") or ""}
                 for c in demo.mock.CLIPS if c.get("path") and not c.get("_removed")]
        folders = [{"name": f.get("name") or "",
                    "folder_path": f.get("folder_path") or "",
                    "parent": f.get("parent") or "all",
                    "depth": f.get("depth") or 1}
                   for f in getattr(self.demo_library, "folders", [])
                   if f.get("id") not in ("all",) and f.get("name")]
        selected = {c["path"] for c in demo.mock.CLIPS
                    if c.get("id") in getattr(self.controller, "selected", set())
                    and c.get("path")}
        page = getattr(self, "page_analyze", None)
        removed_items = [dict(c) for c in demo.mock.CLIPS if c.get("_removed")
                         and c.get("path")]
        return {
            "clips": clips,
            "folders": folders,
            "selected": sorted(selected),
            "confirmed": list(getattr(self, "_confirmed_paths", []) or []),
            "params": self._project_params(),
            "params_v3": (self.demo_params.capture()
                          if hasattr(self.demo_params, "capture") else {}),
            "tree": self._tree_state(),
            "removed_items": removed_items,
            "analysis": {
                "mode": getattr(page, "_mode_key", "medium"),
                "finished": bool(getattr(page, "_finished", False)),
                "items": [dict(r) for r in getattr(page, "_results", [])],
                "pool": (page.pool_state()
                         if hasattr(page, "pool_state") else {}),
            },
            "filters": self._demo_filters_state(),
            "page": getattr(getattr(self, "shell", None), "current", "library"),
            "current_path": next((c.get("path") for c in demo.mock.CLIPS
                                  if c.get("id") == getattr(self.controller, "_current", None)
                                  and c.get("path")), ""),
        }

    def _history_sig(self, st=None):
        st = st or self._history_state()
        no_root = ("全部素材", "")
        return (tuple((c["path"], c["folder"])
                      for c in st["clips"] if c["folder"] not in no_root),
                tuple(st["folders"]))

    def _push_history(self, st=None):
        if getattr(self, "_history_busy", False):
            return
        try:
            st = st or self._history_state()
        except Exception:
            return
        stack = getattr(self, "_undo_stack", None)
        if stack is None:
            self._undo_stack = []
            self._redo_stack = []
            stack = self._undo_stack
        key = repr(st)
        if stack and stack[-1].get("_key") == key:
            return
        st["_key"] = key
        stack.append(st)
        if len(stack) > 80:
            stack.pop(0)
        self._redo_stack.clear()

    def undo_step(self):
        stack = getattr(self, "_undo_stack", None) or []
        if not stack:
            self.controller.log("撤回 · 没有可撤回的操作了")
            return
        cur = self._history_state()
        target = stack.pop()
        cur["_key"] = repr(cur)
        self._redo_stack.append(cur)
        self._apply_history_state(target, "撤回")

    def redo_step(self):
        stack = getattr(self, "_redo_stack", None) or []
        if not stack:
            self.controller.log("还原 · 没有可还原的操作了")
            return
        cur = self._history_state()
        target = stack.pop()
        cur["_key"] = repr(cur)
        self._undo_stack.append(cur)
        self._apply_history_state(target, "还原")

    def _apply_history_state(self, st, label):
        from mixcut.ui import demo

        self._history_busy = True
        page = getattr(self, "page_analyze", None)
        try:
            # 已移除素材随历史一起回滚，reload 后才能重建“已移除”卡片
            self.controller.removed_clips = [dict(c) for c in
                                             (st.get("removed_items") or [])]
            tree_now = self._tree_state()
            if st.get("tree") is not None and repr(tree_now) != repr(st.get("tree")):
                old = self.backend.ensure()
                tree = getattr(old, "tree", None)
                if tree is not None:
                    tree.clear()
                    tree.deserialize_tree(st["tree"], tree.invisibleRootItem())
                    old.invalidate_materials()
                    old.update_count()
            self.reload_library(preserve_selection=False)
            sel_paths = set(st["selected"])
            self.controller.selected = {
                c["id"] for c in demo.mock.CLIPS if c.get("path") in sel_paths}
            self._confirmed_paths = list(st["confirmed"])
            self.material_confirmed = bool(self._confirmed_paths)
            self._apply_project_params(st.get("params") or {})
            cur_path = st.get("current_path") or ""
            if cur_path:
                cid = next((c["id"] for c in demo.mock.CLIPS
                            if c.get("path") == cur_path), "")
                if cid:
                    self.controller._current = cid
            # 撤回素材树时同步恢复分析结果，而不是只把素材放回来
            ana = st.get("analysis") or {}
            items = [r for r in (ana.get("items") or [])
                     if isinstance(r, dict) and r.get("path")
                     and os.path.isfile(r["path"])]
            if page is not None:
                if items:
                    page.load_results(items, finished=bool(ana.get("finished", True)),
                                      mode_key=ana.get("mode"))
                else:
                    page.clear_results()
                if ana.get("pool"):
                    page.load_pool_state(ana.get("pool"))
            if hasattr(self, "demo_params") and st.get("params_v3"):
                self.demo_params.restore(st.get("params_v3") or {})
            if st.get("filters"):
                self._restore_filters(st["filters"])
        finally:
            self._history_busy = False
        try:
            self._params_seen = self._project_params()
        except Exception:
            pass
        self._sync_material_state()
        self._refresh_topbar()
        if hasattr(self, "demo_params"):
            self.demo_params.refresh_run_state()
        if st.get("page") in getattr(getattr(self, "shell", None), "pages", {}):
            self.go_page(st["page"])
        self.controller.log("%s · 已回到上一步" % label)

    # =================================================
    # 项目存档（Phase 5-B）
    # =================================================

    def _project_params(self):
        """收集当前界面上的混剪参数，供保存工程使用。

        保存与恢复必须一一对应：这里写进去的每个键，open_project 都要能还原。
        新增参数时两边一起改，不再出现“存了不读”的字段。
        """
        def val(widget, getter, default=None):
            if widget is None:
                return default
            try:
                return getter(widget)
            except Exception:
                return default

        return {
            # 时长与片段
            "target_duration": val(getattr(self, "mix_target", None), lambda w: w.value(), 30.0),
            "tolerance": val(getattr(self, "mix_tolerance", None), lambda w: w.value(), 5.0),
            "clip_min": val(getattr(self, "mix_clip_min", None), lambda w: w.value(), 2.0),
            "clip_max": val(getattr(self, "mix_clip_max", None), lambda w: w.value(), 5.0),
            "project_count": val(getattr(self, "mix_clip_count", None), lambda w: w.value(), 1),
            # 画布
            "resolution_index": val(getattr(self, "mix_resolution", None), lambda w: w.currentIndex(), 0),
            "fps_index": val(getattr(self, "mix_fps", None), lambda w: w.currentIndex(), 0),
            # 排列
            "shuffle": val(getattr(self, "mix_shuffle", None), lambda w: w.isChecked(), True),
            "reuse": val(getattr(self, "mix_reuse", None), lambda w: w.isChecked(), True),
            # 卡点
            "beat_sync": val(getattr(self, "mix_beat_sync", None), lambda w: w.isChecked(), True),
            "beat_step": val(getattr(self, "mix_beat_step", None), lambda w: w.currentData(), 0),
            "beat_tolerance": val(getattr(self, "mix_beat_tolerance", None), lambda w: w.value(), 0.12),
            "weak_beat_chance": val(getattr(self, "mix_weak_chance", None), lambda w: w.value(), 0.25),
            # 策略
            "diversity": val(getattr(self, "mix_diversity", None), lambda w: w.currentData(), "medium"),
            "rhythm": val(getattr(self, "mix_rhythm", None), lambda w: w.currentData(), "balance"),
            "version_style": val(getattr(self, "mix_version", None), lambda w: w.currentData(), "balanced"),
            # 增强开关
            "sentence_snap": val(getattr(self, "mix_sentence_snap", None), lambda w: w.isChecked(), False),
            "audio_dedup": val(getattr(self, "mix_audio_dedup", None), lambda w: w.isChecked(), False),
            "openclip": val(getattr(self, "mix_openclip", None), lambda w: w.isChecked(), True),
            # 输出位置
            "draft_dir": val(getattr(self, "mix_draft_dir", None), lambda w: w.text(), ""),
            # 配乐
            "music_mode": val(getattr(self, "music_mode", None), lambda w: w.currentIndex(), 0),
            "music_path": val(getattr(self, "mix_bgm", None), lambda w: w.text(), ""),
            "ducking": val(getattr(self, "mix_ducking", None), lambda w: w.isChecked(), False),
            "base_volume": val(getattr(self, "mix_base_vol", None), lambda w: w.value(), 1.0),
            "duck_volume": val(getattr(self, "mix_duck_vol", None), lambda w: w.value(), 0.35),
            # 转场
            "trans_enable": val(getattr(self, "mix_trans_enable", None), lambda w: w.isChecked(), False),
            "dissolve_duration": val(getattr(self, "mix_dissolve_dur", None), lambda w: w.value(), 0.4),
            "short_threshold": val(getattr(self, "mix_short", None), lambda w: w.value(), 1.5),
            "long_threshold": val(getattr(self, "mix_long", None), lambda w: w.value(), 3.0),
            "blacklist": val(getattr(self, "mix_blacklist", None), lambda w: w.text(), ""),
        }

    def _apply_project_params(self, p):
        """把工程里的参数写回界面；缺字段一律按默认值处理，兼容旧工程。"""
        def set_num(widget, key, default, caster=float):
            if widget is None:
                return
            raw = p.get(key, default)
            try:
                if isinstance(caster, type) and caster is int:
                    widget.setValue(int(raw))
                else:
                    widget.setValue(float(raw))
            except (TypeError, ValueError):
                widget.setValue(type(default)(default) if not isinstance(default, bool) else default)

        def set_bool(widget, key, default):
            if widget is not None:
                widget.setChecked(bool(p.get(key, default)))

        def set_text(widget, key, default=""):
            if widget is not None:
                widget.setText(str(p.get(key, default) or ""))

        def set_index(widget, key, default):
            if widget is None:
                return
            try:
                widget.setCurrentIndex(int(p.get(key, default)))
            except (TypeError, ValueError):
                widget.setCurrentIndex(int(default))

        def set_data(widget, key, default):
            """按 currentData 还原（下拉框的文案可能改，data 才是稳定值）。"""
            if widget is None:
                return
            target = p.get(key, default)
            idx = widget.findData(target)
            if idx < 0:
                idx = widget.findData(default)
            if idx >= 0:
                widget.setCurrentIndex(idx)

        set_num(getattr(self, "mix_target", None), "target_duration", 30.0)
        set_num(getattr(self, "mix_tolerance", None), "tolerance", 5.0)
        set_num(getattr(self, "mix_clip_min", None), "clip_min", 2.0)
        set_num(getattr(self, "mix_clip_max", None), "clip_max", 5.0)
        set_num(getattr(self, "mix_clip_count", None), "project_count", 1, caster=int)
        set_index(getattr(self, "mix_resolution", None), "resolution_index", 0)
        set_index(getattr(self, "mix_fps", None), "fps_index", 0)
        set_bool(getattr(self, "mix_shuffle", None), "shuffle", True)
        set_bool(getattr(self, "mix_reuse", None), "reuse", True)
        set_bool(getattr(self, "mix_beat_sync", None), "beat_sync", True)
        set_data(getattr(self, "mix_beat_step", None), "beat_step", 0)
        set_num(getattr(self, "mix_beat_tolerance", None), "beat_tolerance", 0.12)
        set_num(getattr(self, "mix_weak_chance", None), "weak_beat_chance", 0.25)
        set_data(getattr(self, "mix_diversity", None), "diversity", "medium")
        set_data(getattr(self, "mix_rhythm", None), "rhythm", "balance")
        set_data(getattr(self, "mix_version", None), "version_style", "balanced")
        set_bool(getattr(self, "mix_sentence_snap", None), "sentence_snap", False)
        set_bool(getattr(self, "mix_audio_dedup", None), "audio_dedup", False)
        set_bool(getattr(self, "mix_openclip", None), "openclip", True)
        set_text(getattr(self, "mix_draft_dir", None), "draft_dir", "")
        set_index(getattr(self, "music_mode", None), "music_mode", 0)
        set_text(getattr(self, "mix_bgm", None), "music_path", "")
        set_bool(getattr(self, "mix_ducking", None), "ducking", False)
        set_num(getattr(self, "mix_base_vol", None), "base_volume", 1.0)
        set_num(getattr(self, "mix_duck_vol", None), "duck_volume", 0.35)
        set_bool(getattr(self, "mix_trans_enable", None), "trans_enable", False)
        set_num(getattr(self, "mix_dissolve_dur", None), "dissolve_duration", 0.4)
        set_num(getattr(self, "mix_short", None), "short_threshold", 1.5)
        set_num(getattr(self, "mix_long", None), "long_threshold", 3.0)
        set_text(getattr(self, "mix_blacklist", None), "blacklist", "")

    # =================================================
    # 工程 v3：素材树 / 分类 / 勾选 / 分析结果 / 参数 / 筛选 / 界面状态
    # =================================================
    def _project_tree_state(self):
        """旧素材树全量结构（分类 + 子目录 + 视频路径 + 显示名）。"""
        try:
            old = self.backend.ensure()
        except Exception:
            old = None
        tree = getattr(old, "tree", None)
        if tree is None or not hasattr(tree, "serialize_tree"):
            return None
        try:
            return tree.serialize_tree(tree.invisibleRootItem())
        except Exception:
            return None

    def _demo_filters_state(self):
        page = getattr(self, "demo_library", None)
        if page is None:
            return {}
        chip = next((n for n, c in page.chips.items() if c.isChecked()), "全部")
        folder = next((f for f in page.folders
                       if f.get("id") == getattr(page, "active_folder", "all")), None)
        return {
            "folder_name": (folder or {}).get("name", ""),
            "folder_path": (folder or {}).get("folder_path", ""),
            "chip": chip,
            "search": page.search.text(),
            "sort": page.sort.currentIndex(),
            "min_dur": page.min_dur.currentIndex(),
            "view": page.view_mode.currentIndex(),
        }

    def _project_state_v3(self):
        import datetime
        from mixcut.ui import demo

        try:
            from mixcut import preview_proxy
            proxy_width = preview_proxy.get_width()
        except Exception:
            proxy_width = 0
        page = getattr(self, "page_analyze", None)
        demo_clips = [dict(c) for c in demo.mock.CLIPS if c.get("path")]
        removed_items = [dict(c) for c in demo.mock.CLIPS
                         if c.get("_removed") and c.get("path")]
        player = getattr(self, "player", None)
        audio = getattr(self, "audio_output", None)
        return {
            "version": 3,
            "app": "MixCut",
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "library": {"tree": self._project_tree_state(), "root_name": "素材库"},
            "materials": list(getattr(self, "_confirmed_paths", []) or []),
            "materials_meta": demo_clips,
            "selected": self.selected_paths(),
            "removed": [c.get("path") for c in removed_items],
            "removed_items": removed_items,
            "current_clip": next((c.get("path") for c in demo.mock.CLIPS
                                  if c.get("id") == getattr(self.controller, "_current", None)
                                  and c.get("path")), ""),
            "analysis": {
                "mode": getattr(page, "_mode_key", "medium"),
                "finished": bool(getattr(page, "_finished", False)),
                "items": [dict(r) for r in getattr(page, "_results", [])],
                "pool": (page.pool_state()
                         if hasattr(page, "pool_state") else {}),
            },
            "params": self._project_params(),
            "params_v3": (self.demo_params.capture()
                          if hasattr(self.demo_params, "capture") else {}),
            "filters": self._demo_filters_state(),
            "ui": {
                "page": getattr(getattr(self, "shell", None), "current", "library"),
                "scroll": (self.demo_library.scroll.verticalScrollBar().value()
                           if hasattr(self, "demo_library") else 0),
            },
            "player": {
                "aspect": (self.aspect_combo.currentIndex()
                           if hasattr(self, "aspect_combo") else 0),
                "position": (player.position() if player is not None else 0),
                "volume": (audio.volume() if audio is not None else 1.0),
            },
            "task": (self.demo_tasks.capture_state()
                     if hasattr(self, "demo_tasks") and hasattr(self.demo_tasks, "capture_state")
                     else {}),
            "output": {"proxy_width": proxy_width},
        }

    def _project_state_hash(self, data=None):
        data = data or self._project_state_v3()
        # 只把“真正的工程内容”算进改动指纹；滚动、播放进度、异步探测到的
        # 时长/缩略图等浏览或缓存状态变化，不应触发“未保存”提示。
        keys = ("version", "materials", "selected", "removed", "removed_items",
                "current_clip", "analysis", "params", "params_v3", "filters")
        payload = {k: data.get(k) for k in keys}
        payload["library"] = {"tree": (data.get("library") or {}).get("tree")}
        payload["ui"] = {"page": (data.get("ui") or {}).get("page")}
        payload["output"] = data.get("output")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _project_dirty(self):
        """工程是否有未保存改动：以最近一次保存/打开的内容指纹为准。"""
        base = getattr(self, "_saved_state_hash", "")
        if not base:
            try:
                state = self._project_state_v3()
            except Exception:
                return False
            return bool(state.get("materials") or state.get("analysis", {}).get("items")
                        or self.get_all_video_paths())
        try:
            return self._project_state_hash() != base
        except Exception:
            return False

    def save_project(self, path=None):
        """保存工程：校验 → 原子写临时文件 → 备份旧工程 → 替换。

        Ctrl+S 已保存过时走 `_shortcut_save` 直接传路径；设置页仍可弹框。
        """
        from PySide6.QtWidgets import QFileDialog

        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "保存工程", getattr(self, "_project_path", "") or "",
                "MixCut 工程 (*.mixcut)")
            if not path:
                return False
        if not str(path).lower().endswith(".mixcut"):
            path = str(path) + ".mixcut"
        path = os.path.abspath(path)

        page = getattr(self, "page_analyze", None)
        worker = getattr(page, "_worker", None)
        if worker is not None and worker.isRunning():
            ok = QMessageBox.question(
                self, "分析还在进行",
                "AI 分析尚未完成，现在保存的是已完成的部分结果。\n"
                "建议等分析结束后再保存。仍要保存吗？")
            if ok != QMessageBox.StandardButton.Yes:
                self.log("工程保存已取消：等待分析完成")
                return False

        data = self._project_state_v3()
        if data.get("library", {}).get("tree") is None:
            QMessageBox.warning(self, "保存失败", "后台素材库尚未加载，无法保存完整工程。")
            self.log("工程保存失败：后台素材库未就绪")
            return False

        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".mixcut_save_", suffix=".tmp",
                                       dir=os.path.dirname(path) or ".")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if os.path.isfile(path):
                try:
                    shutil.copy2(path, path + ".bak")
                except OSError:
                    pass
            os.replace(tmp, path)
            tmp = None
        except Exception as exc:
            self.log("工程保存失败：%s" % exc)
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            QMessageBox.warning(self, "保存失败", "工程写入失败：%s" % exc)
            return False

        self._project_path = path
        self._saved_state_hash = self._project_state_hash(data)
        self._refresh_topbar()
        self.log(f"工程已保存：{path}（{len(data['materials'])} 个已选素材、"
                 f"{len(data['analysis']['items'])} 个分析片段）")
        return True

    def _restore_library_tree(self, data):
        """用工程里的树结构还原旧素材树；旧格式只有名单时退化为平铺。"""
        old = self.backend.ensure()
        if old is None:
            return 0, []
        tree = getattr(old, "tree", None)
        lib = data.get("library") or {}
        tree_state = lib.get("tree")
        paths = []
        if tree_state:
            tree.clear()
            tree.deserialize_tree(tree_state, tree.invisibleRootItem())
            old.invalidate_materials()
            old.update_count()
            try:
                paths = list(old.get_all_video_paths())
            except Exception:
                paths = []
        else:
            mats = [str(x) for x in (data.get("materials") or []) if str(x).strip()]
            alive = [p for p in mats if os.path.isfile(p) and os.path.getsize(p) > 0]
            if alive:
                old.add_paths_to_tree(alive, None)
                old.invalidate_materials()
                old.update_count()
            paths = alive
        existing = {p for p in paths if os.path.isfile(p)}
        missing = [p for p in paths if p not in existing]
        return len(existing), missing

    def _restore_filters(self, f):
        page = getattr(self, "demo_library", None)
        if page is None or not f:
            return
        if f.get("search"):
            page.search.setText(str(f["search"]))
        chip = f.get("chip")
        if chip and chip in page.chips:
            page._filter(chip)
        for widget, key in ((page.sort, "sort"), (page.min_dur, "min_dur"),
                            (page.view_mode, "view")):
            try:
                widget.setCurrentIndex(int(f.get(key, widget.currentIndex())))
            except (TypeError, ValueError):
                pass
        folder_path = f.get("folder_path")
        folder_name = f.get("folder_name") or ""
        fid = ""
        if folder_path is not None:
            fid = next((x["id"] for x in page.folders
                        if x.get("folder_path") == folder_path), "")
        if not fid:
            fid = next((x["id"] for x in page.folders
                        if x.get("name") == folder_name), "all")
        page._select_folder(fid)

    def _load_project_data(self, data, path):
        from mixcut.ui import demo

        if self.backend.ensure() is None:
            self.log("打开工程失败：后台素材库未加载，工程未切换")
            QMessageBox.warning(self, "打开失败", "后台素材库未加载，无法恢复工程。")
            return False
        p = data.get("params") or {}
        applied = len(p)
        page = getattr(self, "page_analyze", None)
        if page is not None:
            page.clear_results()

        self._history_busy = True
        try:
            # 0) 已移除素材先放回 controller，reload 时才能重新生成“已移除”卡片
            removed_items = data.get("removed_items")
            if not isinstance(removed_items, list):
                removed_items = []
                for rp in (data.get("removed") or []):
                    if isinstance(rp, str) and rp:
                        removed_items.append({"path": rp, "name": Path(rp).name,
                                              "folder": "", "folder_path": "",
                                              "dur": "—", "_removed": True})
            self.controller.removed_clips = [dict(x) for x in removed_items
                                             if isinstance(x, dict) and x.get("path")]
            # 1) 旧参数先还原（v2 只有这一套；v3 随后被新 UI 参数覆盖）
            if p:
                self._apply_project_params(p)
            # 2) 素材树（分类 / 子目录 / 素材顺序）
            alive_n, missing = self._restore_library_tree(data)
            # 3) 新 UI 素材库刷新（明确换工程，不保留上一工程勾选）
            self.reload_library(preserve_selection=False)
            # 2.5) 用工程里保存的素材元数据覆盖重新探测前的占位值
            meta_by_path = {}
            for m in (data.get("materials_meta") or []):
                if isinstance(m, dict) and m.get("path"):
                    meta_by_path[m["path"]] = m
            if meta_by_path:
                fields = ("name", "dur", "width", "height", "codec", "fps",
                          "pix_fmt", "bit_depth", "file_size", "quality", "tags",
                          "thumb_path")
                for clip in demo.mock.CLIPS:
                    meta = meta_by_path.get(clip.get("path"))
                    if meta:
                        for key in fields:
                            if key in meta:
                                clip[key] = meta[key]
                if hasattr(self.demo_library, "refresh"):
                    self.demo_library.refresh()
            # 4) 筛选状态
            self._restore_filters(data.get("filters") or {})
            # 5) 勾选 / 确认名单
            sel_paths = set(data.get("selected") or [])
            self.controller.selected = {c["id"] for c in demo.mock.CLIPS
                                        if c.get("path") in sel_paths}
            removed_paths = set(data.get("removed") or [])
            try:
                self.controller.removed = {c["id"] for c in demo.mock.CLIPS
                                           if c.get("path") in removed_paths}
            except Exception:
                pass
            cur_path = data.get("current_clip") or ""
            if cur_path:
                cid = next((c["id"] for c in demo.mock.CLIPS
                            if c.get("path") == cur_path), "")
                if cid:
                    self.controller._current = cid
            confirmed = [p2 for p2 in (data.get("materials") or [])
                         if os.path.isfile(p2)]
            self._confirmed_paths = confirmed
            self.material_confirmed = bool(confirmed)
            old_page = getattr(self, "page_library", None)
            if old_page is not None and hasattr(old_page, "_checked_paths"):
                old_page._checked_paths = set(confirmed)
                for name in ("_refresh_checkboxes", "_sync_selection_labels"):
                    fn = getattr(old_page, name, None)
                    if callable(fn):
                        fn()
            self.controller.stateChanged.emit()
            # 6) 分析结果（片段级，含废镜 / 纳入状态）
            ana = data.get("analysis") or {}
            items = ana.get("items") or []
            alive_items = [r for r in items
                           if isinstance(r, dict) and r.get("path")
                           and os.path.isfile(r["path"])]
            if len(alive_items) != len(items):
                self.log("分析结果里有 %d 个片段的原素材已不存在，已跳过"
                         % (len(items) - len(alive_items)))
            if page is not None and alive_items:
                page.load_results(alive_items,
                                  finished=bool(ana.get("finished", True)),
                                  mode_key=ana.get("mode"))
            if page is not None and ana.get("pool"):
                page.load_pool_state(ana.get("pool"))
            # 7) 新 UI 参数（真正驱动混剪的一套）
            self.demo_params.restore(data.get("params_v3") or {})
            try:
                self.backend.sync_params(self.demo_params)
            except Exception:
                pass
            # 8) 代理开关（工程里记录过就沿用）
            width = (data.get("output") or {}).get("proxy_width")
            if width:
                try:
                    self.set_preview_proxy_width(width)
                except Exception:
                    pass
        finally:
            self._history_busy = False

        # 9) 收尾刷新
        self._known_paths = set(self._probe_paths())
        self._refresh_topbar()
        if hasattr(self, "demo_tasks"):
            if hasattr(self.demo_tasks, "restore_state"):
                self.demo_tasks.restore_state(data.get("task") or {})
            self.demo_tasks.refresh()
        self.demo_params.refresh_run_state()
        if hasattr(self, "demo_workbench"):
            self.demo_workbench.refresh()
        # 10) 回到保存时所在的页面
        target_page = (data.get("ui") or {}).get("page") or "library"
        if getattr(getattr(self, "shell", None), "pages", None) and \
                target_page in self.shell.pages:
            self.go_page(target_page)
        # 工作区播放器/画幅/滚动位置
        pstate = data.get("player") or {}
        try:
            if hasattr(self, "aspect_combo") and pstate.get("aspect") is not None:
                self.aspect_combo.setCurrentIndex(int(pstate.get("aspect") or 0))
            if hasattr(self, "audio_output") and pstate.get("volume") is not None:
                self.audio_output.setVolume(float(pstate.get("volume", 1.0)))
            if hasattr(self, "player") and pstate.get("position"):
                self.player.setPosition(int(pstate.get("position") or 0))
        except Exception:
            pass
        try:
            scroll = int((data.get("ui") or {}).get("scroll") or 0)
            if hasattr(self, "demo_library"):
                self.demo_library.scroll.verticalScrollBar().setValue(scroll)
        except Exception:
            pass

        self._project_path = path
        self._undo_stack = []
        self._redo_stack = []
        try:
            self._params_seen = self._project_params()
            self._saved_state_hash = self._project_state_hash()
        except Exception:
            self._saved_state_hash = ""
        n_ana = len(self.page_analyze._results) if page is not None else 0
        self.log(f"工程已打开：{path}（{alive_n} 个素材、{n_ana} 个分析片段）")
        if missing:
            names = "、".join(Path(m).name for m in missing[:5])
            more = f" 等 {len(missing)} 个" if len(missing) > 5 else ""
            self.log(f"工程里有素材找不到（路径保留待重连）：{names}{more}")
        return True

    def new_project(self):
        """新建空工程：清素材树、勾选、分析与参数，不重启程序。"""
        if self._project_dirty():
            ok = QMessageBox.question(
                self, "新建工程",
                "当前工程还有未保存的改动，新建会丢失这些改动。\n要继续吗？")
            if ok != QMessageBox.StandardButton.Yes:
                return False
        old = self.backend.ensure()
        if old is None:
            QMessageBox.warning(self, "新建失败", "后台素材库尚未加载。")
            return False
        self._history_busy = True
        try:
            old.tree.clear()
            old.invalidate_materials()
            old.update_count()
            self.controller.selected.clear()
            self.controller.removed.clear()
            self.controller.removed_clips = []
            self.controller.analyzed = False
            self.controller.analyzed_all = False
            self._confirmed_paths = []
            self.material_confirmed = False
            page = getattr(self, "page_analyze", None)
            if page is not None:
                page.clear_results()
            self.reload_library(preserve_selection=False)
            if hasattr(self.demo_params, "_reset_defaults"):
                self.demo_params._reset_defaults()
            try:
                self.backend.sync_params(self.demo_params)
            except Exception:
                pass
        finally:
            self._history_busy = False
        self._project_path = ""
        self._saved_state_hash = ""
        self._undo_stack = []
        self._redo_stack = []
        self._known_paths = set()
        self._refresh_topbar()
        if hasattr(self, "demo_tasks"):
            if hasattr(self.demo_tasks, "restore_state"):
                self.demo_tasks.restore_state({})
            self.demo_tasks.refresh()
        self.demo_params.refresh_run_state()
        if hasattr(self, "demo_workbench"):
            self.demo_workbench.refresh()
        self.go_page("library")
        self.log("已新建空工程")
        return True

    def open_project(self):
        """打开工程：校验 → 提示未保存 → 还原素材树 → 刷新 UI → 恢复分析/参数。"""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", getattr(self, "_project_path", "") or "",
            "MixCut 工程 (*.mixcut)")
        if not path:
            return False
        if self._project_dirty():
            ok = QMessageBox.question(
                self, "有未保存的改动",
                "当前工程还有未保存的改动，打开另一个工程会丢失这些改动。\n要继续打开吗？")
            if ok != QMessageBox.StandardButton.Yes:
                return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("工程文件格式不正确")
            version = int(data.get("version") or 0)
            if version > 3:
                raise ValueError("工程版本比当前软件新，请升级 MixCut")
            if not isinstance(data.get("materials", []), list):
                raise ValueError("工程素材清单格式不正确")
            lib = data.get("library", {})
            if lib is not None and not isinstance(lib, dict):
                raise ValueError("工程素材库结构不正确")
            if isinstance(lib, dict) and lib.get("tree") is not None \
                    and not isinstance(lib.get("tree"), (dict, list)):
                raise ValueError("工程素材树结构不正确")
            for key in ("filters", "analysis", "params", "params_v3", "ui", "player"):
                if data.get(key) is not None and not isinstance(data.get(key), dict):
                    raise ValueError("工程字段 %s 格式不正确" % key)
        except Exception as exc:
            self.log("打开工程失败：%s" % exc)
            QMessageBox.warning(self, "打开失败",
                                "这个文件不是有效的 MixCut 工程：\n%s" % exc)
            return False
        try:
            return self._load_project_data(data, path)
        except Exception as exc:
            self.log("工程恢复中断：%s" % exc)
            QMessageBox.warning(self, "打开失败",
                                "工程数据恢复失败，已保留当前界面：\n%s" % exc)
            return False
