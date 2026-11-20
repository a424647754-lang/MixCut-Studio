# -*- coding: utf-8 -*-
"""视觉多样性：判断「画面是否相似」，不只是「来源是否不同」。

问题
====
现在 ``_candidate_score`` 的多样性只看两件事：
``source_id``（不同文件）和用户标签。于是：

    素材 A：人物正面近景
    素材 B：人物正面近景
    素材 C：人物正面近景

三个不同文件、标签也可能不同，但**画面几乎一样**，连排会非常单调。

设计
----
分两层，**不依赖任何 AI 模型也必须能工作**：

**第一层（默认可用）：传统视觉指纹**
用已有的分析数据算一个轻量描述向量，比较余弦相似度：

| 分量 | 来源 | 含义 |
|---|---|---|
| 平均亮度 | ``brightness`` | 明暗基调 |
| 对比度 | ``contrast`` | 画面反差 |
| 边缘密度 | ``edge_density`` | 结构复杂度 |
| 饱和度 | ``saturation`` | 色彩浓淡 |
| 运动强度 | ``motion`` | 动静 |

这些值 ``bad_shot_detector`` 已经算过、``cv_cache`` 里已有，
**不需要额外解码**。它抓不到「都是人脸近景」这种语义重复，
但能抓住「明暗/结构/运动雷同」，是零成本的第一道防线。

**第二层（可选）：OpenCLIP 语义向量**
已有 ``clip_embedding`` 时用它做语义相似度，效果更好。
没有模型时自动跳过，不影响主流程。

关键原则
--------
**相似度只做软惩罚，不做硬淘汰。** 两个相似画面也可能都有剪辑价值
（比如一个近景一个更近的近景，节奏上是有意义的）。
"""
from __future__ import annotations


# 传统指纹的分量顺序与归一化区间。
# 每个区间选的是「分析器实际会产出的量级」，超出会被夹紧。
_FINGERPRINT_FIELDS = (
    ("brightness", 0.0, 255.0),
    ("contrast", 0.0, 100.0),
    ("edge_density", 0.0, 100.0),
    ("saturation", 0.0, 255.0),
    ("motion", 0.0, 50.0),
)
FINGERPRINT_DIM = len(_FINGERPRINT_FIELDS)


def fingerprint(quality):
    """从一帧/一段的实测质量数据算归一化视觉指纹。

    Args:
        quality: dict，含 ``brightness`` / ``contrast`` / ``edge_density``
                 / ``saturation`` / ``motion`` 任意几项

    Returns:
        长度为 :data:`FINGERPRINT_DIM` 的元组；全为 0 表示数据不足。
    """
    q = quality or {}
    vec = []
    have = 0
    for key, lo, hi in _FINGERPRINT_FIELDS:
        try:
            v = float(q.get(key))
        except (TypeError, ValueError):
            vec.append(0.0)
            continue
        if v != v:              # NaN
            vec.append(0.0)
            continue
        have += 1
        span = max(1e-6, hi - lo)
        x = (v - lo) / span
        vec.append(max(0.0, min(1.0, x)))
    if have == 0:
        return tuple(0.0 for _ in vec)
    return tuple(vec)


def usable(vec):
    """指纹是否可用（不是全零/全缺）。"""
    if not vec:
        return False
    return any(abs(float(x)) > 1e-9 for x in vec)


def cosine(a, b):
    """余弦相似度；任一向量为零向量时返回 0（视为「无从判断」）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        try:
            fx = float(x)
            fy = float(y)
        except (TypeError, ValueError):
            continue
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def similarity(a, b):
    """两张画面的相似度 0~1（1 = 完全一样）。

    余弦可能为负（在色/亮度轴上方向相反），这里夹到 0~1：
    负相关同样是「很不一样」，对多样性来说与 0 等价。
    """
    return max(0.0, min(1.0, cosine(a, b)))


def novelty_penalty(vec, recent_vectors, *, threshold=0.92,
                    weight=1.0, max_count=6, exclude_self=True):
    """与「最近几个镜头」的最大相似度超过阈值时的惩罚（负数）。

    Args:
        vec: 候选片段的指纹
        recent_vectors: 最近已选片段的指纹列表（新的在前也无所谓）
        threshold: 相似度超过它才开始惩罚（默认 0.92，很严格——
                   只有「几乎一样」才罚，避免误伤正常变化）
        weight: 惩罚权重
        max_count: 最多看最近几个
        exclude_self: 跳过与 ``vec`` 完全相同的项。历史里若混进了候选
                      自己（调用方把当前项也塞进 recent 了），
                      自我比较会得到相似度 1.0，导致「新片段被判雷同」。
                      默认 True，把这个常见误用挡在外面。
    """
    if not usable(vec) or not recent_vectors:
        return 0.0
    weight = max(0.0, float(weight or 0.0))
    if weight <= 0:
        return 0.0
    peak = 0.0
    for other in list(recent_vectors)[:max_count]:
        if not usable(other):
            continue
        if exclude_self and len(other) == len(vec) and \
                all(abs(float(x) - float(y)) < 1e-12
                    for x, y in zip(other, vec)):
            continue        # 这是候选自己，跳过
        s = similarity(vec, other)
        if s > peak:
            peak = s
    if peak <= threshold:
        return 0.0
    # 超出阈值的部分线性放大；1.0 相似度时达到 weight*2 的惩罚上限
    over = (peak - threshold) / max(1e-6, 1.0 - threshold)
    return -min(2.0, over) * weight


def openclip_penalty(vector, recent_vectors, *, threshold=0.90,
                     weight=1.0, max_count=6):
    """OpenCLIP 语义向量的新鲜度惩罚（负数）。

    与 :func:`novelty_penalty` 同样的形状，但用的是 CLIP 向量
    （通常已归一化，余弦直接就是相似度）。
    没有向量时返回 0，调用方无需先判断模型是否可用。
    """
    if not vector or not recent_vectors:
        return 0.0
    weight = max(0.0, float(weight or 0.0))
    if weight <= 0:
        return 0.0
    peak = 0.0
    for other in list(recent_vectors)[:max_count]:
        if not other or len(other) != len(vector):
            continue
        try:
            s = sum(float(x) * float(y) for x, y in zip(vector, other))
        except (TypeError, ValueError):
            continue
        s = max(0.0, min(1.0, s))
        if s > peak:
            peak = s
    if peak <= threshold:
        return 0.0
    over = (peak - threshold) / max(1e-6, 1.0 - threshold)
    return -min(2.0, over) * weight


def describe():
    """给界面/文档用的说明。"""
    return ("视觉多样性：用亮度/对比度/边缘/饱和度/运动五项传统特征"
            "判断画面是否雷同（阈值 0.92，只做软惩罚）；"
            "启用 OpenCLIP 时额外做语义相似度判断。")
