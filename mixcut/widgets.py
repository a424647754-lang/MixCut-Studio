"""
MixCut · 可复用界面组件

MaterialSelector：勾选式素材选择框架。
在智能混剪界面复用，列出素材管理页已确认的素材，
让用户在素材很多时只勾选本次要处理的视频。
"""

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QLabel,
)


class MaterialSelector(QGroupBox):

    selection_changed = Signal()

    def __init__(self, title="素材选择（勾选要剪辑的视频）", parent=None):
        super().__init__(title, parent)

        layout = QVBoxLayout(self)

        self.hint = QLabel("素材确认后会在此列出，勾选即参与本次处理")
        self.hint.setStyleSheet("color:#8491a5;")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        btn_row = QHBoxLayout()
        self.btn_all = QPushButton("全选")
        self.btn_none = QPushButton("全不选")
        self.btn_invert = QPushButton("反选")
        self.btn_remove = QPushButton("删除已选")
        self.btn_remove.setStyleSheet(
            "background:#7a3030;color:white;"
        )
        btn_row.addWidget(self.btn_all)
        btn_row.addWidget(self.btn_none)
        btn_row.addWidget(self.btn_invert)
        btn_row.addWidget(self.btn_remove)
        layout.addLayout(btn_row)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        # 已删除的素材（刷新时仍保持删除）与最近一次完整素材
        self._removed = set()
        self._last_paths = []

        self.btn_all.clicked.connect(lambda: self.set_all(Qt.CheckState.Checked))
        self.btn_none.clicked.connect(lambda: self.set_all(Qt.CheckState.Unchecked))
        self.btn_invert.clicked.connect(self.invert)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.list.itemChanged.connect(lambda *_: self.selection_changed.emit())

    # ------------------------------------------------
    # 数据
    # ------------------------------------------------

    def populate(self, paths, checked_paths=None):
        """
        重建列表。默认全部勾选；已删除的素材不显示；勾选状态按路径保留。
        """
        self._last_paths = list(paths)
        previous = set(self.selected_paths())
        default_checked = set(checked_paths) if checked_paths is not None else None

        visible = [p for p in paths if str(p) not in self._removed]

        self.list.blockSignals(True)
        self.list.clear()

        for path in visible:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))

            if default_checked is not None:
                checked = str(path) in default_checked
            else:
                # 首次全选；刷新时保留之前的勾选状态
                checked = str(path) in previous if previous else True

            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)

        self.list.blockSignals(False)
        self.selection_changed.emit()

    def clear(self):
        """清空列表（素材失效时调用），已删除记录一并重置。"""
        self._removed.clear()
        self._last_paths = []
        self.list.blockSignals(True)
        self.list.clear()
        self.list.blockSignals(False)
        self.selection_changed.emit()

    def remove_selected(self):
        """把勾选的素材移出本框架（不删除磁盘文件），刷新后仍保持删除。"""
        checked = self.selected_paths()
        if not checked:
            return
        self._removed.update(checked)
        self.populate(self._last_paths)

    def reset_removed(self):
        """清空删除记录，使被移除的素材重新可显示。"""
        self._removed.clear()

    def all_paths(self):
        result = []
        for i in range(self.list.count()):
            result.append(self.list.item(i).data(Qt.UserRole))
        return result

    def selected_paths(self):
        result = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.UserRole))
        return result

    def selected_count(self):
        return len(self.selected_paths())

    def set_all(self, state):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)
        self.list.blockSignals(False)
        self.selection_changed.emit()

    def invert(self):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            new_state = (Qt.CheckState.Unchecked
                         if item.checkState() == Qt.CheckState.Checked
                         else Qt.CheckState.Checked)
            item.setCheckState(new_state)
        self.list.blockSignals(False)
        self.selection_changed.emit()
