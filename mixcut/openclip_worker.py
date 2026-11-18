# -*- coding: utf-8 -*-
"""OpenCLIP 独立环境里的执行端（只在 venv 里运行，不导入到 MixCut 主环境）。

调用方式：
    <venv>\\Scripts\\python.exe openclip_worker.py <job.json> <out.json>

job 结构：
    {"action": "status"}
    {"action": "embed_images", "paths": ["...jpg", ...]}
    {"action": "embed_text",   "queries": ["...", ...]}

out 结构：
    {"ok": true, "device": "cuda", "arch": "ViT-L-14", "dim": 768,
     "vectors": [[...], ...], "ms": 12.3}
"""

import json
import sys
import time
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
CKPT = Path(os.environ.get(
    "MIXCUT_OPENCLIP_CKPT",
    str(_ROOT / "OpenCLIP" / "open_clip_pytorch_model.bin")))
ARCH = "ViT-L-14"

_MODEL = {}


def _load():
    """懒加载：模型 + 预处理 + 分词器（半精度只在 GPU 上启用）。"""
    if _MODEL:
        return _MODEL

    import torch
    import open_clip

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.time()
    model, _, preprocess = open_clip.create_model_and_transforms(
        ARCH, pretrained=None, precision="fp32", device=dev)
    state = torch.load(CKPT, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model.eval()
    if dev == "cuda":
        model = model.half()
    model_ms = round((time.time() - t0) * 1000, 1)

    _MODEL.update({
        "model": model,
        "preprocess": preprocess,
        "tokenizer": open_clip.get_tokenizer(ARCH),
        "device": dev,
        "torch": torch,
        "model_ms": model_ms,
    })
    return _MODEL


def _status():
    import torch
    m = _load()
    return {
        "ok": True,
        "device": m["device"],
        "cuda": m["device"] == "cuda",
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "torch": torch.__version__,
        "arch": ARCH,
        "dim": 768,
        "model_ms": m.get("model_ms"),
        "ckpt_mb": round(CKPT.stat().st_size / 1024 / 1024) if CKPT.exists() else None,
    }


def _embed_images(paths, batch_size=8):
    """分批图像编码，限制峰值内存；坏图单独跳过并保持输入索引。"""
    import torch
    from PIL import Image

    m = _load()
    model, preprocess, dev = m["model"], m["preprocess"], m["device"]
    dtype = next(model.parameters()).dtype
    batch_size = max(1, int(batch_size))
    feats = [None] * len(paths)
    errors = []
    t0 = time.time()

    for offset in range(0, len(paths), batch_size):
        chunk = paths[offset:offset + batch_size]
        tensors, indices = [], []
        for local_idx, path in enumerate(chunk):
            try:
                with Image.open(path) as img:
                    tensors.append(preprocess(img.convert("RGB")))
                    indices.append(offset + local_idx)
            except Exception as exc:
                errors.append(f"{path}: {type(exc).__name__}: {exc}")
        if not tensors:
            continue
        x = torch.stack(tensors).to(dev, dtype=dtype)
        with torch.no_grad():
            f = model.encode_image(x)
        if dev == "cuda":
            torch.cuda.synchronize()
        normalized = torch.nn.functional.normalize(f.float(), dim=-1).cpu().tolist()
        for i, vec in zip(indices, normalized):
            feats[i] = vec
        del x, f, tensors

    return {"ok": True, "vectors": feats,
            "errors": errors, "ms": round((time.time() - t0) * 1000, 1)}

def _embed_text(queries):
    import torch

    m = _load()
    model, tokenizer, dev = m["model"], m["tokenizer"], m["device"]
    tokens = tokenizer(queries).to(dev)
    t0 = time.time()
    with torch.no_grad():
        f = model.encode_text(tokens)
    if dev == "cuda":
        torch.cuda.synchronize()
    vecs = torch.nn.functional.normalize(f.float(), dim=-1).tolist()
    return {"ok": True, "vectors": vecs, "ms": round((time.time() - t0) * 1000, 1)}


def main():
    job_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    job = json.loads(job_path.read_text(encoding="utf-8"))
    action = job.get("action")
    try:
        if action == "status":
            result = _status()
        elif action == "embed_images":
            result = _embed_images([str(p) for p in job.get("paths", [])],
                                   batch_size=job.get("batch_size", 8))
        elif action == "embed_text":
            result = _embed_text([str(q) for q in job.get("queries", [])])
        else:
            result = {"ok": False, "error": f"未知 action: {action}"}
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    result.setdefault("device", _MODEL.get("device"))
    result.setdefault("arch", ARCH)
    result.setdefault("dim", 768)
    out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
