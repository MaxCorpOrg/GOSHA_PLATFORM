#!/usr/bin/env python3
import os
import tempfile
from pathlib import Path

import selfhost_xiaozhi_common as selfhost


def test_claimed_device_receives_runtime_event_delivery_config():
    original_state_path = selfhost.STATE_PATH
    original_public_panel_url = os.environ.get("PUBLIC_PANEL_URL")
    os.environ["PUBLIC_PANEL_URL"] = "https://panel.example.invalid"
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
        if original_public_panel_url is None:
            os.environ.pop("PUBLIC_PANEL_URL", None)
        else:
            os.environ["PUBLIC_PANEL_URL"] = original_public_panel_url


def test_default_backend_config_does_not_synthesize_public_endpoint():
    saved_env = {key: os.environ.get(key) for key in (
        "SELFHOST_XIAOZHI_PUBLIC_HTTP_BASE",
        "PUBLIC_PANEL_URL",
        "SELFHOST_GOSHA_OTA_URL",
        "SELFHOST_XIAOZHI_OTA_URL",
        "SELFHOST_GOSHA_ACTIVATE_URL",
        "SELFHOST_XIAOZHI_ACTIVATE_URL",
        "SELFHOST_GOSHA_WS_URL",
        "SELFHOST_XIAOZHI_WS_URL",
        "SELFHOST_XIAOZHI_MCP_ENDPOINT_BASE",
    )}
    try:
        for key in saved_env:
            os.environ.pop(key, None)
        backend = selfhost.default_backend_config()
        assert backend["public_http_base"] == ""
        assert backend["ota_url"] == ""
        assert backend["activate_url"] == ""
        assert backend["websocket_url"] == ""
        assert backend["mcp_endpoint_base"] == ""
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    test_claimed_device_receives_runtime_event_delivery_config()
    test_default_backend_config_does_not_synthesize_public_endpoint()
    print("self-hosted runtime event config tests: OK")
