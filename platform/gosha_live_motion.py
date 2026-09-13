"""Bounded voice actions over the robot's existing calibrated Motion Live core."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import time
import uuid

PROTOCOL = "gosha.motion.live.v1"
JOINTS = {"arm_positive_x", "leg_negative_x", "leg_positive_x", "foot_negative_x", "foot_positive_x"}


def credentials_for(robot_id):
    path = os.environ.get("GOSHA_VOICE_MOTION_CREDENTIALS")
    if not path:
        return None
    source = Path(path)
    if not source.is_file() or source.stat().st_mode & 0o077:
        raise ValueError("motion_credentials_permissions")
    records = json.loads(source.read_text())
    if not isinstance(records, dict):
        raise ValueError("motion_credentials_invalid")
    record = records.get(robot_id)
    if not isinstance(record, dict):
        return None
    key, calibration = record.get("access_key"), record.get("calibration_id")
    if not isinstance(key, str) or not 16 <= len(key) <= 128 or not isinstance(calibration, str) or not re.fullmatch(r"[a-f0-9]{64}", calibration):
        raise ValueError("motion_credentials_invalid")
    return {"access_key": key, "calibration_id": calibration}


class MotionDevice:
    def __init__(self, send_json, credentials=None):
        self.send_json, self.credentials = send_json, credentials
        self.pending = {}
        self.lock = asyncio.Lock()
        self.session_id = None
        self.seq = 0
        self.worker = None
        self.state = "idle"
        self.last_pose = None

    def receive(self, payload):
        if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
            return
        request_id = payload.get("request_id")
        if not isinstance(request_id, str):
            return
        future = self.pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(payload)

    async def rpc(self, op, **fields):
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send_json("motion_live", payload={"protocol": PROTOCOL, "op": op,
                                                         "request_id": request_id, **fields})
            result = await asyncio.wait_for(future, 2)
            if result.get("op") == "error":
                raise RuntimeError("motion_device_rejected")
            return result
        finally:
            self.pending.pop(request_id, None)

    async def status(self):
        caps = await self.rpc("hello")
        pose = caps.get("commanded_pose")
        if caps.get("op") != "capabilities" or not isinstance(pose, dict) or any(type(pose.get(j)) not in (int, float) for j in JOINTS):
            raise ValueError("motion_capabilities_invalid")
        if self.credentials and caps.get("calibration_id") != self.credentials["calibration_id"]:
            raise ValueError("motion_calibration_mismatch")
        return {"status": "confirmed", "motion_state": self.state,
                "motion_allowed": caps.get("motion_allowed") is True,
                "right_arm_initialized": caps.get("right_arm_initialized") is True,
                "commanded_pose": caps.get("commanded_pose"),
                "physical_angles_measured": False}

    def check_profile(self, caps, *, require_ready=True):
        if not self.credentials or caps.get("calibration_id") != self.credentials["calibration_id"]:
            raise ValueError("motion_calibration_mismatch")
        if caps.get("op") != "capabilities" or caps.get("calibrated") is not True:
            raise ValueError("motion_capabilities_invalid")
        limits = caps.get("joint_limits")
        if not isinstance(limits, list) or {item.get("id") for item in limits if isinstance(item, dict)} != JOINTS:
            raise ValueError("motion_joint_profile_unsupported")
        for limit in limits:
            if limit.get("max_speed_dps", 0) < 5 or limit.get("min", 1) > 0 or limit.get("max", -1) < 0:
                raise ValueError("motion_limits_invalid")
            if limit["id"] == "arm_positive_x" and limit["min"] > -15:
                raise ValueError("motion_wave_outside_limits")
        if require_ready and (caps.get("motion_allowed") is not True or caps.get("right_arm_initialized") is not True):
            raise ValueError("right_arm_initialization_required")
        if require_ready and caps.get("servo_degrees", {}).get("right_hand") != 135:
            raise ValueError("motion_right_arm_neutral_mismatch")
        if caps.get("active_motion") is not None:
            raise ValueError("motion_already_active")
        pose = caps.get("commanded_pose")
        if not isinstance(pose, dict) or any(pose.get(joint) != 0 for joint in JOINTS):
            raise ValueError("motion_neutral_required")

    async def enable_right_arm(self):
        async with self.lock:
            if self.worker and not self.worker.done():
                return {"status": "rejected", "reason": "motion_busy"}
            caps = await self.rpc("hello")
            self.check_profile(caps, require_ready=False)
            if caps.get("right_arm_initialized") is not True:
                # Only this explicitly named user action initializes the servo.
                caps = await self.rpc("initialize_right_arm", **self.credentials)
            self.check_profile(caps)
            return {"status": "confirmed", "right_arm_initialized": True}

    async def wave(self):
        async with self.lock:
            if self.worker and not self.worker.done():
                return {"status": "rejected", "reason": "motion_busy"}
            started = time.monotonic()
            caps = await self.rpc("hello")
            round_trip = time.monotonic() - started
            self.check_profile(caps)
            # Existing 300 ms firmware lease remains authoritative. A slow route
            # cannot be made reliable by silently weakening that safety timer.
            if round_trip >= caps.get("watchdog_ms", 0) / 2000 or caps.get("max_rate_hz", 0) < 10:
                return {"status": "rejected", "reason": "motion_connection_too_slow"}
            armed = await self.rpc("arm", **self.credentials)
            if armed.get("op") != "armed" or not isinstance(armed.get("session_id"), str):
                raise ValueError("motion_arm_not_confirmed")
            self.session_id, self.seq = armed["session_id"], 0
            self.state = "running"
            self.worker = asyncio.create_task(self.wave_loop())
            return {"status": "in_progress", "action": "wave_right_hand", "amplitude_degrees": 15,
                    "completion_confirmed": False}

    async def command(self, op, **fields):
        self.seq += 1
        result = await self.rpc(op, session_id=self.session_id, seq=self.seq, **fields)
        if result.get("session_id") != self.session_id or result.get("op") != ("stopped" if op == "stop" else "ack"):
            raise ValueError("motion_ack_invalid")
        if op != "stop" and result.get("seq") != self.seq:
            raise ValueError("motion_sequence_mismatch")
        self.last_pose = result.get("commanded_pose")
        return result

    async def wave_loop(self):
        try:
            for angle in (-15, 0):
                target = dict.fromkeys(JOINTS, 0)
                target["arm_positive_x"] = angle
                started = time.monotonic()
                deadline = started + 5
                while True:
                    tick = time.monotonic()
                    await self.command("pose", target=target, speed_dps=5)
                    if (time.monotonic() - started >= 3 and isinstance(self.last_pose, dict)
                            and all(self.last_pose.get(joint) == value for joint, value in target.items())):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("motion_target_not_reached")
                    await asyncio.sleep(max(0, tick + 0.1 - time.monotonic()))
            self.state = "finished"
        except asyncio.CancelledError:
            self.state = "interrupted"
            raise
        except Exception:
            self.state = "unknown"
        finally:
            try:
                await self.command("stop")
            except Exception:
                self.state = "unknown"
            self.session_id = None

    async def stop(self):
        async with self.lock:
            if self.worker and not self.worker.done():
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)
            # Stop holds the last pose; it must never be described as a Home.
            return {"status": "unknown" if self.state == "unknown" else "confirmed",
                    "motion_state": self.state, "scope": "voice_owned_motion_only",
                    "returned_to_neutral": self.state == "finished"}

    async def call(self, name):
        try:
            if name == "robot_motion_status":
                return await self.status()
            if name == "robot_wave_right_hand":
                return await self.wave()
            if name == "robot_enable_right_arm":
                return await self.enable_right_arm()
            if name == "robot_stop_motion":
                return await self.stop()
        except ValueError as exc:
            # Exception strings here are fixed local reason codes only.
            return {"status": "rejected", "reason": str(exc)}
        except RuntimeError:
            return {"status": "failed", "reason": "motion_device_rejected"}
        except Exception:
            return {"status": "unknown", "reason": "motion_result_not_confirmed", "retry": False}

    async def close(self):
        with contextlib.suppress(Exception):
            await self.stop()
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()
