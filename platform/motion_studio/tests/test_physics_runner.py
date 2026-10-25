import contextlib
import importlib.util
import io
import unittest

from physics.model import validate_request
from test_physics_model import request


@unittest.skipUnless(importlib.util.find_spec("newton"), "Run with the isolated requirements-physics environment")
class PhysicsRunnerTests(unittest.TestCase):
    @staticmethod
    def run_motion(value):
        from physics.runner import simulate
        with contextlib.redirect_stdout(io.StringIO()):
            return simulate(validate_request(value))

    def test_real_cpu_solver_has_gravity_floor_support_and_exact_zero_and_odd_end_times(self):
        value = request(501, settle_ms=0)
        value["motion"]["keyframes"][0]["pose"]["arm_negative_x"] = 15
        value["motion"]["keyframes"][1]["pose"]["arm_negative_x"] = 15
        result = self.run_motion(value)
        self.assertEqual(result["engine"]["newton"], "1.6.1")
        self.assertEqual(result["engine"]["device"], "cpu")
        self.assertEqual(result["frames"][0]["time_ms"], 0)
        self.assertAlmostEqual(result["frames"][0]["pose"]["arm_negative_x"], 15, places=4)
        self.assertEqual(result["frames"][-1]["time_ms"], 501)
        self.assertEqual(result["duration_ms"], 501)
        end = result["frames"][-1]
        self.assertGreater(sum(c["normal_force_n"] for c in end["contacts"]), 3)
        self.assertTrue(any(c["body"] == "foot_negative_x" for c in end["contacts"]))
        self.assertTrue(any(c["body"] == "foot_positive_x" for c in end["contacts"]))
        self.assertLess(result["metrics"]["max_tilt_deg"], 5)
        self.assertFalse(result["hardware_validated"])

    def test_torque_setting_changes_actual_tracking_and_respects_force_bound(self):
        value = request(500, settle_ms=0, target_speed_dps=1200, torque_nm=.02)
        value["motion"]["interpolation"] = "hold"
        value["motion"]["keyframes"][1]["pose"]["arm_negative_x"] = 60
        value["motion"]["keyframes"].insert(1, {"time_ms": 20,
            "pose": dict(value["motion"]["keyframes"][-1]["pose"])})
        weak = self.run_motion(value)
        value["settings"]["torque_nm"] = .3
        strong = self.run_motion(value)
        self.assertLessEqual(weak["metrics"]["max_effort_nm"], .020001)
        self.assertLessEqual(strong["metrics"]["max_effort_nm"], .300001)
        weak_at_80 = next(f for f in weak["frames"] if f["time_ms"] >= 80)["pose"]["arm_negative_x"]
        strong_at_80 = next(f for f in strong["frames"] if f["time_ms"] >= 80)["pose"]["arm_negative_x"]
        self.assertGreater(strong_at_80, weak_at_80 + 20)
        self.assertNotEqual(weak["request_sha256"], strong["request_sha256"])
