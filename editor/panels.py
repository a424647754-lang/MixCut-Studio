"""工作区专属面板：混剪 / 调色 / 导出。

第一阶段只做了工作区骨架（按钮只切布局），这里把三个工作区各自补成
真正能操作的面板：
  - 混剪：按节奏从素材库自动铺满时间线（可撤销）
  - 调色：亮度/对比度/饱和度/色温，实时近似预览 + 写入片段属性
  - 导出：输出规格、预估体积、开始导出（与"导出视频"对话框共用后端）
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import Project
from . import ui_theme as T


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    return label


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


class _ToolPanel(QWidget):
    """统一样式的工作区面板容器。"""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PanelBody")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(12, 12, 12, 12)
        self._outer.setSpacing(8)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("PanelTitle")
        self._outer.addWidget(self.title_label)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{T.BORDER};")
        self._outer.addWidget(line)

    def _add(self, widget) -> None:
        self._outer.addWidget(widget)

    def _stretch(self) -> None:
        self._outer.addStretch(1)


# ---------------------------------------------------------------------------
# 混剪
# ---------------------------------------------------------------------------
class MixPanel(_ToolPanel):
    """一键混剪：把素材按固定节奏铺满指定总时长。"""

    mix_requested = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__("混剪设置", parent)

        self._add(_muted("从媒体库按节奏自动铺满整条 V1 轨道，生成后可继续逐段微调。"))

        form = QFormLayout()
        form.setSpacing(8)

        self.total_spin = QSpinBox()
        self.total_spin.setRange(1, 3600)
        self.total_spin.setValue(30)
        self.total_spin.setSuffix(" 秒")
        form.addRow("总时长", self.total_spin)

        self.segment_spin = QDoubleSpinBox()
        self.segment_spin.setRange(0.2, 60.0)
        self.segment_spin.setSingleStep(0.5)
        self.segment_spin.setDecimals(1)
        self.segment_spin.setValue(3.0)
        self.segment_spin.setSuffix(" 秒")
        form.addRow("单段时长", self.segment_spin)

        self.order_combo = QComboBox()
        self.order_combo.addItems(["顺序使用", "随机挑选"])
        form.addRow("素材顺序", self.order_combo)

        self.range_combo = QComboBox()
        self.range_combo.addItems(["全部素材", "仅媒体库选中"])
        form.addRow("素材范围", self.range_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["覆盖整条 V1", "追加到 V1 末尾"])
        form.addRow("生成方式", self.mode_combo)

        self._outer.addLayout(form)

        self.info_label = _muted("等待素材…")
        self._add(self.info_label)

        self.btn_mix = QPushButton("生成混剪")
        self.btn_mix.setObjectName("Primary")
        self.btn_mix.setMinimumHeight(32)
        self.btn_mix.clicked.connect(self._emit)
        self._add(self.btn_mix)

        self._add(_muted("提示：生成的片段都是独立片段，可直接在时间线上修剪、"
                         "换序或替换；整段操作可 Ctrl+Z 撤销。"))
        self._stretch()

    def set_available(self, count: int, selected: int) -> None:
        self.info_label.setText(f"可用素材 {count} 个（已选中 {selected} 个）")
        self.btn_mix.setEnabled(count > 0)

    def _emit(self) -> None:
        self.mix_requested.emit({
            "total_ms": int(self.total_spin.value() * 1000),
            "segment_ms": int(self.segment_spin.value() * 1000),
            "order": "random" if self.order_combo.currentIndex() == 1 else "sequence",
            "selected_only": self.range_combo.currentIndex() == 1,
            "append": self.mode_combo.currentIndex() == 1,
        })


export_estimate_note = "按码率粗估，仅作参考"


def estimate_size_mb(width: int, height: int, fps: float, duration_ms: int,
                     crf: int, prores: bool = False) -> float:
    """粗略估算输出体积（MB）。只用于界面提示，不参与任何决策。"""
    if duration_ms <= 0:
        return 0.0
    pixels = max(1, width * height)
    if prores:
        mbps = 120.0 * (pixels / (1920 * 1080)) * (fps / 30.0)
    else:
        quality = 2.0 ** ((23 - crf) / 6.0)
        mbps = 8.0 * (pixels / (1920 * 1080)) * (fps / 30.0) * quality
    return mbps * (duration_ms / 1000.0) / 8.0


class ExportPanel(_ToolPanel):
    """导出工作区：输出规格 + 预估体积 + 开始导出。"""

    export_requested = Signal(dict)

    RESOLUTIONS = [
        ("项目设置", (0, 0)),
        ("1920×1080", (1920, 1080)),
        ("1080×1920 竖屏", (1080, 1920)),
        ("1280×720", (1280, 720)),
        ("3840×2160", (3840, 2160)),
    ]

    def __init__(self, parent=None) -> None:
        super().__init__("导出设置", parent)
        self.project: Project | None = None
        self._auto_path = ""

        form = QFormLayout()
        form.setSpacing(8)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["H.264 (.mp4)", "ProRes 422 HQ (.mov)"])
        form.addRow("格式", self.format_combo)

        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(18)
        self.crf_spin.setToolTip("CRF：数字越小画质越高、文件越大（推荐 18-23）")
        form.addRow("画质 CRF", self.crf_spin)

        self.res_combo = QComboBox()
        for label, _size in self.RESOLUTIONS:
            self.res_combo.addItem(label)
        form.addRow("分辨率", self.res_combo)

        self.audio_combo = QComboBox()
        self.audio_combo.addItems(["包含音频", "不要音频"])
        form.addRow("音频", self.audio_combo)

        self._outer.addLayout(form)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("输出文件路径")
        browse = QPushButton("选择…")
        browse.clicked.connect(self._choose)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        self._outer.addLayout(path_row)

        self.summary = _muted("时间线为空")
        self._add(self.summary)

        self.btn_export = QPushButton("开始导出")
        self.btn_export.setObjectName("Primary")
        self.btn_export.setMinimumHeight(32)
        self.btn_export.clicked.connect(lambda: self.export_requested.emit(self.options()))
        self._add(self.btn_export)

        self._add(_muted("导出在后台线程执行，可随时取消；时长与分辨率取自时间线与上方设置。"))
        self._stretch()

        self.format_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self.res_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self.crf_spin.valueChanged.connect(lambda _v: self.refresh())

    # ------------------------------------------------------------------
    def set_project(self, project: Project) -> None:
        previous_auto = self._auto_path
        self.project = project
        root = Path(__file__).resolve().parent.parent
        exports = root / "exports"
        name = project.name if project.name else "export"
        suffix = ".mov" if self.format_combo.currentIndex() == 1 else ".mp4"
        auto_path = str(exports / f"{name}{suffix}")
        current = self.path_edit.text().strip()
        # 只有当用户还没手动改过路径（或路径是上一个工程的默认值）时才自动改写
        if auto_path and (not current or current == previous_auto):
            self.path_edit.setText(auto_path)
            self._auto_path = auto_path
        self.path_edit.setCursorPosition(0)
        self.refresh()

    def refresh(self) -> None:
        if self.project is None:
            return
        duration = self.project.duration()
        if duration <= 0:
            self.summary.setText("时间线为空，先导入素材并把片段放到时间线上。")
            self.btn_export.setEnabled(False)
            return
        width, height = self._resolution()
        fps = float(self.project.fps_fraction)
        size = estimate_size_mb(width, height, fps, duration,
                                self.crf_spin.value(),
                                self.format_combo.currentIndex() == 1)
        self.summary.setText(
            f"时长 {duration / 1000:.1f} 秒 · {width}×{height} · {fps:.2f} fps\n"
            f"预估体积约 {size:.1f} MB（{export_estimate_note}）")
        self.btn_export.setEnabled(True)

    def _resolution(self) -> tuple[int, int]:
        index = self.res_combo.currentIndex()
        width, height = self.RESOLUTIONS[max(0, index)][1]
        if width == 0 and self.project is not None:
            return self.project.width, self.project.height
        return width or 1920, height or 1080

    def _choose(self) -> None:
        prores = self.format_combo.currentIndex() == 1
        suffix = ".mov" if prores else ".mp4"
        root = Path(__file__).resolve().parent.parent / "exports"
        default = self.path_edit.text() or str(root / f"export{suffix}")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出到", default,
            "QuickTime (*.mov)" if prores else "MP4 视频 (*.mp4)")
        if path:
            self.path_edit.setText(path)

    def options(self) -> dict:
        prores = self.format_combo.currentIndex() == 1
        width, height = self._resolution()
        path = self.path_edit.text().strip()
        if not path:
            root = Path(__file__).resolve().parent.parent / "exports"
            name = (self.project.name if self.project else "export")
            path = str(root / f"{name}{'.mov' if prores else '.mp4'}")
        if prores and not path.lower().endswith(".mov"):
            path = str(Path(path).with_suffix(".mov"))
        if not prores and not path.lower().endswith(".mp4"):
            path = str(Path(path).with_suffix(".mp4"))
        self.path_edit.setText(path)
        return {
            "output": path,
            "prores": prores,
            "crf": self.crf_spin.value(),
            "width": width,
            "height": height,
            "audio": self.audio_combo.currentIndex() == 0,
        }
