"""HTTP contract and STOP priority; no connection to a physical robot."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


class MotionHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"APP_ROOT": self.temp.name})
        self.env.start()
        spec = importlib.util.spec_from_file_location("motion_http_test_gui", Path(__file__).with_name("gui_panel.py"))
        self.gui = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.gui
        spec.loader.exec_module(self.gui)
        self.calls = []
        self.robot = patch.object(self.gui, "require_robot_dir", return_value=None)
        self.endpoint = patch.object(self.gui, "get_robot_mcp_endpoint", return_value="wss://test.invalid/mcp")
        self.robot.start()
        self.endpoint.start()
        self.server = self.gui.ThreadingHTTPServer(("127.0.0.1", 0), self.gui.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/operator/robots/gosha-main/motions"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.endpoint.stop()
        self.robot.stop()
        self.env.stop()
        sys.modules.pop("motion_http_test_gui", None)
        self.temp.cleanup()

    def request(self, payload=None):
        req = urllib.request.Request(self.url, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_catalog_retains_builtin_and_uploaded_identity(self):
        movements = [{"motion_id": f"builtin/action-{i}"} for i in range(27)]
        movements += [{"motion_id": "stored/existing-wave", "source": "stored"}]
        with patch.object(self.gui, "call_motion_socket", return_value={"status": "confirmed", "movements": movements}) as call:
            status, result = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["movements"], movements)
        self.assertEqual(call.call_args.args[2], "list")

    def test_stop_reaches_executor_while_play_response_is_pending(self):
        started = threading.Event()
        stopped = threading.Event()
        def call(_path, _robot, action, motion_id="", **_kwargs):
            self.calls.append((action, motion_id))
            if action == "play":
                started.set()
                self.assertTrue(stopped.wait(2), "STOP was blocked by play")
            if action == "stop":
                stopped.set()
            return {"status": "stopped" if action == "stop" else "in_progress"}
        with patch.object(self.gui, "call_motion_socket", side_effect=call), ThreadPoolExecutor() as pool:
            play = pool.submit(self.request, {"action": "play", "motion_id": "stored/existing-wave"})
            self.assertTrue(started.wait(2))
            status, result = self.request({"action": "stop"})
            self.assertEqual((status, result["data"]["status"]), (200, "stopped"))
            self.assertEqual(play.result()[0], 200)
        self.assertEqual(self.calls, [("play", "stored/existing-wave"), ("stop", "")])

    def test_invalid_action_and_motion_id_do_not_reach_robot(self):
        with patch.object(self.gui, "ws_connect") as connect:
            for payload in [{"action": "home"}, {"action": "play", "motion_id": "../other"}]:
                status, _result = self.request(payload)
                self.assertEqual(status, 400)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
