# -*- coding: utf-8 -*-
"""素材勾选状态的独立持有者。

背景
----
改造前，素材勾选状态 `_checked_paths` 存在旧素材库页 `LibraryPage` 上，
被四个模块从外部读写：
    mixcut/appkit/library_ops.py     confirm_materials
    mixcut/appkit/shell_nav.py       _refresh_topbar
    mixcut/appkit/project_state.py   _load_project_data
    mixcut/ui/demo/backend_bridge.py _normalize_initial_state / confirm / clear

这让「新界面依赖旧界面实例」成为硬约束——想删旧页就必须先解耦这块。

做法
----
把勾选状态抽成独立对象 `MaterialSelection`：

    · 旧页 `LibraryPage` 通过 property 把自己的 `_checked_paths`
      转接到这个对象上（旧代码 `self._checked_paths.add(p)` 继续可用）
    · 宿主 `MainWindow` 持有唯一实例 `self.selection`
    · 外部模块改为读写 `self.selection`，不再碰旧页

本模块只依赖标准库与 QtCore，不导入 main.py 或任何 UI 页面。
"""
from PySide6.QtCore import QObject, Signal


class MaterialSelection(QObject):
    """素材勾选状态的唯一持有者。

    维护一个路径集合，并在变化时发出 ``changed`` 信号。
    所有方法都对空值/异常输入做了容错——勾选状态散落在多处改写，
    任何一处传进来 None 都不应该让整个界面崩掉。
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths = set()

    # ---- 读 ----

    def checked_paths(self):
        """返回当前勾选路径的副本（外部修改副本不影响内部）。"""
        return set(self._paths)

    def is_checked(self, path):
        return str(path) in self._paths

    def count(self):
        return len(self._paths)

    def __contains__(self, path):
        return str(path) in self._paths

    def __len__(self):
        return len(self._paths)

    def __iter__(self):
        return iter(set(self._paths))

    # ---- 写 ----

    def set_checked(self, paths):
        """整体替换勾选集合。返回是否有实际变化。"""
        new = {str(p) for p in (paths or []) if p}
        if new == self._paths:
            return False
        self._paths = new
        self.changed.emit()
        return True

    def add(self, path):
        if not path:
            return False
        key = str(path)
        if key in self._paths:
            return False
        self._paths.add(key)
        self.changed.emit()
        return True

    def discard(self, path):
        if not path:
            return False
        key = str(path)
        if key not in self._paths:
            return False
        self._paths.discard(key)
        self.changed.emit()
        return True

    def clear(self):
        if not self._paths:
            return False
        self._paths.clear()
        self.changed.emit()
        return True

    def update_many(self, paths, checked=True):
        """批量加入或移出。返回是否发生变化。"""
        items = {str(p) for p in (paths or []) if p}
        if not items:
            return False
        before = len(self._paths)
        if checked:
            self._paths |= items
        else:
            self._paths -= items
        if len(self._paths) == before and checked:
            # 全部已存在
            if items <= self._paths and len(self._paths) == before:
                return False
        self.changed.emit()
        return True

    # ---- 与素材对齐 ----

    def reconcile(self, existing_paths=None):
        """按当前素材库中真实存在的素材裁剪勾选，返回被移除的路径集合。

        Args:
            existing_paths: 允许保留的路径集合；传 None 表示不裁剪。

        素材被删除后必须调用，否则混剪/分析还会引用已删除的原片。
        """
        if existing_paths is None:
            return set()
        allow = {str(p) for p in existing_paths}
        gone = self._paths - allow
        if gone:
            self._paths = self._paths & allow
            self.changed.emit()
        return gone

    # ---- 兼容层 ----

    def discard_missing(self, missing):
        """移出指定路径集合（别名，语义与 reconcile 的删除部分一致）。"""
        items = {str(p) for p in (missing or []) if p}
        if not items:
            return set()
        gone = self._paths & items
        if gone:
            self._paths -= gone
            self.changed.emit()
        return gone

    # ---- 集合协议 ----
    # 旧代码在 LibraryPage 内部直接对 _checked_paths 做集合运算
    # （`-=`、`in`、`len()`），这里实现对应协议让那些写法继续有效。

    def __isub__(self, other):
        """支持 `selection -= {...}`。"""
        items = {str(p) for p in (other or []) if p}
        if items & self._paths:
            self._paths -= items
            self.changed.emit()
        return self

    def __ior__(self, other):
        """支持 `selection |= {...}`。"""
        items = {str(p) for p in (other or []) if p}
        if items - self._paths:
            self._paths |= items
            self.changed.emit()
        return self

    def __eq__(self, other):
        if isinstance(other, MaterialSelection):
            return self._paths == other._paths
        if isinstance(other, (set, frozenset)):
            return self._paths == other
        return NotImplemented

    def __repr__(self):
        return "MaterialSelection(%d 项)" % len(self._paths)
