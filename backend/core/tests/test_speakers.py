"""C5.3(计划 #10)——说话人身份表:钉住两个调用口径的历史行为。"""

import os

from core import speakers
from core.processor import TextProcessor


def test_engine_ref_entries_match_historical_icl_table():
    """engine(ICL)口径:4 组 (voice, lang) 的历史选择逐字对拍。"""
    base = "/ref"
    assert speakers.ref_entry(base, "Serena", "zh") == (
        "/ref/ref_serena_zh.wav",
        "欢迎收听本期播客，我是女主持塞蕾娜。",
    )
    assert speakers.ref_entry(base, "Serena", "en") == (
        "/ref/ref_serena_en.wav",
        "This is the research headquarters for one of the oldest companies in tech, IBM.",
    )
    assert speakers.ref_entry(base, "Ryan", "zh") == (
        "/ref/ref_ryan.wav",
        "各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。",
    )
    # Ryan 无 en 参考音(历史如此,无回退)
    assert speakers.ref_entry(base, "Ryan", "en") is None
    assert speakers.ref_entry(base, "Ethan", "zh") is None


def test_dialogue_speaker_config_matches_historical_processor_cfg():
    """processor(双人对话)口径:Serena 恒 EN 参考、Ryan 恒 zh(历史行为)。"""
    base = "/ref"
    serena = speakers.dialogue_speaker_config("Serena", base)
    assert serena == {
        "voice": "Serena",
        "instruct": "Professional female anchor, steady and clear.",
        "ref_audio": "/ref/ref_serena_en.wav",
        "ref_text": "This is the research headquarters for one of the oldest companies in tech, IBM.",
    }
    ryan = speakers.dialogue_speaker_config("Ryan", base)
    assert ryan == {
        "voice": "Ryan",
        "instruct": "A professional male anchor, reading news in a steady and clear voice.",
        "ref_audio": "/ref/ref_ryan.wav",
        "ref_text": "各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。",
    }
    assert speakers.dialogue_speaker_config(None, base) == {}
    assert speakers.dialogue_speaker_config("Nobody", base) == {}


def test_parse_dialogue_uses_speaker_table():
    """parse_dialogue_or_text 的 chunk cfg 与身份表一致(端到端对拍)。"""
    from core.paths import runtime_paths

    tp = TextProcessor()
    chunks = tp.parse_dialogue_or_text("[Serena]: 你好。\n[Ryan]: 大家好。")
    assert isinstance(chunks[0], dict)
    cfgs = {c["config"].get("voice"): c["config"] for c in chunks if c["config"]}
    assert cfgs["Serena"] == speakers.dialogue_speaker_config(
        "Serena", runtime_paths.reference_path
    )
    assert cfgs["Ryan"] == speakers.dialogue_speaker_config(
        "Ryan", runtime_paths.reference_path
    )
