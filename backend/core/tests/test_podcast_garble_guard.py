"""Podcast output-quality guard — the AR "code degeneration" (e...o retching)
artifact detector + re-synthesis retry.

Root cause (CONTEXT.md / confirmed 2026-07-05 on a 33-min podcast, artifact at
26:11–26:18): Qwen3-TTS is autoregressive over audio codec tokens; on a podcast
chunk (ICL voice-cloning on, EOS-only termination) it occasionally fails to stop
and hallucinates codes off the trailing padding — a sustained, noise-like,
low-energy hiss. Nothing in the pipeline screened for it, so a garbled chunk was
written and concatenated verbatim. `detect_garble` catches the acoustic
signature; `_handle_podcast_task` re-synthesizes with escape params, keeping the
least-garbled attempt.

Thresholds were derived from real artifact-vs-speech measurements; these tests
reproduce the signature synthetically (no GPU, no mlx).
"""

import numpy as np

from core.inference.engine import (
    GARBLE_MIN_SECONDS,
    InferenceEngine,
    detect_garble,
    normalize_frame,
)
from core.inference.model_backend import FakeBackend

SR = 24000


def _tone(seconds: float, freq: float = 220.0, amp: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * SR), dtype=np.float32) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _noise(seconds: float, amp: float, seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    return (amp * rng.randn(int(seconds * SR))).astype(np.float32)


def _stereo(mono: np.ndarray) -> np.ndarray:
    return np.stack([mono, mono], axis=1).astype(np.float32)


# --- detect_garble: signature separation ---------------------------------------


def test_clean_tonal_speech_is_not_garble():
    is_bad, secs, _ = detect_garble(_stereo(_tone(3.0)))
    assert not is_bad
    assert secs == 0.0


def test_speech_then_quiet_noise_tail_is_garble():
    """The real-world shape: real speech, then the model runs past EOS and emits a
    quiet noise-like tail. The tail is quiet *relative to* the preceding speech."""
    mono = np.concatenate([_tone(1.5, amp=0.4), _noise(2.0, amp=0.03, seed=1)])
    is_bad, secs, reason = detect_garble(_stereo(mono))
    assert is_bad
    assert secs >= GARBLE_MIN_SECONDS
    assert "noise-like" in reason


def test_brief_fricative_burst_is_not_garble():
    """A short (<1s) noise burst inside speech (like a fricative) must NOT trip the
    guard — the run-length floor is what separates it from a real artifact."""
    mono = np.concatenate([_tone(1.0), _noise(0.4, amp=0.2, seed=2), _tone(1.0)])
    is_bad, _, _ = detect_garble(_stereo(mono))
    assert not is_bad


def test_too_short_audio_is_never_garble():
    is_bad, secs, _ = detect_garble(_stereo(_noise(0.2, amp=0.05)))
    assert not is_bad
    assert secs == 0.0


def test_silence_is_not_garble():
    is_bad, _, _ = detect_garble(_stereo(np.zeros(3 * SR, dtype=np.float32)))
    assert not is_bad


def test_normalize_gain_cap_preserves_quietness():
    """The detector's premise: normalize_frame's 6x gain ceiling means a quiet
    garble frame stays quiet (≈0.03) instead of being amplified up to speech level,
    so 'quiet relative to speech' survives per-frame normalization."""
    loud = normalize_frame(_tone(0.5, amp=0.4))
    quiet_noise = normalize_frame(_noise(0.5, amp=0.005, seed=3))
    loud_rms = np.sqrt(np.mean(loud**2))
    quiet_rms = np.sqrt(np.mean(quiet_noise**2))
    assert quiet_rms < 0.25 * loud_rms  # stays well below speech after normalization


# --- retry loop in _handle_podcast_task ----------------------------------------


class _GarblingBackend(FakeBackend):
    """Yields a speech+garble-tail chunk for the first `bad_calls` generate()
    invocations, then a clean tone. Records the kwargs of every call so the test
    can assert the retry escalates sampling params."""

    def __init__(self, bad_calls: int = 1, **kw):
        super().__init__(**kw)
        self.calls = 0
        self.bad_calls = bad_calls
        self.seen_kwargs = []

    def generate(self, text, generate_kwargs):
        if self._loaded_path is None:
            raise RuntimeError("generate before load")
        self.calls += 1
        self.seen_kwargs.append(dict(generate_kwargs))
        if self.calls <= self.bad_calls:
            yield _tone(1.5, amp=0.4)              # real speech
            for i in range(4):
                yield _noise(0.5, amp=0.005, seed=100 + i)  # quiet garble tail
        else:
            yield _tone(3.0, amp=0.4)              # clean re-synthesis


class _State:
    """Minimal shared_state: no cancel epoch, so synthesis never aborts."""

    podcast_cancel_epoch = None


def _profile_fn(_name):
    return {"chunk_sleep": 0.0}


def _engine(backend):
    backend.load("/fake")
    return InferenceEngine(backend, cache_dir="/tmp/garble_test_cache")


def test_garbled_chunk_triggers_resynthesis_and_writes_clean_audio(tmp_path):
    be = _GarblingBackend(bad_calls=1)
    eng = _engine(be)
    chunk_file = str(tmp_path / "chunk_0.npy")
    task = {
        "chunk_file": chunk_file,
        "text": "自然界的退化是人类繁荣的系统性风险。",
        "config": {"model": "Qwen3-TTS-0.6B-4bit", "voice": "Serena"},
    }

    eng._handle_podcast_task(_State(), task, _profile_fn)

    assert be.calls == 2, "a garbled first attempt must trigger exactly one retry"
    assert (tmp_path / "chunk_0.npy").exists()
    assert not (tmp_path / "chunk_0.npy.err").exists()
    written = np.load(chunk_file)
    is_bad, secs, _ = detect_garble(written)
    assert not is_bad, f"written chunk should be the clean retry, got {secs:.1f}s garble"


def test_retry_escalates_repetition_penalty_and_temperature(tmp_path):
    be = _GarblingBackend(bad_calls=1)
    eng = _engine(be)
    task = {
        "chunk_file": str(tmp_path / "chunk_0.npy"),
        "text": "测试重试参数升级。",
        "config": {"model": "Qwen3-TTS-0.6B-4bit", "temperature": 0.2, "repetition_penalty": 1.1},
    }
    eng._handle_podcast_task(_State(), task, _profile_fn)

    first, retry = be.seen_kwargs[0], be.seen_kwargs[1]
    assert first["repetition_penalty"] == 1.1
    assert retry["repetition_penalty"] >= 1.5, "retry must raise repetition_penalty"
    assert retry["temperature"] <= 0.15, "retry must lower temperature"


def test_persistent_garble_keeps_least_garbled_and_still_writes(tmp_path):
    """If every attempt garbles, the guard must not loop forever or drop the chunk:
    it exhausts retries and writes the best (here: all equal) attempt, never .err."""
    be = _GarblingBackend(bad_calls=99)  # never produces clean audio
    eng = _engine(be)
    chunk_file = str(tmp_path / "chunk_0.npy")
    task = {
        "chunk_file": chunk_file,
        "text": "持续退化的情况。",
        "config": {"model": "Qwen3-TTS-0.6B-4bit"},
    }
    eng._handle_podcast_task(_State(), task, _profile_fn)

    assert be.calls == 1 + InferenceEngine.PODCAST_GARBLE_RETRIES  # initial + retries, then stop
    assert (tmp_path / "chunk_0.npy").exists(), "must still emit audio, not a hole"
    assert not (tmp_path / "chunk_0.npy.err").exists()


def test_clean_chunk_does_not_retry(tmp_path):
    be = _GarblingBackend(bad_calls=0)  # clean from the start
    eng = _engine(be)
    task = {
        "chunk_file": str(tmp_path / "chunk_0.npy"),
        "text": "一切正常的一句话。",
        "config": {"model": "Qwen3-TTS-0.6B-4bit"},
    }
    eng._handle_podcast_task(_State(), task, _profile_fn)
    assert be.calls == 1, "a clean chunk must synthesize exactly once"
