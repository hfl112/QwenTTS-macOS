"""Step 2 acceptance (CONTEXT.md §4): InferenceEngine kwargs/normalization/cache.

All exercised through FakeBackend — no GPU, no mlx.
"""

import os

import numpy as np

from core.inference.engine import (
    InferenceEngine,
    build_generate_kwargs,
    cache_key,
    normalize_frame,
)
from core.inference.model_backend import FakeBackend


class CountingBackend(FakeBackend):
    """FakeBackend that records how many times generate() is invoked."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.generate_calls = 0

    def generate(self, text, generate_kwargs):
        self.generate_calls += 1
        yield from super().generate(text, generate_kwargs)


class FakeStorage:
    def __init__(self, rows):
        self._rows = list(rows)  # newest-first
        self.deleted = []

    def add_cache_metadata(self, **kw):
        pass

    def get_all_cache(self):
        return list(self._rows)

    def delete_cache_by_md5(self, md5):
        self.deleted.append(md5)


def _engine(tmp_path, backend=None, storage=None):
    be = backend or FakeBackend()
    be.load("/fake")
    return InferenceEngine(be, cache_dir=str(tmp_path), storage=storage), be


# --- 串音 bug regression: key must distinguish voice / model / lang ---

def test_cache_key_distinguishes_voice():
    k1 = cache_key("你好世界", "Serena", "Qwen3-TTS-0.6B", "zh")
    k2 = cache_key("你好世界", "Ryan", "Qwen3-TTS-0.6B", "zh")
    assert k1 != k2, "same text, different voice must not collide"


def test_cache_key_distinguishes_model_and_lang():
    base = cache_key("hi", "Serena", "Qwen3-TTS-0.6B", "en")
    assert base != cache_key("hi", "Serena", "Qwen3-TTS-1.7B", "en")
    assert base != cache_key("hi", "Serena", "Qwen3-TTS-0.6B", "zh")


# --- read-through cache: hit must skip the backend (no GPU) ---

def test_cache_hit_skips_backend(tmp_path):
    eng, be = _engine(tmp_path, backend=CountingBackend(frames=2))

    first = list(eng.synthesize_local("你好世界", {"voice": "Serena"}))
    assert be.generate_calls == 1
    assert len(first) > 0

    second = list(eng.synthesize_local("你好世界", {"voice": "Serena"}))
    assert be.generate_calls == 1, "cache hit must not call the backend again"
    assert len(second) > 0


def test_use_icl_switch_gates_reference_injection(tmp_path):
    """P1:config use_icl=False 跳过 ICL 参考音注入(原生预设音色,快 ~3x);
    缺省/True 保持注入(音色克隆,现状)。当前无调用方传 False——应急杠杆。

    #12 CI 修:参考音 wav 是 gitignored 二进制,CI 上不存在 → 原先依赖真实
    reference/ 目录会假红。_inject_icl 只做 os.path.exists 检查,touch 空文件
    即可驱动逻辑——测试改用自备 ref 目录,处处确定。"""
    from core import speakers
    from core.inference.engine import build_generate_kwargs

    ref_dir = tmp_path / "reference"
    ref_dir.mkdir()
    for filename, _text in speakers.REFS.values():
        (ref_dir / filename).touch()
    ref_base = str(ref_dir)

    _, kw_default, _ = build_generate_kwargs("你好世界", {"voice": "Ryan"}, ref_base)
    assert "ref_audio" in kw_default, "缺省应注入 ICL 参考音"

    _, kw_off, _ = build_generate_kwargs(
        "你好世界", {"voice": "Ryan", "use_icl": False}, ref_base
    )
    assert "ref_audio" not in kw_off, "use_icl=False 必须跳过 ICL 注入"


def test_cache_hit_touches_db_row(tmp_path):
    """#8 R5:命中即刷新 DB created_at(touch),常用条目不被按-新旧淘汰误清;miss 不 touch。"""

    class TouchTrackingStorage(FakeStorage):
        def __init__(self):
            super().__init__([])
            self.touched = []

        def touch_cache(self, md5):
            self.touched.append(md5)

    storage = TouchTrackingStorage()
    eng, be = _engine(tmp_path, backend=CountingBackend(frames=2), storage=storage)

    list(eng.synthesize_local("你好世界", {"voice": "Serena"}))  # miss → 生成+落库
    assert storage.touched == [], "首次是 miss,不应 touch"

    list(eng.synthesize_local("你好世界", {"voice": "Serena"}))  # hit → touch
    assert len(storage.touched) == 1
    assert be.generate_calls == 1


def test_different_voice_is_a_cache_miss(tmp_path):
    eng, be = _engine(tmp_path, backend=CountingBackend(frames=2))
    list(eng.synthesize_local("你好世界", {"voice": "Serena"}))
    list(eng.synthesize_local("你好世界", {"voice": "Ryan"}))
    assert be.generate_calls == 2, "different voice must re-synthesize, not replay"


# --- normalization: stereo, clamped to [-0.98, 0.98] ---

def test_normalize_frame_is_clamped_stereo():
    loud = np.full(1000, 5.0, dtype=np.float32)  # way over range
    out = normalize_frame(loud)
    assert out.ndim == 2 and out.shape[1] == 2
    assert out.dtype == np.float32
    assert float(np.max(np.abs(out))) <= 0.98 + 1e-6


def test_synthesized_frames_are_clamped(tmp_path):
    eng, _ = _engine(tmp_path)
    frames = list(eng.synthesize_local("hello world", {"voice": "Serena"}))
    assert frames
    for f in frames:
        assert f.shape[1] == 2
        assert float(np.max(np.abs(f))) <= 0.98 + 1e-6


# --- kwargs: per-chunk language autodetect ---

def test_kwargs_autodetect_language():
    # Declared zh but English text -> override to en.
    _, kw_en, lang_en = build_generate_kwargs("hello there", {"lang_code": "zh"}, None)
    assert lang_en == "en" and kw_en["lang_code"] == "en"
    # Declared en but Chinese text -> override to zh.
    _, kw_zh, lang_zh = build_generate_kwargs("你好啊", {"lang_code": "en"}, None)
    assert lang_zh == "zh" and kw_zh["lang_code"] == "zh"


def test_kwargs_max_tokens_capped():
    _, kw, _ = build_generate_kwargs("x" * 10000, {}, None)
    assert kw["max_tokens"] == 8192


# --- eviction mirrors manage_cache_limit ---

def test_evict_cache_drops_beyond_limit(tmp_path):
    rows = [{"md5": f"k{i}", "file_path": None} for i in range(13)]  # newest-first
    storage = FakeStorage(rows)
    eng, _ = _engine(tmp_path, storage=storage)
    eng.max_cache_items = 10
    eng.evict_cache()
    assert storage.deleted == ["k10", "k11", "k12"]


# --- Speed control and Resampling Smoke Tests ---

def test_cache_key_distinguishes_speed():
    k1 = cache_key("你好世界", "Serena", "Qwen3-TTS-0.6B", "zh", speed=1.0)
    k2 = cache_key("你好世界", "Serena", "Qwen3-TTS-0.6B", "zh", speed=1.5)
    assert k1 != k2, "same text and voice, different speed must not collide"


def test_speed_resampling_numpy():
    from core.inference.engine import adjust_speed_numpy
    audio = np.arange(1000, dtype=np.float32)
    # Test 2.0x (length should halve)
    fast = adjust_speed_numpy(audio, 2.0)
    assert len(fast) == 500
    # Test 0.5x (length should double)
    slow = adjust_speed_numpy(audio, 0.5)
    assert len(slow) == 2000


def test_engine_synthesize_with_speed(tmp_path):
    eng, be = _engine(tmp_path, backend=CountingBackend(frames=2))
    
    # 1.0x baseline (FakeBackend generate yields mock frames of length 24000)
    norm = list(eng.synthesize_local("hello world", {"voice": "Serena", "speed": 1.0}))
    total_norm_samples = sum(len(f) for f in norm)

    # 1.5x speed
    fast = list(eng.synthesize_local("hello world", {"voice": "Serena", "speed": 1.5}))
    total_fast_samples = sum(len(f) for f in fast)

    # Ensure length scales inversely with speed: 1.5x length should be ~2/3 of 1.0x length
    expected_len = int(total_norm_samples / 1.5)
    assert abs(total_fast_samples - expected_len) <= 5, f"Expected ~{expected_len} samples, got {total_fast_samples}"

