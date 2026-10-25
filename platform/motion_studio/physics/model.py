"""Versioned, explicitly estimated Gosha mechanics in SI units."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

PROFILE_PATH = Path(__file__).with_name("gosha-preview-v1.json")
PROFILE = json.loads(PROFILE_PATH.read_text())
JOINTS = PROFILE["joints"]
IDS = tuple(j["id"] for j in JOINTS)
DEFAULT_SETTINGS = {
    "total_mass_g": 400.0,
    "friction": 0.8,
    "torque_nm": 0.18,
    "target_speed_dps": 300.0,
    "settle_ms": 600,
}
SETTING_LIMITS = {
    "total_mass_g": (100, 3000), "friction": (0.05, 2.0),
    "torque_nm": (0.02, 3.0), "target_speed_dps": (5, 1200),
    "settle_ms": (0, 2000),
}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_request(value):
    if not isinstance(value, dict) or set(value) - {"motion", "settings", "model_id"}:
        raise ValueError("Нужны движение и параметры физической модели.")
    if value.get("model_id", PROFILE["id"]) != PROFILE["id"]:
        raise ValueError("Неизвестная физическая модель.")
    settings = dict(DEFAULT_SETTINGS)
    supplied = value.get("settings", {})
    if not isinstance(supplied, dict) or set(supplied) - set(settings):
        raise ValueError("Неизвестные параметры физики.")
    for key, number in supplied.items():
        low, high = SETTING_LIMITS[key]
        if not finite(number) or not low <= number <= high:
            raise ValueError(f"Параметр {key} должен быть от {low} до {high}.")
        settings[key] = number
    settings["settle_ms"] = round(settings["settle_ms"])
    motion = value.get("motion")
    if not isinstance(motion, dict) or type(motion.get("schema_version")) is not int or motion["schema_version"] != 1:
        raise ValueError("Нужен проект Motion Studio версии 1.")
    if (motion.get("profile_id") != PROFILE["profile_id"] or type(motion.get("profile_version")) is not int
            or motion.get("profile_version") != 1
            or motion.get("units") != "relative_degrees" or motion.get("preview_only") is not True
            or motion.get("hardware_validated") is not False):
        raise ValueError("Проект не соответствует профилю Гоши для предпросмотра.")
    duration = motion.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 500 <= duration <= 120000:
        raise ValueError("Длительность должна быть от 0,5 до 120 секунд.")
    if not isinstance(motion.get("interpolation"), str) or motion["interpolation"] not in {"linear", "smooth", "hold"}:
        raise ValueError("Неизвестная интерполяция.")
    frames = motion.get("keyframes")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 1000:
        raise ValueError("В проекте должно быть от 1 до 1000 поз.")
    previous = -1
    clean = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("Некорректная поза.")
        time_ms, pose = frame.get("time_ms"), frame.get("pose")
        if isinstance(time_ms, bool) or not isinstance(time_ms, int) or not previous < time_ms <= duration:
            raise ValueError("Время поз должно возрастать внутри длительности проекта.")
        if not isinstance(pose, dict) or set(pose) != set(IDS):
            raise ValueError("В каждой позе должны быть ровно шесть известных суставов.")
        for joint in JOINTS:
            angle = pose[joint["id"]]
            if not finite(angle) or not joint["min"] <= angle <= joint["max"]:
                raise ValueError(f"{joint['label']}: угол вне диапазона модели.")
        clean.append({"time_ms": time_ms, "pose": {key: float(pose[key]) for key in IDS}})
        previous = time_ms
    if clean[0]["time_ms"] != 0:
        raise ValueError("Первая поза должна быть на отметке 0.")
    return {"model_id": PROFILE["id"], "settings": settings, "motion": {
        "schema_version": 1, "profile_id": PROFILE["profile_id"], "profile_version": 1,
        "units": "relative_degrees", "preview_only": True, "hardware_validated": False,
        "id": str(motion.get("id", "preview"))[:80], "name": str(motion.get("name", "Движение"))[:80],
        "duration_ms": duration, "interpolation": motion["interpolation"], "keyframes": clean,
    }}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def pose_at(motion, time_ms):
    frames = motion["keyframes"]
    if time_ms <= 0:
        return dict(frames[0]["pose"])
    for index in range(1, len(frames)):
        b, a = frames[index], frames[index - 1]
        if time_ms <= b["time_ms"]:
            mix = (time_ms - a["time_ms"]) / (b["time_ms"] - a["time_ms"])
            if motion["interpolation"] == "smooth":
                mix = mix * mix * (3 - 2 * mix)
            elif motion["interpolation"] == "hold" and time_ms != b["time_ms"]:
                mix = 0
            return {key: a["pose"][key] + (b["pose"][key] - a["pose"][key]) * mix for key in IDS}
    return dict(frames[-1]["pose"])


def mjcf(settings):
    """Box proxies, CAD shaft frames, free torso; no visual mesh mass inference."""
    root = ET.Element("mujoco", model=PROFILE["id"])
    ET.SubElement(root, "compiler", angle="radian", inertiafromgeom="true")
    ET.SubElement(root, "option", timestep="0.002", gravity="0 0 -9.81", integrator="implicitfast")
    default = ET.SubElement(root, "default")
    ET.SubElement(default, "joint", damping="0.002", armature="0.00001")
    ET.SubElement(default, "geom", friction=f"{settings['friction']} 0.005 0.0001", condim="3",
                  solref="0.006 1", solimp="0.9 0.95 0.001", margin="0")
    world = ET.SubElement(root, "worldbody")
    ET.SubElement(world, "geom", name="floor", type="plane", pos="0 0 -0.09", size="2 2 0.01")
    torso = ET.SubElement(world, "body", name="torso")
    ET.SubElement(torso, "freejoint", name="floating_base")
    total_mass = settings["total_mass_g"] / 1000

    def geom(body, name, center, half, mass):
        ET.SubElement(body, "geom", name=name, type="box", pos=" ".join(map(str, center)),
                      size=" ".join(map(str, half)), mass=str(mass))

    geom(torso, "body_proxy", [0, 0, -0.011], [0.0545, 0.0365, 0.030], total_mass * .60)
    bodies = {}
    actuator = ET.SubElement(root, "actuator")
    for joint in JOINTS:
        parent = next((j for j in JOINTS if j["id"] == joint.get("parent")), None)
        pivot = [c / 1000 for c in joint["pivot"]]
        if parent:
            pivot = [c - p / 1000 for c, p in zip(pivot, parent["pivot"])]
        body = ET.SubElement(bodies[parent["id"]] if parent else torso, "body", name=joint["id"],
                             pos=" ".join(map(str, pivot)))
        bodies[joint["id"]] = body
        axis = [0, 0, 0]
        axis["xyz".index(joint["axis"])] = joint["modelDirection"]
        ET.SubElement(body, "joint", name=joint["id"], type="hinge", axis=" ".join(map(str, axis)),
                      range=f"{math.radians(joint['min'])} {math.radians(joint['max'])}", limited="true")
        side = 1 if joint["pivot"][0] > 0 else -1
        if joint["id"].startswith("arm"):
            geom(body, joint["id"] + "_proxy", [side*.022, -.002, -.024], [.022, .016, .031], total_mass*.06)
        elif joint["id"].startswith("leg"):
            geom(body, joint["id"] + "_proxy", [0, -.002, -.020], [.0103, .0218, .0195], total_mass*.06)
        else:
            geom(body, joint["id"] + "_sole", [side*.0107, 0, -.0155], [.0212, .032, .002], total_mass*.056)
            geom(body, joint["id"] + "_bracket", [0, 0, -.002], [.009, .020, .011], total_mass*.024)
        ET.SubElement(actuator, "position", name=joint["id"] + "_drive", joint=joint["id"],
                      kp="2.0", kv="0.035",
                      forcelimited="true", forcerange=f"{-settings['torque_nm']} {settings['torque_nm']}")
    return ET.tostring(root, encoding="unicode")
