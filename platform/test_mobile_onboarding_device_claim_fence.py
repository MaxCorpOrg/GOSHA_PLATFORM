#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PANEL_DIR = Path(__file__).resolve().parent

CLAIM_FENCE_PROBE = r"""
import os
from pathlib import Path

import gui_panel
import selfhost_xiaozhi_common as selfhost

robot_id = "gosha-main"
root = Path(os.environ["APP_ROOT"])
robot_dir = root / "robots" / robot_id
robot_dir.mkdir(parents=True)
(robot_dir / "robot.env").write_text(
    "ROBOT_NAME=Гоша Main\nROBOT_BACKEND_MODE=self_hosted_xiaozhi\n",
    encoding="utf-8",
)
(robot_dir / "mcp_config.json").write_text('{"mcpServers": {}}', encoding="utf-8")


def seed_claim(device_id, *, claimed_at):
    state = selfhost.load_state()
    state["claims"] = {
        device_id: {
            "device_id": device_id,
            "robot_id": robot_id,
            "status": "claimed",
            "claimed_at": claimed_at,
            "claimed_at_iso": selfhost.ts_to_iso(claimed_at),
            "first_seen": claimed_at,
            "first_seen_iso": selfhost.ts_to_iso(claimed_at),
            "last_seen": claimed_at,
            "last_seen_iso": selfhost.ts_to_iso(claimed_at),
            "websocket_url": "ws://backend.example.invalid/xiaozhi/v1/",
            "websocket_token": "test-only-token",
            "control_mcp_endpoint": "ws://backend.example.invalid/mcp/?token=test-only-token&robot_id=gosha-main",
        }
    }
    selfhost.save_state(state)


def save_code(code, *, created_at):
    gui_panel.save_mobile_codes(
        {
            code: gui_panel.normalize_mobile_code_entry(
                {
                    "robot_id": robot_id,
                    "created_at": created_at,
                    "activated_at": 0,
                    "expires_at": created_at + 3600,
                    "revoked_at": 0,
                    "revoked_reason": "",
                },
                fallback_robot_id=robot_id,
            )
        }
    )


seed_claim("old-board", claimed_at=1000)
save_code("NEWCODE1", created_at=2000)
stale_bundle = gui_panel.onboarding_bundle(robot_id, code="NEWCODE1")
stale_device_id = stale_bundle.get("selfhost_xiaozhi", {}).get("device_id", "")
if stale_device_id:
    raise AssertionError("stale pre-registration device_id leaked into onboarding bundle")

seed_claim("new-board", claimed_at=3001)
save_code("NEWCODE2", created_at=3000)
fresh_bundle = gui_panel.onboarding_bundle(robot_id, code="NEWCODE2")
fresh_device_id = fresh_bundle.get("selfhost_xiaozhi", {}).get("device_id", "")
if fresh_device_id != "new-board":
    raise AssertionError(f"fresh post-registration device_id missing: {fresh_device_id!r}")

seed_claim("same-second-board", claimed_at=4000)
save_code("SAMESEC1", created_at=4000)
same_second_bundle = gui_panel.onboarding_bundle(robot_id, code="SAMESEC1")
same_second_device_id = same_second_bundle.get("selfhost_xiaozhi", {}).get("device_id", "")
if same_second_device_id != "same-second-board":
    raise AssertionError(f"same-second claim device_id missing: {same_second_device_id!r}")

create_result = gui_panel.create_mobile_onboarding_code(robot_id, robot_name="Гоша Main")
created_device_id = create_result.get("bundle", {}).get("selfhost_xiaozhi", {}).get("device_id", "")
if created_device_id:
    raise AssertionError("newly created replacement onboarding code must not inherit older claim device_id")
"""


def test_onboarding_device_id_is_fenced_by_code_creation_time():
    with tempfile.TemporaryDirectory() as temp_dir:
        env = os.environ.copy()
        env["APP_ROOT"] = temp_dir
        env["PUBLIC_PANEL_URL"] = "http://panel.example.invalid"
        env.pop("PUBLIC_EDGE_HUB_URL", None)
        result = subprocess.run(
            [sys.executable, "-B", "-c", CLAIM_FENCE_PROBE],
            cwd=PANEL_DIR,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    test_onboarding_device_id_is_fenced_by_code_creation_time()
    print("mobile onboarding device claim fence tests: OK")
