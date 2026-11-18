# -*- coding: utf-8 -*-
"""MixCut 预览代理系统。

对外兼容入口仍是 ``mixcut.preview_proxy``；本包只负责代理生成、缓存、校验与队列。
"""

from .spec import (  # noqa: F401
    AUDIO_BITRATE,
    AUDIO_CODEC,
    DEFAULT_TARGET_HEIGHT,
    PROXY_CODEC,
    PROXY_PIX_FMT,
    PROXY_PROFILE,
    PROXY_SPEC_VERSION,
    WIDTH_CHOICES,
)
