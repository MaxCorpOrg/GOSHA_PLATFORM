from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any


SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{24,128}$")


class SessionStore:
    def __init__(self, root: Path, ttl_seconds: int = 43200) -> None:
        self.root = root.resolve()
        self.ttl_seconds = max(300, int(ttl_seconds))
        self.root.mkdir(parents=True, exist_ok=True)

    def new_session_id(self) -> str:
        return secrets.token_urlsafe(24)

    def get(self, session_id: str) -> dict[str, Any]:
        envelope = self._read_envelope(session_id)
        if not envelope:
            return {}
        now = time.time()
        updated_at = float(envelope.get("updated_at", 0.0) or 0.0)
        if updated_at and now - updated_at > self.ttl_seconds:
            self.delete(session_id)
            return {}
        payload = envelope.get("payload") or {}
        return dict(payload) if isinstance(payload, dict) else {}

    def put(self, session_id: str, payload: dict[str, Any]) -> None:
        existing = self._read_envelope(session_id)
        now = time.time()
        envelope = {
            "created_at": float(existing.get("created_at", now) or now) if existing else now,
            "updated_at": now,
            "payload": dict(payload),
        }
        self._write_envelope(session_id, envelope)

    def patch(self, session_id: str, **updates: Any) -> dict[str, Any]:
        payload = self.get(session_id)
        payload.update(updates)
        self.put(session_id, payload)
        return payload

    def delete(self, session_id: str) -> None:
        path = self._path_for_session(session_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def _path_for_session(self, session_id: str) -> Path:
        if not SESSION_ID_RE.fullmatch(session_id or ""):
            raise ValueError("Некорректный идентификатор серверного сеанса.")
        return self.root / f"{session_id}.json"

    def _read_envelope(self, session_id: str) -> dict[str, Any]:
        path = self._path_for_session(session_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_envelope(self, session_id: str, envelope: dict[str, Any]) -> None:
        path = self._path_for_session(session_id)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        os.chmod(temp_path, 0o600)
        temp_path.replace(path)
