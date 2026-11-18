# -*- coding: utf-8 -*-
"""验证打包配置：spec 能解析、TransNetV2 权重与模块会被收集。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = (ROOT / "MixCutStudio.spec").read_text(encoding="utf-8")

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


check("spec 收集 transnetv2_pytorch 数据(权重)",
      "collect_data_files('transnetv2_pytorch')" in spec)
check("spec 显式包含 transnetv2_pytorch 模块",
      "'transnetv2_pytorch'" in spec)
check("spec 包含 ffmpeg-python",
      "'ffmpeg'" in spec)

try:
    from PyInstaller.utils.hooks import collect_data_files
    data = collect_data_files("transnetv2_pytorch")
except Exception as exc:  # pragma: no cover
    data = []
    print(f"  收集失败：{exc}")
weights = [d for d in data if d[0].endswith(".pth")]
check("能收集到权重文件", bool(weights), str([w[0] for w in weights]))

from transnetv2_pytorch import TransNetV2  # noqa: E402
import transnetv2_pytorch  # noqa: E402
wpath = Path(transnetv2_pytorch.__file__).parent / "transnetv2-pytorch-weights.pth"
check("权重文件存在于安装目录", wpath.is_file(),
      f"{wpath.stat().st_size / 1e6:.1f} MB" if wpath.is_file() else "缺失")

sys.exit(1 if FAILED else 0)
