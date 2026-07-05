"""Serena/Ryan 说话人身份(voice → ref_audio/ref_text/instruct)的单一定义
(计划 #10 C5.3)。

此前同一份身份数据在 processor.parse_dialogue_or_text 与 engine._inject_icl/
_redirect_cross_language_icl 各写一份,且**行为并不相同**(processor 的 Serena
恒用 EN 参考音 ref_serena_en.wav;engine 按 lang 选)。收口为数据表 + 两个口径的
查询函数,**精确保留**各调用点的历史选择;要改音色参考,只动这一个文件。
"""
from __future__ import annotations

import os

# (voice, lang) → (参考音文件名, 参考文本)。Ryan 无 en 参考音(历史如此)。
REFS: dict[tuple[str, str], tuple[str, str]] = {
    ("Serena", "zh"): (
        "ref_serena_zh.wav",
        "欢迎收听本期播客，我是女主持塞蕾娜。",
    ),
    ("Serena", "en"): (
        "ref_serena_en.wav",
        "This is the research headquarters for one of the oldest companies in tech, IBM.",
    ),
    ("Ryan", "zh"): (
        "ref_ryan.wav",
        "各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。",
    ),
}

INSTRUCTS: dict[str, str] = {
    "Serena": "Professional female anchor, steady and clear.",
    "Ryan": "A professional male anchor, reading news in a steady and clear voice.",
}

# 双人对话(processor)口径:说话人 → 固定用哪个 lang 的参考音(历史行为:
# Serena 恒 EN、Ryan 恒 zh,与 engine 的按 lang 动态选不同,勿混)。
_DIALOGUE_REF_LANG: dict[str, str] = {"Serena": "en", "Ryan": "zh"}


def ref_entry(base_ref_path: str, voice: str, lang: str) -> tuple[str, str] | None:
    """engine(ICL)口径:(voice, lang) 命中表 → (参考音绝对路径, 参考文本);
    未命中 → None。不做跨 lang 回退(与历史一致)。"""
    entry = REFS.get((voice, lang))
    if entry is None:
        return None
    filename, ref_text = entry
    return os.path.join(base_ref_path, filename), ref_text


def dialogue_speaker_config(speaker: str | None, base_ref_path: str) -> dict:
    """processor(双人对话)口径:speaker → 完整 cfg dict;非 Serena/Ryan → {}。"""
    if speaker not in _DIALOGUE_REF_LANG:
        return {}
    entry = ref_entry(base_ref_path, speaker, _DIALOGUE_REF_LANG[speaker])
    assert entry is not None
    ref_audio, ref_text = entry
    return {
        "voice": speaker,
        "instruct": INSTRUCTS[speaker],
        "ref_audio": ref_audio,
        "ref_text": ref_text,
    }
