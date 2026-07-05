from __future__ import annotations

import time
from typing import Any

from core.services.json_job_store import JsonJobStore


class UrlJobStore(JsonJobStore):
    def create(
        self,
        *,
        job_id: str,
        url: str,
        mode: str,
        action: str,
        has_html: bool,
    ) -> dict[str, Any]:
        now = time.time()
        job = {
            "job_id": job_id,
            "url": url,
            "mode": mode,
            "action": action,
            "has_html": has_html,
            "status": "queued",
            "stage": "queued",
            "created_at": now,
            "updated_at": now,
            "title": "",
            "source": "web",
            "text_chars": 0,
            "error": None,
        }
        return self.insert(job)

    def mark_unfinished_failed(self, reason: str) -> None:
        self.update_matching(
            lambda job: job.get("status") in {"queued", "running"},
            status="failed",
            stage="interrupted",
            error=reason,
        )
