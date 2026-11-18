# -*- coding: utf-8 -*-
"""MixCut · 抽帧性能对比脚本

用途：在同一条素材上对比「cv2 随机 seek 取帧」和「ffmpeg 顺序抽帧」的耗时，
用来验证 frame_pipe 改造是否真的有效。

用法：
    py -3.14 tools/benchmark_frames.py 素材.mp4
    py -3.14 tools/benchmark_frames.py 素材.mp4 --window 5 --samples 3

注意：两种方式最终拿到的帧尺寸不同（cv2 会按原尺寸返回再缩放），
这里统一让 cv2 只读不处理，只比「取到图片」这一步的耗时。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mixcut import frame_pipe


def bench_cv2_seek(path, window=5.0, samples=3, max_side=960):
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if total <= 0 or fps <= 0:
        cap.release()
        return None

    duration = total / fps
    windows = max(1, int(np.ceil(duration / max(0.5, window))))
    offsets = np.linspace(0.15, 0.85, max(1, samples))

    t0 = time.perf_counter()
    got = 0
    for i in range(windows):
        start = i * window
        end = min(duration, start + window)
        for x in offsets:
            frame_index = min(total - 1, max(0, int(round((start + (end - start) * x) * fps))))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if ok and frame is not None:
                got += 1
    cap.release()
    return time.perf_counter() - t0, got


def bench_pipe(path, window=5.0, samples=3, max_side=960):
    fps = float(samples) / max(0.5, window)
    t0 = time.perf_counter()
    got = 0
    for _ts, _frame in frame_pipe.iter_frames(path, fps=fps, max_side=max_side):
        got += 1
    return time.perf_counter() - t0, got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--window", type=float, default=5.0)
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--max-side", type=int, default=960)
    args = ap.parse_args()

    if not os.path.isfile(args.video):
        print("找不到文件:", args.video)
        return 1

    meta = frame_pipe.probe_media(args.video)
    print("素材:", os.path.basename(args.video))
    if meta:
        print("尺寸/时长: {}x{} / {:.1f}s".format(meta[0], meta[1], meta[2]))

    print("\n[1/2] ffmpeg 顺序抽帧 ...")
    pipe_res = bench_pipe(args.video, args.window, args.samples, args.max_side)
    print("    耗时 {:.2f}s，取到 {} 帧".format(*pipe_res))

    print("\n[2/2] cv2 随机 seek ...")
    try:
        cv_res = bench_cv2_seek(args.video, args.window, args.samples, args.max_side)
    except ImportError:
        print("    跳过：没装 opencv-python")
        cv_res = None
    if cv_res:
        print("    耗时 {:.2f}s，取到 {} 帧".format(*cv_res))
        if pipe_res[0] > 0:
            print("\n加速比: {:.1f}x".format(cv_res[0] / pipe_res[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
