# -*- coding: utf-8 -*-
"""时间覆盖（Coverage）约束。

问题
====
现在的选段全靠 ``_candidate_score`` 里的 ``recent`` 惩罚来「软性」分散，
但那是**对单条素材的上次出现时间**扣分。它有两个短板：

1. 只是倾向，可能被 ``quality`` / ``openclap`` 等更强的项压过；
2. 完全不看「这条素材的时间轴被用了多少」。

后果：一条 10 分钟的素材，如果前 2 分钟画质特别好，
成片很可能几乎全来自前 2 分钟，后 8 分钟一次都没碰。

设计
----
把每条素材的时间轴切成若干「桶」（bucket），记录每个桶已经贡献了多少秒。
选段时，对「已经用得多」的桶施加惩罚；必要时用**硬上限**保证分散。

两个关键取舍（来自 VideoHighlighter 的 coverage 设计）：

· **不能机械均匀抽样**。某段素材本身就烂，为了覆盖率强行使用是错的。
  所以覆盖不足只是「不加分」，从不「强制入选」。
· **硬上限必须可关闭**。coverage=0 时完全退化成纯评分排序，
  与原行为一致——这样这个功能出问题时可以一键回到旧行为。

本模块是纯函数，不碰 Qt、不碰 I/O，可以用假数据毫秒级测试。
"""
from __future__ import annotations

import math


def bucket_count(duration, target_duration, clip_hint):
    """把素材时间轴切成几个桶。

    取以下两者的**较大值**：

    · ``target_duration / clip_hint``——按成片能放几个片段
    · ``ceil(duration / BUCKET_SPAN)``——按固定时间跨度

    只用前者是不够的：一条 10 分钟素材配 30 秒成片，
    前者只给 8~10 个桶，每桶 60~75 秒。「前 2 分钟画质好」
    这种很常见的分布会整个落在 2 个桶里，coverage 根本看不出集中。

    加后者保证**无论成片多短，素材时间轴上都有足够细的桶**，
    才能识别「只用了前段」这种偏斜。

    ⚠ 这里必须**向上取整**（2026-11-20 修正）
    ----------------------------------------
    早期用的是 ``duration // BUCKET_SPAN``（向下取整），
    与「桶跨度上限 10 秒」的约定不符：

        素材时长   向下取整   实际桶宽      向上取整
        19 秒      1 个桶     19.0 秒  ✗   2 个桶
        25 秒      2 个桶     12.5 秒  ✗   3 个桶
        31 秒      3 个桶     10.3 秒  ✗   4 个��

    19 秒素材干脆只有 1 个桶，等于完全没有时间分区，
    coverage 退化成了「整段一个桶」——功能静默失效。
    改为 ``math.ceil`` 后，每桶跨度不超过 BUCKET_SPAN。

    Args:
        duration: 素材时长（秒）
        target_duration: 成片目标时长（秒）
        clip_hint: 单个片段的大致时长（秒）

    Returns:
        桶数，至少 1，最多 :data:`MAX_BUCKETS`
    """
    try:
        duration = float(duration or 0.0)
        target_duration = float(target_duration or 0.0)
        clip_hint = float(clip_hint or 0.0)
    except (TypeError, ValueError):
        return 1
    if duration <= 0:
        return 1

    by_budget = int(target_duration // clip_hint) if (
        clip_hint > 0 and target_duration > 0) else 1
    # 向上取整：保证每个桶的跨度不超过 BUCKET_SPAN
    by_span = (math.ceil(duration / BUCKET_SPAN) if BUCKET_SPAN > 0 else 1)
    n = max(1, by_budget, by_span)
    return min(n, MAX_BUCKETS)


# 桶的时间跨度上限（秒）：保证长素材也有足够细的颗粒度
BUCKET_SPAN = 10.0
# 桶数硬上限，避免超长素材切出几万个桶
MAX_BUCKETS = 512


def bucket_of(start, duration, count):
    """某个起始时间落在第几个桶（0 基）。"""
    if count <= 1 or duration <= 0:
        return 0
    ratio = max(0.0, min(0.999999, float(start) / float(duration)))
    return int(ratio * count)


def fair_share(target_duration, count):
    """每个桶的公平配额（秒）。"""
    if count <= 0:
        return 0.0
    return max(0.0, float(target_duration or 0.0)) / count


def bucket_ceiling(target_duration, count, coverage, clip_hint=None):
    """单个桶最多能贡献多少秒。

    ``coverage`` 在 0~1 之间插值：

    · 0.0 → 上限 = 整个目标时长（等于没上限，纯评分排序）
    · 1.0 → 上限 = 一个桶的公平配额（强制分散）

    **公平配额不能低于一个片段的长度**：桶是时间轴上的位置，
    而片段是有长度的。如果配额只有 0.4 秒而片段是 3 秒，
    那么任何片段都必然「超配额」，惩罚就退化成常数，coverage 失效。
    因此配额取 ``max(target/count, clip_hint)``——
    意思是「这一桶至少要能放下一个正常片段」，超出一个片段才罚。

    Args:
        target_duration: 成片目标时长
        count: 桶数
        coverage: 覆盖强度 0~1
        clip_hint: 单个片段的大致时长（秒）；给定时作为配额下限
    """
    total = max(0.0, float(target_duration or 0.0))
    if count <= 1:
        return total
    try:
        cov = float(coverage)
    except (TypeError, ValueError):
        cov = 0.0
    cov = max(0.0, min(1.0, cov))
    share = fair_share(total, count)
    if clip_hint:
        # 配额至少能装下一个正常片段
        share = max(share, float(clip_hint))
    return share + (1.0 - cov) * (total - share)


def coverage_penalty(used_sec, ceiling, *, weight=1.0, cap=12.0):
    """某个桶超配额时的惩罚分（负数，越小越不容易被选中）。

    没超配额 → 0（不奖励也不惩罚，避免「为了凑覆盖而硬选」）。

    超出比例线性放大。``cap`` 是单项惩罚的绝对值上限。

    为什么 cap 必须足够大
    --------------------
    实测（2026-11-20）：cap 太小时惩罚只有 -8，
    而质量分差 + 同源惩罚合计更大，表现为「开了 coverage 但结果没变」。

    为什么用「超出比例」而不是「已用秒数」
    ------------------------------------
    配额本身随 coverage 变化。按比例算，coverage 从 0.5 提到 1.0
    时惩罚力度会同步增强，而不是要用户手动再调 weight。

    Args:
        used_sec: 该桶已贡献的秒数
        ceiling: 该桶的上限
        weight: 惩罚权重
        cap: 单项惩罚上限（绝对值）
    """
    ceiling = max(1e-6, float(ceiling or 0.0))
    used = max(0.0, float(used_sec or 0.0))
    if used <= ceiling:
        return 0.0
    overshoot = (used - ceiling) / ceiling      # 超出的比例
    return -min(max(0.0, float(cap or 0.0)), overshoot) * max(
        0.0, float(weight or 0.0))


def stats(source_usage, source_duration, count):
    """统计一条素材的覆盖情况，供界面/自检显示。

    Args:
        source_usage: ``{bucket_index: used_seconds}``
        source_duration: 素材时长
        count: 桶数

    Returns:
        dict：covered 桶数、覆盖比例、各桶用量、最热桶占比
    """
    count = max(1, int(count or 1))
    usage = dict(source_usage or {})
    total = sum(max(0.0, float(v or 0.0)) for v in usage.values())
    covered = sum(1 for i in range(count) if usage.get(i, 0.0) > 1e-6)
    hot = max((float(usage.get(i, 0.0) or 0.0) for i in range(count)),
              default=0.0)
    return {
        "bucket_count": count,
        "covered": covered,
        "coverage": covered / count,
        "used_total": round(total, 3),
        "hot_share": (hot / total) if total > 1e-6 else 0.0,
        "per_bucket": [round(float(usage.get(i, 0.0) or 0.0), 3)
                       for i in range(count)],
    }


class CoverageTracker:
    """可选段的覆盖记账器：记录每 (素材, 桶) 用了多少秒。

    与 ``_decision_state`` 分开是因为后者已经很重，而且这个功能要能
    一键关掉（coverage=0 时 build 不创建它）。
    """

    def __init__(self, durations, *, target_duration, clip_hint,
                 coverage, weight=0.0, cap=12.0):
        self.coverage = max(0.0, min(1.0, float(coverage or 0.0)))
        self.weight = max(0.0, float(weight or 0.0))
        self.cap = max(0.0, float(cap or 0.0))
        self.durations = dict(durations or {})
        self.counts = {}
        self.ceilings = {}
        self.usage = {}
        for sid, dur in self.durations.items():
            n = bucket_count(dur, target_duration, clip_hint)
            self.counts[sid] = n
            self.ceilings[sid] = bucket_ceiling(
                target_duration, n, self.coverage, clip_hint=clip_hint)
            self.usage[sid] = {}

    @property
    def enabled(self):
        """coverage=0 时完全关闭，行为与改造前一致。"""
        return self.coverage > 0.0 and self.weight > 0.0

    def bucket_range(self, sid, start, end):
        """返回 [start, end) 覆盖到的所有桶索引及其在里面停留的秒数。

        这是跨桶分摊的基础（第二十二阶段修正）。

        早期实现只按**片段起点**定桶，把整段时长记进那一个桶：

            桶 A: 0~10 秒，桶 B: 10~20 秒
            片段 8~13 秒 → 5 秒全记进桶 A

        但其中 2 秒其实取自桶 B。长期下来，「某个桶被用了多少」
        与实际取材分布不符——正是 coverage 想度量的东西。

        Returns:
            ``{bucket_index: seconds}``，各值之和等于片段时长（不超过素材边界）。
        """
        n = self.counts.get(sid, 1)
        dur = self.durations.get(sid, 0.0)
        if n <= 1 or dur <= 0:
            return {0: max(0.0, float(end) - float(start))}

        width = dur / n
        s = max(0.0, float(start))
        e = min(float(dur), float(end))
        if e <= s:
            return {}

        out = {}
        b0 = bucket_of(s, dur, n)
        b1 = bucket_of(max(s, e - 1e-9), dur, n)
        for b in range(b0, b1 + 1):
            lo = b * width
            hi = min(dur, (b + 1) * width)
            overlap = min(e, hi) - max(s, lo)
            if overlap > 1e-9:
                out[b] = out.get(b, 0.0) + overlap
        return out

    def bucket(self, sid, start):
        """片段**起点**所在的桶（仅用于「从哪个桶开始」这类粗判断）。"""
        n = self.counts.get(sid, 1)
        return bucket_of(start, self.durations.get(sid, 0.0), n)

    def penalty(self, sid, start, end=None):
        """取材惩罚（负数越小越不容易被选中）。

        给出 ``end`` 时按**覆盖到的所有桶**分别算惩罚并相加——
        跨桶片段会同时为其触及的每个桶承担超配额代价。
        这正是「8~13 秒的片段不该全部记进桶 A」的落地方式。

        惩罚量按「该桶将因这次取材变成多少用量」计算：
        用 ``used + 本次分摊秒数`` 与上限比较，超多少罚多少。

        不给 ``end`` 时退化为「只看起点所在桶」（兼容旧调用）。
        """
        if not self.enabled:
            return 0.0
        ceiling = self.ceilings.get(sid, 1e-6)

        if end is None:
            b = self.bucket(sid, start)
            used = self.usage.get(sid, {}).get(b, 0.0)
            return coverage_penalty(used, ceiling, weight=self.weight,
                                    cap=self.cap)

        shares = self.bucket_range(sid, start, end)
        if not shares:
            return 0.0
        total = 0.0
        for b, sec in shares.items():
            if sec <= 1e-6:
                continue
            used = self.usage.get(sid, {}).get(b, 0.0)
            total += coverage_penalty(used + sec, ceiling,
                                      weight=self.weight, cap=self.cap)
        return total

    def record(self, sid, start, seconds):
        """把一次取材记进覆盖统计。

        ``start`` 是片段起点，``seconds`` 是时长；按实际跨桶范围分摊。
        """
        if sid not in self.usage:
            self.usage[sid] = {}
        s = float(start)
        e = s + max(0.0, float(seconds or 0.0))
        for b, sec in self.bucket_range(sid, s, e).items():
            self.usage[sid][b] = self.usage[sid].get(b, 0.0) + sec

    def report(self):
        out = {}
        for sid, n in self.counts.items():
            out[sid] = stats(self.usage.get(sid), self.durations.get(sid), n)
        return out
