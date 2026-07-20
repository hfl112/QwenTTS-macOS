import os
import sys
import tempfile
import multiprocessing as mp
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
URL_READER_ROOT = os.path.abspath(os.path.join(ROOT, "URL-Reader"))
if URL_READER_ROOT not in sys.path:
    sys.path.insert(0, URL_READER_ROOT)

from core.constants import DEFAULT_TTS_MODEL
from core.api_models import (
    GenerateSinglePodcastRequest,
    PlaySavedRequest,
    ReadRequest,
    ReadUrlRequest,
)
from core.services.performance import get_performance_profile
from core.services import podcast_service as podcast_service_module
from core.services.saved_items_service import SavedItemsService
from core.services.podcast_service import PodcastService
from core.services.podcast_jobs import PodcastJobStore
from core.services.runtime_log import RuntimeEventLog
from core.services.playback_service import PlaybackController
from core.services.url_jobs import UrlJobStore
from core.state.runtime_state import RuntimeState
from reader_service import cache_key, clean_markdown_content, extract_title


def test_performance_profile_defaults_to_balanced():
    assert get_performance_profile("quiet")["name"] == "quiet"
    assert get_performance_profile("missing")["name"] == "balanced"


def test_runtime_state_snapshot():
    # E4:main_is_playing 存储标志已物理删除——播放真相由 playback_status() 现算,
    # RuntimeState 只承载展示性元数据(标题/进度/媒体指针)。
    state = RuntimeState()
    state.set_main(title="Title", progress="1/2")
    state.set_current_media(podcast="a.wav", md5="abc")

    snapshot = state.snapshot()
    assert "main_is_playing" not in snapshot  # wire 值由端点从 playback_status 派生
    assert snapshot["main_title"] == "Title"
    assert snapshot["current_podcast_file"] == "a.wav"
    assert snapshot["current_playing_md5"] == "abc"


def test_saved_items_pin_toggle_keeps_order():
    """ADR-003 F4: toggle_pin flips a persisted `pinned` flag by md5 WITHOUT
    reordering storage (frontend sorts for display; /play_saved is index-based,
    so reordering here would play the wrong item). Old items lacking the field
    default to unpinned."""
    with tempfile.TemporaryDirectory() as tmp:
        service = SavedItemsService(tmp)
        service.save("alpha", source="web", title="A")
        service.save("beta", source="web", title="B")
        service.save("gamma", source="web", title="C")
        before = [i["md5"] for i in service.load()]
        assert all(not i.get("pinned", False) for i in service.load())  # default unpinned

        beta_md5 = service.load()[1]["md5"]
        assert service.toggle_pin(beta_md5) is True

        items = service.load()
        assert [i["md5"] for i in items] == before, "toggle_pin must NOT reorder storage"
        assert items[1].get("pinned") is True  # beta pinned, still at index 1
        assert items[0].get("pinned", False) is False

        # idempotent toggle back
        assert service.toggle_pin(beta_md5) is True
        assert service.load()[1].get("pinned") is False
        # unknown md5
        assert service.toggle_pin("nope") is False


def test_saved_items_service_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        service = SavedItemsService(tmp)
        count = service.save("hello world", source="test", voice="Serena", title="Hello")
        assert count == 1
        items = service.load()
        assert items[0]["title"] == "Hello"

        text, voice, md5 = service.selected_text([0])
        assert text == "hello world"
        assert voice == "Serena"
        assert md5 == items[0]["md5"]

        assert service.delete(md5=md5)
        assert service.load() == []


def test_saved_items_no_truncation_beyond_five():
    """回归:save() 不得截断列表。早期实现只留最近 5 条(items[-5:]),
    第 6 篇进来会静默永久删除最老一篇(置顶也不能幸免)。存储层全量保留,
    展示条数限制(如扩展弹窗只显示 5 条)是各前端 UI 自己的事。"""
    with tempfile.TemporaryDirectory() as tmp:
        service = SavedItemsService(tmp)
        for i in range(8):
            service.save(f"article body {i}", source="test", title=f"T{i}")
        items = service.load()
        assert len(items) == 8
        assert [i["title"] for i in items] == [f"T{i}" for i in range(8)]


def test_podcast_service_file_ops():
    with tempfile.TemporaryDirectory() as tmp:
        podcasts_dir = os.path.join(tmp, "podcasts")
        os.makedirs(podcasts_dir)
        path = os.path.join(podcasts_dir, "podcast_单篇_web_Title_abcd1234_1.wav")
        with open(path, "wb") as f:
            f.write(b"RIFF")

        service = PodcastService(
            podcasts_dir=podcasts_dir,
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=RuntimeState(),
            active_url_tasks={},
        )

        listed = service.list_files()
        assert listed[0]["filename"] == os.path.basename(path)
        assert service.find_file(os.path.basename(path)) == path
        assert service.toggle_pin(os.path.basename(path))["status"] == "ok"
        assert service.delete("pinned_" + os.path.basename(path))["status"] == "ok"


def test_podcast_pause_state_allows_long_paused_frontend():
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            is_frontend_active=lambda: False,
            # 电源策略设为 allow，使本用例与运行机器是否插电解耦（此前在电池供电
            # 的机器上会走 battery 分支返回 (True,"battery") 而误失败）。
            get_battery_policy=lambda: "allow",
        )

        should_pause, reason = service._pause_state()
        assert should_pause is False
        assert reason == "none"


def test_podcast_pause_state_blocks_active_frontend():
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            is_frontend_active=lambda: True,
        )

        should_pause, reason = service._pause_state()
        assert should_pause is True
        assert reason == "frontend_active"


def test_podcast_pause_state_ignores_device_switching():
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 10
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            is_frontend_active=lambda: True,
            is_device_switching=lambda: True,
        )

        should_pause, reason = service._pause_state()
        assert should_pause is False
        assert reason == "device_switching"


def test_podcast_battery_policy_pause_blocks_on_battery(monkeypatch):
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: True)
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            get_battery_policy=lambda: "pause",
        )

        should_pause, reason = service._pause_state()
        assert should_pause is True
        assert reason == "battery"


def test_podcast_battery_policy_quiet_allows_and_forces_quiet(monkeypatch):
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: True)
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            get_battery_policy=lambda: "quiet",
        )

        should_pause, reason = service._pause_state()
        assert should_pause is False
        assert reason == "none"

        config = service._apply_battery_policy_to_config(
            {"performance_profile": "fast", "model": "Qwen3-TTS-1.7B-8bit"}
        )
        assert config["performance_profile"] == "quiet"
        assert config["model"] == DEFAULT_TTS_MODEL  # M3:电池 quiet 强制的是现役 4bit,不再钉 bf16 慢模型


def test_podcast_battery_policy_allow_does_not_pause(monkeypatch):
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: True)
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            get_battery_policy=lambda: "allow",
        )

        should_pause, reason = service._pause_state()
        assert should_pause is False
        assert reason == "none"


class _FakeProc:
    """Stand-in for an orchestrator mp.Process: alive until killed, satisfies the
    is_alive/join/exitcode/terminate/kill surface cleanup_finished + cancel_all use."""

    def __init__(self):
        self._alive = True
        self.exitcode = 0

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self._alive = False

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False


def test_podcast_jobs_serialize_fifo(monkeypatch):
    """Only ONE podcast spawns at a time; the rest wait FIFO by submission order,
    and the next launches when the running one finishes."""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=RuntimeState(),
            active_url_tasks={},
            jobs_file=os.path.join(tmp, "podcast_jobs.json"),
            get_battery_policy=lambda: "allow",
        )

        spawned: list[str] = []

        def fake_spawn(pending):
            spawned.append(pending["job_id"])
            proc = _FakeProc()
            service.active_procs.append(proc)
            service.active_tasks[pending["md5"]] = proc
            service.active_job_ids[pending["md5"]] = pending["job_id"]

        monkeypatch.setattr(service, "_spawn", fake_spawn)

        service.start_single(text="A", config={}, md5="aaa", source="web", title="A")
        service.start_single(text="B", config={}, md5="bbb", source="web", title="B")
        service.start_single(text="C", config={}, md5="ccc", source="web", title="C")

        # Cap=1: only the earliest job ran; the other two wait in FIFO order.
        assert len(spawned) == 1
        assert sum(1 for p in service.active_procs if p.is_alive()) == 1
        assert len(service._pending) == 2
        first_job = spawned[0]
        assert service.active_job_ids["aaa"] == first_job
        assert [p["md5"] for p in service._pending] == ["bbb", "ccc"]

        # Job A finishes → reap + dispatch launches the next in line (B), not C.
        service.active_procs[0]._alive = False
        service.cleanup_finished()
        service._try_dispatch()
        # B (next in line) ran; C still waits.
        assert len(spawned) == 2
        assert spawned[1] == service.active_job_ids["bbb"]
        assert [p["md5"] for p in service._pending] == ["ccc"]

        # cancel_all drops the pending tail so a canceled job is never dispatched.
        service.cancel_all(terminate_timeout=0.1)
        assert service._pending == []
        service._try_dispatch()
        assert len(spawned) == 2  # C was canceled, never spawned

        service.shutdown(terminate_timeout=0.1)


def test_auto_mode_resolves_by_target_lang(monkeypatch):
    """'auto' translates only when content language differs from target_lang."""
    import reader_service

    en_text = "This is an English article about clinical medicine and patient care."
    zh_text = "这是一篇关于临床医学与患者护理的中文文章，内容相当详尽。"

    assert reader_service._detect_lang(en_text) == "en"
    assert reader_service._detect_lang(zh_text) == "zh"

    # target = zh: English content → translate, Chinese content → original
    monkeypatch.setattr(
        "engine_config.load_engines",
        lambda: {"translate": {"target_lang": "zh"}},
    )
    assert reader_service.resolve_auto_mode(en_text) == "translate"
    assert reader_service.resolve_auto_mode(zh_text) == "original"

    # target = en: English content → original, Chinese content → translate (to en)
    monkeypatch.setattr(
        "engine_config.load_engines",
        lambda: {"translate": {"target_lang": "en"}},
    )
    assert reader_service.resolve_auto_mode(en_text) == "original"
    assert reader_service.resolve_auto_mode(zh_text) == "translate"


def test_runtime_event_log_recent_events():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "runtime_events.jsonl")
        log = RuntimeEventLog(path, max_events=2)

        log.record("first", value=1)
        log.record("second", value=2)
        log.record("third", value=3)

        events = log.recent(limit=10)
        assert [event["event"] for event in events] == ["second", "third"]
        assert events[-1]["value"] == 3


def test_podcast_job_store_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        store = PodcastJobStore(os.path.join(tmp, "podcast_jobs.json"))

        store.create(
            job_id="job-1",
            kind="single",
            md5="abc",
            title="Title",
            source="web",
        )
        assert store.active_for_md5("abc")

        store.update("job-1", status="done", output_path="/tmp/out.wav")
        jobs = store.list()
        assert jobs[0]["status"] == "done"
        assert jobs[0]["output_path"] == "/tmp/out.wav"
        assert not store.active_for_md5("abc")

        store.create(
            job_id="job-2",
            kind="batch",
            md5="def",
            title="Batch",
            source="web",
        )
        store.mark_unfinished_failed("restart")
        assert store.list()[0]["status"] == "failed"
        assert store.list()[0]["error"] == "restart"


def test_api_models_keep_backward_compatible_defaults():
    read = ReadRequest(text="hello")
    assert read.voice is None
    assert read.from_saved is False
    assert read.performance_profile is None

    read_url = ReadUrlRequest(url="https://example.com", translate=True)
    assert read_url.effective_mode() == "translate"
    assert read_url.action() == "read"
    assert ReadUrlRequest(url="x", save=True).action() == "save"
    assert ReadUrlRequest(url="x", save=True, podcast=True).action() == "podcast"

    podcast = GenerateSinglePodcastRequest(text="hello")
    assert podcast.source == "web"
    assert podcast.performance_profile == "quiet"

    first = PlaySavedRequest()
    second = PlaySavedRequest()
    first.indices.append(1)
    assert second.indices == []


class DummySharedState:
    def __init__(self):
        self.audio_q = mp.Queue()
        self.stop_event = mp.Event()
        self.current_task_id = mp.Value("i", 0)


class DummyPlayer:
    def __init__(self):
        self.audio_queue = mp.Queue()
        self.stop_count = 0

    def stop(self):
        self.stop_count += 1


def test_playback_status_predicate():
    """ADR-003 A1: playback_status() is computed on-read from the player (no
    stored main_is_playing flag). Priority: not running→idle; paused→paused;
    prebuffering→generating; else→playing."""
    from core.services.playback_service import PlaybackService

    class FakePlayer:
        def __init__(self):
            self.running = False
            self.is_paused = False
            self.is_prebuffering = False

        def is_running(self):
            return self.running

    player = FakePlayer()
    svc = PlaybackService(
        shared_state=DummySharedState(),
        player=player,
        storage=None,
        runtime_state=RuntimeState(),
        sentinel="X",
        get_text_hash=lambda t: t,
        get_performance_profile=get_performance_profile,
        event_log=None,
    )

    assert svc.playback_status() == "idle"        # not running
    player.running = True
    assert svc.playback_status() == "playing"      # running, not paused/prebuffering
    player.is_prebuffering = True
    assert svc.playback_status() == "generating"   # running + prebuffering
    player.is_paused = True
    assert svc.playback_status() == "paused"        # paused beats prebuffering
    player.running = False
    assert svc.playback_status() == "idle"          # not running beats everything


def test_playback_controller_invalidates_old_sessions():
    shared_state = DummySharedState()
    player = DummyPlayer()
    controller = PlaybackController(shared_state, player)

    first_session = controller.start_new_session()
    assert controller.can_feed_audio(first_session)

    second_session = controller.start_new_session()
    assert not controller.can_feed_audio(first_session)
    assert controller.can_feed_audio(second_session)
    assert player.stop_count == 2

    controller.stop_current_session()
    assert not controller.can_feed_audio(second_session)
    assert shared_state.stop_event.is_set()


def test_playback_session_tracks_single_identity():
    # ADR-002: PlaybackSession.id == current_task_id; is_current() flips as new
    # sessions are started (no separate _session_id shadow).
    from core.services.playback_service import PlaybackController, PlaybackSession

    shared_state = DummySharedState()
    controller = PlaybackController(shared_state, DummyPlayer())

    s1 = PlaybackSession(id=controller.start_new_session(), chunks=["a"], config={})
    assert controller.is_current(s1.id)
    assert s1.start_idx == 0 and s1.title is None

    s2 = PlaybackSession(id=controller.start_new_session(), chunks=["b"], config={})
    assert controller.is_current(s2.id)
    assert not controller.is_current(s1.id)


def _silence_gap_runs(mono, sr, rel=0.02):
    """Return inner silence-gap lengths (seconds) in a mono signal, excluding
    leading/trailing silence. Mirrors the podcast WAV analyzer used to diagnose
    Bug 1."""
    import numpy as np

    env = np.abs(mono)
    peak = float(env.max()) or 1.0
    quiet = env < (rel * peak)
    runs = []
    i = 0
    n = len(quiet)
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    inner = [(a, b) for a, b in runs if a > 0 and b < n]
    return [(b - a) / sr for a, b in inner]


def test_podcast_assembly_trims_silence_and_inserts_speaker_aware_gaps():
    """Bug 1 repro+fix at the real seam: each synthesized chunk carries model
    head/tail silence; raw concatenation yields a ~700ms gap at every sentence
    boundary (15% of the podcast was silence → choppy). assemble_podcast_audio
    must trim each chunk and join with a fixed pause: ~120ms within a speaker,
    ~350ms across speakers."""
    import numpy as np

    from core.services.podcast_service import assemble_podcast_audio

    sr = 24000

    def chunk(lead_ms, tone_ms, tail_ms):
        lead = np.zeros((int(sr * lead_ms / 1000), 2), dtype=np.float32)
        t = np.arange(int(sr * tone_ms / 1000)) / sr
        tone = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        tone = np.stack([tone, tone], axis=1)
        tail = np.zeros((int(sr * tail_ms / 1000), 2), dtype=np.float32)
        return np.concatenate([lead, tone, tail])

    parts = [chunk(300, 500, 400) for _ in range(4)]
    speakers = ["Serena", "Serena", "Ryan", "Ryan"]

    out = assemble_podcast_audio(parts, speakers, sr=sr, same_gap_ms=120, switch_gap_ms=350)
    assert out is not None and out.ndim == 2

    dur = len(out) / sr
    # Raw concat would be 4*1.2s = 4.8s. Trimmed: 4*0.5s tone + 0.12+0.35+0.12
    # gaps + small pads ≈ 2.6-3.0s. Proves trimming happened.
    assert 2.3 < dur < 3.2, f"unexpected assembled duration {dur:.2f}s (trim failed?)"

    gaps = sorted(_silence_gap_runs(np.abs(out).max(axis=1), sr))
    # No raw 700ms boundary gaps survive.
    assert max(gaps) < 0.45, f"a silence gap of {max(gaps):.2f}s survived trimming"
    # Exactly one cross-speaker gap (~350ms) and it is the largest.
    assert max(gaps) > 0.28, f"speaker-switch gap missing/too small: {max(gaps):.2f}s"
    # Same-speaker gaps are clearly shorter than the switch gap.
    same = [g for g in gaps if g < 0.28]
    assert same and all(g < 0.22 for g in same), f"same-speaker gaps too long: {same}"


def test_play_wav_after_read_tail_keeps_is_playing_true():
    """Bug 2 家族回归(E4 改写):原版守"陈旧 read 线程不得把存储标志
    main_is_playing 打回 False"。E4 物理删除了该标志——clobber 目标结构性消失;
    仍需守住 finally 里剩下的 is_current 门:陈旧线程不得把 status_code 打回
    IDLE,污染新会话的诊断状态。"""
    import queue as _queue
    import threading

    from core.services.playback_service import PlaybackService, PlaybackSession

    class TailSharedState:
        def __init__(self):
            self.text_q = _queue.Queue()
            self.audio_q = _queue.Queue()
            self.stop_event = mp.Event()
            self.current_task_id = mp.Value("i", 0)
            self.status_calls = []

        def set_status(self, code):
            self.status_calls.append(code)

    class BlockingPlayer:
        """Models the real player: stop() fires playback_finished_event, which
        is what wait_until_finished() blocks on."""

        def __init__(self):
            self.audio_queue = _queue.Queue()
            self.is_paused = False
            self._release = threading.Event()
            self.entered_wait = threading.Event()

        def start(self, speed=1.0, prebuffer_frames=None):
            if prebuffer_frames is not None:
                self.min_chunks_to_start = max(1, int(prebuffer_frames))
            self._release.clear()

        def finish(self):
            ev = getattr(self, "playback_finished_event", None)
            if ev is not None:
                ev.set()

        def queue_depth(self):
            try:
                return self.audio_queue.qsize()
            except Exception:
                return 0

        def stop(self, graceful=False):
            self._release.set()

        def wait_until_finished(self, timeout=120.0):
            self.entered_wait.set()
            return self._release.wait(timeout)

        def get_queue_duration(self):
            return 0.0

    shared_state = TailSharedState()
    player = BlockingPlayer()
    runtime_state = RuntimeState()
    service = PlaybackService(
        shared_state=shared_state,
        player=player,
        storage=None,
        runtime_state=runtime_state,
        sentinel="PIPELINE_END_STRICT_V1",
        get_text_hash=lambda t: t,
        get_performance_profile=get_performance_profile,
        event_log=None,
    )

    # A "read" session that immediately hits the finally (no chunks to stream).
    read_session = PlaybackSession(
        id=service.controller.start_new_session(), chunks=[], config={}
    )

    t = threading.Thread(target=service._shared_task_loop, args=(read_session,))
    t.start()
    # Wait until the read thread is parked in wait_until_finished (it has already
    # passed the `if is_current(sid)` check at this point).
    assert player.entered_wait.wait(2.0), "read thread never reached the wait"

    # Now a podcast starts: a new session whose start_new_session()→player.stop()
    # wakes the parked (now-stale) read thread.
    service.controller.start_new_session()  # calls player.stop() → releases wait
    marker = len(shared_state.status_calls)

    t.join(2.0)
    assert not t.is_alive(), "read thread did not finish"

    # 陈旧线程醒来后不得再写 IDLE(is_current 门必须挡住它)
    assert "IDLE" not in shared_state.status_calls[marker:], (
        "stale read thread clobbered status_code to IDLE after a new session started"
    )


def test_short_wav_stays_playing_until_playback_finishes():
    """Bug 3 repro: a SHORT podcast's wav producer feeds all chunks almost
    instantly (qsize never throttles), exits, and the finally sets
    is_playing=False while ~7s of audio is still queued. The UI then sees
    main_is_playing=False and can't pause the short podcast. is_playing must
    track *playback* finishing, not the producer thread finishing."""
    import os
    import queue as _queue
    import tempfile
    import threading

    import numpy as np
    import scipy.io.wavfile as wavfile

    from core.services.playback_service import PlaybackService

    class TailSharedState:
        def __init__(self):
            self.text_q = _queue.Queue()
            self.audio_q = _queue.Queue()
            self.stop_event = mp.Event()
            self.current_task_id = mp.Value("i", 0)

        def set_status(self, _code):
            pass

    class BlockingPlayer:
        def __init__(self):
            self.audio_queue = _queue.Queue()
            self.is_paused = False
            self.is_prebuffering = False
            self._release = threading.Event()
            self.entered_wait = threading.Event()

        def is_running(self):
            # E4:计算式真相——播放未结束(release 未触发)即在播
            return not self._release.is_set()

        def start(self, speed=1.0, prebuffer_frames=None):
            if prebuffer_frames is not None:
                self.min_chunks_to_start = max(1, int(prebuffer_frames))
            self._release.clear()

        def finish(self):
            ev = getattr(self, "playback_finished_event", None)
            if ev is not None:
                ev.set()

        def queue_depth(self):
            try:
                return self.audio_queue.qsize()
            except Exception:
                return 0

        def stop(self, graceful=False):
            self._release.set()

        def play_chunk(self, _c):
            pass

        def signal_end_of_article(self):
            pass

        def wait_until_finished(self, timeout=120.0):
            self.entered_wait.set()
            return self._release.wait(timeout)

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "short.wav")
        wavfile.write(wav_path, 24000, (np.zeros(12000) ).astype(np.int16))

        shared_state = TailSharedState()
        player = BlockingPlayer()
        runtime_state = RuntimeState()
        service = PlaybackService(
            shared_state=shared_state,
            player=player,
            storage=None,
            runtime_state=runtime_state,
            sentinel="PIPELINE_END_STRICT_V1",
            get_text_hash=lambda t: t,
            get_performance_profile=get_performance_profile,
            event_log=None,
        )

        session_id = service.controller.start_new_session()
        player.start()  # play_wav_file 会同步 start;此 harness 直接驱动线程,补上

        t = threading.Thread(
            target=service._play_wav_thread, args=(wav_path, session_id, [], "short")
        )
        t.start()

        # The producer finishes feeding almost instantly; with the fix it then
        # parks in wait_until_finished (playback still ongoing). Either way give
        # it a moment to get past the feed loop.
        assert player.entered_wait.wait(2.0), "wav thread never waited for playback to finish"

        # E4 改写:播放真相=playback_status() 现算。音频未放完 → 仍是 playing → 可暂停。
        assert service.playback_status() == "playing", (
            "playback_status went idle while audio still playing (short podcast can't pause)"
        )

        player.stop()  # playback actually finishes
        t.join(2.0)
        assert not t.is_alive()
        assert service.playback_status() == "idle"


def test_play_wav_file_entrypoint_starts_playback():
    """E4 回归(2026-07-01 M smoke 实测抓获):play_wav_file 本体曾因残留的
    set_main(..., is_playing=True) 调用抛 TypeError 当场夭折——点播客无声、
    只留 podcast_play_requested 事件。守住:入口调用不抛、wav 线程真启动、
    playback_status 现算为 playing。(此前测试只驱动 _play_wav_thread 内层,
    没盖住入口的 runtime_state/storage 交互。)"""
    import os
    import queue as _queue
    import tempfile
    import threading

    import numpy as np
    import scipy.io.wavfile as wavfile

    from core.services.playback_service import PlaybackService

    class TailSharedState:
        def __init__(self):
            self.text_q = _queue.Queue()
            self.audio_q = _queue.Queue()
            self.stop_event = mp.Event()
            self.current_task_id = mp.Value("i", 0)

        def set_status(self, _code):
            pass

    class BlockingPlayer:
        def __init__(self):
            self.audio_queue = _queue.Queue()
            self.is_paused = False
            self.is_prebuffering = False
            self.min_chunks_to_start = 1
            self.playback_finished_event = threading.Event()
            self._release = threading.Event()
            self.entered_wait = threading.Event()

        def is_running(self):
            return not self._release.is_set()

        def start(self, speed=1.0, prebuffer_frames=None):
            if prebuffer_frames is not None:
                self.min_chunks_to_start = max(1, int(prebuffer_frames))
            self._release.clear()
            self.playback_finished_event.clear()

        def finish(self):
            ev = getattr(self, "playback_finished_event", None)
            if ev is not None:
                ev.set()

        def queue_depth(self):
            try:
                return self.audio_queue.qsize()
            except Exception:
                return 0

        def stop(self, graceful=False):
            self._release.set()
            self.playback_finished_event.set()

        def play_chunk(self, _c):
            pass

        def signal_end_of_article(self):
            pass

        def wait_until_finished(self, timeout=120.0):
            self.entered_wait.set()
            return self._release.wait(timeout)

        def get_queue_duration(self):
            return 0.0

    class FakeStorage:
        def __init__(self):
            self.saved = None

        def load_state(self):
            return {}

        def save_state(self, state):
            self.saved = state

        def load_config(self):
            return {}

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "p.wav")
        wavfile.write(wav_path, 24000, np.zeros(12000).astype(np.int16))
        with open(os.path.join(tmp, "p.txt"), "w", encoding="utf-8") as f:
            f.write("第一句。第二句。")

        storage = FakeStorage()
        player = BlockingPlayer()
        service = PlaybackService(
            shared_state=TailSharedState(),
            player=player,
            storage=storage,
            runtime_state=RuntimeState(),
            sentinel="PIPELINE_END_STRICT_V1",
            get_text_hash=lambda t: t,
            get_performance_profile=get_performance_profile,
            event_log=None,
        )
        try:
            service.play_wav_file(wav_path, "p.wav")  # 入口本体,不抛即第一关
            assert player.entered_wait.wait(2.0), "wav thread never started"
            assert service.playback_status() == "playing"
            # 持久化播客标记仍在(RESTART_MODE 依赖)
            assert storage.saved["current_article"]["podcast_filename"] == "p.wav"
        finally:
            player.stop()
            service.shutdown(join_timeout=2.0)


def test_play_marks_playing_synchronously():
    """#1 (pause after 'next'): seek calls play() synchronously, so the moment
    play() returns, the computed playback_status must already be non-idle — no
    deferred/async restart window where the UI sees idle and the pause button
    breaks. (E4 改写:断言从存储标志迁移到 playback_status() 计算式真相。)"""
    import queue as _queue

    from core.services.playback_service import PlaybackService

    class TailSharedState:
        def __init__(self):
            self.text_q = _queue.Queue()
            self.audio_q = _queue.Queue()
            self.stop_event = mp.Event()
            self.current_task_id = mp.Value("i", 0)

        def set_status(self, _code):
            pass

    import threading

    class BlockingPlayer:
        """Models real playback: the producer parks in wait_until_finished until
        audio actually drains, so playback_status stays non-idle until then."""

        def __init__(self):
            self.audio_queue = _queue.Queue()
            self.is_paused = False
            self.is_prebuffering = False
            self.min_chunks_to_start = 1
            self._started = False
            self._release = threading.Event()
            self.playback_finished_event = threading.Event()

        def is_running(self):
            return self._started and not self._release.is_set()

        def start(self, speed=1.0, prebuffer_frames=None):
            if prebuffer_frames is not None:
                self.min_chunks_to_start = max(1, int(prebuffer_frames))
            self._started = True
            self._release.clear()  # real player clears playback_finished_event on start

        def finish(self):
            ev = getattr(self, "playback_finished_event", None)
            if ev is not None:
                ev.set()

        def queue_depth(self):
            try:
                return self.audio_queue.qsize()
            except Exception:
                return 0

        def stop(self, graceful=False):
            self._release.set()

        def wait_until_finished(self, timeout=120.0):
            return self._release.wait(timeout)

        def get_queue_duration(self):
            return 0.0

    runtime_state = RuntimeState()
    player = BlockingPlayer()
    service = PlaybackService(
        shared_state=TailSharedState(),
        player=player,
        storage=None,
        runtime_state=runtime_state,
        sentinel="PIPELINE_END_STRICT_V1",
        get_text_hash=lambda t: t,
        get_performance_profile=get_performance_profile,
        event_log=None,
    )
    try:
        assert service.playback_status() == "idle"
        service.play(["a", "b", "c"], {}, start_idx=1, prebuffer_frames=6)
        # The moment play() returns the UI must already read non-idle, and it
        # must STAY non-idle while playback is ongoing (the seek pause-button fix).
        assert service.playback_status() in ("playing", "generating")
        time.sleep(0.1)
        assert service.playback_status() in ("playing", "generating")
    finally:
        player.stop()  # let the producer thread finish and exit cleanly
        service.shutdown(join_timeout=2.0)


def test_play_starts_player_synchronously_and_marks_generating():
    """ADR-003 F1: play() starts the player BEFORE returning (was: in the
    producer thread), so a command response computed right after play() sees
    'generating' instead of stale 'idle' — which had flipped the pause button to
    'play' after seek/next. Also the seek prebuffer is applied at start. Replaces
    the old _shared_task_loop-driven prebuffer test."""
    import queue as _queue
    import threading

    from core.services.playback_service import PlaybackService

    class TailSharedState:
        def __init__(self):
            self.text_q = _queue.Queue()
            self.audio_q = _queue.Queue()
            self.stop_event = mp.Event()
            self.current_task_id = mp.Value("i", 0)

        def set_status(self, _code):
            pass

    class RecPlayer:
        def __init__(self):
            self.audio_queue = _queue.Queue()
            self.is_paused = False
            self.is_prebuffering = False
            self.min_chunks_to_start = 1
            self.seen_at_start = None
            self.running = False
            self._release = threading.Event()
            self.playback_finished_event = threading.Event()

        def start(self, speed=1.0, prebuffer_frames=None):
            if prebuffer_frames is not None:
                self.min_chunks_to_start = max(1, int(prebuffer_frames))
            self.seen_at_start = self.min_chunks_to_start
            self.running = True
            self.is_prebuffering = True
            self._release.clear()

        def finish(self):
            ev = getattr(self, "playback_finished_event", None)
            if ev is not None:
                ev.set()

        def queue_depth(self):
            try:
                return self.audio_queue.qsize()
            except Exception:
                return 0

        def stop(self, graceful=False):
            self.running = False
            self._release.set()

        def is_running(self):
            return self.running

        def wait_until_finished(self, timeout=120.0):
            return self._release.wait(timeout)

        def get_queue_duration(self):
            return 0.0

    player = RecPlayer()
    service = PlaybackService(
        shared_state=TailSharedState(),
        player=player,
        storage=None,
        runtime_state=RuntimeState(),
        sentinel="PIPELINE_END_STRICT_V1",
        get_text_hash=lambda t: t,
        get_performance_profile=get_performance_profile,
        event_log=None,
    )

    try:
        # Seek: prebuffer applied at start, AND status is generating right away.
        service.play(["a"], {}, prebuffer_frames=6)
        assert player.seen_at_start == 6, "seek prebuffer not applied at start()"
        assert service.playback_status() == "generating", "play() must mark generating synchronously"

        # Normal read keeps prebuffer 1 (the new session stops the first).
        service.play(["a"], {})
        assert player.seen_at_start == 1, "normal read should keep prebuffer_frames=1"
    finally:
        player.stop()
        service.shutdown(join_timeout=2.0)


def test_wav_playback_resets_seek_prebuffer():
    """A WAV is fully available, so _play_wav_thread must reset min_chunks_to_start
    to 1 before start(). Otherwise a large pre-roll left over from a prior seek
    could exceed a short podcast's total chunk count and stall it forever."""
    import os
    import queue as _queue
    import tempfile
    import threading

    import numpy as np
    import scipy.io.wavfile as wavfile

    from core.services.playback_service import PlaybackService

    class TailSharedState:
        def __init__(self):
            self.text_q = _queue.Queue()
            self.audio_q = _queue.Queue()
            self.stop_event = mp.Event()
            self.current_task_id = mp.Value("i", 0)

        def set_status(self, _code):
            pass

    class RecPlayer:
        def __init__(self):
            self.audio_queue = _queue.Queue()
            self.is_paused = False
            self.min_chunks_to_start = 99  # leftover from a prior seek
            self.seen_at_start = None

        def start(self, speed=1.0, prebuffer_frames=None):
            if prebuffer_frames is not None:
                self.min_chunks_to_start = max(1, int(prebuffer_frames))
            self.seen_at_start = self.min_chunks_to_start

        def finish(self):
            ev = getattr(self, "playback_finished_event", None)
            if ev is not None:
                ev.set()

        def queue_depth(self):
            try:
                return self.audio_queue.qsize()
            except Exception:
                return 0

        def stop(self, graceful=False):
            pass

        def play_chunk(self, _c):
            pass

        def signal_end_of_article(self):
            pass

        def wait_until_finished(self, timeout=120.0):
            return True

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "short.wav")
        wavfile.write(wav_path, 24000, np.zeros(12000).astype(np.int16))
        shared_state = TailSharedState()
        player = RecPlayer()
        service = PlaybackService(
            shared_state=shared_state,
            player=player,
            storage=None,
            runtime_state=RuntimeState(),
            sentinel="PIPELINE_END_STRICT_V1",
            get_text_hash=lambda t: t,
            get_performance_profile=get_performance_profile,
            event_log=None,
        )
        session_id = service.controller.start_new_session()
        t = threading.Thread(
            target=service._play_wav_thread, args=(wav_path, session_id, [], "x")
        )
        t.start()
        t.join(2.0)
        assert not t.is_alive()
        assert player.seen_at_start == 1, "WAV playback did not reset seek prebuffer"


def test_url_job_store_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        store = UrlJobStore(os.path.join(tmp, "url_jobs.json"))
        store.create(
            job_id="url-1",
            url="https://example.com",
            mode="podcast-discuss",
            action="podcast",
            has_html=True,
        )
        store.update("url-1", status="running", stage="gemini", text_chars=120)
        assert store.list()[0]["stage"] == "gemini"
        assert store.list()[0]["text_chars"] == 120

        store.mark_unfinished_failed("restart")
        assert store.list()[0]["status"] == "failed"
        assert store.list()[0]["stage"] == "interrupted"


def test_reader_service_helpers_are_stable():
    assert cache_key("a", "bc") != cache_key("ab", "c")
    # #12-②:title_for_mode 已删——模式不再烤进标题,内容形态由
    # content_mode 字段 + labels.mode_label 承载(见 test_labels)。


def test_reader_service_cleans_references_and_web_links():
    raw = """# Title

Useful [article link](https://example.com/a?x=1) text.

<iframe src="https://tracker.example/embed"></iframe>

Bare URL https://tracking.example/path should go.

## References

[^1]: A long citation https://doi.org/example
"""
    cleaned = clean_markdown_content(raw)
    assert "Useful article link text." in cleaned
    assert "iframe" not in cleaned
    assert "https://" not in cleaned
    assert "References" not in cleaned
    assert "long citation" not in cleaned

    zh_cleaned = clean_markdown_content("正文\n\n## 参考文献\n\n[文章](https://example.com)")
    assert zh_cleaned == "正文"


def test_reader_service_does_not_use_references_as_title():
    raw = """![image](https://example.com/image.jpg)

Short article body.

## References

[^1]: Citation.
"""
    assert extract_title(raw) == ""
    assert extract_title(clean_markdown_content(raw)) == ""

