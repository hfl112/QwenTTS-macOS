"""播客档位独立设置(podcast_performance_profile)。

用户反馈(2026-07-05):播客后台连续推理烧机,档位却藏在客户端硬编码的请求值里,
且与读路径 performance_profile 纠缠不清。收口后:播客域只认独立设置
podcast_performance_profile(默认 quiet),读档位/请求值一律不生效,
prepare_podcast_config 是唯一解析点(单篇/合集两个 worker 都先过它)。
"""

from core.constants import DEFAULT_TTS_MODEL
from core.services.podcast_service import DEFAULT_PODCAST_PROFILE, prepare_podcast_config


def test_default_is_quiet():
    assert DEFAULT_PODCAST_PROFILE == "quiet"
    cfg = prepare_podcast_config({}, "短文本")
    assert cfg["performance_profile"] == "quiet"


def test_ignores_read_profile_and_request_value():
    """读路径设 balanced/请求带 fast,播客照样走自己的档(缺设置 → quiet)。"""
    cfg = prepare_podcast_config({"performance_profile": "balanced"}, "短文本")
    assert cfg["performance_profile"] == "quiet"


def test_honors_explicit_podcast_profile():
    for name in ("fast", "balanced", "quiet"):
        cfg = prepare_podcast_config(
            {"performance_profile": "balanced", "podcast_performance_profile": name},
            "短文本",
        )
        assert cfg["performance_profile"] == name


def test_invalid_value_falls_back_to_quiet():
    cfg = prepare_podcast_config({"podcast_performance_profile": "turbo"}, "短文本")
    assert cfg["performance_profile"] == "quiet"


def test_battery_force_quiet_still_wins():
    """电池 quiet 策略优先级高于用户播客档位(fast 也压回 quiet + 量化模型)。"""
    cfg = prepare_podcast_config(
        {"podcast_performance_profile": "fast", "force_battery_quiet": True}, "短文本"
    )
    assert cfg["performance_profile"] == "quiet"
    assert cfg["model"] == DEFAULT_TTS_MODEL
