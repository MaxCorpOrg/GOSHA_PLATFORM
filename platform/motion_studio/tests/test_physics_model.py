import copy
import json
from pathlib import Path
import subprocess
import unittest
import xml.etree.ElementTree as ET

from physics.model import DEFAULT_SETTINGS, IDS, JOINTS, PROFILE, fingerprint, mjcf, pose_at, validate_request


def request(duration=500, **settings):
    return {"model_id": PROFILE["id"], "settings": settings, "motion": {
        "schema_version": 1, "profile_id": PROFILE["profile_id"], "profile_version": 1,
        "units": "relative_degrees", "preview_only": True, "hardware_validated": False,
        "id": "physics-test", "name": "Проверка", "duration_ms": duration, "interpolation": "smooth",
        "keyframes": [{"time_ms": 0, "pose": dict.fromkeys(IDS, 0)},
                      {"time_ms": duration, "pose": dict.fromkeys(IDS, 0)}]}}


class PhysicsModelTests(unittest.TestCase):
    def test_validation_does_not_modify_the_authoring_document(self):
        source = request()
        before = copy.deepcopy(source)
        value = validate_request(source)
        self.assertEqual(source, before)
        self.assertEqual(value["settings"], DEFAULT_SETTINGS)
        self.assertTrue(value["motion"]["preview_only"])
        self.assertFalse(value["motion"]["hardware_validated"])

    def test_bad_versions_units_angles_times_and_parameters_are_rejected(self):
        changes = [lambda d: d.update(model_id="wrong"), lambda d: d.update(extra=1),
                   lambda d: d["motion"].update(schema_version=True),
                   lambda d: d["motion"].update(profile_version=True),
                   lambda d: d["motion"].update(units="radians"),
                   lambda d: d["motion"].update(preview_only=False),
                   lambda d: d["motion"].update(hardware_validated=True),
                   lambda d: d["motion"].update(duration_ms=499),
                   lambda d: d["motion"].update(interpolation=[]),
                   lambda d: d["motion"]["keyframes"][0].update(time_ms=1),
                   lambda d: d["motion"]["keyframes"][1].update(time_ms=0),
                   lambda d: d["motion"]["keyframes"][0]["pose"].update(arm_negative_x=100),
                   lambda d: d["motion"]["keyframes"][0]["pose"].update(arm_negative_x=float("nan")),
                   lambda d: d["motion"]["keyframes"][0]["pose"].update(arm_negative_x=True),
                   lambda d: d["motion"]["keyframes"][0]["pose"].pop("leg_positive_x"),
                   lambda d: d["settings"].update(total_mass_g=10),
                   lambda d: d["settings"].update(torque_nm=float("inf")),
                   lambda d: d["settings"].update(friction=True),
                   lambda d: d["settings"].update(servo_device="usb")]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                value = request(); change(value)
                with self.assertRaises(ValueError):
                    validate_request(value)

    def test_interpolation_matches_actual_studio_for_all_modes_and_keyframe_boundaries(self):
        studio = Path(__file__).resolve().parents[1]
        for mode in ("smooth", "linear", "hold"):
            value = request(1001)["motion"]
            value["interpolation"] = mode
            value["keyframes"].insert(1, {"time_ms": 251, "pose": dict.fromkeys(IDS, 20)})
            times = [0, 1, 125, 250, 251, 252, 700, 1001, 1200]
            script = "import {poseAt} from './src/motion.js'; const d=JSON.parse(process.argv[1]); console.log(JSON.stringify(d.times.map(t=>poseAt(d.motion,t))));"
            expected = json.loads(subprocess.check_output(["node", "--input-type=module", "-e", script,
                json.dumps({"motion": value, "times": times})], cwd=studio))
            for time_ms, pose in zip(times, expected):
                for key in IDS:
                    self.assertAlmostEqual(pose_at(value, time_ms)[key], pose[key], places=10)

    def test_mjcf_has_free_base_six_cad_axes_correct_mass_and_bounded_drives(self):
        settings = {**DEFAULT_SETTINGS, "total_mass_g": 850, "torque_nm": .27, "friction": .4}
        root = ET.fromstring(mjcf(settings))
        self.assertEqual(len(root.findall(".//freejoint")), 1)
        self.assertEqual(len(root.findall(".//body/joint")), 6)
        self.assertAlmostEqual(sum(float(g.attrib.get("mass", 0)) for g in root.findall(".//geom")), .85)
        for joint in JOINTS:
            node = root.find(f".//joint[@name='{joint['id']}']")
            axis = list(map(float, node.attrib["axis"].split()))
            self.assertEqual(axis["xyz".index(joint["axis"])], joint["modelDirection"])
        for drive in root.findall("actuator/position"):
            self.assertEqual(drive.attrib["forcerange"], "-0.27 0.27")
        self.assertEqual(root.find("option").attrib["gravity"], "0 0 -9.81")
        self.assertEqual(fingerprint(settings), fingerprint(dict(reversed(list(settings.items())))))
