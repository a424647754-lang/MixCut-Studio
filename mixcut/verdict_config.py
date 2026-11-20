# -*- coding: utf-8 -*-
"""判废规则配置：让用户决定「哪些因素才会判废」以及严格程度。

背景
----
改造前判废条件全部硬编码在 ``bad_shot_detector`` 与 ``analyze_page``：

    q.is_bad = bool(reasons)          # 任何一条原因成立就废
    ratio >= mode["bad_ratio"]        # 这个比例判断被上面的条件架空了

结果是「一帧模糊就整段判废」，好画面误伤严重，而用户又无法调节。

本模块把判废拆成可配置的维度：
    · 每个维度可单独开关
    · 每个维度用「严格程度档位」而非裸数值，用户更容易理解
    · 片段级判废有两个条件，任一满足即废：
        1. 差帧比例 ≥ bad_ratio
        2. 质量分 < min_score

档位的含义
----------
``LEVELS`` 从低到高表示「越严格，越容易判废」。
每个维度的阈值按自己的方向映射到档位：

    · 阈值越高越严格（模糊、黑场）：档位↑ → 阈值↑
    · 阈值越低越严格（白场）：    档位↑ → 阈值↓
    · 阈值越高越严格（欠曝/过曝）：档位↑ → 阈值↑

配置随工程保存（见 ``to_dict`` / ``from_dict``）。
"""
from dataclasses import dataclass, field

# ---------------------------------------------------------------
# 档位定义：用户拉滑动条时看到的文字
# ---------------------------------------------------------------
LEVELS = ["低", "偏低", "中", "偏高", "高"]
LEVEL_COUNT = len(LEVELS)

# 位置的显示名（用于「严格程度越高越容易判废」的提示）
LEVEL_HINT = {
    0: "几乎不判这个因素",
    1: "只在非常明显时才判",
    2: "平衡（推荐）",
    3: "宁可错判也不放过",
    4: "非常严格，容易误伤",
}


# ---------------------------------------------------------------
# 判废维度定义
#   levels: 5 个档位对应的阈值，从「低」到「高」
#   higher_is_stricter: 阈值升高是否意味着更严格
# ---------------------------------------------------------------
DIMENSIONS = {
    "blur": {
        "label": "模糊",
        "desc": "画面不够清晰。数值越大越敏感，纯色背景容易被误判。",
        "default_enabled": True,
        "levels": [8.0, 14.0, 22.0, 30.0, 42.0],
        "unit": "拉普拉斯方差下限",
    },
    "dark": {
        "label": "黑场",
        "desc": "整段接近全黑，基本不可用。",
        "default_enabled": True,
        "levels": [2.0, 4.0, 6.0, 10.0, 20.0],
        "unit": "平均亮度上限",
    },
    "bright": {
        "label": "白场",
        "desc": "整段接近全白，基本不可用。",
        "default_enabled": True,
        "levels": [252.0, 250.0, 248.0, 244.0, 235.0],
        "unit": "平均亮度下限",
    },
    "underexposed": {
        "label": "偏暗",
        "desc": "暗但没全黑。夜景素材建议调低。",
        "default_enabled": True,
        "levels": [10.0, 18.0, 24.0, 34.0, 48.0],
        "unit": "平均亮度上限",
    },
    "overexposed": {
        "label": "偏亮",
        "desc": "亮但没全白。雪景、天空建议调低。",
        "default_enabled": True,
        "levels": [246.0, 240.0, 235.0, 228.0, 215.0],
        "unit": "平均亮度下限",
    },
    "noise": {
        "label": "噪点",
        "desc": "高 ISO 噪点明显。",
        "default_enabled": True,
        "levels": [40.0, 28.0, 20.0, 15.0, 8.0],
        "unit": "噪点估计上限",
    },
    "shake": {
        "label": "手抖",
        "desc": "镜头晃动明显。采样帧太少时参考价值有限。",
        "default_enabled": False,
        "levels": [70.0, 55.0, 45.0, 35.0, 22.0],
        "unit": "帧间差异上限",
    },
    # ---- 第十五阶段补充：以下指标均为「阈值越低越严格」----
    "flat": {
        "label": "画面发灰",
        "desc": "对比度过低，雾天、逆光、脏镜头常见。",
        "default_enabled": False,
        "levels": [10.0, 16.0, 24.0, 34.0, 46.0],
        "unit": "对比度下限",
    },
    "empty": {
        "label": "空镜",
        "desc": "细节极少，纯色墙、大天空、失焦背景。调高会误伤特写。",
        "default_enabled": False,
        "levels": [0.3, 0.8, 1.5, 2.6, 4.0],
        "unit": "边缘密度下限(%)",
        "decimals": 1,
    },
    "gray": {
        "label": "灰片",
        "desc": "色彩极淡，接近黑白。低饱和风格化素材请关闭。",
        "default_enabled": False,
        "levels": [8.0, 16.0, 28.0, 45.0, 70.0],
        "unit": "饱和度下限",
    },
    "cast": {
        "label": "色偏",
        "desc": "颜色明显偏离，白平衡错误。",
        "default_enabled": False,
        "levels": [45.0, 34.0, 24.0, 15.0, 8.0],
        "unit": "通道差上限",
    },
    "shadow": {
        "label": "阴影过重",
        "desc": "大面积死黑，细节丢失。剪影风格请关闭。",
        "default_enabled": False,
        "levels": [85.0, 75.0, 65.0, 52.0, 38.0],
        "unit": "暗部占比上限(%)",
    },
}

# 阈值随档位升高而**降低**的维度（越高越严格 = 上限越低）
# 判定方向为「指标 > 阈值 算废」的维度，阈值都必须越低越严格，全部归此。
# 判定方向为「指标 < 阈值 算废」的维度（blur / dark / underexposed /
# flat / empty / gray），阈值越高越严格，属升序。
DESCENDING_DIMENSIONS = ("bright", "overexposed", "noise",
                         "shake", "cast", "shadow")


# ---------------------------------------------------------------
# 弹窗分组：把 12 个因素分成两块，避免一长串堆在一起
# ---------------------------------------------------------------
GROUPS = [
    ("基础画质", ["blur", "dark", "bright", "underexposed",
                 "overexposed", "noise"]),
    ("画面内容", ["flat", "empty", "gray", "cast", "shadow", "shake"]),
]

# 各维度默认档位（取 levels 的哪一项）
DEFAULT_LEVEL = 2   # 「中」

# 片段级判定默认
DEFAULT_BAD_RATIO = 0.5     # 差帧占比达到多少才算「画质差帧过多」
DEFAULT_MIN_SCORE = 25.0    # 质量分低于多少直接判废


# ---------------------------------------------------------------
# 分析模式（采样密度）：与 analyze_page.ANALYSIS_MODES 的键一致
# 顺序即下拉顺序，index 与 backend.start_analysis 的 mode_index 对应
# ---------------------------------------------------------------
ANALYSIS_MODE_ORDER = ["fast", "medium", "strict"]

ANALYSIS_MODE_TEXT = {
    "fast": ("快速", "只取 3 个采样点，最快，适合粗筛"),
    "medium": ("中度", "6 个采样点，速度与准确度平衡（推荐）"),
    "strict": ("严格", "12 个采样点，抓得最细，耗时最长"),
}

# 采样点数量提示（单独一句，避免和上面的描述重复）
ANALYSIS_MODE_SAMPLES = {"fast": 3, "medium": 6, "strict": 12}


def level_to_threshold(key, level):
    """把档位（0..4）转成该维度的实际阈值。"""
    spec = DIMENSIONS.get(key)
    if not spec:
        return 0.0
    lv = max(0, min(LEVEL_COUNT - 1, int(level)))
    return float(spec["levels"][lv])


def threshold_to_level(key, threshold):
    """把实际阈值反查成最接近的档位（用于兼容旧配置）。"""
    spec = DIMENSIONS.get(key)
    if not spec:
        return DEFAULT_LEVEL
    vals = spec["levels"]
    try:
        target = float(threshold)
    except (TypeError, ValueError):
        return DEFAULT_LEVEL
    best, best_diff = DEFAULT_LEVEL, None
    for i, v in enumerate(vals):
        diff = abs(float(v) - target)
        if best_diff is None or diff < best_diff:
            best, best_diff = i, diff
    return best


@dataclass
class VerdictConfig:
    """判废规则配置。

    Attributes:
        enabled: 维度 key -> 是否启用
        levels: 维度 key -> 档位（0=低 ... 4=高）
        bad_ratio: 差帧占比阈值（0~1）
        min_score: 质量分下限（0~100）
    """

    enabled: dict = field(default_factory=dict)
    levels: dict = field(default_factory=dict)
    bad_ratio: float = DEFAULT_BAD_RATIO
    min_score: float = DEFAULT_MIN_SCORE
    analysis_mode: str = "medium"   # fast / medium / strict

    def __post_init__(self):
        for key, spec in DIMENSIONS.items():
            self.enabled.setdefault(key, spec["default_enabled"])
            self.levels.setdefault(key, DEFAULT_LEVEL)
        # 清掉已经不存在的维度
        for key in list(self.enabled):
            if key not in DIMENSIONS:
                self.enabled.pop(key, None)
        for key in list(self.levels):
            if key not in DIMENSIONS:
                self.levels.pop(key, None)
        # 分析模式必须合法
        if self.analysis_mode not in ANALYSIS_MODE_ORDER:
            self.analysis_mode = "medium"

    # ---- 查询 ----

    def is_on(self, key):
        return bool(self.enabled.get(key, False))

    def level(self, key):
        return max(0, min(LEVEL_COUNT - 1,
                          int(self.levels.get(key, DEFAULT_LEVEL))))

    def level_text(self, key):
        return LEVELS[self.level(key)]

    def threshold(self, key):
        """该维度的实际阈值（由档位换算）。"""
        return level_to_threshold(key, self.level(key))

    def enabled_keys(self):
        return [k for k in DIMENSIONS if self.is_on(k)]

    def summary(self):
        """一句话概括当前规则，用于日志。"""
        if not self.enabled_keys():
            return "不判废任何因素"
        parts = []
        for k in self.enabled_keys():
            parts.append("%s·%s" % (DIMENSIONS[k]["label"], self.level_text(k)))
        return "、".join(parts)

    def mode_index(self):
        """分析模式在 ANALYSIS_MODE_ORDER 里的下标（对应 mode_index）。"""
        try:
            return ANALYSIS_MODE_ORDER.index(self.analysis_mode)
        except ValueError:
            return 1

    def mode_text(self):
        return ANALYSIS_MODE_TEXT.get(self.analysis_mode, ("中度", ""))[0]

    def bad_ratio_level(self):
        """差帧占比档位：0=低 ... 4=高（对应 80%→20%）。"""
        thresholds = [0.80, 0.65, 0.50, 0.35, 0.20]
        best, best_diff = DEFAULT_LEVEL, None
        for i, v in enumerate(thresholds):
            diff = abs(v - float(self.bad_ratio))
            if best_diff is None or diff < best_diff:
                best, best_diff = i, diff
        return best

    def set_bad_ratio_level(self, level):
        thresholds = [0.80, 0.65, 0.50, 0.35, 0.20]
        self.bad_ratio = thresholds[max(0, min(4, int(level)))]

    # ---- 序列化 ----

    def to_dict(self):
        return {
            "enabled": dict(self.enabled),
            "levels": dict(self.levels),
            "bad_ratio": float(self.bad_ratio),
            "min_score": float(self.min_score),
            "analysis_mode": str(self.analysis_mode),
        }

    @classmethod
    def from_dict(cls, data):
        """从字典恢复；容忍缺失/非法值，始终返回可用配置。"""
        if not isinstance(data, dict):
            return cls()
        cfg = cls()
        enabled = data.get("enabled")
        if isinstance(enabled, dict):
            for key in DIMENSIONS:
                if key in enabled:
                    cfg.enabled[key] = bool(enabled[key])

        # 新格式：直接给档位
        levels = data.get("levels")
        if isinstance(levels, dict):
            for key in DIMENSIONS:
                if key not in levels:
                    continue
                try:
                    cfg.levels[key] = max(0, min(LEVEL_COUNT - 1,
                                                 int(levels[key])))
                except (TypeError, ValueError):
                    continue
        # 兼容旧格式：给的是 thresholds（裸数值），反查成档位
        old_th = data.get("thresholds")
        if isinstance(old_th, dict):
            for key in DIMENSIONS:
                if key not in old_th or key in (levels or {}):
                    continue
                cfg.levels[key] = threshold_to_level(key, old_th[key])

        try:
            cfg.bad_ratio = max(0.0, min(1.0, float(
                data.get("bad_ratio", DEFAULT_BAD_RATIO))))
        except (TypeError, ValueError):
            pass
        try:
            cfg.min_score = max(0.0, min(100.0, float(
                data.get("min_score", DEFAULT_MIN_SCORE))))
        except (TypeError, ValueError):
            pass
        mode = data.get("analysis_mode")
        if isinstance(mode, str) and mode in ANALYSIS_MODE_ORDER:
            cfg.analysis_mode = mode
        return cfg

    # ---- 预设 ----

    @classmethod
    def preset(cls, name):
        """取预设：loose / balanced / strict

        每个预设都**显式**设置所有维度的开关与档位，不能只改档位——
        画面内容那几个维度默认是关的，只改档位等于点了「严格」也没开。

        （原有的 ``legacy``「旧版」预设已移除：它还原的是改造前
        「任意一帧出问题就整段废」的行为，那正是要修掉的问题。）
        """
        cfg = cls()
        if name == "loose":
            # 宽松：只拦全黑全白，其余（含画面内容）都不管
            for key in DIMENSIONS:
                cfg.enabled[key] = key in ("dark", "bright")
                cfg.levels[key] = 1
            cfg.set_bad_ratio_level(0)
            cfg.min_score = 15.0
        elif name == "balanced":
            # 均衡：基础画质全开；画面内容只开最稳的两个
            # （空镜、画面发灰误伤少；灰片/色偏/阴影重对风格化素材容易误判）
            for key in DIMENSIONS:
                if key in ("flat", "empty"):
                    cfg.enabled[key] = True
                elif key in ("gray", "cast", "shadow", "shake"):
                    cfg.enabled[key] = False
                else:
                    cfg.enabled[key] = True
                cfg.levels[key] = 2
            cfg.set_bad_ratio_level(2)
            cfg.min_score = 25.0
        elif name == "strict":
            # 严格：**只加强基础画质，不碰场景相关的画面内容维度**。
            #
            # ⚠ 2026-11-20 第二十八阶段修正
            # ------------------------------
            # 早期严格预设是「12 项全开 + 档位偏高 + bad_ratio 降到 35%」，
            # 两个严叠加，实测在真实人像素材上 **6/6 帧全部命中、整段判废**：
            #
            #   手抖 5/6 (83%) —— 有人走动被当成手抖
            #   空镜 3/6 (50%) —— 白背景画面边缘密度低
            #   灰片 2/6 (33%) —— 白墙背景对比度偏低
            #   色偏 1/6 (17%) —— 暖色调被判色偏
            #
            # 这四项都不是"素材差"，而是**拍摄场景的正常特征**。
            # 严格模式应当"对画质更挑"，而不是"对任何风格都判废"。
            #
            # 现在：基础画质 6 项开、档位偏高（比均衡更严一档）；
            #       画面内容只保留「空镜」「画面发灰」这两个相对客观的，
            #       但档位留在「中」——
            #       `empty` 的官方描述就写着「调高会误伤特写」，
            #       白背景人像/产品特写的边缘密度天然就低，调高必误伤。
            #       灰片/色偏/阴影/手抖交给用户按素材类型自己开。
            # 差帧占比从 35% 放宽到 50%（与均衡一致）——
            # 加强严格度靠"每帧的门槛"，而不是靠"少数帧就能否定整段"。
            for key in DIMENSIONS:
                if key in ("gray", "cast", "shadow", "shake"):
                    cfg.enabled[key] = False
                    cfg.levels[key] = 3
                elif key in ("empty", "flat"):
                    cfg.enabled[key] = True
                    cfg.levels[key] = 2      # 留「中」：调高会误伤特写
                else:
                    cfg.enabled[key] = True
                    cfg.levels[key] = 3      # 基础画质：比均衡严一档
            cfg.set_bad_ratio_level(2)      # 50%，与均衡一致
            cfg.min_score = 35.0
        return cfg
