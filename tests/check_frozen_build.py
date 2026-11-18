# -*- coding: utf-8 -*-
"""
检查打包产物与源码是否一致。

背景：build_windows.bat 若复用旧的 PyInstaller 缓存（--clean 之外还残留
build/MixCutStudio），可能出现“界面代码是新的、workers 代码是旧的”
这种混合旧包，exe 一启动就报
'ProbeWorker' object has no attribute 'thumbnail_ready'。

做法：
  · 把 dist/MixCutStudio/MixCutStudio.exe 里的 PYZ 归档取出来
  · 读出关键模块的代码对象
  · 用反汇编文本（dis）搜索关键符号——注意信号名等是以“代码指令”
    而不是常量字符串的形式存在，只看 co_consts 会漏检
  · 与工作区源码是否存在同一个符号做对比

返回码 0 = 一致 / 未构建；1 = 落后于源码。

运行：python tests/check_frozen_build.py
"""
import dis
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "MixCutStudio" / "MixCutStudio.exe"

# 每个模块里必须出现的符号（会同时从反汇编与源码里查找）
REQUIRED = {
    "mixcut.workers": ["thumbnail_ready", "source_analyzed", "ProbeWorker"],
    "mixcut.ffmpeg_tools": ["make_thumbnail", "make_preview_clip"],
    "mixcut.ui.library_page": ["set_analysis", "set_thumbnail"],
    "mixcut.mixbuilder": ["VERSION_STYLES", "resolve_version_options"],
}


def _disassembly(code):
    buf = io.StringIO()
    dis.dis(code, file=buf)
    return buf.getvalue()


def _frozen_symbols(reader, module):
    return _disassembly(reader.extract(module))


def main():
    if not EXE.is_file():
        print("[SKIP] 没有 dist/MixCutStudio/MixCutStudio.exe")
        return 0
    try:
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    except Exception:
        print("[SKIP] 当前环境没有 PyInstaller，无法读取归档")
        return 0

    archive = CArchiveReader(str(EXE))
    pyz_names = [n for n in archive.toc if str(n).endswith(".pyz")]
    if not pyz_names:
        print("[SKIP] exe 里没有 PYZ 归档")
        return 0
    with tempfile.NamedTemporaryFile(suffix=".pyz", delete=False) as fh:
        fh.write(archive.extract(pyz_names[0]))
        tmp = fh.name
    reader = ZlibArchiveReader(tmp)

    stale = []
    for module, needles in REQUIRED.items():
        if module not in reader.toc:
            stale.append(f"{module}: 打包里根本没有这个模块")
            continue
        frozen = _frozen_symbols(reader, module)
        src_path = ROOT / (module.replace(".", "/") + ".py")
        src = src_path.read_text(encoding="utf-8") if src_path.is_file() else ""
        for needle in needles:
            if needle not in src:
                # 源码里都没有，说明 REQUIRED 写错了，跳过而不是误报
                continue
            if needle not in frozen:
                stale.append(f"{module}: 缺少 {needle}")

    print(f"检查了 {len(REQUIRED)} 个模块（目标：dist/MixCutStudio/MixCutStudio.exe）")
    if stale:
        print("[FAIL] 打包产物落后于源码：")
        for item in stale:
            print("   -", item)
        print("处理：关闭 exe → 删除 build/MixCutStudio → 重新运行 build_windows.bat")
        return 1
    print("[PASS] 打包产物与当前源码一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
