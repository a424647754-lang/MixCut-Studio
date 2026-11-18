# -*- coding: utf-8 -*-
"""界面常量。素材、任务、日志均来自旧后台，不在前端内置演示数据。"""

# 缩略图占位色板，不是素材文件；素材缩略图由真实路径另行生成。
THUMBS = [""] * 6

# 启动时为空；真实素材由 app/backend_bridge.py 的 snapshot 注入。
CLIPS = []

STAGES = [
    ("import", "素材导入", "library"),
    ("shots", "镜头检测", "spark"),
    ("semantic", "语义分析", "flow"),
    ("filter", "无效过滤", "check"),
    ("mix", "混剪组合", "grid"),
    ("draft", "草稿生成", "folder"),
]

# 启动时没有任务与日志；真实任务由后台混剪 worker 驱动。
STAGE_LOGS = {key: [] for key, _, _ in STAGES}

TASKS = []

