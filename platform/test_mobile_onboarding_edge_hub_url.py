#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PANEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = PANEL_DIR.parent
REMOVED_PUBLIC_ENDPOINT = "151" + ".241" + ".228" + ".232"
REMOVED_EXAMPLE_PASSWORD = "change" + "-me"
REMOVED_LEGACY_PANEL_PORT = "88" + "76"
REMOVED_LEGACY_EDGE_PORT = "88" + "90"

ONBOARDING_PROBE = r"""
import json
import os
from pathlib import Path

import gui_panel

robot_id = "robot-01"
root = Path(os.environ["APP_ROOT"])
robot_dir = root / "robots" / robot_id
robot_dir.mkdir(parents=True)
(robot_dir / "robot.env").write_text(
    "ROBOT_NAME=Гоша Main\nROBOT_BACKEND_MODE=self_hosted_xiaozhi\n",
    encoding="utf-8",
)
(robot_dir / "mcp_config.json").write_text('{"mcpServers": {}}', encoding="utf-8")

bundle = gui_panel.onboarding_bundle(robot_id, code="TESTCODE")
edge_hub_url = bundle.get("edge_hub_url")
expected = os.environ["EXPECTED_EDGE_HUB_URL"]
if edge_hub_url != expected:
    raise AssertionError(f"edge_hub_url={edge_hub_url!r}, expected={expected!r}")
if "18080/mcp" in edge_hub_url:
    raise AssertionError("PUBLIC_EDGE_HUB_URL must not default to the voice MCP websocket")
if "mobile_profile" not in bundle:
    raise AssertionError("mobile_profile is required for mobile onboarding")

print(json.dumps({"edge_hub_url": edge_hub_url}, ensure_ascii=False))
"""


def run_probe(public_edge_hub_url, expected_edge_hub_url):
    with tempfile.TemporaryDirectory() as temp_dir:
        env = os.environ.copy()
        env["APP_ROOT"] = temp_dir
        env["PUBLIC_PANEL_URL"] = "http://panel.example.invalid"
        env["EXPECTED_EDGE_HUB_URL"] = expected_edge_hub_url
        if public_edge_hub_url is None:
            env.pop("PUBLIC_EDGE_HUB_URL", None)
        else:
            env["PUBLIC_EDGE_HUB_URL"] = public_edge_hub_url

        result = subprocess.run(
            [sys.executable, "-B", "-c", ONBOARDING_PROBE],
            cwd=PANEL_DIR,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
        return json.loads(stdout_lines[-1])


def test_public_edge_hub_url_defaults_empty():
    payload = run_probe(public_edge_hub_url=None, expected_edge_hub_url="")
    assert payload["edge_hub_url"] == ""


def test_public_edge_hub_url_uses_explicit_env_only():
    payload = run_probe(
        public_edge_hub_url="ws://edge.example.invalid/operator/",
        expected_edge_hub_url="ws://edge.example.invalid/operator",
    )
    assert payload["edge_hub_url"] == "ws://edge.example.invalid/operator"


def test_installer_does_not_seed_voice_mcp_as_edge_hub():
    install_script = (REPO_ROOT / "ops" / "install_server.sh").read_text(encoding="utf-8")
    assert 'PUBLIC_EDGE_HUB_URL=ws://${PUBLIC_HOST}:18080/mcp' not in install_script
    assert (
        'ensure_env_key "${ENV_ROOT}/panel.env" "PUBLIC_EDGE_HUB_URL" "ws://${PUBLIC_HOST}:18080/mcp"'
        not in install_script
    )


def test_public_endpoint_defaults_are_explicit_configuration_only():
    checked_files = [
        REPO_ROOT / "platform" / "gui_panel.py",
        REPO_ROOT / "platform" / "selfhost_xiaozhi_common.py",
        REPO_ROOT / "platform" / "check_gosha_mobile_contract.py",
        REPO_ROOT / "ops" / "install_server.sh",
        REPO_ROOT / "platform" / "panel-auth.env.example",
    ]
    for path in checked_files:
        text = path.read_text(encoding="utf-8")
        assert REMOVED_PUBLIC_ENDPOINT not in text


def test_backend_env_example_requires_runtime_database_password():
    env_example = (REPO_ROOT / "backend" / "selfhost-backend.env.example").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "backend" / "selfhost-backend.compose.yml").read_text(encoding="utf-8")
    assert f"SELFHOST_XIAOZHI_DB_PASSWORD={REMOVED_EXAMPLE_PASSWORD}" not in env_example
    assert "SELFHOST_XIAOZHI_DB_PASSWORD=" in env_example
    assert "${SELFHOST_XIAOZHI_DB_PASSWORD:?set SELFHOST_XIAOZHI_DB_PASSWORD in the runtime env file}" in compose


def test_gui_panel_direct_run_uses_canonical_or_explicit_ports():
    gui_panel = (REPO_ROOT / "platform" / "gui_panel.py").read_text(encoding="utf-8")
    assert 'os.environ.get("PANEL_PORT", "18876")' in gui_panel
    assert f'os.environ.get("PANEL_PORT", "{REMOVED_LEGACY_PANEL_PORT}")' not in gui_panel
    assert f"http://127.0.0.1:{REMOVED_LEGACY_EDGE_PORT}" not in gui_panel


if __name__ == "__main__":
    test_public_edge_hub_url_defaults_empty()
    test_public_edge_hub_url_uses_explicit_env_only()
    test_installer_does_not_seed_voice_mcp_as_edge_hub()
    test_public_endpoint_defaults_are_explicit_configuration_only()
    test_backend_env_example_requires_runtime_database_password()
    test_gui_panel_direct_run_uses_canonical_or_explicit_ports()
    print("mobile onboarding edge hub URL tests: OK")
