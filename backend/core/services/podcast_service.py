import json
import multiprocessing as mp
import os
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from typing import Any, Callable

import numpy as np
import scipy.io.wavfile

from core import labels, reader_bridge
from core.constants import DEFAULT_TTS_MODEL
from core.hashing import text_md5
from core.inference.engine import trim_silence
from core.paths import runtime_paths
from core.processor import TextProcessor
from core.services import podcast_naming as pn
from core.services.podcast_jobs import PodcastJobStore, content_key
from core.services.performance import (
    PERFORMANCE_PROFILES,
    estimate_reading_minutes,
    get_performance_profile,
)
from core.services.runtime_supervisor import stop_process
from core.services.runtime_log import RuntimeEventLog

BATTERY_PODCAST_POLICIES = {"pause", "quiet", "allow"}

# 播客生成的档位默认值。播客档位只认独立设置 podcast_performance_profile
# (设置页"播客生成档位"),不继承读路径的 performance_profile,也不认请求值。
DEFAULT_PODCAST_PROFILE = "quiet"


def resolve_voice(voice: str | None, config: dict[str, Any]) -> str:
    """voice 请求值 → config 实际值 → 默认音色。生成与查重共用,口径一致。"""
    return voice or config.get("voice") or "Serena"

# Max podcasts synthesizing at once. 1 = strict serialization: jobs run one at a
# time, FIFO by submission order. This keeps the single shared inference worker
# from being kept continuously busy by interleaved jobs — which is what defeats
# the per-job inter-chunk pauses (quiet profile) and pins the GPU/fan. Tunable.
MAX_CONCURRENT_PODCASTS = 1


# 缓存 pmset 结果几秒：该函数被 podcast 管理循环（每 2s）与 snapshot（多次）频繁调用，
# 否则每次都 spawn 一个 pmset 子进程。电源状态变化慢，5s 缓存足够。
_BATTERY_CACHE: dict[str, Any] = {"value": False, "ts": 0.0}
_BATTERY_TTL = 5.0


def is_on_battery_power() -> bool:
    now = time.time()
    if now - _BATTERY_CACHE["ts"] < _BATTERY_TTL:
        return _BATTERY_CACHE["value"]
    try:
        result = subprocess.run(
            ["pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        value = "Battery Power" in result.stdout
    except Exception:
        value = False
    _BATTERY_CACHE["value"] = value
    _BATTERY_CACHE["ts"] = now
    return value


def prepare_podcast_config(
    config: dict[str, Any],
    text: str,
    force_small_model: bool = False,
) -> dict[str, Any]:
    podcast_config = config.copy()
    # 播客域档位单一真相:podcast_performance_profile(非法/缺失 → quiet)。
    # 有意覆盖上游 config/请求里的 performance_profile——两个 worker 都先过
    # 这里再切块/推理,所以此处一改,整条播客链(smart_split + engine sleep)生效。
    profile_name = podcast_config.get("podcast_performance_profile")
    if profile_name not in PERFORMANCE_PROFILES:
        profile_name = DEFAULT_PODCAST_PROFILE
    podcast_config["performance_profile"] = profile_name
    if podcast_config.get("force_battery_quiet"):
        podcast_config["performance_profile"] = "quiet"
        podcast_config["model"] = DEFAULT_TTS_MODEL
    profile = get_performance_profile(podcast_config["performance_profile"])
    if force_small_model or estimate_reading_minutes(text) >= 20.0:
        podcast_config["model"] = profile.get("model") or DEFAULT_TTS_MODEL
    return podcast_config


def wait_for_podcast_slot(pause_event, shutdown_event, poll_sec: float) -> None:
    while pause_event.is_set():
        if shutdown_event.wait(poll_sec):
            raise RuntimeError("podcast generation canceled")


def _submit_chunk_and_wait(
    podcast_q,
    job_id: str,
    idx: int,
    chunk_file: str,
    text: str,
    config: dict[str, Any],
    shutdown_event,
    poll_sec: float,
) -> None:
    """Submit one chunk to the shared engine's podcast lane and block until the
    engine writes chunk_file (success) or chunk_file.err (failure). File-based
    signaling means concurrent jobs never steal each other's completions."""
    err_file = chunk_file + ".err"
    if os.path.exists(err_file):
        try:
            os.remove(err_file)
        except OSError:
            pass
    podcast_q.put(
        {
            "job_id": job_id,
            "chunk_index": idx,
            "chunk_file": chunk_file,
            "text": text,
            "config": config,
        }
    )
    poll = max(0.1, poll_sec)
    while True:
        if shutdown_event.is_set():
            raise RuntimeError("podcast generation canceled")
        if os.path.exists(chunk_file):
            return
        if os.path.exists(err_file):
            msg = ""
            try:
                with open(err_file, encoding="utf-8") as fh:
                    msg = fh.read()
            except Exception:
                pass
            raise RuntimeError(f"engine failed podcast chunk {idx}: {msg}")
        shutdown_event.wait(poll)


def generate_podcast_chunks(
    podcast_q: Any,
    job_id: str,
    text: str,
    config: dict[str, Any],
    chunk_dir: str,
    pause_event,
    shutdown_event,
) -> tuple[list[str], list[Any]]:
    profile = get_performance_profile(config.get("performance_profile"))
    os.makedirs(chunk_dir, exist_ok=True)
    chunks = TextProcessor().parse_dialogue_or_text(
        text,
        performance_profile=config.get("performance_profile", "quiet"),
    )
    chunk_files: list[str] = []
    speakers: list[Any] = []
    progress_path = os.path.join(chunk_dir, "progress.json")

    for idx, chunk in enumerate(chunks):
        if shutdown_event.is_set():
            raise RuntimeError("podcast generation canceled")
        chunk_file = os.path.join(chunk_dir, f"chunk_{idx:05d}.npy")
        chunk_files.append(chunk_file)
        # Track the speaker per chunk (aligned with chunk_files, incl. resumed
        # ones) so write_podcast_wav_from_chunks can size inter-chunk pauses by
        # speaker change.
        speakers.append(chunk.get("config", {}).get("voice") if isinstance(chunk, dict) else None)
        if os.path.exists(chunk_file):
            continue

        wait_for_podcast_slot(
            pause_event,
            shutdown_event,
            profile["podcast_pause_poll_sec"],
        )
        if isinstance(chunk, dict):
            chunk_config = config.copy()
            chunk_config.update(chunk.get("config", {}))
            actual_text = chunk["text"]
        else:
            chunk_config = config
            actual_text = chunk

        # Synthesize via the single shared engine (one model, GPU serialized,
        # reads preempt at chunk boundaries). The engine writes chunk_file.
        _submit_chunk_and_wait(
            podcast_q,
            job_id,
            idx,
            chunk_file,
            actual_text,
            chunk_config,
            shutdown_event,
            profile["podcast_pause_poll_sec"],
        )

        if os.path.exists(chunk_file):
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"completed_chunks": idx + 1, "total_chunks": len(chunks)},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        if shutdown_event.wait(profile["sentence_sleep"]):
            raise RuntimeError("podcast generation canceled")

    return chunk_files, speakers


def assemble_podcast_audio(
    parts: list[Any],
    speakers: list[Any] | None = None,
    sr: int = 24000,
    same_gap_ms: int = 120,
    switch_gap_ms: int = 350,
) -> Any:
    """Trim each chunk's head/tail silence, then join with a *fixed* inter-chunk
    pause: ``same_gap_ms`` within one speaker, ``switch_gap_ms`` when the speaker
    changes. Replaces the variable model-generated gaps that made podcasts sound
    choppy (Bug 1). Returns None if nothing survives trimming."""
    trimmed = []
    for i, p in enumerate(parts):
        t = trim_silence(p, sr)
        if len(t) == 0:
            continue
        spk = speakers[i] if speakers and i < len(speakers) else None
        trimmed.append((t, spk))
    if not trimmed:
        return None

    out = [trimmed[0][0]]
    prev_spk = trimmed[0][1]
    for audio, spk in trimmed[1:]:
        gap_ms = switch_gap_ms if spk != prev_spk else same_gap_ms
        gap = int(sr * gap_ms / 1000)
        if gap > 0:
            if audio.ndim == 2:
                out.append(np.zeros((gap, audio.shape[1]), dtype=audio.dtype))
            else:
                out.append(np.zeros(gap, dtype=audio.dtype))
        out.append(audio)
        prev_spk = spk
    return np.concatenate(out)


def write_podcast_wav_from_chunks(
    chunk_files: list[str], output_path: str, speakers: list[Any] | None = None
) -> bool:
    # Keep chunk_files and speakers aligned while dropping any chunk whose file
    # is missing (partial/failed synthesis), so speaker-aware gaps stay correct.
    existing = [
        (path, (speakers[i] if speakers and i < len(speakers) else None))
        for i, path in enumerate(chunk_files)
        if os.path.exists(path)
    ]
    if not existing:
        return False
    parts = [np.load(path) for path, _ in existing]
    spk = [s for _, s in existing]
    full_wav = assemble_podcast_audio(parts, spk)
    if full_wav is None or len(full_wav) == 0:
        return False
    wav_data = (np.clip(full_wav, -1.0, 1.0) * 32767).astype(np.int16)
    scipy.io.wavfile.write(output_path, 24000, wav_data)
    return True


def _configure_low_priority_process() -> None:
    try:
        os.nice(19)
        print("[PodcastProcess] Nice level set to 19 (lowest priority)")
    except Exception as e:
        print(f"[PodcastProcess] Failed to set nice level: {e}")

    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"


def run_single_podcast_generation_thread(
    text: str,
    config: dict[str, Any],
    md5: str,
    source: str,
    pause_event,
    shutdown_event,
    podcast_q,
    podcasts_dir: str,
    podcast_chunk_dir: str,
    jobs_file: str,
    job_id: str,
    event_log_path: str | None,
    title: str | None = None,
) -> None:
    _configure_low_priority_process()
    job_store = PodcastJobStore(jobs_file)
    event_log = RuntimeEventLog(event_log_path) if event_log_path else None
    job_store.update(job_id, status="running", pid=os.getpid())
    if event_log:
        event_log.record("podcast_job_running", job_id=job_id, md5=md5, pid=os.getpid())

    # 复核 P2-8:文件名文法真正走命名拥有者(此前 worker 仍自带一份硬编码)
    safe_title = pn.safe_title_for_output(title, text)

    # M1(计划 #13):不再写磁盘 pending 哨兵——「生成中」的真相是 job store 的
    # 活任务状态(上面已 update status="running"),崩溃由启动对账收尸。
    os.makedirs(podcasts_dir, exist_ok=True)
    try:
        # Synthesis is delegated to the single shared engine process via
        # podcast_q; this subprocess only orchestrates (no model load, no
        # gpu_lock — the engine serializes the GPU for read + podcast).
        config = prepare_podcast_config(config, text)
        chunk_dir = os.path.join(podcast_chunk_dir, pn.chunk_dir_name("single", md5))
        chunk_files, speakers = generate_podcast_chunks(
            podcast_q,
            job_id,
            text,
            config,
            chunk_dir,
            pause_event,
            shutdown_event,
        )
        out_name = pn.single_output_name(source, safe_title, md5, int(time.time()))
        output_path = os.path.join(podcasts_dir, out_name)
        if not write_podcast_wav_from_chunks(chunk_files, output_path, speakers):
            raise RuntimeError("no generated podcast chunks")
        # 同名 .txt 文稿 sidecar，供内容中心双击查看播客脚本
        try:
            with open(output_path[:-4] + ".txt", "w", encoding="utf-8") as tf:
                tf.write(text)
        except Exception:
            pass
        job_store.update(job_id, status="done", output_path=output_path, error=None)
        if event_log:
            event_log.record("podcast_job_done", job_id=job_id, md5=md5, output_path=output_path)
        # 成品(.wav)+ 文稿已存盘,中间碎片 chunk_*.npy 不再需要 → 立即清该集碎片目录
        # (方案 A:避免 PodcastChunks 堆积)。ignore_errors:清理失败不影响已成功的任务。
        shutil.rmtree(chunk_dir, ignore_errors=True)
    except Exception as e:
        job_store.update(job_id, status="failed", error=str(e))
        if event_log:
            event_log.record("podcast_job_failed", job_id=job_id, md5=md5, error=str(e))
        print(f"[PodcastProcess] Error: {e}")
        traceback.print_exc()


def run_podcast_generation_thread(
    filename: str,
    text: str,
    config: dict[str, Any],
    pause_event,
    shutdown_event,
    podcast_q,
    podcast_chunk_dir: str,
    jobs_file: str,
    job_id: str,
    event_log_path: str | None,
) -> None:
    _configure_low_priority_process()
    job_store = PodcastJobStore(jobs_file)
    event_log = RuntimeEventLog(event_log_path) if event_log_path else None
    job_store.update(job_id, status="running", pid=os.getpid())
    if event_log:
        event_log.record("podcast_job_running", job_id=job_id, pid=os.getpid())

    # M1(计划 #13):同单篇 worker,不再写 pending 哨兵,真相=job store。
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    try:
        # Synthesis delegated to the shared engine via podcast_q (no model load,
        # no gpu_lock — the engine serializes the GPU for read + podcast).
        config = prepare_podcast_config(config, text, force_small_model=True)
        batch_hash = text_md5(text)
        chunk_dir = os.path.join(podcast_chunk_dir, pn.chunk_dir_name("batch", batch_hash))
        chunk_files, speakers = generate_podcast_chunks(
            podcast_q,
            job_id,
            text,
            config,
            chunk_dir,
            pause_event,
            shutdown_event,
        )
        if not write_podcast_wav_from_chunks(chunk_files, filename, speakers):
            raise RuntimeError("no generated podcast chunks")
        # 同名 .txt 文稿 sidecar，供内容中心双击查看播客脚本
        try:
            with open(filename[:-4] + ".txt", "w", encoding="utf-8") as tf:
                tf.write(text)
        except Exception:
            pass
        job_store.update(job_id, status="done", output_path=filename, error=None)
        if event_log:
            event_log.record("podcast_job_done", job_id=job_id, output_path=filename)
        # 成品(.wav)+ 文稿已存盘,中间碎片不再需要 → 立即清该集碎片目录(方案 A)。
        shutil.rmtree(chunk_dir, ignore_errors=True)
    except Exception as e:
        job_store.update(job_id, status="failed", error=str(e))
        if event_log:
            event_log.record("podcast_job_failed", job_id=job_id, error=str(e))
        print(f"[PodcastProcess] Error: {e}")


class PodcastService:
    def __init__(
        self,
        *,
        podcasts_dir: str,
        podcast_chunk_dir: str,
        runtime_state: Any,
        active_url_tasks: dict[str, dict],
        jobs_file: str | None = None,
        event_log: RuntimeEventLog | None = None,
        is_frontend_active: Callable[[], bool] | None = None,
        is_device_switching: Callable[[], bool] | None = None,
        get_battery_policy: Callable[[], str] | None = None,
        podcast_q: Any | None = None,
        podcast_cancel_epoch: Any | None = None,
    ) -> None:
        self.podcasts_dir = podcasts_dir
        self.podcast_chunk_dir = podcast_chunk_dir
        self.runtime_state = runtime_state
        self.active_url_tasks = active_url_tasks
        self.is_frontend_active = is_frontend_active
        self.is_device_switching = is_device_switching
        self.get_battery_policy = get_battery_policy
        self.job_store = PodcastJobStore(
            jobs_file
            or os.path.join(os.path.dirname(self.podcast_chunk_dir), "podcast_jobs.json")
        )
        self.job_store.mark_unfinished_failed("backend restarted before job completed")
        self.event_log = event_log
        self.pause_event = mp.Event()
        self.worker_shutdown_event = mp.Event()
        # Synthesis goes through the shared engine's podcast lane; this service
        # no longer loads its own model or serializes the GPU with a lock.
        self.podcast_q = podcast_q
        # P0-1:取消代际(mp.Value,与引擎共享)。cancel_all bump 它 → 引擎中途掐断在做的段
        self.podcast_cancel_epoch = podcast_cancel_epoch
        self.active_procs: list[mp.Process] = []
        self.active_tasks: dict[str, mp.Process] = {}
        self.active_job_ids: dict[str, str] = {}
        # FIFO queue of jobs created but not yet spawned (oldest at index 0). The
        # dispatcher spawns the head only when live orchestrators <
        # MAX_CONCURRENT_PODCASTS. In-memory only: on restart, the persisted job
        # records (status "queued") are flipped to "failed" by mark_unfinished_failed.
        self._pending: list[dict[str, Any]] = []
        # Guards _pending + active_* mutations + the spawn decision. start_*
        # (API threads) and _manager_loop (manager thread) both touch this state.
        self._dispatch_lock = threading.RLock()
        self.last_pause_reason = "recent_activity"
        self.last_battery_policy = "pause"
        self._shutdown_event = threading.Event()
        self._manager_thread = threading.Thread(
            target=self._manager_loop,
            name="podcast-manager",
            daemon=True,
        )
        self._manager_thread.start()

    def _manager_loop(self) -> None:
        while not self._shutdown_event.is_set():
            should_pause, reason = self._pause_state()

            if should_pause:
                self.last_pause_reason = reason
                if not self.pause_event.is_set():
                    self.pause_event.set()
                    self._record_event("podcast_generation_paused", reason=reason)
            else:
                self.last_pause_reason = "none"
                if self.pause_event.is_set():
                    self.pause_event.clear()
                    self._record_event("podcast_generation_resumed")
            # Reap finished orchestrators and launch the next queued job, so a
            # freed slot pulls the next pending job within ~2s (a lone job does
            # NOT wait — start_* dispatches synchronously).
            if not self._shutdown_event.is_set():
                self.cleanup_finished()
                self._try_dispatch()
            self._shutdown_event.wait(2)

    def _frontend_active(self) -> bool:
        if self.is_frontend_active is not None:
            try:
                return bool(self.is_frontend_active())
            except Exception:
                return True
        # E4:main_is_playing 存储标志已删;未注入判定(仅测试场景)时视为
        # 前台不活跃——生产路径始终由 backend.py 注入 player 现算的 lambda。
        return False

    def _pause_state(self) -> tuple[bool, str]:
        if self.is_device_switching is not None:
            try:
                if self.is_device_switching():
                    return False, "device_switching"
            except Exception:
                pass

        frontend_active = self._frontend_active()
        url_active = len(self.active_url_tasks) > 0
        battery_policy = self._battery_policy()
        self.last_battery_policy = battery_policy
        self.runtime_state.update_activity_if_busy(frontend_active or url_active)
        runtime_snapshot = self.runtime_state.snapshot()

        if frontend_active:
            return True, "frontend_active"
        if url_active:
            return True, "url_active"
        if time.time() - runtime_snapshot["last_active_time"] < 120:
            return True, "recent_activity"
        if is_on_battery_power() and battery_policy == "pause":
            return True, "battery"
        return False, "none"

    def _battery_policy(self) -> str:
        if self.get_battery_policy is None:
            return "pause"
        try:
            policy = self.get_battery_policy()
        except Exception:
            return "pause"
        return policy if policy in BATTERY_PODCAST_POLICIES else "pause"

    def _apply_battery_policy_to_config(self, config: dict[str, Any]) -> dict[str, Any]:
        config = config.copy()
        policy = self._battery_policy()
        if is_on_battery_power() and policy == "quiet":
            config["performance_profile"] = "quiet"
            config["model"] = DEFAULT_TTS_MODEL
            config["force_battery_quiet"] = True
            self._record_event("battery_quiet_policy_applied")
        return config

    def cleanup_finished(self) -> None:
        # Locked: the manager thread (via _manager_loop) and API threads (via
        # start_*/_spawn) both mutate active_* — serialize them under one lock.
        # All calls here (is_alive/join(0)/store writes) are non-blocking.
        with self._dispatch_lock:
            for md5, proc in list(self.active_tasks.items()):
                if not proc.is_alive():
                    try:
                        proc.join(0)
                    except (AssertionError, OSError, ValueError):
                        pass
                    job_id = self.active_job_ids.pop(md5, None)
                    if proc.exitcode not in (0, None):
                        self.job_store.update(
                            job_id,
                            status="failed",
                            error=f"process exited with code {proc.exitcode}",
                        )
                        self._record_event(
                            "podcast_process_failed",
                            md5=md5,
                            job_id=job_id,
                            exitcode=proc.exitcode,
                        )
                    self.active_tasks.pop(md5, None)
            live_processes = []
            for proc in self.active_procs:
                if proc.is_alive():
                    live_processes.append(proc)
                else:
                    try:
                        proc.join(0)
                    except (AssertionError, OSError, ValueError):
                        pass
            self.active_procs = live_processes

    def _spawn(self, pending: dict[str, Any]) -> None:
        """Construct and start one orchestrator subprocess from a pending
        descriptor, then register it. Callers hold _dispatch_lock. The
        orchestrator flips the job queued→running itself, so we don't touch the
        store here."""
        target = (
            run_single_podcast_generation_thread
            if pending["tag"] == "single"
            else run_podcast_generation_thread
        )
        md5 = pending["md5"]
        job_id = pending["job_id"]
        p = mp.Process(target=target, args=pending["args"], daemon=True)
        p.start()
        self.active_procs.append(p)
        self.active_tasks[md5] = p
        self.active_job_ids[md5] = job_id
        self._record_event(
            "podcast_job_spawned", job_id=job_id, md5=md5, kind=pending["tag"]
        )

    def _try_dispatch(self) -> None:
        """Spawn pending jobs (FIFO) up to MAX_CONCURRENT_PODCASTS live
        orchestrators. The whole check-pop-spawn runs under one lock so two
        threads can't both observe a free slot and over-spawn. mp.Process.start()
        is held under the lock deliberately (it's bounded, not a blocking wait);
        the cap depends on it being atomic with the live-count check."""
        with self._dispatch_lock:
            while self._pending:
                live = sum(1 for p in self.active_procs if p.is_alive())
                if live >= MAX_CONCURRENT_PODCASTS:
                    return
                pending = self._pending.pop(0)  # FIFO: oldest first
                try:
                    self._spawn(pending)
                except Exception as e:
                    # Don't requeue (would loop forever); fail the job and move on.
                    self.job_store.update(
                        pending["job_id"],
                        status="failed",
                        error=f"failed to spawn process: {e}",
                    )
                    self._record_event(
                        "podcast_process_failed",
                        md5=pending["md5"],
                        job_id=pending["job_id"],
                        error=str(e),
                    )

    def is_generating(self, md5: str) -> bool:
        self.cleanup_finished()
        # A queued-but-not-yet-spawned job is persisted as status "queued", which
        # job_store.active_for_md5 treats as active → dedup holds without scanning
        # _pending.
        return md5 in self.active_tasks or self.job_store.active_for_md5(md5)

    def find_reusable_output(
        self,
        *,
        text: str,
        mode: str,
        voice: str | None,
        config: dict[str, Any],
    ) -> str | None:
        """#8 复用:同 内容+模式+音色 已有完成成品且 wav 仍在 → 返回其路径,否则 None。

        N1 改名兼容:mode 先归一;规范 key 未命中时,再用历史旧名
        (podcast-discuss/podcast-trans 时代)的 key 查一遍——已生成的成品不作废。"""
        resolved = resolve_voice(voice, config)
        mode = reader_bridge.normalize_mode(mode)
        for m in [mode] + reader_bridge.legacy_mode_equivalents(mode):
            job = self.job_store.newest_done_for_content_key(
                content_key(text, m, resolved)
            )
            if job:
                output_path = job.get("output_path")
                if output_path and os.path.exists(output_path):
                    return output_path
        return None

    def start_single(
        self,
        *,
        text: str,
        config: dict[str, Any],
        md5: str,
        source: str,
        title: str | None,
        mode: str = "original",
        voice: str | None = None,
        content_identity_text: str | None = None,
    ) -> None:
        if self._shutdown_event.is_set():
            raise RuntimeError("podcast service is shutting down")
        self.cleanup_finished()
        config = self._apply_battery_policy_to_config(config)
        mode = reader_bridge.normalize_mode(mode)  # N1:job 记录一律存规范名
        safe_title = title if title else (text[:20].replace("\n", " ") + "...")
        job_id = f"single_{md5[:12]}_{uuid.uuid4().hex[:8]}"
        resolved = resolve_voice(voice, config)
        # saved 路径:合成用 LLM 脚本(text),但身份用原文(identity)——exists 查重
        # 发生在 LLM 之前,必须与端点侧 data.text 同口径。
        identity = content_identity_text if content_identity_text is not None else text
        self.job_store.create(
            job_id=job_id,
            kind="single",
            md5=md5,
            title=safe_title,
            source=source,
            mode=mode,
            voice=resolved,
            content_key=content_key(identity, mode, resolved),
            chunk_dir=pn.chunk_dir_name("single", md5),
        )
        self._record_event(
            "podcast_job_queued",
            job_id=job_id,
            kind="single",
            md5=md5,
            title=safe_title,
            source=source,
        )
        pending = {
            "tag": "single",
            "md5": md5,
            "job_id": job_id,
            "args": (
                text,
                config,
                md5,
                source,
                self.pause_event,
                self.worker_shutdown_event,
                self.podcast_q,
                self.podcasts_dir,
                self.podcast_chunk_dir,
                self.job_store.path,
                job_id,
                self.event_log.path if self.event_log else None,
                title,
            ),
        }
        with self._dispatch_lock:
            self._pending.append(pending)
        self._try_dispatch()

    def start_batch(
        self,
        *,
        filename: str,
        text: str,
        config: dict[str, Any],
        md5: str,
    ) -> None:
        if self._shutdown_event.is_set():
            raise RuntimeError("podcast service is shutting down")
        self.cleanup_finished()
        config = self._apply_battery_policy_to_config(config)
        job_id = f"batch_{md5[:12]}_{uuid.uuid4().hex[:8]}"
        self.job_store.create(
            job_id=job_id,
            kind="batch",
            md5=md5,
            title="大合集播客",
            source="web",
            output_path=filename,
            # worker 用 md5(text) 重算 batch_hash;/generate_podcast 传入的 md5
            # 正是 md5(text)(backend.py:1150),两者同源 → 目录一致
            chunk_dir=pn.chunk_dir_name("batch", md5),
        )
        self._record_event(
            "podcast_job_queued",
            job_id=job_id,
            kind="batch",
            md5=md5,
            output_path=filename,
        )
        pending = {
            "tag": "batch",
            "md5": md5,
            "job_id": job_id,
            "args": (
                filename,
                text,
                config,
                self.pause_event,
                self.worker_shutdown_event,
                self.podcast_q,
                self.podcast_chunk_dir,
                self.job_store.path,
                job_id,
                self.event_log.path if self.event_log else None,
            ),
        }
        with self._dispatch_lock:
            self._pending.append(pending)
        self._try_dispatch()

    def cancel_all(
        self,
        *,
        graceful_timeout: float = 0.0,
        terminate_timeout: float = 2.0,
    ) -> None:
        # Snapshot + clear in-memory state under the lock (incl. _pending, so a
        # canceled-but-unspawned job can never be dispatched later), then stop
        # the procs OUTSIDE the lock — stop_process joins/terminates and can
        # block up to terminate_timeout, which must not stall API/manager threads.
        with self._dispatch_lock:
            procs = list(self.active_procs)
            self._pending.clear()
            self.active_procs.clear()
            self.active_tasks.clear()
            self.active_job_ids.clear()
        for proc in procs:
            stop_process(
                proc,
                graceful_timeout=graceful_timeout,
                terminate_timeout=terminate_timeout,
            )
        self.job_store.cancel_active()
        # P0-1:掐断推理侧残余——bump 代际(引擎逐帧对照,中途丢弃在做的段)
        # + 排空已排队的段(submit-and-wait 协议下最多 1 个,但排干净不留死角)。
        if self.podcast_cancel_epoch is not None:
            try:
                with self.podcast_cancel_epoch.get_lock():
                    self.podcast_cancel_epoch.value += 1
            except Exception:
                pass
        drained = 0
        if self.podcast_q is not None:
            try:
                while True:
                    self.podcast_q.get_nowait()
                    drained += 1
            except Exception:
                pass
        self._record_event("podcast_jobs_canceled", drained_queue_items=drained)

    def shutdown(
        self,
        *,
        graceful_timeout: float = 0.0,
        terminate_timeout: float = 2.0,
    ) -> None:
        self._shutdown_event.set()
        self.worker_shutdown_event.set()
        self.pause_event.clear()
        self.cancel_all(
            graceful_timeout=graceful_timeout,
            terminate_timeout=terminate_timeout,
        )
        if self._manager_thread is not threading.current_thread():
            self._manager_thread.join(terminate_timeout)

    def mark_orphans_failed(self, reason: str) -> None:
        """启动对账(M1):上次进程死亡时没跑完的任务一律落 failed——
        pending 伪行由 job store 推导,对账后幽灵「生成中」结构性消失。"""
        self.job_store.mark_unfinished_failed(reason)

    def cleanup_pending_files(self) -> None:
        """清 .pending_* 哨兵文件。M1 后不再产生新哨兵,此方法只负责
        清扫升级前遗留的历史文件(启动时与 cancel_all 各调一次)。"""
        if not os.path.exists(self.podcasts_dir):
            return
        for filename in os.listdir(self.podcasts_dir):
            if ".pending_" in filename:
                try:
                    os.remove(os.path.join(self.podcasts_dir, filename))
                    # P0-2 审计:任何成品目录的删除都留字据(2026-07-01 wav 消失悬案教训)
                    self._record_event("podcast_file_deleted", filename=filename, reason="pending_cleanup")
                except Exception:
                    pass

    def snapshot(self) -> dict[str, Any]:
        self.cleanup_finished()
        jobs = self.job_store.list()
        _active_statuses = {"running", "queued", "paused"}
        return {
            "podcast_generation_paused": self.pause_event.is_set(),
            "podcast_generation_pause_reason": self.last_pause_reason,
            "battery_podcast_policy": self.last_battery_policy,
            "on_battery_power": is_on_battery_power(),
            # 正在跑的 worker 进程数(串行执行,通常 0/1)——用于"是否有进程在烧"判断。
            "active_podcast_processes": sum(1 for p in self.active_procs if p.is_alive()),
            # 队列口径:进行中 + 排队中 + 暂停的任务总数。侧栏"生成中"指示用这个,
            # 才能反映"我提交了 3 个"(1 跑 + 2 排队),而非只显示 1 个活跃进程。
            "active_podcast_jobs": sum(1 for j in jobs if j.get("status") in _active_statuses),
            "podcast_jobs": jobs[:20],
        }

    def record_failed_single(
        self, *, md5: str, title: str | None, source: str, mode: str, error: str
    ) -> None:
        """落一条 failed 单篇 job(如 LLM 前处理失败),让内容中心可见原因。
        job_store 的写入归本服务拥有(#10 C1.4:此前路由直写 job_store)。"""
        job_id = f"single_{md5[:12]}_llmfail"
        self.job_store.create(
            job_id=job_id, kind="single", md5=md5,
            title=title, source=source, mode=mode,
        )
        self.job_store.update(job_id, status="failed", error=error)

    def generating_title(self) -> str:
        """当前正在生成的播客标题("" = 无)。/status 的 generating_title 唯一来源。

        M1(计划 #13):真相 = job store 的 running 任务(串行执行,取最旧的
        一条),不再扫磁盘哨兵文件——哨兵在崩溃后残留曾致幽灵「生成中」。"""
        for job in reversed(self.job_store.list()):  # list 为新→旧,反转取最旧
            if job.get("status") == "running":
                return job.get("title") or ""
        return ""

    def list_jobs(self) -> list[dict[str, Any]]:
        self.cleanup_finished()
        jobs = self.job_store.list()
        for job in jobs:
            if job.get("status") in {"running", "paused", "queued"}:
                # C2.2:worker 的目录名 = chunk_dir_name(kind, md5)(无 uuid 后缀),
                # 历史代码误用 job_id(带后缀)→ 进度从未读到。旧记录无字段时
                # 退回 job_id(保持旧行为:读不到、不崩)。
                dir_name = job.get("chunk_dir") or job.get("job_id")
                chunk_dir = os.path.join(self.podcast_chunk_dir, dir_name)
                progress_path = os.path.join(chunk_dir, "progress.json")
                if os.path.exists(progress_path):
                    try:
                        with open(progress_path, "r", encoding="utf-8") as f:
                            prog = json.load(f)
                            comp = prog.get("completed_chunks", 0)
                            tot = prog.get("total_chunks", 0)
                            if tot > 0:
                                job["completed_chunks"] = comp
                                job["total_chunks"] = tot
                                job["progress_percent"] = int((comp / tot) * 100)
                    except Exception:
                        pass
        # #11 N2:任务行同样带展示三件套(App 播客 tab 的"处理中"行用)。
        for job in jobs:
            job["display_title"] = labels.clean_display_title(job.get("title") or "")
            job["source_label"] = labels.source_label(job.get("source"))
            job["mode_label"] = labels.mode_label(job.get("mode"))
        return jobs

    def search_dirs(self) -> list[str]:
        # 仅 runtime_paths 解析的 podcasts 目录；此前还会从 self.podcasts_dir 上推目录
        # 拼出 legacy QwenTTS-App/data/*，违反分离约束与“不要按 repo 深度推路径”。
        # 旧数据由 paths.migrate_legacy_data 在启动时迁移到此目录。
        return [self.podcasts_dir]

    def find_file(self, filename: str) -> str | None:
        safe_filename = os.path.basename(filename)
        for directory in self.search_dirs():
            candidate = os.path.join(directory, safe_filename)
            if os.path.exists(candidate):
                return candidate
        return None

    def rename_file(self, filename: str, new_title: str) -> bool:
        """重命名已生成的播客文件（包括 .wav 与关联的 .txt 文稿）。"""
        filepath = self.find_file(filename)
        if not filepath or not os.path.exists(filepath):
            return False
            
        directory = os.path.dirname(filepath)
        # C2.4:标题替换的文件名文法归命名拥有者
        new_filename = pn.renamed_filename(filename, new_title)
        new_filepath = os.path.join(directory, new_filename)
        
        if new_filepath == filepath:
            return True
            
        try:
            os.rename(filepath, new_filepath)
            old_txt = os.path.splitext(filepath)[0] + ".txt"
            new_txt = os.path.splitext(new_filepath)[0] + ".txt"
            if os.path.exists(old_txt):
                os.rename(old_txt, new_txt)
            return True
        except Exception:
            return False

    def _mode_by_output_filename(self) -> dict[str, str]:
        """成品文件名 → 生成时 mode(#11 N2:job 记录反查,老文件查不到不猜)。"""
        out: dict[str, str] = {}
        for job in self.job_store.list():
            op = job.get("output_path")
            mode = job.get("mode")
            if op and mode:
                out[os.path.basename(op)] = mode
        return out

    def list_files(self) -> list[dict[str, Any]]:
        mode_map = self._mode_by_output_filename()
        files: list[dict[str, Any]] = []
        if os.path.exists(self.podcasts_dir):
            for filename in os.listdir(self.podcasts_dir):
                if not filename.endswith(".wav"):
                    # M1:非 wav(含历史遗留的 .pending_* 哨兵)一律不渲染;
                    # 「生成中」伪行由下方 job store 推导。
                    continue
                path = os.path.join(self.podcasts_dir, filename)
                # C2.4:文件名 → 展示信息的解析归命名拥有者
                parsed = pn.parse_output_filename(filename)
                title = parsed["title"]
                source = parsed["source"]
                try:
                    size_mb = os.path.getsize(path) / (1024 * 1024)
                except Exception:
                    size_mb = 0
                # #11 N2:mode 从 job 记录反查(置顶改名只加 pinned_ 前缀,
                # 用 clean_filename 匹配);老文件查不到 → 不给 mode_label,不猜。
                mode = mode_map.get(parsed["clean_filename"]) or mode_map.get(filename)
                entry = {
                    "title": title,
                    "filename": filename,
                    "timestamp": os.path.getmtime(path),
                    "is_pending": False,
                    "source": source,
                    "is_pinned": parsed["is_pinned"],
                    "size_mb": size_mb,
                    "display_title": labels.clean_display_title(title),
                    "source_label": labels.source_label(source),
                }
                if mode:
                    entry["mode_label"] = labels.mode_label(mode)
                files.append(entry)

        # M1(计划 #13):pending 伪行的唯一来源 = job store 活任务。
        # 崩溃残留由启动对账 mark_orphans_failed 收尸,幽灵「生成中」不再可能。
        for job in self.job_store.list():
            if job.get("status") in {"running", "queued", "paused"}:
                title = job.get("title") or ""
                files.append(
                    {
                        "title": title + " (正在生成中...)",
                        "filename": job.get("job_id"),
                        "timestamp": job.get("created_at") or time.time(),
                        "is_pending": True,
                        "source": job.get("source"),
                        "is_pinned": False,
                        "display_title": labels.clean_display_title(title),
                        "source_label": labels.source_label(job.get("source")),
                    }
                )

        # M4-③:伪行过滤规则收在 labels,与 /saved_items 共用
        for url, info in labels.pending_url_tasks(self.active_url_tasks, podcast=True):
            files.insert(
                0,
                {
                    "title": labels.PENDING_FETCH_TITLE,
                    "filename": url,
                    "timestamp": info["timestamp"],
                    "is_pending": True,
                    "source": "web",
                    "is_pinned": False,
                    "size_mb": 0,
                    "display_title": labels.PENDING_FETCH_TITLE,
                    "source_label": labels.source_label("web"),
                },
            )

        files.sort(key=lambda x: (not x["is_pinned"], -x["timestamp"]))
        return files

    def toggle_pin(self, filename: str) -> dict[str, Any]:
        filepath = self.find_file(filename)
        if not filepath:
            return {"error": "File not found"}

        dir_name = os.path.dirname(filepath)
        safe_filename = os.path.basename(filename)
        if "pinned_" in safe_filename:
            new_name = safe_filename.replace("pinned_", "")
        else:
            new_name = "pinned_" + safe_filename

        try:
            os.rename(filepath, os.path.join(dir_name, new_name))
            return {"status": "ok", "new_name": new_name}
        except Exception as e:
            return {"error": str(e)}

    def clear_unpinned(self) -> int:
        deleted_count = 0
        for directory in [self.podcasts_dir]:
            if os.path.exists(directory):
                for filename in os.listdir(directory):
                    if filename.endswith(".wav") and "pinned_" not in filename:
                        try:
                            os.remove(os.path.join(directory, filename))
                            deleted_count += 1
                            # P0-2 审计:2026-07-01 三个成品 wav "神秘消失",查了一小时
                            # 才定位到扩展的清空按钮调 /podcasts/clear。留字据,秒破案。
                            self._record_event("podcast_file_deleted", filename=filename, reason="clear_unpinned")
                        except Exception:
                            pass
        return deleted_count

    def delete(self, filename: str) -> dict[str, Any]:
        if not filename:
            return {"error": "Empty filename"}
        filepath = self.find_file(filename)
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                self._record_event("podcast_file_deleted", filename=filename, reason="user_delete")
                return {"status": "ok"}
            except Exception as e:
                return {"error": f"Failed to delete file: {e}"}
        return {"error": "File not found"}

    def _record_event(self, event: str, **fields: Any) -> None:
        if self.event_log:
            self.event_log.record(event, **fields)
