# -*- coding: utf-8 -*-
"""MixCut · 整句切割（切点吸附到语音的整句边界）

问题
----
混剪的切点原来是纯按节奏随机生成的（beat / 时长策略），落在哪就是哪。
口播、访谈、Vlog 这类素材上，切点经常卡在半个字或半句话中间，
成片听起来"话被剪断了"。

做法
----
用语音转写得到的整句时间戳（gpu_engine.transcribe 的 segments）当作
"合法切点集合"，把随机算出来的长度吸附到最近的整句边界：

  · 边界点取每句的 end（= 下一句的 start），切在句间空隙里，不切句子内部；
  · 只在 [cursor + lower, cursor + upper] 这个由节奏策略算出的可达区间里找，
    不会因为吸附而破坏「最短/最长片段」和「剩余时长」约束；
  · 区间里没有合适边界时原样返回，绝不因为字幕还没算好而卡住混剪；
  · 起点也做一次轻量吸附：明显落在句子中间时，前移到句首（最多让出 0.75 秒）。

对外接口
--------
  sentence_ends(segments)                    -> list[float]
  snap_cut(cursor, target_length, lower, upper, ends, ends_tolerance) -> float
  snap_start(cursor, segments, max_skip)     -> float
"""

from __future__ import annotations

# 切点与句尾允许的"本来就对齐"误差：小于它就认定已经是整句切，不做微调
ALREADY_SNAPPED = 0.12

# 起点吸附：离句首超过这个距离才算"句子中间"
START_MID_SENTENCE = 0.25


def _as_pairs(segments):
    """把转写片段统一成 [(start, end), ...]。

    既支持 gpu_engine 的 dict 结果，也支持混剪时间轴里压缩过的元组。
    """
    pairs = []
    for seg in segments or []:
        try:
            if isinstance(seg, dict):
                start, end = float(seg.get("start")), float(seg.get("end"))
            else:
                start, end = float(seg[0]), float(seg[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        pairs.append((start, end))
    return pairs


def sentence_ends(segments):
    """从转写片段里取出可用的切点（每句结束时刻，秒）。

    过滤掉过短/异常的句子（个别识别碎片会把切点拉得很碎）。
    """
    ends = []
    for start, end in _as_pairs(segments):
        if end - start < 0.08:
            continue
        ends.append(end)
    return sorted(ends)


def snap_cut(cursor, target_length, lower, upper, ends):
    """把切点吸附到最近的一句结尾。

    参数：
      cursor         片段起始时刻（素材内时间，秒）
      target_length  节奏策略算出的目标长度
      lower/upper    本条片段允许的长度区间（含最短片段/剩余时长约束）
      ends           sentence_ends() 的结果

    返回吸附后的长度；区间内没有整句边界时返回 target_length（行为与原来一致）。
    """
    target_length = float(target_length)
    lower = max(0.0, float(lower))
    upper = max(lower, float(upper))
    if not ends:
        return target_length

    base = float(cursor) + target_length
    lo = float(cursor) + lower
    hi = float(cursor) + upper

    candidates = [e for e in ends if lo <= e <= hi]
    if not candidates:
        return target_length

    nearest = min(candidates, key=lambda e: abs(e - base))
    if abs(nearest - base) <= ALREADY_SNAPPED:
        return target_length
    return max(lower, min(upper, nearest - float(cursor)))


def snap_start(cursor, segments, max_skip=0.75):
    """把明显落在句子中间的起点前移到句首。

    只允许前移很小一段（默认 0.75 秒），超过就保持原样——
    否则会为了"整句"丢掉大段可用画面。
    """
    cursor = float(cursor)
    for start, end in _as_pairs(segments):
        if start <= cursor < end:
            if cursor - start <= START_MID_SENTENCE:
                return cursor          # 就在句首附近，不用动
            if end - cursor <= max_skip:
                return end             # 前移到本句结束 = 下一句开始
            return cursor              # 让出太多画面，宁可从句子中间开始
    return cursor
