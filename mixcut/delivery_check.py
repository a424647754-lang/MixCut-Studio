# -*- coding: utf-8 -*-
"""成片自检：检查混剪结果是否符合预期。

为什么单独做一个模块
====================
混剪引擎算出时间线就结束了。但**交付给用户的是成片**，不是候选池。
选片阶段的评分再漂亮，也可能拼出这样的结果：

· 成片里同一个片段出现两次
· 时间线总长和目标差了一大截
· 某个片段的时间超出素材时长（剪映打开会报错或黑帧）
· 连续 8 个 0.5 秒的碎镜头（节奏崩塌）
· 一条素材占了成片 70%
· 音乐时长和成片对不上

这些问题在选片阶段看不出来，只能对**最终时间线**检查。
本模块只读时间线，不改它——修复由调用方决定。

设计原则
--------
1. **能定位到具体片段**。不只说「有问题」，要说「第 12 个片段，时间线 34.2 秒处」。
2. **分严重级别**。error 会导致剪映打不开/画面出错；warn 只是观感问题。
3. **不做静默修复**。给建议，不擅自改。修不修是用户的决定。
4. **纯函数**。不碰 Qt、不碰文件、不解码，可以用假时间线毫秒级测试。
"""
from __future__ import annotations

from . import timebase as tb

# 严重级别
ERROR = "error"      # 会出错：超出素材、时间线重叠、引用缺失
WARN = "warn"        # 观感问题：重复、节奏失衡、来源集中
INFO = "info"        # 提示：仅供参考

SEVERITY_ORDER = {ERROR: 0, WARN: 1, INFO: 2}


def check_timeline(timeline, *, target_duration=None, tolerance=None,
                   sources=None):
    """对最终时间线做全面自检。

    Args:
        timeline: ``timeline.create_timeline()`` 产物
        target_duration: 目标时长（秒）；不给则跳过时长检查
        tolerance: 允许的时长误差；不给则按 2 秒
        sources: 源素材字典 ``{id: {"path", "duration"}}``；
                 不给则从 ``timeline["sources"]`` 取

    Returns:
        dict:
            ``issues``   问题列表，每项含 level/code/title/detail/clip_index
            ``summary``  各等级计数
            ``ok``       是否没有 error/warn
            ``stats``    成片的统计量（时长、片段数、来源占比等）
    """
    issues = []
    tl = timeline or {}
    clips = list(tl.get("clips") or [])

    src_map = {}
    for s in (sources or {}).values() if isinstance(sources, dict) else []:
        src_map[int(s["id"])] = s
    for s in tl.get("sources") or []:
        try:
            src_map.setdefault(int(s["id"]), s)
        except (KeyError, TypeError, ValueError):
            continue

    stats = _collect_stats(clips, src_map)

    # ---- 1) 时长 ----
    if target_duration is not None:
        issues.extend(_check_duration(stats, float(target_duration),
                                      float(tolerance or 2.0)))

    # ---- 2) 片段级结构问题 ----
    issues.extend(_check_clips(clips, src_map, stats))

    # ---- 3) 重复 ----
    issues.extend(_check_duplicates(clips, stats))

    # ---- 4) 来源集中 ----
    issues.extend(_check_source_concentration(stats))

    # ---- 5) 节奏 ----
    issues.extend(_check_rhythm(clips))

    # ---- 6) 音乐 ----
    issues.extend(_check_music(tl, stats))

    summary = {ERROR: 0, WARN: 0, INFO: 0}
    for it in issues:
        summary[it["level"]] = summary.get(it["level"], 0) + 1

    issues.sort(key=lambda x: (SEVERITY_ORDER.get(x["level"], 9),
                               x.get("clip_index") or 0))

    return {
        "issues": issues,
        "summary": summary,
        "ok": summary[ERROR] == 0 and summary[WARN] == 0,
        "stats": stats,
    }


# ---------------------------------------------------------
# 统计
# ---------------------------------------------------------

def _collect_stats(clips, src_map):
    total = 0.0
    by_source = {}
    lengths = []
    for c in clips:
        try:
            d = float(c["timeline_end"]) - float(c["timeline_start"])
        except (KeyError, TypeError, ValueError):
            d = 0.0
        d = max(0.0, d)
        total += d
        lengths.append(d)
        sid = c.get("source_id")
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            sid = None
        if sid is not None:
            by_source[sid] = by_source.get(sid, 0.0) + d
    hot_sid = None
    hot_sec = 0.0
    for sid, sec in by_source.items():
        if sec > hot_sec:
            hot_sid, hot_sec = sid, sec
    return {
        "clip_count": len(clips),
        "total_duration": round(total, 3),
        "min_clip": round(min(lengths), 3) if lengths else 0.0,
        "max_clip": round(max(lengths), 3) if lengths else 0.0,
        "avg_clip": round(total / len(lengths), 3) if lengths else 0.0,
        "source_count": len(by_source),
        "source_seconds": {k: round(v, 3) for k, v in by_source.items()},
        "hot_source": hot_sid,
        "hot_source_share": (hot_sec / total) if total > 1e-6 else 0.0,
        "lengths": [round(x, 3) for x in lengths],
    }


# ---------------------------------------------------------
# 各项检查
# ---------------------------------------------------------

def _issue(level, code, title, detail, clip_index=None, suggestion=""):
    return {"level": level, "code": code, "title": title, "detail": detail,
            "clip_index": clip_index, "suggestion": suggestion}


def _check_duration(stats, target, tolerance):
    out = []
    total = stats["total_duration"]
    diff = total - target
    if abs(diff) <= tolerance:
        return out
    if diff < 0:
        out.append(_issue(
            WARN, "duration_short",
            "成片短于目标 %.2f 秒" % abs(diff),
            "目标 %.1f 秒（±%.1f），实际 %.1f 秒。"
            % (target, tolerance, total),
            suggestion="可用素材不足或片段下限太大；"
                       "可勾选「允许重复使用」、减小最短片段，或增加素材。"))
    else:
        out.append(_issue(
            WARN, "duration_long",
            "成片长于目标 %.2f 秒" % diff,
            "目标 %.1f 秒（±%.1f），实际 %.1f 秒。"
            % (target, tolerance, total),
            suggestion="最后一个片段超长导致；可减小「最长片段」。"))
    return out


def _check_clips(clips, src_map, stats):
    """片段级：越界、零长、时间线不连续。"""
    out = []
    prev_end = 0.0
    for i, c in enumerate(clips):
        try:
            ss = float(c["source_start"])
            se = float(c["source_end"])
            ts = float(c["timeline_start"])
            te = float(c["timeline_end"])
        except (KeyError, TypeError, ValueError):
            out.append(_issue(ERROR, "clip_malformed",
                              "第 %d 个片段缺少时间字段" % (i + 1),
                              "无法读取 source/timeline 起止时间。",
                              clip_index=i))
            continue

        if te <= ts:
            out.append(_issue(ERROR, "clip_zero_length",
                              "第 %d 个片段时长为 0" % (i + 1),
                              "时间线 %.3f → %.3f 秒。" % (ts, te),
                              clip_index=i))
        if se <= ss:
            out.append(_issue(ERROR, "clip_bad_source_range",
                              "第 %d 个片段取材区间无效" % (i + 1),
                              "素材内 %.3f → %.3f 秒。" % (ss, se),
                              clip_index=i))

        # 超出素材时长（剪映会黑帧或报错）
        sid = c.get("source_id")
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            sid = None
        src = src_map.get(sid) if sid is not None else None
        if src is not None:
            try:
                dur = float(src.get("duration") or 0.0)
            except (TypeError, ValueError):
                dur = 0.0
            if dur > 0 and se > dur + 0.05:
                out.append(_issue(
                    ERROR, "clip_exceed_source",
                    "第 %d 个片段超出素材时长" % (i + 1),
                    "取材到 %.3f 秒，素材只有 %.3f 秒（超出 %.3f）。"
                    % (se, dur, se - dur),
                    clip_index=i,
                    suggestion="剪映会黑帧或导入失败；"
                               "请把该片段结束时间收到素材时长以内。"))

        # 时间线不连续（中间有缝或重叠）
        if abs(ts - prev_end) > 0.01 and i > 0:
            gap = ts - prev_end
            out.append(_issue(
                ERROR if gap < -0.01 else WARN,
                "timeline_gap" if gap > 0 else "timeline_overlap",
                ("第 %d 个片段与前一段重叠 %.3f 秒" % (i + 1, -gap))
                if gap < 0 else
                ("第 %d 个片段前有空隙 %.3f 秒" % (i + 1, gap)),
                "上一段结束 %.3f 秒，本段开始 %.3f 秒。" % (prev_end, ts),
                clip_index=i))
        prev_end = max(prev_end, te)

    return out


def _check_duplicates(clips, stats):
    """同一区间被用多次。"""
    out = []
    seen = {}
    for i, c in enumerate(clips):
        try:
            key = (int(c.get("source_id")),
                   round(float(c["source_start"]), 2),
                   round(float(c["source_end"]), 2))
        except (KeyError, TypeError, ValueError):
            continue
        seen.setdefault(key, []).append(i)

    repeated = {k: v for k, v in seen.items() if len(v) > 1}
    if not repeated:
        return out

    # 汇总成一条（避免刷屏），给出最严重的那几处
    worst = sorted(repeated.items(), key=lambda kv: -len(kv[1]))[:3]
    detail = "；".join(
        "素材 %s 的 %.2f~%.2f 秒重复 %d 次"
        % (k[0], k[1], k[2], len(v)) for k, v in worst)
    extra = len(repeated) - len(worst)
    if extra > 0:
        detail += "；另有 %d 处" % extra
    first_idx = worst[0][1][0]
    out.append(_issue(
        WARN, "duplicate_ranges",
        "有 %d 个取材区间被重复使用" % len(repeated),
        detail,
        clip_index=first_idx,
        suggestion="若这是有意复用可忽略；否则勾选「素材不重复使用」"
                   "或提高多样性强度。"))
    return out


def _check_source_concentration(stats):
    out = []
    if stats["source_count"] <= 1 or stats["total_duration"] <= 1e-6:
        return out
    share = stats["hot_source_share"]
    fair = 1.0 / max(1, stats["source_count"])

    # 「显著超过公平份额」的判定。
    #
    # ⚠ 不能写成 share > fair * 2.0：只有 2 条素材时 fair=0.5，
    #   fair*2 = 1.0，占比永远不可能超过 1.0，这条检查等于失效。
    #   正确做法是「超过公平份额的 +N 个百分点」：
    #   2 条素材时 fair=50%，某个占到 75% 就算明显偏斜。
    excess = share - fair
    if share >= 0.5 and excess >= 0.25:
        out.append(_issue(
            WARN, "source_concentrated",
            "素材 %s 占了成片的 %.0f%%" % (stats["hot_source"], share * 100),
            "共 %d 条素材，理想均匀约 %.0f%%，超出 %.0f 个百分点。"
            % (stats["source_count"], fair * 100, excess * 100),
            suggestion="提高「多样性」强度，或开启时间覆盖（coverage）。"))
    return out


def _check_rhythm(clips):
    """节奏：连续碎镜头 / 连续超长镜头 / 时长过于单一。"""
    out = []
    lengths = []
    for c in clips:
        try:
            lengths.append(max(0.0, float(c["timeline_end"])
                               - float(c["timeline_start"])))
        except (KeyError, TypeError, ValueError):
            lengths.append(0.0)
    if len(lengths) < 4:
        return out

    # 连续过短（< 0.8 秒）的片段
    run = 0
    run_start = 0
    worst = (0, 0)
    for i, d in enumerate(lengths):
        if d < 0.8:
            if run == 0:
                run_start = i
            run += 1
            if run > worst[0]:
                worst = (run, run_start)
        else:
            run = 0
    if worst[0] >= 4:
        out.append(_issue(
            WARN, "rhythm_dense",
            "连续 %d 个不足 0.8 秒的碎镜头" % worst[0],
            "从第 %d 个片段开始，连续 %d 段都短于 0.8 秒，观感会很碎。"
            % (worst[1] + 1, worst[0]),
            clip_index=worst[1],
            suggestion="把「最短片段」调大，或换舒缓型节奏策略。"))

    # 时长过于单一（标准差极小）
    avg = sum(lengths) / len(lengths)
    if avg > 1e-6:
        var = sum((x - avg) ** 2 for x in lengths) / len(lengths)
        std = var ** 0.5
        if std / avg < 0.05 and len(lengths) >= 6:
            out.append(_issue(
                INFO, "rhythm_uniform",
                "所有片段长度几乎一样（%.2f±%.2f 秒）" % (avg, std),
                "缺少长短变化，观感偏机械。",
                suggestion="试试「长短交替」或「动态」节奏策略。"))
    return out


def _check_music(tl, stats):
    out = []
    music = tl.get("music") or {}
    if not music:
        return out
    try:
        m_dur = float(music.get("duration") or 0.0)
    except (TypeError, ValueError):
        m_dur = 0.0
    total = stats["total_duration"]
    if m_dur <= 0 or total <= 0:
        return out
    diff = total - m_dur
    if diff > 3.0:
        out.append(_issue(
            WARN, "music_short",
            "音乐短于成片 %.1f 秒" % diff,
            "成片 %.1f 秒，配乐只有 %.1f 秒，结尾会没有音乐。"
            % (total, m_dur),
            suggestion="换一段更长的音乐，或让算法循环补足。"))
    elif diff < -5.0:
        out.append(_issue(
            INFO, "music_long",
            "音乐比成片长 %.1f 秒" % (-diff),
            "成片 %.1f 秒，配乐 %.1f 秒。" % (total, m_dur),
            suggestion="多余部分会被裁掉，通常无影响。"))
    return out


# ---------------------------------------------------------
# 修复建议
# ---------------------------------------------------------

def suggest_fixes(report):
    """把自检结果整理成可读的修复清单。

    Returns:
        ``[{"code", "title", "action", "auto": bool}, ...]``
        ``auto=True`` 表示可以自动处理（例如裁掉超长片段），
        ``auto=False`` 需要用户决定（例如换素材）。
    """
    fixes = []
    seen = set()
    for it in report.get("issues") or []:
        code = it.get("code")
        if code in seen:
            continue
        seen.add(code)
        action = it.get("suggestion") or ""
        auto = code in ("clip_exceed_source", "duration_long")
        fixes.append({
            "code": code,
            "title": it.get("title") or "",
            "action": action,
            "auto": bool(auto and action),
        })
    return fixes
