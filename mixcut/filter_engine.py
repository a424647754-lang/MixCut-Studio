# -*- coding: utf-8 -*-
"""
MixCut · 智能粗剪筛选引擎（外挂可选分支）

在不改动 amverge 主流程的前提下，按需调度多个检测器：
  amverge / PySceneDetect / VideoScope / FFmpeg(black+silence) / TransNetV2
融合多检测器结果，输出供智能混剪使用的统一素材分析结构。

设计约束：
  · 组件按需加载：未勾选不 import、不跑视频；
  · 单模块报错不中断：每个检测器独立 try/except，错误回传日志；
  · 时间统一为秒（浮点），对齐 ffmpeg 时间戳；
  · 不依赖 Qt，可独立测试。
"""

from __future__ import annotations

import hashlib
import importlib
import os
import tempfile
from functools import lru_cache
from pathlib import Path
import threading

from . import ffmpeg_tools as ft
from . import analysis as analysis_core
from . import timeline as tl


# ---------------------------------------------------------
# 配置与预设
# ---------------------------------------------------------

DEFAULT_FILTER_CONFIG = {
    "cpu_mode": False,                 # 默认开 NVDEC；探测失败自动回退软解
    "amverge_enable": True,            # 强制开启，不可关
    "scenedetect_enable": True,
    "videoscope_enable": False,
    "ffmpeg_audio_black_enable": True,
    "transnetv2_enable": False,
    "min_waste_duration": 0.3,         # 短于该值的废段丢弃
    "cut_merge_tolerance": 0.08,       # 切点去重容差（秒）
}

# TransNetV2：官方 TF 版导入名是 transnetv2，本项目实际安装的是 PyTorch 版
# transnetv2_pytorch（自带权重、CPU 即可跑）。自检按实际安装的包判断，
# 两个名字都探测不到时才标记“未安装”。
TRANSNET_MODULE = "transnetv2_pytorch"
TRANSNET_ALIASES = ("transnetv2_pytorch", "transnetv2")

# 组件元信息：key -> (对外中文名, 依赖导入名, 是否只输出切点)
COMPONENTS = {
    "amverge": ("amverge 静止空镜检测", "amverge", True),
    "scenedetect": ("PySceneDetect 自适应镜头校验", "scenedetect", True),
    "videoscope": ("VideoScope 画质缺陷检测", "videoscope", False),
    "ffmpeg_audio_black": ("FFmpeg 静音+黑屏检测", None, False),
    "transnetv2": ("TransNetV2 高阶镜头切变", TRANSNET_MODULE, True),
}


def preset_fast_batch():
    """快速批量：amverge + FFmpeg，关 VideoScope/TransNet，PySceneDetect 保留可选。"""
    return {
        **DEFAULT_FILTER_CONFIG,
        "amverge_enable": True,
        "ffmpeg_audio_black_enable": True,
        "scenedetect_enable": True,
        "videoscope_enable": False,
        "transnetv2_enable": False,
    }


def preset_fine_qc():
    """精细质检：可用组件全开（不可用的由 UI 置灰）。"""
    return {
        **DEFAULT_FILTER_CONFIG,
        "amverge_enable": True,
        "ffmpeg_audio_black_enable": True,
        "scenedetect_enable": True,
        "videoscope_enable": True,
        "transnetv2_enable": True,
    }


# ---------------------------------------------------------
# 依赖自检
# ---------------------------------------------------------

def _can_import(module_name):
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def _transnet_available():
    return any(_can_import(name) for name in TRANSNET_ALIASES)


@lru_cache(maxsize=1)
def self_check():
    """返回各组件可用性 + CUDA 可用性。结果缓存，UI 启动时调用一次。"""
    avail = {
        "amverge": _can_import("amverge"),
        "scenedetect": _can_import("scenedetect"),
        "videoscope": _can_import("videoscope"),
        "transnetv2": _transnet_available(),
        "ffmpeg": bool(ft.find_ffmpeg()),
        "cuda": False,
    }
    # CUDA：能找到 ffmpeg 即具备探测条件；具体到某素材由 resolve_gpu_mode 判定
    avail["cuda"] = bool(ft.find_ffmpeg())
    return avail


def missing_modules(config):
    """返回配置里想开、但依赖缺失的组件中文名列表（供 UI 提示）。"""
    avail = self_check()
    missing = []
    for key, (_label, module, _cuts_only) in COMPONENTS.items():
        if not config.get(f"{key}_enable", False):
            continue
        if module is not None and not avail.get(key, False):
            missing.append(_label)
        if key == "ffmpeg_audio_black" and not avail["ffmpeg"]:
            missing.append(_label)
    return missing


def force_self_check():
    """强制自检：清掉缓存后重新导入全部组件，返回 (avail, report_lines)。

    不依赖启动时的缓存结果，启动前调用可真实反映当前环境。
    """
    self_check.cache_clear()
    avail = self_check()
    report = []
    label_map = {
        "amverge": "amverge 静止空镜检测",
        "scenedetect": "PySceneDetect 自适应镜头校验",
        "videoscope": "VideoScope 画质缺陷检测",
        "transnetv2": "TransNetV2 高阶镜头切变",
        "ffmpeg": "FFmpeg 可执行程序",
        "cuda": "CUDA 硬件加速",
    }
    for key, label in label_map.items():
        report.append(f"[{'OK' if avail.get(key) else '缺失'}] {label}")
    return avail, report


# ---------------------------------------------------------
# 区间工具（带来源标签）
# ---------------------------------------------------------

def merge_tagged(intervals, gap=0.0):
    """合并重叠/相邻区间并合并来源标签。

    输入 [(start, end, tag), ...]；
    返回 [(start, end, [tag...]), ...]，按起点排序。
    """
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda x: x[0])
    out = []
    cs, ce, tags = ordered[0][0], ordered[0][1], {ordered[0][2]}
    for s, e, tag in ordered[1:]:
        if s <= ce + gap:
            ce = max(ce, e)
            tags.add(tag)
        else:
            out.append((cs, ce, sorted(tags)))
            cs, ce, tags = s, e, {tag}
    out.append((cs, ce, sorted(tags)))
    return out


def merge_cuts(cuts, tolerance=0.08):
    """合并时间相近的切点并合并来源。

    输入 [(time, type, source), ...]；
    返回 [(time, type, [source...]), ...]。
    """
    if not cuts:
        return []
    ordered = sorted(cuts, key=lambda x: x[0])
    out = []  # [time, type, set(sources)]
    for t, typ, src in ordered:
        if out and t - out[0 if False else -1][0] <= tolerance:
            pt, ptype, psrc = out[-1]
            # 非普通 cut 的类型优先保留（fadein/fadeout/flash）
            ntype = typ if ptype == "cut" else ptype
            out[-1] = (pt, ntype, psrc | {src})
        else:
            out.append((t, typ, {src}))
    return [(t, typ, sorted(srcs)) for t, typ, srcs in out]


# ---------------------------------------------------------
# 检测器适配器（按需 import，异常隔离）
# ---------------------------------------------------------

def _amverge_cuts(path, min_keep):
    from amverge.core.detection.keyframe import detect_scenes_by_keyframe
    raw = detect_scenes_by_keyframe(path, min_duration=min_keep)
    cuts = []
    for s, _e in raw or []:
        s = float(s)
        if s > 0:
            cuts.append((s, "cut", "amverge"))
    return cuts


def _amverge_waste(path):
    """amverge 输出的是切点（True），不是废段；这里只保留接入点。

    静止空镜的判定实际上由 ffmpeg 的 freezedetect 完成，amverge 不产出
    任何废段，因此这个函数永远返回空列表。
    """
    return []


def _scenedetect_cuts(path, cancel_check=None):
    """场景切点检测：用新的三轮自适应采样引擎（NVDEC 优先）。

    同时粗采帧顺带算出黑屏/静止区间，存到 _scene_cache 供 run_filter 复用，
    这样 detect_all 只跑音频 silencedetect，不再逐帧软解视频。
    cancel_check 透传给场景引擎，保证取消/关闭时能立即终止 ffmpeg 子进程。
    """
    from . import scene_analyzer as sa
    try:
        duration = ft.probe_duration(path)
    except Exception:
        duration = None
    result = sa.analyze(path, duration=duration, options={"gpu": False},
                        cancel_check=cancel_check)
    _scene_cache[path] = result
    return [(float(t), "cut", "scenedetect") for t, _c in result.get("cuts", [])]


# 场景检测结果缓存：path -> scene_analyzer.analyze() 返回值
_scene_cache = {}


# TransNetV2 模型只加载一次（约 30MB 权重 + 数百毫秒），之后复用。
_TRANSNET_MODEL = None
_TRANSNET_LOCK = threading.Lock()
_TRANSNET_THRESHOLD = 0.5


def _ensure_ffmpeg_on_path():
    """TransNetV2 通过 ffmpeg-python 调用 ffmpeg.exe，依赖 PATH。

    本程序自带 ffmpeg 但不在 PATH 里，这里把它的目录临时补进去，
    避免“模型装好却因为找不到 ffmpeg 而失败”。
    """
    ffmpeg = ft.find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，TransNetV2 无法抽帧")
    folder = str(Path(ffmpeg).parent)
    current = os.environ.get("PATH", "")
    if folder.lower() not in [p.lower() for p in current.split(os.pathsep)]:
        os.environ["PATH"] = folder + os.pathsep + current


def _get_transnet_model():
    global _TRANSNET_MODEL
    if _TRANSNET_MODEL is not None:
        return _TRANSNET_MODEL
    with _TRANSNET_LOCK:
        if _TRANSNET_MODEL is not None:
            return _TRANSNET_MODEL
        from transnetv2_pytorch import TransNetV2
        _ensure_ffmpeg_on_path()
        device = "cpu"
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
        except Exception:
            pass
        model = TransNetV2(device=device)
        model.eval()
        _TRANSNET_MODEL = model
        return model


def _transnetv2_cuts(path):
    """TransNetV2 镜头切变识别：返回切点 [(time, "cut", "transnetv2"), ...]。

    只在整段解码后用神经网络判定每个镜头边界，对闪白、快速运镜等
    PySceneDetect 容易漏掉的切变更稳。
    """
    model = _get_transnet_model()
    scenes = model.detect_scenes(str(path), threshold=_TRANSNET_THRESHOLD)
    cuts = []
    for scene in scenes[1:]:
        start = float(scene.get("start_time", 0.0))
        if start > 0:
            cuts.append((start, "cut", "transnetv2"))
    return cuts


def _videoscope_waste(path, duration):
    from videoscope.domain import VideoMetadata
    from videoscope.detectors import (
        AnalysisContext,
        DetectorRunner,
        create_builtin_detector_registry,
    )

    p = Path(path)
    digest = hashlib.sha256()
    with p.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    sha = digest.hexdigest()
    details = ft.probe_details(path)
    v = details.get("video") or {}
    a = details.get("audio")
    fps = float(v.get("fps") or 30.0)
    meta = VideoMetadata(
        filename=p.name,
        container_format=details.get("format", {}).get("format_name") or "mp4",
        codec=v.get("codec") or "h264",
        width=int(v.get("width") or 640),
        height=int(v.get("height") or 360),
        duration_seconds=float(duration),
        average_frame_rate=fps,
        estimated_frame_count=int(round(duration * fps)),
        has_audio=a is not None,
        file_size_bytes=int(details.get("file_size") or p.stat().st_size),
    )
    with tempfile.TemporaryDirectory(prefix="vscope_") as workspace_dir:
        context = AnalysisContext(
            input_path=p, input_hash=sha, metadata=meta,
            workspace=Path(workspace_dir),
        )
        runner = DetectorRunner(create_builtin_detector_registry())
        result = runner.run(context)
        return [
            (
                float(f.time_range.start_seconds),
                float(f.time_range.end_seconds),
                "videoscope",
            )
            for f in result.findings
        ]


def _ffmpeg_waste(path, duration, progress_cb, cancel_check,
                  ffmpeg_options, force_mode):
    # 复用现有黑屏/静止/静音检测，按用户选择传硬件模式与阈值
    detected = ft.detect_all(
        path, ffmpeg_options, duration=duration,
        progress_cb=progress_cb, cancel_check=cancel_check,
        force_mode=force_mode,
    )
    out = []
    video_on = ffmpeg_options.get("video_detect", True)
    audio_on = ffmpeg_options.get("audio_detect", True)
    if video_on and ffmpeg_options.get("detect_black", True):
        for s, e in detected.get("black", []):
            out.append((float(s), float(e), "blackdetect"))
    if video_on and ffmpeg_options.get("detect_freeze", True):
        for s, e in detected.get("freeze", []):
            out.append((float(s), float(e), "freeze"))
    if audio_on:
        silence = detected.get("silence", [])
        # 空音轨（全程 -91dB 那种）不当作废片：画面照常可用。
        # 判定逻辑与 analysis.is_silent_track 保持一致，避免两条路径行为不同。
        if not analysis_core.is_silent_track(silence, duration):
            for s, e in silence:
                out.append((float(s), float(e), "silencedetect"))
    return out


# ---------------------------------------------------------
# 主流程
# ---------------------------------------------------------

def cache_key_config(config):
    """返回一份“只用于缓存键”的筛选配置。

    这里面包含了所有会改变检测结果的参数（组件开关、阈值、CPU/GPU），
    同时也包含素材自己的画面/静音检测开关，避免两套开关各算一套却
    共用同一个缓存。进度回调之类不影响结果的键不会被包含（run_filter
    只认固定字段，多余的键会被忽略）。
    """
    cfg = dict(config or {})
    cfg.pop("progress_cb", None)
    cfg.pop("cancel_check", None)
    return cfg


def _emit(progress_cb, fraction, message):
    if progress_cb:
        progress_cb(max(0.0, min(1.0, fraction)), message)


def run_filter(paths, config=None, progress_cb=None, cancel_check=None):
    """对多个素材跑筛选引擎，返回统一结构（含新字段）。

    progress_cb(fraction: float, message: str)
    返回：
      {
        "detect_engine": {...},
        "waste_segments": [{start,end,level,source}],
        "shot_cuts": [{time,type,source}],
        "sources": {path: {duration, waste, cuts, errors:[...]}}
      }
    """
    config = {**DEFAULT_FILTER_CONFIG, **(config or {})}
    avail = self_check()
    min_waste = float(config["min_waste_duration"])
    cut_tol = float(config["cut_merge_tolerance"])

    # 硬件模式：cpu_mode=True 强制 CPU，否则尝试 GPU（不满足由 detect_all 回退）
    force_mode = "cpu" if config["cpu_mode"] else "gpu"
    # ffmpeg 检测选项：默认阈值 + 上层传入的开关/阈值
    ffmpeg_options = {**analysis_core.DEFAULT_OPTIONS, **(config.get("ffmpeg_options") or {})}

    # 开启且实际可用的组件（不可用的自动跳过并记录）
    enabled = {
        "amverge": config["amverge_enable"] and avail["amverge"],
        "scenedetect": config["scenedetect_enable"] and avail["scenedetect"],
        "videoscope": config["videoscope_enable"] and avail["videoscope"],
        "ffmpeg_audio_black":
            config["ffmpeg_audio_black_enable"] and avail["ffmpeg"],
        "transnetv2": config["transnetv2_enable"] and avail["transnetv2"],
    }

    result_sources = {}
    all_waste, all_cuts = [], []
    total = max(1, len(paths))

    for idx, path in enumerate(paths):
        if cancel_check and cancel_check():
            break
        errors = []
        duration = ft.probe_duration(path)
        waste, cuts = [], []
        base = idx / total
        name = Path(path).name

        def _sub(pct, msg, base=base):
            _emit(progress_cb, base + pct / total, msg)

        # 1) amverge 切点
        if enabled["amverge"]:
            _sub(0.0, f"[{name}] amverge 镜头检测")
            try:
                cuts += _amverge_cuts(path, analysis_core.DEFAULT_OPTIONS["min_keep_duration"])
            except Exception as exc:
                errors.append(f"amverge: {type(exc).__name__} {exc}")

        # 2) PySceneDetect 切点
        if enabled["scenedetect"]:
            if cancel_check and cancel_check():
                break
            _sub(0.15, f"[{name}] PySceneDetect 镜头校验")
            try:
                cuts += _scenedetect_cuts(path, cancel_check)
            except Exception as exc:
                errors.append(f"scenedetect: {type(exc).__name__} {exc}")

        # 3) TransNetV2 切点（只出切点）
        if enabled["transnetv2"]:
            _sub(0.3, f"[{name}] TransNetV2 切变识别")
            try:
                cuts += _transnetv2_cuts(path)
            except Exception as exc:
                errors.append(f"transnetv2: {type(exc).__name__} {exc}")

        # 4) VideoScope 画质废段
        if enabled["videoscope"]:
            if cancel_check and cancel_check():
                break
            _sub(0.45, f"[{name}] VideoScope 画质检测")
            try:
                waste += _videoscope_waste(path, duration)
            except Exception as exc:
                errors.append(f"videoscope: {type(exc).__name__} {exc}")

        # 5) FFmpeg 静音检测 + 场景引擎粗采帧的黑屏/静止
        if enabled["ffmpeg_audio_black"]:
            if cancel_check and cancel_check():
                break
            _sub(0.55, f"[{name}] FFmpeg 静音检测")
            try:
                def _ff_pct(pct):
                    _sub(0.55 + 0.4 * (max(0, min(100, int(pct))) / 100.0),
                         f"[{name}] FFmpeg 检测 {int(pct)}%")
                # 场景引擎粗采帧已经算了黑屏/静止区间，直接复用，不再让
                # detect_all 逐帧软解视频；只跑音频 silencedetect。
                scene = _scene_cache.get(path) or {}
                for s, e in scene.get("black_intervals", []):
                    waste.append((float(s), float(e), "blackdetect"))
                for s, e in scene.get("freeze_intervals", []):
                    waste.append((float(s), float(e), "freeze"))
                audio_only = dict(ffmpeg_options)
                audio_only["video_detect"] = False
                audio_only["detect_black"] = False
                audio_only["detect_freeze"] = False
                waste += _ffmpeg_waste(
                    path, duration, _ff_pct, cancel_check,
                    audio_only, force_mode,
                )
            except Exception as exc:
                errors.append(f"ffmpeg: {type(exc).__name__} {exc}")

        # 本素材内融合
        merged_waste = merge_tagged(waste, gap=analysis_core.DEFAULT_OPTIONS["merge_gap"])
        merged_cuts = merge_cuts(cuts, tolerance=cut_tol)

        src_waste = []
        for s, e, tags in merged_waste:
            if e - s < min_waste:
                continue
            level = "confirm" if len(tags) >= 2 else "risky"
            seg = {"start": round(float(s), 3), "end": round(float(e), 3),
                   "level": level, "source": tags}
            src_waste.append(seg)
            all_waste.append((s, e, tags))

        src_cuts = []
        for t, typ, srcs in merged_cuts:
            cut = {"time": round(float(t), 3), "type": typ, "source": srcs}
            src_cuts.append(cut)
            all_cuts.append((t, typ, srcs))

        result_sources[path] = {
            "duration": duration,
            "waste_segments": src_waste,
            "shot_cuts": src_cuts,
            "errors": errors,
        }
        _emit(progress_cb, (idx + 1) / total, f"[{name}] 检测完成")

    # 全局再融合（跨素材不合并，这里仅做分级汇总）
    global_waste = []
    for s, e, tags in merge_tagged(
        [(s, e, t) for s, e, tags in all_waste for t in tags]
    ):
        if e - s < min_waste:
            continue
        global_waste.append({
            "start": round(float(s), 3), "end": round(float(e), 3),
            "level": "confirm" if len(tags) >= 2 else "risky",
            "source": tags,
        })

    global_cuts = [
        {"time": round(float(t), 3), "type": typ, "source": srcs}
        for t, typ, srcs in merge_cuts(
            [(t, typ, s) for t, typ, srcs in all_cuts for s in srcs],
            tolerance=cut_tol,
        )
    ]

    _emit(progress_cb, 1.0, "筛选引擎完成")
    return {
        "detect_engine": {
            "cpu_mode": bool(config["cpu_mode"]),
            "amverge_enable": enabled["amverge"],
            "scenedetect_enable": enabled["scenedetect"],
            "videoscope_enable": enabled["videoscope"],
            "transnetv2_enable": enabled["transnetv2"],
            "ffmpeg_audio_black_enable": enabled["ffmpeg_audio_black"],
        },
        "waste_segments": global_waste,
        "shot_cuts": global_cuts,
        "sources": result_sources,
    }


# ---------------------------------------------------------
# 转换为智能混剪使用的统一素材分析结构
# ---------------------------------------------------------

def collect_errors(filter_result):
    """汇总所有素材上的检测器错误，返回去重后的 ["组件: 错误", ...]。

    检测器单个失败不会中断任务，但必须让用户看得见；后台线程用它把
    “有组件没跑起来”的情况写进日志。
    """
    seen = []
    for src in (filter_result or {}).get("sources", {}).values():
        for err in src.get("errors") or []:
            text = str(err)
            if text not in seen:
                seen.append(text)
    return seen


def to_mix_analysis(paths, filter_result):
    """把引擎结果转成 mixbuilder 已消费的 analysis 列表。

    输出混剪引擎使用的 source / _junk_merged / _scenes 结构。
    ``analysis.valid_segments_for`` 会把 `_scenes` 的每个起点当作镜头边界，
    与废段求补集后切分；因此这里只需要给出切点，不必拼成完整区间。
    """
    analysis = []
    for idx, path in enumerate(paths):
        src = filter_result["sources"].get(path)
        if src is None:
            continue
        duration = src["duration"]
        junk = [
            (w["start"], w["end"], set(w["source"]))
            for w in src["waste_segments"]
        ]
        # 切点还原成镜头边界（切点后面属于新镜头）
        cuts = sorted({float(c["time"]) for c in src["shot_cuts"]
                       if 0.0 < float(c["time"]) < duration})
        scenes = [(t, None) for t in cuts] or None
        analysis.append({
            "source": tl.make_source(idx, path, duration),
            "junk": [tl.make_junk(idx, s, e, sorted(t)) for s, e, t in junk],
            "_junk_merged": junk,
            "_scenes": scenes,
        })
    return analysis
