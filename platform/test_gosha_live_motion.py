"""Calibrated voice motion checks on an in-memory device; no hardware."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from gosha_live_motion import JOINTS, MotionDevice, credentials_for

KEY = {"access_key": "unit-test-only-motion-key", "calibration_id": "a" * 64}
CAPS = {"op": "capabilities", "calibration_id": KEY["calibration_id"], "calibrated": False,
        "mode": "motion_editor", "commissioning": False,
        "motion_allowed": True, "right_arm_initialized": True, "active_motion": None,
        "right_arm_available": True, "initialization_required": False,
        "initialization_op": "initialize_right_arm", "reason": "ok",
        "commanded_pose": dict.fromkeys(JOINTS, 0), "servo_degrees": {"right_hand": 135},
        "watchdog_ms": 300, "max_rate_hz": 20,
        "joint_limits": [{"id": joint, "min": -30, "max": 30, "max_speed_dps": 10} for joint in JOINTS]}


class SimulatedMotion(MotionDevice):
    def __init__(self):
        super().__init__(None, dict(KEY))
        self.caps = copy.deepcopy(CAPS)
        self.sent = []
        self.fail_pose = False

    async def rpc(self, op, **fields):
        self.sent.append((time.monotonic(), op, fields))
        if op == "hello":
            return copy.deepcopy(self.caps)
        if op == "initialize_right_arm":
            self.caps.update(motion_allowed=True, right_arm_initialized=True,
                             initialization_required=False, reason="ok", servo_degrees={"right_hand": 135})
            return copy.deepcopy(self.caps)
        if op == "arm":
            return {"op": "armed", "session_id": "simulated-session"}
        if op == "pose" and self.fail_pose:
            raise TimeoutError()
        return {"op": "stopped" if op == "stop" else "ack", "session_id": fields["session_id"],
                "seq": fields["seq"], "commanded_pose": fields.get("target", self.last_pose)}


class MotionTests(unittest.IsolatedAsyncioTestCase):
    def test_private_credentials_are_scoped_to_one_robot(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "motion.json"
            source.write_text(json.dumps({"fixture1": KEY}))
            source.chmod(0o600)
            with patch.dict(os.environ, {"GOSHA_VOICE_MOTION_CREDENTIALS": str(source)}):
                self.assertEqual(credentials_for("fixture1"), KEY)
                self.assertIsNone(credentials_for("fixture2"))
                source.chmod(0o644)
                with self.assertRaisesRegex(ValueError, "permissions"):
                    credentials_for("fixture1")

    async def test_missing_initialization_calibration_or_neutral_never_arms(self):
        changes = [{"right_arm_initialized": False}, {"motion_allowed": False},
                   {"calibration_id": "b" * 64}, {"servo_degrees": {"right_hand": 90}},
                   {"commanded_pose": {**CAPS["commanded_pose"], "leg_positive_x": 1}},
                   {"active_motion": {"owner": "external"}}]
        for change in changes:
            device = SimulatedMotion()
            device.caps.update(change)
            self.assertEqual((await device.call("robot_wave_right_hand"))["status"], "rejected")
            self.assertEqual([op for _, op, _ in device.sent], ["hello"])

    async def test_initialization_is_a_separate_explicit_operation(self):
        device = SimulatedMotion()
        device.caps.update(right_arm_initialized=False, motion_allowed=False,
                           initialization_required=True, reason="right_arm_initialization_required",
                           servo_degrees={"right_hand": 90})
        self.assertEqual((await device.call("robot_enable_right_arm"))["status"], "confirmed")
        self.assertEqual([op for _, op, _ in device.sent], ["hello", "initialize_right_arm"])
        self.assertEqual(device.sent[-1][2], KEY)

    async def test_unknown_profiles_and_failed_initialization_never_initialize(self):
        changes = [{"mode": "verified"}, {"mode": "commissioning"}, {"commissioning": True},
                   {"calibration_id": "b" * 64}, {"right_arm_available": False},
                   {"initialization_required": False}, {"initialization_op": "other"},
                   {"reason": "right_arm_initialization_failed"}]
        for change in changes:
            device = SimulatedMotion()
            device.caps.update(right_arm_initialized=False, motion_allowed=False,
                               initialization_required=True, reason="right_arm_initialization_required")
            device.caps.update(change)
            self.assertEqual((await device.call("robot_enable_right_arm"))["status"], "rejected")
            self.assertEqual([op for _, op, _ in device.sent], ["hello"])

    async def test_already_initialized_arm_still_requires_neutral(self):
        device = SimulatedMotion()
        device.caps.update(servo_degrees={"right_hand": 90})
        self.assertEqual((await device.call("robot_enable_right_arm"))["status"], "rejected")
        self.assertEqual([op for _, op, _ in device.sent], ["hello"])

    async def test_wave_holds_both_targets_returns_zero_then_stops(self):
        device = SimulatedMotion()
        result = await device.call("robot_wave_right_hand")
        self.assertEqual(result["status"], "in_progress")
        self.assertFalse(result["completion_confirmed"])
        await asyncio.wait_for(device.worker, 8)
        poses = [(at, fields) for at, op, fields in device.sent if op == "pose"]
        negative = [at for at, fields in poses if fields["target"]["arm_positive_x"] == -15]
        zero = [at for at, fields in poses if fields["target"]["arm_positive_x"] == 0]
        self.assertGreaterEqual(negative[-1] - negative[0], 2.9)
        self.assertGreaterEqual(zero[-1] - zero[0], 2.9)
        self.assertTrue(all(fields["speed_dps"] == 5 for _, fields in poses))
        self.assertTrue(all(all(value == 0 for joint, value in fields["target"].items() if joint != "arm_positive_x") for _, fields in poses))
        self.assertEqual(device.state, "finished")
        self.assertEqual(device.sent[-1][1], "stop")
        self.assertEqual(poses[-1][1]["target"], dict.fromkeys(JOINTS, 0))

    async def test_stop_interrupts_wave_without_waiting_or_home(self):
        device = SimulatedMotion()
        await device.wave()
        await asyncio.sleep(0.01)
        result = await asyncio.wait_for(device.stop(), 0.5)
        self.assertEqual(result["motion_state"], "interrupted")
        self.assertFalse(result["returned_to_neutral"])
        self.assertEqual([op for _, op, _ in device.sent], ["hello", "arm", "pose", "stop"])

    async def test_lost_ack_is_unknown_and_stops_without_retrying_motion(self):
        device = SimulatedMotion()
        device.fail_pose = True
        await device.wave()
        await asyncio.wait_for(device.worker, 1)
        self.assertEqual(device.state, "unknown")
        self.assertEqual([op for _, op, _ in device.sent], ["hello", "arm", "pose", "stop"])

    async def test_malformed_status_is_not_reported_as_confirmed(self):
        device = SimulatedMotion()
        device.caps = {"op": "armed"}
        self.assertEqual((await device.call("robot_motion_status"))["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
