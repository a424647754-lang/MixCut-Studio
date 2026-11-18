# -*- coding: utf-8 -*-
"""MixCut · OpenCLIP 嵌入桥（GPU 优先，CPU 自动降级）

设计前提
--------
主环境是 torch 2.15.0+cpu，没有 CUDA；为了不动主环境，OpenCLIP 的推理跑在
独立虚拟环境 ``<工程根>\\OpenCLIP\\venv``（torch 2.14.1+cu130，RTX 4060）。
本模块在主环境里**不 import torch**，只负责拼 job、起子进程、读回向量，
所以即使独立环境缺失或没装 CUDA，主环境也不会被拖垮。

对外接口
--------
  status()                   -> dict          环境 / 模型 / GPU 状态
  available()                -> bool          独立环境是否就绪
  load_model(force=False)    -> dict          确保模型加载完成（含耗时）
  encode_images(paths)       -> list[vec]     图片 → 768 维（GPU 优先，带缓存）
  encode_text(queries)       -> list[vec]     文本 → 768 维
  cosine(a, b)               -> list[list]    余弦相似度矩阵
  search(paths, query, top)  -> list          文字检索素材
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE / "openclip_worker.py"
PROJECT_ROOT = HERE.parent                      # 工程根目录
LEGACY_CLIP_ROOT = Path(r"D:\mixcut源文件\OpenCLIP")   # 迁移前的旧位置（兼容保留）


def _default_clip_root():
    """优先工程内 OpenCLIP/，其次打包目录旁、旧外部目录；都没有就用工程内路径。"""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "OpenCLIP")
        candidates.append(PROJECT_ROOT / "OpenCLIP")
    else:
        candidates.append(PROJECT_ROOT / "OpenCLIP")
    candidates.append(PROJECT_ROOT.parent / "OpenCLIP")
    candidates.append(LEGACY_CLIP_ROOT)
    for cand in candidates:
        try:
            if (cand / "venv" / "Scripts" / "python.exe").is_file() \
                    or (cand / "open_clip_pytorch_model.bin").is_file():
                return cand
        except OSError:
            continue
    return candidates[0]


DEFAULT_CLIP_ROOT = _default_clip_root()
CKPT = Path(os.environ.get(
    "MIXCUT_OPENCLIP_CKPT",
    str(DEFAULT_CLIP_ROOT / "open_clip_pytorch_model.bin"),
))
DEFAULT_VENV_PY = Path(os.environ.get(
    "MIXCUT_OPENCLIP_PY",
    str(DEFAULT_CLIP_ROOT / "venv" / "Scripts" / "python.exe"),
))
ARCH = "ViT-L-14"
DIM = 768
TIMEOUT = 600
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_STATE = {"model": None, "probed": False, "why": ""}


# ---------------------------------------------------------
# 定位
# ---------------------------------------------------------

def venv_python():
    """独立环境解释器（env MIXCUT_OPENCLIP_PY 可覆盖）。找不到返回 None。"""
    env = os.environ.get("MIXCUT_OPENCLIP_PY", "").strip()
    candidates = [Path(env)] if env else []
    candidates.append(DEFAULT_VENV_PY)
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    root = Path(base) / "MixCut" / "cache" / "clip"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_file(path, cache_key=None):
    try:
        ckpt_stat = CKPT.stat()
        model_sig = f"{ckpt_stat.st_size}:{ckpt_stat.st_mtime_ns}"
    except OSError:
        model_sig = "missing"
    identity = f"{cache_key if cache_key is not None else path}|{ARCH}|{DIM}|{model_sig}"
    key = hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:24]
    return cache_dir() / f"{key}.json"


def _run(job, timeout=TIMEOUT):
    """起 venv 子进程跑一次 job，返回 (ok, dict|str)。"""
    py = venv_python()
    if py is None:
        return False, "未找到 OpenCLIP 独立环境（venv/Scripts/python.exe）"
    if not WORKER.exists():
        return False, f"缺少执行端：{WORKER}"
    if not CKPT.exists():
        return False, f"缺少权重：{CKPT}"

    tmp = Path(tempfile.mkdtemp(prefix="mixcut_clip_"))
    job_path, out_path = tmp / "job.json", tmp / "out.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

    try:
        env = os.environ.copy()
        env["MIXCUT_OPENCLIP_CKPT"] = str(CKPT)
        proc = subprocess.run(
            [str(py), str(WORKER), str(job_path), str(out_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=_NO_WINDOW, env=env,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or "").strip()[-500:]
        if not out_path.exists():
            return False, "执行端没有产出结果"
        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"结果解析失败：{type(exc).__name__}: {exc}"
        if not data.get("ok"):
            return False, data.get("error") or "执行端返回失败"
        return True, data
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------
# 状态与加载
# ---------------------------------------------------------

def load_model(force=False):
    """确认模型能在独立环境里加载并跑通一次；返回状态字典（含耗时）。"""
    if _STATE["model"] is not None and not force:
        return _STATE["model"]

    t0 = time.time()
    ok, res = _run({"action": "status"}, timeout=TIMEOUT)
    elapsed = round(time.time() - t0, 2)

    py = venv_python()
    info = {
        "available": bool(ok),
        "loaded": bool(ok and res.get("load_ms") is not None or ok),
        "device": (res.get("device") if ok else None),
        "gpu": (res.get("gpu") if ok else None),
        "torch": (res.get("torch") if ok else None),
        "arch": (res.get("arch") if ok else ARCH),
        "dim": (res.get("dim") if ok else DIM),
        "ckpt": str(CKPT),
        "ckpt_mb": round(CKPT.stat().st_size / 1024 / 1024) if CKPT.exists() else None,
        "venv_python": str(py) if py else None,
        "probe_s": elapsed,
        "cache_count": len(list(cache_dir().glob("*.json"))),
    }
    if ok:
        info["model_ms"] = res.get("model_ms")
        info["cuda"] = res.get("device") == "cuda"
    else:
        info["reason"] = str(res)

    _STATE["model"] = info
    _STATE["probed"] = True
    _STATE["why"] = "" if ok else str(res)
    return info


def available():
    return bool(load_model().get("available"))


def status():
    """界面用状态；不重复跑推理，只复用 load_model 的结果。"""
    return load_model()


# ---------------------------------------------------------
# 编码
# ---------------------------------------------------------

def _load_cached(path, cache_key=None):
    f = _cache_file(path, cache_key)
    try:
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            vector = data.get("vector")
            if (data.get("dim") == DIM and isinstance(vector, list)
                    and len(vector) == DIM
                    and all(isinstance(x, (int, float)) for x in vector)):
                return vector
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    return None


def _store_cached(path, vector, cache_key=None):
    if not isinstance(vector, list) or len(vector) != DIM:
        return
    try:
        target = _cache_file(path, cache_key)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"dim": DIM, "vector": vector}), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def encode_images(paths, use_cache=True, cache_keys=None):
    """图片路径 → 768 维归一化向量；失败返回 None。顺序与输入一致。"""
    paths = [str(p) for p in paths]
    cache_keys = ([None] * len(paths) if cache_keys is None
                  else list(cache_keys))
    if len(cache_keys) != len(paths):
        raise ValueError("cache_keys 数量必须与 paths 一致")
    vectors = [None] * len(paths)
    pending, idx = [], []
    for i, p in enumerate(paths):
        if use_cache:
            hit = _load_cached(p, cache_keys[i])
            if hit is not None:
                vectors[i] = hit
                continue
        pending.append(p)
        idx.append(i)
    if not pending:
        return vectors

    ok, res = _run({"action": "embed_images", "paths": pending})
    if not ok:
        return None
    encoded = res.get("vectors", [])
    if len(encoded) != len(idx):
        return None
    for j, vec in zip(idx, encoded):
        vectors[j] = vec
        if use_cache:
            _store_cached(paths[j], vec, cache_keys[j])
    return vectors


def encode_text(queries):
    """文本 → 768 维归一化向量；失败返回 None。"""
    ok, res = _run({"action": "embed_text",
                    "queries": [str(q) for q in queries]})
    return res.get("vectors") if ok else None


def cosine(a, b):
    """余弦相似度矩阵。输入可直接用 encode_* 的输出。"""
    out = []
    for va in a:
        row = []
        for vb in b:
            dot = sum(x * y for x, y in zip(va, vb))
            na = sum(x * x for x in va) ** 0.5
            nb = sum(x * x for x in vb) ** 0.5
            row.append(dot / (na * nb) if na and nb else 0.0)
        out.append(row)
    return out


def search(paths, query, top=5):
    """文字检索素材：返回 [(相似度, 路径)]，从高到低。"""
    img_vecs = encode_images(paths)
    txt_vecs = encode_text([query])
    if img_vecs is None or txt_vecs is None:
        return []
    sims = cosine(txt_vecs, img_vecs)[0]
    return sorted(zip(sims, paths), key=lambda x: -x[0])[:top]


def describe():
    """一句话状态，方便日志/界面直接用。"""
    info = status()
    if info.get("available"):
        dev = "GPU" if info.get("cuda") else "CPU"
        return f"OpenCLIP {info.get('arch')} · {dev} 可用 · {info.get('dim')} 维"
    return f"OpenCLIP 未就绪：{info.get('reason', '未知原因')}"


def frame_cache_dir():
    """OpenCLIP 中心帧持久缓存目录；重复混剪时直接复用，不再重新抽帧。"""
    root = cache_dir() / "frames"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _frame_cache_file(cache_key):
    key = hashlib.sha256(
        f"frame-v1|{cache_key}".encode("utf-8", "replace")
    ).hexdigest()[:24]
    return frame_cache_dir() / f"{key}.jpg"


def encode_video_segments(fragments, progress_cb=None, cancel_check=None):
    """为混剪候选片段提取中心帧并生成 OpenCLIP 向量。

    每个源视频只打开一次；每个候选片段取中心帧。失败片段返回 None，
    不影响其他候选，也不改变现有硬过滤结果。抽帧结果按
    「源路径 + 文件签名 + 时间点」持久缓存，重复混剪不会重新抽帧。
    """
    if not fragments:
        return []
    try:
        import cv2
    except Exception:
        cv2 = None
    try:
        from . import ffmpeg_tools as ft
        ffmpeg = ft.find_ffmpeg()
    except Exception:
        ffmpeg = None

    def _ffmpeg_frame(path, timestamp, output):
        if not ffmpeg:
            return False
        try:
            proc = subprocess.run(
                [str(ffmpeg), "-hide_banner", "-loglevel", "error",
                 "-ss", f"{timestamp:.3f}", "-i", str(path),
                 "-map", "0:v:0", "-frames:v", "1",
                 "-vf", "scale=640:-2", "-q:v", "2", "-y", str(output)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, creationflags=_NO_WINDOW,
            )
            return proc.returncode == 0 and output.is_file() and output.stat().st_size > 0
        except (OSError, subprocess.SubprocessError):
            return False

    vectors = [None] * len(fragments)
    grouped = {}
    for i, frag in enumerate(fragments):
        path = str(frag.get("path") or "")
        if path:
            grouped.setdefault(path, []).append((i, frag))

    image_paths, image_indices, cache_keys = [], [], []
    total = len(fragments)
    done = 0
    cache_hits = 0
    for path, entries in grouped.items():
        if cancel_check and cancel_check():
            return None
        try:
            st = os.stat(path)
            signature = f"{st.st_size}:{st.st_mtime_ns}"
        except OSError:
            signature = "missing"

        pending = []
        for idx, frag in entries:
            if cancel_check and cancel_check():
                return None
            start = float(frag.get("start", 0.0))
            end = float(frag.get("end", start))
            if end <= start:
                done += 1
                continue
            ts = (start + end) * 0.5
            cache_key = f"{path}|{signature}|{ts:.3f}"
            image = _frame_cache_file(cache_key)
            if image.is_file() and image.stat().st_size > 0:
                image_paths.append(str(image))
                image_indices.append(idx)
                cache_keys.append(cache_key)
                cache_hits += 1
                done += 1
                if progress_cb:
                    progress_cb(done, total, f"OpenCLIP 复用抽帧 {done}/{total}")
                continue
            pending.append((idx, ts, cache_key, image))

        if not pending:
            continue

        cap = cv2.VideoCapture(path) if cv2 is not None else None
        cap_open = bool(cap is not None and cap.isOpened())
        try:
            for idx, ts, cache_key, image in pending:
                if cancel_check and cancel_check():
                    return None
                saved = False
                if cap_open:
                    cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.size:
                        h, w = frame.shape[:2]
                        if w > 640:
                            nh = max(1, int(h * 640 / w))
                            frame = cv2.resize(
                                frame, (640, nh),
                                interpolation=cv2.INTER_AREA)
                        saved = bool(cv2.imwrite(
                            str(image), frame,
                            [int(cv2.IMWRITE_JPEG_QUALITY), 92]))
                # OpenCV 对部分 HEVC 4:2:2 / 高位深素材可能无法解码，失败时回退 FFmpeg。
                if not saved:
                    saved = _ffmpeg_frame(path, ts, image)
                if saved:
                    image_paths.append(str(image))
                    image_indices.append(idx)
                    cache_keys.append(cache_key)
                done += 1
                if progress_cb:
                    progress_cb(done, total, f"OpenCLIP 抽帧 {done}/{total}")
        finally:
            if cap is not None:
                cap.release()

    if not image_paths:
        return vectors
    if cancel_check and cancel_check():
        return None
    if progress_cb:
        note = f"，复用缓存 {cache_hits} 张" if cache_hits else ""
        progress_cb(
            total, total,
            f"OpenCLIP 编码 {len(image_paths)} 个候选画面{note}…")
    encoded = encode_images(image_paths, cache_keys=cache_keys)
    if encoded is None:
        return None
    for idx, vec in zip(image_indices, encoded):
        vectors[idx] = vec
    return vectors
