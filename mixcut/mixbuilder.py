"""
MixCut · 智能混剪时间线装配

在“废镜头已剔除的有效段”内取片，拼成目标时长的混剪时间线：
  - 素材使用统计 + 相邻重复控制（严格 / 适中 / 宽松）
  - 片段时长策略（均衡 / 长短交替 / 快速 / 舒缓）
  - 多版本差异化（均衡 / 快节奏 / 长短交替 / 多样性 / 音乐优先）
  - BGM 卡点：强弱拍识别、每 1/2/4 拍切换、自动回退
  - 素材标签衔接评分 + 画面质量排序（不推断画面语义）
  - 支持随机排列 / 顺序循环、素材重复使用、单段最短最长
  - 不重复使用素材时不会提前结束：同一素材里没被用过的区间会继续消耗
本模块只负责智能混剪的片段抽取与时间线装配。
"""

import random
from pathlib import Path

from . import analysis as analysis_core
from . import coverage as cov
from . import sentence_cut as scut
from . import timeline as tl
from . import visual_diversity as vd


DEFAULT_MIX_OPTIONS = {
    "target_duration": 30.0,
    "duration_tolerance": 2.0,  # 成片落在 目标±浮动 即停，不硬凑精确秒数
    "clip_min": 2.0,
    "clip_max": 5.0,
    "shuffle": True,
    "reuse": True,
    # 整句切割：切点吸附到语音句尾（需先做语音转写，见 workers._gpu_enrich）
    "sentence_snap": False,
    "beats": None,              # 卡点时传入鼓点时间列表
    "beat_tolerance": 0.12,
    "tags_by_source": None,     # {source_id: [标签...]}，来自素材虚拟分类
    # 相邻重复控制强度：strict 尽量不留相邻重复，medium 适度，loose 只做轻微约束
    "diversity": "medium",
    # 片段时长策略：balance 均衡 / dynamic 长短交替 / fast 快速切换 / slow 舒缓
    "rhythm": "balance",
    # 多版本策略：均衡 / 快节奏 / 长短交替 / 多样性优先 / 音乐节奏优先
    "version_style": "balanced",
    # 卡点密度：每 1 / 2 / 4 拍切一次；0 表示自动（按 BPM 选）
    "beat_step": 0,
    # 强拍（小节重拍）时间点；给出时优先在强拍处切换
    "downbeats": None,
    # 允许在弱拍补切的概率（0~1），避免每个鼓点都机械切换
    "weak_beat_chance": 0.25,
    # 画面质量排序权重：只影响候选优先级，不硬删除低分片段。
    "quality_weight": 2.5,
    # OpenCLIP 语义多样性：只调整候选优先级，不作为硬性过滤条件。
    "openclip_enabled": True,
    "openclip_weight": 2.0,
    # ---- 时间覆盖（Coverage，2026-11-20 第二十一阶段）----
    # 0 = 关闭（行为与旧版完全一致）；1 = 强制每条素材的时间轴被均匀使用。
    # 防止「前 2 分钟画质好 → 成片几乎全来自前 2 分钟」。
    "coverage": 0.0,
    "coverage_weight": 3.0,
    # ---- 视觉多样性（2026-11-20 第二十一阶段）----
    # 用亮度/对比度/边缘/饱和度/运动五项传统特征判断画面是否雷同。
    # 只看 source_id 不够：三个不同文件可以都是「人脸近景」，连排照样单调。
    "visual_diversity": 0.0,      # 0 = 关闭；建议 1.5~3.0
    "visual_similar_threshold": 0.92,
}


# ---------------------------------------------------------
# 阶段 3：多版本差异化策略
# ---------------------------------------------------------
# 每种版本给出一组确定的算法倾向（不是换个 seed）：
#   diversity / rhythm 覆盖重复控制与时长策略；seed_offset 让批量版本保持差异。
VERSION_STYLES = {
    "balanced": {
        "label": "均衡混剪",
        "diversity": "medium",
        "rhythm": "balance",
        "seed_offset": 0,
    },
    "fast": {
        "label": "快节奏混剪",
        "diversity": "medium",
        "rhythm": "fast",
        "seed_offset": 1013,
    },
    "alternate": {
        "label": "长短交替",
        "diversity": "medium",
        "rhythm": "dynamic",
        "seed_offset": 2027,
    },
    "diverse": {
        "label": "多样性优先",
        "diversity": "strict",
        "rhythm": "balance",
        "seed_offset": 3041,
    },
    "music": {
        "label": "音乐节奏优先",
        "diversity": "medium",
        "rhythm": "balance",
        "seed_offset": 4057,
    },
}
VERSION_STYLE_KEYS = tuple(VERSION_STYLES)

# 批量生成时，多个版本按这个顺序轮换（同一批素材出不同风格）
_VERSION_ROTATION = ("balanced", "fast", "diverse", "alternate", "music")


def resolve_version_options(base_options, index, count):
    """按“第几个工程 / 共几个”把版本策略写进 mix_options。

    返回新字典，不修改传入对象。count<=1 或未指定策略时保持原样。
    """
    opt = dict(base_options or {})
    style = str(opt.get("version_style") or "balanced").lower()
    if style not in VERSION_STYLES:
        style = "balanced"
    if count <= 1:
        preset = VERSION_STYLES[style]
        opt.setdefault("diversity", preset["diversity"])
        opt.setdefault("rhythm", preset["rhythm"])
        return opt, style

    chosen = _VERSION_ROTATION[index % len(_VERSION_ROTATION)]
    preset = VERSION_STYLES[chosen]
    opt["diversity"] = preset["diversity"]
    opt["rhythm"] = preset["rhythm"]
    opt["version_style"] = chosen
    return opt, chosen


# ---------------------------------------------------------
# 阶段 2：动态片段时长分配
# ---------------------------------------------------------
# 每个策略给一段“长度权重曲线”：x∈[0,1] 是 (clip_min..clip_max) 的位置，
# 返回该位置被选中的相对权重。权重只决定倾向，最终长度仍夹在上下限内。
_RHYTHM_PROFILES = {
    # 均衡：钟形，集中在中间长度，几乎不出现极端长短
    "balance": lambda x: 0.02 + (1.0 - abs(2.0 * x - 1.0)) ** 4.0 * 30.0,
    # 动态：两端重、中间轻，形成长短交替的起伏
    "dynamic": lambda x: 0.1 + abs(2.0 * x - 1.0) ** 2.5 * 10.0,
    # 快速：强烈偏向短片段
    "fast": lambda x: 0.02 + (1.0 - x) ** 4.0 * 30.0,
    # 舒缓：强烈偏向长片段
    "slow": lambda x: 0.02 + x ** 4.0 * 30.0,
    # 脉冲：极端两端，短长对比更强烈
    "pulse": lambda x: 0.05 + abs(2.0 * x - 1.0) ** 1.5 * 20.0,
    # 断奏：几乎全是极短片段，节奏密集
    "staccato": lambda x: 0.02 + (1.0 - x) ** 6.0 * 40.0,
    # 电影感：偏长镜头，偶尔插入短切
    "cinematic": lambda x: 0.05 + x ** 2.5 * 25.0,
}
# 长短交替时，两个方向之间至少拉开的比例（相对 clip_max-clip_min）
_ALTERNATE_SPREAD = 0.6
# 强拍优先时跳过整个小节的最大代价（秒）：超过它就不等强拍了
_STRONG_WAIT_MAX = 0.6


def _length_weight(mode, x):
    fn = _RHYTHM_PROFILES.get(mode, _RHYTHM_PROFILES["balance"])
    return max(0.0, float(fn(max(0.0, min(1.0, x)))))


def _pick_weighted_length(lower, upper, mode, rnd):
    """在 [lower, upper] 内按策略权重取一个长度（分段线性采样）。"""
    if upper - lower <= 1e-6:
        return lower
    # 把区间切成若干小格，按格子中点的权重做加权抽样
    n = 12
    ticks = [i / n for i in range(n + 1)]
    weights = []
    total = 0.0
    for i in range(n):
        mid = (ticks[i] + ticks[i + 1]) / 2.0
        w = _length_weight(mode, mid)
        weights.append(w)
        total += w
    if total <= 0:
        return rnd.uniform(lower, upper)
    r = rnd.random() * total
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if r <= acc:
            lo_t = lower + (upper - lower) * ticks[i]
            hi_t = lower + (upper - lower) * ticks[i + 1]
            return rnd.uniform(lo_t, hi_t)
    return rnd.uniform(lower, upper)


def _rhythm_length(lower, upper, mode, state, rnd):
    """按节奏策略取片段长度；长短交替会参考上一段的相对长短。"""
    span = upper - lower
    if span <= 1e-6:
        return lower
    if mode == "dynamic":
        # 交替：上一段偏短 → 这一段偏长，反之亦然
        direction = state.get("alt_dir", 1)
        prev = state.get("last_rel")
        lo, hi = (0.55, 1.0) if direction > 0 else (0.0, 0.45)
        rel = rnd.uniform(lo, hi)
        if prev is not None and abs(rel - prev) < _ALTERNATE_SPREAD:
            # 兜底：至少拉开固定差距，避免“视觉上还是差不多长”
            rel = (prev + _ALTERNATE_SPREAD if direction > 0
                   else prev - _ALTERNATE_SPREAD)
            rel = max(0.0, min(1.0, rel))
        length = lower + span * rel
        state["alt_dir"] = -direction
        state["last_rel"] = rel
        return length
    state["last_rel"] = (state.get("last_rel") or 0.0)
    return _pick_weighted_length(lower, upper, mode, rnd)


# ---------------------------------------------------------
# 阶段 1：素材使用统计 + 相邻重复控制
# ---------------------------------------------------------
# 重复控制强度的惩罚系数
# 说明：“分散”与“均衡”是两股力量——_USAGE_REFERENCE=3 时“用过优先”会压过
# “同源要隔开”，所以这里把 recent（同源刚用过扣分）按强度拉开，
# 让宽松策略真的更愿意紧挨着用同一个素材。
_REPEAT_WEIGHTS = {
    "strict": {"same_label": 4.0, "same_source": 8.0, "recent": 3.0, "usage": 1.6},
    "medium": {"same_label": 0.8, "same_source": 1.5, "recent": 1.2, "usage": 1.0},
    "loose": {"same_label": 0.0, "same_source": 0.0, "recent": 0.0, "usage": 0.5},
}
# 允许的重复控制强度（界面下拉框也用这份列表）
DIVERSITY_LEVELS = tuple(_REPEAT_WEIGHTS)

# 期望使用次数：用于把“使用次数均衡”归一化到可比较的分数
_USAGE_REFERENCE = 3
# 时间线位置离散度的归一化参照（秒）
_TIME_SPREAD_REF = 30.0

# 最后一段允许比“最短片段”短多少：只有收尾时才会用到，避免出现无意义的极短片段
END_SLACK = 0.25
# 片段长度绝对下限：低于它的片段一律不生成（防止循环取片无法推进）
MIN_CLIP_FLOOR = 0.05
# 每次取片的安全上限：超时说明参数或素材有问题，直接报错而不是死循环
MAX_CLIP_GUARD = 200000
# 浮点比较容差
_EPS = 1e-6


# ---------------------------------------------------------
# 阶段 4：强弱拍与节奏密度
# ---------------------------------------------------------

def _beat_grid(beats, downbeats, step):
    """把鼓点整理成“可用于切点的网格”。

    返回 (强拍列表, 弱拍列表)。强拍是每 step 拍的位置（第 1 拍起算），
    其余拍点作为弱拍，只按概率补切。
    """
    strong = []
    weak = []
    total = len(beats)
    for i in range(total):
        b = round(float(beats[i]), 3)
        if i % step == 0:
            strong.append(b)
        else:
            weak.append(b)
    return strong, weak


def _pick_beat_cut(candidates, t_now, low, high):
    """从给定拍点列表中取 [low, high] 内最早的可用切点。"""
    usable = [c - t_now for c in candidates if low <= c - t_now <= high]
    return usable[0] if usable else None


def _sanity_check_beats(beats, duration=None):
    """检查鼓点是否合理；不合理就当作“没有鼓点”，回退普通混剪。"""
    try:
        arr = [float(b) for b in (beats or [])]
    except Exception:
        return []
    arr = sorted(b for b in arr if b >= 0)
    if len(arr) < 4:
        return []
    gaps = [b - a for a, b in zip(arr, arr[1:]) if b > a]
    if not gaps:
        return []
    avg = sum(gaps) / len(gaps)
    if avg < 0.2 or avg > 5.0:
        return []
    # 间隔方差过大（明显乱跳）时不可信
    var = sum((g - avg) ** 2 for g in gaps) / len(gaps)
    if var ** 0.5 > avg * 0.7:
        return []
    if duration and duration > 0 and arr[-1] > duration * 1.5:
        return []
    return arr


def _source_tags(source_id, tags_map):
    """取某个来源的标签集合（用户没给标签时返回空集合，不虚构）。"""
    return {str(t) for t in (tags_map.get(source_id) or []) if str(t)}


def _decision_state():
    """新建一份取片决策状态：使用统计只用于本次装配，不写进时间线。"""
    return {
        "source_clips": {},   # source_id -> 已用片段数
        "source_sec": {},     # source_id -> 已用秒数
        "range_clips": {},    # range_idx -> 该区间已用片段数
        "last_source": None,  # 上一片段的来源
        "last_tags": set(),   # 上一片段的标签
        "last_time": {},      # source_id -> 上一次出现在时间线的时刻
        "clip_recent": [],    # 最近若干片段的 OpenCLIP 语义向量
        "visual_recent": [],  # 最近若干片段的传统视觉指纹（第二十一阶段）
        "coverage": None,     # CoverageTracker 或 None（关闭时不创建）
    }


def _visual_fingerprint_of(rng):
    """取一个区间的视觉指纹；没有可用数据时返回 None。

    指纹来自分析阶段已经算好的质量指标（亮度/对比度/边缘/饱和度/运动），
    不需要额外解码。缺数据时返回 None，调用方跳过这项评分——
    **不因为缺数据就把片段判死**。
    """
    fp = rng.get("visual_fp")
    if fp is not None:
        return fp if vd.usable(fp) else None
    raw = rng.get("visual_metrics")
    if not raw:
        return None
    fp = vd.fingerprint(raw)
    return fp if vd.usable(fp) else None


def _candidate_score(idx, ranges, state, t_now, tags_map, level, weights):
    """给一个候选区间打分；返回 None 表示该候选被当前强度排除。

    评分全部来自已有数据（来源 ID、用户标签、使用次数、时间线位置），
    不推断任何画面语义。
    """
    rng = ranges[idx]
    sid = rng["source_id"]
    tags = _source_tags(sid, tags_map)

    clip_count = state["source_clips"].get(sid, 0)
    used_sec = state["source_sec"].get(sid, 0.0)
    range_count = state["range_clips"].get(idx, 0)

    # 1) 次数均衡：用过的越少分越高（片段数与秒数双重归一）
    count_norm = clip_count / _USAGE_REFERENCE
    sec_norm = used_sec / max(
        1e-6, _USAGE_REFERENCE * max(rng["end"] - rng["start"], 1.0)
    )
    score = -(count_norm + sec_norm) * weights["usage"]

    # 2) 相邻控制：同一来源 / 同一标签紧邻。
    #    严格模式的“不许同源相邻”由 _choose_candidate 先做硬过滤；
    #    这里只在严格模式下对同标签再额外重罚。
    same_source = (state["last_source"] is not None
                   and sid == state["last_source"])
    same_label = bool(tags & state["last_tags"])
    if same_source:
        score -= weights["same_source"]
    if same_label:
        score -= weights["same_label"]

    # 3) 分布均衡：同源上一次出现得越近，扣分越多
    last_seen = state["last_time"].get(sid)
    if last_seen is not None:
        gap = max(0.0, float(t_now) - last_seen)
        score -= weights["recent"] * max(0.0, 1.0 - gap / _TIME_SPREAD_REF)

    # 4) 区间新鲜度：同一区间已经切过，尽量换一个区间
    if range_count:
        score -= 1.0 * min(range_count, 3)

    # 5) 画面质量：质量分越高越优先；未能评估的片段使用中性分 70。
    #    低分只降低入选概率，不直接淘汰，避免素材量不足时无法成片。
    quality = max(0.0, min(100.0, float(rng.get("quality_score", 70.0))))
    quality_weight = float(weights.get("quality", 0.0))
    score += ((quality - 50.0) / 50.0) * quality_weight
    if rng.get("quality_bad"):
        score -= quality_weight * 0.35

    # 6) OpenCLIP 语义新鲜度：倾向选择与最近镜头画面语义不同的片段。
    #    仅作软评分；缺向量/模型不可用时完全回退旧算法，不淘汰任何片段。
    vector = rng.get("clip_embedding")
    recent = state.get("clip_recent") or []
    clip_weight = max(0.0, float(weights.get("openclip", 0.0)))
    if vector and recent and clip_weight > 0:
        similarities = []
        for previous in recent:
            if not previous or len(previous) != len(vector):
                continue
            similarities.append(sum(float(a) * float(b)
                                    for a, b in zip(vector, previous)))
        if similarities:
            max_similarity = max(-1.0, min(1.0, max(similarities)))
            novelty = (1.0 - max_similarity) * 0.5
            score += novelty * clip_weight

    # 7) 顺序模式下的“就近复用”：宽松强度下，刚用过的素材上一段若有剩余
    #    就直接接着用（切点仍按素材游标连续推进），用来拉开与严格/适中策略的区别。
    if (level == "loose" and state.get("ordered")
            and state["last_source"] == sid and range_count):
        score += 2.0

    # 8) 时间覆盖（Coverage）：防止成片过度集中在素材的某一小段。
    #    完全由 CoverageTracker 决定；coverage=0 时它未启用、恒返回 0，
    #    行为与改造前一致。
    #    传入 end 使跨桶片段按实际覆盖范围分摊惩罚（第二十二阶段）。
    tracker = state.get("coverage")
    if tracker is not None:
        score += tracker.penalty(sid, rng["start"], rng["end"])

    # 9) 视觉多样性：与最近几个镜头「画面雷同」时扣分。
    #    只看 source_id 不够——三个不同文件可以都是人脸近景，连排照样单调。
    #    visual_diversity=0 时直接跳过，无额外开销。
    visual_weight = max(0.0, float(weights.get("visual", 0.0)))
    if visual_weight > 0:
        recent_vis = state.get("visual_recent") or []
        vec = _visual_fingerprint_of(rng)
        if vec is not None and recent_vis:
            score += vd.novelty_penalty(
                vec, recent_vis,
                threshold=float(weights.get("visual_threshold", 0.92)),
                weight=visual_weight)

    return score


# ---------------------------------------------------------
# 标签衔接评分
# ---------------------------------------------------------
# 只使用素材来源 ID 和用户标签；画面质量由独立质量分负责，不推断语义。

def _clip_length_score(length, clip_min, clip_max):
    """片段长度合理性：太短或太长的小扣分，中间段最好的形状。"""
    span = max(1e-6, clip_max - clip_min)
    x = (length - clip_min) / span
    # 中间偏长的镜头更容易作为“主镜”承接节奏
    return 1.0 - abs(min(1.0, max(0.0, x)) - 0.6) * 2.0


def _transition_score(candidate_sid, candidate_tags, prev_sid, prev_tags,
                      blacklist=None):
    """衔接评分：来源差异 + 标签差异 + 黑名单规避。

    返回 0~1 的分数；只是“更愿意接哪一段”的倾向，不强制所有镜头都不同。
    """
    score = 0.5
    if prev_sid is None:
        return score
    if candidate_sid != prev_sid:
        score += 0.25
    if prev_tags and candidate_tags:
        overlap = len(set(candidate_tags) & set(prev_tags))
        score += 0.25 * (0.0 if overlap else 1.0)
    if blacklist and (set(candidate_tags) & set(blacklist)):
        score -= 0.2
    return max(0.0, min(1.0, score))


def _choose_candidate(indices, ranges, state, t_now, tags_map, level, rnd,
                      clip_min=2.0, clip_max=5.0, blacklist=None,
                      length_hint=None, link_weight=0.15, quality_weight=2.5):
    """在候选区间里按综合评分选一个；并列时随机挑，保留随机性。

    综合分 = 素材均衡/相邻重复 + 标签衔接 + 画面质量倾向。
    画面质量只作为排序项，不进行语义推断或硬性废片淘汰。
    link_weight 不宜过大：阶段 1 的重复控制是主规则，衔接评分只做微调。

    唯一强规则：严格模式下，只要候选里还有“与上一段不同来源”的区间，
    就不允许再选同一来源，避免同源镜头连续出现。
    """
    weights = dict(_REPEAT_WEIGHTS[level])
    weights["quality"] = max(0.0, float(quality_weight))
    weights["openclip"] = max(0.0, float(
        # 由调用方注入到候选状态的全局设置；缺省时保持兼容。
        state.get("openclip_weight", 0.0)))
    # 视觉多样性权重与阈值（第二十一阶段）：0 表示关闭，不产生额外开销
    weights["visual"] = max(0.0, float(
        state.get("visual_diversity", 0.0) or 0.0))
    weights["visual_threshold"] = float(
        state.get("visual_threshold", 0.92) or 0.92)
    prev_tags = state.get("last_tags") or set()
    prev_sid = state.get("last_source")

    # 严格模式：先过滤掉“同源紧邻”的候选（前提是还有别的来源可选）
    if level == "strict" and prev_sid is not None and len(indices) > 1:
        others = [i for i in indices if ranges[i]["source_id"] != prev_sid]
        if others:
            indices = others

    # 同标签惩罚只在“存在标签不同的候选”时生效，避免把不同标签的候选也
    # 一起压低后又被均衡/衔接分抵消；没有别的选择时仍可复用同标签素材。
    any_different_tag = any(
        not (_source_tags(ranges[i]["source_id"], tags_map) & prev_tags)
        for i in indices
    ) if prev_tags else False

    scored = []
    for idx in indices:
        score = _candidate_score(idx, ranges, state, t_now, tags_map,
                                 level, weights)
        if score is None:
            continue
        rng = ranges[idx]
        tags = _source_tags(rng["source_id"], tags_map)
        same_label = bool(tags & prev_tags)
        if same_label and any_different_tag and level in ("strict", "medium"):
            # 还有标签不同的候选可用：同标签的直接不给机会
            score -= weights["same_label"] + 2.0
        link = _transition_score(rng["source_id"], tags, prev_sid, prev_tags,
                                 blacklist)
        if length_hint is not None and length_hint > 0:
            link += _clip_length_score(length_hint, clip_min, clip_max) * 0.3
        score += link * link_weight
        scored.append((float(score), idx))
    if not scored:
        return None

    # 不再“永远取最高分”：从最高分附近的一小撮候选里按分加权随机。
    # 这样同一片段池、不同 seed 会得到不同的镜头组合；质量差距明显时，
    # 低分候选仍会被窗口挡在外面，不会为了随机性牺牲画面。
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score = scored[0][0]
    window = 1.5
    top = [item for item in scored if item[0] >= best_score - window]
    top = top[:max(6, min(12, len(scored) // 3 or 1))]
    if len(top) <= 1:
        return top[0][1]
    # 窗口内按相对分加权：最高分权重最大，但其它高分候选仍有机会。
    weights = []
    for score, _idx in top:
        rel = (score - (best_score - window)) / window
        weights.append(max(0.02, rel))
    return top[rnd.choices(range(len(top)), weights=weights, k=1)[0]][1]



def _record_decision(state, idx, rng, tags, t_now, seconds):
    sid = rng["source_id"]
    vector = rng.get("clip_embedding")
    if vector:
        state.setdefault("clip_recent", []).append(vector)
        state["clip_recent"] = state["clip_recent"][-5:]
    # 传统视觉指纹同样只留最近几条：多样性只看「紧邻的镜头像不像」
    vis = _visual_fingerprint_of(rng)
    if vis is not None:
        state.setdefault("visual_recent", []).append(vis)
        state["visual_recent"] = state["visual_recent"][-5:]
    # 覆盖记账：按「素材 + 起点」累加秒数，供后续候选算惩罚
    tracker = state.get("coverage")
    if tracker is not None:
        tracker.record(sid, rng["start"], seconds)
    state["source_clips"][sid] = state["source_clips"].get(sid, 0) + 1
    state["source_sec"][sid] = state["source_sec"].get(sid, 0.0) + float(seconds)
    state["last_source"] = sid
    state["last_tags"] = set(tags)
    state["last_time"][sid] = float(t_now)
    state["range_clips"][idx] = state["range_clips"].get(idx, 0) + 1


def _refill_cursors(ranges, state):
    """reuse=True 且所有区间用完时，重置游标并清空区间计数。"""
    for idx, rng in enumerate(ranges):
        rng["cursor"] = rng["start"]
    state["range_clips"] = {}



def _visual_metrics_of(frag):
    """从分析页片段里抽出视觉指纹所需的五项指标。

    分析页的片段结构（``_assemble`` 产出）里，统计量放在
    ``metrics`` / ``frames_avg`` 等位置，这里做兼容式读取：
    找不到就返回 None，绝不猜测性填值（填默认值会让所有片段指纹相同，
    反而把「多样性」判成一模一样）。
    """
    if not isinstance(frag, dict):
        return None
    for key in ("metrics", "quality_metrics", "frames_avg", "avg_metrics"):
        m = frag.get(key)
        if isinstance(m, dict) and m:
            picked = {k: m.get(k) for k in
                      ("brightness", "contrast", "edge_density",
                       "saturation", "motion")
                      if m.get(k) is not None}
            if picked:
                return picked
    # 退化：片段自身直接带这几个键
    picked = {k: frag.get(k) for k in
              ("brightness", "contrast", "edge_density", "saturation",
               "motion") if frag.get(k) is not None}
    return picked or None


def _ranges_from_fragments(fragments, project, min_piece):
    """把分析页片段转成混剪区间池（片段级唯一来源）。

    只接受 ``included=True`` 的片段：人工排除在这里生效，混剪无法绕过。
    路径、起止时间原样保留；越界或缺失直接报错，不做猜测性修正。
    """
    ranges = []
    short_ranges = 0
    excluded = 0
    source_ids = {}
    durations = {}

    for frag in fragments:
        path = str(frag.get("path") or "")
        if not path:
            raise RuntimeError("分析页片段缺少原始文件路径，无法建立混剪池。")
        if not bool(frag.get("included", True)):
            excluded += 1
            continue

        start = float(frag.get("start", frag.get("seg_start", 0.0)))
        end = float(frag.get("end", frag.get("seg_end", 0.0)))
        if end <= start:
            raise RuntimeError(
                f"分析页片段起止时间无效：{Path(path).name} "
                f"{start:.3f}-{end:.3f} 秒")

        source_id = source_ids.get(path)
        if source_id is None:
            duration = float(frag.get("source_duration") or 0.0)
            if duration <= 0:
                duration = max(end, 0.0)
            source_id = len(source_ids) + 1
            source_ids[path] = source_id
            durations[path] = duration
            project["sources"].append(
                tl.make_source(source_id, path, duration))

        if end > durations[path] + 0.05:
            raise RuntimeError(
                f"片段超出素材时长：{Path(path).name} 片段 "
                f"{start:.3f}-{end:.3f} 秒，素材仅 {durations[path]:.2f} 秒")

        length = end - start
        if length <= MIN_CLIP_FLOOR:
            continue
        if length < min_piece:
            short_ranges += 1
        ranges.append({
            "frag_id": str(frag.get("frag_id") or ""),
            "source_id": source_id,
            "start": start,
            "end": end,
            "cursor": start,
            "quality_score": float(frag.get("score", 70.0)),
            "quality_bad": bool(frag.get("is_bad", False)),
            "quality_reason": str(frag.get("reason", "")),
            "quality_available": True,
            "clip_embedding": frag.get("_clip_embedding"),
            # 视觉指纹（第二十一阶段）：分析阶段已有的质量指标，
            # 用来判断「两个不同素材的画面是否雷同」。缺数据时为 None，
            # 评分时跳过该项，不会因为缺数据把片段判死。
            "visual_metrics": _visual_metrics_of(frag),
            # 语音整句时间轴（分析页本身不转写，保留接口位）
            "sentences": frag.get("_sentences") or [],
            "sentence_ends": frag.get("_sentence_ends") or [],
        })

    if not ranges:
        if excluded:
            raise RuntimeError(
                f"所选素材的片段全部被标记为“不加入”（{excluded} 段）。"
                "请在分析页把要用的片段加回，或勾选其他素材。")
        raise RuntimeError(
            "分析页没有可用的片段：请先在“分析”页完成素材分析，"
            "并确保至少一个片段被加入。")
    return ranges, short_ranges


def build_mix_timeline(paths, canvas, detect_options=None, mix_options=None,
                       progress_cb=None, cancel_check=None, analysis=None,
                       seed=None, fragment_pool=None):
    """装配一条混剪时间线。

    ``fragment_pool`` 非空时，片段池完全取自分析页结果，不再调用
    ``analyze_sources`` 重建另一套区间；``included=False`` 的片段不会进入池。
    未传池时保持原有行为（现场分析），供测试与旧调用方使用。

    ``reuse=False`` 时会持续消耗所有尚未取完的有效区间，不会只取每个区间
    一刀就提前结束；``reuse=True`` 才会在全部区间耗尽后从头开始循环。
    ``seed`` 用于让批量生成的每个工程拥有独立的随机排列。
    """
    detect_options = {**analysis_core.DEFAULT_OPTIONS, **(detect_options or {})}
    opt = {**DEFAULT_MIX_OPTIONS, **(mix_options or {})}

    target = float(opt["target_duration"])
    tolerance = float(opt.get("duration_tolerance", 0) or 0)
    clip_min = float(opt["clip_min"])
    clip_max = float(opt["clip_max"])
    if target <= 0:
        raise ValueError("目标时长必须大于 0 秒。")
    if tolerance < 0:
        raise ValueError("时长浮动不能小于 0 秒。")
    if clip_min <= 0 or clip_max <= 0 or clip_min > clip_max:
        raise ValueError("片段最短时长必须大于 0，且不能大于最长时长。")

    # 每次装配都从同一个起点开始随机：批量生成时各工程互不影响，
    # 也能按 seed 复现某一次的随机结果。
    rnd = random.Random(seed) if seed is not None else random.Random()

    project = tl.create_timeline(canvas=canvas, mode="mix")

    # 登记源 + 建立可用区间池。ranges 的索引是稳定 ID，用于跨轮次防止相邻重复。
    # 可用区间只要求“长度大于 0”，不再强制 0.3 秒下限：下限由用户的“最短片段”
    # 设置决定，极短的有效区间会在下面按 clip_min 单独统计后给出准确提示。
    ranges = []
    short_ranges = 0          # 短于一个可切长度的区间数
    min_piece = max(MIN_CLIP_FLOOR, min(clip_min, 1.0) * 0.5)

    if fragment_pool is not None:
        # 片段级唯一来源：直接用分析页确认的片段，不再现场重算区间。
        pool_ranges, short_ranges = _ranges_from_fragments(
            fragment_pool, project, min_piece)
        ranges.extend(pool_ranges)
        analysis = None
    else:
        if analysis is None:
            analysis = analysis_core.analyze_sources(
                paths, detect_options,
                progress_cb=progress_cb, cancel_check=cancel_check,
            )
        for item in analysis:
            source = item["source"]
            project["sources"].append(source)
            for vs, ve in analysis_core.valid_segments_for(item, detect_options):
                for piece_start, piece_end, quality_info in analysis_core.quality_pieces_for(
                        item, vs, ve):
                    length = float(piece_end) - float(piece_start)
                    if length <= MIN_CLIP_FLOOR:
                        continue
                    if length < min_piece:
                        short_ranges += 1
                    ranges.append({
                        "source_id": source["id"],
                        "start": float(piece_start),
                        "end": float(piece_end),
                        "cursor": float(piece_start),
                        "quality_score": float(quality_info.get("quality_score", 70.0)),
                        "quality_bad": bool(quality_info.get("is_bad", False)),
                        "quality_reason": str(quality_info.get("reason", "")),
                        "quality_available": bool(quality_info.get("quality_available", False)),
                        # 语音整句时间轴（开启"整句切割"并转写成功时才有）
                        "sentences": item.get("_sentences") or [],
                        "sentence_ends": item.get("_sentence_ends") or [],
                    })
            for junk in item.get("junk", []):
                project["junk"].append(junk)

    if not ranges:
        raise RuntimeError(
            "所选素材没有任何可用的画面区间：整段都被判定为黑屏/静止/静音。"
            "请在“废镜头检测”里放宽阈值或关掉对应检测后重试。"
        )
    if target - tolerance > sum(r["end"] - r["start"] for r in ranges) \
            and not opt["reuse"]:
        # 不重复使用素材时，素材总量不够就是不够，提前告诉用户，而不是生成一条短片。
        total_sec = sum(r["end"] - r["start"] for r in ranges)
        raise RuntimeError(
            f"可用素材合计约 {total_sec:.1f} 秒，低于目标时长下限 "
            f"{max(0.0, target - tolerance):.1f} 秒，且未允许重复使用素材。"
            "请勾选“允许素材重复使用”、增加素材，或降低目标时长。"
        )
    if len(ranges) == short_ranges:
        # 所有区间都太短：如实说明，并给出可操作的参数建议（不强制 0.3 秒）
        raise RuntimeError(
            f"可用画面区间都太短（最长 {max(r['end'] - r['start'] for r in ranges):.2f} 秒），"
            f"按当前“最短片段 {clip_min:.1f} 秒”切不出片段。"
            "请把最短片段调小（例如 0.5 秒），或换更完整的素材。"
        )

    tags_map = opt.get("tags_by_source") or {}
    beats = _sanity_check_beats(opt.get("beats"))
    downbeats = _sanity_check_beats(opt.get("downbeats"))
    beat_step = int(opt.get("beat_step") or 0)
    if beats and beat_step not in (1, 2, 4):
        # 自动：按平均拍间隔换算 BPM，快歌每拍切、慢歌每 2/4 拍切
        bpm = 60.0 / ((beats[-1] - beats[0]) / max(1, len(beats) - 1))
        beat_step = 1 if bpm >= 120 else (2 if bpm >= 90 else 4)
    if beat_step not in (1, 2, 4):
        beat_step = 2
    weak_chance = max(0.0, min(1.0, float(opt.get("weak_beat_chance", 0.25))))
    if downbeats:
        # 有强拍信息：强拍是小节重拍；弱拍是其余全部拍点
        strong_set = {round(b, 3) for b in downbeats}
        strong_grid = sorted(strong_set)
        weak_pool = sorted(b for b in beats if round(b, 3) not in strong_set)
    else:
        # 只有普通鼓点（_beat_grid 按 beat_step 划分）
        strong_grid, weak_pool = _beat_grid(beats, downbeats, beat_step)
    diversity = str(opt.get("diversity") or "medium").lower()
    if diversity not in _REPEAT_WEIGHTS:
        diversity = "medium"
    rhythm = str(opt.get("rhythm") or "balance").lower()
    if rhythm not in _RHYTHM_PROFILES:
        rhythm = "balance"
    # 黑名单标签只用于素材标签衔接评分，不作为画面内容识别。
    blacklist = list(opt.get("blacklist") or [])
    t_now = 0.0
    clip_id = 0
    # 素材使用统计 / 相邻重复控制状态
    state = _decision_state()
    state["openclip_weight"] = (
        max(0.0, float(opt.get("openclip_weight", 2.0)))
        if opt.get("openclip_enabled", True) else 0.0
    )
    # ---- 时间覆盖（Coverage）----
    # 只在 coverage>0 时创建：不启用时 tracker 为 None，
    # _candidate_score / _record_decision 都跳过，行为与改造前完全一致。
    cov_strength = max(0.0, min(1.0, float(opt.get("coverage", 0.0) or 0.0)))
    cov_weight = max(0.0, float(opt.get("coverage_weight", 3.0) or 0.0))
    if cov_strength > 0.0 and cov_weight > 0.0:
        durations = {s["id"]: float(s.get("duration") or 0.0)
                     for s in project["sources"]}
        clip_hint = max(1e-3, (float(opt.get("clip_min", 2.0))
                               + float(opt.get("clip_max", 5.0))) / 2.0)
        state["coverage"] = cov.CoverageTracker(
            durations,
            target_duration=float(opt.get("target_duration", 30.0) or 0.0),
            clip_hint=clip_hint,
            coverage=cov_strength,
            weight=cov_weight,
        )
    # ---- 视觉多样性 ----
    # 指纹来自分析阶段已有的质量指标，不需要额外解码。
    state["visual_diversity"] = max(
        0.0, float(opt.get("visual_diversity", 0.0) or 0.0))
    # 节奏状态（长短交替用）
    rhythm_state = {}
    # 统计连续“换了一个区间却没放下片段”的次数：只有剩余空间整体放不下
    # 最短片段时才结束，避免目标下限不合理时一直空转。
    idle_skips = 0

    # 取片的最小单位：只用来保证循环能推进，不再强制 0.3 秒。
    # 真正的长度下限由用户的“最短片段”决定。
    usable_floor = MIN_CLIP_FLOOR

    def available_indices():
        # 剩余长度连一个合法最短片段都切不出来时，这块区间按“已用完”处理。
        # 只按 usable_floor 判断会留下极短边角：候选永远选到它、又因短于 clip_min 被跳过，
        # idle_skips 攒到 len(ranges) 就直接结束混剪，导致“允许重复使用素材”在片段池较小时失效
        # （例如 5 个 2.8 秒片段、目标 30 秒，只出 12 秒就停）。
        floor = max(usable_floor, clip_min - 1e-6)
        return [
            idx for idx, rng in enumerate(ranges)
            if rng["end"] - rng["cursor"] >= floor
        ]

    def order_pool(indices):
        """生成当前可用的候选池。

        shuffle=True：先按使用次数打乱，再交给评分挑（评分优先、打破平局随机）；
        shuffle=False：保持导入顺序，评分只在顺序约束内做修正。
        """
        if opt["shuffle"]:
            pool = list(indices)
            rnd.shuffle(pool)
            return pool
        return list(indices)

    # 不同的素材来源数量：严格模式下只剩一个来源时不能强行排除同源
    source_ids = {rng["source_id"] for rng in ranges}
    state["same_source_count"] = len(source_ids)
    # 顺序模式标记（宽松强度的“就近复用”只在顺序模式下启用）
    state["ordered"] = not opt["shuffle"]

    # 允许的成片长度区间：不超过目标上限，也不主动短于目标下限
    ceiling = target + tolerance

    def need_stop():
        # 只在 t_now 已经越过“整段计时”的情况下才停：
        # 目标计时（target）以上的部分只用来吸收片段长度，保证不越过上限。
        return clip_id > 0 and t_now >= target

    strong_list = list(strong_grid)
    step_grid = _beat_grid(beats, None, beat_step)[0] if beats else []

    guard = 0
    while not need_stop():
        guard += 1
        if guard > MAX_CLIP_GUARD:
            raise RuntimeError("混剪取片次数异常，已安全终止。请检查时长参数和素材。")
        if cancel_check and cancel_check():
            break

        # 每轮重新收集“还有余量”的区间；reuse=False 时用完即退出。
        candidates = available_indices()
        if not candidates:
            if not opt["reuse"]:
                break
            # 所有区间都取完后才重置游标，允许真正复用素材。
            _refill_cursors(ranges, state)
            candidates = available_indices()
            if not candidates:
                break

        range_idx = _choose_candidate(
            order_pool(candidates), ranges, state, t_now,
            tags_map, diversity, rnd,
            clip_min=clip_min, clip_max=clip_max,
            blacklist=blacklist, length_hint=(clip_min + clip_max) / 2.0,
            quality_weight=opt.get("quality_weight", 2.5),
        )
        if range_idx is None:
            # 所有候选都被当前强度排除（例如只剩同源可接）：放宽后重选一次。
            range_idx = _choose_candidate(
                order_pool(candidates), ranges, state, t_now,
                tags_map, "loose", rnd,
                clip_min=clip_min, clip_max=clip_max, blacklist=blacklist,
                quality_weight=opt.get("quality_weight", 2.5),
            )
            if range_idx is None:
                break

        rng = ranges[range_idx]

        avail = rng["end"] - rng["cursor"]
        if avail <= usable_floor:
            idle_skips += 1
            if idle_skips >= len(ranges):
                break
            continue

        # —— 整句切割 · 起点吸附：不从半句话开始 ——
        # 只前移很短一段（sentence_cut.snap_start 内部限制 max_skip），
        # 前移后余量不足就放弃吸附，不影响原有取片。
        if opt.get("sentence_snap") and rng.get("sentences"):
            moved = scut.snap_start(rng["cursor"], rng["sentences"])
            if moved > rng["cursor"] and (rng["end"] - moved) > usable_floor:
                rng["cursor"] = moved
                avail = rng["end"] - rng["cursor"]

        cap_remaining = ceiling - t_now
        if cap_remaining <= 0.05:
            break

        # 最短片段硬约束：剩余目标时长或素材余量放不下一个完整 clip_min 时，
        # 直接结束混剪；不生成低于最短时长的尾段，也不靠延长源边界、
        # 重复使用片段或越界时间戳凑时长。
        if cap_remaining < clip_min - 1e-6:
            break
        if avail < clip_min - 1e-6:
            idle_skips += 1
            if idle_skips >= len(ranges):
                break
            continue

        # 本段的长度上限按剩余空间分三档：
        #   1) 还能放下“本段 + 后面一段正常片段” → 取正常长度；
        #   2) 只够再放一段 → 本段限制在“剩余 - 最短片段”，剩下的留给下一段，
        #      保证下一段也有足够长度、收尾正好停在目标上；
        #   3) 最后一段：必须仍能放下一个完整最短片段，放不下就收尾。
        if cap_remaining >= clip_max * 2:
            upper = min(clip_max, avail)
            tail_exact = False
        elif cap_remaining >= clip_min * 2:
            upper = min(clip_max, avail, cap_remaining - clip_min)
            tail_exact = False
        else:
            upper = min(clip_max, avail, cap_remaining)
            tail_exact = True
            if upper < clip_min - 1e-6:
                break
        if upper > clip_max:
            if t_now < target - tolerance - END_SLACK:
                # 离目标下限还远：换成“补满到上限”，让成片尽量贴近目标
                upper = clip_max
            else:
                # 已接近目标下限，再塞一段会超出最长片段：就停在这里
                idle_skips += 1
                if idle_skips >= len(ranges):
                    break
                continue
        lower = clip_min
        if upper < lower - 1e-6:
            idle_skips += 1
            if idle_skips >= len(ranges):
                break
            continue
        # —— 卡点：先试强拍网格；强拍在可达范围外时按 beat_step 的拍位网格；
        #    都没有可用落点时才按概率补弱拍，避免每个鼓点都机械切换 ——
        length = None
        if strong_list or weak_pool:
            cut = _pick_beat_cut(strong_list, t_now, lower, upper) \
                if strong_list else None
            if cut is None and weak_pool:
                cut = _pick_beat_cut(weak_pool, t_now, lower, upper)
                if cut is not None:
                    cut_t = t_now + cut
                    at_step = any(abs(cut_t - s) <= _STRONG_PREFER
                                  for s in step_grid)
                    if not at_step and rnd.random() >= weak_chance:
                        cut = None
            length = cut
        if length is None:
            # 没有鼓点、或本次不想落在弱拍：按节奏策略取长度
            length = _rhythm_length(lower, upper, rhythm, rhythm_state, rnd)
        idle_skips = 0

        length = min(float(length), avail, cap_remaining)
        # —— 整句切割 · 切点吸附：落点挪到最近的一句结尾 ——
        # 只在 [lower, upper] 内找边界，所以不会破坏最短/最长片段与剩余时长约束；
        # 找不到合适句尾时原样返回（字幕没转写成功的素材照常按节奏切）。
        if opt.get("sentence_snap") and rng.get("sentence_ends"):
            length = scut.snap_cut(
                rng["cursor"], length, lower, upper, rng["sentence_ends"])
            length = min(float(length), avail, cap_remaining)
        if length < clip_min - 1e-6:
            idle_skips += 1
            if idle_skips >= len(ranges):
                break
            continue

        src_start = rng["cursor"]
        src_end = src_start + length
        rng["cursor"] = src_end
        tags = tags_map.get(rng["source_id"], [])

        project["clips"].append(
            tl.make_clip(
                clip_id, rng["source_id"], src_start, src_end,
                t_now, t_now + length, tags,
            )
        )
        _record_decision(state, range_idx, rng, tags, t_now, length)
        t_now += length
        clip_id += 1

    if not project["clips"]:
        if cancel_check and cancel_check():
            return project
        raise RuntimeError("没有生成任何有效片段，请检查素材与混剪参数。")

    return project
