#!/usr/bin/env python3
import importlib
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path


def load_gui_panel(temp_dir):
    os.environ["APP_ROOT"] = temp_dir
    for module_name in ("gui_panel", "selfhost_xiaozhi_common"):
        sys.modules.pop(module_name, None)
    return importlib.import_module("gui_panel")


def request_json(url, *, method="GET", payload=None, headers=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_operator_panel_event_round_trip():
    with tempfile.TemporaryDirectory() as temp_dir:
        gui_panel = load_gui_panel(temp_dir)

        robot_dir = Path(temp_dir) / "robots" / "robot-01"
        robot_dir.mkdir(parents=True)
        (robot_dir / "robot.env").write_text("ROBOT_NAME=Тестовый робот\n", encoding="utf-8")

        server = gui_panel.ThreadingHTTPServer(("127.0.0.1", 0), gui_panel.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            status, accepted = request_json(
                base_url + "/api/operator/robots/robot-01/events",
                method="POST",
                payload={
                    "schema_version": "gosha.runtime.event.v1",
                    "event_id": "panel-event-1",
                    "event_type": "panel.robot_workspace.opened",
                    "source": {"instance_id": "panel-process-1"},
                    "state": {"domain": "observation", "name": "robot_workspace", "status": "observing"},
                    "link": {"kind": "panel_platform", "status": "available"},
                },
            )
            assert status == 200
            assert accepted["ok"] is True
            status, journal = request_json(
                base_url + "/api/operator/robots/robot-01/runtime-events?limit=10"
            )
            assert status == 200
            events = journal["data"]["events"]
            assert events[-1]["event_id"] == "panel-event-1"
            assert events[-1]["source"]["kind"] == "panel"
            assert events[-1]["subject"]["robot_id"] == "robot-01"
            assert events[-1]["subject"]["panel_id"] == "panel-open-access"

            status, rejected = request_json(
                base_url + "/api/operator/robots/robot-01/events",
                method="POST",
                payload={"event_id": "invalid-event", "event_type": "invalid"},
            )
            assert status == 422
            assert rejected["error_type"] == "validation_error"

            for event_id, value in (
                ("invalid-nan", float("nan")),
                ("invalid-positive-infinity", float("inf")),
                ("invalid-negative-infinity", float("-inf")),
            ):
                status, rejected_number = request_json(
                    base_url + "/api/operator/robots/robot-01/events",
                    method="POST",
                    payload={
                        "event_id": event_id,
                        "event_type": "panel.runtime.heartbeat",
                        "metrics": {"value": value},
                    },
                )
                assert status == 422
                assert rejected_number["error_type"] == "validation_error"

            original_record = gui_panel.RUNTIME_EVENT_STORE.record
            gui_panel.RUNTIME_EVENT_STORE.record = lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("temporary database failure")
            )
            try:
                status, unavailable = request_json(
                    base_url + "/api/operator/robots/robot-01/events",
                    method="POST",
                    payload={
                        "event_id": "retry-event",
                        "event_type": "panel.runtime.heartbeat",
                    },
                )
            finally:
                gui_panel.RUNTIME_EVENT_STORE.record = original_record
            assert status == 503
            assert unavailable["error_type"] == "transient_error"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_selfhost_runtime_event_uses_pseudonymous_robot_source():
    with tempfile.TemporaryDirectory() as temp_dir:
        gui_panel = load_gui_panel(temp_dir)

        robot_dir = Path(temp_dir) / "robots" / "robot-01"
        robot_dir.mkdir(parents=True)
        (robot_dir / "robot.env").write_text("ROBOT_NAME=Тестовый робот\n", encoding="utf-8")

        raw_device_id = "dc:b4:d9:35:1b:e0"
        gui_panel.selfhost_xiaozhi.record_device_contact(device_id=raw_device_id, client_id="client-01")
        claim = gui_panel.selfhost_xiaozhi.claim_device_to_robot(
            raw_device_id,
            "robot-01",
            websocket_token="runtime-secret",
        )
        expected_source_id = gui_panel.runtime_events.robot_claim_source_id(claim)

        server = gui_panel.ThreadingHTTPServer(("127.0.0.1", 0), gui_panel.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            status, accepted = request_json(
                base_url + "/gosha/events",
                method="POST",
                headers={
                    "Device-Id": raw_device_id,
                    "Authorization": f"Bearer {claim['websocket_token']}",
                },
                payload={
                    "schema_version": "gosha.runtime.event.v1",
                    "event_id": "robot-voice-phase-1",
                    "event_type": "voice.turn.phase",
                    "source": {"instance_id": "firmware-session-1", "firmware_version": "2.2.2"},
                    "trace": {"session_id": "voice-session-http", "correlation_id": "voice-correlation-http"},
                    "occurred_at": "2026-09-04T12:00:01.000Z",
                    "state": {"domain": "voice_turn", "name": "phase", "status": "robot_first_audio_out"},
                    "task": {"id": "voice-task-http", "kind": "voice_turn", "status": "running"},
                    "voice": {"phase": "robot_first_audio_out", "warm_state": "warm"},
                },
            )
            assert status == 200
            assert accepted["ok"] is True

            status, journal = request_json(
                base_url + "/api/operator/robots/robot-01/runtime-events?limit=10"
            )
            assert status == 200
            body = json.dumps(journal, ensure_ascii=False)
            assert raw_device_id not in body
            event = journal["data"]["events"][-1]
            assert event["source"]["id"] == expected_source_id
            assert event["source"]["id"].startswith("robot-claim-")
            turn = journal["data"]["snapshot"]["voice_turns"]["recent"][0]
            assert turn["phases"][0]["source_id"] == expected_source_id
            assert turn["perceived_latency"]["available"] is False
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_runtime_summary_pill_markup_is_well_formed():
    html = (Path(__file__).parent / "panel_index.html").read_text(encoding="utf-8")
    assert 'class="pill ${freshLinks.length ? "ok" : "warn"}">свежих связей' in html
    assert 'class="pill ${activeTasks ? "warn" : "ok"}">задач' in html
    assert 'class="pill ${errors ? "bad" : "ok"}">ошибок' in html


if __name__ == "__main__":
    test_operator_panel_event_round_trip()
    test_selfhost_runtime_event_uses_pseudonymous_robot_source()
    test_runtime_summary_pill_markup_is_well_formed()
    print("runtime event HTTP contract tests: OK")
