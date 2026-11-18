# -*- coding: utf-8 -*-
"""模拟 .gitignore 规则，核对哪些文件会被提交、哪些被排除。"""
import fnmatch
import os

ROOT = r"D:\mixcut源文件\混剪副本（测试）源文件"

# 与 .gitignore 等价的规则（目录级 + 文件级）
DIR_EXCLUDE = {
    "OpenCLIP", "ffmpeg", "engine", "cache", ".preview_proxy", "proxy_cache",
    "build", "dist", "installer_output", "__pycache__", ".venv", "venv",
    "env", "ENV", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".vscode", ".idea", ".git", "bench_results",
}
FILE_PATTERNS = [
    "*.py[cod]", "*.so", "*.bak", "*.bak-*", "*.orig", "*.rej", "*.tmp",
    "*.log", "*.mp4", "*.mov", "*.mkv", "*.m4v", "*.avi", "*.webm", "*.flv",
    "*.wmv", "*.ts", "*.mp3", "*.wav", "*.flac", "*.aac", "*.m4a", "*.cube",
    "*.lrf", "*.psd", "*.ai", "*.mixcut", "*.autosave", "*.swp", "*.swo",
    ".DS_Store", "Thumbs.db", "desktop.ini", "*.zip", "*.7z", "*.rar",
    "*.tar", "*.gz", "*.pem", "*.key", ".env", "build_out.log",
    "installer_build.log", "e3d4fbdd*", "splash_*", "*.ttf", "*.otf",
    "*.jpg", "*.jpeg", "*.png",
]
# 允许保留的就地例外（不排除）
KEEP = {
    "MixCutStudio.spec",
    "mixcut/ui/fonts/msyh.ttf",   # 程序真正使用的中文字体
    "assets/app.png",             # 程序图标
    "mixcut.ico",
}
TEMP_SCRIPTS = {
    "tests/_crash_scan.py", "tests/_crash_scan.txt",
    "tests/_dead_code_scan.py", "tests/_dead_code_report.txt",
    "tests/_dead_code_verify.py", "tests/_dead_code_verify.txt",
    "tests/_perf_scan.py", "tests/_perf_report.txt",
    "tests/_size_scan.py", "tests/_size_report.txt",
    "tests/_grid_dump.txt",
}

kept, dropped = [], []
dir_dropped = set()
for dirpath, dirnames, filenames in os.walk(ROOT):
    rel_dir = os.path.relpath(dirpath, ROOT)
    # 记录被目录规则整体排除的目录
    for d in list(dirnames):
        if (d in DIR_EXCLUDE or d.startswith("_verify_ui_")
                or d.startswith("_便签取证_")):
            dir_dropped.add(d)
    dirnames[:] = [d for d in dirnames
                   if d not in DIR_EXCLUDE
                   and not d.startswith("_verify_ui_")
                   and not d.startswith("_便签取证_")]
    for name in filenames:
        rel = (name if rel_dir == "." else os.path.join(rel_dir, name))
        rel_norm = rel.replace("\\", "/")
        if rel_norm in KEEP:
            kept.append(rel_norm)
            continue
        if rel_norm in TEMP_SCRIPTS:
            dropped.append(rel_norm)
            continue
        if any(fnmatch.fnmatch(name, p) for p in FILE_PATTERNS):
            dropped.append(rel_norm)
            continue
        # spec 默认排除但允许例外
        if name.endswith(".spec") and name != "MixCutStudio.spec":
            dropped.append(rel_norm)
            continue
        kept.append(rel_norm)

kept_sorted = sorted(kept)
total = sum(os.path.getsize(os.path.join(ROOT, f)) for f in kept_sorted)

lines = []
lines.append("将提交到 GitHub 的文件清单（模拟 .gitignore 结果）")
lines.append("=" * 68)
lines.append("共 %d 个文件，合计 %.2f MB" % (len(kept_sorted), total / 1048576))
lines.append("")
for f in kept_sorted:
    sz = os.path.getsize(os.path.join(ROOT, f))
    lines.append("  %8.1f KB  %s" % (sz / 1024, f))

lines.append("")
lines.append("=" * 68)
lines.append("被排除的文件数：%d（另有 %d 个目录整体排除）"
             % (len(dropped), len(dir_dropped)))
lines.append("")
lines.append("【关键检查：这些必须被排除】")
must_drop = ["OpenCLIP", "ffmpeg", "engine", "cache", "dist", "build",
             "__pycache__"]
for m in must_drop:
    ok = m in dir_dropped or any(d.startswith(m + "/") for d in dropped)
    lines.append("  %-14s %s" % (m, "✓ 已排除" if ok else "✗ 未排除！"))
lines.append("")
lines.append("【关键检查：这些必须保留】")
must_keep = ["main.py", "mixcut/ui/analyze_page.py",
             "mixcut/ui/fonts/msyh.ttf", "MixCut全周期工作台账.md",
             "tests/test_analyze_folders.py", "README.md",
             ".gitignore", "MixCutStudio.spec"]
for m in must_keep:
    lines.append("  %-42s %s" % (
        m, "✓ 保留" if m in kept_sorted else "✗ 被误排除！"))

path = os.path.join(ROOT, "tests", "_gitignore_check.txt")
open(path, "w", encoding="utf-8").write("\n".join(lines))
print("files to commit:", len(kept_sorted), "%.2f MB" % (total / 1048576))
