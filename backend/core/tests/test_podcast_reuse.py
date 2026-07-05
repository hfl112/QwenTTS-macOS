"""功能计划 #8(CONTEXT.md §4h)——三层复用+去重 的回归测试。

R1: job 记录带 mode/voice/content_key,旧 podcast_jobs.json 兼容。
"""

import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import core.services.podcast_service as podcast_service_module
from core.services.podcast_jobs import PodcastJobStore, content_key
from core.services.podcast_service import PodcastService, resolve_voice
from core.state.runtime_state import RuntimeState


class _FakeProc:
    def __init__(self):
        self._alive = True

    @property
    def exitcode(self):
        return None if self._alive else 0

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self._alive = False


def _make_service(tmp: str) -> PodcastService:
    return PodcastService(
        podcasts_dir=os.path.join(tmp, "podcasts"),
        podcast_chunk_dir=os.path.join(tmp, "chunks"),
        runtime_state=RuntimeState(),
        active_url_tasks={},
        jobs_file=os.path.join(tmp, "podcast_jobs.json"),
        get_battery_policy=lambda: "allow",
    )


def test_content_key_sensitivity():
    """content_key 对 (text, mode, voice) 三者敏感;同输入稳定。"""
    base = content_key("hello world", "original", "Serena")
    assert base == content_key("hello world", "original", "Serena")
    assert base != content_key("hello world!", "original", "Serena")
    assert base != content_key("hello world", "podcast-discuss", "Serena")
    assert base != content_key("hello world", "original", "Ethan")


def test_resolve_voice_falls_back_to_config():
    assert resolve_voice("Ethan", {"voice": "Serena"}) == "Ethan"
    assert resolve_voice(None, {"voice": "Serena"}) == "Serena"
    assert resolve_voice(None, {}) == "Serena"  # 最终兜底=storage 默认音色


def test_start_single_persists_mode_voice_content_key(monkeypatch):
    """新 job 落盘含 mode/voice/content_key,且 key 与 content_key() 一致。"""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(tmp)

        def fake_spawn(pending):
            proc = _FakeProc()
            service.active_procs.append(proc)
            service.active_tasks[pending["md5"]] = proc
            service.active_job_ids[pending["md5"]] = pending["job_id"]

        monkeypatch.setattr(service, "_spawn", fake_spawn)

        service.start_single(
            text="早上好", config={"voice": "Serena"}, md5="aaa",
            source="web", title="T", mode="podcast-discuss", voice=None,
        )
        job = service.job_store.list()[0]
        assert job["mode"] == "dual-summary"  # N1:旧名入口被归一
        assert job["voice"] == "Serena"  # voice=None 落 config 实际值
        assert job["content_key"] == content_key("早上好", "dual-summary", "Serena")  # N1:key 用规范名
        # title 不参与身份
        assert job["content_key"] == content_key("早上好", "dual-summary", "Serena")  # N1:key 用规范名


def _seed_done_job(service: PodcastService, tmp: str, *, text: str, mode: str, voice: str) -> str:
    """向 job store 塞一条 done 任务并落一个真实 wav 文件,返回 wav 路径。"""
    wav = os.path.join(tmp, f"podcast_{mode}_{voice}.wav")
    with open(wav, "wb") as f:
        f.write(b"RIFF fake wav")
    service.job_store.create(
        job_id=f"single_done_{mode}_{voice}",
        kind="single",
        md5="feedface",
        title="done",
        source="web",
        mode=mode,
        voice=voice,
        content_key=content_key(text, mode, voice),
    )
    service.job_store.update(f"single_done_{mode}_{voice}", status="done", output_path=wav)
    return wav


def test_find_reusable_output_hits_done_product(monkeypatch):
    """R2①:同 text+mode+voice、前 job done 且 wav 在 → 命中成品。"""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(tmp)
        wav = _seed_done_job(service, tmp, text="你好", mode="original", voice="Serena")
        got = service.find_reusable_output(
            text="你好", mode="original", voice=None, config={"voice": "Serena"}
        )
        assert got == wav


def test_find_reusable_output_misses_when_wav_deleted(monkeypatch):
    """R2②:wav 被删 → 不复用(应正常重新生成)。"""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(tmp)
        wav = _seed_done_job(service, tmp, text="你好", mode="original", voice="Serena")
        os.remove(wav)
        assert service.find_reusable_output(
            text="你好", mode="original", voice=None, config={"voice": "Serena"}
        ) is None


def test_find_reusable_output_respects_mode_and_voice(monkeypatch):
    """R2③:同 text 不同 mode/voice → 不判重。"""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(tmp)
        _seed_done_job(service, tmp, text="你好", mode="original", voice="Serena")
        assert service.find_reusable_output(
            text="你好", mode="podcast-discuss", voice=None, config={"voice": "Serena"}
        ) is None
        assert service.find_reusable_output(
            text="你好", mode="original", voice="Ethan", config={"voice": "Serena"}
        ) is None


def test_endpoint_exists_and_force(monkeypatch):
    """R2④⑤(接线):exists 不开工;force=true 绕过复用开工;生成中仍 409。"""
    from fastapi.testclient import TestClient

    os.environ["TTS_MANAGEMENT_TOKEN"] = "test-token-123"
    try:
        import core.backend as backend_module
        from core.backend import app, init_runtime_services

        init_runtime_services()
        client = TestClient(app)
        headers = {"x-management-token": "test-token-123"}
        started: list[dict] = []
        monkeypatch.setattr(
            backend_module.podcast_service, "start_single",
            lambda **kw: started.append(kw),
        )
        monkeypatch.setattr(
            backend_module.podcast_service, "is_generating", lambda md5: False
        )
        monkeypatch.setattr(
            backend_module.podcast_service, "find_reusable_output",
            lambda **kw: "/tmp/fake/prod.wav",
        )

        # exists:有成品 → 不开工
        r = client.post("/generate_single_podcast", json={"text": "你好"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "exists"
        assert r.json()["filename"] == "prod.wav"
        assert started == []

        # force:绕过复用 → 开工
        r = client.post(
            "/generate_single_podcast",
            json={"text": "你好", "force": True},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "generating"
        assert len(started) == 1
        assert started[0]["mode"] == "original"

        # 生成中 → 仍 409
        monkeypatch.setattr(
            backend_module.podcast_service, "is_generating", lambda md5: True
        )
        r = client.post("/generate_single_podcast", json={"text": "你好"}, headers=headers)
        assert r.status_code == 409
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_readurl_style_mode_flows_to_job_then_second_call_exists(monkeypatch):
    """R3:mode(如 podcast-discuss)透传进 job;同 text+mode 完成后二次提交 → exists。"""
    from fastapi.testclient import TestClient

    os.environ["TTS_MANAGEMENT_TOKEN"] = "test-token-123"
    try:
        import core.backend as backend_module
        from core.backend import app, init_runtime_services

        init_runtime_services()
        client = TestClient(app)
        headers = {"x-management-token": "test-token-123"}
        monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
        with tempfile.TemporaryDirectory() as tmp:
            service = _make_service(tmp)

            def fake_spawn(pending):
                proc = _FakeProc()
                service.active_procs.append(proc)
                service.active_tasks[pending["md5"]] = proc
                service.active_job_ids[pending["md5"]] = pending["job_id"]

            monkeypatch.setattr(service, "_spawn", fake_spawn)
            monkeypatch.setattr(backend_module, "podcast_service", service)

            script = "主持人A:今天聊聊……\n主持人B:好的。"
            # preprocessed=True 模拟 read_url:text 已是 LLM 脚本,端点不再跑 LLM
            body = {"text": script, "mode": "podcast-discuss", "source": "url", "preprocessed": True}

            # 第一次:开工,job 带 mode + 一致的 content_key
            r = client.post("/generate_single_podcast", json=body, headers=headers)
            assert r.status_code == 200 and r.json()["status"] == "generating"
            job = service.job_store.list()[0]
            assert job["mode"] == "dual-summary"  # N1:旧名入口被归一
            assert job["content_key"] == content_key(script, "dual-summary", job["voice"])

            # 完成:落成品 wav,清活跃任务
            wav = os.path.join(tmp, "prod.wav")
            with open(wav, "wb") as f:
                f.write(b"RIFF fake")
            service.job_store.update(job["job_id"], status="done", output_path=wav)
            for proc in service.active_procs:
                proc._alive = False
            service.cleanup_finished()

            # 第二次同 text+mode:exists,不开工
            r = client.post("/generate_single_podcast", json=body, headers=headers)
            assert r.status_code == 200
            assert r.json()["status"] == "exists"
            assert r.json()["filename"] == "prod.wav"
            assert len(service.job_store.list()) == 1  # 没建新 job
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_saved_item_mode_round_trip():
    """S1:saved item 存 mode 并持久化;旧条目无 mode 字段读取不炸。"""
    from core.services.saved_items_service import SavedItemsService

    with tempfile.TemporaryDirectory() as tmp:
        service = SavedItemsService(data_path=tmp)
        service.save("英文文章……", source="web", mode="podcast-trans")
        items = service.load()
        assert items[0]["mode"] == "podcast-trans"
        # 旧条目(手写无 mode)混入 → .get 兼容
        items.append({"text": "旧条目", "md5": "old", "title": "旧"})
        service.write(items)
        loaded = service.load()
        assert loaded[-1].get("mode", "original") == "original"


def _reader_modules():
    reader_dir = os.path.join(ROOT, "URL-Reader")
    if reader_dir not in sys.path:
        sys.path.append(reader_dir)
    import llm_engine
    import reader_service
    return llm_engine, reader_service


def test_saved_mode_requires_llm_configured(monkeypatch):
    """S1:mode≠original(podcast-*)且未配 LLM key → 4xx 带明确原因,不静默降级。"""
    from fastapi.testclient import TestClient

    os.environ["TTS_MANAGEMENT_TOKEN"] = "test-token-123"
    try:
        from core.backend import app, init_runtime_services

        init_runtime_services()
        client = TestClient(app)
        llm_engine, _ = _reader_modules()
        monkeypatch.setattr(llm_engine, "llm_selected_available", lambda: False)

        r = client.post(
            "/generate_single_podcast",
            json={"text": "原文……", "mode": "podcast-discuss"},
            headers={"x-management-token": "test-token-123"},
        )
        assert r.status_code == 400
        assert "AI 引擎" in r.json()["detail"]
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_saved_mode_runs_llm_then_tts_with_original_identity(monkeypatch):
    """S1:mode≠original → 后台先 LLM 再 TTS;job 身份=原文(与 exists 查重同口径)。"""
    from fastapi.testclient import TestClient

    os.environ["TTS_MANAGEMENT_TOKEN"] = "test-token-123"
    try:
        import core.backend as backend_module
        from core.backend import app, init_runtime_services

        init_runtime_services()
        client = TestClient(app)
        headers = {"x-management-token": "test-token-123"}
        llm_engine, reader_service = _reader_modules()
        monkeypatch.setattr(llm_engine, "llm_selected_available", lambda: True)
        monkeypatch.setattr(
            reader_service, "process_with_llm", lambda text, mode: "剧本:" + text
        )
        monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
        with tempfile.TemporaryDirectory() as tmp:
            service = _make_service(tmp)
            spawned_texts: list[str] = []

            def fake_spawn(pending):
                spawned_texts.append(pending["args"][0])
                proc = _FakeProc()
                service.active_procs.append(proc)
                service.active_tasks[pending["md5"]] = proc
                service.active_job_ids[pending["md5"]] = pending["job_id"]

            monkeypatch.setattr(service, "_spawn", fake_spawn)
            monkeypatch.setattr(backend_module, "podcast_service", service)

            original = "这是导入时标记为双人讨论的原文。"
            r = client.post(
                "/generate_single_podcast",
                json={"text": original, "mode": "podcast-discuss"},
                headers=headers,
            )
            assert r.status_code == 200 and r.json()["status"] == "generating"

            # 后台任务在 TestClient 的事件循环上跑:用后续请求推动循环直至 job 出现
            for _ in range(100):
                client.get("/health")
                if service.job_store.list():
                    break
            job = service.job_store.list()[0]
            assert job["mode"] == "dual-summary"  # N1:旧名入口被归一
            # 身份=原文,不是 LLM 脚本
            assert job["content_key"] == content_key(original, "dual-summary", job["voice"])
            # 合成用的却是 LLM 脚本
            assert spawned_texts and spawned_texts[0].startswith("剧本:")
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_process_with_llm_caches_per_mode_and_text(monkeypatch):
    """R4:同 (mode,text) 二次调用只烧一次 LLM;original 直通不写缓存;use_cache=False 每次都调。"""
    llm_engine, reader_service = _reader_modules()
    calls: list[str] = []

    def fake_call_llm(prompt, tier="standard", step_name="", **kw):
        calls.append(step_name)
        return "生成的剧本"

    monkeypatch.setattr(llm_engine, "call_llm", fake_call_llm)
    with tempfile.TemporaryDirectory() as tmp:
        # 两次同 (mode,text):第二次走缓存,LLM 只被调 1 次
        r1 = reader_service.process_with_llm("原文A", "podcast-discuss", cache_dir=tmp)
        r2 = reader_service.process_with_llm("原文A", "podcast-discuss", cache_dir=tmp)
        assert r1 == r2 == "生成的剧本"
        assert len(calls) == 1
        # 不同 text → 再烧一次
        reader_service.process_with_llm("原文B", "podcast-discuss", cache_dir=tmp)
        assert len(calls) == 2
        # original 直通:不调 LLM、不写缓存文件
        before = set(os.listdir(tmp))
        assert reader_service.process_with_llm("原文A", "original", cache_dir=tmp) == "原文A"
        assert set(os.listdir(tmp)) == before
        # use_cache=False(process_url_job 外层缓存路径):每次都真调
        reader_service.process_with_llm("原文A", "podcast-discuss", cache_dir=tmp, use_cache=False)
        assert len(calls) == 3


def test_stop_no_longer_cancels_podcast_jobs(monkeypatch):
    """停止键语义回归(2026-07-01 用户实测拍板):/stop 只停播放,
    不得掐死后台播客生成;取消播客走专门的 /podcasts/cancel_all。"""
    from fastapi.testclient import TestClient

    os.environ["TTS_MANAGEMENT_TOKEN"] = "test-token-123"
    try:
        import core.backend as backend_module
        from core.backend import app, init_runtime_services

        init_runtime_services()
        client = TestClient(app)
        headers = {"x-management-token": "test-token-123"}
        canceled: list[str] = []
        monkeypatch.setattr(
            backend_module.podcast_service, "cancel_all",
            lambda **kw: canceled.append("cancel"),
        )

        r = client.post("/stop", headers=headers)
        assert r.status_code == 200
        assert canceled == [], "/stop 不得取消后台播客任务"

        r = client.post("/podcasts/cancel_all", headers=headers)
        assert r.status_code == 200
        assert canceled == ["cancel"]
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_cancel_all_drains_queue_and_bumps_epoch(monkeypatch):
    """P0-1:cancel_all 排空 podcast_q + bump 取消代际(引擎据此掐断在做的段)。"""
    import multiprocessing as mp
    import queue as _queue

    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        q = _queue.Queue()
        epoch = mp.Value("i", 0)
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=RuntimeState(),
            active_url_tasks={},
            jobs_file=os.path.join(tmp, "podcast_jobs.json"),
            get_battery_policy=lambda: "allow",
            podcast_q=q,
            podcast_cancel_epoch=epoch,
        )
        q.put({"job_id": "a", "text": "x"})
        q.put({"job_id": "b", "text": "y"})

        service.cancel_all(terminate_timeout=0.1)

        assert q.empty(), "cancel_all 必须排空播客队列"
        assert epoch.value == 1, "cancel_all 必须 bump 取消代际"


def test_legacy_jobs_json_loads_without_new_fields():
    """旧格式 podcast_jobs.json(无 mode/voice/content_key)加载不炸、查询正常。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "podcast_jobs.json")
        legacy = [{
            "job_id": "single_old", "kind": "single", "md5": "deadbeef",
            "title": "旧任务", "source": "web", "status": "running",
            "created_at": 1.0, "updated_at": 1.0, "pid": None,
            "output_path": None, "error": None,
        }]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        store = PodcastJobStore(path)
        jobs = store.list()
        assert jobs[0].get("mode") is None
        assert jobs[0].get("content_key") is None
        assert store.active_for_md5("deadbeef") is True
