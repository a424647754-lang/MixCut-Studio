"""
MixCut · 素材分析核心

逐素材检测黑屏 / 静止 / 静音，并提取可用于智能混剪的有效片段。
本模块只负责分析与有效区间计算。

核心函数：
  analyze_sources(...)            逐素材分析，返回带废段信息的源数据
  valid_segments_for(...)          返回可供混剪使用的有效区间
"""

from pathlib import Path

from . import analysis_schema
from . import ffmpeg_tools as ft
from . import timeline as tl


# 废镜头类型标签（中文，直接用于界面展示）
TYPE_BLACK = "黑屏"
TYPE_FREEZE = "静止"
TYPE_SILENCE = "静音"


DEFAULT_OPTIONS = {
    # —— 总开关 ——
    "video_detect": True,        # 画面检测（黑屏 + 静止）
    "detect_black": True,
    "detect_freeze": True,
    "audio_detect": True,        # 静音检测

    # —— 黑屏阈值 ——
    "black_min_duration": 0.5,
    "black_pixel_threshold": 0.10,
    "black_picture_threshold": 0.98,

    # —— 静止阈值 ——
    "freeze_min_duration": 1.0,
    "freeze_noise": 0.001,

    # —— 静音阈值 ——
    "silence_min_duration": 0.5,
    "silence_noise_db": -35.0,

    # 合并 / 保留策略
    "merge_gap": 0.15,           # 两段废镜头间隔小于该值则合并
    # 有效段的最小长度：只用来丢掉“短到切不出来”的碎渣（约 1~2 帧）。
    # 片段真正的最短长度由混剪参数 clip_min 决定，不在这里卡死。
    "min_keep_duration": 0.05,

    # 画面质量只参与混剪排序，不作为硬性废片过滤条件。
    "quality_detect": True,
    "quality_segment_duration": 5.0,
    "quality_samples_per_segment": 3,
}


# 区间比较的浮点容差（秒）
_EPS = 1e-6


# ---------------------------------------------------------
# 区间工具
# ---------------------------------------------------------

def merge_intervals(intervals, merge_gap=0.0):
    """
    输入 [(start, end, set(types)), ...]，合并重叠/相邻区间并合并类型集合。
    返回 [(start, end, [types...])]，按起点排序。
    """
    if not intervals:
        return []

    ordered = sorted(intervals, key=lambda x: x[0])
    merged = []
    cur_s, cur_e, cur_types = ordered[0]

    for s, e, types in ordered[1:]:
        if s <= cur_e + merge_gap:
            cur_e = max(cur_e, e)
            cur_types |= set(types)
        else:
            merged.append((cur_s, cur_e, sorted(cur_types)))
            cur_s, cur_e, cur_types = s, e, set(types)

    merged.append((cur_s, cur_e, sorted(cur_types)))
    return merged


def complement_intervals(duration, junk_intervals, min_keep):
    """
    求 [0, duration] 内废镜头之外的有效段，并丢弃过短的段。
    junk_intervals: [(start, end, types)]
    """
    valid = []
    cursor = 0.0

    for s, e, _types in junk_intervals:
        s = max(0.0, min(s, duration))
        e = max(0.0, min(e, duration))
        if s > cursor and s - cursor >= min_keep:
            valid.append((cursor, s))
        cursor = max(cursor, e)

    if duration - cursor >= min_keep:
        valid.append((cursor, duration))

    return valid


# ---------------------------------------------------------
# 单素材分析
# ---------------------------------------------------------

def is_silent_track(silence, duration):
    """判断整条音轨是否全程安静（空音轨 / 未录到声音）。

    很多相机、手机会生成“有音轨但全程 -91 dB”的空音轨。这种素材画面是
    正常的，只是没有声音；如果按“整段静音”剔除，就会把正常素材判成废片。

    判定条件：静音区间的总长度覆盖了绝大部分时长（>= 95%）。
    """
    if not duration or duration <= 0 or not silence:
        return False
    covered = sum(max(0.0, min(e, duration) - max(s, 0.0)) for s, e in silence)
    return covered >= duration * 0.95


def analyze_source(source_id, path, options, progress_cb=None, cancel_check=None):
    duration = ft.probe_duration(path)

    # 单进程联合检测，视频滤镜前内部已缩小，进度按真实时间回传
    detected = ft.detect_all(
        path, options, duration=duration,
        progress_cb=progress_cb, cancel_check=cancel_check,
    )

    # 空音轨（全程静音）不参与剔除：画面照常可用，只当作“没有声音”
    silence = detected["silence"]
    whole_silent = is_silent_track(silence, duration)

    raw = []
    for s, e in detected["black"]:
        raw.append((s, e, {TYPE_BLACK}))
    for s, e in detected["freeze"]:
        raw.append((s, e, {TYPE_FREEZE}))
    if not whole_silent:
        for s, e in silence:
            raw.append((s, e, {TYPE_SILENCE}))

    junk = merge_intervals(raw, merge_gap=options["merge_gap"])

    # 独立评估固定时间窗口的画面质量。评分只用于混剪排序，不直接剔除片段。
    quality_segments = []
    if options.get("quality_detect", True):
        try:
            from .bad_shot_detector import BadShotDetector
            detector = BadShotDetector()
            measured = detector.analyze_video(
                path,
                segment_duration=options.get("quality_segment_duration", 5.0),
                sample_count_per_segment=options.get("quality_samples_per_segment", 3),
                cancel_check=cancel_check,
            )
            quality_segments = [
                {
                    "start": float(q.start_sec),
                    "end": float(q.end_sec),
                    "quality_score": float(q.quality_score),
                    "bad_frame_ratio": float(q.bad_frame_ratio),
                    "is_bad": bool(q.is_bad),
                    "reason": str(q.reason or ""),
                    "sharpness": float(q.sharpness),
                    "brightness": float(q.brightness),
                    "contrast": float(q.contrast),
                    "noise": float(q.noise),
                    "motion": float(q.motion),
                }
                for q in measured if q.end_sec > q.start_sec
            ]
        except Exception:
            # OpenCV 不可用或单素材解码失败时，保留原有规则检测结果，避免中断整批任务。
            quality_segments = []

    return {
        "source": tl.make_source(source_id, path, duration),
        "junk": [
            tl.make_junk(source_id, s, e, types)
            for s, e, types in junk
        ],
        "_junk_merged": junk,   # 内部使用，重建时方便
        "_quality_segments": quality_segments,
        "_silent_track": whole_silent,   # 空音轨标记（界面/其他模块可用）
        # 契约字段：本路径不跑镜头检测，明确标记为「没测过」而不是「没找到」
        "_scenes": None,
        "_analysis_schema": analysis_schema.ANALYSIS_SCHEMA,
    }


def analyze_sources(paths, options=None, progress_cb=None, cancel_check=None):
    options = {**DEFAULT_OPTIONS, **(options or {})}
    results = []
    total = len(paths)

    for idx, path in enumerate(paths):
        if cancel_check and cancel_check():
            break
        name = Path(path).name
        if progress_cb:
            progress_cb(idx, total, f"分析素材 {idx + 1}/{total}：{name}")

        def _inner(pct, i=idx, n=name):
            if progress_cb:
                # 用浮点 idx 把单文件内部 0~100% 映射到整体进度
                frac = i + max(0, min(100, int(pct))) / 100.0
                progress_cb(frac, total,
                            f"分析素材 {i + 1}/{total}：{n}（{int(pct)}%）")

        results.append(
            analyze_source(idx, path, options,
                           progress_cb=_inner, cancel_check=cancel_check)
        )

    if progress_cb and not (cancel_check and cancel_check()):
        progress_cb(total, total, "素材分析完成")
    return results


# ---------------------------------------------------------
# 由分析结果构建时间轴
# ---------------------------------------------------------

def _junk_tuples(analysis_item, options):
    """优先用人工确认后的剔除区间，否则用检测合并结果。"""
    overrides = analysis_item.get("exclusions")
    if overrides is not None:
        tuples = []
        for item in overrides:
            tuples.append((item["start"], item["end"], set(item.get("types", []))))
        return merge_intervals(tuples, merge_gap=options["merge_gap"])
    return analysis_item["_junk_merged"]


# ---------------------------------------------------------
# 废段 → 有效区间
# ---------------------------------------------------------

def valid_segments_for(item, options=None):
    """返回某个素材分析结果里的有效区间 [(start, end)]，供混剪取片使用。

    这些区间一定互不重叠；画面质量窗口只在混剪建立候选池时进一步细分，
    不改变基础有效区间的定义。

    只丢弃“短到切不出来”的碎渣（小于 min_keep_duration，默认 0.05 秒 =
    一帧量级），不再用 0.6 秒这类固定门槛把短片段整段丢掉；真正的片段
    最短长度由混剪参数 clip_min 决定。
    """
    options = {**DEFAULT_OPTIONS, **(options or {})}
    duration = item["source"]["duration"]
    junk = _junk_tuples(item, options)
    min_keep = max(_EPS, float(options.get("min_keep_duration", 0.05)))

    return complement_intervals(duration, junk, min_keep)


def quality_pieces_for(item, start, end):
    """把有效区间按质量窗口切开，返回 [(start, end, quality_info), ...]。

    没有质量数据时返回中性分 70，保证 OpenCV/解码失败不会误伤素材。
    """
    start, end = float(start), float(end)
    if end - start <= _EPS:
        return []

    segments = item.get("_quality_segments") or []
    if not segments:
        return [(start, end, {
            "quality_score": 70.0, "bad_frame_ratio": 0.0,
            "is_bad": False, "reason": "", "quality_available": False,
        })]

    points = {start, end}
    for seg in segments:
        a, b = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        if start + _EPS < a < end - _EPS:
            points.add(a)
        if start + _EPS < b < end - _EPS:
            points.add(b)
    ordered = sorted(points)
    pieces = []

    for a, b in zip(ordered, ordered[1:]):
        if b - a <= _EPS:
            continue
        overlaps = []
        for seg in segments:
            overlap = max(0.0, min(b, float(seg.get("end", 0.0))) -
                          max(a, float(seg.get("start", 0.0))))
            if overlap > 0:
                overlaps.append((seg, overlap))
        if not overlaps:
            info = {
                "quality_score": 70.0, "bad_frame_ratio": 0.0,
                "is_bad": False, "reason": "", "quality_available": False,
            }
        else:
            total = sum(weight for _seg, weight in overlaps)
            score = sum(float(seg.get("quality_score", 70.0)) * weight
                        for seg, weight in overlaps) / total
            bad_ratio = sum(float(seg.get("bad_frame_ratio", 0.0)) * weight
                            for seg, weight in overlaps) / total
            reasons = sorted({str(seg.get("reason", "")) for seg, _ in overlaps
                              if seg.get("reason")})
            info = {
                "quality_score": score,
                "bad_frame_ratio": bad_ratio,
                "is_bad": bad_ratio >= 0.67 or score < 25.0,
                "reason": "、".join(reasons),
                "quality_available": True,
            }
        pieces.append((a, b, info))
    return pieces

