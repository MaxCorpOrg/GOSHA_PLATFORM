import json
import unittest

from gosha_motion_panel import MotionPanelError, call_motion_tool


class FakeSocket:
    def __init__(self, reply):
        self.sent = []
        self.reply = reply
        self.responses = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self, timeout=None):
        del timeout
        if self.responses == 0:
            self.responses += 1
            return json.dumps({"jsonrpc": "2.0", "id": self.sent[0]["id"], "result": {}})
        self.responses += 1
        call = self.sent[-1]
        return json.dumps({
            "jsonrpc": "2.0", "id": call["id"],
            "result": {"content": [{"type": "text", "text": json.dumps(self.reply)}]},
        })


class MotionPanelTests(unittest.TestCase):
    def test_list_and_play_use_named_robot_tools(self):
        sockets = []

        def connect(*_args, **_kwargs):
            socket = FakeSocket({"status": "confirmed", "movements": [
                {"motion_id": "stored/new-wave", "name": "Новый взмах"}
            ]})
            sockets.append(socket)
            return socket

        result = call_motion_tool("wss://example.test/mcp", "list", connect=connect)
        self.assertEqual(result["movements"][0]["motion_id"], "stored/new-wave")
        self.assertEqual(sockets[0].sent[-1]["params"]["name"], "self.motion.list")

        def play_connect(*_args, **_kwargs):
            socket = FakeSocket({"status": "in_progress"})
            sockets.append(socket)
            return socket

        result = call_motion_tool(
            "wss://example.test/mcp", "play", "stored/new-wave", connect=play_connect
        )
        self.assertEqual(result["status"], "in_progress")
        call = sockets[1].sent[-1]
        self.assertEqual(call["params"]["name"], "self.motion.play")
        self.assertEqual(call["params"]["arguments"]["motion_id"], "stored/new-wave")
        self.assertEqual(len(call["params"]["arguments"]["request_id"]), 32)
        self.assertEqual(sum(item.get("method") == "tools/call" for item in sockets[1].sent), 1)

    def test_invalid_id_never_connects(self):
        with self.assertRaisesRegex(MotionPanelError, "invalid_motion_id"):
            call_motion_tool(
                "wss://example.test/mcp", "play", "../../other",
                connect=lambda *_a, **_k: self.fail("must not connect"),
            )

    def test_stop_and_status_use_existing_tools(self):
        sent = []

        def connect(*_args, **_kwargs):
            socket = FakeSocket({"status": "stopped"})
            sent.append(socket)
            return socket

        self.assertEqual(
            call_motion_tool("wss://example.test/mcp", "stop", connect=connect)["status"],
            "stopped",
        )
        self.assertEqual(sent[0].sent[-1]["params"], {
            "name": "self.motion.stop", "arguments": {},
        })

        self.assertEqual(
            call_motion_tool("wss://example.test/mcp", "status", connect=connect)["status"],
            "stopped",
        )
        self.assertEqual(sent[1].sent[-1]["params"], {
            "name": "self.motion.status", "arguments": {},
        })


if __name__ == "__main__":
    unittest.main()
