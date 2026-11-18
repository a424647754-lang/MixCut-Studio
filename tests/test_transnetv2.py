# -*- coding: utf-8 -*-
"""
TransNetV2 实测：对比 amverge / PySceneDetect / TransNetV2 三种切点检测。

素材由 ffmpeg 现场生成：
  cuts.mp4   0-3s 红 / 3-6s 绿 / 6-9s 蓝（两处硬切，3.000s 与 6.000s）
  flash.mp4  0-2s 红 / 2-4s 绿 / 4-8s 每 5 帧红绿交替（快速闪切，硬切之外的压力样本）

运行：python tests/test_transnetv2.py
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mixcut import ffmpeg_tools as ft          # noqa: E402
from mixcut import filter_engine as FE         # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def make_clips(workdir):
    ff = ft.find_ffmpeg()
    cuts = Path(workdir) / "cuts.mp4"
    flash = Path(workdir) / "flash.mp4"
    subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=320x180:r=25:d=3",
         "-f", "lavfi", "-i", "color=c=green:s=320x180:r=25:d=3",
         "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=3",
         "-filter_complex",
         "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", str(cuts)],
        check=True, timeout=120)
    subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=320x180:r=25:d=2",
         "-f", "lavfi", "-i", "color=c=green:s=320x180:r=25:d=2",
         "-f", "lavfi", "-i", "color=c=red:s=320x180:r=25:d=4",
         "-f", "lavfi", "-i", "color=c=green:s=320x180:r=25:d=4",
         "-filter_complex",
         "[2:v][3:v]blend=all_expr='if(lt(mod(N\\,10)\\,5)\\,A\\,B)'[f];"
         "[0:v][1:v][f]concat=n=3:v=1:a=0,format=yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", str(flash)],
        check=True, timeout=120)
    return cuts, flash


def near(times, target, tol=0.12):
    return any(abs(t - target) <= tol for t in times)


def run_cuts(path, label):
    t0 = time.time()
    cuts = FE._transnetv2_cuts(str(path))
    times = sorted(round(t, 3) for t, _k, _s in cuts)
    print(f"    {label}: {len(times)} 个切点 {times}（{time.time() - t0:.1f}s）")
    return times


def main():
    workdir = tempfile.mkdtemp(prefix="mixcut_transnet_")
    cuts_path, flash_path = make_clips(workdir)

    print("== 1) 组件自检 ==")
    avail = FE.self_check()
    check("TransNetV2 被识别为可用", avail.get("transnetv2") is True, str(avail))
    missing = FE.missing_modules({**FE.DEFAULT_FILTER_CONFIG,
                                  "transnetv2_enable": True})
    check("没有把 TransNetV2 报成缺失", "TransNetV2" not in " ".join(missing),
          str(missing))

    print("== 2) 硬切素材 cuts.mp4：应识别 3.0s、6.0s ==")
    times = run_cuts(cuts_path, "TransNetV2")
    check("识别出 3.0s 切点", near(times, 3.0, 0.08), f"{times}")
    check("识别出 6.0s 切点", near(times, 6.0, 0.08), f"{times}")
    check("没有多余切点", len(times) == 2, f"共 {len(times)} 个")

    print("== 3) 与 PySceneDetect / amverge 对比 ==")
    for name, fn in (("PySceneDetect", FE._scenedetect_cuts),
                     ("amverge", lambda p: FE._amverge_cuts(
                         p, FE.DEFAULT_FILTER_CONFIG["min_waste_duration"]))):
        try:
            found = sorted(round(t, 3) for t, _k, _s in fn(str(cuts_path)))
        except Exception as exc:
            found = f"失败 {type(exc).__name__}: {exc}"
        print(f"    {name}: {found}")

    print("== 4) 闪切素材 flash.mp4（压力样本）==")
    t_tn = run_cuts(flash_path, "TransNetV2")
    try:
        sd = sorted(round(t, 3) for t, _k, _s in
                    FE._scenedetect_cuts(str(flash_path)))
    except Exception as exc:
        sd = [f"失败 {exc}"]
    print(f"    PySceneDetect: {len(sd)} 个切点 {sd[:12]}...")
    check("闪切素材上 TransNetV2 至少抓到 2/4 秒两处主切点",
          near(t_tn, 2.0, 0.12) and near(t_tn, 4.0, 0.12))

    print("== 5) 走完整筛选引擎（transnetv2_enable=True）==")
    cfg = dict(FE.DEFAULT_FILTER_CONFIG, cpu_mode=True,
               transnetv2_enable=True, videoscope_enable=False)
    t0 = time.time()
    res = FE.run_filter([str(cuts_path)], cfg)
    src = res["sources"][str(cuts_path)]
    el = time.time() - t0
    tags = sorted({s for c in src["shot_cuts"] for s in c["source"]})
    print(f"    耗时 {el:.1f}s，切点来源 {tags}，错误 {src['errors']}")
    check("引擎没有报错", not src["errors"], str(src["errors"]))
    check("引擎结果里带有 transnetv2 来源", "transnetv2" in tags, str(tags))
    check("引擎输出的切点包含 3.0/6.0",
          near([c["time"] for c in src["shot_cuts"]], 3.0, 0.08)
          and near([c["time"] for c in src["shot_cuts"]], 6.0, 0.08))

    print("== 6) 分析：切点转成混剪可用区间 ==")
    analysis = FE.to_mix_analysis([str(cuts_path)], res)
    print("    _scenes:", analysis[0]["_scenes"])

    import shutil
    shutil.rmtree(workdir, ignore_errors=True)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
