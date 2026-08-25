#!/usr/bin/env python3
"""Versioned runtime events for the robot-mobile-panel triangle."""

import json
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import deque
from pathlib import Path


EVENT_SCHEMA_VERSION = "gosha.runtime.event.v1"
SNAPSHOT_SCHEMA_VERSION = "gosha.runtime.snapshot.v1"
SOURCE_KINDS = {"robot", "mobile", "panel", "service"}
SEVERITIES = {"debug", "info", "warning", "error", "critical"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9._:@/-]{1,128}$")
ROBOT_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+){1,7}$")
FORBIDDEN_KEYS = {
    "authorization",
    "password",
    "secret",
    "ssid",
    "token",
    "ota_url",
    "websocket_url",
    "ws_url",
}
MAX_EVENT_BYTES = 64 * 1024
MAX_STRING_LENGTH = 2048
MAX_ATTRIBUTES_DEPTH = 4
MAX_RECENT_EVENTS = 100
MAX_RECENT_ERRORS = 40
MAX_RECENT_TASKS = 40
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:https?|wss?)://\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(
        r"(?i)\b(?:authorization|password|passwd|secret|ssid|token|api[_-]?key|access[_-]?key|credential|credentials)\s*[:=]\s*\S+"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b"),
)


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clean_text(value, *, limit=MAX_STRING_LENGTH):
    return str(value or "").strip()[:limit]


def _identifier(value, name, *, required=False):
    text = _clean_text(value, limit=128)
    if not text and not required:
        return ""
    if not IDENTIFIER_RE.fullmatch(text):
        raise ValueError(f"invalid {name}")
    return text


def _robot_id(value):
    text = _clean_text(value, limit=128)
    if not ROBOT_ID_RE.fullmatch(text):
        raise ValueError("invalid robot_id")
    return text


def _is_forbidden_key(key):
    lowered = key.lower()
    return (
        lowered in FORBIDDEN_KEYS
        or "token" in lowered
        or "password" in lowered
        or "secret" in lowered
        or lowered.endswith("_url")
    )


def _is_secret_value_key(key):
    compact = re.sub(r"[^a-z0-9]", "", str(key or "").lower())
    return (
        "apikey" in compact
        or "accesskey" in compact
        or "credential" in compact
        or "clientsecret" in compact
        or "privatekey" in compact
        or "secretkey" in compact
    )


def _clean_map(value, *, depth=0):
    if depth > MAX_ATTRIBUTES_DEPTH or not isinstance(value, dict):
        return {}
    clean = {}
    for raw_key, raw_value in value.items():
        key = _clean_text(raw_key, limit=64)
        if not key:
            continue
        if _is_secret_value_key(key):
            clean[key] = "[redacted]" if raw_value not in (None, "") else ""
            continue
        if _is_forbidden_key(key):
            continue
        if isinstance(raw_value, dict):
            clean[key] = _clean_map(raw_value, depth=depth + 1)
        elif isinstance(raw_value, list):
            clean[key] = [
                _clean_map(item, depth=depth + 1) if isinstance(item, dict) else _clean_scalar(item)
                for item in raw_value[:64]
            ]
        else:
            clean[key] = _clean_scalar(raw_value)
    return clean


def _clean_scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clean_sensitive_text(value)


def _clean_sensitive_text(value, *, limit=MAX_STRING_LENGTH):
    text = _clean_text(value, limit=limit)
    if any(pattern.search(text) for pattern in SENSITIVE_VALUE_PATTERNS):
        return "[redacted]"
    return text


def _clean_section(payload, name):
    return _clean_map(payload.get(name, {}))


def _summary(event):
    return {
        key: event[key]
        for key in (
            "schema_version",
            "event_id",
            "event_type",
            "source",
            "subject",
            "trace",
            "occurred_at",
            "received_at",
            "sequence",
            "severity",
            "state",
            "link",
            "task",
            "error",
            "metrics",
        )
        if key in event
    }


def normalize_event(payload, *, robot_id, source_kind, source_id, client_id="", panel_id=""):
    if not isinstance(payload, dict):
        raise ValueError("event payload must be an object")
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_EVENT_BYTES:
        raise ValueError("event payload is too large")

    supplied_schema = _clean_text(payload.get("schema_version"), limit=64)
    if supplied_schema and supplied_schema != EVENT_SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    event_type = _clean_text(payload.get("event_type"), limit=128)
    if not EVENT_TYPE_RE.fullmatch(event_type):
        raise ValueError("invalid event_type")

    normalized_source_kind = _clean_text(source_kind, limit=16).lower()
    if normalized_source_kind not in SOURCE_KINDS:
        raise ValueError("invalid source kind")
    normalized_robot_id = _robot_id(robot_id)
    normalized_source_id = _identifier(source_id, "source.id", required=True)
    event_id = _identifier(payload.get("event_id") or str(uuid.uuid4()), "event_id", required=True)

    raw_source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source = {
        "kind": normalized_source_kind,
        "id": normalized_source_id,
    }
    for key in ("instance_id", "app_version", "firmware_version"):
        text = _clean_sensitive_text(raw_source.get(key), limit=128)
        if text:
            source[key] = text

    subject = {"robot_id": normalized_robot_id}
    normalized_client_id = _identifier(client_id, "client_id")
    if normalized_client_id:
        subject["client_id"] = normalized_client_id
    normalized_panel_id = _identifier(panel_id, "panel_id")
    if normalized_panel_id:
        subject["panel_id"] = normalized_panel_id

    trace = {}
    raw_trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
    for key in ("session_id", "correlation_id", "causation_id"):
        text = _identifier(raw_trace.get(key), key)
        if text:
            trace[key] = text

    severity = _clean_text(payload.get("severity") or "info", limit=16).lower()
    if severity not in SEVERITIES:
        raise ValueError("invalid severity")
    occurred_at = _clean_sensitive_text(payload.get("occurred_at") or _now_iso(), limit=64)
    sequence = payload.get("sequence")
    if sequence is not None:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("invalid sequence")

    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": event_type,
        "source": source,
        "subject": subject,
        "occurred_at": occurred_at,
        "received_at": _now_iso(),
        "severity": severity,
    }
    if trace:
        event["trace"] = trace
    if sequence is not None:
        event["sequence"] = sequence
    for section in ("state", "link", "task", "error", "metrics", "attributes"):
        clean = _clean_section(payload, section)
        if clean:
            event[section] = clean
    return event


class RuntimeEventStore:
    def __init__(self, root, *, max_events_per_robot=10000):
        self.root = Path(root)
        self.max_events_per_robot = max(100, int(max_events_per_robot))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._cursor = 0
        self._bus = deque(maxlen=1000)
        self._connection = None

    def _robot_dir(self, robot_id):
        return self.root / _robot_id(robot_id)

    def _snapshot_path(self, robot_id):
        return self._robot_dir(robot_id) / "snapshot.json"

    def _database_path(self):
        return self.root / "events.sqlite3"

    def _connect(self):
        if self._connection is not None:
            return self._connection
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path(), timeout=10, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_events (
                robot_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                received_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (robot_id, event_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS runtime_events_robot_received "
            "ON runtime_events(robot_id, received_at)"
        )
        self._connection = connection
        return self._connection

    def _empty_snapshot(self, robot_id):
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "robot_id": _robot_id(robot_id),
            "updated_at": "",
            "components": {kind: [] for kind in sorted(SOURCE_KINDS)},
            "links": [],
            "tasks": {"active": [], "recent": []},
            "errors": {"recent": []},
            "statistics": {
                "events_total": 0,
                "by_type": {},
                "by_source_kind": {},
                "by_severity": {},
            },
            "recent_events": [],
        }

    def snapshot(self, robot_id):
        path = self._snapshot_path(robot_id)
        if not path.exists():
            return self._empty_snapshot(robot_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else self._empty_snapshot(robot_id)
        except Exception:
            return self._empty_snapshot(robot_id)

    def list_events(self, robot_id, *, limit=100):
        bounded_limit = max(1, min(int(limit), 500))
        normalized_robot_id = _robot_id(robot_id)
        if not self._database_path().exists():
            return []
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM runtime_events WHERE robot_id = ? "
                "ORDER BY rowid DESC LIMIT ?",
                (normalized_robot_id, bounded_limit),
            ).fetchall()
        events = []
        for (raw,) in reversed(rows):
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if isinstance(item, dict):
                events.append(item)
        return events

    def _save_snapshot(self, path, snapshot):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
            json.dump(snapshot, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)

    @staticmethod
    def _item_key(item, key_fields):
        return tuple(item.get(field) for field in key_fields)

    @staticmethod
    def _is_stale_candidate(existing, candidate):
        if not isinstance(existing, dict):
            return False
        same_instance = existing.get("source_instance_id", "") == candidate.get("source_instance_id", "")
        previous_sequence = existing.get("sequence")
        candidate_sequence = candidate.get("sequence")
        return (
            same_instance
            and isinstance(previous_sequence, int)
            and isinstance(candidate_sequence, int)
            and candidate_sequence < previous_sequence
        )

    @classmethod
    def _find_by_key(cls, items, candidate, key_fields):
        candidate_key = cls._item_key(candidate, key_fields)
        return next((item for item in items if cls._item_key(item, key_fields) == candidate_key), None)

    @classmethod
    def _replace_by_key(cls, items, candidate, key_fields, *, limit):
        existing = cls._find_by_key(items, candidate, key_fields)
        if cls._is_stale_candidate(existing, candidate):
            return items[:limit]
        key = cls._item_key(candidate, key_fields)
        remaining = [item for item in items if cls._item_key(item, key_fields) != key]
        return ([candidate] + remaining)[:limit]

    def _apply_event(self, snapshot, event):
        source = event["source"]
        component = {
            "kind": source["kind"],
            "id": source["id"],
            "instance_id": source.get("instance_id", ""),
            "source_instance_id": source.get("instance_id", ""),
            "sequence": event.get("sequence"),
            "event_type": event["event_type"],
            "severity": event["severity"],
            "state": event.get("state", {}),
            "last_seen_at": event["received_at"],
        }
        components = snapshot.setdefault("components", {}).setdefault(source["kind"], [])
        snapshot["components"][source["kind"]] = self._replace_by_key(
            components,
            component,
            ("kind", "id", "instance_id"),
            limit=32,
        )

        link = event.get("link")
        if isinstance(link, dict) and link.get("kind"):
            link_record = dict(link)
            link_record.update(
                {
                    "source_kind": source["kind"],
                    "source_id": source["id"],
                    "source_instance_id": source.get("instance_id", ""),
                    "sequence": event.get("sequence"),
                    "updated_at": event["received_at"],
                }
            )
            snapshot["links"] = self._replace_by_key(
                snapshot.get("links", []),
                link_record,
                ("kind", "source_kind", "source_id"),
                limit=64,
            )

        task = event.get("task")
        if isinstance(task, dict) and task.get("id"):
            task_record = dict(task)
            task_record.update(
                {
                    "source_kind": source["kind"],
                    "source_id": source["id"],
                    "source_instance_id": source.get("instance_id", ""),
                    "sequence": event.get("sequence"),
                    "updated_at": event["received_at"],
                }
            )
            tasks = snapshot.setdefault("tasks", {"active": [], "recent": []})
            recent = tasks.get("recent", [])
            if not self._is_stale_candidate(self._find_by_key(recent, task_record, ("id",)), task_record):
                tasks["active"] = [item for item in tasks.get("active", []) if item.get("id") != task.get("id")]
                if task.get("status") not in TERMINAL_TASK_STATUSES:
                    tasks["active"] = ([task_record] + tasks["active"])[:MAX_RECENT_TASKS]
                tasks["recent"] = self._replace_by_key(recent, task_record, ("id",), limit=MAX_RECENT_TASKS)

        error = event.get("error")
        if isinstance(error, dict) and error:
            error_record = dict(error)
            error_record.update(
                {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "severity": event["severity"],
                    "source_kind": source["kind"],
                    "source_id": source["id"],
                    "occurred_at": event["occurred_at"],
                }
            )
            errors = snapshot.setdefault("errors", {"recent": []})
            errors["recent"] = ([error_record] + errors.get("recent", []))[:MAX_RECENT_ERRORS]

        stats = snapshot.setdefault("statistics", {})
        stats["events_total"] = int(stats.get("events_total", 0) or 0) + 1
        for field, value in (
            ("by_type", event["event_type"]),
            ("by_source_kind", source["kind"]),
            ("by_severity", event["severity"]),
        ):
            bucket = stats.setdefault(field, {})
            bucket[value] = int(bucket.get(value, 0) or 0) + 1

        snapshot["updated_at"] = event["received_at"]
        snapshot["recent_events"] = ([_summary(event)] + snapshot.get("recent_events", []))[:MAX_RECENT_EVENTS]
        return snapshot

    def record(self, payload, *, robot_id, source_kind, source_id, client_id="", panel_id=""):
        event = normalize_event(
            payload,
            robot_id=robot_id,
            source_kind=source_kind,
            source_id=source_id,
            client_id=client_id,
            panel_id=panel_id,
        )
        with self._condition:
            snapshot = self.snapshot(robot_id)
            with self._connect() as connection:
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO runtime_events(robot_id, event_id, received_at, payload) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        event["subject"]["robot_id"],
                        event["event_id"],
                        event["received_at"],
                        json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                    ),
                ).rowcount
                if not inserted:
                    row = connection.execute(
                        "SELECT payload FROM runtime_events WHERE robot_id = ? AND event_id = ?",
                        (event["subject"]["robot_id"], event["event_id"]),
                    ).fetchone()
                    original = json.loads(row[0]) if row else event
                    return original, snapshot, True
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM runtime_events WHERE robot_id = ?",
                    (event["subject"]["robot_id"],),
                ).fetchone()[0]
                excess = int(event_count or 0) - self.max_events_per_robot
                if excess > 0:
                    connection.execute(
                        "DELETE FROM runtime_events WHERE rowid IN ("
                        "SELECT rowid FROM runtime_events WHERE robot_id = ? ORDER BY rowid ASC LIMIT ?"
                        ")",
                        (event["subject"]["robot_id"], excess),
                    )
            snapshot = self._apply_event(snapshot, event)
            self._save_snapshot(self._snapshot_path(robot_id), snapshot)
            self._cursor += 1
            self._bus.append((self._cursor, event))
            self._condition.notify_all()
            return event, snapshot, False

    def wait_for_events(self, cursor, *, timeout=15.0):
        with self._condition:
            requested = max(0, int(cursor or 0))
            if requested > self._cursor:
                # The process may have restarted while a browser retained an
                # older in-memory SSE cursor. Resume from this process instead
                # of waiting until the new cursor catches up.
                requested = 0
            if self._cursor <= requested:
                self._condition.wait(timeout=max(0.0, min(float(timeout), 30.0)))
            events = [(item_cursor, event) for item_cursor, event in self._bus if item_cursor > requested]
            return self._cursor, events
