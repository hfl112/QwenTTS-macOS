from __future__ import annotations

import hashlib
import time
from typing import Any

from core.services.json_job_store import JsonJobStore


def content_key(text: str, mode: str, voice: str) -> str:
    """内容身份 key:同一段文字 + 同一模式 + 同一音色 = 同一份成品。

    生成前查重与 job 落盘共用此函数,保证口径一致(title/source 不参与身份)。
    """
    raw = f"{mode}\x1f{voice}\x1f{text}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class PodcastJobStore(JsonJobStore):
    def create(
        self,
        *,
        job_id: str,
        kind: str,
        md5: str,
        title: str,
        source: str,
        output_path: str | None = None,
        mode: str = "original",
        voice: str | None = None,
        content_key: str | None = None,
        chunk_dir: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        job = {
            "job_id": job_id,
            "kind": kind,
            "md5": md5,
            "title": title,
            "source": source,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "pid": None,
            "output_path": output_path,
            "error": None,
            # 内容身份(#8 复用去重):旧 json 无这些字段,读取一律 .get() 兼容
            "mode": mode,
            "voice": voice,
            "content_key": content_key,
            # chunk 工作目录名(#10 C2.2):job_id 带 uuid 后缀但目录不带,
            # 进度读端必须用这个字段而非 job_id;旧 json 无此字段 .get() 兼容
            "chunk_dir": chunk_dir,
        }
        return self.insert(job)

    def newest_done_for_content_key(self, ck: str) -> dict[str, Any] | None:
        """按内容身份查最新的已完成 job(list 为新→旧序)。文件是否仍存在由调用方校验。"""
        if not ck:
            return None
        for job in self.list():
            if (
                job.get("content_key") == ck
                and job.get("status") == "done"
                and job.get("output_path")
            ):
                return job
        return None

    def active_for_md5(self, md5: str) -> bool:
        active_statuses = {"queued", "running", "paused"}
        return any(
            job.get("md5") == md5 and job.get("status") in active_statuses
            for job in self.list()
        )

    def cancel_active(self) -> None:
        self.update_matching(
            lambda job: job.get("status") in {"queued", "running", "paused"},
            status="canceled",
        )

    def mark_unfinished_failed(self, reason: str) -> None:
        self.update_matching(
            lambda job: job.get("status") in {"queued", "running", "paused"},
            status="failed",
            error=reason,
        )
