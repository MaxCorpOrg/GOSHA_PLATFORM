import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from gosha_motion_control import MotionControlServer
from gosha_motion_panel import call_motion_socket, MotionPanelError


class Device:
    def __init__(self):
        self.ready = asyncio.Event()
        self.ready.set()
        self.available = dict.fromkeys(["robot_list_movements", "robot_play_movement", "robot_motion_status", "robot_stop_motion"])
        self.calls = []

    async def call(self, operation, encoded):
        self.calls.append((operation, json.loads(encoded)))
        result = {"status": "confirmed", "movements": [{"motion_id": "stored/test-wave"}]}
        return {"status": "confirmed", "device_result": result}


class MotionControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "motion.sock"
        self.control = await MotionControlServer(self.path).__aenter__()
        self.device = Device()
        self.control.attach("robot-01", self.device)

    async def asyncTearDown(self):
        await self.control.__aexit__()
        self.temp.cleanup()

    async def call(self, action, motion_id=""):
        return await asyncio.to_thread(call_motion_socket, self.path, "robot-01", action, motion_id)

    async def test_socket_reads_real_session_catalog_and_has_private_permissions(self):
        result = await self.call("list")
        self.assertEqual(result["movements"][0]["motion_id"], "stored/test-wave")
        self.assertEqual(self.device.calls, [("robot_list_movements", {})])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    async def test_stop_is_forwarded_while_play_waits_and_duplicate_play_is_rejected(self):
        play_started, stop_received = asyncio.Event(), asyncio.Event()
        async def call(operation, encoded):
            self.device.calls.append((operation, json.loads(encoded)))
            if operation == "robot_play_movement":
                play_started.set()
                await asyncio.wait_for(stop_received.wait(), 2)
                return {"status": "in_progress", "device_result": {"status": "in_progress"}}
            stop_received.set()
            return {"status": "stopped", "device_result": {"status": "stopped"}}
        self.device.call = call
        play = asyncio.create_task(self.call("play", "stored/test-wave"))
        await asyncio.wait_for(play_started.wait(), 2)
        rejected = await self.call("play", "stored/test-wave")
        self.assertEqual(rejected["reason"], "movement_busy")
        self.assertEqual((await self.call("stop"))["status"], "stopped")
        self.assertEqual((await play)["status"], "in_progress")
        self.assertEqual([item[0] for item in self.device.calls], ["robot_play_movement", "robot_stop_motion"])

    async def test_unknown_play_is_not_repeated(self):
        async def unknown(operation, encoded):
            self.device.calls.append((operation, json.loads(encoded)))
            return {"status": "unknown", "retry": False}
        self.device.call = unknown
        with self.assertRaisesRegex(MotionPanelError, "motion_result_not_confirmed"):
            await self.call("play", "stored/test-wave")
        self.assertEqual(len(self.device.calls), 1)

    async def test_offline_and_unsupported_devices_are_explicit(self):
        self.control.detach("robot-01")
        with self.assertRaisesRegex(MotionPanelError, "robot_voice_not_connected"):
            await self.call("list")
        self.control.attach("robot-01", self.device)
        self.device.available = {}
        with self.assertRaisesRegex(MotionPanelError, "robot_motion_tools_unavailable"):
            await self.call("list")
        self.assertEqual(self.device.calls, [])

    async def test_invalid_request_cannot_reach_device(self):
        for action, motion_id in [("home", ""), ("play", "../../other")]:
            with self.assertRaises(MotionPanelError):
                await self.call(action, motion_id)
        for request in [{"robot_id": [], "action": "list"}, {"robot_id": "robot-01", "action": []}]:
            self.assertFalse((await self.control.dispatch(request))["ok"])
        self.assertEqual(self.device.calls, [])

    async def test_optional_transport_failure_allows_voice_startup(self):
        import gosha_voice_router
        listening = asyncio.Event()
        class Listener:
            async def __aenter__(self):
                listening.set()
            async def __aexit__(self, *_args):
                pass
        control = MotionControlServer(self.path.parent / "blocked.sock")
        with patch.object(control, "__aenter__", AsyncMock(side_effect=PermissionError())), patch("gosha_voice_router.MotionControlServer", return_value=control), patch("gosha_voice_router.serve", return_value=Listener()) as serve, self.assertLogs("gosha.motion_control", level="ERROR"):
            task = asyncio.create_task(gosha_voice_router.main())
            try:
                await asyncio.wait_for(listening.wait(), 2)
                self.assertFalse(task.done())
                self.assertIsNone(serve.call_args.args[0].__self__.motion_control)
                self.assertFalse(control.path.exists())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_optional_transport_does_not_remove_another_active_server(self):
        second = MotionControlServer(self.path)
        with self.assertLogs("gosha.motion_control", level="ERROR"):
            async with second.optional() as active:
                self.assertIsNone(active)
        self.assertEqual((await self.call("list"))["status"], "confirmed")

    async def test_voice_router_registers_only_authenticated_active_bridge(self):
        from gosha_voice_router import VoiceRouter
        device, control = self.device, self.control
        class Bridge:
            device_tools = device
            asserted = False
            async def run(self):
                self.asserted = control.devices.get("robot-current") is device
                if not self.asserted:
                    raise AssertionError("active bridge was not registered")
        class Robot:
            request_headers = {}
            async def close(self, *_args):
                pass
        bridge = Bridge()
        router = VoiceRouter("ws://127.0.0.1:18081", bridge_factory=lambda *_args, **_kwargs: bridge, motion_control=control)
        with patch("gosha_voice_router.claims.find_claim_by_device", return_value={"robot_id": "robot-current"}), patch("gosha_voice_router.authenticate", return_value={"robot_id": "robot-current"}), patch("gosha_voice_router.live_settings", return_value=({}, "test-key")):
            await router.handle(Robot(), "/xiaozhi/v1/")
        self.assertTrue(bridge.asserted)
        self.assertNotIn("robot-current", control.devices)


if __name__ == "__main__":
    unittest.main()
