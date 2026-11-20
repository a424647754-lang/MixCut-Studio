# -*- coding: utf-8 -*-
"""分析结果的「可信度标注」：区分「测过、结果是 0」与「压根没测」。

为什么需要
----------
VideoHighlighter 的 analysis_plan.py 记录了一个反复出现的 bug 模式：

    一个设置了（a）决定某个检测器跑不跑、（b）又不在缓存键里。
    于是：权重设 0 → 跳过检测 → 缓存一个空列表；
          用户调高权重 → 同名缓存命中 → 读到空列表；
          **永远重新检测不到**，而且完全静默——
          因为「空列表」是个合法答案。

我们项目里有同类问题，只是形态不同：

    1. 判废规则变了，但缓存还是旧规则算的（已用规则指纹修掉）
    2. 某些维度**从未被计算过**（比如旧版本没有 edge_density），
       明细里显示 0，用户以为「测了，是 0」，其实是「没有这个数」
    3. 某个片段只有部分帧有缓存（中途取消过），
       判废是基于不完整的样本做的，但界面看不出来

本模块不修这些，只**如实标注**每份结果的可信度，让界面能说清：
「这段是真测过」，还是「这段的结论基于不完整的数据」。

设计口径
--------
- **宁可说「不知道」，也不要说「0」**。
- 标注是纯函数：给定片段/缓存，返回一个状态，不碰 I/O 和 Qt。
"""
from dataclasses import dataclass

# 一个片段的分析完整性
COMPLETE = "complete"          # 所有采样帧都有结果，且维度齐全
PARTIAL = "partial"            # 有结果，但采样帧不全（如中途取消）
STALE = "stale"                # 结果是用旧规则算的（规则指纹不匹配）
LEGACY = "legacy"              # 结果早于明细功能，没有逐帧数据
MISSING = "missing"            # 完全没分析过

STATE_TEXT = {
    COMPLETE: "",
    PARTIAL: "数据不完整",
    STALE: "规则已变更",
    LEGACY: "旧版结果",
    MISSING: "未分析",
}

STATE_HINT = {
    COMPLETE: "",
    PARTIAL: "本次只分析了一部分帧，结论可能不准。重新分析可补全。",
    STALE: "判废规则改过，这份结果是旧规则算的。重新分析才会用新规则。",
    LEGACY: "这份结果生成于「判废明细」功能上线之前，没有逐帧数据。"
            "重新分析即可看到明细。",
    MISSING: "这个片段还没有分析结果。",
}


@dataclass
class Integrity:
    """一份分析结果的可信度。

    Attributes:
        state: COMPLETE / PARTIAL / STALE / LEGACY / MISSING
        have: 实际有结果的采样帧数
        want: 应有的采样帧数
    """

    state: str = COMPLETE
    have: int = 0
    want: int = 0

    @property
    def ok(self):
        return self.state == COMPLETE

    def text(self):
        """给界面用的一句短语（完整时不显示）。"""
        base = STATE_TEXT.get(self.state, "")
        if self.state == PARTIAL and self.want:
            return "%s %d/%d 帧" % (base, self.have, self.want)
        return base

    def hint(self):
        return STATE_HINT.get(self.state, "")

    def to_dict(self):
        return {"state": self.state, "have": self.have, "want": self.want,
                "text": self.text(), "hint": self.hint()}


def of_item(item, expected_frames=None, current_signature=None):
    """判断一个片段结果的可信度。

    Args:
        item: 片段 dict
        expected_frames: 该片段应有的采样帧数；None 表示不检查完整性
        current_signature: 当前判废规则指纹；用于识别「旧规则结果」
    """
    item = item or {}
    frames = item.get("frames_detail") or []

    # 1) 完全没有结果
    if not item:
        return Integrity(MISSING)

    # 2) 有结果但没有逐帧明细 → 早于明细功能
    if not frames:
        if item.get("is_bad") is None and item.get("score") is None:
            return Integrity(MISSING)
        return Integrity(LEGACY)

    # 3) 明细帧数不全
    want = int(expected_frames or 0)
    have = len(frames)
    if want and have < want:
        return Integrity(PARTIAL, have, want)

    # 4) 规则指纹不匹配（结果是用旧规则算的）
    recorded = item.get("analyzed_signature")
    if current_signature is not None and recorded is not None \
            and recorded != current_signature:
        return Integrity(STALE, have, want)

    return Integrity(COMPLETE, have, want or have)


def describe_counts(items):
    """整批结果的完整性统计，用于状态栏/日志一行说明。"""
    counts = {COMPLETE: 0, PARTIAL: 0, STALE: 0, LEGACY: 0, MISSING: 0}
    for it in items or []:
        st = (it.get("integrity") or {}).get("state") if isinstance(
            it.get("integrity"), dict) else None
        if st in counts:
            counts[st] += 1
        else:
            counts[COMPLETE] += 1
    parts = []
    if counts[PARTIAL]:
        parts.append("%d 段数据不完整" % counts[PARTIAL])
    if counts[STALE]:
        parts.append("%d 段规则已变更" % counts[STALE])
    if counts[LEGACY]:
        parts.append("%d 段是旧版结果" % counts[LEGACY])
    return " · ".join(parts)
