"""M3(计划 #13)——现役 TTS 模型名的单一真相(core.constants.DEFAULT_TTS_MODEL)。

历史教训(§4l):裸回退曾写未量化 "Qwen3-TTS-0.6B"(RTF 1.08 追不上实时),
config 一缺 model 字段就静默跑回卡顿模型,且电池 quiet 分支直接钉了慢模型。
"""

import os

from core.constants import DEFAULT_TTS_MODEL
from core.services.performance import get_performance_profile
from core.services.podcast_service import prepare_podcast_config
from core.storage import Storage


def test_constant_is_the_quantized_model():
    assert DEFAULT_TTS_MODEL == "Qwen3-TTS-0.6B-4bit"


def test_storage_default_and_quiet_profile_pin_same_model():
    """两处既有钉子(storage 默认 config、quiet 档)与常量同源——换模型只改一处。"""
    assert Storage().default_config["model"] == DEFAULT_TTS_MODEL
    assert get_performance_profile("quiet")["model"] == DEFAULT_TTS_MODEL


def test_prepare_podcast_config_battery_quiet_uses_default_model():
    """电池 quiet 分支曾直接钉未量化 bf16(真 bug):电池上反而跑最慢的模型。"""
    cfg = prepare_podcast_config({"force_battery_quiet": True}, "短文本")
    assert cfg["model"] == DEFAULT_TTS_MODEL


def test_prepare_podcast_config_small_model_fallback_uses_default_model():
    """force_small_model 且 profile 无 model 键时,回退也必须是现役量化模型。"""
    cfg = prepare_podcast_config(
        {"podcast_performance_profile": "fast"}, "长文" * 60000, force_small_model=True
    )
    # fast 档无 model 钉子 → 走 or-回退;quiet 档有钉子,两条路答案必须一致
    assert cfg["model"] == DEFAULT_TTS_MODEL


def test_no_bare_bf16_fallback_literals_in_source():
    """防回流:engine.py / podcast_service.py 不得再出现裸 "Qwen3-TTS-0.6B" 字面量
    (常量定义本体在 core/constants.py,不在扫描范围)。"""
    core_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("inference/engine.py", "services/podcast_service.py"):
        with open(os.path.join(core_dir, rel), encoding="utf-8") as f:
            src = f.read()
        assert '"Qwen3-TTS-0.6B"' not in src, f"{rel} 仍有裸 bf16 模型名回退"
