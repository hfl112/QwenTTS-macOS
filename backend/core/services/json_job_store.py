"""文件锁 JSON 任务存储基类(计划 #10 C2.5)。

podcast_jobs 与 url_jobs 此前是两份 ~90% 相同的实现(文件锁 / corrupt 备份 /
原子写 / 按 job_id 去重插入 / update)。收进一个基类;子类只负责各自的
job 字段与业务查询。
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator


@contextmanager
def file_lock(path: str) -> Iterator[None]:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


class JsonJobStore:
    def __init__(self, path: str, max_jobs: int = 100) -> None:
        self.path = path
        self.max_jobs = max_jobs

    def list(self) -> list[dict[str, Any]]:
        with file_lock(self.path):
            return self._load_unlocked()

    def insert(self, job: dict[str, Any]) -> dict[str, Any]:
        """按 job_id 去重后插到最前(list 为新→旧序),裁到 max_jobs。"""
        with file_lock(self.path):
            jobs = [
                item
                for item in self._load_unlocked()
                if item.get("job_id") != job.get("job_id")
            ]
            jobs.insert(0, job)
            self._write_unlocked(jobs[: self.max_jobs])
        return job

    def update(self, job_id: str | None, **fields: Any) -> None:
        if not job_id:
            return
        with file_lock(self.path):
            jobs = self._load_unlocked()
            for job in jobs:
                if job.get("job_id") == job_id:
                    job.update(fields)
                    job["updated_at"] = time.time()
                    break
            self._write_unlocked(jobs[: self.max_jobs])

    def update_matching(
        self, predicate: Callable[[dict[str, Any]], bool], **fields: Any
    ) -> None:
        """对所有命中 predicate 的 job 应用 fields(带 updated_at),一次锁内完成。"""
        with file_lock(self.path):
            jobs = self._load_unlocked()
            now = time.time()
            for job in jobs:
                if predicate(job):
                    job.update(fields)
                    job["updated_at"] = now
            self._write_unlocked(jobs[: self.max_jobs])

    def _backup_corrupt(self, error: Exception) -> None:
        # 备份损坏文件而非静默返回 []——否则后续 _write_unlocked 会用空列表覆盖、
        # 清空整个任务历史。备份后历史仍可从 .corrupt 文件恢复。
        try:
            backup = f"{self.path}.corrupt.{int(time.time())}"
            os.replace(self.path, backup)
            print(f"[{type(self).__name__}] Corrupt {self.path} backed up to {backup}: {error}")
        except Exception:
            pass

    def _load_unlocked(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            raise ValueError("expected a JSON list")
        except Exception as error:
            self._backup_corrupt(error)
            return []

    def _write_unlocked(self, jobs: list[dict[str, Any]]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)
