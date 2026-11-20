# -*- coding: utf-8 -*-
"""分析结果的统一契约。

为什么需要这个模块
==================
在 2026-11-20 的审计里发现，项目里有**三套**互不相认的 ``analysis item`` 生产点：

| 生产点 | 产出字段 |
|---|---|
| ``analysis.analyze_source()`` | source / junk / _junk_merged / _quality_segments / _silent_track |
| ``filter_engine.to_mix_analysis()`` | source / junk / _junk_merged / _scenes |
| ``workers._pool_stubs()`` | source（**手写 dict**）/ junk / _quality_segments |

后果：
· ``_quality_segments`` 只有前两者有，``_scenes`` 只有中间那个有；
· ``workers`` 的 ``source`` 是手写的，少了 ``name`` 字段；
· 混剪引擎对三者各写一套 if 分支，任一处改动都可能悄悄失效。

本模块只做一件事：**定义一个所有生产点都必须满足的结构**，
并提供构造函数与校验函数。它不负责分析，不负责决策，也不碰 Qt。

设计原则
--------
1. **只统一、不裁掉**。每个生产点原有的字段全部保留，这里只是把
   「必须有」和「可选」说清楚，缺的补默认值。
2. **允许 None 表示「没测过」**。``_scenes=None`` 与 ``_scenes=[]`` 语义不同：
   前者是「镜头检测没跑」，后者是「跑了但没找到切点」。
   这条与 ``verdict_integrity`` 是同一个教训（空列表是合法答案）。
3. **不引入新的时间基准类型**。时间仍用秒（float），
   统一的是「这个秒数代表源视频时间轴上的位置」这条约定。
"""
from __future__ import annotations

# 结构版本：任何会改变字段语义的改动都要 +1。
# 缓存与分析结果里记录它，便于未来迁移。
ANALYSIS_SCHEMA = 1

# 所有生产点都必须提供的键
REQUIRED_KEYS = ("source", "junk", "_junk_merged")

# 可选键及其默认值。
# 注意 _scenes / _quality_segments 的默认值是 None 而不是 []：
#   None = 该检测没跑
#   []   = 跑了但结果为空
# 两者不能混为一谈，否则「没测」会被读成「测了没问题」。
OPTIONAL_DEFAULTS = {
    "_quality_segments": None,
    "_scenes": None,
    "_sentences": None,
    "_sentence_ends": None,
    "_silent_track": False,
    "exclusions": None,
    "shot_cuts": None,
}

# source 子结构必须有的键
SOURCE_KEYS = ("id", "path", "name", "duration")


def make_source(source_id, path, duration, name=None):
    """构造 ``source`` 子结构（与 ``timeline.make_source`` 保持一致）。

    单独在这里再写一份的原因：``workers`` 之前是手写 dict，漏了 ``name``，
    而下游有按 ``source["name"]`` 显示的地方。这里作为唯一入口，
    保证三套生产点产出的 source 结构相同。
    """
    from pathlib import Path
    return {
        "id": int(source_id),
        "path": str(path),
        "name": name or Path(str(path)).name,
        "duration": round(float(duration or 0.0), 3),
    }


def make_item(source, *, junk=None, junk_merged=None, schema=None,
              **extra):
    """构造一个符合契约的 analysis item。

    Args:
        source: ``make_source()`` 的返回值，或等价的 dict
        junk: 已切好的废段（``timeline.make_junk`` 结构）
        junk_merged: ``[(start, end, types), ...]``，供 ``_junk_tuples`` 直接使用
        extra: 其它可选字段（``_scenes`` / ``_quality_segments`` / ...）

    Returns:
        完整 item；缺失的可选字段按 ``OPTIONAL_DEFAULTS`` 补齐。
    """
    junk = list(junk or [])
    if junk_merged is None:
        junk_merged = [(float(j["start"]), float(j["end"]),
                        set(j.get("types") or ()))
                       for j in junk]

    item = {
        "source": source,
        "junk": junk,
        "_junk_merged": junk_merged,
        "_analysis_schema": ANALYSIS_SCHEMA if schema is None else int(schema),
    }
    for key, default in OPTIONAL_DEFAULTS.items():
        item[key] = default
    for key, value in extra.items():
        item[key] = value
    return item


def normalize(item, schema=None):
    """把一个「野生」item 补齐成契约结构。

    · 缺 source.name 的补上（``workers`` 手写 dict 的情况）
    · 缺可选字段的补 ``OPTIONAL_DEFAULTS``
    · **不覆盖已有值**，包括已有的 None
    """
    if not isinstance(item, dict):
        raise TypeError("analysis item 必须是 dict，收到 %r" % type(item))

    out = dict(item)

    src = out.get("source")
    if not isinstance(src, dict):
        raise ValueError("analysis item 缺少 source 子结构")
    fixed_src = dict(src)
    if not fixed_src.get("name"):
        from pathlib import Path
        fixed_src["name"] = Path(str(fixed_src.get("path") or "")).name
    fixed_src["id"] = int(fixed_src.get("id") or 0)
    fixed_src["duration"] = round(float(fixed_src.get("duration") or 0.0), 3)
    out["source"] = fixed_src

    out.setdefault("junk", [])
    if "_junk_merged" not in out:
        out["_junk_merged"] = [
            (float(j["start"]), float(j["end"]), set(j.get("types") or ()))
            for j in out["junk"]
        ]

    for key, default in OPTIONAL_DEFAULTS.items():
        out.setdefault(key, default)

    out.setdefault("_analysis_schema",
                   ANALYSIS_SCHEMA if schema is None else int(schema))
    return out


def check(item):
    """校验一个 item 是否满足契约，返回问题列表（空列表 = 合格）。

    只检查结构，不检查数值合理性——那是各模块自己的事。
    """
    problems = []
    if not isinstance(item, dict):
        return ["item 不是 dict：%r" % type(item)]

    for key in REQUIRED_KEYS:
        if key not in item:
            problems.append("缺少必需字段 %s" % key)

    src = item.get("source")
    if not isinstance(src, dict):
        problems.append("source 不是 dict")
    else:
        for key in SOURCE_KEYS:
            if key not in src:
                problems.append("source 缺少 %s" % key)
        if "duration" in src and not isinstance(src["duration"], (int, float)):
            problems.append("source.duration 不是数字")

    jm = item.get("_junk_merged")
    if jm is not None and not isinstance(jm, (list, tuple)):
        problems.append("_junk_merged 不是列表")

    # 区分「没测过」和「测了是空」
    for key in ("_scenes", "_quality_segments", "shot_cuts"):
        if key in item and item[key] is not None:
            if not isinstance(item[key], (list, tuple)):
                problems.append("%s 存在但不是列表（应为 list 或 None）" % key)

    return problems


def is_measured(item, key):
    """某个可选检测是否跑过。

    ``None`` → 没跑过；其它（包括空列表）→ 跑过。
    调用方据此决定「要不要提示重新分析」，而不是把空结果当成正常。
    """
    if not isinstance(item, dict):
        return False
    return item.get(key) is not None


def merge_items(base, extra):
    """把后一次分析的结果合并进前一次（增量补测场景）。

    规则：``extra`` 里**非 None** 的字段覆盖 ``base``；
    ``None`` 表示这次没测，不覆盖已有的实测值。
    """
    out = dict(base or {})
    for key, value in (extra or {}).items():
        if value is None and out.get(key) is not None:
            continue
        out[key] = value
    return out
