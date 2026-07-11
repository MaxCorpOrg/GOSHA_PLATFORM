#!/usr/bin/env python3
from pathlib import Path

from gui_panel import edge_hub_transport_diagnostics


PANEL_HTML = Path(__file__).with_name("panel_index.html")


def assert_transport(name, status, expected_state, expected_age=None):
    result = edge_hub_transport_diagnostics(status)
    actual_state = result.get("transport_state")
    if actual_state != expected_state:
        raise AssertionError(f"{name}: expected {expected_state!r}, got {actual_state!r}")
    if expected_age is not None and result.get("transport_cache_age_ms") != expected_age:
        raise AssertionError(f"{name}: expected cache age {expected_age!r}, got {result.get('transport_cache_age_ms')!r}")
    return result


def test_executed_uses_reported_states():
    assert_transport("executed ready", {"robot_ws_probe_state": "executed", "robot_ws_ok": True}, "reported-ready")
    assert_transport(
        "executed unreachable",
        {"robot_ws_probe_state": "executed", "robot_ws_ok": False},
        "reported-unreachable",
    )


def test_skipped_uses_explicit_cached_states_and_age():
    assert_transport(
        "skipped ready",
        {"robot_ws_probe_state": "skipped", "robot_ws_ok": True, "robot_ws_probe_cached_age_ms": 42000},
        "cached-ready",
        expected_age=42000,
    )
    assert_transport(
        "skipped unreachable",
        {"robot_ws_probe_state": "skipped", "robot_ws_ok": False, "robot_ws_probe_cached_age_ms": "65000"},
        "cached-unreachable",
        expected_age=65000,
    )


def test_stale_never_reports_ready():
    ready = assert_transport(
        "stale ready",
        {"robot_ws_probe_state": "stale", "robot_ws_ok": True, "robot_ws_probe_cached_age_ms": 300000},
        "stale-ready",
        expected_age=300000,
    )
    if ready.get("transport_state") == "reported-ready":
        raise AssertionError("stale ready must not be reported-ready")
    assert_transport(
        "stale unreachable",
        {"robot_ws_probe_state": "stale", "robot_ws_ok": False},
        "stale-unreachable",
    )


def test_legacy_robot_ws_ok_is_preserved():
    assert_transport("legacy ready", {"robot_ws_ok": True}, "reported-ready")
    assert_transport("legacy unreachable", {"robot_ws_ok": False}, "reported-unreachable")


def test_panel_cache_age_missing_values_are_not_zero():
    source = PANEL_HTML.read_text(encoding="utf-8")
    function_start = source.index("function formatCacheAgeMs(value)")
    number_call = source.index("const ms = Number(value);", function_start)
    missing_null_guard = source.index('if (value === null || value === undefined) return "";', function_start)
    missing_empty_guard = source.index('if (typeof value === "string" && value.trim() === "") return "";', function_start)
    if not (missing_null_guard < number_call and missing_empty_guard < number_call):
        raise AssertionError("formatCacheAgeMs must return empty for null/undefined/empty string before Number(value)")


def main():
    test_executed_uses_reported_states()
    test_skipped_uses_explicit_cached_states_and_age()
    test_stale_never_reports_ready()
    test_legacy_robot_ws_ok_is_preserved()
    test_panel_cache_age_missing_values_are_not_zero()
    print("edge-hub transport diagnostics tests: OK")


if __name__ == "__main__":
    main()
