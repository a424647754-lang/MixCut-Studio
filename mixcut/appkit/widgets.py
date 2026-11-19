# -*- coding: utf-8 -*-
"""可复用的基础控件（从 main.py 原样搬出）。

三个控件都只依赖 PySide6 与标准库，不导入 mixcut 业务模块，
因此可以被任何层安全导入。

关于隐式依赖的说明
------------------
``UndoableTreeWidget`` 在运行时通过 ``self.window()`` / ``owner_window``
找到宿主窗口，再调用宿主上的 ``invalidate_materials`` / ``update_count`` /
``_refresh_library_cards`` / ``reload_library`` / ``add_paths_to_tree`` /
``remove_selected``。这是原实现就有的**鸭子类型**契约，拆分时保持原样，
不改成信号槽（那样会改变时序，风险大于收益）。
"""
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QObject
from PySide6.QtGui import QAction, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QAbstractSpinBox, QComboBox, QMenu,
)

from .constants import open_folder


class AspectRatioWidget(QWidget):
    """让内部 child 在容器内保持固定宽高比居中显示。

    窗口缩放时 child 跟着等比缩放，不会溢出遮挡下面的控件。
    """

    def __init__(self, child, ratio=9 / 16.0, parent=None):
        super().__init__(parent)
        self._child = child
        self._ratio = ratio  # width / height
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(child)

    def set_ratio(self, ratio):
        self._ratio = ratio
        self.resizeEvent(None)

    def resizeEvent(self, event):
        cw, ch = self.width(), self.height()
        if cw <= 0 or ch <= 0:
            return
        if cw / ch > self._ratio:
            w, h = int(ch * self._ratio), ch
        else:
            w, h = cw, int(cw / self._ratio)
        x = (cw - w) // 2
        y = (ch - h) // 2
        self._child.setGeometry(x, y, w, h)


class WheelGuard(QObject):
    """滚轮守卫：数字框 / 下拉框必须“先点击”才能用滚轮改值。

    规则（按用户要求）：
      · 鼠标左键或右键点过该控件 → 允许滚轮修改；
      · 点其它控件 / 点空白处 → 之前那个控件立即失效；
      · 只是鼠标悬停在上面（哪怕因为悬停/键盘获得了焦点）→ 滚轮被忽略。

    这样滚动页面时不会误改参数。
    """

    def __init__(self, app):
        super().__init__(app)
        self._armed = None      # 当前允许滚轮的控件（通过点击获得）
        app.installEventFilter(self)

    def eventFilter(self, obj, event):
        etype = event.type()

        # 1) 点击：左键 / 右键点中的控件获得“滚轮许可”
        if etype == QEvent.Type.MouseButtonPress:
            widget = obj if isinstance(obj, QWidget) else None
            if widget is None:
                return False
            button = event.button()
            if button in (Qt.LeftButton, Qt.RightButton):
                target = self._wheel_target(widget)
                self._armed = target
            return False

        # 2) 滚轮：只有被左键/右键点过的控件才允许改值；
        #    没点过的控件不吞事件，而是把滚轮转发给最近的滚动祖先，
        #    这样页面/列表照常滚动，不会“卡一下”，也不会误改参数。
        if etype == QEvent.Type.Wheel and isinstance(
                obj, (QAbstractSpinBox, QComboBox)):
            if obj is self._armed:
                return False  # 放行，让数字框/下拉框正常改值
            # 找最近的 QAbstractScrollArea 祖先，把事件转给它的 viewport
            from PySide6.QtWidgets import QAbstractScrollArea
            from PySide6.QtCore import QCoreApplication
            node = obj
            while node is not None:
                if isinstance(node, QAbstractScrollArea):
                    target = node.viewport()
                    if target is not None and target is not obj:
                        try:
                            QCoreApplication.sendEvent(target, event)
                        except Exception:
                            pass
                    return True  # 吞掉原事件，避免数字框改值
                node = node.parentWidget()
            return True

        return False

    @staticmethod
    def _wheel_target(widget):
        """从点击到的控件往上找，返回真正吃滚轮的数字框/下拉框。"""
        node = widget
        while node is not None:
            if isinstance(node, (QAbstractSpinBox, QComboBox)):
                return node
            node = node.parentWidget() if hasattr(node, "parentWidget") else None
        return None


class UndoableTreeWidget(QTreeWidget):
    """支持撤销、拖放、右键移动分类的素材树。

    运行时通过 ``window()`` 访问宿主的以下方法（鸭子类型契约）：
    ``invalidate_materials`` / ``update_count`` / ``_refresh_library_cards`` /
    ``reload_library`` / ``add_paths_to_tree`` / ``remove_selected``。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.history = []

        self.setSelectionMode(
            QAbstractItemView.ExtendedSelection
        )

        # 允许“树内移动 + 从资源管理器拖入文件”
        self.setDragDropMode(
            QAbstractItemView.DragDrop
        )
        self.setAcceptDrops(True)
        self.setDefaultDropAction(Qt.MoveAction)
        # 右键/拖放的目标行判定需要整行选中
        self.setDropIndicatorShown(True)

        self.undo_shortcut = QShortcut(
            QKeySequence("Ctrl+Z"),
            self
        )

        self.undo_shortcut.activated.connect(
            self.undo_action
        )

    def serialize_tree(self, item):

        data = []

        for i in range(item.childCount()):

            child = item.child(i)

            data.append({
                "text": child.text(0),
                "path": child.data(1, Qt.UserRole),
                "children": self.serialize_tree(child)
            })

        return data

    def record_state(self):

        state = self.serialize_tree(
            self.invisibleRootItem()
        )

        self.history.append(state)

        if len(self.history) > 50:
            self.history.pop(0)

    def deserialize_tree(self, data, parent):

        for item_data in data:

            item = QTreeWidgetItem([
                item_data["text"],
                ""
            ])

            path = item_data["path"]

            if path:
                item.setData(
                    1,
                    Qt.UserRole,
                    path
                )

                item.setText(
                    1,
                    str(Path(path).parent)
                )

            parent.addChild(item)

            self.deserialize_tree(
                item_data["children"],
                item
            )

        self.expandAll()

    def undo_action(self):

        if not self.history:
            return

        state = self.history.pop()

        self.clear()

        self.deserialize_tree(
            state,
            self.invisibleRootItem()
        )

        # 树嵌在旧页/TabWidget 里，新 UI 下 parentWidget() 链到不了主窗口，
        # 优先用建树时记下的宿主，其次再沿父链向上找。
        window = getattr(self, "owner_window", None)
        if window is None or not hasattr(window, "invalidate_materials"):
            window = self.window()
            while window is not None and not hasattr(window, "invalidate_materials"):
                window = window.parentWidget()
        if window is None:
            return

        window.invalidate_materials()
        window.update_count()

        # 新界面的素材卡片来自旧树快照：撤销后必须重新同步，
        # 否则树已经恢复、可见界面还是老样子。
        if getattr(window, "use_new_ui", False) and hasattr(window, "reload_library"):
            window.reload_library()

    def dragEnterEvent(self, event):
        # 资源管理器拖进来的文件也要接收
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        """拖放处理：

        · 从资源管理器拖入的文件：放进鼠标所指的分类（或根节点）；
        · 树内拖动素材：移动到目标分类下（QTreeWidget 的默认行为）。
        两种情况都会记一次撤销状态并刷新界面。
        """
        window = self.window()
        mime = event.mimeData()

        # ① 外部文件拖入
        if mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if not paths:
                event.ignore()
                return
            target = self.itemAt(event.position().toPoint())
            parent = self._category_target(target)
            window.add_paths_to_tree(paths, parent)
            event.acceptProposedAction()
            return

        # ② 树内移动
        self.record_state()
        super().dropEvent(event)
        self.clearSelection()
        window.invalidate_materials()
        window._refresh_library_cards()
        # 新界面读的是旧树快照：不重新同步，素材看起来还留在原来的文件夹里。
        if getattr(window, "use_new_ui", False) and hasattr(window, "reload_library"):
            window.reload_library()

    def _category_target(self, item):
        """返回可以接收素材的分类节点：分类本身，或素材所在的分类。"""
        if item is None:
            return None
        # 顶层/中间的分类节点：没有素材路径
        if not item.data(1, Qt.UserRole):
            return item
        # 拖到素材上：放进该素材所属的分类
        return item.parent()

    def _fill_move_menu(self, menu, item):
        """列出所有分类，点击即把素材移到该分类下。"""
        root = self.invisibleRootItem()
        current_parent = item.parent()
        found = []

        def move_to(target):
            self.record_state()
            old_parent = item.parent() or root
            old_parent.removeChild(item)
            target.addChild(item)
            target.setExpanded(True)
            window = self.window()
            window.invalidate_materials()
            window.update_count()
            window._refresh_library_cards()

        def collect(node, target_menu):
            for i in range(node.childCount()):
                child = node.child(i)
                # 分类节点：没有素材路径
                if child.data(1, Qt.UserRole):
                    continue
                label = child.text(0) or "（未命名分类）"
                if child is current_parent:
                    label += "（当前）"
                act = QAction(label, self)
                act.setEnabled(child is not current_parent)
                act.triggered.connect(
                    lambda _c=False, t=child: move_to(t))
                target_menu.addAction(act)
                found.append(child)
                # 子分类用缩进表示层级，避免空子菜单
                sub_menu = target_menu.addMenu("└ 子分类")
                before = len(sub_menu.actions())
                collect(child, sub_menu)
                if len(sub_menu.actions()) == before:
                    sub_menu.menuAction().setVisible(False)

        collect(root, menu)
        return found

    def contextMenuEvent(self, event):

        item = self.itemAt(event.pos())
        if not item:
            return

        menu = QMenu(self)

        path = item.data(1, Qt.UserRole)

        if path:

            action = QAction(
                "在本地文件夹中显示",
                self
            )

            action.triggered.connect(
                lambda: open_folder(
                    str(Path(path).parent)
                )
            )

            menu.addAction(action)

        # 素材：可以移动到某个分类
        if path:
            move_menu = menu.addMenu("移动到分类")
            self._fill_move_menu(move_menu, item)
            if move_menu.isEmpty():
                move_menu.setEnabled(False)
                move_menu.setTitle("移动到分类（暂无分类）")

        if item.childCount() > 0:

            action = QAction(
                "展开/折叠",
                self
            )

            action.triggered.connect(
                lambda: item.setExpanded(
                    not item.isExpanded()
                )
            )

            menu.addAction(action)

        # 删除：选中当前节点后调用主窗口的移除逻辑
        delete_action = QAction("删除", self)

        def _do_delete():
            self.clearSelection()
            item.setSelected(True)
            self.window().remove_selected()

        delete_action.triggered.connect(_do_delete)

        menu.addAction(delete_action)

        menu.exec_(event.globalPos())
