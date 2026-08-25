#!/usr/bin/env python3
import tempfile
from pathlib import Path

from gosha_runtime_events import EVENT_SCHEMA_VERSION, RuntimeEventStore, normalize_event


def sample_event(event_id="mobile-install-1:1"):
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": "mobile.network.state_changed",
        "source": {"instance_id": "process-1", "app_version": "1.2.3"},
        "subject": {"robot_id": "forged-robot", "client_id": "mobile-client", "panel_id": "forged-panel"},
        "trace": {"session_id": "session-1", "correlation_id": "recovery-1"},
        "severity": "warning",
        "state": {"domain": "connectivity", "name": "home_wifi", "status": "lost"},
        "link": {"kind": "mobile_robot", "status": "unavailable"},
        "task": {"id": "recovery-1", "kind": "wifi_recovery", "status": "running"},
        "error": {"code": "wifi_lost", "message": "Домашняя сеть временно недоступна", "retryable": True},
        "metrics": {"retry_count": 1},
        "attributes": {
            "token": "must-not-survive",
            "panel_client_token": "must-not-survive-either",
            "api_key": "sk-opaque-value",
            "accessKey": "ak-opaque-value",
            "diagnostic_url": "http://internal.invalid",
            "credential": "Bearer must-not-survive-in-a-value",
            "nested": {"private_key": "opaque-private-key", "safe": "nested-yes"},
            "note": "internal endpoint http://internal.invalid/path",
            "network_alias": "ssid=private-network-name",
            "safe": "yes",
        },
    }


def test_server_context_overrides_untrusted_identity_and_redacts_secrets():
    event = normalize_event(
        sample_event(),
        robot_id="robot-01",
        source_kind="mobile",
        source_id="installation-01",
        client_id="authenticated-client",
    )
    assert event["subject"]["robot_id"] == "robot-01"
    assert event["subject"]["client_id"] == "authenticated-client"
    assert "panel_id" not in event["subject"]
    assert event["source"]["kind"] == "mobile"
    assert event["source"]["id"] == "installation-01"
    assert "token" not in event["attributes"]
    assert "panel_client_token" not in event["attributes"]
    assert event["attributes"]["api_key"] == "[redacted]"
    assert event["attributes"]["accessKey"] == "[redacted]"
    assert "diagnostic_url" not in event["attributes"]
    assert event["attributes"]["credential"] == "[redacted]"
    assert event["attributes"]["nested"]["private_key"] == "[redacted]"
    assert event["attributes"]["nested"]["safe"] == "nested-yes"
    assert event["attributes"]["note"] == "[redacted]"
    assert event["attributes"]["network_alias"] == "[redacted]"
    assert event["attributes"]["safe"] == "yes"


def test_sensitive_values_are_redacted_outside_attribute_maps():
    payload = sample_event("sensitive-values-1")
    payload["source"]["app_version"] = "build from http://internal.invalid/app"
    payload["occurred_at"] = "Bearer must-not-survive"
    event = normalize_event(
        payload,
        robot_id="robot-01",
        source_kind="mobile",
        source_id="installation-01",
    )
    assert event["source"]["app_version"] == "[redacted]"
    assert event["occurred_at"] == "[redacted]"


def test_store_aggregates_components_links_tasks_errors_and_statistics():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(Path(temp_dir))
        event, snapshot, duplicate = store.record(
            sample_event(),
            robot_id="robot-01",
            source_kind="mobile",
            source_id="installation-01",
            client_id="authenticated-client",
        )
        assert not duplicate
        assert snapshot["statistics"]["events_total"] == 1
        assert snapshot["components"]["mobile"][0]["id"] == "installation-01"
        assert snapshot["links"][0]["kind"] == "mobile_robot"
        assert snapshot["tasks"]["active"][0]["id"] == "recovery-1"
        assert snapshot["errors"]["recent"][0]["event_id"] == event["event_id"]
        assert store.list_events("robot-01", limit=10)[0]["event_id"] == event["event_id"]


def test_duplicate_event_is_idempotent():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        kwargs = dict(robot_id="robot-01", source_kind="mobile", source_id="installation-01")
        store.record(sample_event(), **kwargs)
        _, snapshot, duplicate = store.record(sample_event(), **kwargs)
        assert duplicate
        assert snapshot["statistics"]["events_total"] == 1
        assert len(store.list_events("robot-01")) == 1


def test_terminal_task_leaves_active_list():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        kwargs = dict(robot_id="robot-01", source_kind="mobile", source_id="installation-01")
        store.record(sample_event(), **kwargs)
        completed = sample_event("mobile-install-1:2")
        completed["task"]["status"] = "completed"
        completed.pop("error")
        _, snapshot, _ = store.record(completed, **kwargs)
        assert snapshot["tasks"]["active"] == []
        assert snapshot["tasks"]["recent"][0]["status"] == "completed"


def test_robot_id_cannot_escape_storage_root():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        try:
            store.record(
                sample_event(),
                robot_id="../outside",
                source_kind="mobile",
                source_id="installation-01",
            )
        except ValueError as exc:
            assert str(exc) == "invalid robot_id"
        else:
            raise AssertionError("path-like robot_id must be rejected")


def test_panel_identity_is_taken_from_authenticated_context():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        panel_event = sample_event("panel-event-1")
        panel_event["event_type"] = "panel.robot_workspace.opened"
        event, _, _ = store.record(
            panel_event,
            robot_id="robot-01",
            source_kind="panel",
            source_id="panel-session-abc123",
            panel_id="panel-session-abc123",
        )
        assert event["source"]["id"] == "panel-session-abc123"
        assert event["subject"]["panel_id"] == "panel-session-abc123"


def test_idempotency_survives_recent_event_window():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        kwargs = dict(robot_id="robot-01", source_kind="mobile", source_id="installation-01")
        first = sample_event("long-window-0")
        store.record(first, **kwargs)
        for index in range(1, 150):
            item = sample_event(f"long-window-{index}")
            store.record(item, **kwargs)
        _, snapshot, duplicate = store.record(first, **kwargs)
        assert duplicate
        assert snapshot["statistics"]["events_total"] == 150


def test_per_robot_journal_retention_is_bounded():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir, max_events_per_robot=100)
        kwargs = dict(robot_id="robot-01", source_kind="mobile", source_id="installation-01")
        for index in range(120):
            store.record(sample_event(f"retention-{index}"), **kwargs)
        events = store.list_events("robot-01", limit=500)
        assert len(events) == 100
        assert events[0]["event_id"] == "retention-20"
        assert store.snapshot("robot-01")["statistics"]["events_total"] == 120


def test_late_retry_does_not_roll_back_component_or_link_state():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        kwargs = dict(robot_id="robot-01", source_kind="robot", source_id="device-01")
        recovered = sample_event("robot-session:2")
        recovered["source"]["instance_id"] = "boot-1"
        recovered["sequence"] = 2
        recovered["state"]["status"] = "connected"
        recovered["link"]["status"] = "available"
        store.record(recovered, **kwargs)
        delayed_failure = sample_event("robot-session:1")
        delayed_failure["source"]["instance_id"] = "boot-1"
        delayed_failure["sequence"] = 1
        delayed_failure["state"]["status"] = "disconnected"
        delayed_failure["link"]["status"] = "unavailable"
        _, snapshot, _ = store.record(delayed_failure, **kwargs)
        assert snapshot["components"]["robot"][0]["state"]["status"] == "connected"
        assert snapshot["links"][0]["status"] == "available"


def test_late_retry_does_not_resurrect_completed_task():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        kwargs = dict(robot_id="robot-01", source_kind="mobile", source_id="installation-01")
        completed = sample_event("task-session:2")
        completed["source"]["instance_id"] = "app-process-1"
        completed["sequence"] = 2
        completed["task"]["status"] = "completed"
        completed.pop("error")
        store.record(completed, **kwargs)

        delayed_running = sample_event("task-session:1")
        delayed_running["source"]["instance_id"] = "app-process-1"
        delayed_running["sequence"] = 1
        delayed_running["task"]["status"] = "running"
        _, snapshot, _ = store.record(delayed_running, **kwargs)

        assert snapshot["tasks"]["active"] == []
        assert snapshot["tasks"]["recent"][0]["status"] == "completed"
        assert snapshot["tasks"]["recent"][0]["sequence"] == 2


def test_sse_cursor_recovers_after_process_restart():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        store.record(
            sample_event("restart-event-1"),
            robot_id="robot-01",
            source_kind="mobile",
            source_id="installation-01",
        )
        cursor, events = store.wait_for_events(1000, timeout=0)
        assert cursor == 1
        assert events[0][1]["event_id"] == "restart-event-1"


def main():
    test_server_context_overrides_untrusted_identity_and_redacts_secrets()
    test_sensitive_values_are_redacted_outside_attribute_maps()
    test_store_aggregates_components_links_tasks_errors_and_statistics()
    test_duplicate_event_is_idempotent()
    test_terminal_task_leaves_active_list()
    test_robot_id_cannot_escape_storage_root()
    test_panel_identity_is_taken_from_authenticated_context()
    test_idempotency_survives_recent_event_window()
    test_per_robot_journal_retention_is_bounded()
    test_late_retry_does_not_roll_back_component_or_link_state()
    test_late_retry_does_not_resurrect_completed_task()
    test_sse_cursor_recovers_after_process_restart()
    print("runtime triangle event tests: OK")


if __name__ == "__main__":
    main()
