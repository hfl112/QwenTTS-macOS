"""C2.2(计划 #10)——播客进度读写目录一致性回归测试。

历史 bug:job_id = f"single_{md5[:12]}_{uuid8}",worker 把 progress.json 写进
chunks/single_{md5[:12]}(无 uuid 后缀),而 list_jobs 按 chunks/{job_id} 读
→ 路径永远不存在 → progress_percent 从未出现,前端静默回退「生成中...」。
修法:job 落盘持久化 chunk_dir 字段(命名拥有者产出),读端优先用它。
"""

import json
import os
import tempfile

import core.services.podcast_service as podcast_service_module
from core.services import podcast_naming as pn
from core.services.podcast_service import PodcastService
from core.state.runtime_state import RuntimeState


MD5 = "0123456789abcdef0123456789abcdef"


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


def _fake_spawn_into(service, monkeypatch):
    def fake_spawn(pending):
        proc = _FakeProc()
        service.active_procs.append(proc)
        service.active_tasks[pending["md5"]] = proc
        service.active_job_ids[pending["md5"]] = pending["job_id"]

    monkeypatch.setattr(service, "_spawn", fake_spawn)


def _write_worker_progress(tmp: str, kind: str, md5: str, completed: int, total: int):
    """把 progress.json 写到 worker 实际写的位置(single_{md5:12}/batch_{md5:12})。"""
    worker_dir = os.path.join(tmp, "chunks", pn.chunk_dir_name(kind, md5))
    os.makedirs(worker_dir, exist_ok=True)
    with open(os.path.join(worker_dir, "progress.json"), "w", encoding="utf-8") as f:
        json.dump({"completed_chunks": completed, "total_chunks": total}, f)


def test_list_jobs_reads_progress_from_worker_chunk_dir(monkeypatch):
    """single:list_jobs 必须从 worker 实际目录读到进度(历史必红)。"""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(tmp)
        _fake_spawn_into(service, monkeypatch)
        service.start_single(
            text="早上好", config={"voice": "Serena"}, md5=MD5,
            source="web", title="T", mode="original", voice=None,
        )
        job_id = service.job_store.list()[0]["job_id"]
        service.job_store.update(job_id, status="running")
        _write_worker_progress(tmp, "single", MD5, completed=3, total=10)

        job = next(j for j in service.list_jobs() if j["job_id"] == job_id)
        assert job.get("progress_percent") == 30
        assert job.get("completed_chunks") == 3 and job.get("total_chunks") == 10


def test_list_jobs_reads_batch_progress(monkeypatch):
    """batch 口味同样命中(worker 用 md5(text) 重算,与 start_batch 的 md5 同源)。"""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(tmp)
        _fake_spawn_into(service, monkeypatch)
        service.start_batch(
            filename=os.path.join(tmp, "podcasts", "batch.wav"),
            text="合集文本", config={}, md5=MD5,
        )
        job_id = service.job_store.list()[0]["job_id"]
        service.job_store.update(job_id, status="running")
        _write_worker_progress(tmp, "batch", MD5, completed=1, total=4)

        job = next(j for j in service.list_jobs() if j["job_id"] == job_id)
        assert job.get("progress_percent") == 25


def test_list_jobs_old_records_without_chunk_dir_do_not_crash(monkeypatch):
    """旧 podcast_jobs.json(无 chunk_dir 字段)加载不炸、无进度但字段齐全。"""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(tmp)
        service.job_store.create(
            job_id="single_oldstyle_deadbeef", kind="single",
            md5=MD5, title="旧", source="web",
        )
        service.job_store.update("single_oldstyle_deadbeef", status="running")
        jobs = service.list_jobs()
        job = next(j for j in jobs if j["job_id"] == "single_oldstyle_deadbeef")
        assert "progress_percent" not in job  # 无目录可读,但不崩


def test_generating_title_owned_by_service_and_exposed_on_status():
    """M1(计划 #13):generating_title 真相 = job store 的 running 任务,
    不再扫磁盘哨兵文件(崩溃后哨兵残留曾致幽灵「生成中」)。"""
    import core.backend as backend_mod
    from fastapi.testclient import TestClient
    from core.backend import app, init_runtime_services

    init_runtime_services()
    client = TestClient(app)
    svc = backend_mod.podcast_service

    job_id = "single_m1title_ffffffff"
    svc.job_store.create(
        job_id=job_id, kind="single", md5=MD5, title="生成中标题", source="web"
    )
    try:
        # queued 还不算「正在生成」(与旧行为一致:哨兵只在 worker 起跑后出现)
        assert svc.generating_title() == ""
        svc.job_store.update(job_id, status="running")
        assert svc.generating_title() == "生成中标题"
        assert client.get("/status").json()["generating_title"] == "生成中标题"
    finally:
        svc.job_store.update(job_id, status="done")

    assert svc.generating_title() == ""


def test_pending_rows_derive_from_job_store_not_disk(monkeypatch):
    """M1:list_files 的 pending 伪行来自 job store 活任务;
    磁盘上的历史哨兵文件(升级前遗留)绝不能再渲染成「生成中」。"""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(tmp)
        os.makedirs(service.podcasts_dir, exist_ok=True)
        stale = os.path.join(
            service.podcasts_dir, pn.single_pending_name("web", "幽灵", MD5)
        )
        with open(stale, "w") as f:
            f.write("x")

        service.job_store.create(
            job_id="single_live_ffffffff", kind="single",
            md5=MD5, title="活任务", source="web",
        )
        service.job_store.update("single_live_ffffffff", status="running")

        rows = service.list_files()
        pending = [r for r in rows if r["is_pending"]]
        assert len(pending) == 1
        assert pending[0]["filename"] == "single_live_ffffffff"
        assert "活任务" in pending[0]["title"]
        assert not any("幽灵" in r["title"] for r in rows)

        service.job_store.update("single_live_ffffffff", status="done")
        assert [r for r in service.list_files() if r["is_pending"]] == []


def test_startup_reconciliation_kills_ghost_pending(monkeypatch):
    """M1 核心场景:上次进程死亡残留 running 任务 → 启动对账后
    无 pending 伪行、任务以 failed 可见(不再靠磁盘哨兵存亡)。"""
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: False)
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(tmp)
        service.job_store.create(
            job_id="single_ghost_ffffffff", kind="single",
            md5=MD5, title="崩溃孤儿", source="web",
        )
        service.job_store.update("single_ghost_ffffffff", status="running")

        # 模拟重启:新 service 实例对同一 jobs 文件做启动对账
        service2 = _make_service(tmp)
        service2.mark_orphans_failed("backend restarted before podcast job completed")

        assert [r for r in service2.list_files() if r["is_pending"]] == []
        assert service2.generating_title() == ""
        job = next(
            j for j in service2.job_store.list()
            if j["job_id"] == "single_ghost_ffffffff"
        )
        assert job["status"] == "failed"
