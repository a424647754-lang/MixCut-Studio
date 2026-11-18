# -*- coding: utf-8 -*-
"""MixCut · GPU 引擎桥（进程内 sd_fp.dll + 外置 CUDA 引擎 exe）

背景
----
MixCut 自己用的是 pip 版 OpenCV（cv2 5.0.0），它不带 CUDA 模块，
`cv2.cuda.getCudaEnabledDeviceCount()` 恒为 0，所以画面侧的 GPU 能力一直受限。

引擎（sd_fp / sd_asr / sd_dedup）是用 CUDA 13.5 + OpenCV CUDA 模块编译的，
本模块把它接进来，只借用其中两件与本项目相关的能力：

  · transcribe()   语音转写 → 返回整句时间戳（供"整句切割"用），并落一份 SRT
  · fingerprint()  CUDA 提取视频指纹（phash / 色彩直方图 / 运动 / 音频 chromaprint）

指纹提取优先走**进程内** sd_fp.dll（ctypes 直调，不开子进程、不解析 stdout）；
DLL 缺失或初始化失败时自动回退到 sd_dedup.exe，结果与 fp.json 逐字段一致。
设置环境变量 ``MIXCUT_FP_INPROC=0`` 可强制走外置进程。

指纹缓存：文件内记录源素材签名（路径 + 大小 + mtime_ns），命中前校验；
签名不符或缓存损坏自动重算。落盘只保留比较逻辑实际用到的字段
（duration / keyframe_ts / phash_frames / color_hist），motion、场景边界、
chromaprint、clip_embedding 不再保存。

相似度比较不在这里做进程级比较，而是由 audio_dedup.py 直接读指纹 JSON 计算，
避免每条素材对都起一次进程（那会为 N² 次比较反复初始化 CUDA）。

引擎查找顺序（找不到就安静降级，不阻断混剪流程）：
  1. 环境变量 ``MIXCUT_GPU_ENGINE``
  2. 项目内 ``engine/bin``

对外接口
--------
  available()        -> bool            引擎是否可用
  describe()         -> dict            自检信息（路径、缺失的 exe/DLL）
  transcribe(...)    -> (ok, segments)  语音转写
  fingerprint(...)   -> dict | None     CUDA 视频指纹
  cache_path(...)    -> Path            各类派生文件的缓存位置
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

# Windows 下不弹控制台窗口
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# 引擎 exe 名字：转写、指纹
ASR_EXE = "sd_asr.exe"
DEDUP_EXE = "sd_dedup.exe"
# 进程内指纹模块（与 exe 同目录，由 engine/cpp 的 sd_fp 目标编译）
INPROC_DLL = "sd_fp.dll"

# 单次子进程的默认超时（秒）。语音转写按整条素材算，给足时间。
DEFAULT_ASR_TIMEOUT = 3600
DEFAULT_FP_TIMEOUT = 1200


# ---------------------------------------------------------
# 定位与自检
# ---------------------------------------------------------

def _candidate_dirs():
    """可能存放引擎 exe 的目录（按优先级）。"""
    dirs = []
    env = os.environ.get("MIXCUT_GPU_ENGINE", "").strip()
    if env:
        dirs.append(Path(env))
    here = Path(__file__).resolve().parent
    dirs.append(here.parent / "engine" / "bin")
    return dirs


_ENGINE_DIR_CACHE = {"done": False, "dir": None}


def engine_dir():
    """返回找到的引擎目录（Path），找不到返回 None。结果会缓存。"""
    if _ENGINE_DIR_CACHE["done"]:
        return _ENGINE_DIR_CACHE["dir"]
    found = None
    for cand in _candidate_dirs():
        try:
            if cand.is_dir() and (cand / ASR_EXE).exists():
                found = cand
                break
        except OSError:
            continue
    _ENGINE_DIR_CACHE["done"] = True
    _ENGINE_DIR_CACHE["dir"] = found
    return found


def exe_path(name):
    d = engine_dir()
    if d is None:
        return None
    p = d / name
    return p if p.exists() else None


def available():
    return engine_dir() is not None and exe_path(DEDUP_EXE) is not None


def describe():
    """自检信息：给界面/日志用，说明 GPU 引擎是否就位。"""
    d = engine_dir()
    exes = {}
    for name in (ASR_EXE, DEDUP_EXE):
        exes[name] = bool(d and (d / name).exists())
    return {
        "available": bool(d),
        "dir": str(d) if d else "",
        "exe": exes,
        "dll": bool(d and (d / INPROC_DLL).exists()),
        "fp_backend": fp_backend(),
        "hint": (
            "" if d else
            "未找到 CUDA 引擎目录：可设置环境变量 MIXCUT_GPU_ENGINE 指向 "
            "sd_asr.exe / sd_dedup.exe 所在目录；找不到时相关功能会自动跳过。"
        ),
    }


# ---------------------------------------------------------
# 进程内指纹（sd_fp.dll，ctypes 直调，不起子进程）
# ---------------------------------------------------------
# 说明：sd_fp.dll 与 sd_dedup.exe 用的是同一份 FingerprintExtractor，
# 参数相同，产出 JSON 与 fp.json 逐字段一致（见 regression/p5 对比）。
# 只在“加载/初始化失败”时回退到 exe；不允许静默返回空结果。

_INPROC = {"done": False, "lib": None, "error": ""}


def inproc_enabled():
    """是否允许走进程内指纹（MIXCUT_FP_INPROC=0 可强制走外置 exe）。"""
    return os.environ.get("MIXCUT_FP_INPROC", "1").strip() != "0"


def _ensure_tool_env():
    """给进程内调用准备 ffmpeg / ffprobe / fpcalc 路径。

    DLL 里的引擎按“exe 同级 / tools / 仓库 ffmpeg\\bin”找工具；被 Python
    进程内加载时“exe 同级”是 python.exe 目录，所以这里显式注入环境变量
    （引擎侧已支持宽字符读取，中文路径安全）。已存在的环境变量不覆盖。
    """
    d = engine_dir()
    if d is None:
        return
    project = d.parent.parent  # engine\bin -> 项目根
    ffmpeg_bin = project / "ffmpeg" / "bin"
    candidates = {
        "SD_FFMPEG": [d / "ffmpeg.exe", d / "tools" / "ffmpeg.exe",
                      ffmpeg_bin / "ffmpeg.exe"],
        "SD_FFPROBE": [d / "ffprobe.exe", d / "tools" / "ffprobe.exe",
                       ffmpeg_bin / "ffprobe.exe"],
        "SD_FPCALC": [d / "fpcalc.exe", d / "tools" / "fpcalc.exe",
                      ffmpeg_bin / "fpcalc.exe"],
    }
    for name, paths in candidates.items():
        if os.environ.get(name):
            continue
        for p in paths:
            try:
                if p.is_file():
                    os.environ[name] = str(p)
                    break
            except OSError:
                continue


def _inproc_lib():
    """加载 sd_fp.dll 并预热 CUDA。失败返回 None（原因见 _inproc_error）。"""
    if _INPROC["done"]:
        return _INPROC["lib"]
    _INPROC["done"] = True
    if not inproc_enabled():
        _INPROC["error"] = "已通过 MIXCUT_FP_INPROC=0 关闭进程内指纹"
        return None
    d = engine_dir()
    dll = (d / INPROC_DLL) if d is not None else None
    if dll is None or not dll.exists():
        _INPROC["error"] = f"未找到 {INPROC_DLL}"
        return None
    try:
        import ctypes

        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            # OpenCV CUDA / CUDA Runtime 的 DLL 都在引擎目录里
            os.add_dll_directory(str(d))
        _ensure_tool_env()
        lib = ctypes.WinDLL(str(dll))
        lib.sd_fp_init.restype = ctypes.c_int
        lib.sd_fp_init.argtypes = []
        lib.sd_fp_extract.restype = ctypes.c_void_p
        lib.sd_fp_extract.argtypes = [ctypes.c_char_p]
        lib.sd_fp_last_error.restype = ctypes.c_char_p
        lib.sd_fp_last_error.argtypes = []
        # 必须按原始指针释放：若 restype 用 c_char_p，ctypes 会把返回值拷成
        # Python bytes，拿那个拷贝去 free 会踩坏堆（0xC0000374）。
        lib.sd_fp_free.argtypes = [ctypes.c_void_p]
        lib.sd_fp_free.restype = None
        if lib.sd_fp_init() != 0:
            err = lib.sd_fp_last_error()
            _INPROC["error"] = "CUDA 初始化失败: " + (err.decode("utf-8", "replace") if err else "未知错误")
            return None
        _INPROC["lib"] = lib
        return lib
    except (OSError, AttributeError, ValueError) as exc:
        _INPROC["error"] = f"{type(exc).__name__}: {exc}"
        return None


def _inproc_error():
    return _INPROC["error"]


def fp_backend():
    """当前指纹后端：'inproc' / 'exe' / ''（都不可用）。

    刻意不在这里加载 DLL / 初始化 CUDA：这个方法会被界面自检频繁调用，
    真正的加载与预热推迟到第一次提指纹时。
    """
    if _INPROC["lib"] is not None:
        return "inproc"
    d = engine_dir()
    if (d is not None and (d / INPROC_DLL).exists() and inproc_enabled()):
        return "inproc"
    return "exe" if exe_path(DEDUP_EXE) is not None else ""


def _extract_inproc(video_path):
    """进程内提指纹，返回指纹 dict；不可用/失败返回 None。"""
    lib = _inproc_lib()
    if lib is None:
        return None
    import ctypes

    raw = lib.sd_fp_extract(ctypes.c_char_p(str(video_path).encode("utf-8")))
    if not raw:
        err = lib.sd_fp_last_error()
        _INPROC["error"] = err.decode("utf-8", "replace") if err else "提取失败"
        return None
    try:
        text = ctypes.string_at(raw).decode("utf-8", "replace")
    finally:
        lib.sd_fp_free(ctypes.c_void_p(raw))
    try:
        data = json.loads(text)
    except ValueError:
        _INPROC["error"] = "sd_fp 返回的 JSON 解析失败"
        return None
    if not isinstance(data, dict) or any(
            data.get(k) in (None, "", [], {}) for k in FP_FIELDS_REQUIRED):
        _INPROC["error"] = "sd_fp 返回的指纹缺少关键特征"
        return None
    return data


# ---------------------------------------------------------
# 缓存位置
# ---------------------------------------------------------

def _safe_name(text, limit=72):
    """把任意字符串压成可用于文件名的片段。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(text)).strip("_")
    return (safe or "item")[:limit]


def cache_dir(kind):
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    root = Path(base) / "MixCut" / "cache" / kind
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_path(kind, key, ext=""):
    """派生文件缓存路径（asr / fp）。

    名称 = 素材名（可读）+ 路径摘要（防不同文件夹同名素材撞车）。
    放用户级缓存目录，打包成 exe 后也不会往只读目录里写。
    """
    digest = hashlib.md5(str(key).encode("utf-8", "replace")).hexdigest()[:10]
    stem = _safe_name(Path(str(key).split("|")[0]).stem)
    return cache_dir(kind) / f"{stem}_{digest}{ext}"


def _legacy_cache_file(kind, old_name):
    """早期版本只用素材名做缓存文件名，命中旧文件时直接复用，避免重复转写。"""
    p = cache_dir(kind) / old_name
    return p if p.exists() else None


# ---------------------------------------------------------
# 指纹缓存正确性（文件签名校验）
# ---------------------------------------------------------
# 缓存文件内记录源素材签名（路径 + 大小 + mtime_ns）。
# 命中前先校验：签名不符（素材被覆盖/替换）或缓存损坏（JSON 读不了、
# 关键特征缺失）一律重算，避免沿用旧指纹把不同片子判成重复。
FP_CACHE_VERSION = 2

# 指纹里 MixCut Python 比较逻辑（audio_dedup.fingerprint_similarity）只用到：
#   duration / keyframe_ts / phash_frames / color_hist
# 其余字段引擎虽会算，但本项目比较不使用，落盘时不再保存（不影响判定）。
FP_FIELDS_KEEP = ("video_id", "path", "duration", "fps",
                  "keyframe_ts", "phash_frames", "color_hist")
FP_FIELDS_REQUIRED = ("duration", "keyframe_ts", "phash_frames", "color_hist")


def source_signature(video_path):
    """源素材签名：路径 + 文件大小 + mtime_ns；文件缺失时记 missing。"""
    p = str(video_path)
    try:
        stat = os.stat(p)
    except OSError:
        return {"path": p, "missing": True}
    return {
        "path": p,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _signature_matches(meta, sig):
    """缓存里记录的签名与当前源文件是否一致。"""
    if not isinstance(meta, dict) or sig.get("missing"):
        return False
    return (
        meta.get("path") == sig.get("path")
        and int(meta.get("size") or -1) == int(sig.get("size") or -2)
        and int(meta.get("mtime_ns") or -1) == int(sig.get("mtime_ns") or -2)
    )


def _read_fp_cache(fp_path, sig):
    """读取并校验指纹缓存。

    返回 (data, state)：
      state = "hit"      签名一致，可直接用
      state = "adopt"    旧缓存（无签名）且缓存文件比源素材新，采纳后回写签名
      state = "stale"    签名不符（素材被替换/修改）
      state = "broken"   文件读不了 / 关键特征缺失
    """
    try:
        data = json.loads(Path(fp_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "broken"
    if not isinstance(data, dict) or any(
            data.get(k) in (None, "", [], {}) for k in FP_FIELDS_REQUIRED):
        return None, "broken"

    meta = data.get("_cache")
    if _signature_matches(meta, sig):
        return data, "hit"

    if not meta:
        # 旧缓存没有签名：缓存文件比源素材新，说明缓存反映的就是当前文件，可采纳。
        try:
            if os.stat(fp_path).st_mtime_ns >= int(sig.get("mtime_ns") or 0):
                return data, "adopt"
        except OSError:
            pass
    return None, "stale"


def _write_fp_cache(fp_path, data, sig):
    """回写指纹缓存：只留比较用得到的字段 + 源素材签名。

    先写临时文件再原子替换，避免中途失败留下半截 JSON。
    引擎仍会算 motion/chromaprint 等维度（解码侧优化不在本阶段范围），
    这里只是不再把它们落盘。
    """
    slim = {k: data[k] for k in FP_FIELDS_KEEP if k in data}
    slim["_cache"] = {
        "version": FP_CACHE_VERSION,
        "path": sig.get("path"),
        "size": sig.get("size"),
        "mtime_ns": sig.get("mtime_ns"),
    }
    tmp = Path(str(fp_path) + ".tmp")
    try:
        tmp.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, fp_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
    return slim


# ---------------------------------------------------------
# 子进程调用
# ---------------------------------------------------------

def _run(args, timeout=None):
    """执行引擎命令，合并 stdout/stderr 返回 (code, text)。"""
    if not args:
        return -1, ""
    cmd = [str(a) for a in args]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "")


# ---------------------------------------------------------
# 语音转写
# ---------------------------------------------------------

_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def parse_srt(text):
    """解析 SRT → [{start, end, text}]，时间为秒（浮点）。"""
    segments = []
    if not text:
        return segments
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        match = _SRT_TIME.search(block)
        if not match:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in match.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
        # 时间行之后的所有行都是字幕正文
        idx = next(
            (i for i, ln in enumerate(lines) if "-->" in ln), None
        )
        body = " ".join(lines[idx + 1:]) if idx is not None else ""
        if end <= start:
            continue
        segments.append({"start": start, "end": end, "text": body.strip()})
    return segments


def transcribe(video_path, model="small", language="zh", timeout=None):
    """调用 sd_asr 转写一条素材。

    返回 (ok, segments, srt_path, message)。segments 为 [{start,end,text}]。
    转写结果按素材路径缓存在本地，同一条素材不会重复转写。
    """
    video_path = str(video_path)
    exe = exe_path(ASR_EXE)
    if exe is None:
        return False, [], None, "未找到 sd_asr.exe（CUDA 引擎目录缺失）"

    srt_path = cache_path("asr", f"{video_path}|{model}|{language}", ".srt")
    if not srt_path.exists():
        legacy = _legacy_cache_file(
            "asr", f"{_safe_name(Path(video_path).stem)}_{model}_{language}.srt")
        if legacy is not None:
            srt_path = legacy
    if not srt_path.exists():
        code, out = _run(
            [exe, "transcribe", "-i", video_path, "-o", str(srt_path),
             "--model", model, "--language", language],
            timeout=timeout or DEFAULT_ASR_TIMEOUT,
        )
        if code != 0 or not srt_path.exists():
            tail = "\n".join(out.strip().splitlines()[-3:])
            return False, [], None, f"转写失败（rc={code}）：{tail}"

    try:
        text = srt_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, [], None, f"读取字幕失败：{exc}"
    segments = parse_srt(text)
    if not segments:
        return False, [], srt_path, "字幕为空（素材可能没有人声）"
    return True, segments, srt_path, f"转写完成：{len(segments)} 句"


# ---------------------------------------------------------
# 指纹（CUDA）
# ---------------------------------------------------------

def fingerprint(video_path, timeout=None, force=False):
    """CUDA 提取视频指纹，返回指纹字典（失败返回 None）。

    缓存命中条件：路径 + 文件大小 + mtime_ns 与缓存内签名一致。
    签名不符（素材被覆盖/替换）、缓存损坏、关键特征缺失时自动重算；
    旧版本按"素材名 + 路径摘要"命名的无签名缓存，只有在缓存文件比
    源素材新时才采纳，并随即补写签名。
    """
    video_path = str(video_path)
    sig = source_signature(video_path)
    if sig.get("missing"):
        return None

    fp_path = cache_path("fp", video_path, ".json")
    candidates = [fp_path]
    for name in (f"{_safe_name(Path(video_path).stem)}.json",
                 f"{_safe_name(Path(video_path).stem)}"
                 f"_{hashlib.md5(video_path.encode('utf-8', 'replace')).hexdigest()[:10]}.json"):
        legacy = cache_dir("fp") / name
        if legacy.exists() and legacy != fp_path:
            candidates.append(legacy)

    if not force:
        for cand in candidates:
            data, state = _read_fp_cache(cand, sig)
            if data is None:
                continue
            if state == "adopt":
                return _write_fp_cache(cand, data, sig)
            return data

    # 1) 优先进程内（sd_fp.dll）：不起子进程、不用解析 stdout
    data = _extract_inproc(video_path)
    if data is not None:
        return _write_fp_cache(fp_path, data, sig)

    # 2) 兜底：外置 sd_dedup.exe（DLL 缺失/初始化失败时走这里）
    exe = exe_path(DEDUP_EXE)
    if exe is None:
        return None
    code, out = _run(
        [exe, "fingerprint", video_path, "-o", str(fp_path)],
        timeout=timeout or DEFAULT_FP_TIMEOUT,
    )
    if code != 0 or not fp_path.exists():
        return None

    data, state = _read_fp_cache(fp_path, sig)
    if data is None:
        return None
    return _write_fp_cache(fp_path, data, sig)
