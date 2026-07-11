#!/usr/bin/env python3
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))


class MobileRuntimeContractTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_root = Path(self.temp_dir.name) / "app_root"
        os.environ["APP_ROOT"] = str(self.app_root)
        os.environ["PUBLIC_PANEL_URL"] = "http://151.241.228.232:18876"

        for module_name in (
            "gui_panel",
            "gosha_agent_gateway_client",
            "gosha_agent_store",
            "gosha_assistant_store",
            "selfhost_xiaozhi_common",
        ):
            sys.modules.pop(module_name, None)
        self.panel = importlib.import_module("gui_panel")

    def _write_robot_runtime_fixture(self):
        now = self.panel.now_ts()
        robot_id = "gosha-main"
        robot_dir = self.app_root / "robots" / robot_id
        robot_dir.mkdir(parents=True)
        (robot_dir / "robot.env").write_text(
            "\n".join(
                [
                    f"ROBOT_ID={robot_id}",
                    "ROBOT_NAME=gosha main",
                    "ROBOT_RUNTIME_CLASS=runtime",
                    "ROBOT_BACKEND_MODE=self_hosted_xiaozhi",
                    "ROBOT_CONTROL_TRANSPORT=cloud-mcp",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (robot_dir / "mcp_endpoint.txt").write_text(
            "ws://151.241.228.232:18080/mcp/?token=runtime-secret-token&robot_id=gosha-main\n",
            encoding="utf-8",
        )
        (robot_dir / "mcp_config.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
        (robot_dir / "mcp_activity.json").write_text(
            json.dumps(
                {
                    "available": True,
                    "updated_at": now,
                    "last_request_seen": now,
                    "last_request_method": "tools/call",
                    "last_request_target": "ws://151.241.228.232:18080/mcp/?token=activity-secret",
                    "last_tool_call_seen": now,
                    "last_tool_name": "self.otto.get_status",
                    "last_tool_target": "ws://151.241.228.232:18080/mcp/?token=tool-secret",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        state_path = self.app_root / "selfhost_xiaozhi" / "state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps(
                {
                    "backend": {
                        "provider": "self_hosted_xiaozhi",
                        "backend_mode": "self_hosted_xiaozhi",
                        "transport": "websocket_only",
                        "public_http_base": "http://151.241.228.232:18876",
                        "ota_url": "http://151.241.228.232:18876/gosha/ota/",
                        "activate_url": "http://151.241.228.232:18876/gosha/ota/activate",
                        "websocket_url": "ws://151.241.228.232:18080/xiaozhi/v1/",
                        "mcp_endpoint_base": "ws://151.241.228.232:18080/mcp/",
                    },
                    "pending_devices": {},
                    "claims": {
                        "device-1": {
                            "device_id": "device-1",
                            "robot_id": robot_id,
                            "status": "claimed",
                            "last_seen": now,
                            "last_seen_iso": self.panel.ts_to_iso(now),
                            "claimed_at": now,
                            "claimed_at_iso": self.panel.ts_to_iso(now),
                            "websocket_url": "ws://151.241.228.232:18080/xiaozhi/v1/",
                            "websocket_token": "runtime-secret-token",
                            "control_mcp_endpoint": "ws://151.241.228.232:18080/mcp/?token=runtime-secret-token&robot_id=gosha-main",
                            "payload": {
                                "board": {"name": "gosha-v1", "ip": "192.168.1.44"},
                                "application": {"version": "2.2.2"},
                            },
                            "remote_addr": "203.0.113.10",
                        }
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return robot_id

    def test_runtime_snapshot_hides_service_urls_and_tokenized_endpoints(self):
        robot_id = self._write_robot_runtime_fixture()
        with mock.patch.object(self.panel, "fetch_edge_snapshot", return_value={"hub_state": "offline", "hub_error": "", "agents": {}}), mock.patch.object(
            self.panel, "service_state", return_value="active"
        ):
            snapshot = self.panel.get_robot_runtime_snapshot(robot_id)

        self.assertEqual(snapshot["control"]["transport"], "cloud-mcp")
        self.assertTrue(snapshot["control"]["configured"])
        self.assertEqual(snapshot["diagnostics"]["transport_state"], "configured")
        self.assertTrue(snapshot["cloud_console"]["device_claimed"])
        self.assertTrue(snapshot["cloud_console"]["websocket_token_configured"])
        self.assertTrue(snapshot["cloud_console"]["mcp_endpoint_ready"])
        self.assertTrue(snapshot["connectivity"]["connected"])
        self.assertEqual(snapshot["connectivity"]["evidence"], "fresh_device_contact")

        serialized = json.dumps(snapshot, ensure_ascii=False)
        for forbidden in (
            "runtime-secret-token",
            "activity-secret",
            "tool-secret",
            "token=",
            "ws://151.241.228.232:18080/mcp",
            "ws://151.241.228.232:18080/xiaozhi/v1",
            "http://151.241.228.232:18876/gosha/ota",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
