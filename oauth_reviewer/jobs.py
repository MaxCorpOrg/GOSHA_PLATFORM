from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReviewJob:
    job_id: str
    owner_session_id: str
    repo_full_name: str
    pr_number: int
    mode: str
    status: str = "queued"
    current_stage: str = "Ожидание запуска"
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    last_log_at: float = 0.0
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "repo_full_name": self.repo_full_name,
            "pr_number": self.pr_number,
            "mode": self.mode,
            "status": self.status,
            "current_stage": self.current_stage,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_log_at": self.last_log_at,
            "logs": list(self.logs),
            "result": dict(self.result),
            "error": self.error,
        }


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ReviewJob] = {}

    def create_or_get_active(
        self,
        *,
        owner_session_id: str,
        repo_full_name: str,
        pr_number: int,
        mode: str,
    ) -> tuple[ReviewJob, bool]:
        with self._lock:
            for job in self._jobs.values():
                if (
                    job.owner_session_id == owner_session_id
                    and job.repo_full_name == repo_full_name
                    and job.pr_number == pr_number
                    and job.mode == mode
                    and job.status in {"queued", "running"}
                ):
                    return job, False
            job = ReviewJob(
                job_id=uuid.uuid4().hex,
                owner_session_id=owner_session_id,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                mode=mode,
            )
            self._jobs[job.job_id] = job
            return job, True

    def append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.logs.append(message.rstrip())
            job.last_log_at = time.time()
            if len(job.logs) > 400:
                job.logs = job.logs[-400:]

    def set_stage(self, job_id: str, stage: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.current_stage = stage.strip() or job.current_stage
            job.last_log_at = time.time()

    def start(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = time.time()
            job.current_stage = "Подготовка задачи"
            job.last_log_at = job.started_at

    def finish(self, job_id: str, *, result: dict[str, Any] | None = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "completed"
            job.finished_at = time.time()
            job.current_stage = "Задача завершена"
            job.last_log_at = job.finished_at
            job.result = result or {}

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.finished_at = time.time()
            job.current_stage = "Задача завершилась ошибкой"
            job.last_log_at = job.finished_at
            job.error = error
            job.logs.append(f"ERROR: {error}")

    def get(self, job_id: str) -> ReviewJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, *, owner_session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [
                job
                for job in self._jobs.values()
                if job.owner_session_id == owner_session_id
            ]
            jobs.sort(key=lambda item: item.created_at, reverse=True)
            return [job.to_dict() for job in jobs[:20]]
