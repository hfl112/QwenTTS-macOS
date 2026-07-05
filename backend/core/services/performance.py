from core.constants import DEFAULT_TTS_MODEL
import re


# P1 重标定(2026-07-02,基于 4bit 模型 profile_gen 实测推导,不再是经验值):
# 实测 RTF(raw)=0.35,每帧 ≈0.92s 音频 / 0.32s 生成 → 有效RTF=(0.32+chunk_sleep)/0.92。
# 三档定位:fast=冲刺(全速干完即歇,race-to-idle);balanced=日常读 TTS 默认
# (有效RTF≈0.46,温和有余量);quiet=播客默认/图书馆级(0.45s/帧 → 有效RTF≈0.89,
# 占空比~40% 最凉,仍<1 可实时,靠加厚水位兜贴线风险)。
PERFORMANCE_PROFILES = {
    "fast": {
        "chunk_sleep": 0.0,
        "sentence_sleep": 0.2,
        "buffer_high_sec": 40.0,
        "buffer_low_sec": 15.0,
        "podcast_pause_poll_sec": 1.0,
        "model": None,
    },
    "balanced": {
        "chunk_sleep": 0.10,
        "sentence_sleep": 1.0,
        "buffer_high_sec": 25.0,
        "buffer_low_sec": 10.0,
        "podcast_pause_poll_sec": 2.0,
        "model": None,
    },
    "quiet": {
        "chunk_sleep": 0.45,
        "sentence_sleep": 3.0,
        "buffer_high_sec": 14.0,
        "buffer_low_sec": 6.0,
        "podcast_pause_poll_sec": 3.0,
        "model": DEFAULT_TTS_MODEL,
    },
}


def get_performance_profile(name: str | None) -> dict:
    profile_name = name if name in PERFORMANCE_PROFILES else "balanced"
    profile = PERFORMANCE_PROFILES[profile_name].copy()
    profile["name"] = profile_name
    return profile


def estimate_reading_minutes(text: str) -> float:
    zh_chars = len([ch for ch in text if "\u4e00" <= ch <= "\u9fff"])
    en_words = len([w for w in re.split(r"\s+", text) if w.strip()])
    return (zh_chars / 250.0) + (en_words / 150.0)
