# -*- coding: utf-8 -*-
"""
MixCut · 引擎能力页

把 Super Director 移植过来的三项能力集中显示：
  ① 整句切割（切点对齐语音句尾）
  ② 指纹查重（跳过重复素材）
  ③ CUDA 引擎桥（外置 sd_asr / sd_dedup 进程）

这一页只读状态与说明，开关本体仍在「超级混剪」的参数里。
"""

from pathlib import Path
import threading

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QPushButton, QMessageBox,
)

from mixcut import gpu_engine as gpue
from mixcut import openclip_embed as oce

from .components import SectionCard, LabeledField, StatusBadge, make_button


CAPABILITIES = [
    ("① 整句切割（切点对齐语音句尾）",
     "对有人声的素材先做语音转写，拿到整句时间轴，再把混剪算出的切点吸附到最近的句尾；"
     "只在最短/最长片段允许的区间里寻找，不会为了整句把节奏改乱。\n"
     "实测：同素材同种子，切点离句尾的最大偏差从 1.37 秒降到 0.11 秒。"),
    ("② 指纹查重（跳过重复素材）",
     "用 CUDA 引擎为每条素材提指纹（关键帧 phash + 色彩直方图），在本地比对，"
     "综合相似度 ≥ 0.92 判为重复；同组只保留时长最长的一条，其余跳过并在日志里点名。\n"
     "实测：同一文件副本 1.000，不同素材 0.000；时长比超过 1.4 的两条直接判为不同片子。"),
    ("③ CUDA 引擎桥（进程内 sd_fp.dll + 外置 sd_asr.exe）",
     "MixCut 用的是 pip 版 OpenCV，cv2.cuda 恒为 0 个设备，Python 侧拿不到 GPU 算子，"
     "所以 GPU 计算交给随包的引擎：指纹提取由 sd_fp.dll 在 MixCut 进程内直接调用"
     "（CUDA 13.5 + OpenCV CUDA 编译），不再为每条素材起一次 sd_dedup.exe；"
     "语音转写仍由 sd_asr.exe 完成。DLL 缺失或 CUDA 初始化失败时自动回退到外置"
     "sd_dedup.exe，当前实际后端显示在下方状态区。"),
]


class EnginePage(QWidget):
    """引擎状态与能力说明。"""

    clip_status_ready = Signal(dict)

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._clip_busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel("引擎能力")
        title.setObjectName("Hero")
        top.addWidget(title)
        self.state_badge = StatusBadge("检查中", "idle")
        top.addWidget(self.state_badge)
        top.addStretch(1)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # ---- 运行状态 ----
        status = SectionCard("运行状态", "引擎、模型与缓存的实况")
        self.status_label = QLabel("尚未检查")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_label.setStyleSheet(
            "font-family:Consolas;font-size:12px;line-height:170%;")
        status.add_row(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        refresh_btn = make_button("刷新状态", kind="ghost")
        refresh_btn.clicked.connect(self.refresh)
        doc_btn = make_button("打开说明页", kind="ghost")
        doc_btn.clicked.connect(self.open_doc)
        cache_btn = make_button("打开缓存目录", kind="ghost")
        cache_btn.clicked.connect(self.open_cache)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(doc_btn)
        btn_row.addWidget(cache_btn)
        btn_row.addStretch(1)
        status.add_layout(btn_row)

        # 开关回显（只读，改动去「超级混剪」）
        self.switch_label = QLabel("")
        self.switch_label.setObjectName("Faint")
        self.switch_label.setWordWrap(True)
        status.add_row(self.switch_label)
        col.addWidget(status)

        # ---- OpenCLIP（独立环境，GPU 优先）----
        clip = SectionCard("OpenCLIP 嵌入", "ViT-L-14 · 独立 venv · 768 维")
        clip_head = QHBoxLayout()
        clip_head.setSpacing(8)
        self.clip_badge = StatusBadge("未探测", "idle")
        clip_head.addWidget(self.clip_badge)
        clip_head.addStretch(1)
        clip.add_layout(clip_head)

        self.clip_label = QLabel("尚未探测。首次加载约 5–10 秒，点「刷新状态」开始。")
        self.clip_label.setWordWrap(True)
        self.clip_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.clip_label.setStyleSheet(
            "font-family:Consolas;font-size:12px;line-height:170%;")
        clip.add_row(self.clip_label)

        clip_btns = QHBoxLayout()
        clip_btns.setSpacing(8)
        probe_btn = make_button("刷新状态", kind="ghost")
        probe_btn.clicked.connect(lambda: self.probe_clip(force=False))
        reload_btn = make_button("重新加载模型", kind="ghost")
        reload_btn.clicked.connect(lambda: self.probe_clip(force=True))
        clip_btns.addWidget(probe_btn)
        clip_btns.addWidget(reload_btn)
        clip_btns.addStretch(1)
        clip.add_layout(clip_btns)

        note = QLabel(
            "用途：文字检索素材、画面风格相似度。不做内容语义判断，也未接入混剪筛选逻辑。")
        note.setWordWrap(True)
        note.setObjectName("Faint")
        clip.add_row(note)
        col.addWidget(clip)
        self.clip_status_ready.connect(self._apply_clip_status)

        # ---- 三项能力 ----
        for cap_title, cap_text in CAPABILITIES:
            card = SectionCard(cap_title, collapsible=False)
            body = QLabel(cap_text)
            body.setWordWrap(True)
            body.setObjectName("Muted")
            card.add_row(body)
            col.addWidget(card)

        col.addStretch(1)
        self.refresh()

    # ---- 数据 ----
    def _status_text(self):
        try:
            info = gpue.describe()
        except Exception as exc:
            return f"自检失败：{type(exc).__name__}: {exc}", "error"

        exes = info.get("exe", {}) or {}
        ok = bool(info.get("available"))
        lines = [f"引擎目录：{info.get('dir') or '未找到（流程会降级，照常出片）'}"]
        for name in ("sd_asr.exe", "sd_dedup.exe"):
            lines.append(f"{name}：{'在位' if exes.get(name) else '缺失'}")
        backend = info.get("fp_backend") or ""
        backend_text = {
            "inproc": "进程内 sd_fp.dll",
            "exe": "外置 sd_dedup.exe",
        }.get(backend, "不可用")
        lines.append(
            f"sd_fp.dll：{'在位' if info.get('dll') else '缺失'}"
            f"（当前指纹后端：{backend_text}）")

        engine_bin = Path(info["dir"]) if info.get("dir") else None
        model = (engine_bin.parent / "models" / "whisper-ggml" / "ggml-small.bin"
                 if engine_bin else None)
        lines.append(
            f"whisper 模型：{'在位' if model and model.exists() else '缺失'}（ggml-small.bin）")

        for kind, label, pattern in (
            ("asr", "转写缓存", "*.srt"),
            ("fp", "指纹缓存", "*.json"),
        ):
            try:
                root = gpue.cache_dir(kind)
                count = len(list(root.glob(pattern)))
            except OSError:
                root, count = None, 0
            lines.append(f"{label}：{count} 个（{root}）")
        return "\n".join(lines), ("success" if ok else "error")

    def refresh(self):
        text, level = self._status_text()
        self.status_label.setText(text)
        self.status_badge_text = text
        self.state_badge.set_size(level)
        self.state_badge.setText("引擎在位" if level == "success" else "引擎缺失")

        win = self.win
        snap = bool(getattr(win, "mix_sentence_snap", None)
                    and win.mix_sentence_snap.isChecked())
        dedup = bool(getattr(win, "mix_audio_dedup", None)
                     and win.mix_audio_dedup.isChecked())
        self.switch_label.setText(
            f"当前混剪开关：整句切割 {'开' if snap else '关'} · "
            f"指纹查重 {'开' if dedup else '关'}（在两个开关的原始位置「超级混剪 → AI 增强」）")

    def on_shown(self):
        self.refresh()
        if not self._clip_busy and self.clip_badge.text() == "未探测":
            self.probe_clip()

    # ---- OpenCLIP 状态 ----
    def probe_clip(self, force=False):
        """后台探测 OpenCLIP（主环境不 import torch，探测走独立 venv）。"""
        if self._clip_busy:
            return
        self._clip_busy = True
        self.clip_badge.setText("探测中")
        self.clip_badge.set_size("running")
        self.clip_label.setText("正在探测 OpenCLIP：首次加载模型约 5–10 秒…")

        def work():
            try:
                info = oce.load_model(force=force)
            except Exception as exc:
                info = {"available": False,
                        "reason": f"{type(exc).__name__}: {exc}"}
            self.clip_status_ready.emit(info)

        threading.Thread(target=work, daemon=True).start()

    def _apply_clip_status(self, info):
        self._clip_busy = False
        if not info.get("available"):
            self.clip_badge.setText("不可用")
            self.clip_badge.set_size("error")
            self.clip_label.setText(
                "独立环境未就绪：" + str(info.get("reason", "未知原因")) + "\n"
                "venv：" + str(info.get("venv_python") or "未找到"))
            return

        is_cuda = bool(info.get("cuda"))
        self.clip_badge.setText("GPU 可用" if is_cuda else "CPU 可用")
        self.clip_badge.set_size("success" if is_cuda else "warning")

        dev_line = f"设备：{info.get('device')}"
        if info.get("gpu"):
            dev_line += f" · {info.get('gpu')}"
        ckpt = Path(info.get("ckpt", "")).name or "open_clip_pytorch_model.bin"

        self.clip_label.setText("\n".join([
            "运行状态：可用（独立 venv 子进程，主环境不导入 torch）",
            f"模型加载：完成 · {info.get('model_ms')} ms",
            dev_line,
            f"torch：{info.get('torch')}（独立环境版本）",
            f"架构：{info.get('arch')} · 维度 {info.get('dim')}",
            f"权重：{ckpt} · {info.get('ckpt_mb')} MB",
            f"向量缓存：{info.get('cache_count')} 条（%LOCALAPPDATA%\\MixCut\\cache\\clip）",
            f"venv：{info.get('venv_python')}",
        ]))

    # ---- 动作 ----
    def open_doc(self):
        page = Path(__file__).resolve().parents[2] / "功能说明" / "新增三项能力.html"
        if page.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(page)))
        else:
            QMessageBox.information(self, "说明页缺失", f"未找到说明页：\n{page}")

    def open_cache(self):
        try:
            root = gpue.cache_dir("asr").parent
        except OSError as exc:
            QMessageBox.information(self, "打不开缓存目录", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))
