# -*- coding: utf-8 -*-
"""判废明细弹窗：告诉用户「这一段为什么被废」。

设计原则
--------
参考 VideoHighlighter 的报告思路：**不是给结论，而是给算术。**

    片段 3 · 12.0-17.0s · 废
    原因：画质差帧过多(4/6)

    判定条件
      差帧占比   >= 50%   实际 67%    ✗ 命中
      整体质量分 <  25     实际 42     ✓ 未命中

    采样帧明细
      12.4s  模糊  8.2 < 22.0   ✗
      13.2s  —     正常          ✓
      ...

只显示**参与判定的维度**，未启用的维度不出现（避免用户以为它们起作用了）。
每个差值都标出「差多少」，用户据此就能判断该调哪一档。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from mixcut.verdict_config import DIMENSIONS, LEVELS

# 维度 key -> 用哪个实测值、判定方向
#   "<" 表示实测值小于阈值算命中（模糊/黑场/偏暗/发灰/空镜/灰片）
#   ">" 表示实测值大于阈值算命中（白场/偏亮/噪点/色偏/阴影/手抖）
METRIC_OF = {
    "blur": ("sharpness", "<"),
    "dark": ("brightness", "<"),
    "bright": ("brightness", ">"),
    "underexposed": ("brightness", "<"),
    "overexposed": ("brightness", ">"),
    "noise": ("noise", ">"),
    "shake": ("motion", ">"),
    "flat": ("contrast", "<"),
    "empty": ("edge_density", "<"),
    "gray": ("saturation", "<"),
    "cast": ("color_cast", ">"),
    "shadow": ("dark_ratio", ">"),
}

# 实测值的显示单位（百分比类要带 %）
UNIT_OF = {
    "edge_density": "%",
    "dark_ratio": "%",
}

# 判定条件命中的颜色
COLOR_HIT = "#E06C75"      # 红色：命中（这一帧被判差）
COLOR_OK = "#7A828C"       # 灰色：未命中
COLOR_TEXT = "#E8EAED"
COLOR_MUTED = "#9AA0A6"
COLOR_FAINT = "#5F6368"


def _fmt(value, metric=None):
    """数值格式化：百分比类带 %，其余保留合适精度。"""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if metric in UNIT_OF:
        return "%.1f%%" % v
    if abs(v) >= 100:
        return "%.0f" % v
    return "%.1f" % v


class VerdictDetailDialog(QDialog):
    """一个片段的判废明细。"""

    def __init__(self, item, config=None, parent=None, advices=None):
        super().__init__(parent)
        self._item = item or {}
        self._cfg = config
        self._advices = list(advices or [])

        name = self._item.get("name") or "片段"
        start = float(self._item.get("seg_start") or 0.0)
        end = float(self._item.get("seg_end") or 0.0)
        is_bad = bool(self._item.get("is_bad"))

        self.setWindowTitle("判废明细")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self.setStyleSheet("QDialog{background:#16181C;}")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(10)

        # ---- 标题行 ----
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("%s · %.1f-%.1fs" % (name, start, end))
        title.setStyleSheet("color:%s;font-size:15px;font-weight:700;"
                            % COLOR_TEXT)
        head.addWidget(title)
        head.addStretch(1)

        badge = QLabel("废" if is_bad else "正常")
        badge.setStyleSheet(
            "QLabel{color:%s;font-size:12px;font-weight:700;"
            "border:1px solid %s;border-radius:4px;padding:2px 10px;}"
            % (("#E06C75", "#E06C75") if is_bad else ("#7DBE7D", "#7D3E3E")))
        head.addWidget(badge)
        root.addLayout(head)

        # ---- C：可信度警示 ----
        # 「测到 0」和「没测」必须分开说，否则用户会把缺数据读成没问题。
        integ = self._item.get("integrity")
        if not isinstance(integ, dict) or not integ.get("state"):
            # 早期结果没有 integrity 字段：按「有没有逐帧明细」推断
            from mixcut.verdict_integrity import of_item
            integ = of_item(self._item).to_dict()
        if integ.get("state") not in (None, "complete"):
            hint = integ.get("hint") or ""
            if hint:
                warn = QLabel("⚠  " + hint)
                warn.setWordWrap(True)
                warn.setStyleSheet(
                    "QLabel{color:#E7B06B;font-size:12px;"
                    "background:#2A2418;border:none;border-radius:5px;"
                    "padding:7px 10px;}")
                root.addWidget(warn)

        # ---- 判废原因 ----
        reason = (self._item.get("bad_reason") or "").strip()
        if reason:
            rl = QLabel(reason)
            rl.setWordWrap(True)
            rl.setStyleSheet("color:#E06C75;font-size:12px;")
            root.addWidget(rl)
        elif self._item.get("minor_issues"):
            rl = QLabel("未判废，但有少量差帧：%s"
                        % self._item["minor_issues"])
            rl.setWordWrap(True)
            rl.setStyleSheet("color:%s;font-size:12px;" % COLOR_MUTED)
            root.addWidget(rl)

        # ---- 判定条件 ----
        limits = self._item.get("verdict_limits") or {}
        conds = limits.get("conditions") or []
        if conds:
            root.addWidget(self._section_title("判定条件"))
            box = QFrame()
            box.setStyleSheet(
                "QFrame{background:#1C1E22;border:none;border-radius:5px;}")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(12, 8, 12, 8)
            bl.setSpacing(4)
            for c in conds:
                bl.addWidget(self._condition_row(c))
            root.addWidget(box)

        # ---- 采样帧明细 ----
        frames = self._item.get("frames_detail") or []
        root.addWidget(self._section_title(
            "采样帧明细 · 共 %d 帧" % len(frames) if frames
            else "采样帧明细"))
        if not frames:
            tip = QLabel("没有明细数据。可能是本轮分析早于该功能上线，"
                         "重新分析一次即可看到。")
            tip.setWordWrap(True)
            tip.setStyleSheet("color:%s;font-size:12px;" % COLOR_MUTED)
            root.addWidget(tip)
        else:
            root.addWidget(self._build_frames(frames), 1)

        # ---- 建议（B：可执行）----
        # 只有当调用方传进来建议时才显示；建议本身带 settings，
        # 用户可以点「应用」直接改配置并重新判定。
        if self._advices:
            root.addWidget(self._section_title("可以试试"))
            for adv in self._advices:
                root.addWidget(self._advice_row(adv))

        # ---- 底部 ----
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        close = btns.button(QDialogButtonBox.Close)
        close.setText("关闭")
        close.setStyleSheet(
            "QPushButton{background:transparent;color:#C8CCD2;"
            "border:1px solid #35383D;border-radius:5px;padding:7px 18px;}"
            "QPushButton:hover{background:#2A2D31;}")
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _advice_row(self, adv):
        """一条建议：文字 + 救回多少段 + 「应用」按钮。

        「应用」只是把配置改掉并关窗，真正的重新判定由调用方做
        （这里不持有卡片/结果列表，不做状态修改）。
        """
        row = QFrame()
        row.setStyleSheet(
            "QFrame{background:#1C1E22;border:none;border-radius:5px;}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 7, 12, 7)
        lay.setSpacing(10)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        t1 = QLabel(getattr(adv, "text", str(adv)))
        t1.setStyleSheet("color:%s;font-size:12px;" % COLOR_TEXT)
        texts.addWidget(t1)
        detail = getattr(adv, "detail", "") or ""
        if detail:
            t2 = QLabel(detail)
            t2.setWordWrap(True)
            t2.setStyleSheet("color:%s;font-size:11px;" % COLOR_FAINT)
            texts.addWidget(t2)
        lay.addLayout(texts, 1)

        changes = int(getattr(adv, "changes", 0) or 0)
        if changes > 0:
            badge = QLabel("救回 %d 段" % changes)
            badge.setStyleSheet(
                "QLabel{color:#7DBE7D;font-size:11px;"
                "border:1px solid #2F4A2F;border-radius:4px;"
                "padding:1px 8px;}")
            lay.addWidget(badge)

        apply_btn = QPushButton("应用")
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setStyleSheet(
            "QPushButton{background:#2A2D31;color:#E8EAED;"
            "border:1px solid #35383D;border-radius:4px;"
            "padding:3px 12px;font-size:11px;}"
            "QPushButton:hover{background:#35383D;}")
        apply_btn.clicked.connect(lambda _=False, a=adv: self._on_apply(a))
        lay.addWidget(apply_btn)
        return row

    def _on_apply(self, adv):
        from mixcut.verdict_advice import apply_advice
        if self._cfg is not None:
            self._applied = apply_advice(self._cfg, adv)
        else:
            self._applied = None
        self._applied_advice = adv
        self.accept()

    def applied_config(self):
        """用户点了「应用」时返回新配置，否则 None。"""
        return getattr(self, "_applied", None)

    # ---- 构件 ----

    def _section_title(self, text):
        lb = QLabel(text)
        lb.setStyleSheet("color:%s;font-size:12px;font-weight:600;"
                         "padding-top:6px;" % COLOR_MUTED)
        return lb

    def _condition_row(self, cond):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        label = QLabel(cond.get("label") or "")
        label.setFixedWidth(88)
        label.setStyleSheet("color:%s;font-size:12px;" % COLOR_TEXT)
        lay.addWidget(label)

        # 差帧占比是 0~1 的比例，显示成百分比更直观
        is_ratio = "占比" in (cond.get("label") or "")

        def _show(v):
            if v is None:
                return "—"
            if is_ratio:
                try:
                    return "%.0f%%" % (float(v) * 100)
                except (TypeError, ValueError):
                    return str(v)
            return _fmt(v)

        expr = QLabel("%s %s" % (cond.get("op") or "", _show(cond.get("limit"))))
        expr.setFixedWidth(76)
        expr.setStyleSheet("color:%s;font-size:12px;" % COLOR_MUTED)
        lay.addWidget(expr)

        actual = QLabel("实际 %s" % _show(cond.get("actual")))
        actual.setStyleSheet("color:%s;font-size:12px;" % COLOR_TEXT)
        lay.addWidget(actual)
        lay.addStretch(1)

        hit = bool(cond.get("hit"))
        state = QLabel("命中" if hit else "未命中")
        state.setStyleSheet(
            "QLabel{color:%s;font-size:11px;font-weight:%s;}"
            % (COLOR_HIT if hit else COLOR_FAINT,
               "700" if hit else "400"))
        lay.addWidget(state)
        return row

    def _build_frames(self, frames):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")

        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(3)

        # 表头
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 2, 10, 2)
        hl.setSpacing(8)
        for text, width in (("时间", 60), ("判定", 56), ("说明", 0),
                            ("实测", 150)):
            lb = QLabel(text)
            if width:
                lb.setFixedWidth(width)
            lb.setStyleSheet("color:%s;font-size:10px;" % COLOR_FAINT)
            hl.addWidget(lb, 1 if not width else 0)
        lay.addWidget(head)

        for fr in frames:
            lay.addWidget(self._frame_row(fr))
        lay.addStretch(1)

        scroll.setWidget(holder)
        return scroll

    def _frame_row(self, fr):
        bad = bool(fr.get("is_bad"))
        row = QFrame()
        row.setStyleSheet(
            "QFrame{background:%s;border:none;border-radius:4px;}"
            % ("#2A1E20" if bad else "#1C1E22"))
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)

        t = QLabel("%.2fs" % float(fr.get("time") or 0.0))
        t.setFixedWidth(60)
        t.setStyleSheet("color:%s;font-size:11px;" % COLOR_MUTED)
        lay.addWidget(t)

        verdict = QLabel("✗" if bad else "✓")
        verdict.setFixedWidth(56)
        verdict.setStyleSheet(
            "color:%s;font-size:11px;font-weight:700;"
            % (COLOR_HIT if bad else "#6A9955"))
        lay.addWidget(verdict)

        reason = QLabel(fr.get("reason") or "正常")
        reason.setStyleSheet("color:%s;font-size:11px;"
                            % (COLOR_TEXT if bad else COLOR_FAINT))
        lay.addWidget(reason, 1)

        detail = QLabel(self._measured_text(fr))
        detail.setStyleSheet("color:%s;font-size:11px;" % COLOR_MUTED)
        lay.addWidget(detail)
        return row

    def _measured_text(self, fr):
        """把「命中的维度：实测 vs 阈值」拼成一行。

        只显示**这次真的命中**的维度，其它不列，避免一行太长。
        """
        measured = fr.get("measured") or {}
        limits = fr.get("limits") or {}
        reason = (fr.get("reason") or "")

        parts = []
        for key, (metric, op) in METRIC_OF.items():
            if key not in limits:
                continue
            # 只解释 reason 里出现过的维度
            label = DIMENSIONS[key]["label"]
            if label not in reason:
                continue
            actual = measured.get(metric)
            limit = limits.get(key)
            if actual is None or limit is None:
                continue
            parts.append("%s %s %s %s" % (
                label, _fmt(actual, metric), op, _fmt(limit, metric)))

        if parts:
            return "  ·  ".join(parts)
        if not fr.get("is_bad"):
            return "—"
        return ""
