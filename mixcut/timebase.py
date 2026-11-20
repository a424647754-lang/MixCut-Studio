# -*- coding: utf-8 -*-
"""时间基准的统一契约。

问题
====
项目里同时存在三种「秒」，名字都叫 ``time`` / ``fps``，含义却不同：

| 基准 | 怎么来的 | 误差 | 能用于剪辑吗 |
|---|---|---|---|
| **A 源时间轴** | ffprobe 时长 / 关键帧 PTS / 镜头切点 | 0 | **能，且只能用这个** |
| **B 采样序号** | ``index / fps``（fps 是**采样频率**，不是视频帧率） | 实测 0~27ms | **不能** |
| **C 视频帧号** | ``frame_no`` | 需乘真实帧率才是秒 | 不能 |

2026-11-20 审计结论：当前代码**没有**把 B/C 当 A 用——
两处 B/C 只用于分析弹窗显示（``analyze_page`` 的 ``frames_detail["time"]``
和 ``frame_pipe.iter_frames`` 的 timestamp）。所以剪辑没有错位。

但这只是「恰好没人误用」，不是设计保证。后来的代码完全可能
把 ``frames_detail`` 里的 time 直接拿去当剪辑点，引入累积误差
（25fps 下 27ms = 0.67 帧，多个片段叠加就看得见）。

本模块不改变现有数据结构，只提供：
  · 三个明确命名的构造函数
  · 一个「从采样序号估算源时间」的显式换算（带误差上限说明）
  · 一个校验函数，用来在测试里断言「剪辑只用了基准 A」
"""
from __future__ import annotations

# 基准标识。字符串而不是 Enum，便于直接写进 dict / JSON / 日志。
TIMEBASE_SOURCE = "source_axis"    # A：源视频时间轴（秒），可剪辑
TIMEBASE_SAMPLE = "sample_index"   # B：分析采样序号换算的秒，仅供显示
TIMEBASE_FRAME = "frame_index"     # C：视频帧号，需换算

# 采样时间戳相对源时间轴的最大偏差（实测值，见台账第二十一阶段）。
# 25fps 素材实测 0.0267 秒；这里留一点余量。
SAMPLE_TIME_MAX_ERROR = 0.05


class TimebaseError(ValueError):
    """把非源时间轴的时间当作剪辑时间使用时抛出。"""


def source_time(seconds, *, origin=""):
    """基准 A：源视频时间轴上的秒。**剪辑唯一允许使用的时间。**

    Args:
        seconds: 秒数
        origin: 来源说明（检测器名 / ffprobe 等），便于排错
    """
    return {"t": float(seconds), "tb": TIMEBASE_SOURCE,
            "origin": str(origin)}


def sample_time(index, sample_fps, *, origin="frame_pipe"):
    """基准 B：由采样序号推算的秒。**只能用于界面显示。**

    ``sample_fps`` 是**采样频率**（如 0.6 = 每 5 秒 3 帧），
    不是视频帧率。用它算出来的时间与源时间轴存在
    ``SAMPLE_TIME_MAX_ERROR`` 量级的偏差，因此标记为不可剪辑。

    Args:
        index: 第几个采样点（从 0 开始）
        sample_fps: 采样频率
    """
    fps = float(sample_fps or 0.0)
    if fps <= 0:
        raise ValueError("sample_fps 必须为正数，收到 %r" % sample_fps)
    return {"t": int(index) / fps, "tb": TIMEBASE_SAMPLE,
            "origin": str(origin)}


def frame_time(frame_no, video_fps, *, origin="frame_index"):
    """基准 C：视频帧号换算的秒。**不能直接当剪辑时间。**

    换算本身是精确的（帧号 / 帧率），但前提是 ``video_fps`` 是该素材的
    真实帧率。可变帧率（VFR）素材上这个换算会失真，因此仍标记为不可剪辑，
    需要调用方确认真实帧率后再转成 :func:`source_time`。

    Args:
        frame_no: 绝对帧号
        video_fps: 视频真实帧率
    """
    fps = float(video_fps or 0.0)
    if fps <= 0:
        raise ValueError("video_fps 必须为正数，收到 %r" % video_fps)
    return {"t": int(frame_no) / fps, "tb": TIMEBASE_FRAME,
            "origin": str(origin)}


def assert_clippable(value, *, where=""):
    """断言一个时间值可以用于剪辑。测试里用，运行时也可用于兜底。

    Raises:
        TimebaseError: 该时间不是源时间轴
    """
    if not isinstance(value, dict):
        raise TimebaseError(
            "%s 期望带基准标记的时间结构，收到 %r" % (where, type(value)))
    tb = value.get("tb")
    if tb != TIMEBASE_SOURCE:
        raise TimebaseError(
            "%s 使用了非源时间轴的时间（tb=%r）；"
            "采样序号/帧号换算的秒不能用于剪辑" % (where, tb))
    return float(value["t"])


def to_source_seconds(value, video_fps=None, *, where=""):
    """把一个带基准标记的时间**显式**转成源时间轴的秒。

    只有两种情况允许转换：
      · 本来就是基准 A → 直接返回
      · 基准 C（帧号）+ 明确的真实帧率 → 精确换算
    基准 B **永远不允许**当剪辑时间用——它本身就是近似值，
    转换只会把近似包装成精确，掩盖问题。
    """
    if isinstance(value, dict):
        tb = value.get("tb")
        if tb == TIMEBASE_SOURCE:
            return float(value["t"])
        if tb == TIMEBASE_FRAME:
            if not video_fps:
                raise TimebaseError(
                    "%s 帧号换算成源时间需要真实帧率" % where)
            return float(value["t"])
        raise TimebaseError(
            "%s 不能把 %r 转成剪辑时间（采样时间是近似值）" % (where, tb))
    raise TimebaseError("%s 收到无基准标记的时间：%r" % (where, value))


def describe():
    """给界面/文档用的一段说明。"""
    return (
        "时间基准：剪辑只使用「源视频时间轴」（ffprobe/PTS/镜头切点）；"
        "分析弹窗里的采样时间由采样序号换算，"
        "与源时间轴存在最多 %.0f ms 偏差，仅用于显示。" %
        (SAMPLE_TIME_MAX_ERROR * 1000)
    )
