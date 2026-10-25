"""A bounded, one-world Newton/MuJoCo CPU replay. stdin/stdout are JSON only."""
from __future__ import annotations

import contextlib
import json
import math
import os
from pathlib import Path
import sys
import time

from .model import IDS, PROFILE, fingerprint, mjcf, pose_at, validate_request


def simulate(request):
    import resource
    import numpy as np
    import mujoco
    import newton
    import warp as wp
    from newton.solvers import SolverMuJoCo

    started = time.monotonic()
    wp.config.log_level = wp.LOG_WARNING
    wp.config.kernel_cache_dir = str(Path(os.environ.get("GOSHA_PHYSICS_CACHE", "local_only/physics-cache")).resolve())
    wp.init()
    wp.set_device("cpu")
    motion, settings = request["motion"], request["settings"]
    builder = newton.ModelBuilder()
    builder.add_mjcf(mjcf(settings), parse_visuals=False, enable_self_collisions=True)
    model = builder.finalize(device="cpu")
    # Import order may differ from the editor order. Every mapping uses names.
    first_pose = motion["keyframes"][0]["pose"]
    q_starts = {label.rsplit("/", 1)[-1]: int(model.joint_q_start.numpy()[i]) for i, label in enumerate(model.joint_label)}
    q = model.joint_q.numpy()
    for key in IDS:
        q[q_starts[key]] = math.radians(first_pose[key])
    model.joint_q.assign(q)
    state, next_state, control = model.state(), model.state(), model.control()
    newton.eval_fk(model, model.joint_q, model.joint_qd, state)
    solver = SolverMuJoCo(model, use_mujoco_cpu=True, use_mujoco_contacts=True,
                           solver="newton", integrator="implicitfast", iterations=30, nconmax=128, njmax=256)
    mm, data = solver.mj_model, solver.mj_data
    def named_id(kind, count, key):
        matches = [i for i in range(count) if (mujoco.mj_id2name(mm, kind, i) or "").endswith("_" + key)]
        if len(matches) != 1:
            raise RuntimeError(f"Неоднозначное имя в модели Newton: {key}.")
        return matches[0]

    joint_ids = {key: named_id(mujoco.mjtObj.mjOBJ_JOINT, mm.njnt, key) for key in IDS}
    qpos_ids = {key: int(mm.jnt_qposadr[index]) for key, index in joint_ids.items()}
    dof_ids = {key: int(mm.jnt_dofadr[index]) for key, index in joint_ids.items()}
    torso_id = named_id(mujoco.mjtObj.mjOBJ_BODY, mm.nbody, "torso")
    body_ids = {key: named_id(mujoco.mjtObj.mjOBJ_BODY, mm.nbody, key) for key in IDS}
    body_keys = {value: key for key, value in body_ids.items()}
    body_keys[torso_id] = "torso"
    base_joint = named_id(mujoco.mjtObj.mjOBJ_JOINT, mm.njnt, "floating_base")
    base_qpos = int(mm.jnt_qposadr[base_joint])
    total_ms = motion["duration_ms"] + settings["settle_ms"]
    dt = .002
    total_steps = math.ceil(total_ms / 2)
    sample_steps = max(8, math.ceil(total_steps / 2400))
    frames = []
    commands = dict(first_pose)
    peak_error = peak_speed = peak_effort = peak_tilt = 0.0
    first_fall_ms = None
    slip = {"foot_negative_x": 0.0, "foot_positive_x": 0.0}
    last_feet = {}
    termination = "completed"
    force = np.zeros(6)
    targets = control.joint_target_q.numpy()

    def update_tilt(time_ms):
        nonlocal peak_tilt, first_fall_ms
        _, x, y, _ = data.qpos[base_qpos + 3:base_qpos + 7]
        up_z = float(1 - 2 * (x*x + y*y))
        tilt = math.degrees(math.acos(max(-1, min(1, up_z))))
        peak_tilt = max(peak_tilt, tilt)
        if tilt >= 50 and first_fall_ms is None:
            first_fall_ms = time_ms
        return tilt

    def capture(time_ms, desired):
        actual = {key: math.degrees(float(data.qpos[qpos_ids[key]])) for key in IDS}
        position = data.xpos[torso_id].copy()
        quaternion = data.xquat[torso_id].copy()  # MuJoCo wxyz -> viewer xyzw.
        tilt = update_tilt(time_ms)
        contacts = []
        stance = set()
        for index in range(data.ncon):
            contact = data.contact[index]
            body_pair = [int(mm.geom_bodyid[int(g)]) for g in contact.geom]
            if 0 not in body_pair:
                continue
            other = body_keys.get(next((body for body in body_pair if body != 0), -1), "")
            mujoco.mj_contactForce(mm, data, index, force)
            # MuJoCo may retain an inactive contact within its margin.
            if force[0] <= 1e-7:
                continue
            for key in slip:
                if other.startswith(key):
                    stance.add(key)
            if len(contacts) < 24:
                contacts.append({"body": other, "position_m": [round(float(v), 7) for v in contact.pos],
                                 "normal_force_n": round(float(force[0]), 5)})
        for key in slip:
            body_id = body_ids[key]
            xy = data.xpos[body_id, :2].copy()
            if key in stance and key in last_feet:
                slip[key] += float(np.linalg.norm(xy - last_feet[key])) * 1000
            if key in stance:
                last_feet[key] = xy
            else:
                last_feet.pop(key, None)
        frames.append({"time_ms": time_ms, "pose": {k: round(v, 5) for k, v in actual.items()},
                       "target": {k: round(v, 5) for k, v in desired.items()},
                       "base": {"position_m": [round(float(v), 8) for v in position],
                                "quaternion_xyzw": [round(float(v), 8) for v in (*quaternion[1:], quaternion[0])]},
                       "contacts": contacts, "tilt_deg": round(tilt, 3),
                       "effort_nm": {k: round(float(data.qfrc_actuator[dof_ids[k]]), 6) for k in IDS}})

    # The first recorded frame is the project's actual initial state. The
    # solver resynchronizes these CPU arrays from the Newton state on each step.
    for key in IDS:
        data.qpos[qpos_ids[key]] = math.radians(first_pose[key])
    for actuator in range(mm.nu):
        joint_id = int(mm.actuator_trnid[actuator, 0])
        key = next((key for key in IDS if joint_ids[key] == joint_id), None)
        if key is not None:
            data.ctrl[actuator] = math.radians(first_pose[key])
    mujoco.mj_forward(mm, data)
    capture(0, first_pose)
    for step in range(total_steps):
        clock = min((step + 1) * 2, total_ms)
        step_dt = (clock - step * 2) / 1000
        desired = pose_at(motion, min(clock, motion["duration_ms"]))
        limit = settings["target_speed_dps"] * step_dt
        for key in IDS:
            commands[key] += max(-limit, min(limit, desired[key] - commands[key]))
            targets[q_starts[key]] = math.radians(commands[key])
        control.joint_target_q.assign(targets)
        state.clear_forces()
        solver.step(state, next_state, control, None, step_dt)
        state, next_state = next_state, state
        if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all():
            termination = "numerical_error"
            break
        peak_error = max(peak_error, *(abs(desired[k] - math.degrees(data.qpos[qpos_ids[k]])) for k in IDS))
        peak_speed = max(peak_speed, *(abs(math.degrees(data.qvel[dof_ids[k]])) for k in IDS))
        peak_effort = max(peak_effort, *(abs(data.qfrc_actuator[dof_ids[k]]) for k in IDS))
        update_tilt(clock)
        if step == 0 or (step + 1) % sample_steps == 0 or step == total_steps - 1:
            # mj_step integrates qpos after computing body/contact fields.
            # Refresh those fields so all output channels share one time.
            mujoco.mj_forward(mm, data)
            capture(clock, desired)
        if np.linalg.norm(data.xpos[torso_id, :2]) > 1.5:
            termination = "out_of_bounds"
            if frames[-1]["time_ms"] != clock:
                mujoco.mj_forward(mm, data)
                capture(clock, desired)
            break
    if not frames:
        raise RuntimeError("Расчёт не вернул корректных кадров.")
    elapsed = time.monotonic() - started
    end = frames[-1]["base"]["position_m"]
    return {
        "schema_version": 1, "preview_only": True, "hardware_validated": False,
        "parameter_status": "assumed", "request_sha256": fingerprint(request),
        "model": {"id": PROFILE["id"], "sha256": fingerprint(PROFILE), "description": PROFILE},
        "engine": {"newton": newton.__version__, "mujoco": mujoco.__version__, "warp": wp.__version__,
                   "solver": "MuJoCo CPU", "device": "cpu", "timestep_s": dt},
        "settings": settings, "source_motion": motion, "motion_duration_ms": motion["duration_ms"],
        "duration_ms": frames[-1]["time_ms"], "frames": frames,
        "metrics": {"displacement_mm": [round(end[0]*1000, 3), round(end[1]*1000, 3)],
                    "distance_mm": round(math.hypot(end[0], end[1])*1000, 3),
                    "max_tilt_deg": round(peak_tilt, 3), "first_fall_ms": first_fall_ms,
                    "max_tracking_error_deg": round(float(peak_error), 3),
                    "max_joint_speed_dps": round(float(peak_speed), 3),
                    "max_effort_nm": round(float(peak_effort), 6),
                    "contact_travel_mm": {k: round(v, 3) for k, v in slip.items()},
                    "termination": termination, "compute_seconds": round(elapsed, 3),
                    "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)},
    }


def main():
    # Library logs never corrupt the result protocol.
    try:
        request = validate_request(json.load(sys.stdin))
        with contextlib.redirect_stdout(sys.stderr):
            result = simulate(request)
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except Exception as exc:
        json.dump({"error": str(exc)[:600]}, sys.stdout, ensure_ascii=False)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
