# -*- coding: utf-8 -*-
"""MixCut · 音频/画面指纹去重

用途
----
同一批素材里经常混进同一条片子的重复拷贝（手机相册重复导出、
剪辑人另存版本、不同清晰度的同一段内容）。重复素材进池子会挤掉
真正的新内容，成片看着"来回就那几个镜头"。

做法
----
1. 每条素材用 CUDA 引擎（sd_dedup，见 gpu_engine）提一次指纹：
   phash 关键帧哈希 + 色彩直方图 + 时长；指纹结果按路径缓存，只提一次。
2. 在 Python 里直接比指纹，不再为每一对素材起一次进程：
   · phash：按归一化时间重采样到相同长度后算汉明相似度；
   · 色彩：262144 维直方图做余弦相似度；
   · 时长：相差超过 1.4 倍直接判为不同片子，不浪费计算。
3. 综合相似度超过阈值（默认 0.92）视为重复，同组里保留时长最长的那条，
   其余标记为"重复，跳过"。

为什么不用引擎的 compare-fp：
   那是给"两条片子精查"用的，每调用一次都要重新初始化 CUDA；
   批量去重是 N² 对比较，起进程的开销会比计算本身还大。

对外接口
--------
  fingerprint_similarity(fp_a, fp_b) -> float           0~1
  find_duplicates(paths, ...) -> (groups, scores)       分组结果 + 每条素材的判重说明
  DEFAULT_DUP_THRESHOLD
"""

from __future__ import annotations

import itertools

import numpy as np

from . import gpu_engine

# 判为"同一条片子"的综合相似度门槛
DEFAULT_DUP_THRESHOLD = 0.92

# phash 重采样后的比较点数
_PHASH_POINTS = 16

# 时长相差超过这个倍数就完全不比（不同片子）
_DURATION_RATIO = 1.4


def _as_hash(value):
    """把 phash 统一成整数。

    引擎写出的 phash 是 16 位十六进制字符串（如 '4e292cafb5544b2a'），
    历史指纹里也可能直接是十进制整数，两种都要认。
    """
    if isinstance(value, str):
        return int(value, 16)
    return int(value)


def _hamming(a, b):
    """64 位哈希的汉明相似度（0~1）。"""
    try:
        diff = _as_hash(a) ^ _as_hash(b)
    except (TypeError, ValueError):
        return 0.0
    return 1.0 - bin(diff).count("1") / 64.0


def _resample_phash(fp):
    """按归一化时间把 phash 序列重采样成固定长度，便于跨时长比较。"""
    frames = fp.get("phash_frames") or []
    ts = fp.get("keyframe_ts") or []
    if not frames:
        return []
    if len(frames) < 2 or len(ts) != len(frames):
        return [_as_hash(frames[0])] * _PHASH_POINTS

    t0, t1 = float(ts[0]), float(ts[-1])
    span = max(1e-6, t1 - t0)
    out = []
    for k in range(_PHASH_POINTS):
        want = t0 + span * (k / (_PHASH_POINTS - 1))
        # ts 有序，取最近的关键帧
        idx = min(range(len(ts)), key=lambda i: abs(float(ts[i]) - want))
        out.append(_as_hash(frames[idx]))
    return out


def _phash_similarity(a_fp, b_fp):
    a = _resample_phash(a_fp)
    b = _resample_phash(b_fp)
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    return sum(_hamming(a[i], b[i]) for i in range(n)) / n


def _color_similarity(a_fp, b_fp):
    a_hist = a_fp.get("color_hist") or []
    b_hist = b_fp.get("color_hist") or []
    if not a_hist or not b_hist or len(a_hist) != len(b_hist):
        return 0.0
    a = np.asarray(a_hist, dtype=np.float32)
    b = np.asarray(b_hist, dtype=np.float32)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def fingerprint_similarity(fp_a, fp_b):
    """两条素材的综合相似度（0~1）。时长差太多直接返回 0。"""
    if not fp_a or not fp_b:
        return 0.0
    da = float(fp_a.get("duration") or 0.0)
    db = float(fp_b.get("duration") or 0.0)
    if da > 0 and db > 0:
        ratio = max(da, db) / min(da, db)
        if ratio > _DURATION_RATIO:
            return 0.0

    phash_sim = _phash_similarity(fp_a, fp_b)
    color_sim = _color_similarity(fp_a, fp_b)
    # phash 更能反映"同一段画面"，色彩直方图只是兜底
    return 0.65 * phash_sim + 0.35 * color_sim


def find_duplicates(paths, threshold=None, progress_cb=None, cancel_check=None):
    """在素材列表里找重复片子。

    返回 (groups, scores)：
      groups  [[path, ...], ...]  每组第一条是"保留"的那条，其余是重复拷贝
      scores  {path: (相似度, 与谁重复)}  便于日志逐条说明

    引擎不可用或指纹提取失败时返回空结果，不抛异常。
    """
    threshold = float(threshold or DEFAULT_DUP_THRESHOLD)
    paths = [str(p) for p in paths]
    groups, scores = [], {}

    if not gpu_engine.available():
        return groups, scores

    fps = {}
    total = len(paths)
    for idx, path in enumerate(paths):
        if cancel_check and cancel_check():
            return groups, scores
        if progress_cb:
            progress_cb(idx, total, "提取指纹进行查重")
        fp = gpu_engine.fingerprint(path)
        if fp:
            fps[path] = fp
    if progress_cb:
        progress_cb(total, total, "指纹提取完成")

    entries = list(fps.keys())
    dropped = set()
    for a, b in itertools.combinations(entries, 2):
        if cancel_check and cancel_check():
            return groups, scores
        if a in dropped or b in dropped:
            continue
        sim = fingerprint_similarity(fps[a], fps[b])
        if sim < threshold:
            continue
        # 保留时长更长的那条（同长保留先入池的）
        keep, dup = (a, b)
        if float(fps[b].get("duration") or 0.0) > float(fps[a].get("duration") or 0.0):
            keep, dup = b, a
        dropped.add(dup)
        scores[dup] = (sim, keep)
        # 并入已有分组，避免同一条片子散在多个组里
        placed = False
        for group in groups:
            if keep in group:
                group.append(dup)
                placed = True
                break
        if not placed:
            groups.append([keep, dup])

    return groups, scores
