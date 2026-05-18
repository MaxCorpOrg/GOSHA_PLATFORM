from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionJob:
    job_id: str
    repo_full_name: str
    pr_number: int
    trigger: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "repo_full_name": self.repo_full_name,
            "pr_number": self.pr_number,
            "trigger": self.trigger,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "logs": list(self.logs),
            "result": dict(self.result),
            "error": self.error,
        }


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ExecutionJob] = {}

    def create_or_get_active(self, *, repo_full_name: str, pr_number: int, trigger: str) -> tuple[ExecutionJob, bool]:
        with self._lock:
            for job in self._jobs.values():
                if (
                    job.repo_full_name == repo_full_name
                    and job.pr_number == pr_number
                    and job.status in {"queued", "running"}
                ):
                    return job, False
            job = ExecutionJob(job_id=uuid.uuid4().hex, repo_full_name=repo_full_name, pr_number=pr_number, trigger=trigger)
            self._jobs[job.job_id] = job
            return job, True

    def append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.logs.append(message.rstrip())
            if len(job.logs) > 400:
                job.logs = job.logs[-400:]

    def start(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = time.time()

    def finish(self, job_id: str, *, result: dict[str, Any] | None = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "completed"
            job.finished_at = time.time()
            job.result = result or {}

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.finished_at = time.time()
            job.error = error
            job.logs.append(f"ERROR: {error}")

    def get(self, job_id: str) -> ExecutionJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [job.to_dict() for job in jobs[:20]]
