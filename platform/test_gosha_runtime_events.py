#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from pathlib import Path

from gosha_runtime_events import (
    EVENT_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    RuntimeEventStore,
    _json_digest,
    normalize_event,
)


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


def test_db_snapshot_preserves_lifetime_state_after_journal_pruning_and_cache_loss():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir, max_events_per_robot=100)
        bootstrap = sample_event("pruned-bootstrap-0")
        bootstrap["event_type"] = "service.runtime.started"
        bootstrap["source"]["instance_id"] = "bootstrap-process"
        bootstrap["state"]["status"] = "bootstrapped"
        bootstrap["task"] = {"id": "provisioning", "kind": "bootstrap", "status": "running"}
        bootstrap.pop("error")
        store.record(bootstrap, robot_id="robot-01", source_kind="service", source_id="bootstrap-service")

        for index in range(1, 120):
            item = sample_event(f"retention-state-{index}")
            item["event_type"] = "mobile.runtime.heartbeat"
            item["source"]["instance_id"] = "mobile-process"
            item["state"]["status"] = f"heartbeat-{index}"
            item.pop("task")
            item.pop("error")
            store.record(item, robot_id="robot-01", source_kind="mobile", source_id="installation-01")

        events = store.list_events("robot-01", limit=500)
        assert len(events) == 100
        assert all(item["event_id"] != "pruned-bootstrap-0" for item in events)

        snapshot_path = store._snapshot_path("robot-01")
        snapshot_path.unlink()
        recovered = store.snapshot("robot-01")
        assert recovered["statistics"]["events_total"] == 120
        assert recovered["components"]["service"][0]["state"]["status"] == "bootstrapped"
        assert recovered["tasks"]["active"][0]["id"] == "provisioning"

        snapshot_path.write_text("{corrupted-json", encoding="utf-8")
        healed = store.snapshot("robot-01")
        assert healed["statistics"]["events_total"] == 120
        assert healed["components"]["service"][0]["state"]["status"] == "bootstrapped"
        assert healed["tasks"]["active"][0]["id"] == "provisioning"


def build_pruned_legacy_fixture(temp_dir):
    store = RuntimeEventStore(temp_dir, max_events_per_robot=100)
    bootstrap = sample_event("legacy-bootstrap-0")
    bootstrap["event_type"] = "service.runtime.started"
    bootstrap["source"]["instance_id"] = "legacy-bootstrap-process"
    bootstrap["state"]["status"] = "legacy-bootstrapped"
    bootstrap["task"] = {"id": "legacy-provisioning", "kind": "bootstrap", "status": "running"}
    bootstrap.pop("error")
    store.record(bootstrap, robot_id="robot-01", source_kind="service", source_id="bootstrap-service")
    for index in range(1, 120):
        item = sample_event(f"legacy-retained-{index}")
        item["event_type"] = "mobile.runtime.heartbeat"
        item["source"]["instance_id"] = "legacy-mobile-process"
        item["state"]["status"] = f"legacy-heartbeat-{index}"
        item.pop("task")
        item.pop("error")
        store.record(item, robot_id="robot-01", source_kind="mobile", source_id="installation-01")
    connection = store._connect()
    connection.execute("DROP TABLE runtime_snapshots")
    connection.commit()
    connection.close()
    store._connection = None
    return store._snapshot_path("robot-01")


def assert_db_snapshot_row_is_consistent(temp_dir, expected_total):
    db_path = Path(temp_dir) / "events.sqlite3"
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT payload, last_event_rowid, last_event_id, events_total, payload_digest "
            "FROM runtime_snapshots WHERE robot_id = ?",
            ("robot-01",),
        ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    projection = payload["projection"]
    assert row[4] == _json_digest(payload)
    assert int(row[1]) == int(projection["last_event_rowid"])
    assert row[2] == projection["last_event_id"]
    assert int(row[3]) == expected_total
    assert int(projection["events_total"]) == expected_total


def test_pre_upgrade_valid_legacy_snapshot_seeds_db_projection_after_pruning():
    with tempfile.TemporaryDirectory() as temp_dir:
        build_pruned_legacy_fixture(temp_dir)
        upgraded = RuntimeEventStore(temp_dir, max_events_per_robot=100)

        snapshot = upgraded.snapshot("robot-01")
        assert snapshot["statistics"]["events_total"] == 120
        assert snapshot["components"]["service"][0]["state"]["status"] == "legacy-bootstrapped"
        assert snapshot["tasks"]["active"][0]["id"] == "legacy-provisioning"
        assert len(upgraded.list_events("robot-01", limit=500)) == 100
        assert_db_snapshot_row_is_consistent(temp_dir, expected_total=120)


def test_pre_upgrade_valid_legacy_snapshot_is_seeded_before_first_new_record():
    with tempfile.TemporaryDirectory() as temp_dir:
        build_pruned_legacy_fixture(temp_dir)
        upgraded = RuntimeEventStore(temp_dir, max_events_per_robot=100)
        new_event = sample_event("legacy-new-120")
        new_event["event_type"] = "mobile.runtime.heartbeat"
        new_event["source"]["instance_id"] = "legacy-mobile-process"
        new_event["state"]["status"] = "legacy-new-state"
        new_event.pop("task")
        new_event.pop("error")

        _, snapshot, duplicate = upgraded.record(
            new_event,
            robot_id="robot-01",
            source_kind="mobile",
            source_id="installation-01",
        )
        assert not duplicate
        assert snapshot["statistics"]["events_total"] == 121
        assert snapshot["components"]["service"][0]["state"]["status"] == "legacy-bootstrapped"
        assert snapshot["tasks"]["active"][0]["id"] == "legacy-provisioning"
        assert snapshot["components"]["mobile"][0]["state"]["status"] == "legacy-new-state"
        assert_db_snapshot_row_is_consistent(temp_dir, expected_total=121)


def test_pre_projection_valid_legacy_snapshot_migrates_and_records_once():
    with tempfile.TemporaryDirectory() as temp_dir:
        snapshot_path = build_pruned_legacy_fixture(temp_dir)
        legacy = json.loads(snapshot_path.read_text(encoding="utf-8"))
        legacy.pop("projection", None)
        snapshot_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

        upgraded = RuntimeEventStore(temp_dir, max_events_per_robot=100)
        snapshot = upgraded.snapshot("robot-01")
        assert snapshot["statistics"]["events_total"] == 120
        assert snapshot["components"]["service"][0]["state"]["status"] == "legacy-bootstrapped"
        assert snapshot["tasks"]["active"][0]["id"] == "legacy-provisioning"
        assert_db_snapshot_row_is_consistent(temp_dir, expected_total=120)

        next_event = sample_event("legacy-no-projection-120")
        next_event["event_type"] = "mobile.runtime.heartbeat"
        next_event["source"]["instance_id"] = "legacy-mobile-process"
        next_event["state"]["status"] = "legacy-no-projection-new-state"
        next_event.pop("task")
        next_event.pop("error")
        _, snapshot, duplicate = upgraded.record(
            next_event,
            robot_id="robot-01",
            source_kind="mobile",
            source_id="installation-01",
        )

        assert not duplicate
        assert snapshot["statistics"]["events_total"] == 121
        assert snapshot["components"]["service"][0]["state"]["status"] == "legacy-bootstrapped"
        assert snapshot["tasks"]["active"][0]["id"] == "legacy-provisioning"
        assert snapshot["components"]["mobile"][0]["state"]["status"] == "legacy-no-projection-new-state"
        assert_db_snapshot_row_is_consistent(temp_dir, expected_total=121)


def test_stale_corrupt_or_mismatched_legacy_snapshot_is_rejected():
    variants = ("stale-tip", "corrupt-json", "wrong-robot")
    for variant in variants:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = build_pruned_legacy_fixture(temp_dir)
            if variant == "stale-tip":
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                snapshot["recent_events"][0]["event_id"] = "not-the-journal-tip"
                snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            elif variant == "corrupt-json":
                snapshot_path.write_text("{not-json", encoding="utf-8")
            else:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                snapshot["robot_id"] = "other-robot"
                snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

            upgraded = RuntimeEventStore(temp_dir, max_events_per_robot=100)
            snapshot = upgraded.snapshot("robot-01")
            assert snapshot["statistics"]["events_total"] == 100
            assert snapshot["components"]["service"] == []
            assert snapshot["tasks"]["active"] == []
            assert_db_snapshot_row_is_consistent(temp_dir, expected_total=100)


def test_malformed_same_tip_legacy_snapshot_is_rejected_and_next_event_succeeds():
    def set_by_type_to_list(snapshot):
        snapshot["statistics"]["by_type"] = []

    def set_by_source_kind_bad_bool_counter(snapshot):
        snapshot["statistics"]["by_source_kind"]["mobile"] = True

    def set_by_severity_bad_negative_counter(snapshot):
        snapshot["statistics"]["by_severity"]["warning"] = -1

    def set_by_type_bad_key(snapshot):
        snapshot["statistics"]["by_type"]["bad key"] = 1

    def set_events_total_bool(snapshot):
        snapshot["statistics"]["events_total"] = True

    def set_events_total_huge(snapshot):
        snapshot["statistics"]["events_total"] = 10**100

    def add_statistics_url(snapshot):
        snapshot["statistics"]["diagnostic_url"] = "https://internal.example.invalid/diagnostics"

    def set_counter_huge(snapshot):
        snapshot["statistics"]["by_type"]["mobile.runtime.heartbeat"] = 10**100

    def set_projection_rowid_huge(snapshot):
        snapshot["projection"]["last_event_rowid"] = 10**100

    def set_component_kind_to_non_list(snapshot):
        snapshot["components"]["mobile"] = {}

    def set_component_item_to_non_dict(snapshot):
        snapshot["components"]["mobile"][0] = []

    def set_component_state_to_non_dict(snapshot):
        snapshot["components"]["mobile"][0]["state"] = []

    def set_component_state_to_nan(snapshot):
        snapshot["components"]["mobile"][0]["state"]["status"] = float("nan")

    def set_component_state_status_to_list(snapshot):
        snapshot["components"]["mobile"][0]["state"]["status"] = []

    def set_component_sequence_huge(snapshot):
        snapshot["components"]["mobile"][0]["sequence"] = 10**100

    def set_component_all_item_to_non_dict(snapshot):
        snapshot["components"]["all"] = [[]]

    def add_component_secret_fields(snapshot):
        snapshot["components"]["mobile"][0]["websocket_url"] = "ws://internal.example.invalid/voice"
        snapshot["components"]["mobile"][0]["api_key"] = "raw-secret"

    def set_link_item_to_non_dict(snapshot):
        snapshot["links"][0] = []

    def set_link_kind_to_list(snapshot):
        snapshot["links"][0]["kind"] = []

    def set_task_active_item_to_non_dict(snapshot):
        snapshot["tasks"]["active"][0] = []

    def add_task_container_secret(snapshot):
        snapshot["tasks"][" credential "] = "raw-secret"

    def set_task_recent_item_to_non_dict(snapshot):
        snapshot["tasks"]["recent"][0] = []

    def set_task_id_to_list(snapshot):
        snapshot["tasks"]["recent"][0]["id"] = []

    def set_error_recent_item_to_non_dict(snapshot):
        snapshot["errors"]["recent"] = [[]]

    def set_error_code_to_dict(snapshot):
        latest = snapshot["recent_events"][0]
        snapshot["errors"]["recent"] = [
            {
                "event_id": latest["event_id"],
                "event_type": latest["event_type"],
                "severity": latest["severity"],
                "source_kind": latest["source"]["kind"],
                "source_id": latest["source"]["id"],
                "occurred_at": latest["occurred_at"],
                "code": {},
            }
        ]

    def set_recent_event_item_to_non_dict(snapshot):
        snapshot["recent_events"].append([])

    def set_recent_event_source_to_list(snapshot):
        snapshot["recent_events"][0]["source"] = []

    def set_recent_event_link_kind_to_list(snapshot):
        snapshot["recent_events"][0]["link"]["kind"] = []

    def set_recent_event_foreign_robot(snapshot):
        snapshot["recent_events"][0]["subject"]["robot_id"] = "other-robot"

    def set_recent_event_secret_field(snapshot):
        snapshot["recent_events"][0]["attributes"] = {"token": "must-not-survive"}

    def add_unknown_top_level_key(snapshot):
        snapshot["__proto__"] = {"polluted": True}

    def add_unknown_projection_key(snapshot):
        snapshot["projection"]["__proto__"] = {"polluted": True}

    cases = (
        ("by-type-list", set_by_type_to_list),
        ("by-source-kind-bool-counter", set_by_source_kind_bad_bool_counter),
        ("by-severity-negative-counter", set_by_severity_bad_negative_counter),
        ("by-type-bad-key", set_by_type_bad_key),
        ("events-total-bool", set_events_total_bool),
        ("events-total-huge", set_events_total_huge),
        ("statistics-url", add_statistics_url),
        ("counter-huge", set_counter_huge),
        ("projection-rowid-huge", set_projection_rowid_huge),
        ("component-kind-non-list", set_component_kind_to_non_list),
        ("component-item-non-dict", set_component_item_to_non_dict),
        ("component-state-non-dict", set_component_state_to_non_dict),
        ("component-state-nan", set_component_state_to_nan),
        ("component-state-status-list", set_component_state_status_to_list),
        ("component-sequence-huge", set_component_sequence_huge),
        ("component-all-item-non-dict", set_component_all_item_to_non_dict),
        ("component-secret-fields", add_component_secret_fields),
        ("link-item-non-dict", set_link_item_to_non_dict),
        ("link-kind-list", set_link_kind_to_list),
        ("task-active-item-non-dict", set_task_active_item_to_non_dict),
        ("task-container-secret", add_task_container_secret),
        ("task-recent-item-non-dict", set_task_recent_item_to_non_dict),
        ("task-id-list", set_task_id_to_list),
        ("error-recent-item-non-dict", set_error_recent_item_to_non_dict),
        ("error-code-dict", set_error_code_to_dict),
        ("recent-event-item-non-dict", set_recent_event_item_to_non_dict),
        ("recent-event-source-list", set_recent_event_source_to_list),
        ("recent-event-link-kind-list", set_recent_event_link_kind_to_list),
        ("recent-event-foreign-robot", set_recent_event_foreign_robot),
        ("recent-event-secret-field", set_recent_event_secret_field),
        ("unknown-top-level-key", add_unknown_top_level_key),
        ("unknown-projection-key", add_unknown_projection_key),
    )
    for case_name, mutate in cases:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = build_pruned_legacy_fixture(temp_dir)
            legacy = json.loads(snapshot_path.read_text(encoding="utf-8"))
            mutate(legacy)
            snapshot_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

            upgraded = RuntimeEventStore(temp_dir, max_events_per_robot=100)
            snapshot = upgraded.snapshot("robot-01")
            assert snapshot["statistics"]["events_total"] == 100, case_name
            assert snapshot["components"]["service"] == [], case_name
            assert snapshot["tasks"]["active"] == [], case_name

            next_event = sample_event(f"malformed-legacy-{case_name}-120")
            next_event["event_type"] = "mobile.runtime.heartbeat"
            next_event["source"]["instance_id"] = "legacy-mobile-process"
            next_event["state"]["status"] = f"after-{case_name}"
            next_event.pop("task")
            next_event.pop("error")
            _, snapshot, duplicate = upgraded.record(
                next_event,
                robot_id="robot-01",
                source_kind="mobile",
                source_id="installation-01",
            )
            assert not duplicate, case_name
            assert snapshot["statistics"]["events_total"] == 101, case_name
            assert snapshot["components"]["mobile"][0]["state"]["status"] == f"after-{case_name}", case_name
            assert_db_snapshot_row_is_consistent(temp_dir, expected_total=101)


def test_fresh_events_reject_non_finite_and_out_of_range_numbers():
    invalid_payloads = []
    for value in (float("nan"), float("inf"), float("-inf")):
        state_payload = sample_event(f"invalid-state-{len(invalid_payloads)}")
        state_payload["state"]["status"] = value
        invalid_payloads.append(state_payload)

        metrics_payload = sample_event(f"invalid-metrics-{len(invalid_payloads)}")
        metrics_payload["metrics"]["retry_count"] = value
        invalid_payloads.append(metrics_payload)

    huge_metric = sample_event("invalid-huge-metric")
    huge_metric["metrics"]["retry_count"] = 10**100
    invalid_payloads.append(huge_metric)

    huge_sequence = sample_event("invalid-huge-sequence")
    huge_sequence["sequence"] = 10**100
    invalid_payloads.append(huge_sequence)

    for payload in invalid_payloads:
        try:
            normalize_event(
                payload,
                robot_id="robot-01",
                source_kind="mobile",
                source_id="installation-01",
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid numeric value was accepted: {payload['event_id']}")


def test_invalid_retained_journal_event_is_quarantined_from_projection_and_duplicates():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        kwargs = dict(robot_id="robot-01", source_kind="mobile", source_id="installation-01")
        poisoned_payload = sample_event("poisoned-retained-event")
        store.record(poisoned_payload, **kwargs)

        database = store._connect()
        poisoned_event = json.loads(
            database.execute(
                "SELECT payload FROM runtime_events WHERE robot_id = ? AND event_id = ?",
                ("robot-01", "poisoned-retained-event"),
            ).fetchone()[0]
        )
        poisoned_event["link"]["kind"] = []
        poisoned_event["metrics"]["retry_count"] = float("nan")
        database.execute(
            "UPDATE runtime_events SET payload = ? WHERE robot_id = ? AND event_id = ?",
            (json.dumps(poisoned_event, ensure_ascii=False), "robot-01", "poisoned-retained-event"),
        )
        database.execute("DROP TABLE runtime_snapshots")
        database.commit()
        database.close()
        store._connection = None
        store._snapshot_path("robot-01").unlink()

        restarted = RuntimeEventStore(temp_dir)
        rebuilt = restarted.snapshot("robot-01")
        assert rebuilt["statistics"]["events_total"] == 0
        assert restarted.list_events("robot-01") == []

        try:
            restarted.record(poisoned_payload, **kwargs)
        except ValueError as exc:
            assert "stored duplicate event is invalid" in str(exc)
        else:
            raise AssertionError("invalid stored duplicate must fail closed")

        healthy_payload = sample_event("healthy-after-poison")
        _, healthy_snapshot, duplicate = restarted.record(healthy_payload, **kwargs)
        assert not duplicate
        assert healthy_snapshot["statistics"]["events_total"] == 1
        assert healthy_snapshot["recent_events"][0]["event_id"] == "healthy-after-poison"


def test_secret_bearing_or_foreign_retained_events_fail_closed():
    def add_forbidden_top_level(event):
        event["websocket_url"] = "ws://internal.example.invalid/xiaozhi/v1/"

    def add_nested_token(event):
        event.setdefault("attributes", {})["nested"] = {"token": "must-not-survive"}

    def add_disguised_token(event):
        event.setdefault("attributes", {})[" token "] = "must-not-survive"

    def add_raw_secret_value(event):
        event.setdefault("attributes", {})["api_key"] = "raw-secret-value"

    def change_subject_robot(event):
        event["subject"]["robot_id"] = "other-robot"

    cases = (
        ("forbidden-top-level", add_forbidden_top_level),
        ("nested-token", add_nested_token),
        ("disguised-token", add_disguised_token),
        ("raw-secret-value", add_raw_secret_value),
        ("foreign-subject", change_subject_robot),
    )
    for case_name, mutate in cases:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RuntimeEventStore(temp_dir)
            event_id = f"poison-{case_name}"
            payload = sample_event(event_id)
            kwargs = dict(robot_id="robot-01", source_kind="mobile", source_id="installation-01")
            store.record(payload, **kwargs)

            database = store._connect()
            retained = json.loads(
                database.execute(
                    "SELECT payload FROM runtime_events WHERE robot_id = ? AND event_id = ?",
                    ("robot-01", event_id),
                ).fetchone()[0]
            )
            mutate(retained)
            database.execute(
                "UPDATE runtime_events SET payload = ? WHERE robot_id = ? AND event_id = ?",
                (json.dumps(retained, ensure_ascii=False), "robot-01", event_id),
            )
            database.execute("DROP TABLE runtime_snapshots")
            database.commit()
            database.close()
            store._connection = None
            store._snapshot_path("robot-01").unlink()

            restarted = RuntimeEventStore(temp_dir)
            assert restarted.list_events("robot-01") == [], case_name
            rebuilt = restarted.snapshot("robot-01")
            assert rebuilt["statistics"]["events_total"] == 0, case_name
            assert rebuilt["recent_events"] == [], case_name

            try:
                restarted.record(payload, **kwargs)
            except ValueError as exc:
                assert "stored duplicate event is invalid" in str(exc), case_name
            else:
                raise AssertionError(f"poisoned duplicate must fail closed: {case_name}")


def test_secret_bearing_db_snapshot_is_rejected_even_with_matching_digest():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        payload = sample_event("hostile-db-snapshot-1")
        payload.pop("error")
        store.record(
            payload,
            robot_id="robot-01",
            source_kind="mobile",
            source_id="installation-01",
        )

        database = store._connect()
        snapshot = json.loads(
            database.execute(
                "SELECT payload FROM runtime_snapshots WHERE robot_id = ?",
                ("robot-01",),
            ).fetchone()[0]
        )
        component = snapshot["components"]["mobile"][0]
        component["websocket_url"] = "ws://internal.example.invalid/voice"
        component["api_key"] = "raw-secret"
        database.execute(
            "UPDATE runtime_snapshots SET payload = ?, payload_digest = ? WHERE robot_id = ?",
            (
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                _json_digest(snapshot),
                "robot-01",
            ),
        )
        database.commit()
        database.close()
        store._connection = None
        store._snapshot_path("robot-01").unlink()

        rebuilt = RuntimeEventStore(temp_dir).snapshot("robot-01")
        rebuilt_component = rebuilt["components"]["mobile"][0]
        assert rebuilt["statistics"]["events_total"] == 1
        assert "websocket_url" not in rebuilt_component
        assert "api_key" not in rebuilt_component


def test_legacy_snapshot_validator_returns_false_for_hostile_shapes():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        hostile_values = (
            None,
            True,
            0,
            "snapshot",
            [],
            {"schema_version": SNAPSHOT_SCHEMA_VERSION, "robot_id": "robot-01", "components": []},
            {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "robot_id": "robot-01",
                "updated_at": [],
                "components": {"mobile": []},
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
            },
            {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "robot_id": "robot-01",
                "updated_at": "",
                "projection": {"kind": "sqlite-runtime-snapshot.v1", "last_event_rowid": [], "last_event_id": "", "events_total": 0},
                "components": {"mobile": []},
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
            },
        )
        for value in hostile_values:
            assert store._legacy_snapshot_shape_is_safe(value, "robot-01") is False


def test_corrupt_snapshot_cache_with_matching_tip_is_healed_by_digest():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        connected = sample_event("digest-heal-1")
        connected["state"]["status"] = "connected"
        connected.pop("error")
        store.record(connected, robot_id="robot-01", source_kind="mobile", source_id="installation-01")

        snapshot_path = store._snapshot_path("robot-01")
        corrupted = json.loads(snapshot_path.read_text(encoding="utf-8"))
        corrupted["components"]["mobile"][0]["state"]["status"] = "corrupted-but-same-tip"
        snapshot_path.write_text(json.dumps(corrupted, ensure_ascii=False), encoding="utf-8")

        healed = store.snapshot("robot-01")
        assert healed["components"]["mobile"][0]["state"]["status"] == "connected"
        healed_file = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert healed_file["components"]["mobile"][0]["state"]["status"] == "connected"


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


def test_snapshot_projection_recovers_after_write_failure_restart_and_retry():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        kwargs = dict(robot_id="robot-01", source_kind="mobile", source_id="installation-01")

        running = sample_event("recoverable-task:1")
        running["source"]["instance_id"] = "app-process-1"
        running["sequence"] = 1
        running["state"]["status"] = "recovering"
        running["task"]["status"] = "running"
        store.record(running, **kwargs)

        completed = sample_event("recoverable-task:2")
        completed["source"]["instance_id"] = "app-process-1"
        completed["sequence"] = 2
        completed["state"]["status"] = "connected"
        completed["link"]["status"] = "available"
        completed["task"]["status"] = "completed"
        completed.pop("error")

        original_save_snapshot = store._save_snapshot

        def fail_snapshot_once(path, snapshot):
            store._save_snapshot = original_save_snapshot
            raise OSError("simulated snapshot write failure")

        store._save_snapshot = fail_snapshot_once
        event, snapshot, duplicate = store.record(completed, **kwargs)
        assert not duplicate
        assert event["event_id"] == "recoverable-task:2"
        assert snapshot["components"]["mobile"][0]["state"]["status"] == "connected"
        assert snapshot["tasks"]["active"] == []
        assert "simulated snapshot write failure" in store._last_snapshot_export_error

        restarted = RuntimeEventStore(temp_dir)
        event, snapshot, duplicate = restarted.record(completed, **kwargs)

        assert duplicate
        assert event["event_id"] == "recoverable-task:2"
        assert snapshot["components"]["mobile"][0]["state"]["status"] == "connected"
        assert snapshot["links"][0]["status"] == "available"
        assert snapshot["tasks"]["active"] == []
        assert snapshot["tasks"]["recent"][0]["status"] == "completed"
        assert snapshot["tasks"]["recent"][0]["sequence"] == 2

        recovered = restarted.snapshot("robot-01")
        assert recovered["components"]["mobile"][0]["state"]["status"] == "connected"
        assert recovered["tasks"]["active"] == []
        assert recovered["tasks"]["recent"][0]["status"] == "completed"


def test_retention_boundary_export_failure_duplicate_uses_db_projection_without_double_apply():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir, max_events_per_robot=100)
        kwargs = dict(robot_id="robot-01", source_kind="mobile", source_id="installation-01")
        for index in range(100):
            item = sample_event(f"boundary-{index}")
            item["event_type"] = "mobile.runtime.heartbeat"
            item["source"]["instance_id"] = "mobile-process"
            item["state"]["status"] = f"before-boundary-{index}"
            item.pop("task")
            item.pop("error")
            store.record(item, **kwargs)

        boundary = sample_event("boundary-100")
        boundary["source"]["instance_id"] = "mobile-process"
        boundary["sequence"] = 100
        boundary["state"]["status"] = "event-101"
        boundary["link"]["status"] = "available"
        boundary["task"]["status"] = "completed"
        boundary.pop("error")

        original_save_snapshot = store._save_snapshot

        def fail_snapshot_once(path, snapshot):
            store._save_snapshot = original_save_snapshot
            raise OSError("simulated retention-boundary export failure")

        store._save_snapshot = fail_snapshot_once
        event, snapshot, duplicate = store.record(boundary, **kwargs)
        assert not duplicate
        assert event["event_id"] == "boundary-100"
        assert snapshot["statistics"]["events_total"] == 101
        assert snapshot["components"]["mobile"][0]["state"]["status"] == "event-101"
        assert "retention-boundary" in store._last_snapshot_export_error

        restarted = RuntimeEventStore(temp_dir, max_events_per_robot=100)
        event, snapshot, duplicate = restarted.record(boundary, **kwargs)
        assert duplicate
        assert event["event_id"] == "boundary-100"
        assert snapshot["statistics"]["events_total"] == 101
        assert snapshot["components"]["mobile"][0]["state"]["status"] == "event-101"
        assert snapshot["links"][0]["status"] == "available"
        assert snapshot["tasks"]["active"] == []
        assert snapshot["tasks"]["recent"][0]["status"] == "completed"
        assert len(restarted.list_events("robot-01", limit=500)) == 100


def test_persistent_snapshot_export_failure_keeps_db_read_and_sse_best_effort():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RuntimeEventStore(temp_dir)
        event_payload = sample_event("persistent-export-failure-1")

        def always_fail_snapshot_export(path, snapshot):
            raise OSError("persistent snapshot export failure")

        store._save_snapshot = always_fail_snapshot_export
        event, snapshot, duplicate = store.record(
            event_payload,
            robot_id="robot-01",
            source_kind="mobile",
            source_id="installation-01",
        )
        assert not duplicate
        assert event["event_id"] == "persistent-export-failure-1"
        assert snapshot["statistics"]["events_total"] == 1
        assert "persistent snapshot export failure" in store._last_snapshot_export_error

        cursor, published = store.wait_for_events(0, timeout=0)
        assert cursor == 1
        assert published[0][1]["event_id"] == "persistent-export-failure-1"

        read_snapshot = store.snapshot("robot-01")
        assert read_snapshot["statistics"]["events_total"] == 1
        assert "persistent snapshot export failure" in store._last_snapshot_export_error

        _, duplicate_snapshot, duplicate = store.record(
            event_payload,
            robot_id="robot-01",
            source_kind="mobile",
            source_id="installation-01",
        )
        assert duplicate
        assert duplicate_snapshot["statistics"]["events_total"] == 1
        next_cursor, duplicate_published = store.wait_for_events(cursor, timeout=0)
        assert next_cursor == cursor
        assert duplicate_published == []

        healthy = RuntimeEventStore(temp_dir)
        snapshot = healthy.snapshot("robot-01")
        assert snapshot["statistics"]["events_total"] == 1
        assert len(healthy.list_events("robot-01")) == 1
        assert healthy._snapshot_path("robot-01").exists()


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
    test_db_snapshot_preserves_lifetime_state_after_journal_pruning_and_cache_loss()
    test_pre_upgrade_valid_legacy_snapshot_seeds_db_projection_after_pruning()
    test_pre_upgrade_valid_legacy_snapshot_is_seeded_before_first_new_record()
    test_pre_projection_valid_legacy_snapshot_migrates_and_records_once()
    test_stale_corrupt_or_mismatched_legacy_snapshot_is_rejected()
    test_malformed_same_tip_legacy_snapshot_is_rejected_and_next_event_succeeds()
    test_legacy_snapshot_validator_returns_false_for_hostile_shapes()
    test_fresh_events_reject_non_finite_and_out_of_range_numbers()
    test_invalid_retained_journal_event_is_quarantined_from_projection_and_duplicates()
    test_secret_bearing_or_foreign_retained_events_fail_closed()
    test_secret_bearing_db_snapshot_is_rejected_even_with_matching_digest()
    test_corrupt_snapshot_cache_with_matching_tip_is_healed_by_digest()
    test_late_retry_does_not_roll_back_component_or_link_state()
    test_late_retry_does_not_resurrect_completed_task()
    test_snapshot_projection_recovers_after_write_failure_restart_and_retry()
    test_retention_boundary_export_failure_duplicate_uses_db_projection_without_double_apply()
    test_persistent_snapshot_export_failure_keeps_db_read_and_sse_best_effort()
    test_sse_cursor_recovers_after_process_restart()
    print("runtime triangle event tests: OK")


if __name__ == "__main__":
    main()
