#!/usr/bin/env python3
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path


def request_json(url, *, method="GET", payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_operator_panel_event_round_trip():
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["APP_ROOT"] = temp_dir
        import gui_panel

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


def test_runtime_summary_pill_markup_is_well_formed():
    html = (Path(__file__).parent / "panel_index.html").read_text(encoding="utf-8")
    assert 'class="pill ${freshLinks.length ? "ok" : "warn"}">свежих связей' in html
    assert 'class="pill ${activeTasks ? "warn" : "ok"}">задач' in html
    assert 'class="pill ${errors ? "bad" : "ok"}">ошибок' in html


if __name__ == "__main__":
    test_operator_panel_event_round_trip()
    test_runtime_summary_pill_markup_is_well_formed()
    print("runtime event HTTP contract tests: OK")
