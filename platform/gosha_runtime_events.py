#!/usr/bin/env python3
"""Versioned runtime events for the robot-mobile-panel triangle."""

import hashlib
import json
import math
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
MAX_JSON_SAFE_DEPTH = 16
MAX_RECENT_EVENTS = 100
MAX_RECENT_ERRORS = 40
MAX_RECENT_TASKS = 40
SQLITE_INT64_MIN = -(2**63)
SQLITE_INT64_MAX = 2**63 - 1
SNAPSHOT_TOP_LEVEL_KEYS = {
    "schema_version",
    "robot_id",
    "updated_at",
    "components",
    "links",
    "tasks",
    "errors",
    "statistics",
    "recent_events",
    "projection",
}
SNAPSHOT_PROJECTION_KEYS = {"kind", "last_event_rowid", "last_event_id", "events_total"}
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:https?|wss?)://\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(
        r"(?i)\b(?:authorization|password|passwd|secret|ssid|token|api[_-]?key|access[_-]?key|credential|credentials)\s*[:=]\s*\S+"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b"),
)


def _json_dumps(value, **kwargs):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, **kwargs)


def _json_digest(value):
    data = _json_dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _int64_is_safe(value):
    return isinstance(value, int) and not isinstance(value, bool) and SQLITE_INT64_MIN <= value <= SQLITE_INT64_MAX


def _nonnegative_int64_is_safe(value):
    return _int64_is_safe(value) and value >= 0


def _json_value_is_safe(value, *, depth=0):
    if depth > MAX_JSON_SAFE_DEPTH:
        return False
    if value is None or isinstance(value, bool) or isinstance(value, str):
        return True
    if isinstance(value, int):
        return _int64_is_safe(value)
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value_is_safe(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return False
            if not _json_value_is_safe(item, depth=depth + 1):
                return False
        return True
    return False


def _string_value_is_safe(value, *, required=False):
    if value is None:
        return not required
    if not isinstance(value, str):
        return False
    if not value:
        return not required
    return True


def _identifier_value_is_safe(value, *, required=False):
    if not _string_value_is_safe(value, required=required):
        return False
    if not value:
        return not required
    return IDENTIFIER_RE.fullmatch(value) is not None


def _event_type_value_is_safe(value, *, required=False):
    if not _string_value_is_safe(value, required=required):
        return False
    if not value:
        return not required
    return EVENT_TYPE_RE.fullmatch(value) is not None


def _require_string_value(value, name, *, required=False):
    if not _string_value_is_safe(value, required=required):
        raise ValueError(f"invalid {name}")


def _require_identifier_value(value, name, *, required=False):
    if not _identifier_value_is_safe(value, required=required):
        raise ValueError(f"invalid {name}")


def _validate_clean_section(name, clean):
    if not _json_value_is_safe(clean):
        raise ValueError(f"invalid {name}")
    if name == "state":
        for field in ("domain", "name", "status"):
            if field in clean:
                _require_string_value(clean[field], f"state.{field}")
    elif name == "link":
        if "kind" in clean:
            _require_identifier_value(clean["kind"], "link.kind")
        if "status" in clean:
            _require_string_value(clean["status"], "link.status")
    elif name == "task":
        if "id" in clean:
            _require_identifier_value(clean["id"], "task.id")
        for field in ("kind", "status"):
            if field in clean:
                _require_string_value(clean[field], f"task.{field}")
    elif name == "error":
        if "code" in clean:
            _require_identifier_value(clean["code"], "error.code")
        if "message" in clean:
            _require_string_value(clean["message"], "error.message")


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
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not _int64_is_safe(value):
            raise ValueError("integer is outside signed 64-bit range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float is not allowed")
        return value
    return _clean_sensitive_text(value)


def _clean_sensitive_text(value, *, limit=MAX_STRING_LENGTH):
    text = _clean_text(value, limit=limit)
    if any(pattern.search(text) for pattern in SENSITIVE_VALUE_PATTERNS):
        return "[redacted]"
    return text


def _stored_clean_value_is_safe(value, *, depth=0, canonical_shape=True):
    """Verify that persisted JSON is already in the canonical secret-safe form."""
    if depth > MAX_JSON_SAFE_DEPTH:
        return False
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return False
            inspected_key = key.strip()
            if not inspected_key:
                return False
            if canonical_shape and key != _clean_text(key, limit=64):
                return False
            if _is_forbidden_key(inspected_key):
                return False
            if _is_secret_value_key(inspected_key):
                if item not in ("", "[redacted]"):
                    return False
                continue
            if not _stored_clean_value_is_safe(
                item,
                depth=depth + 1,
                canonical_shape=canonical_shape,
            ):
                return False
        return True
    if isinstance(value, list):
        if canonical_shape and len(value) > 64:
            return False
        return all(
            _stored_clean_value_is_safe(
                item,
                depth=depth + 1,
                canonical_shape=canonical_shape,
            )
            for item in value
        )
    if isinstance(value, str):
        return len(value) <= MAX_STRING_LENGTH and _clean_sensitive_text(value) == value
    return _json_value_is_safe(value, depth=depth)


def _clean_section(payload, name):
    clean = _clean_map(payload.get(name, {}))
    if clean:
        _validate_clean_section(name, clean)
    return clean


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
    if not _json_value_is_safe(payload):
        raise ValueError("event payload contains invalid JSON value")
    if len(_json_dumps(payload).encode("utf-8")) > MAX_EVENT_BYTES:
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

    if "source" in payload and payload.get("source") is not None and not isinstance(payload.get("source"), dict):
        raise ValueError("invalid source")
    raw_source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source = {
        "kind": normalized_source_kind,
        "id": normalized_source_id,
    }
    instance_id = _identifier(raw_source.get("instance_id"), "source.instance_id")
    if instance_id:
        source["instance_id"] = instance_id
    for key in ("instance_id", "app_version", "firmware_version"):
        if key == "instance_id":
            continue
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
        if not _nonnegative_int64_is_safe(sequence):
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
        self._last_snapshot_export_error = ""

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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_snapshots (
                robot_id TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                last_event_rowid INTEGER NOT NULL,
                last_event_id TEXT NOT NULL,
                events_total INTEGER NOT NULL,
                payload_digest TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
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
        normalized_robot_id = _robot_id(robot_id)
        with self._lock:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._load_or_rebuild_db_snapshot(connection, normalized_robot_id)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            if self._snapshot_cache_is_current(self._snapshot_path(normalized_robot_id), row):
                return self._read_snapshot_cache(self._snapshot_path(normalized_robot_id))
            self._export_snapshot_cache(self._snapshot_path(normalized_robot_id), row["payload"])
            return row["payload"]

    def _db_snapshot_row(self, connection, robot_id):
        normalized_robot_id = _robot_id(robot_id)
        row = connection.execute(
            "SELECT payload, updated_at, last_event_rowid, last_event_id, events_total, payload_digest "
            "FROM runtime_snapshots WHERE robot_id = ?",
            (normalized_robot_id,),
        ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[0])
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        if not self._legacy_snapshot_shape_is_safe(payload, normalized_robot_id, require_projection=True):
            return None
        digest = str(row[5] or "")
        try:
            calculated_digest = _json_digest(payload)
        except (TypeError, ValueError):
            return None
        if digest != calculated_digest:
            return None
        projection = payload.get("projection") if isinstance(payload.get("projection"), dict) else {}
        try:
            row_last_event_rowid = int(row[2] or 0)
            row_last_event_id = str(row[3] or "")
            row_events_total = int(row[4] or 0)
        except (TypeError, ValueError):
            return None
        payload_events_total = payload["statistics"]["events_total"]
        if (
            int(projection.get("last_event_rowid", 0) or 0) != row_last_event_rowid
            or str(projection.get("last_event_id", "") or "") != row_last_event_id
            or int(projection.get("events_total", 0) or 0) != row_events_total
            or payload_events_total != row_events_total
        ):
            return None
        return {
            "payload": payload,
            "updated_at": str(row[1] or ""),
            "last_event_rowid": row_last_event_rowid,
            "last_event_id": row_last_event_id,
            "events_total": row_events_total,
            "payload_digest": digest,
        }

    def _journal_tip(self, connection, robot_id):
        normalized_robot_id = _robot_id(robot_id)
        row = connection.execute(
            "SELECT rowid, payload FROM runtime_events WHERE robot_id = ? ORDER BY rowid DESC LIMIT 1",
            (normalized_robot_id,),
        ).fetchone()
        retained_count = connection.execute(
            "SELECT COUNT(*) FROM runtime_events WHERE robot_id = ?",
            (normalized_robot_id,),
        ).fetchone()[0]
        if not row:
            return 0, "", int(retained_count or 0)
        try:
            event = json.loads(row[1])
        except Exception:
            event = {}
        return int(row[0] or 0), str((event if isinstance(event, dict) else {}).get("event_id", "") or ""), int(retained_count or 0)

    def _legacy_snapshot_cache(self, connection, robot_id):
        normalized_robot_id = _robot_id(robot_id)
        path = self._snapshot_path(normalized_robot_id)
        if not path.exists():
            return None
        try:
            snapshot = self._read_snapshot_cache(path)
        except Exception:
            return None
        if not self._legacy_snapshot_shape_is_safe(snapshot, normalized_robot_id):
            return None

        tip_rowid, tip_event_id, retained_count = self._journal_tip(connection, normalized_robot_id)
        stats = snapshot.get("statistics") if isinstance(snapshot.get("statistics"), dict) else {}
        events_total = int(stats.get("events_total", 0) or 0)
        if events_total < retained_count:
            return None
        recent = snapshot.get("recent_events") if isinstance(snapshot.get("recent_events"), list) else []
        if retained_count > 0:
            latest = recent[0] if recent and isinstance(recent[0], dict) else {}
            if str(latest.get("event_id", "") or "") != tip_event_id:
                return None
        elif events_total != 0:
            return None
        return self._upsert_db_snapshot(
            connection,
            normalized_robot_id,
            snapshot,
            last_event_rowid=tip_rowid,
            last_event_id=tip_event_id,
        )

    @staticmethod
    def _nonnegative_int(value):
        return _nonnegative_int64_is_safe(value)

    @staticmethod
    def _safe_counter_key(value):
        return isinstance(value, str) and IDENTIFIER_RE.fullmatch(value) is not None

    @classmethod
    def _counter_map_is_safe(cls, value):
        if not isinstance(value, dict):
            return False
        for key, counter in value.items():
            if not cls._safe_counter_key(key) or not cls._nonnegative_int(counter):
                return False
        return True

    @staticmethod
    def _dict_list_is_safe(value, *, item_validator=None):
        if not isinstance(value, list):
            return False
        for item in value:
            if not isinstance(item, dict):
                return False
            if item_validator is not None and not item_validator(item):
                return False
        return True

    @staticmethod
    def _component_record_is_safe(item):
        if item.get("kind") not in SOURCE_KINDS:
            return False
        if not _identifier_value_is_safe(item.get("id"), required=True):
            return False
        for field in ("instance_id", "source_instance_id"):
            if field in item and not _identifier_value_is_safe(item.get(field)):
                return False
        if not _event_type_value_is_safe(item.get("event_type"), required=True):
            return False
        if item.get("severity") not in SEVERITIES:
            return False
        if not _string_value_is_safe(item.get("last_seen_at"), required=True):
            return False
        sequence = item.get("sequence")
        if sequence is not None and not _nonnegative_int64_is_safe(sequence):
            return False
        state = item.get("state")
        if state is None:
            return True
        if not isinstance(state, dict):
            return False
        try:
            _validate_clean_section("state", state)
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _link_record_is_safe(item):
        if not _identifier_value_is_safe(item.get("kind"), required=True):
            return False
        if item.get("source_kind") not in SOURCE_KINDS:
            return False
        if not _identifier_value_is_safe(item.get("source_id"), required=True):
            return False
        if "source_instance_id" in item and not _identifier_value_is_safe(item.get("source_instance_id")):
            return False
        if not _string_value_is_safe(item.get("updated_at"), required=True):
            return False
        if "status" in item and not _string_value_is_safe(item.get("status")):
            return False
        sequence = item.get("sequence")
        return sequence is None or _nonnegative_int64_is_safe(sequence)

    @staticmethod
    def _task_record_is_safe(item):
        if not _identifier_value_is_safe(item.get("id"), required=True):
            return False
        for field in ("kind", "status"):
            if field in item and not _string_value_is_safe(item.get(field)):
                return False
        if item.get("source_kind") not in SOURCE_KINDS:
            return False
        if not _identifier_value_is_safe(item.get("source_id"), required=True):
            return False
        if "source_instance_id" in item and not _identifier_value_is_safe(item.get("source_instance_id")):
            return False
        if not _string_value_is_safe(item.get("updated_at"), required=True):
            return False
        sequence = item.get("sequence")
        return sequence is None or _nonnegative_int64_is_safe(sequence)

    @staticmethod
    def _error_record_is_safe(item):
        if not _identifier_value_is_safe(item.get("event_id"), required=True):
            return False
        if not _event_type_value_is_safe(item.get("event_type"), required=True):
            return False
        if item.get("severity") not in SEVERITIES:
            return False
        if item.get("source_kind") not in SOURCE_KINDS:
            return False
        if not _identifier_value_is_safe(item.get("source_id"), required=True):
            return False
        if not _string_value_is_safe(item.get("occurred_at"), required=True):
            return False
        if "code" in item and not _identifier_value_is_safe(item.get("code")):
            return False
        if "message" in item and not _string_value_is_safe(item.get("message")):
            return False
        if "retryable" in item and not isinstance(item.get("retryable"), bool):
            return False
        return True

    @staticmethod
    def _recent_event_record_is_safe(item, *, expected_robot_id=""):
        if not isinstance(item, dict) or not _stored_clean_value_is_safe(item):
            return False
        if item.get("schema_version") != EVENT_SCHEMA_VERSION:
            return False
        if not _identifier_value_is_safe(item.get("event_id"), required=True):
            return False
        if not _event_type_value_is_safe(item.get("event_type"), required=True):
            return False
        if item.get("severity") not in SEVERITIES:
            return False
        if not _string_value_is_safe(item.get("occurred_at"), required=True):
            return False
        if not _string_value_is_safe(item.get("received_at"), required=True):
            return False
        source = item.get("source")
        if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
            return False
        if not _identifier_value_is_safe(source.get("id"), required=True):
            return False
        subject = item.get("subject")
        if not isinstance(subject, dict) or not isinstance(subject.get("robot_id"), str):
            return False
        if ROBOT_ID_RE.fullmatch(subject["robot_id"]) is None:
            return False
        if expected_robot_id and subject["robot_id"] != expected_robot_id:
            return False
        for field in ("trace", "state", "link", "task", "error", "metrics"):
            if field in item and not isinstance(item.get(field), dict):
                return False
        try:
            for section in ("state", "link", "task", "error", "metrics"):
                if section in item:
                    _validate_clean_section(section, item[section])
        except (TypeError, ValueError):
            return False
        sequence = item.get("sequence")
        return sequence is None or _nonnegative_int64_is_safe(sequence)

    @staticmethod
    def _stored_event_is_safe(event, *, expected_robot_id=""):
        if not isinstance(event, dict) or not _json_value_is_safe(event):
            return False
        if not _stored_clean_value_is_safe(event):
            return False
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            return False
        if not _identifier_value_is_safe(event.get("event_id"), required=True):
            return False
        if not _event_type_value_is_safe(event.get("event_type"), required=True):
            return False
        if event.get("severity") not in SEVERITIES:
            return False
        if not _string_value_is_safe(event.get("occurred_at"), required=True):
            return False
        if not _string_value_is_safe(event.get("received_at"), required=True):
            return False
        source = event.get("source")
        if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
            return False
        if not _identifier_value_is_safe(source.get("id"), required=True):
            return False
        if "instance_id" in source and not _identifier_value_is_safe(source.get("instance_id")):
            return False
        for field in ("app_version", "firmware_version"):
            if field in source and not _string_value_is_safe(source.get(field)):
                return False
        subject = event.get("subject")
        if not isinstance(subject, dict):
            return False
        subject_robot_id = subject.get("robot_id")
        if not isinstance(subject_robot_id, str) or ROBOT_ID_RE.fullmatch(subject_robot_id) is None:
            return False
        if expected_robot_id and subject_robot_id != expected_robot_id:
            return False
        for field in ("client_id", "panel_id"):
            if field in subject and not _identifier_value_is_safe(subject.get(field)):
                return False
        trace = event.get("trace")
        if trace is not None:
            if not isinstance(trace, dict):
                return False
            for field in ("session_id", "correlation_id", "causation_id"):
                if field in trace and not _identifier_value_is_safe(trace.get(field)):
                    return False
        sequence = event.get("sequence")
        if sequence is not None and not _nonnegative_int64_is_safe(sequence):
            return False
        try:
            for section in ("state", "link", "task", "error", "metrics", "attributes"):
                if section not in event:
                    continue
                value = event.get(section)
                if not isinstance(value, dict):
                    return False
                _validate_clean_section(section, value)
        except (TypeError, ValueError):
            return False
        return True

    @classmethod
    def _projection_is_safe(cls, projection):
        if not isinstance(projection, dict):
            return False
        if set(projection) != SNAPSHOT_PROJECTION_KEYS:
            return False
        if projection.get("kind") != "sqlite-runtime-snapshot.v1":
            return False
        if not cls._nonnegative_int(projection.get("last_event_rowid")):
            return False
        last_event_id = projection.get("last_event_id")
        if not isinstance(last_event_id, str):
            return False
        if last_event_id and IDENTIFIER_RE.fullmatch(last_event_id) is None:
            return False
        return cls._nonnegative_int(projection.get("events_total"))

    def _legacy_snapshot_shape_is_safe(self, snapshot, robot_id, *, require_projection=False):
        try:
            return self._legacy_snapshot_shape_is_safe_unchecked(
                snapshot,
                _robot_id(robot_id),
                require_projection=require_projection,
            )
        except Exception:
            return False

    def _legacy_snapshot_shape_is_safe_unchecked(self, snapshot, normalized_robot_id, *, require_projection):
        if not isinstance(snapshot, dict):
            return False
        if not _json_value_is_safe(snapshot):
            return False
        if not _stored_clean_value_is_safe(snapshot, canonical_shape=False):
            return False
        if not set(snapshot).issubset(SNAPSHOT_TOP_LEVEL_KEYS):
            return False
        if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            return False
        if snapshot.get("robot_id") != normalized_robot_id:
            return False
        updated_at = snapshot.get("updated_at", "")
        if not isinstance(updated_at, str):
            return False

        projection = snapshot.get("projection")
        if require_projection or projection is not None:
            if not self._projection_is_safe(projection):
                return False

        components = snapshot.get("components")
        if not isinstance(components, dict):
            return False
        allowed_component_keys = SOURCE_KINDS | {"all"}
        for kind, records in components.items():
            if kind not in allowed_component_keys:
                return False
            if not self._dict_list_is_safe(records, item_validator=self._component_record_is_safe):
                return False
        for kind in SOURCE_KINDS:
            if kind in components and not isinstance(components[kind], list):
                return False

        if not self._dict_list_is_safe(snapshot.get("links"), item_validator=self._link_record_is_safe):
            return False

        tasks = snapshot.get("tasks")
        if not isinstance(tasks, dict):
            return False
        if not self._dict_list_is_safe(tasks.get("active"), item_validator=self._task_record_is_safe):
            return False
        if not self._dict_list_is_safe(tasks.get("recent"), item_validator=self._task_record_is_safe):
            return False

        errors = snapshot.get("errors")
        if not isinstance(errors, dict):
            return False
        if not self._dict_list_is_safe(errors.get("recent"), item_validator=self._error_record_is_safe):
            return False

        stats = snapshot.get("statistics")
        if not isinstance(stats, dict):
            return False
        if not self._nonnegative_int(stats.get("events_total")):
            return False
        for field in ("by_type", "by_source_kind", "by_severity"):
            if not self._counter_map_is_safe(stats.get(field)):
                return False

        if not self._dict_list_is_safe(
            snapshot.get("recent_events"),
            item_validator=lambda item: self._recent_event_record_is_safe(
                item,
                expected_robot_id=normalized_robot_id,
            ),
        ):
            return False
        return True

    def _build_snapshot_from_journal(self, connection, robot_id):
        normalized_robot_id = _robot_id(robot_id)
        snapshot = self._empty_snapshot(normalized_robot_id)
        last_event_rowid = 0
        last_event_id = ""
        rows = connection.execute(
            "SELECT rowid, payload FROM runtime_events WHERE robot_id = ? ORDER BY rowid ASC",
            (normalized_robot_id,),
        ).fetchall()
        for rowid, raw in rows:
            try:
                event = json.loads(raw)
            except Exception:
                continue
            if not self._stored_event_is_safe(event, expected_robot_id=normalized_robot_id):
                continue
            snapshot = self._apply_event(snapshot, event)
            last_event_rowid = int(rowid or 0)
            last_event_id = str(event.get("event_id", "") or "")
        return snapshot, last_event_rowid, last_event_id

    def _stamp_snapshot_projection(self, snapshot, *, last_event_rowid, last_event_id):
        stats = snapshot.setdefault("statistics", {})
        events_total = int(stats.get("events_total", 0) or 0)
        snapshot["projection"] = {
            "kind": "sqlite-runtime-snapshot.v1",
            "last_event_rowid": int(last_event_rowid or 0),
            "last_event_id": str(last_event_id or ""),
            "events_total": events_total,
        }
        return events_total

    def _upsert_db_snapshot(self, connection, robot_id, snapshot, *, last_event_rowid, last_event_id):
        normalized_robot_id = _robot_id(robot_id)
        events_total = self._stamp_snapshot_projection(
            snapshot,
            last_event_rowid=last_event_rowid,
            last_event_id=last_event_id,
        )
        if not self._legacy_snapshot_shape_is_safe(snapshot, normalized_robot_id, require_projection=True):
            raise ValueError("invalid runtime snapshot projection")
        payload = _json_dumps(snapshot, separators=(",", ":"))
        digest = _json_digest(snapshot)
        connection.execute(
            """
            INSERT INTO runtime_snapshots(
                robot_id,
                updated_at,
                last_event_rowid,
                last_event_id,
                events_total,
                payload_digest,
                payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(robot_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                last_event_rowid = excluded.last_event_rowid,
                last_event_id = excluded.last_event_id,
                events_total = excluded.events_total,
                payload_digest = excluded.payload_digest,
                payload = excluded.payload
            """,
            (
                normalized_robot_id,
                snapshot.get("updated_at", ""),
                int(last_event_rowid or 0),
                str(last_event_id or ""),
                events_total,
                digest,
                payload,
            ),
        )
        return {
            "payload": snapshot,
            "updated_at": str(snapshot.get("updated_at", "") or ""),
            "last_event_rowid": int(last_event_rowid or 0),
            "last_event_id": str(last_event_id or ""),
            "events_total": events_total,
            "payload_digest": digest,
        }

    def _load_or_rebuild_db_snapshot(self, connection, robot_id):
        normalized_robot_id = _robot_id(robot_id)
        row = self._db_snapshot_row(connection, normalized_robot_id)
        if row is not None:
            return row
        row = self._legacy_snapshot_cache(connection, normalized_robot_id)
        if row is not None:
            return row
        snapshot, last_event_rowid, last_event_id = self._build_snapshot_from_journal(connection, normalized_robot_id)
        return self._upsert_db_snapshot(
            connection,
            normalized_robot_id,
            snapshot,
            last_event_rowid=last_event_rowid,
            last_event_id=last_event_id,
        )

    def _read_snapshot_cache(self, path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("snapshot cache is not an object")
        return raw

    def _snapshot_cache_is_current(self, path, row):
        if not path.exists():
            return False
        try:
            raw = self._read_snapshot_cache(path)
        except Exception:
            return False
        try:
            cache_digest = _json_digest(raw)
        except (TypeError, ValueError):
            return False
        if cache_digest != row["payload_digest"]:
            return False
        projection = raw.get("projection") if isinstance(raw.get("projection"), dict) else {}
        if not self._projection_is_safe(projection):
            return False
        try:
            return (
                int(projection.get("last_event_rowid", 0) or 0) == int(row.get("last_event_rowid", 0) or 0)
                and str(projection.get("last_event_id", "") or "") == str(row.get("last_event_id", "") or "")
            )
        except (TypeError, ValueError):
            return False

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
            if self._stored_event_is_safe(item, expected_robot_id=normalized_robot_id):
                events.append(item)
        return events

    def _save_snapshot(self, path, snapshot):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
            tmp.write(_json_dumps(snapshot, indent=2))
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)

    def _export_snapshot_cache(self, path, snapshot):
        try:
            self._save_snapshot(path, snapshot)
            self._last_snapshot_export_error = ""
            return True
        except OSError as exc:
            self._last_snapshot_export_error = str(exc)
            return False

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
            normalized_robot_id = event["subject"]["robot_id"]
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            try:
                snapshot_row = self._load_or_rebuild_db_snapshot(connection, normalized_robot_id)
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO runtime_events(robot_id, event_id, received_at, payload) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        normalized_robot_id,
                        event["event_id"],
                        event["received_at"],
                        _json_dumps(event, separators=(",", ":")),
                    ),
                )
                inserted = cursor.rowcount
                if not inserted:
                    row = connection.execute(
                        "SELECT rowid, payload FROM runtime_events WHERE robot_id = ? AND event_id = ?",
                        (normalized_robot_id, event["event_id"]),
                    ).fetchone()
                    try:
                        original = json.loads(row[1]) if row else event
                    except Exception as exc:
                        raise ValueError("stored duplicate event is invalid") from exc
                    if not self._stored_event_is_safe(original, expected_robot_id=normalized_robot_id):
                        raise ValueError("stored duplicate event is invalid")
                    rowid = int(row[0] or 0) if row else 0
                else:
                    original = event
                    rowid = int(cursor.lastrowid or 0)

                snapshot = snapshot_row["payload"]
                last_event_rowid = snapshot_row["last_event_rowid"]
                last_event_id = snapshot_row["last_event_id"]
                if inserted:
                    snapshot = self._apply_event(snapshot, event)
                    last_event_rowid = rowid
                    last_event_id = event["event_id"]

                snapshot_row = self._upsert_db_snapshot(
                    connection,
                    normalized_robot_id,
                    snapshot,
                    last_event_rowid=last_event_rowid,
                    last_event_id=last_event_id,
                )
                if inserted:
                    event_count = connection.execute(
                        "SELECT COUNT(*) FROM runtime_events WHERE robot_id = ?",
                        (normalized_robot_id,),
                    ).fetchone()[0]
                    excess = int(event_count or 0) - self.max_events_per_robot
                    if excess > 0:
                        connection.execute(
                            "DELETE FROM runtime_events WHERE rowid IN ("
                            "SELECT rowid FROM runtime_events WHERE robot_id = ? ORDER BY rowid ASC LIMIT ?"
                            ")",
                            (normalized_robot_id, excess),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

            self._export_snapshot_cache(self._snapshot_path(normalized_robot_id), snapshot_row["payload"])
            if inserted:
                # The SSE bus is process-local best-effort. SQLite journal and
                # runtime_snapshots are authoritative; JSON export failure must
                # not suppress the one in-process publication for new events.
                self._cursor += 1
                self._bus.append((self._cursor, event))
                self._condition.notify_all()
                return event, snapshot_row["payload"], False
            return original, snapshot_row["payload"], True

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
