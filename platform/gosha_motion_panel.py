"""Operator panel calls the robot's existing self.motion MCP tools."""

import json
import re
import secrets
import time
import uuid


TOOLS = {
    "list": "self.motion.list",
    "play": "self.motion.play",
    "status": "self.motion.status",
    "stop": "self.motion.stop",
}
MOTION_ID = re.compile(r"^(?:builtin|stored)/[A-Za-z0-9_.-]{1,64}$")
STATES = {"confirmed", "idle", "in_progress", "finished", "stopped", "rejected", "failed"}


class MotionPanelError(Exception):
    pass


def call_motion_tool(endpoint, action, motion_id="", *, connect, timeout=8.0):
    if action not in TOOLS:
        raise MotionPanelError("invalid_action")
    if action == "play" and not MOTION_ID.fullmatch(motion_id):
        raise MotionPanelError("invalid_motion_id")
    if not endpoint or not endpoint.startswith(("ws://", "wss://")):
        raise MotionPanelError("motion_endpoint_unavailable")
    if connect is None:
        raise MotionPanelError("websockets_unavailable")

    init_id = secrets.randbits(31)
    call_id = secrets.randbits(31)
    arguments = {}
    if action == "play":
        arguments = {"motion_id": motion_id, "request_id": uuid.uuid4().hex}
    initialize = {
        "jsonrpc": "2.0", "id": init_id, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "gosha-motion-panel", "version": "1"},
        },
    }
    call = {
        "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
        "params": {"name": TOOLS[action], "arguments": arguments},
    }
    deadline = time.monotonic() + timeout

    def send(ws, payload):
        ws.send(json.dumps(payload, ensure_ascii=False))

    try:
        with connect(
            endpoint, open_timeout=timeout, close_timeout=1,
            ping_interval=None, compression=None, max_size=512_000,
        ) as ws:
            send(ws, initialize)
            sent_notice = False
            sent_call = False
            while time.monotonic() < deadline:
                try:
                    raw = ws.recv(timeout=min(2.0, deadline - time.monotonic()))
                except TimeoutError:
                    continue
                if not isinstance(raw, str) or len(raw) > 512_000:
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if isinstance(message.get("payload"), dict):
                    message = message["payload"]
                request_id = message.get("id")
                method = message.get("method")
                if request_id == call_id:
                    if isinstance(message.get("error"), dict):
                        raise MotionPanelError("motion_call_rejected")
                    result = message.get("result")
                    if not isinstance(result, dict) or result.get("isError"):
                        raise MotionPanelError("motion_call_rejected")
                    content = result.get("content")
                    if not isinstance(content, list) or len(content) != 1:
                        raise MotionPanelError("motion_result_invalid")
                    item = content[0]
                    if not isinstance(item, dict) or item.get("type") != "text":
                        raise MotionPanelError("motion_result_invalid")
                    text = item.get("text")
                    if not isinstance(text, str) or len(text) > 16000:
                        raise MotionPanelError("motion_result_invalid")
                    payload = json.loads(text)
                    if not isinstance(payload, dict) or payload.get("status") not in STATES:
                        raise MotionPanelError("motion_result_invalid")
                    if action == "list" and not isinstance(payload.get("movements"), list):
                        raise MotionPanelError("motion_result_invalid")
                    return payload
                if request_id == init_id:
                    if not isinstance(message.get("result"), dict):
                        raise MotionPanelError("motion_initialize_failed")
                    if not sent_notice:
                        send(ws, {"jsonrpc": "2.0", "method": "notifications/initialized"})
                        sent_notice = True
                    if not sent_call:
                        send(ws, call)
                        sent_call = True
                    continue
                if method == "initialize" and request_id is not None:
                    send(ws, {
                        "jsonrpc": "2.0", "id": request_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "gosha-motion-panel", "version": "1"},
                        },
                    })
                    continue
                if method == "notifications/initialized" and not sent_call:
                    send(ws, call)
                    sent_call = True
                    continue
                if method == "ping" and request_id is not None:
                    send(ws, {"jsonrpc": "2.0", "id": request_id, "result": {}})
                    continue
                if method == "tools/list" and request_id is not None:
                    send(ws, {"jsonrpc": "2.0", "id": request_id, "result": {"tools": []}})
    except MotionPanelError:
        raise
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        raise MotionPanelError("motion_connection_failed") from exc
    raise MotionPanelError("motion_timeout")
