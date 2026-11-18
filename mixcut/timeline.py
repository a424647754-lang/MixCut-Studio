"""
MixCut · 统一时间轴数据结构

这是 MixCut 自家的中间工程格式（JSON），描述“哪些源素材、各自保留哪几段、
在时间线上如何摆放”。它既可以被 project_builder 转成剪映可编辑草稿，也
可以被重新加载继续调整，而不是只输出一条成片。

Schema: mixcut-timeline/1.0
"""

import json
from pathlib import Path

SCHEMA_NAME = "mixcut-timeline"
SCHEMA_VERSION = "1.0"
SCHEMA_FULL = f"{SCHEMA_NAME}/{SCHEMA_VERSION}"

DEFAULT_FPS = 30


def r3(value):
    """统一保留 3 位小数，避免浮点噪声。"""
    return round(float(value), 3)


def create_canvas(width=1080, height=1920, fps=DEFAULT_FPS):
    return {"width": int(width), "height": int(height), "fps": int(fps)}


def create_timeline(canvas=None, mode="mix", created_from=None):
    """构造一个空时间轴字典。"""
    return {
        "app": "MixCut Studio",
        "schema": SCHEMA_FULL,
        "mode": mode,                      # 当前正式模式：mix（智能混剪）
        "canvas": canvas or create_canvas(),
        "sources": [],                     # 源素材清单
        "clips": [],                       # 时间线上的有效片段
        "junk": [],                        # 被剔除的废镜头（带类型）
        "music": None,                     # 配乐信息（节拍/闪避）
        "transitions": [],                 # 转场规则结果
        "created_from": created_from,      # 来源工程/素材目录，便于追溯
    }


def make_source(source_id, path, duration, name=None):
    return {
        "id": int(source_id),
        "path": str(path),
        "name": name or Path(path).name,
        "duration": r3(duration),
    }


def make_clip(clip_id, source_id, source_start, source_end,
              timeline_start, timeline_end, tags=None):
    return {
        "id": int(clip_id),
        "source_id": int(source_id),
        "source_start": r3(source_start),
        "source_end": r3(source_end),
        "timeline_start": r3(timeline_start),
        "timeline_end": r3(timeline_end),
        "tags": list(tags or []),
    }


def make_junk(source_id, start, end, types):
    return {
        "source_id": int(source_id),
        "start": r3(start),
        "end": r3(end),
        "types": list(types),
    }


def total_duration(timeline):
    """时间线总时长 = 最后一个片段的结束时间。"""
    if not timeline["clips"]:
        return 0.0
    return max(c["timeline_end"] for c in timeline["clips"])


def save_timeline(timeline, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def load_timeline(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "schema" not in data:
        raise ValueError("不是有效的 MixCut 时间轴文件。")
    # 基本字段兜底，兼容早期手工文件
    data.setdefault("sources", [])
    data.setdefault("clips", [])
    data.setdefault("junk", [])
    data.setdefault("music", None)
    data.setdefault("transitions", [])
    return data
