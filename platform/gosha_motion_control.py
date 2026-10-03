"""Local panel access to the robot's existing authenticated DeviceTools session."""
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import re
import socket

from gosha_motion_panel import MOTION_ID


OPERATIONS = {"list": "robot_list_movements", "play": "robot_play_movement",
              "status": "robot_motion_status", "stop": "robot_stop_motion"}
LOG = logging.getLogger("gosha.motion_control")


class MotionControlServer:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("GOSHA_MOTION_SOCKET", "/run/gosha/motion-panel.sock"))
        self.devices = {}
        self.play_pending = set()
        self.server = None
        self.inode = None

    def attach(self, robot_id, device):
        if device is not None:
            self.devices[robot_id] = device

    def detach(self, robot_id):
        self.devices.pop(robot_id, None)

    async def dispatch(self, request):
        if not isinstance(request, dict) or set(request) - {"robot_id", "action", "motion_id"}:
            return {"ok": False, "error": "invalid_request"}
        robot_id, action = request.get("robot_id"), request.get("action")
        if not isinstance(robot_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", robot_id):
            return {"ok": False, "error": "invalid_robot_id"}
        if not isinstance(action, str) or action not in OPERATIONS:
            return {"ok": False, "error": "invalid_action"}
        motion_id = request.get("motion_id", "")
        if action == "play" and (not isinstance(motion_id, str) or not MOTION_ID.fullmatch(motion_id)):
            return {"ok": False, "error": "invalid_motion_id"}
        device = self.devices.get(robot_id)
        if device is None:
            return {"ok": False, "error": "robot_voice_not_connected"}
        operation = OPERATIONS[action]
        try:
            await asyncio.wait_for(device.ready.wait(), 2)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "robot_motion_tools_not_ready"}
        if operation not in device.available:
            return {"ok": False, "error": "robot_motion_tools_unavailable"}
        if action == "play" and robot_id in self.play_pending:
            return {"ok": True, "data": {"status": "rejected", "reason": "movement_busy"}}
        if action == "play":
            self.play_pending.add(robot_id)
        try:
            arguments = {"motion_id": motion_id} if action == "play" else {}
            result = await device.call(operation, json.dumps(arguments))
            payload = result.get("device_result") if isinstance(result, dict) else None
            if not isinstance(payload, dict) or result.get("status") == "unknown":
                return {"ok": False, "error": "motion_result_not_confirmed"}
            return {"ok": True, "data": payload}
        finally:
            if action == "play":
                self.play_pending.discard(robot_id)

    async def handle(self, reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readline(), 2)
            if len(raw) > 4096 or not raw.endswith(b"\n"):
                raise ValueError()
            reply = await self.dispatch(json.loads(raw))
            encoded = json.dumps(reply, ensure_ascii=False).encode() + b"\n"
            if len(encoded) > 32000:
                raise ValueError()
            writer.write(encoded)
            await writer.drain()
        except Exception:
            with contextlib.suppress(Exception):
                writer.write(b'{"ok":false,"error":"motion_result_not_confirmed"}\n')
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def __aenter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists():
            if not self.path.is_socket():
                raise RuntimeError("Motion control path is occupied by a file")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                try:
                    probe.connect(str(self.path))
                except (ConnectionRefusedError, FileNotFoundError):
                    self.path.unlink(missing_ok=True)
                else:
                    raise RuntimeError("Motion control server is already running")
        self.server = await asyncio.start_unix_server(self.handle, path=str(self.path), limit=4096)
        self.inode = self.path.stat().st_ino
        try:
            self.path.chmod(0o600)
        except OSError:
            await self.__aexit__()
            raise
        return self

    async def __aexit__(self, *_args):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        with contextlib.suppress(OSError):
            if self.inode is not None and self.path.exists() and self.path.stat().st_ino == self.inode:
                self.path.unlink()

    @contextlib.asynccontextmanager
    async def optional(self):
        """A panel transport failure must not prevent ordinary voice startup."""
        try:
            await self.__aenter__()
        except (OSError, RuntimeError) as error:
            LOG.error("motion_control_unavailable error_type=%s", type(error).__name__)
            yield None
            return
        try:
            yield self
        finally:
            await self.__aexit__()
