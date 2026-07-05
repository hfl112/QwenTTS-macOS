"""InferenceEngine — the deep module above the ModelBackend seam.

Per ADR-001 (CONTEXT.md §3): one module owns prompt-kwargs building, audio
normalization, and read-through caching. The model itself is reached only
through a ModelBackend, so all of this is unit-testable with FakeBackend.

Step 2 scope: synthesize_local() — a same-process generator. The cross-process
worker loop, priority queue, and TTSRequest land in Step 3.
"""

import hashlib
import os
import queue as _queue
import time
from typing import Iterator, Optional

import numpy as np

from core import speakers
from core.constants import DEFAULT_TTS_MODEL

# Frame size used to slice cached audio back out on a cache hit (parity with the
# legacy inference_worker, which replayed cache in ~SR-sized chunks).
_CACHE_REPLAY_FRAME = 16000

_PUNCT_ENDINGS = (".", "。", "!", "！", "?", "？", ";", "；")


def _has_chinese(text: str) -> bool:
    return any("一" <= c <= "鿿" for c in text)


def cache_key(text: str, voice: str, model: str, lang: str, speed: float = 1.0) -> str:
    """Composite cache key — fixes the legacy bug where the key hashed *text
    only*, so the same sentence in a different voice/model/lang/speed collided and
    replayed the wrong audio. (CONTEXT.md §3, decision #4.)"""
    raw = f"{model}\x1f{voice}\x1f{lang}\x1f{speed:.2f}\x1f{text}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def normalize_frame(mono: np.ndarray) -> np.ndarray:
    """Mono raw frame -> normalized stereo float32, [N, 2] in [-0.98, 0.98].

    Lifted from tts_engine.generate_stream: stereo broadcast, robust 99.5th-
    percentile gain (caps clicks), 6x ceiling, hard clip.
    """
    mono = np.asarray(mono, dtype=np.float32).reshape(-1)
    stereo = np.stack([mono, mono], axis=1)  # [N, 2]
    abs_samples = np.abs(stereo)
    if abs_samples.size > 0:
        robust_peak = np.percentile(abs_samples, 99.5)
        if robust_peak > 0.002:
            gain = min(0.85 / robust_peak, 6.0)
            stereo = stereo * gain
    return np.clip(stereo, -0.98, 0.98).astype(np.float32)


def trim_silence(audio: np.ndarray, sr: int = 24000, pad_ms: int = 20) -> np.ndarray:
    """Trim leading/trailing near-silence from one synthesized chunk (mono or
    stereo). Each chunk carries model-generated head/tail silence (amplified by
    the trailing ``"。  "`` text padding in build_generate_kwargs); leaving it in
    makes both podcasts (concatenated) and live reads (sequential) audibly choppy
    at every sentence boundary (Bug 1). A short pad keeps word onsets/tails intact.
    Shared by the podcast assembler and the read lane so there's one trim, not two."""
    a = np.asarray(audio)
    if a.size == 0:
        return a
    env = np.abs(a).max(axis=1) if a.ndim == 2 else np.abs(a)
    peak = float(env.max())
    if peak <= 0:
        return a[:0]
    thresh = max(0.02 * peak, 0.004)
    loud = np.where(env > thresh)[0]
    if loud.size == 0:
        return a[:0]
    pad = int(sr * pad_ms / 1000)
    start = max(0, int(loud[0]) - pad)
    end = min(len(a), int(loud[-1]) + 1 + pad)
    return a[start:end]


def trim_leading_silence(audio: np.ndarray, sr: int = 24000, pad_ms: int = 20) -> np.ndarray:
    """Trim leading near-silence streamingly for the very first frame of a chunk."""
    a = np.asarray(audio)
    if a.size == 0:
        return a
    env = np.abs(a).max(axis=1) if a.ndim == 2 else np.abs(a)
    peak = float(env.max())
    if peak <= 0:
        return a[:0]
    thresh = max(0.02 * peak, 0.004)
    loud = np.where(env > thresh)[0]
    if loud.size == 0:
        return a[:0]
    pad = int(sr * pad_ms / 1000)
    start = max(0, int(loud[0]) - pad)
    return a[start:]


# --- podcast output-quality guard (garble / AR "code degeneration" detector) ---
#
# Qwen3-TTS is autoregressive over audio codec tokens. On a podcast chunk (ICL
# voice-cloning on, EOS-only termination) the model occasionally fails to stop
# and hallucinates audio codes off the trailing padding — a sustained, noise-like,
# low-energy "e...o" retching/dry-heaving sound (confirmed 2026-07-05 on a 33-min
# podcast, artifact at 26:11–26:18). There is otherwise NO output-quality guard in
# the pipeline: a garbled chunk is normalized, written, and concatenated verbatim.
# This detector's signature (noise-like AND quiet-vs-speech AND sustained) does not
# occur in clean speech/breaths/laughs, so it is safe to trigger a re-synthesis.
# Thresholds derived from the confirmed artifact vs. clean speech (per-0.5s-frame,
# measured 2026-07-05): clean speech sits at RMS≈0.25, flatness≈0.005, centroid<1.1kHz;
# the artifact sat at RMS 0.01–0.035, flatness 0.03–0.14, centroid 3.5–5kHz. A garble
# frame is noise-like AND (quiet relative to the chunk's own speech — the normal
# "runs past EOS, tail hiss after real speech" case — OR strongly noise-like in
# absolute terms — covers a garble-dominant chunk where the relative reference is
# itself corrupt). Clean speech never sustains either condition for >1s.
GARBLE_FLATNESS = 0.035        # spectral flatness above this = noise-like (speech ≈ 0.005)
GARBLE_FLATNESS_STRONG = 0.06  # so noise-like it counts regardless of loudness (speech peaks ≈ 0.035, only in brief fricatives)
GARBLE_QUIET_RATIO = 0.25      # frame RMS below this fraction of the chunk's speech level
GARBLE_ABS_FLOOR = 0.003       # ignore true digital silence (RMS below this)
GARBLE_MIN_SECONDS = 1.2       # a garble run must last at least this long to count


def detect_garble(audio: np.ndarray, sr: int = 24000):
    """Detect the AR "code degeneration" artifact in one synthesized chunk.

    Returns (is_garble, garble_seconds, reason). Fires only on a *sustained* run
    of frames that are simultaneously noise-like (high spectral flatness) and quiet
    relative to the chunk's own speech level — the exact signature of the model
    hallucinating audio codes after it should have emitted EOS. `garble_seconds`
    (the longest such run) doubles as a quality score: smaller is better, so the
    retry loop can keep the least-garbled attempt."""
    a = np.asarray(audio, dtype=np.float32)
    mono = a.mean(axis=1) if a.ndim == 2 else a
    win = int(0.5 * sr)
    hop = int(0.25 * sr)
    if mono.size < win:  # < 0.5s — too short to judge
        return False, 0.0, ""
    n = 1 + (len(mono) - win) // hop
    if n <= 0:
        return False, 0.0, ""
    w = np.hanning(win)
    rms = np.empty(n, dtype=np.float64)
    flat = np.empty(n, dtype=np.float64)
    for i in range(n):
        seg = mono[i * hop : i * hop + win]
        rms[i] = np.sqrt(np.mean(seg.astype(np.float64) ** 2) + 1e-12)
        sp = np.abs(np.fft.rfft(seg * w)) ** 2 + 1e-12
        flat[i] = np.exp(np.mean(np.log(sp))) / np.mean(sp)
    speech_level = float(np.percentile(rms, 90))
    if speech_level < 1e-3:  # essentially silent chunk — nothing to judge
        return False, 0.0, ""
    audible = rms > GARBLE_ABS_FLOOR
    noise_like = flat > GARBLE_FLATNESS
    quiet = rms < GARBLE_QUIET_RATIO * speech_level
    strong_noise = flat > GARBLE_FLATNESS_STRONG
    garble = audible & noise_like & (quiet | strong_noise)
    # longest consecutive garble run (in seconds)
    best = cur = 0
    for g in garble:
        cur = cur + 1 if g else 0
        best = max(best, cur)
    garble_seconds = best * hop / sr
    if garble_seconds >= GARBLE_MIN_SECONDS:
        return True, garble_seconds, f"{garble_seconds:.1f}s noise-like low-energy run"
    return False, garble_seconds, ""


def adjust_speed_numpy(audio, speed_factor: float) -> np.ndarray:
    """Adjust the speed of the audio by resampling (linear interpolation).
    speed_factor > 1: faster
    speed_factor < 1: slower
    """
    audio_np = np.asarray(audio, dtype=np.float32)
    if abs(speed_factor - 1.0) < 0.01 or speed_factor <= 0:
        return audio_np
    
    old_length = len(audio_np)
    new_length = int(old_length / speed_factor)
    if new_length <= 0:
        return audio_np
        
    old_indices = np.arange(old_length)
    new_indices = np.linspace(0, old_length - 1, new_length)
    
    if audio_np.ndim == 2:
        ch0 = np.interp(new_indices, old_indices, audio_np[:, 0])
        ch1 = np.interp(new_indices, old_indices, audio_np[:, 1])
        return np.stack([ch0, ch1], axis=1)
    else:
        return np.interp(new_indices, old_indices, audio_np)


def build_generate_kwargs(text: str, config: dict, reference_base: Optional[str]):
    """Faithful port of the prompt-kwargs construction from tts_engine: text
    padding, dynamic max_tokens, per-chunk language autodetect, base sampling
    params, global ICL voice-locking injection, and cross-language redirect.

    Returns (text_to_generate, generate_kwargs, resolved_lang).
    """
    text_to_generate = text.strip()
    if not any(text_to_generate.endswith(p) for p in _PUNCT_ENDINGS):
        text_to_generate += "。"
    text_to_generate += "  "

    dynamic_max_tokens = max(2048, len(text_to_generate) * 20)
    dynamic_max_tokens = min(dynamic_max_tokens, 8192)

    current_lang_code = config.get("lang_code", "zh")
    has_zh = _has_chinese(text_to_generate)
    if current_lang_code == "zh" and not has_zh:
        current_lang_code = "en"
    elif current_lang_code == "en" and has_zh:
        current_lang_code = "zh"

    generate_kwargs = {
        "voice": config.get("voice", "Serena"),
        "instruct": config.get("instruct", "Professional female anchor, steady and clear."),
        "temperature": config.get("temperature", 0.2),
        "top_p": config.get("top_p", 0.5),
        "top_k": config.get("top_k", 10),
        "repetition_penalty": config.get("repetition_penalty", 1.1),
        "lang_code": current_lang_code,
        "stream": True,
        "streaming_interval": 0.5,
        "response_format": "pcm",
        "max_tokens": dynamic_max_tokens,
    }
    if "ref_audio" in config:
        generate_kwargs["ref_audio"] = config["ref_audio"]
    if "ref_text" in config:
        generate_kwargs["ref_text"] = config["ref_text"]

    # P1(2026-07-01 探针实锤):ICL 音色克隆让每次生成慢 4.6 倍(RTF 1.81 vs
    # 0.39)——实时读通道 config 传 use_icl=False 跳过注入,用官方原生预设音色
    # (Ryan/Serena 本就是 Qwen3-TTS 内置 speaker);播客通道不传即默认 True,
    # 音色克隆一致性零变化。
    if config.get("use_icl", True) and reference_base:
        _inject_icl(generate_kwargs, reference_base)
        _redirect_cross_language_icl(generate_kwargs, reference_base)

    return text_to_generate, generate_kwargs, current_lang_code


def _inject_icl(generate_kwargs: dict, base_ref_path: str) -> None:
    """Global ICL voice-locking to prevent zero-shot voice drift. 身份数据
    单一定义在 core/speakers.py(C5.3);无跨 lang 回退,与历史一致。"""
    if "ref_audio" in generate_kwargs:
        return
    voice = generate_kwargs.get("voice", "Serena")
    lang = generate_kwargs.get("lang_code", "zh")

    entry = speakers.ref_entry(base_ref_path, voice, lang)
    if entry is not None and os.path.exists(entry[0]):
        generate_kwargs["ref_audio"], generate_kwargs["ref_text"] = entry


def _redirect_cross_language_icl(generate_kwargs: dict, base_ref_path: str) -> None:
    """If the ICL prompt text and the target generation language disagree,
    redirect to a same-language prompt, else fall back to zero-shot to avoid
    autoregressive collapse (verbatim logic from tts_engine)."""
    ref_text = generate_kwargs.get("ref_text", "")
    lang = generate_kwargs.get("lang_code", "zh")
    voice = generate_kwargs.get("voice", "Serena")
    if not (ref_text and lang):
        return
    ref_lang = "zh" if _has_chinese(ref_text) else "en"
    if ref_lang == lang:
        return

    redirected = False
    entry = speakers.ref_entry(base_ref_path, voice, lang)
    if entry is not None and os.path.exists(entry[0]):
        generate_kwargs["ref_audio"], generate_kwargs["ref_text"] = entry
        redirected = True

    if not redirected:
        generate_kwargs.pop("ref_audio", None)
        generate_kwargs.pop("ref_text", None)


class InferenceEngine:
    """Owns synthesis logic above the ModelBackend seam: kwargs, normalization,
    and read-through caching. Step 3 adds the worker loop + priority queue."""

    def __init__(
        self,
        backend,
        cache_dir: str,
        storage=None,
        reference_base: Optional[str] = None,
        max_cache_items: int = 10,
        models_path: Optional[str] = None,
    ):
        self.backend = backend
        self.cache_dir = cache_dir
        self.storage = storage
        self.reference_base = reference_base
        self.max_cache_items = max_cache_items
        self.models_path = models_path
        self.current_model: Optional[str] = None
        os.makedirs(self.cache_dir, exist_ok=True)

    # --- model lifecycle (owned by the engine, per ADR-001 decision #5) ---

    @property
    def is_loaded(self) -> bool:
        return self.backend.is_loaded

    def ensure_model(self, model_name: str) -> None:
        """Load `model_name`, switching the backend if a different model is
        currently resident."""
        if self.current_model == model_name and self.backend.is_loaded:
            return
        if self.models_path and not os.path.isabs(model_name):
            abs_path = os.path.join(self.models_path, model_name)
        else:
            abs_path = model_name
        if self.current_model != model_name:
            self.backend.unload()
            print(f"[InferenceEngine] 模型切换 -> {model_name}")
        self.backend.load(abs_path)
        self.current_model = model_name

    def idle_unload(self) -> None:
        if self.backend.is_loaded:
            print("[InferenceEngine] 空闲自动卸载模型...")
            self.backend.unload()
            self.current_model = None

    @staticmethod
    def _apply_model_hardening(config: dict) -> dict:
        """0.6B base models need a persona anchor prepended to instruct
        (ported from inference_worker)."""
        model = config.get("model", "")
        if "0.6B" in model:
            config = dict(config)
            voice = config.get("voice", "Serena")
            config["instruct"] = f"Persona Anchor: {voice}. " + config.get("instruct", "")
        return config

    def cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.npy")

    def synthesize_local(
        self, text: str, config: dict, use_cache: bool = True
    ) -> Iterator[np.ndarray]:
        """Yield normalized stereo frames for `text`. Read-through cache: a hit
        replays cached audio without touching the backend (no GPU); a miss
        synthesizes, normalizes, and stores. Podcast synth passes
        use_cache=False so it neither pollutes nor evicts the read cache."""
        text_to_generate, generate_kwargs, lang = build_generate_kwargs(
            text, config, self.reference_base
        )
        voice = generate_kwargs.get("voice", "Serena")
        model = config.get("model", DEFAULT_TTS_MODEL)
        speed_factor = float(config.get("speed", 1.0))
        key = cache_key(text, voice, model, lang, speed=speed_factor)
        path = self.cache_path(key)

        # 1. Cache hit — replay without the backend.
        if use_cache and os.path.exists(path):
            try:
                cached = np.load(path)
                # #8 R5:命中即"touch"(刷新 DB created_at),常用条目不被按-新旧淘汰误清
                if self.storage is not None:
                    touch = getattr(self.storage, "touch_cache", None)
                    if touch:
                        try:
                            touch(key)
                        except Exception as touch_err:
                            print(f"[InferenceEngine] touch_cache failed: {touch_err}")
                for s in range(0, len(cached), _CACHE_REPLAY_FRAME):
                    yield cached[s : s + _CACHE_REPLAY_FRAME]
                return
            except Exception as e:
                print(f"[InferenceEngine] Cache replay failed, re-synthesizing: {e}")

        # 2. Cache miss — synthesize through the backend, normalize, store.
        frames = []
        pending_mono = []
        pending_samples = 0
        is_first = True
        
        # 24kHz buffering thresholds
        FIRST_FRAME_THRESHOLD = 2400      # 0.1s for stable silence trimming
        SUBSEQUENT_FRAME_THRESHOLD = 12000 # 0.5s to reduce IPC overhead

        for raw_mono in self.backend.generate(text_to_generate, generate_kwargs):
            if abs(speed_factor - 1.0) >= 0.01:
                raw_mono = adjust_speed_numpy(raw_mono, speed_factor)
            
            pending_mono.append(raw_mono)
            pending_samples += len(raw_mono)
            
            if is_first:
                if pending_samples >= FIRST_FRAME_THRESHOLD:
                    concat_mono = np.concatenate(pending_mono)
                    frame = normalize_frame(concat_mono)
                    frames.append(frame)
                    is_first = False
                    yield frame
                    pending_mono = []
                    pending_samples = 0
            else:
                if pending_samples >= SUBSEQUENT_FRAME_THRESHOLD:
                    concat_mono = np.concatenate(pending_mono)
                    frame = normalize_frame(concat_mono)
                    frames.append(frame)
                    yield frame
                    pending_mono = []
                    pending_samples = 0

        if pending_mono:
            concat_mono = np.concatenate(pending_mono)
            frame = normalize_frame(concat_mono)
            frames.append(frame)
            yield frame

        if use_cache and frames:
            self._store(key, path, text, voice, model, frames, config.get("source", ""))

    def _store(self, key, path, text, voice, model, frames, source: str = "") -> None:
        try:
            concat = np.concatenate(frames)
            np.save(path, concat)
            if self.storage is not None:
                duration = len(concat) / 24000.0
                try:
                    self.storage.add_cache_metadata(
                        md5=key,
                        text=text,
                        model=model,
                        voice=voice,
                        duration=duration,
                        file_path=path,
                        source=source,
                    )
                except Exception as db_err:
                    print(f"[InferenceEngine] Failed to save cache metadata: {db_err}")
                self.evict_cache()
        except Exception as save_err:
            print(f"[InferenceEngine] Save cache failed: {save_err}")

    def evict_cache(self) -> None:
        """Drop cache entries beyond max_cache_items, DB created_at order
        authoritative (mirrors the legacy manage_cache_limit)."""
        if self.storage is None:
            return
        try:
            rows = self.storage.get_all_cache()  # newest-first
            cache_root = os.path.abspath(self.cache_dir)
            for row in rows[self.max_cache_items :]:
                fp = row.get("file_path")
                if fp and os.path.exists(fp):
                    # P0-2 纵深防御:淘汰只允许删 Cache 目录自己的文件——万一元数据
                    # 里混进指向别处(如 Podcasts 成品)的路径,拒删并报警,绝不误伤。
                    if not os.path.abspath(fp).startswith(cache_root + os.sep):
                        print(f"[InferenceEngine] REFUSED to evict non-cache path: {fp}")
                        continue
                    try:
                        os.remove(fp)
                    except OSError as e:
                        print(f"[InferenceEngine] Failed to remove {fp}: {e}")
                md5_val = row.get("md5")
                if md5_val:
                    try:
                        self.storage.delete_cache_by_md5(md5_val)
                    except Exception as e:
                        print(f"[InferenceEngine] Failed to delete row {md5_val}: {e}")
        except Exception as e:
            print(f"[InferenceEngine] evict_cache error: {e}")

    # --- the inference process loop (owns both lanes) ---

    def run_loop(self, shared_state, sentinel, profile_fn, idle_unload_sec: float = 600.0) -> None:
        """The single inference process. Drains the read lane (text_q) first so
        reads always preempt podcast work at chunk boundaries (ADR-001 #2), then
        the podcast lane (podcast_q). One model, GPU serialized for free."""
        print(f"[InferenceProcess] 启动成功, PID: {os.getpid()}")
        last_active = time.time()
        metal_warning_reported = False
        while True:
            try:
                try:
                    shared_state.vram_mb.value = self.backend.active_memory_mb()
                    metal_warning_reported = False
                except RuntimeError as error:
                    shared_state.vram_mb.value = 0.0
                    if not metal_warning_reported:
                        print(f"[InferenceProcess] Metal memory query unavailable: {error}")
                        metal_warning_reported = True

                task, is_podcast = self._next_task(shared_state, idle_unload_sec, last_active)
                if task is _IDLE:
                    continue
                last_active = time.time()

                if is_podcast:
                    self._handle_podcast_task(shared_state, task, profile_fn)
                    continue

                # --- read lane: existing text_q/audio_q protocol, preserved ---
                if task is None:
                    break
                # End-of-article marker, tagged (sentinel, task_id). Only forward the
                # CURRENT session's marker; a superseded session's marker is dropped
                # so it can't finish a newer read (which left its chunks generating
                # headless). Bare-string sentinel kept as a defensive fallback.
                if isinstance(task, tuple) and len(task) == 2 and task[0] == sentinel:
                    if task[1] == shared_state.current_task_id.value:
                        shared_state.audio_q.put((sentinel, task[1]))
                    continue
                if isinstance(task, str) and task == sentinel:
                    shared_state.audio_q.put((sentinel, shared_state.current_task_id.value))
                    continue
                if not isinstance(task, dict):
                    continue
                task_id = task.get("task_id", -1)
                chunk_index = task.get("chunk_index", -1)
                if task_id != shared_state.current_task_id.value:
                    continue

                config = self._apply_model_hardening(task["config"])
                self.ensure_model(config.get("model", DEFAULT_TTS_MODEL))
                profile = profile_fn(config.get("performance_profile"))
                throttle = profile.get("chunk_sleep", 0.0) if isinstance(profile, dict) else 0.0
                # Tuning override (A/B for the read-lane underrun probe): TTS_READ_THROTTLE
                # forces the per-frame sleep, bypassing the profile's chunk_sleep. Unset =
                # current behavior. Set to 0 to remove the read-lane throttle entirely.
                _env_throttle = os.environ.get("TTS_READ_THROTTLE")
                if _env_throttle is not None:
                    try:
                        throttle = float(_env_throttle)
                    except ValueError:
                        pass

                # E3: Streaming Emission. Emits frames incrementally as they are synthesized.
                # Lowers the first-sound latency to less than 1.0s.
                def _still_current() -> bool:
                    return not shared_state.stop_event.is_set() and task_id == shared_state.current_task_id.value

                is_first_frame = True
                for frame in self.synthesize_local(task["text"], config):
                    if not _still_current():
                        break
                    if is_first_frame:
                        # Trim leading silence on the first frame to avoid pops, then emit immediately.
                        trimmed_first = trim_leading_silence(frame)
                        if trimmed_first.size > 0:
                            shared_state.audio_q.put((task_id, chunk_index, trimmed_first))
                            shared_state.note_audio_frame()
                            is_first_frame = False
                    else:
                        shared_state.audio_q.put((task_id, chunk_index, frame))
                        shared_state.note_audio_frame()
                    if throttle:
                        time.sleep(throttle)
                shared_state.audio_q.put("CHUNK_DONE")
            except Exception as e:
                import traceback

                print(f"[InferenceProcess] 异常: {e}")
                traceback.print_exc()
                try:
                    shared_state.set_error(str(e))
                except Exception:
                    pass
                time.sleep(1.0)

    def _next_task(self, shared_state, idle_unload_sec, last_active):
        """Read-priority task pickup: text_q (reads) before podcast_q, else a
        short blocking wait on reads that doubles as the idle-unload tick.
        Returns (task, is_podcast); task is _IDLE when nothing was available."""
        try:
            return shared_state.text_q.get_nowait(), False
        except _queue.Empty:
            pass
        pq = getattr(shared_state, "podcast_q", None)
        if pq is not None:
            try:
                return pq.get_nowait(), True
            except _queue.Empty:
                pass
        try:
            return shared_state.text_q.get(timeout=2), False
        except _queue.Empty:
            if self.is_loaded and (time.time() - last_active > idle_unload_sec):
                self.idle_unload()
            return _IDLE, False

    # Podcast chunks that come back garbled (AR code degeneration — the "e...o"
    # retching artifact) are re-synthesized with escalating "escape" params, up to
    # this many extra attempts. Kept small: a bad chunk is rare and each retry
    # re-runs the model. Overridable via TTS_PODCAST_GARBLE_RETRIES (0 = disable).
    PODCAST_GARBLE_RETRIES = 2

    def _garble_retry_config(self, config: dict, attempt: int) -> dict:
        """Escalating params that steer the model out of degeneration on retry.
        attempt 1: force high repetition_penalty + lower temperature (targets the
        documented ICL-prefill code degeneration). attempt 2: also drop ICL cloning
        (use_icl=False → native preset speaker, no long reference prefill), which
        removes the trigger entirely at a small timbre-consistency cost — far better
        than shipping a retching chunk."""
        c = dict(config)
        c["repetition_penalty"] = max(float(config.get("repetition_penalty", 1.1)), 1.5)
        c["temperature"] = min(float(config.get("temperature", 0.2)), 0.15)
        if attempt >= 2:
            c["use_icl"] = False
        return c

    def _synth_chunk(self, text, config, epoch_ref, epoch0, throttle):
        """Synthesize one chunk fully. Returns the frame list, or None if the
        run was aborted mid-synthesis by a cancel (epoch bump)."""
        frames = []
        for frame in self.synthesize_local(text, config, use_cache=False):
            if epoch_ref is not None and epoch_ref.value != epoch0:
                print("[InferenceEngine] Podcast chunk aborted mid-synthesis by cancel")
                return None
            frames.append(frame)
            if throttle:
                time.sleep(throttle)
        return frames

    def _handle_podcast_task(self, shared_state, task, profile_fn) -> None:
        """Synthesize one podcast chunk fully and write chunk_{idx}.npy (the
        format write_podcast_wav_from_chunks already expects). On failure write
        a sibling `.err` marker so the polling subprocess fails fast instead of
        hanging. use_cache=False keeps the read cache untouched.

        Quality guard: after synthesis the chunk is screened by detect_garble; a
        detected AR-degeneration artifact triggers re-synthesis with escape params
        (see _garble_retry_config), keeping the least-garbled attempt.

        后台静默:播客合成按档位(默认 quiet → chunk_sleep=0.25s)逐帧让出 GPU/CPU,
        避免"后台生成"把机器烧满。这条节流原本随 ADR-001 删掉的旧 inference_worker
        一起丢了,现按任务补回(只作用于播客分支,不影响前台读的 balanced 速度)。
        TTS_PODCAST_THROTTLE 可覆盖,想更慢直接调大,无需改代码。"""
        chunk_file = task.get("chunk_file")
        try:
            config = self._apply_model_hardening(task["config"])
            self.ensure_model(config.get("model", DEFAULT_TTS_MODEL))
            profile = profile_fn(config.get("performance_profile"))
            throttle = profile.get("chunk_sleep", 0.0) if isinstance(profile, dict) else 0.0
            _env_throttle = os.environ.get("TTS_PODCAST_THROTTLE")
            if _env_throttle is not None:
                try:
                    throttle = float(_env_throttle)
                except ValueError:
                    pass
            # P0-1:取消代际快照。cancel_all 会 bump 它;逐帧对照,变了就中途
            # 掐断——编排进程已被杀,继续合成纯属白烧 GPU(quiet 档一段可达数分钟)。
            epoch_ref = getattr(shared_state, "podcast_cancel_epoch", None)
            epoch0 = epoch_ref.value if epoch_ref is not None else None

            max_retries = self.PODCAST_GARBLE_RETRIES
            _env_retries = os.environ.get("TTS_PODCAST_GARBLE_RETRIES")
            if _env_retries is not None:
                try:
                    max_retries = int(_env_retries)
                except ValueError:
                    pass

            frames = self._synth_chunk(task["text"], config, epoch_ref, epoch0, throttle)
            if frames is None:
                return  # cancelled mid-synthesis
            best_frames, best_score = frames, self._garble_score(frames)
            attempt = 0
            while best_score >= GARBLE_MIN_SECONDS and attempt < max_retries:
                attempt += 1
                print(
                    f"[InferenceEngine] Podcast chunk garble ({best_score:.1f}s), "
                    f"re-synthesizing (attempt {attempt}/{max_retries})"
                )
                retry_frames = self._synth_chunk(
                    task["text"],
                    self._garble_retry_config(config, attempt),
                    epoch_ref, epoch0, throttle,
                )
                if retry_frames is None:
                    return  # cancelled mid-retry
                score = self._garble_score(retry_frames)
                if score < best_score:  # keep the least-garbled attempt
                    best_frames, best_score = retry_frames, score
                if best_score < GARBLE_MIN_SECONDS:
                    break
            frames = best_frames

            if chunk_file:
                if not frames:
                    raise RuntimeError("no audio frames produced for podcast chunk")
                # Write to a sibling then atomically rename, so the polling
                # subprocess never observes a half-written chunk file.
                building = chunk_file + ".building.npy"  # ends in .npy → np.save keeps it
                np.save(building, np.concatenate(frames))
                os.replace(building, chunk_file)
        except Exception as e:
            if chunk_file:
                try:
                    with open(chunk_file + ".err", "w", encoding="utf-8") as f:
                        f.write(str(e))
                except Exception:
                    pass

    @staticmethod
    def _garble_score(frames) -> float:
        """Longest garble run (seconds) over a chunk's frames; 0.0 if clean/empty."""
        if not frames:
            return 0.0
        _, seconds, _ = detect_garble(np.concatenate(frames))
        return seconds


# Sentinel distinguishing "no task this tick" from a real None (shutdown) task.
_IDLE = object()
