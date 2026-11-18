"""
MixCut · 规则化转场

按片段时长与分类黑名单决定每个切点的转场：
  - 短镜头：硬切 / 闪切（不加转场）
  - 长镜头：淡变（叠化）
  - 黑名单分类（如产品特写）之间：一律硬切

规则只写进 timeline["transitions"]，由 project_builder 落地为剪映转场。
"""

DISSOLVE = "叠化"

DEFAULT_TRANSITION_OPTIONS = {
    "enabled": True,
    "short_threshold": 2.0,     # 短于该时长 → 硬切
    "long_threshold": 4.0,      # 长于该时长 → 叠化
    "dissolve_duration": 0.4,   # 叠化持续秒数
    "blacklist": ["产品特写"],   # 命中这些标签的片段只硬切
}


def _clip_tags(timeline, clip):
    tags = set(clip.get("tags", []))
    return tags


def _in_blacklist(timeline, left, right, blacklist):
    black = set(blacklist)
    if not black:
        return False
    return bool(_clip_tags(timeline, left) & black) or \
        bool(_clip_tags(timeline, right) & black)


def apply_transition_rules(timeline, options=None):
    """根据规则填充 timeline["transitions"] 并返回该列表。"""
    opt = {**DEFAULT_TRANSITION_OPTIONS, **(options or {})}
    clips = timeline["clips"]
    rules = []

    if not opt["enabled"] or len(clips) < 2:
        timeline["transitions"] = []
        return []

    short = float(opt["short_threshold"])
    long = float(opt["long_threshold"])
    blacklist = opt["blacklist"]

    for i in range(len(clips) - 1):
        left = clips[i]
        right = clips[i + 1]
        dur = left["timeline_end"] - left["timeline_start"]

        rule = {
            "after_clip_id": left["id"],
            "to_clip_id": right["id"],
            "kind": "hard",
            "transition": None,
            "duration": None,
        }

        # 黑名单分类：一律硬切
        if _in_blacklist(timeline, left, right, blacklist):
            pass
        # 短镜头（闪切）：硬切，不叠化，避免节奏过密
        elif dur <= short:
            pass
        else:
            # 开启叠化后，非黑名单、超过短镜头阈值的相邻片段都加叠化；
            # 叠化时长由 project_builder 按相邻片段长度自动钳制，
            # 不会出现叠化比片段本身还长的问题。
            rule.update({
                "kind": "dissolve",
                "transition": DISSOLVE,
                "duration": float(opt["dissolve_duration"]),
            })

        rules.append(rule)

    timeline["transitions"] = rules
    return rules
