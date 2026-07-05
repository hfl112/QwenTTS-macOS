"""C1(计划 #10)——朗读/URL 编排的 characterization 测试。

先钉住 /read 与 /read_url 现行为(此前这些分支零覆盖),再抽 ReadOrchestrator;
抽取全程本文件必须**原样保绿**。
"""

import asyncio
import os
import time

from fastapi.testclient import TestClient

import core.backend as backend_mod
from core.api_models import ReadUrlRequest
from core.backend import app, init_runtime_services


def _client(monkeypatch) -> TestClient:
    # 改状态 POST 走 default-deny 鉴权;pytest 下 testclient 是 loopback,
    # 用与 test_week3 相同的 dev 旁路。
    monkeypatch.setenv("TTS_LEGACY_LOOPBACK_CLIENTS", "1")
    init_runtime_services()
    return TestClient(app)


def test_read_llm_mode_replaces_text_before_playback(monkeypatch):
    """① mode≠original:先经 process_with_llm,产物才是被朗读/入 state 的文本。"""
    client = _client(monkeypatch)
    monkeypatch.setattr(
        backend_mod.reader_bridge, "process_with_llm", lambda text, mode: "PROCESSED翻译结果"
    )
    resp = client.post("/read", json={"text": "hello world", "mode": "translate"})
    assert resp.status_code == 200
    art = backend_mod.storage.load_state()["current_article"]
    assert art["title"].startswith("PROCESSED")
    joined = "".join(c if isinstance(c, str) else c.get("text", "") for c in art["chunks"])
    assert "PROCESSED翻译结果" in joined
    client.post("/stop")


def test_read_llm_mode_failure_returns_500_with_reason(monkeypatch):
    """① LLM 抛错 → 500,detail 含「{mode} 处理失败」。"""
    client = _client(monkeypatch)

    def _boom(text, mode):
        raise RuntimeError("no key")

    monkeypatch.setattr(backend_mod.reader_bridge, "process_with_llm", _boom)
    # N1:入口送旧名 podcast-discuss 也被归一为 dual-summary(错误信息用规范名)
    resp = client.post("/read", json={"text": "hello", "mode": "podcast-discuss"})
    assert resp.status_code == 500
    assert "dual-summary 处理失败" in resp.json()["detail"]


def test_restart_mode_wav_branch_replays_podcast_without_tts(monkeypatch):
    """② RESTART_MODE + state 带 podcast_filename → 走 play_wav_file(0),
    不走 TTS 的 play()。"""
    client = _client(monkeypatch)
    svc = backend_mod.podcast_service
    os.makedirs(svc.podcasts_dir, exist_ok=True)
    wav = os.path.join(svc.podcasts_dir, "podcast_单篇_web_标题_abcd1234_1.wav")
    with open(wav, "wb") as f:
        f.write(b"RIFF")
    state = backend_mod.storage.load_state()
    state["current_article"] = {
        "title": "标题",
        "chunks": ["第一句", "第二句"],
        "current_index": 1,
        "podcast_filename": os.path.basename(wav),
    }
    backend_mod.storage.save_state(state)

    wav_calls, play_calls = [], []
    monkeypatch.setattr(
        backend_mod.playback_service,
        "play_wav_file",
        lambda fp, fn, start_idx=0: wav_calls.append((fp, fn, start_idx)),
    )
    monkeypatch.setattr(
        backend_mod.playback_service,
        "play",
        lambda *a, **k: play_calls.append((a, k)),
    )
    try:
        resp = client.post("/read", json={"text": "RESTART_MODE"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok" and "playback_status" in body
        assert len(wav_calls) == 1 and wav_calls[0][2] == 0
        assert wav_calls[0][1] == os.path.basename(wav)
        assert play_calls == []
    finally:
        os.remove(wav)
        state["current_article"] = {"title": "", "chunks": [], "current_index": 0}
        backend_mod.storage.save_state(state)


def test_read_url_dedup_within_60s(monkeypatch):
    """③ 同 URL 60s 内二次提交 → 拒绝并提示后台解析中(任务不重复起)。"""
    client = _client(monkeypatch)
    captured = []
    # 不让后台任务真的跑:create_task 只捕获协程(否则跑完会 pop 掉去重记录)
    monkeypatch.setattr(
        backend_mod.runtime_supervisor,
        "create_task",
        lambda coro, job_id=None: captured.append(coro) or coro.close(),
    )
    url = "https://example.com/article-dedup-test"
    try:
        r1 = client.post("/read_url", json={"url": url}).json()
        assert r1["status"] == "ok" and r1["job_id"].startswith("url_")
        r2 = client.post("/read_url", json={"url": url}).json()
        assert r2["status"] == "error"
        assert "正处于后台解析" in r2["message"]
        assert len(captured) == 1
    finally:
        backend_mod.ACTIVE_URL_TASKS.pop(url, None)


def test_read_url_read_action_dispatches_read_text_with_url_source(monkeypatch):
    """④ 无 save/podcast 的 URL 任务最终以 source="url" 进 read_text;
    job store 走到 done。"""
    client = _client(monkeypatch)

    class _Result:
        title = "文章标题"
        source = "web"
        text = "解析出来的正文"
        voice = None
        from_cache = False

    monkeypatch.setattr(
        backend_mod.reader_bridge, "process_url_job", lambda **kwargs: _Result()
    )
    read_calls = []

    async def fake_read(req):
        read_calls.append(req)
        return {"status": "ok"}

    # C1.3 seam 迁移:read 分发点从路由函数移到 orchestrator.read
    monkeypatch.setattr(backend_mod.orchestrator, "read", fake_read)

    captured = []
    monkeypatch.setattr(
        backend_mod.runtime_supervisor,
        "create_task",
        lambda coro, job_id=None: captured.append(coro),
    )
    url = "https://example.com/article-dispatch-test"
    try:
        r = client.post("/read_url", json={"url": url}).json()
        assert r["status"] == "ok"
        asyncio.run(captured[0])  # 手动驱动后台任务

        assert len(read_calls) == 1
        assert read_calls[0].source == "url"
        assert read_calls[0].text == "解析出来的正文"
        job = next(j for j in backend_mod.url_job_store.list() if j["job_id"] == r["job_id"])
        assert job["status"] == "done"
        assert backend_mod.ACTIVE_URL_TASKS.get(url) is None  # finally 里已清
    finally:
        backend_mod.ACTIVE_URL_TASKS.pop(url, None)


# ---------------------------------------------------------------------------
# M2(计划 #13)——/seek 与 /generate_podcast(合集)编排收进 ReadOrchestrator
# ---------------------------------------------------------------------------

def _set_article(article: dict) -> None:
    state = backend_mod.storage.load_state()
    state["current_article"] = article
    backend_mod.storage.save_state(state)


def test_seek_wav_decision_reads_persisted_podcast_filename(monkeypatch):
    """M2 收口:seek 的 wav-vs-TTS 决策与 RESTART_MODE 同源——读持久化的
    podcast_filename(此前读内存 current_playing_podcast,冷启动后两处决策分叉)。"""
    client = _client(monkeypatch)
    svc = backend_mod.podcast_service
    os.makedirs(svc.podcasts_dir, exist_ok=True)
    wav = os.path.join(svc.podcasts_dir, "podcast_单篇_web_切句_abcd1234_2.wav")
    with open(wav, "wb") as f:
        f.write(b"RIFF")
    _set_article({
        "title": "切句",
        "chunks": ["a", "b", "c"],
        "current_index": 0,
        "podcast_filename": os.path.basename(wav),
    })
    # 冷启动语义:内存信号为空,只有持久化标记在
    backend_mod.runtime_state.current_playing_podcast = None

    wav_calls, play_calls = [], []
    monkeypatch.setattr(
        backend_mod.playback_service, "play_wav_file",
        lambda fp, fn, start_idx=0: wav_calls.append((fp, fn, start_idx)),
    )
    monkeypatch.setattr(
        backend_mod.playback_service, "play",
        lambda *a, **k: play_calls.append((a, k)),
    )
    try:
        r = client.post("/seek", json={"direction": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "seeking" and body["new_index"] == 1
        assert "playback_status" in body
        assert len(wav_calls) == 1 and wav_calls[0][2] == 1
        assert wav_calls[0][1] == os.path.basename(wav)
        assert play_calls == []  # 不烧 GPU
        # 索引已持久化
        assert backend_mod.storage.load_state()["current_article"]["current_index"] == 1
    finally:
        os.remove(wav)
        _set_article({"title": "", "chunks": [], "current_index": 0})


def test_seek_tts_path_clamps_and_uses_seek_prebuffer(monkeypatch):
    """characterization:无播客标记的普通文章,seek=play(new_idx),越界钳制,
    用加大的 SEEK 预缓冲。"""
    client = _client(monkeypatch)
    _set_article({"title": "文", "chunks": ["a", "b"], "current_index": 1})
    play_calls = []
    monkeypatch.setattr(
        backend_mod.playback_service, "play",
        lambda *a, **k: play_calls.append((a, k)),
    )
    try:
        r = client.post("/seek", json={"direction": 1})
        assert r.status_code == 200
        assert r.json()["new_index"] == 1  # 已是末句,钳在 1
        assert len(play_calls) == 1
        a, k = play_calls[0]
        assert k.get("start_idx") == 1
        assert k.get("prebuffer_frames") == 6  # SEEK_PREBUFFER_FRAMES
    finally:
        _set_article({"title": "", "chunks": [], "current_index": 0})


def test_seek_without_article_400(monkeypatch):
    client = _client(monkeypatch)
    _set_article({"title": "", "chunks": [], "current_index": 0})
    r = client.post("/seek", json={"direction": 1})
    assert r.status_code == 400


def test_generate_batch_podcast_orchestration(monkeypatch):
    """characterization:合集播客——quiet 档、首条 voice、start_batch、清空 saved。"""
    client = _client(monkeypatch)
    items = [
        {"text": "第一篇", "voice": "Ryan"},
        {"text": "第二篇", "voice": "Serena"},
    ]
    cleared = []
    monkeypatch.setattr(backend_mod.saved_items_service, "load", lambda: list(items))
    monkeypatch.setattr(backend_mod.saved_items_service, "clear", lambda: cleared.append(1))
    batch_calls = []
    monkeypatch.setattr(backend_mod.podcast_service, "is_generating", lambda md5: False)
    monkeypatch.setattr(
        backend_mod.podcast_service, "start_batch",
        lambda **kw: batch_calls.append(kw),
    )
    r = client.post("/generate_podcast")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "generating" and "合集" in body["filename"]
    assert len(batch_calls) == 1
    kw = batch_calls[0]
    assert kw["config"]["performance_profile"] == "quiet"
    assert kw["config"]["voice"] == "Ryan"
    assert kw["text"] == "第一篇\n\n第二篇"
    assert cleared == [1]


def test_generate_batch_podcast_dedup_and_empty(monkeypatch):
    """characterization:空 saved → 400;同内容已在生成 → 提示且清空、不 start。"""
    client = _client(monkeypatch)
    monkeypatch.setattr(backend_mod.saved_items_service, "load", lambda: [])
    assert client.post("/generate_podcast").status_code == 400

    cleared = []
    monkeypatch.setattr(
        backend_mod.saved_items_service, "load", lambda: [{"text": "同文", "voice": None}]
    )
    monkeypatch.setattr(backend_mod.saved_items_service, "clear", lambda: cleared.append(1))
    monkeypatch.setattr(backend_mod.podcast_service, "is_generating", lambda md5: True)
    started = []
    monkeypatch.setattr(
        backend_mod.podcast_service, "start_batch", lambda **kw: started.append(kw)
    )
    r = client.post("/generate_podcast")
    assert r.status_code == 200
    assert "已在后台生成中" in r.json()["message"]
    assert started == [] and cleared == [1]
