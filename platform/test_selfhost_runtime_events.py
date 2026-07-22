#!/usr/bin/env python3
import tempfile
from pathlib import Path

import selfhost_xiaozhi_common as selfhost


def test_claimed_device_receives_runtime_event_delivery_config():
    original_state_path = selfhost.STATE_PATH
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            selfhost.STATE_PATH = Path(temp_dir) / "state.json"
            selfhost.record_device_contact(device_id="device-01", client_id="client-01")
            claim = selfhost.claim_device_to_robot(
                "device-01",
                "robot-01",
                websocket_token="runtime-secret",
            )
            payload = selfhost.ota_payload_for_device("device-01")
            runtime = payload["runtime_events"]
            assert runtime["schema_version"] == "gosha.runtime.event.v1"
            assert runtime["url"].endswith("/gosha/events")
            assert runtime["token"] == claim["websocket_token"]
            assert runtime["heartbeat_interval_seconds"] >= 10
    finally:
        selfhost.STATE_PATH = original_state_path


if __name__ == "__main__":
    test_claimed_device_receives_runtime_event_delivery_config()
    print("self-hosted runtime event config tests: OK")
