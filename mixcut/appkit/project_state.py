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
