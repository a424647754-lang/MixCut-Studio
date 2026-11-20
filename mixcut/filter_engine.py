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
from . import analysis_schema
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
    """返回各组件可用性 + CUDA 可用性。结果缓存，UI 启动时调用一次。

    ``cuda`` 一项的含义
    ------------------
    指「**推理框架**能不能用 GPU」，也就是 ``torch.cuda.is_available()``。
    这是 UI 给用户看的「有没有 GPU 加速推理」的依据。

    ⚠ 历史 BUG（2026-11-20 修正）
    旧实现写的是 ``avail["cuda"] = bool(ft.find_ffmpeg())``——
    **有 ffmpeg 就报 CUDA 可用**。ffmpeg 是否存在与 GPU 毫无关系，
    结果是：装了 CPU 版 torch 的机器上 UI 显示「CUDA 可用」，
    而 TransNetV2 / OpenCLIP 实际全在 CPU 上跑，用户以为有加速。

    注意区分两个概念，不要再混：
    · **torch CUDA**（本字段）：神经网络推理是否用 GPU
    · **OpenCV CUDA / NVDEC**：视频解码与图像处理是否用 GPU
      这两条互相独立：本项目可以「OpenCV 走 CUDA 硬解 + torch 走 CPU」。
      引擎侧的 CUDA 状态由 ``gpu_engine.describe()`` 单独报告。
    """
    avail = {
        "amverge": _can_import("amverge"),
        "scenedetect": _can_import("scenedetect"),
        "videoscope": _can_import("videoscope"),
        "transnetv2": _transnet_available(),
        "ffmpeg": bool(ft.find_ffmpeg()),
        # 真实探测，不再是「有 ffmpeg 就算有」
        "cuda": _torch_cuda_available(),
        # 单独报告 torch 是否为 CPU-only 构建，便于 UI 说清「为什么没有加速」
        "torch_cpu_only": _torch_is_cpu_only(),
    }
    return avail


def _torch_cuda_available():
    """torch 是否真的能用 CUDA。torch 未安装或 CPU-only 构建都返回 False。"""
    try:
        import torch
    except Exception:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _torch_is_cpu_only():
    """torch 是否是 CPU-only 构建（用来给用户一句明确的解释）。

    ``torch.version.cuda is None`` 表示这个 wheel 根本没编进 CUDA 支持，
    换机器/换驱动都救不了，只能重装对应版本的 torch。
    """
    try:
        import torch
    except Exception:
        return False
    try:
        return getattr(torch.version, "cuda", None) is None
    except Exception:
        return False


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
        "cuda": "CUDA 硬件加速（神经网络推理）",
    }
    for key, label in label_map.items():
        report.append(f"[{'OK' if avail.get(key) else '缺失'}] {label}")
    # 给一句明确的解释，而不是让用户对着「缺失」自己猜：
    # CPU-only 的 torch 换驱动也救不了，只能重装对应 CUDA 版本的 wheel。
    if not avail.get("cuda") and avail.get("torch_cpu_only"):
        report.append("      当前 PyTorch 为 CPU-only 构建，"
                      "TransNetV2 / OpenCLIP 走 CPU 推理（结果相同，速度较慢）")
    elif not avail.get("cuda"):
        report.append("      未检测到可用的 CUDA（PyTorch 未安装或不可用）")
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
    """amverge 画面内容镜头切换检测（Canny 边缘余弦相似度）。

    ⚠ 历史问题（2026-11-20 修正）
    ----------------------------
    旧实现调用 ``detect_scenes_by_keyframe()``，返回的是**编码器关键帧(I 帧)时间戳**，
    不是画面切换点。I 帧间隔由 GOP 决定，与画面内容无关：
    实测 GOP=5s 的素材，切点精确落在 5/10/15/20/25/30，
    且这些位置的前后帧差异与「随机位置」几乎相同（1.44 倍基线）。

    现改为 ``detect_cuts_by_edge()``：在关键帧窗口内用 Canny 边缘图的
    平均池化余弦不相似度找硬切。同一素材实测差异比 **3.37 倍基线**，
    切点真正对应画面变化。

    参数选择的依据（实测，见台账第二十阶段）
    ----------------------------------------
    · ``radius=0.6``（amverge 默认）：质量最好（3.37x）。
      窗口只覆盖每个 GOP 中心附近 1.2 秒，但 I 帧本身就是编码器认为
      「值得独立编码」的位置，真实硬切绝大多数落在 I 帧附近。
    · ``radius=2.5``（全片无缝）：切点数 34，但掺入大量弱信号
      （前后差异 8~11 的「轻微变化」），差异比降到 2.34x。
      **宁可漏检也不误检**，因此不用全覆盖半径。

    保留关键帧能力
    --------------
    ``_amverge_keyframe_cuts()`` 保留原实现，供「需要无损切割点」的场景
    显式调用；两者语义不同，不再混用。

    Returns:
        ``[(time, "cut", "amverge"), ...]``，与其它检测器结构一致。
    """
    from amverge.core.detection.edge import detect_cuts_by_edge
    # 内部用固定的最小间隔做二次合并。
    #
    # 为什么不直接用调用方传进来的 min_keep：
    # run_filter 传的是 analysis.DEFAULT_OPTIONS["min_keep_duration"]=0.05 秒，
    # 那是「片段最短时长」的概念，不是「切点最小间隔」。
    # 用它当切点间隔会放出大量 0.13 秒间隔的密集切点（实测 40 个），
    # 显著拉低可信度（1.64x 基线，强切点仅 10/20）。
    # 见台账第二十阶段。
    merge_gap = max(float(min_keep or 0.0), _AMVERGE_MIN_CUT_GAP)
    raw = detect_cuts_by_edge(
        path,
        threshold=_AMVERGE_EDGE_THRESHOLD,
        radius=_AMVERGE_EDGE_RADIUS,
        blocksize=_AMVERGE_EDGE_BLOCKSIZE,
        min_duration=merge_gap,
    )
    cuts = []
    for t in raw or []:
        t = float(t)
        if t > 0:
            cuts.append((t, "cut", "amverge"))
    # 用真实像素差复核，过滤边缘噪声造成的误检（见 _amverge_verify_cuts）
    return _amverge_verify_cuts(path, cuts)


def _amverge_verify_cuts(path, cuts):
    """用真实像素差复核边缘检测结果，过滤噪声误检。

    为什么需要这一步
    ----------------
    ``detect_cuts_by_edge`` 用的是「Canny 边缘图的池化余弦不相似度」。
    有噪点/纹理的画面里，单帧噪声就能让边缘图轻微抖动、余弦值越过阈值，
    产生误检。实测合成素材：10 个切点里 8 个的前后帧真实差异 < 2（纯噪声）。

    边缘不相似度衡量「边缘结构变化」，用户关心「画面是否真的换了」，
    两者不完全等价。所以这里再用**原始灰度帧平均绝对差**复核一次。

    这是**降低误检**，不是增加切点——宁可漏掉极弱转场，
    也不把噪声当成镜头切换。

    Returns:
        过滤后的切点；取帧失败或复核异常时**保留原切点**，不让检测整体失败。
    """
    if not cuts:
        return cuts
    try:
        import av
        import numpy as np
    except Exception:
        return cuts

    kept = []
    try:
        with av.open(str(path)) as probe:
            fps = float(probe.streams.video[0].average_rate or 25.0)
    except Exception:
        return cuts
    half = max(1.0 / max(fps, 1.0), 0.02)

    def grab(t):
        """取 >= t 的第一帧。

        每次重新 open 而不是复用同一个 container：
        连续在同一 container 上 seek 两次取 t±half，PyAV 的解码器状态
        会互相干扰，实测两次会取到同一帧、差异恒为 0，
        结果把真实切点也当成噪声滤掉。
        """
        try:
            with av.open(str(path)) as c2:
                st2 = c2.streams.video[0]
                c2.seek(int(max(0.0, t) * 1_000_000),
                        any_frame=False, backward=True)
                for fr in st2.decode(st2):
                    if fr.time is None:
                        continue
                    if float(fr.time) >= t:
                        return fr.reformat(width=320, height=180,
                                           format="gray").to_ndarray()
        except Exception:
            return None
        return None

    try:
        for item in cuts:
            t = float(item[0])
            a = grab(max(0.0, t - half))
            b = grab(t + half)
            if a is None or b is None or a.shape != b.shape:
                kept.append(item)      # 取不到帧不判死，保留
                continue
            d = float(np.abs(a.astype(np.int16) -
                             b.astype(np.int16)).mean())
            if d >= _AMVERGE_MIN_PIXEL_DIFF:
                kept.append(item)
    except Exception:
        return cuts        # 复核出错退回未过滤结果

    # 二次过滤：去掉「密集弱切点」簇。
    #
    # 实测：某些图案（高频网格/强纹理）会让边缘图持续抖动，
    # 检测器在中段连续报出 15.08/15.68/16.32/16.96... 这类等距切点，
    # 个个差异只有 0.5~1.0（真实硬切是 13~30）。
    # 这类点在「间隔很近 + 差异很低」两个条件同时成立时必然是噪声，
    # 直接合并掉，只保留其中差异最大的那个代表。
    if len(kept) >= 2:
        kept = _amverge_drop_noise_clusters(path, kept, grab, half)
    return kept


def _amverge_drop_noise_clusters(path, cuts, grab, half):
    """把「密集 + 弱」的切点簇合并成一个代表点。

    判定规则（两条同时满足才算噪声簇）：
      1. 相邻切点间隔 < ``_AMVERGE_NOISE_GAP``（默认 1.0 秒）
      2. 该切点的画面差异 < ``_AMVERGE_NOISE_DIFF``（默认 4.0）

    保留簇内差异最大的那个点，其余丢弃。这样：
      · 真实切点附近偶尔出现的 1~2 个弱切点会被合并，不会重复出片
      · 真实的密集快切（差异大）不受影响
    """
    scored = []
    for item in cuts:
        t = float(item[0])
        a = grab(max(0.0, t - half))
        b = grab(t + half)
        d = 0.0
        if a is not None and b is not None and a.shape == b.shape:
            d = float(np.abs(a.astype(np.int16) -
                             b.astype(np.int16)).mean())
        scored.append((t, d, item))

    result = []
    group = []

    def flush(grp):
        if not grp:
            return
        # 簇内差异最大的作为代表
        best = max(grp, key=lambda x: x[1])
        result.append(best[2])

    for entry in scored:
        t, d, _item = entry
        is_weak = d < _AMVERGE_NOISE_DIFF
        if not is_weak:
            flush(group)
            group = []
            result.append(entry[2])
            continue
        if group and (t - group[-1][0]) >= _AMVERGE_NOISE_GAP:
            flush(group)
            group = []
        group.append(entry)
    flush(group)

    result.sort(key=lambda x: float(x[0]))
    return result


def _amverge_keyframe_cuts(path, min_keep):
    """【保留】关键帧切分点：按 I 帧时间戳切，保证可无损 stream-copy。

    这是 amverge 的原始能力，与「镜头切换检测」语义不同：
    · 本函数回答「在哪切能不重编码」
    · ``_amverge_cuts()`` 回答「画面在哪变了」

    默认流程不再使用它，保留供需要无损切点时显式调用。
    """
    from amverge.core.detection.keyframe import detect_scenes_by_keyframe
    raw = detect_scenes_by_keyframe(path, min_duration=min_keep)
    cuts = []
    for s, _e in raw or []:
        s = float(s)
        if s > 0:
            cuts.append((s, "keyframe", "amverge"))
    return cuts


# amverge 边缘检测参数（实测调优，见台账第二十阶段）
# threshold: 余弦不相似度阈值。0.15 能命中真实切点；再高会漏检（0.35 时 2/3）
_AMVERGE_EDGE_THRESHOLD = 0.15
# radius: 每个关键帧窗口半径。0.6 质量最优（切点/基线 3.37x）；
#         放大到 2.5 全片无缝反而掺入弱信号（2.34x），故不用
_AMVERGE_EDGE_RADIUS = 0.6
# blocksize: 边缘图池化块。7 比 3 更能压制噪声（切点 19→10，命中仍 3/3）
_AMVERGE_EDGE_BLOCKSIZE = 7
# 切点最小间隔（秒）：比这更近的切点合并成一个
_AMVERGE_MIN_CUT_GAP = 0.5
# 复核门槛：切点前后两帧的灰度平均绝对差低于此值 → 判定为噪声误检，丢弃。
# 实测真实硬切差异 13~30，噪声误检 0.5~2，取 5.0 作为分界。
_AMVERGE_MIN_PIXEL_DIFF = 5.0
# 噪声簇判定：相邻弱切点间隔小于此值（秒）→ 视为同一簇
_AMVERGE_NOISE_GAP = 1.0
# 噪声簇判定：画面差异低于此值 → 视为「弱切点」
_AMVERGE_NOISE_DIFF = 4.0


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

    **检测器版本标记**：``_DETECTOR_REVISION`` 参与缓存键。
    2026-11-20 把 amverge 从「关键帧切分」换成「边缘内容检测」，
    同样的配置现在会产生不同的 ``shot_cuts``。不把这个版本写进键里，
    用户会读到旧算法算出的切点，界面看起来正常但结果是错的。
    """
    cfg = dict(config or {})
    cfg.pop("progress_cb", None)
    cfg.pop("cancel_check", None)
    cfg["_detector_revision"] = _DETECTOR_REVISION
    return cfg


# 检测算法版本：改动任何「会产生不同 shot_cuts 结果」的检测逻辑时 +1。
# 作用：让基于旧算法的缓存自动失效（见 cache_key_config）。
#   v1 = amverge 用关键帧时间戳（错误：那是 GOP，不是画面切换）
#   v2 = amverge 用 Canny 边缘余弦相似度（2026-11-20）
_DETECTOR_REVISION = 2


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
        analysis.append(analysis_schema.make_item(
            tl.make_source(idx, path, duration),
            junk=[tl.make_junk(idx, s, e, sorted(t)) for s, e, t in junk],
            junk_merged=junk,
            # 本路径跑了镜头检测：即使没找到切点，也是「测过且为空」，
            # 与 analysis.analyze_source 的 None（没测过）语义不同。
            _scenes=scenes,
            _quality_segments=None,     # 本路径不跑画质窗口
            shot_cuts=[float(c["time"]) for c in src["shot_cuts"]],
        ))
    return analysis
