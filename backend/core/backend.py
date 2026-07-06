import os
import sys
import json
import time
from typing import List, Dict, Any
import threading
import multiprocessing as mp
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
import uvicorn
import signal
import uuid
import hmac

# Disable Hugging Face / transformers telemetry so the app makes zero analytics
# calls of any kind (backs the "no telemetry" promise). Set before those libs load.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# 确保能找到 core 目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core import labels
from core.hashing import text_md5
from core import reader_bridge
from core.paths import runtime_paths

PODCASTS_DIR = runtime_paths.podcasts_path
CACHE_DIR = runtime_paths.cache_path
PODCAST_CHUNK_DIR = os.path.join(runtime_paths.app_support_path, "PodcastChunks")
os.makedirs(PODCAST_CHUNK_DIR, exist_ok=True)
RUNTIME_EVENTS_FILE = os.path.join(runtime_paths.data_path, "runtime_events.jsonl")
PODCAST_JOBS_FILE = os.path.join(runtime_paths.data_path, "podcast_jobs.json")
URL_JOBS_FILE = os.path.join(runtime_paths.data_path, "url_jobs.json")

from core.api_models import (
    DeleteSavedRequest,
    FilenameRequest,
    GenerateSinglePodcastRequest,
    Md5Request,
    PlaySavedRequest,
    ReadRequest,
    ReadUrlRequest,
    SaveForLaterRequest,
    SeekRequest,
    SettingsUpdateRequest,
    UpdateTitleRequest,
    RenamePodcastRequest,
)
from core.player import PCMPlayer
from core.processor import TextProcessor
from core.storage import Storage
from core.state.runtime_state import RuntimeState
from core.state.shared_state import SharedState
from core.state.article_store import ArticleStore
from core.services.playback_service import PlaybackService
from core.services.read_orchestrator import ReadOrchestrator
from core.services.podcast_service import PodcastService
from core.services.performance import get_performance_profile
from core.services.saved_items_service import SavedItemsService
from core.services.settings_service import SettingsService
from core.services.cache_service import CacheService
from core.services.runtime_log import RuntimeEventLog
from core.services.runtime_supervisor import RuntimeSupervisor
from core.services.url_jobs import UrlJobStore


# 终极同步信号：必须是字符串，确保跨进程一致
GLOBAL_SENTINEL = "PIPELINE_END_STRICT_V1"
INSTANCE_ID = str(uuid.uuid4())

# When the app launches us with TTS_BACKEND_PORT=0 it wants an OS-assigned
# ephemeral port (eliminating the app-side pick-then-bind TOCTOU). We bind the
# socket ourselves in __main__, record the real port here, and publish it in
# runtime.json so the app/extension can discover it. None means a fixed port.
BOUND_PORT: int | None = None

# Discovery descriptor: published on startup so the native app and the browser
# extension can find the dynamically-chosen port without relying on a fixed
# 8001. Lives at the App Support root for easy discovery; removed on shutdown.
RUNTIME_DESCRIPTOR_FILE = os.path.join(runtime_paths.app_support_path, "runtime.json")


def write_runtime_descriptor() -> None:
    try:
        descriptor = {
            "port": BOUND_PORT if BOUND_PORT is not None else int(os.environ.get("TTS_BACKEND_PORT", 8001)),
            "host": os.environ.get("TTS_BACKEND_HOST", "127.0.0.1"),
            "pid": os.getpid(),
            "instance_id": INSTANCE_ID,
            "managed": os.environ.get("TTS_WATCHDOG_FD") is not None,
        }
        tmp_path = RUNTIME_DESCRIPTOR_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(descriptor, f)
        os.replace(tmp_path, RUNTIME_DESCRIPTOR_FILE)
        print(f"[Backend] Runtime descriptor published: {RUNTIME_DESCRIPTOR_FILE} (port={descriptor['port']})")
    except Exception as e:
        print(f"[Backend] Failed to write runtime descriptor: {e}")


def remove_runtime_descriptor() -> None:
    try:
        if os.path.exists(RUNTIME_DESCRIPTOR_FILE):
            os.remove(RUNTIME_DESCRIPTOR_FILE)
    except Exception:
        pass


def get_text_hash(text):
    return text_md5(text)

# Number of most-recent cache entries to keep (single source; was hardcoded
# as 10 in two places). The DB `created_at` order is authoritative for eviction.
# #8 复用哲学:"Cache 里有就不烧算力"。原值 10 装不下一篇文章(14+ 句),
# 听到第 11 句时第 1 句已被挤掉,"听过的文章重播"必然从头重烧(2026-07-01
# 用户实测抓获)。默认 300 句(≈几百 MB npy,R5 的命中 touch 让常听的存活);
# 可用 config.json 的 cache_max_items 覆盖。
CACHE_MAX_ITEMS = 300

# Cache eviction now lives in InferenceEngine.evict_cache (core/inference/engine.py),
# which owns the read-through audio cache. The old module-level manage_cache_limit
# was removed with the inference_worker rewrite (ADR-001).

# 跨进程共享状态本体在 core/state/shared_state.py(C3.1 搬家);此处 re-export
# 供旧引用(core.backend.SharedState)过渡一个周期。
# ==========================================
# 2. 推理子进程
# ==========================================
def inference_worker(shared_state):
    """Inference process entry. Constructs the InferenceEngine (over MLXBackend)
    from runtime paths and hands control to its run_loop, which owns the read
    lane (text_q→audio_q, protocol unchanged) and the podcast lane (podcast_q→
    chunk files). See ADR-001 in CONTEXT.md. Cache, normalization, model
    switching, idle-unload and the 串音 cache-key fix all live in the engine."""
    from core.inference.engine import InferenceEngine
    from core.inference.model_backend import MLXBackend

    def handle_signal(sig, frame):
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_signal)

    # Worker metadata must live in Application Support (bundle is read-only).
    worker_storage = Storage(data_dir=runtime_paths.data_path)
    backend = MLXBackend(mlx_audio_path=runtime_paths.mlx_audio_path)
    try:
        cache_max = int(worker_storage.load_config().get("cache_max_items", CACHE_MAX_ITEMS))
    except Exception:
        cache_max = CACHE_MAX_ITEMS
    engine = InferenceEngine(
        backend=backend,
        cache_dir=CACHE_DIR,
        storage=worker_storage,
        reference_base=runtime_paths.reference_path,
        max_cache_items=cache_max,
        models_path=runtime_paths.models_path,
    )
    engine.run_loop(shared_state, sentinel=GLOBAL_SENTINEL, profile_fn=get_performance_profile)

# ==========================================
# 3. 主进程逻辑
# ==========================================
S: SharedState | None = None
storage: Storage | None = None
player: PCMPlayer | None = None
processor: TextProcessor | None = None
runtime_state: RuntimeState | None = None
saved_items_service: SavedItemsService | None = None
cache_service: CacheService | None = None
event_log: RuntimeEventLog | None = None
url_job_store: UrlJobStore | None = None
article_store: ArticleStore | None = None
playback_service: PlaybackService | None = None
settings_service: SettingsService | None = None

# Seek 预缓冲常量已随 seek 编排迁入 read_orchestrator.SEEK_PREBUFFER_FRAMES(M2)。
# Normal /read start-of-playback prebuffer. 1 = lowest first-sound latency (first
# ~0.1s frame), but the thinnest cushion against generation jitter / underrun.
# TTS_PREBUFFER_FRAMES raises it for the underrun A/B (each extra frame ≈ 0.5s of
# cushion at the cost of ~0.5s more first-sound latency). Default 1 = current behavior.
try:
    READ_PREBUFFER_FRAMES = max(1, int(os.environ.get("TTS_PREBUFFER_FRAMES", "1")))
except ValueError:
    READ_PREBUFFER_FRAMES = 1
podcast_service: PodcastService | None = None
orchestrator: ReadOrchestrator | None = None
runtime_supervisor: RuntimeSupervisor | None = None
ACTIVE_URL_TASKS: dict[str, dict] = {}


def init_runtime_services() -> None:
    global S
    global storage
    global player
    global processor
    global runtime_state
    global saved_items_service
    global cache_service
    global event_log
    global url_job_store
    global article_store
    global playback_service
    global settings_service
    global podcast_service
    global orchestrator
    global runtime_supervisor

    if S is not None:
        return

    # Run explicit startup-only path setup (legacy data migration) before any
    # service touches the runtime dirs. Path resolution itself happens at import.
    runtime_paths.init()

    S = SharedState()
    storage = Storage()
    settings_service = SettingsService(storage)
    player = PCMPlayer(sample_rate=24000)
    player.SENTINEL = GLOBAL_SENTINEL
    processor = TextProcessor()
    runtime_state = RuntimeState()
    saved_items_service = SavedItemsService()

    cache_service = CacheService(storage, CACHE_DIR, PODCASTS_DIR)
    event_log = RuntimeEventLog(RUNTIME_EVENTS_FILE)
    url_job_store = UrlJobStore(URL_JOBS_FILE)
    article_store = ArticleStore(storage)
    url_job_store.mark_unfinished_failed("backend restarted before URL job completed")

    playback_service = PlaybackService(
        shared_state=S,
        player=player,
        storage=storage,
        runtime_state=runtime_state,
        sentinel=GLOBAL_SENTINEL,
        get_text_hash=get_text_hash,
        get_performance_profile=get_performance_profile,
        event_log=event_log,
    )

    podcast_service = PodcastService(
        podcasts_dir=PODCASTS_DIR,
        podcast_chunk_dir=PODCAST_CHUNK_DIR,
        runtime_state=runtime_state,
        active_url_tasks=ACTIVE_URL_TASKS,
        jobs_file=PODCAST_JOBS_FILE,
        event_log=event_log,
        # E4:改从 player 现算(≙ playback_status ∈ {playing, generating}),
        # 不再读已删除的 main_is_playing 存储标志
        # C4.3:读侧一律经 PlaybackService,不越级摸 player 内部
        is_frontend_active=lambda: playback_service.playback_status() in ("playing", "generating"),
        is_device_switching=lambda: playback_service.device_switching(),
        get_battery_policy=lambda: storage.load_config().get("battery_podcast_policy", "pause"),
        podcast_q=S.podcast_q,
        podcast_cancel_epoch=S.podcast_cancel_epoch,
    )
    # M1(计划 #13):启动对账——上次崩溃残留的活任务落 failed(镜像上面
    # url_job_store 的同款调用),pending 伪行由 job store 推导后幽灵不再可能;
    # 顺手清升级前遗留的 .pending_* 磁盘哨兵(新代码不再产生)。
    podcast_service.mark_orphans_failed("backend restarted before podcast job completed")
    podcast_service.cleanup_pending_files()

    runtime_supervisor = RuntimeSupervisor(
        shared_state=S,
        player=player,
        playback_service=playback_service,
        podcast_service=podcast_service,
        url_job_store=url_job_store,
        active_url_tasks=ACTIVE_URL_TASKS,
        event_log=event_log,
    )

    orchestrator = ReadOrchestrator(
        playback_service=playback_service,
        # 晚绑定:测试会整体替换全局 podcast_service,orchestrator 须看到当前值
        podcast_service=lambda: podcast_service,
        storage=storage,
        settings=settings_service,
        runtime_state=runtime_state,
        shared_state=S,
        processor=processor,
        event_log=event_log,
        saved_items_service=saved_items_service,
        url_job_store=url_job_store,
        active_url_tasks=ACTIVE_URL_TASKS,
        read_prebuffer_frames=READ_PREBUFFER_FRAMES,
        # 晚绑定同理:测试会 monkeypatch runtime_supervisor.create_task
        create_task=lambda coro, job_id=None: runtime_supervisor.create_task(coro, job_id=job_id),
    )

def performance_monitor_thread(shutdown_event: threading.Event):
    if S is None or playback_service is None or runtime_state is None:
        return
    import psutil
    process = psutil.Process(os.getpid())
    print("[Monitor] 性能监控就绪")
    last_status = "IDLE"
    while not shutdown_event.is_set():
        try:
            st = S.get_status()
            # E4:诊断显示改从 player 现算(缓存回放不经引擎,引擎 IDLE 但确在出声)
            if playback_service.is_active() and st == "IDLE": st = "PLAYING"
            
            if st == "IDLE" and last_status != "IDLE":
                print(f"--- [DIAGNOSE] 任务已结束 (ID: {S.current_task_id.value}) ---\n")
            
            last_status = st
            if st == "IDLE":
                shutdown_event.wait(2)
                continue

            cpu = process.cpu_percent(interval=None) 
            log_msg = (
                f"--- [DIAGNOSE] ---\n"
                f"Task ID: {S.current_task_id.value} | Status: {st}\n"
                f"CPU: {cpu}% | VRAM: {S.vram_mb.value:.1f}MB\n"
                f"Buffer: {playback_service.queue_depth() * (2048/24000):.1f}s\n"
                f"------------------\n"
            )
            print(log_msg)
            shutdown_event.wait(5)
        except Exception:
            shutdown_event.wait(5)

# audio_feeder_thread moved into PlaybackService.feed_audio_loop (ADR-002):
# the inference audio_q now feeds only the player; the old podcast-buffer
# routing was dead after ADR-001.

@asynccontextmanager
async def lifespan(app: FastAPI):
    import shutil
    configured_ffmpeg = runtime_paths.ffmpeg_path
    if not (configured_ffmpeg and os.path.isfile(configured_ffmpeg)) and shutil.which("ffmpeg") is None:
        print("\n" + "="*80)
        print("[Warning] 系统中未检测到 ffmpeg 命令行工具！播客音频合成功能可能无法正常运作。")
        print("请确保已通过 brew install ffmpeg 安装，并将其添加至系统的 PATH 中。")
        print("="*80 + "\n")

    # 启动清扫(方案 A):PodcastChunks 只存播客生成过程的中间碎片(chunk_*.npy)。
    # 成品播客(.wav)在 Podcasts/、收藏在 Data/,都不在此目录、不受影响。上次运行遗留的
    # 碎片(中断/取消/崩溃,成功的任务已在收尾时自清)一律清掉——不跨重启续跑。
    try:
        if os.path.isdir(PODCAST_CHUNK_DIR):
            for _name in os.listdir(PODCAST_CHUNK_DIR):
                shutil.rmtree(os.path.join(PODCAST_CHUNK_DIR, _name), ignore_errors=True)
    except Exception as _e:
        print(f"[Startup] PodcastChunks 清扫失败(忽略): {_e}")

    mp.set_start_method('spawn', force=True)
    try:
        init_runtime_services()
        if S is None or runtime_supervisor is None:
            raise RuntimeError("runtime shared state failed to initialize")
        runtime_supervisor.start_watchdog(asyncio.get_running_loop())
        runtime_supervisor.start_inference(inference_worker, (S,))
        runtime_supervisor.start_thread(playback_service.feed_audio_loop, name="audio-feeder")
        runtime_supervisor.start_thread(
            performance_monitor_thread,
            name="performance-monitor",
        )
        write_runtime_descriptor()
        yield
    finally:
        print("[Backend] lifespan 正在执行统一资源清理...")
        remove_runtime_descriptor()
        if runtime_supervisor is not None:
            await runtime_supervisor.shutdown()
        else:
            if podcast_service is not None:
                podcast_service.shutdown()
            if playback_service is not None:
                playback_service.close_player()

# docs/redoc/openapi disabled: the middleware would serve them token-free (they
# are GET and not state-changing), leaking the API surface. The app never uses them.
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

from fastapi import Request
from fastapi.responses import JSONResponse

@app.middleware("http")
async def management_token_middleware(request: Request, call_next) -> Any:
    if request.method == "OPTIONS":
        return await call_next(request)

    token: str | None = os.environ.get("TTS_MANAGEMENT_TOKEN")
    path: str = request.url.path

    # Compatibility is opt-in and restricted to the loopback interface.  It is
    # used only when app.py owns the backend so the existing extension can keep
    # working while the authenticated native client is developed separately.
    # SECURITY: 该开关一旦开启会对所有匹配主机的请求完全绕过鉴权（含 /control、
    # /settings），仅应在 app.py 自管后端时启用。"testclient" 是 FastAPI TestClient
    # 的伪主机，仅在 pytest 运行时放行，避免生产中被伪造该 host 绕过鉴权。
    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    if "pytest" in sys.modules:
        loopback_hosts.add("testclient")
    legacy_loopback_clients = (
        os.environ.get("TTS_LEGACY_LOOPBACK_CLIENTS") == "1"
        and request.client is not None
        and request.client.host in loopback_hosts
    )
    if legacy_loopback_clients:
        return await call_next(request)
    
    x_token: str | None = request.headers.get("x-management-token")
    x_ext_token: str | None = request.headers.get("x-extension-token")
    has_mgmt: bool = bool(token) and hmac.compare_digest(x_token or "", token)
    method: str = request.method

    def deny(detail: str) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": detail})

    # --- 0. 公开只读端点（无需任何令牌）：仅被轮询的非敏感读接口 ---
    PUBLIC_GET = {"/health", "/snapshot", "/status"}
    if method == "GET" and path in PUBLIC_GET:
        return await call_next(request)

    # --- 1. 管理端独占接口 (AppKit 专用；含控制/可暴露密钥的配置) ---
    #     控制类、/settings(读写)、/engines*(含密钥) 一律需管理令牌。
    #     /stop 是播放控制，不是后端生命周期控制；已配对浏览器扩展也需要它。
    #     未设管理令牌时（开发态）放行，保持本地开发可用。
    is_mgmt_only: bool = (
        path.startswith("/control/")
        or path == "/settings"
        or path.startswith("/engines")
    )
    if is_mgmt_only:
        if token and not has_mgmt:
            return deny("Unauthorized: invalid management token")
        return await call_next(request)

    # --- 2. 其余“改变状态”的请求一律默认拒绝：需管理令牌或扩展配对令牌 ---
    #     默认拒绝（而非默认放行）——此前 /seek /pause /resume /restart_audio
    #     未列入任何名单而被无鉴权放行，可被本地/局域网客户端劫持播放。
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        if has_mgmt:
            return await call_next(request)
        config: Dict[str, Any] = storage.load_config() if storage else {}
        pairing_token: str | None = config.get("extension_pairing_token")
        if pairing_token and hmac.compare_digest(x_ext_token or "", pairing_token):
            return await call_next(request)
        return deny("Unauthorized: invalid extension token or pairing required")

    # --- 3. 其余只读 GET（saved_items / cache / podcasts 列表等内容读取）放行 ---
    return await call_next(request)



@app.post("/read")
async def read_text(data: ReadRequest):
    """朗读入口:校验/编排全在 ReadOrchestrator(#10 C1),路由只转发。"""
    return await orchestrator.read(data)


@app.post("/selftest/voice")
async def selftest_voice():
    """首启向导「一键试音」：朗读固定短句，并**等待真实结果**后返回。

    成功条件是真的产生了音频帧（audio_frames>0），而不是 /read 受理成功——后者在
    模型缺失/加载失败时同样返回 200，会造成"听不到声音却判成功"。失败时回传 inference
    worker 的真实错误文本，便于向导按错误类型给出下一步。
    """
    import asyncio

    if playback_service is None or S is None:
        raise HTTPException(status_code=503, detail="后端尚未就绪")

    # 走与 /read 完全一致的链路（含 reset_run_signals 清零计数/错误）。
    await orchestrator.read(ReadRequest(text="你好，欢迎使用 QwenTTS。"))

    # 轮询真实信号：出声即成功；出现推理错误即失败；超时按"未出声"失败。
    deadline = time.time() + 40
    while time.time() < deadline:
        err = S.get_error()
        if err:
            return {"ok": False, "error": err}
        if S.audio_frames.value > 0:
            return {"ok": True, "frames": S.audio_frames.value}
        await asyncio.sleep(0.3)
    return {"ok": False, "error": "未在 40 秒内产生音频（模型加载过慢或失败、或音频输出不可用）"}

@app.post("/stop")
def stop_read():
    """停止键=只停**播放**。M smoke(2026-07-01)用户实测拍板:此前这里连带
    cancel_all() 把后台正在生成的播客也掐死——"生成播客过程中按停止键,后台
    生成被掐死"是错误语义。取消播客走专门的 /podcasts/cancel_all。"""
    event_log.record("stop_requested")
    runtime_state.clear_current_media()

    playback_service.stop_current_session()
    S.set_status("IDLE")

    return {"status": "ok", "playback_status": playback_service.playback_status()}


@app.post("/podcasts/cancel_all")
def cancel_all_podcasts():
    """取消全部后台播客生成(排队+进行中)。原来混在 /stop 里,现独立成口。"""
    event_log.record("podcast_cancel_all_requested")
    podcast_service.cancel_all()
    podcast_service.cleanup_pending_files()
    return {"status": "ok"}

@app.get("/status")
def get_status():
    if runtime_state is None or player is None or S is None:
        return {
            "is_playing": False,
            "is_paused": False,
            "current_podcast_file": None,
            "current_playing_md5": None,
            "title": "",
            "progress": "",
            "buffer_sec": 0,
            "status_code": "STARTING",
            "generating_title": "",
        }
    runtime_snapshot = runtime_state.snapshot()
    # C2.3:pending 文件名文法归 podcast_naming 拥有,web 层不再 listdir 猜格式
    generating_title = podcast_service.generating_title() if podcast_service else ""

    status_code = S.get_status()
    if generating_title and status_code == "IDLE":
        status_code = "BUSY"

    # ADR-003 C1: derive the wire aliases from the single computed truth (same
    # owner as /snapshot → the two endpoints can't disagree).
    pb_status = playback_service.playback_status() if playback_service is not None else "idle"
    return {
        "playback_status": pb_status,
        "is_playing": pb_status in ("playing", "generating"),
        "is_paused": pb_status == "paused",
        "current_podcast_file": runtime_snapshot["current_podcast_file"],
        "current_playing_md5": runtime_snapshot["current_playing_md5"],
        "title": runtime_snapshot["main_title"],
        "progress": runtime_snapshot["main_progress"],
        "buffer_sec": playback_service.queue_duration(),
        "status_code": status_code,
        "generating_title": generating_title
    }

@app.get("/debug/state")
def debug_state():
    runtime_snapshot = runtime_state.snapshot()
    return {
        **playback_service.snapshot(),
        "status_code": S.get_status(),
        **runtime_snapshot,
        **podcast_service.snapshot(),
        "active_url_tasks": list(ACTIVE_URL_TASKS.keys()),
    }

@app.get("/debug/events")
def debug_events(limit: int = 50):
    return event_log.recent(limit=limit)

@app.post("/pause")
def pause_playback():
    playback_service.pause()
    # ADR-003 A3: return the new authoritative status so the UI applies it
    # optimistically instead of waiting for the next ~500ms poll.
    return {"status": "paused", "playback_status": playback_service.playback_status()}

@app.post("/resume")
def resume_playback():
    playback_service.resume()
    return {"status": "resumed", "playback_status": playback_service.playback_status()}

@app.post("/restart_audio")
def restart_audio():
    if player is not None:
        try:
            playback_service.restart_device()
            return {"status": "ok"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=503, detail="Player not initialized")

@app.post("/seek")
def seek_playback(data: SeekRequest):
    # M2(计划 #13):编排(索引钳制、wav-vs-TTS 决策、SEEK 预缓冲)归 orchestrator。
    return orchestrator.seek(data.direction)

import asyncio

def validate_fetch_url(raw: str) -> str | None:
    """SSRF 防护：校验将要抓取的 URL。不安全时返回中文错误信息，安全返回 None。

    - scheme 仅允许 http/https（拒绝 file://、gopher:// 等）。
    - 解析主机的所有地址，若任一落在内网/环回/链路本地/保留/多播段则拒绝
      （拦截 127.0.0.1、localhost、169.254.169.254 云元数据、192.168/10/172.16 等）。
    注：解析在抓取前完成，存在 DNS rebinding 的残留 TOCTOU（抓取时再次解析可能变化）；
    彻底消除需在抓取层固定已校验 IP，留作后续增强。
    """
    import ipaddress
    import socket as _socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(raw)
    except Exception:
        return "URL 解析失败"
    if parsed.scheme not in ("http", "https"):
        return f"仅支持 http/https，已拒绝 scheme: {parsed.scheme or '(空)'}"
    host = parsed.hostname
    if not host:
        return "URL 缺少主机名"
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = _socket.getaddrinfo(host, port, proto=_socket.IPPROTO_TCP)
    except Exception as e:
        return f"无法解析主机 {host}: {e}"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return f"非法解析地址: {ip_str}"
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return f"拒绝访问内网/保留地址: {host} -> {ip_str}"
    return None


@app.post("/read_url")
async def read_url(payload: ReadUrlRequest) -> dict:
    """URL 阅读入口:路由只做空值 + SSRF 校验,编排在 ReadOrchestrator(#10 C1.3)。"""
    url = payload.url.strip()
    if not url: raise HTTPException(status_code=400, detail="Empty URL")

    # SSRF 防护：拒绝非 http/https 及指向内网/保留地址的 URL（DNS 解析阻塞，放线程池）
    from fastapi.concurrency import run_in_threadpool
    url_err = await run_in_threadpool(validate_fetch_url, url)
    if url_err:
        event_log.record("read_url_rejected", url=url, reason=url_err)
        raise HTTPException(status_code=400, detail=url_err)

    return await orchestrator.read_url(payload)

@app.get("/url_jobs")
def list_url_jobs():
    return url_job_store.list()

@app.post("/delete_saved")
def delete_saved(data: DeleteSavedRequest):
    md5 = data.md5
    index = data.index
    if saved_items_service.delete(md5=md5, index=index):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Item not found")

@app.post("/saved/toggle_pin")
def toggle_saved_pin(data: dict):
    """ADR-003 F4: pin/unpin a saved item by md5 (storage order unchanged)."""
    md5 = data.get("md5")
    if not md5:
        raise HTTPException(status_code=400, detail="md5 required")
    if saved_items_service.toggle_pin(md5):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Item not found")

@app.get("/podcasts/list")
def list_podcasts():
    return podcast_service.list_files()

@app.get("/podcasts/jobs")
def list_podcast_jobs():
    return podcast_service.list_jobs()

@app.post("/podcasts/toggle_pin")
def toggle_pin(data: FilenameRequest):
    return podcast_service.toggle_pin(data.filename)

@app.post("/podcasts/clear")
def clear_podcasts():
    # P0-2 审计:这是"删光所有未置顶成品"的重操作(扩展的清空按钮在调),必须留痕
    event_log.record("podcasts_clear_requested")
    deleted_count = podcast_service.clear_unpinned()
    return {"status": "ok", "deleted_count": deleted_count}

@app.post("/podcasts/delete")
def delete_podcast(data: FilenameRequest):
    return podcast_service.delete(data.filename)

@app.post("/podcasts/play")
def play_podcast(data: FilenameRequest):
    filename = data.filename
    filepath = podcast_service.find_file(filename)
    if not filepath:
        raise HTTPException(status_code=404, detail="File not found")
    
    event_log.record("podcast_play_requested", filename=filename, filepath=filepath)
    playback_service.play_wav_file(filepath, filename)
    return {"status": "ok"}

@app.get("/podcasts/transcript")
def get_podcast_transcript(filename: str):
    """返回播客同名 .txt 文稿（生成时写入的 sidecar）。"""
    filepath = podcast_service.find_file(filename) if podcast_service else None
    if not filepath:
        return {"text": ""}
    txt_path = (filepath[:-4] if filepath.endswith(".wav") else filepath) + ".txt"
    try:
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as f:
                return {"text": f.read()}
    except Exception:
        pass
    return {"text": ""}

@app.post("/save_for_later")
async def save_for_later(data: SaveForLaterRequest):
    return await orchestrator.save_for_later(data)


@app.post("/saved_items/update_title")
async def update_saved_item_title(data: UpdateTitleRequest):
    runtime_state.touch_activity()
    title = data.title.strip()
    if not title: raise HTTPException(status_code=400, detail="Empty title")
    if saved_items_service.update_title(title, md5=data.md5, index=data.index):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Item not found")

@app.post("/podcasts/rename")
async def rename_podcast(data: RenamePodcastRequest):
    runtime_state.touch_activity()
    new_title = data.new_title.strip()
    if not new_title: raise HTTPException(status_code=400, detail="Empty title")
    if podcast_service and podcast_service.rename_file(data.filename, new_title):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Item not found")

@app.post("/generate_single_podcast")
async def generate_single_podcast(data: GenerateSinglePodcastRequest):
    """单篇播客入口:编排(409/exists/LLM 门/落 job)全在 ReadOrchestrator(#10 C1.4)。"""
    return await orchestrator.generate_single_podcast(data)


@app.post("/saved_items/clear")
def clear_saved_items():
    saved_items_service.clear()
    return {"status": "ok"}

@app.post("/generate_podcast")
def generate_podcast_api():
    # M2(计划 #13):合集编排归 orchestrator(与单篇 generate_single_podcast 对称)。
    return orchestrator.generate_batch_podcast()

@app.get("/saved_items")
def get_saved_items():
    saved_items = saved_items_service.load()
    
    # M4-③:伪行过滤规则(60s 窗口/类型分流/文案)收在 labels,两个列表共用
    for url, info in labels.pending_url_tasks(ACTIVE_URL_TASKS, podcast=False):
        saved_items.insert(0, {
            "timestamp": info["timestamp"],
            "text": url,
            "title": labels.PENDING_FETCH_TITLE,
            "source": "web",
            "is_exported": False,
            "is_pending": True
        })
    # ADR-003 F4: expose pin state as is_pinned (back-compat: absent → False).
    # Order is preserved; the frontend sorts pinned-first for DISPLAY only.
    # #11 N2:展示三件套(干净标题/来源标签/模式标签)由后端统一给出,
    # App 与扩展只渲染不加工(词表单一真相在 core/labels.py)。
    for it in saved_items:
        it["is_pinned"] = bool(it.get("pinned", False))
        it["display_title"] = labels.clean_display_title(it.get("title"), it.get("text", ""))
        it["source_label"] = labels.source_label(it.get("source"))
        it["mode_label"] = labels.mode_label(
            it.get("content_mode")
            or labels.infer_mode_from_legacy_prefix(it.get("title"))
            or it.get("mode")
        )
    return saved_items

@app.post("/play_saved")
async def play_saved(data: PlaySavedRequest):
    indices = data.indices
    if not indices: raise HTTPException(status_code=400, detail="No items selected")
    saved_items = saved_items_service.load()
    if not saved_items: raise HTTPException(status_code=400, detail="Queue empty")
    text_to_play, voice, selected_md5 = saved_items_service.selected_text(indices)
    if not text_to_play.strip(): raise HTTPException(status_code=400, detail="Selected items are empty")
    runtime_state.set_current_media(podcast=None, md5=selected_md5)

    payload = ReadRequest(text=text_to_play, from_saved=True, voice=voice)

    return await orchestrator.read(payload)



@app.get("/cache/items")
def get_cache_items():
    items = cache_service.list_items()
    # #11 N2:展示三件套。缓存无标题,干净标题=正文首行;来源标签兜底
    # voice/model(原 App cacheSourceLabel 的回退规则收编到后端)。
    for it in items:
        it["display_title"] = labels.clean_display_title(None, it.get("text", ""))
        it["source_label"] = labels.source_label(it.get("source")) or (
            it.get("voice") or it.get("model") or "缓存"
        )
    return items

@app.post("/cache/play")
async def play_cache(data: Md5Request):
    md5 = data.md5
    text = cache_service.get_text(md5)
    if text is None: raise HTTPException(status_code=404, detail="Cache not found")
    return await orchestrator.read(ReadRequest(text=text))

@app.post("/cache/export")
async def export_cache(data: Md5Request):
    md5 = data.md5
    text = cache_service.get_text(md5)
    if text is None: raise HTTPException(status_code=404, detail="Cache not found")
    return await orchestrator.generate_single_podcast(
        GenerateSinglePodcastRequest(text=text, source="cache")
    )

@app.post("/cache/delete")
def delete_cache(data: Md5Request):
    cache_service.delete(data.md5)
    return {"status": "ok"}

@app.post("/cache/clear")
def clear_cache_endpoint():
    cache_service.clear()
    return {"status": "ok"}


@app.get("/health")
def get_health():
    return {
        "status": "ready",
        "instance_id": INSTANCE_ID,
        "pid": os.getpid(),
        "managed": os.environ.get("TTS_WATCHDOG_FD") is not None,
        "accepting_requests": runtime_supervisor.accepting_requests if runtime_supervisor else True
    }


@app.get("/snapshot")
def get_snapshot():
    runtime_snapshot = runtime_state.snapshot() if runtime_state else {}
    playback_snap = playback_service.snapshot() if playback_service else {}
    podcast_snap = podcast_service.snapshot() if podcast_service else {}
    
    # C3.4:文章视图由单一拥有者 ArticleStore 合成(实时索引仍以 player 为权威,
    # 经 PlaybackService.playing_index() 传入;/snapshot 保持只读不写 state.json)。
    view = article_store.view(
        live_index=playback_service.playing_index() if playback_service is not None else None
    ) if article_store is not None else {"chunks_clean": [], "current_index": 0, "progress_override": None}
    chunks_clean = view["chunks_clean"]
    current_index = view["current_index"]
    if view["progress_override"] is not None:
        runtime_snapshot["main_progress"] = view["progress_override"]

    # ADR-003 C1: playback_status is the single computed truth. The legacy wire
    # aliases is_playing/is_paused/main_is_playing are now DERIVED FROM it (was:
    # is_playing from the racy stored main_is_playing flag), so they can never
    # contradict the truth. Kept on the wire because the Chrome extension reads
    # is_playing/is_paused/status_code.
    pb_status = playback_service.playback_status() if playback_service is not None else "idle"
    pb_is_playing = pb_status in ("playing", "generating")
    pb_is_paused = pb_status == "paused"
    return {
        **playback_snap,
        "status_code": S.get_status() if S else "IDLE",
        **runtime_snapshot,
        **podcast_snap,
        "active_url_tasks": list(ACTIVE_URL_TASKS.keys()),
        "instance_id": INSTANCE_ID,
        "playback_status": pb_status,
        "is_paused": pb_is_paused,
        "is_playing": pb_is_playing,
        # main_is_playing wire alias derived from the truth. E4: the stored flag
        # is physically gone (runtime_state no longer carries it) — this endpoint
        # is the ONLY producer of the field. See CONTEXT.md §4i.
        "main_is_playing": pb_is_playing or pb_is_paused,
        "current_article_chunks": chunks_clean,
        "current_article_index": current_index,
        # 真实出声/失败信号：audio_frames>0 表示本次确有音频产出；inference_error 非空表示
        # 推理失败（如模型缺失）。供向导一键试音与 UI 如实判定，避免假阳性。
        "audio_frames": (S.audio_frames.value if S else 0),
        "inference_error": ((S.get_error() if S else "") or None),
    }


@app.get("/settings")
def get_settings():
    # M5:配置语义归 SettingsService,路由只 validate→调→return
    if settings_service is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    return settings_service.get_settings()


@app.patch("/settings")
def patch_settings(update_data: SettingsUpdateRequest):
    if settings_service is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    config = settings_service.update_settings(update_data.model_dump())
    return {"status": "ok", "config": config}


def _default_engines() -> Dict[str, Any]:
    """Locked default engines schema — single source of truth in
    URL-Reader/engine_config.py (此前 backend.py 另存一份会与之漂移)。"""
    return reader_bridge.default_engines()


@app.get("/engines")
def get_engines():
    if settings_service is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    return settings_service.get_engines()


@app.patch("/engines")
def patch_engines(update: Dict[str, Any]):
    if settings_service is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    settings_service.update_engines(update)
    return {"status": "ok"}


@app.post("/engines/check")
async def check_engine(payload: Dict[str, Any]):
    """检测某个 provider 是否连通。可在 body 带 key/region 先持久化再探测。
    body: {family: 'llm'|'translate', provider: str, key?: str, region?: str}"""
    from fastapi.concurrency import run_in_threadpool

    family = (payload.get("family") or "").strip()
    provider = (payload.get("provider") or "").strip()
    key = payload.get("key")
    region = payload.get("region")
    if not family or not provider:
        return {"ok": False, "message": "缺少 family 或 provider"}

    # 若带了凭据，先写入 config（不改 selected），让引擎 provider 读到
    # (M5:凭证写入语义归 SettingsService,路由不再改嵌套 dict)
    if settings_service is not None:
        settings_service.store_engine_credential(family, provider, key, region=region)

    def _probe():
        try:
            return reader_bridge.probe(family, provider)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    ok, message = await run_in_threadpool(_probe)
    return {"ok": bool(ok), "message": message}


@app.post("/control/heartbeat")
def post_heartbeat():
    if runtime_state:
        runtime_state.touch_activity()
    return {"status": "ok"}


@app.post("/control/shutdown")
def post_shutdown():
    import signal
    
    def trigger_sigterm():
        time.sleep(0.1)
        if "pytest" not in sys.modules:
            os.kill(os.getpid(), signal.SIGTERM)
        else:
            print("[Backend] Pytest environment detected via sys.modules. Skipping self-kill.")
        
    threading.Thread(target=trigger_sigterm, daemon=True).start()
    return {"status": "shutting_down"}




if __name__ == "__main__":
    port = int(os.environ.get("TTS_BACKEND_PORT", 8001))
    host = os.environ.get("TTS_BACKEND_HOST", "127.0.0.1")
    if port == 0:
        # Ephemeral port: bind ourselves, capture the OS-assigned port, then let
        # uvicorn serve the pre-bound socket. runtime.json (written in lifespan
        # startup) publishes BOUND_PORT so the app/extension can discover it.
        import socket as _socket
        _sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        _sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        _sock.bind((host, 0))
        BOUND_PORT = _sock.getsockname()[1]
        print(f"[Backend] OS-assigned ephemeral port: {BOUND_PORT}")
        uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[_sock])
    else:
        uvicorn.run(app, host=host, port=port, log_level="error")
