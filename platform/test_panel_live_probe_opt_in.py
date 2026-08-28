#!/usr/bin/env python3
from pathlib import Path


PANEL_HTML = Path(__file__).with_name("panel_index.html")


def read_panel_html():
    return PANEL_HTML.read_text(encoding="utf-8")


def section_between(text, start, end):
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


def test_panel_open_does_not_start_live_probe_or_wifi_scan():
    html = read_panel_html()
    init_body = section_between(html, "async function initOperatorData()", "window.addEventListener")
    reload_body = section_between(html, "async function reloadRobots()", "async function refreshRuntimeRobots()")

    forbidden_init_calls = (
        "autoDetectLiveRobots(",
        "detectRobotState(",
        "probeRobotState(",
        "reloadWifi(",
    )
    for call in forbidden_init_calls:
        assert call not in init_body, call

    forbidden_reload_calls = (
        "autoDetectLiveRobots(",
        "detectRobotState(",
        "probeRobotState(",
    )
    for call in forbidden_reload_calls:
        assert call not in reload_body, call

    assert "await reloadRobots();" in init_body
    assert "connectRuntimeEventStream();" in init_body


def test_auto_detect_requires_explicit_operator_opt_in():
    html = read_panel_html()
    auto_body = section_between(html, "async function autoDetectLiveRobots(options = {})", "async function reloadRobots()")

    guard_index = auto_body.index("!operatorOptIn")
    queue_index = auto_body.index("const queue = currentRobots")
    detect_index = auto_body.index("await detectRobotState")
    assert guard_index < queue_index < detect_index
    assert "operatorOptIn = false" in auto_body
    assert "autoDetectLiveRobots({ operatorOptIn: true })" in html


def test_manual_detect_button_still_runs_detection_post():
    html = read_panel_html()
    detect_body = section_between(html, "async function detectRobotState(robotId", "async function probeRobotState")

    assert 'apiPost(operatorUrl(`/robots/${robotId}/detect`), {})' in detect_body
    assert 'onclick="detectRobotState(' in html


def main():
    test_panel_open_does_not_start_live_probe_or_wifi_scan()
    test_auto_detect_requires_explicit_operator_opt_in()
    test_manual_detect_button_still_runs_detection_post()
    print("panel live probe opt-in tests: OK")


if __name__ == "__main__":
    main()
