import asyncio
import os
import sys
import threading
import time
from fastapi.testclient import TestClient

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.services.runtime_supervisor import RuntimeSupervisor
from core.backend import app, init_runtime_services


def test_management_token_middleware():
    os.environ["TTS_MANAGEMENT_TOKEN"] = "test-token-123"
    # 确保初始化服务，否则 /stop 里引用的一些服务可能是 None
    init_runtime_services()
    try:
        client = TestClient(app)
        # 1. 无 token 应当返回 401
        response = client.post("/stop")
        assert response.status_code == 401
        assert "extension token" in response.json()["detail"]

        # 2. 错误 token 应当返回 401
        response = client.post("/stop", headers={"x-management-token": "wrong-token"})
        assert response.status_code == 401

        # 3. 正确 token 应当返回 200
        response = client.post("/stop", headers={"x-management-token": "test-token-123"})
        assert response.status_code == 200
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_state_changing_endpoints_require_token_by_default():
    """默认拒绝：之前未鉴权的播放控制端点现在无 token 必须 401。"""
    os.environ["TTS_MANAGEMENT_TOKEN"] = "test-token-123"
    init_runtime_services()
    try:
        client = TestClient(app)
        # 无 token 的状态变更端点（此前被无鉴权放行）现在应 401
        for path, body in [
            ("/seek", {"direction": 1}),
            ("/pause", None),
            ("/resume", None),
            ("/restart_audio", None),
        ]:
            resp = client.post(path, json=body)
            assert resp.status_code == 401, f"{path} should require a token, got {resp.status_code}"

        # 正确管理令牌应放行
        resp = client.post("/pause", headers={"x-management-token": "test-token-123"})
        assert resp.status_code == 200

        # /health 为公开只读端点：无 token 也应 200（修复此前被误纳入令牌门禁）
        resp = client.get("/health")
        assert resp.status_code == 200
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_extension_pairing_token_can_control_playback():
    """A paired browser extension may drive playback controls, but not
    management-only settings/engine endpoints."""
    os.environ["TTS_MANAGEMENT_TOKEN"] = "native-client-token"
    init_runtime_services()
    try:
        import core.backend as backend_mod

        old_config = backend_mod.storage.load_config()
        backend_mod.storage.save_config({"extension_pairing_token": "ext-token"})
        client = TestClient(app)
        headers = {"x-extension-token": "ext-token"}

        for path, body in [
            ("/pause", None),
            ("/resume", None),
            ("/stop", None),
            ("/seek", {"direction": 1}),
        ]:
            resp = client.post(path, json=body, headers=headers)
            # /seek can return 400 when no active article exists; auth passed.
            assert resp.status_code != 401, f"{path} rejected paired extension"

        assert client.get("/settings", headers=headers).status_code == 401
        assert client.get("/engines", headers=headers).status_code == 401
    finally:
        try:
            backend_mod.storage.save_config(old_config)
        except Exception:
            pass
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_engines_endpoints_require_management_token():
    """/engines 与 /engines/check 受管理令牌保护（含密钥，AppKit 独占）。"""
    os.environ["TTS_MANAGEMENT_TOKEN"] = "eng-token"
    init_runtime_services()
    try:
        client = TestClient(app)
        # GET /engines 无 token → 401，正确 token → 200
        assert client.get("/engines").status_code == 401
        assert client.get("/engines", headers={"x-management-token": "eng-token"}).status_code == 200
        # POST /engines/check 无 token → 401
        assert client.post("/engines/check", json={"family": "llm", "provider": "gemini"}).status_code == 401
        # PATCH /engines 无 token → 401
        assert client.patch("/engines", json={}).status_code == 401
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_read_url_ssrf_guard():
    """validate_fetch_url 应拒绝非 http/https 与内网/保留地址，放行公网。"""
    from core.backend import validate_fetch_url
    # 拒绝（本地/字面地址，无需外网 DNS）
    for bad in [
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://localhost:8001/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/",
        "http://10.0.0.5/",
        "",
    ]:
        assert validate_fetch_url(bad) is not None, f"应拒绝: {bad!r}"
    # 放行（公网字面 IP，getaddrinfo 不触发 DNS）
    assert validate_fetch_url("http://8.8.8.8/") is None


def test_legacy_loopback_client_bypasses_management_token():
    os.environ["TTS_MANAGEMENT_TOKEN"] = "native-client-token"
    os.environ["TTS_LEGACY_LOOPBACK_CLIENTS"] = "1"
    init_runtime_services()
    try:
        client = TestClient(app)
        response = client.post("/stop")
        assert response.status_code == 200
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)
        os.environ.pop("TTS_LEGACY_LOOPBACK_CLIENTS", None)


class DummyState:
    def __init__(self):
        self.stop_event = threading.Event()
        self.text_q = None
        self.audio_q = None


def _run_watchdog_scenario(monkeypatch, *, pre_write: bytes | None = None,
                           shutdown_raises: bool = False):
    """E7 F-d 公共骨架:建管道+supervisor,触发 EOF,返回 (supervisor, state, kills)。"""
    fd_read, fd_write = os.pipe()
    os.environ["TTS_WATCHDOG_FD"] = str(fd_read)
    os.environ["TTS_WATCHDOG_EXIT_PROCESS"] = "1"
    # 硬超时拉长到 60s:守护线程是 daemon,pytest 结束即消亡,不会真 os._exit
    os.environ["TTS_WATCHDOG_HARD_EXIT_SEC"] = "60"

    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: kills.append((pid, sig)))

    loop = asyncio.new_event_loop()
    try:
        state = DummyState()
        supervisor = RuntimeSupervisor(
            shared_state=state,
            player=None,
            playback_service=None,
            podcast_service=None,
            graceful_timeout=0.01,
            terminate_timeout=0.01,
        )
        if shutdown_raises:
            async def boom():
                raise RuntimeError("shutdown exploded")
            supervisor.shutdown = boom  # type: ignore[method-assign]

        supervisor.start_watchdog(loop)
        if pre_write is not None:
            os.write(fd_write, pre_write)  # F-c:数据字节不应让 watchdog 罢工
            time.sleep(0.05)
        os.close(fd_write)

        async def wait():
            await asyncio.sleep(0.2)

        loop.run_until_complete(wait())
        return supervisor, state, kills
    finally:
        os.environ.pop("TTS_WATCHDOG_FD", None)
        os.environ.pop("TTS_WATCHDOG_EXIT_PROCESS", None)
        os.environ.pop("TTS_WATCHDOG_HARD_EXIT_SEC", None)
        try:
            os.close(fd_read)
        except OSError:
            pass
        loop.close()


def test_watchdog_suicide_fires_on_eof(monkeypatch):
    """E7 F-a:EXIT_PROCESS=1 时 EOF → SIGTERM 自杀必发。"""
    import signal as signal_module

    _, state, kills = _run_watchdog_scenario(monkeypatch)
    assert state.stop_event.is_set()
    assert (os.getpid(), signal_module.SIGTERM) in kills


def test_watchdog_suicide_fires_even_if_shutdown_raises(monkeypatch):
    """E7 F-a 核心:shutdown() 抛异常,自杀仍必发(finally)——否则主进程成孤儿。"""
    import signal as signal_module

    _, _, kills = _run_watchdog_scenario(monkeypatch, shutdown_raises=True)
    assert (os.getpid(), signal_module.SIGTERM) in kills


def test_watchdog_survives_data_byte_then_eof(monkeypatch):
    """E7 F-c:先读到 1 个数据字节再 EOF → 仍触发 shutdown(不静默罢工)。"""
    supervisor, state, kills = _run_watchdog_scenario(monkeypatch, pre_write=b"x")
    assert supervisor.accepting_requests is False
    assert state.stop_event.is_set()
    assert kills, "EOF 后应发出自杀信号"


def test_watchdog_eof_triggers_shutdown():
    fd_read, fd_write = os.pipe()
    os.environ["TTS_WATCHDOG_FD"] = str(fd_read)

    try:
        loop = asyncio.new_event_loop()
        state = DummyState()
        supervisor = RuntimeSupervisor(
            shared_state=state,
            player=None,
            playback_service=None,
            podcast_service=None,
            graceful_timeout=0.01,
            terminate_timeout=0.01,
        )

        supervisor.start_watchdog(loop)
        
        # 关闭写端产生 EOF
        os.close(fd_write)

        # 运行 loop 允许 run_coroutine_threadsafe 的任务执行
        async def wait_and_stop():
            await asyncio.sleep(0.1)

        loop.run_until_complete(wait_and_stop())

        # 验证是否成功触发了 shutdown 并更新了状态
        assert supervisor.accepting_requests is False
        assert state.stop_event.is_set()

    finally:
        os.environ.pop("TTS_WATCHDOG_FD", None)
        try:
            os.close(fd_read)
        except OSError:
            pass
        loop.close()
