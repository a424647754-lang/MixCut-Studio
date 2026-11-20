# -*- coding: utf-8 -*-
"""判废建议：从一次分析的结果里推出「改哪个设置能救回这些片段」。

设计参考 VideoHighlighter 的 advice_actions：建议**不是一句文字，
而是一份可以直接套用的设置改动**（``{"settings": {...}}``），
并且必须给出「按当前数据改了会怎样」的数字，而不是让用户自己去试。

这里只做**计算**，不碰 Qt、不碰 I/O：
给定一批片段的明细 + 当前规则，返回若干条建议。

判定口径必须与 ``analyze_page._assemble`` 完全一致：
    is_bad = (差帧占比 >= bad_ratio) or (整体质量分 < min_score)
其中「差帧」= 该帧上至少有一个**已启用**的维度命中。
"""
from dataclasses import dataclass, field

from mixcut.verdict_config import (
    DESCENDING_DIMENSIONS,
    DIMENSIONS,
    LEVEL_COUNT,
    LEVELS,
    VerdictConfig,
    level_to_threshold,
)

# 维度 key -> (实测字段, 判定方向)；与 verdict_detail.METRIC_OF 保持一致
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


@dataclass
class Advice:
    """一条建议。

    Attributes:
        kind: 类别（level / disable / ratio / score）
        text: 给用户看的一句话
        settings: 可直接套用的改动（字段语义见应用方）
        changes: 按当前数据，有多少片段会因为这条建议改变判定
        detail: 补充说明
    """

    kind: str
    text: str
    settings: dict = field(default_factory=dict)
    changes: int = 0
    detail: str = ""

    def to_dict(self):
        return {"kind": self.kind, "text": self.text,
                "settings": dict(self.settings), "changes": self.changes,
                "detail": self.detail}


# ---------------------------------------------------------------
# 核心：在给定配置下重新判定一个片段
# ---------------------------------------------------------------
def frame_hits(frame, cfg):
    """这一帧在 cfg 下是否算「差」，以及命中了哪些维度。

    只用已启用维度；未启用的维度即使数值越界也不算命中——
    这正是用户点开「为什么」时最容易困惑的地方，必须与 worker 口径一致。
    """
    measured = frame.get("measured") or {}
    hits = []
    for key, (metric, op) in METRIC_OF.items():
        if not cfg.is_on(key):
            continue
        val = measured.get(metric)
        if val is None:
            continue
        limit = cfg.threshold(key)
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        hit = (v < limit) if op == "<" else (v > limit)
        if hit:
            hits.append(key)
    return bool(hits), hits


def judge(item, cfg, score=None):
    """按 cfg 重新判定一个片段，返回 (is_bad, ratio, bad_count, total)。

    Args:
        item: 片段 dict（含 frames_detail）
        cfg: VerdictConfig
        score: 覆盖整体质量分；None 用 item 里记录的值
    """
    frames = item.get("frames_detail") or []
    total = len(frames)
    if not total:
        # 没有明细时退回已记录的结论（无法重算）
        return bool(item.get("is_bad")), float(item.get("bad_frame_ratio") or 0), 0, 0

    bad = sum(1 for f in frames if frame_hits(f, cfg)[0])
    ratio = bad / total
    if score is None:
        score = _recorded_score(item)
    is_bad = (ratio >= cfg.bad_ratio) or (score < cfg.min_score)
    return is_bad, ratio, bad, total


def _recorded_score(item):
    vl = item.get("verdict_limits") or {}
    val = vl.get("measured_score")
    if val is not None:
        try:
            return float(val)
        except (TypeError, ValueError):
            pass
    try:
        return float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------
# 建议生成
# ---------------------------------------------------------------
def _clone(cfg):
    return VerdictConfig.from_dict(cfg.to_dict())


def suggest(items, cfg, limit=4):
    """对一批片段给出建议，按「能救回多少片段」排序。

    Args:
        items: 片段 dict 列表（含 frames_detail）
        cfg: 当前 VerdictConfig
        limit: 最多返回几条

    Returns:
        (advices, summary)。summary 是整体情况的文字说明（可能为空）。
    """
    items = [it for it in (items or []) if it]
    if not items:
        return [], ""
    if not any(it.get("frames_detail") for it in items):
        return [], "这批结果没有帧明细数据，重新分析一次才能给出建议。"

    current_bad = sum(1 for it in items if it.get("is_bad"))
    advices = []

    # --- 1) 每个启用维度：逐级降档，取第一个真正能救回片段的档位 ---
    # 只降一档常常不够（阈值跨度小，救不回任何片段），
    # 所以逐档往下试，报第一个有效的那一档——报出来的就是「至少得调到这」。
    for key in cfg.enabled_keys():
        lv = cfg.level(key)
        if lv <= 0:
            continue  # 已经是最宽松
        found = None
        for target in range(lv - 1, -1, -1):
            trial = _clone(cfg)
            trial.levels[key] = target
            changes = sum(
                1 for it in items
                if it.get("is_bad") and not judge(it, trial)[0])
            if changes > 0:
                found = (target, changes)
                break
        if not found:
            continue
        target, changes = found
        cur_th = cfg.threshold(key)
        new_th = level_to_threshold(key, target)
        op = METRIC_OF[key][1]
        advices.append(Advice(
            kind="level",
            text="把「%s」从「%s」调到「%s」" % (
                DIMENSIONS[key]["label"], LEVELS[lv], LEVELS[target]),
            settings={"level": {key: target}},
            changes=changes,
            detail="阈值 %s → %s（判定 %s），按当前数据可救回 %d 段"
                   % (_fmt_th(cur_th, key), _fmt_th(new_th, key), op, changes),
        ))

    # --- 2) 整段判定：放宽差帧占比 ---
    cur_rl = cfg.bad_ratio_level()
    if cur_rl > 0:
        for target in range(cur_rl - 1, -1, -1):
            trial = _clone(cfg)
            trial.set_bad_ratio_level(target)
            changes = sum(
                1 for it in items
                if it.get("is_bad") and not judge(it, trial)[0])
            if changes > 0:
                advices.append(Advice(
                    kind="ratio",
                    text="放宽「差帧占比」到 %d%%" % _RATIO_PCT[target],
                    settings={"bad_ratio_level": target},
                    changes=changes,
                    detail="当前要求 %d%% 才算废，放宽后按现有数据可救回 %d 段"
                           % (_RATIO_PCT[cur_rl], changes),
                ))
                break

    # --- 3) 整段判定：降低质量分门槛 ---
    if cfg.min_score > 0:
        trial = _clone(cfg)
        trial.min_score = max(0.0, cfg.min_score - 10.0)
        changes = sum(
            1 for it in items if it.get("is_bad") and not judge(it, trial)[0])
        if changes > 0:
            advices.append(Advice(
                kind="score",
                text="把「整体质量分」门槛从 %.0f 降到 %.0f"
                     % (cfg.min_score, trial.min_score),
                settings={"min_score": trial.min_score},
                changes=changes,
                detail="按现有数据可救回 %d 段" % changes,
            ))

    advices.sort(key=lambda a: -a.changes)
    advices = advices[:limit]

    summary = "当前 %d 段里有 %d 段被判废。" % (len(items), current_bad)
    if not advices:
        summary += "没有哪一项单独调整能救回废片段——问题可能是素材本身。"
    return advices, summary


_RATIO_PCT = {0: 80, 1: 65, 2: 50, 3: 35, 4: 20}


def _fmt_th(value, key):
    """阈值显示：百分比类维度带 %。"""
    if key in ("empty", "gray", "shadow"):
        return "%.1f%%" % float(value)
    return "%.1f" % float(value)


def apply_advice(cfg, advice):
    """把一条建议套到配置上，返回新配置（不改原对象）。"""
    new = _clone(cfg)
    s = advice.settings if isinstance(advice, Advice) else (advice or {})
    for key, lv in (s.get("level") or {}).items():
        if key in DIMENSIONS:
            new.levels[key] = max(0, min(LEVEL_COUNT - 1, int(lv)))
    if "bad_ratio_level" in s:
        new.set_bad_ratio_level(int(s["bad_ratio_level"]))
    if "min_score" in s:
        new.min_score = float(s["min_score"])
    return new


def merge_advices(cfg, advices):
    """把多条建议合并成一份改动。

    参考 VideoHighlighter：同一个设置被多条建议改成不同值时，
    **更宽松的那个胜出**（用户一次点了多条，显然想要更宽松），
    并把冲突报出来，让调用方决定是否提示。
    """
    merged = {}
    conflicts = []
    for adv in advices or []:
        s = adv.settings if isinstance(adv, Advice) else (adv or {})
        for key, lv in (s.get("level") or {}).items():
            if key in merged.get("level", {}) and merged["level"][key] != lv:
                lo = min(merged["level"][key], lv)
                conflicts.append({"setting": key,
                                  "values": [merged["level"][key], lv],
                                  "chosen": lo})
                merged["level"][key] = lo
            else:
                merged.setdefault("level", {})[key] = lv
        if "bad_ratio_level" in s:
            cur = merged.get("bad_ratio_level")
            # 档位越低越宽松
            merged["bad_ratio_level"] = (
                min(cur, s["bad_ratio_level"]) if cur is not None
                else s["bad_ratio_level"])
        if "min_score" in s:
            cur = merged.get("min_score")
            merged["min_score"] = (
                min(cur, s["min_score"]) if cur is not None
                else s["min_score"])
    return apply_advice(cfg, merged), conflicts
