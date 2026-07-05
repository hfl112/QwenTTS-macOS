"""文本身份 hash 的共享 helper(计划 #10 C2.4)。

裸 md5(text) 此前在 backend / saved_items / podcast_service 开写 5 处;
收口到一个函数。注意与两个**键控** hash 区分,勿混用:
- engine.cache_key(text, voice, model, lang, speed) —— 句级语音缓存
- podcast_jobs.content_key(text, mode, voice) —— 播客成品复用身份
"""
from __future__ import annotations

import hashlib


def text_md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()
