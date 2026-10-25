import test from "node:test";
import assert from "node:assert/strict";
import {
  PROFILE, createMotion, zeroPose, parseMotion, toControlAngle, toRobotAngle,
  validateRobotMotionCapabilities, prepareRobotMotionPackage, prepareRobotPackageUpload,
} from "../src/motion.js";
import { LIVE_PROTOCOL, LiveSession, validateCapabilities } from "../src/live.js";
import { liveSpeedLimit } from "../src/motion-speed.js";
import {
  liveInspectorJointViewModel, liveSliderTargetPose, liveSpeedViewModel, livePlaybackScale,
} from "../src/live-panel.js";
import { prepareInstallation } from "../src/workflow.js";

function fiveCaps(speed = 10) {
  return {
    protocol: LIVE_PROTOCOL, op: "capabilities", profile_id: PROFILE.id,
    mode: "motion_editor", calibration_id: "a".repeat(64), calibrated: false,
    motion_allowed: true, right_arm_initialized: true, commissioning: false,
    watchdog_ms: 300, max_rate_hz: 10, stop_mode: "hold_setpoint", auth_required: true,
    commanded_pose: zeroPose(), feedback: { measured_position: false, imu: false },
    ...(speed > 10 ? { speed_profile: "working-v1" } : {}),
    joint_limits: PROFILE.joints.slice(1).map((j) => ({
      id: j.id, min: j.id === "arm_positive_x" ? -70 : j.min,
      max: j.id === "arm_positive_x" ? 45 : j.max, max_speed_dps: speed,
    })),
  };
}
function sixCaps() {
  const caps = fiveCaps(120);
  caps.joint_limits.unshift({ id: "arm_negative_x", min: 0, max: 15, max_speed_dps: 5 });
  return caps;
}
function motion(frames, name = "Шесть приводов") {
  return { ...createMotion(name), id: "stable-five-axis", interpolation: "linear",
    duration_ms: frames.at(-1)[0], keyframes: frames.map(([time_ms, pose]) => ({
      time_ms, pose: { ...zeroPose(), ...pose },
    })) };
}
function session(caps = sixCaps()) {
  let now = 1000, request = 0;
  const socket = { readyState: 0, bufferedAmount: 0, sent: [], closed: false,
    send(value) { this.sent.push(JSON.parse(value)); },
    close() { this.closed = true; this.readyState = 3; },
    receive(value) { this.onmessage({ data: JSON.stringify(value) }); },
  };
  const live = new LiveSession({ socketFactory: () => socket, now: () => now,
    makeId: () => `request-${++request}` });
  live.connectUsb("a".repeat(24)); socket.readyState = 1; socket.onopen();
  socket.receive({ ...caps, request_id: live.requestId });
  const arm = () => {
    live.arm("synthetic-six-axis-key");
    socket.receive({ protocol: LIVE_PROTOCOL, op: "armed", request_id: live.requestId,
      session_id: "session-six-axis-001", calibration_id: caps.calibration_id });
  };
  const ack = (pose) => socket.receive({ protocol: LIVE_PROTOCOL, op: "ack",
    session_id: live.sessionId, seq: live.pending.seq,
    commanded_pose: pose, measured_pose: null, tilt: null });
  return { live, socket, arm, ack, advance(ms) { now += ms; live.tick(); } };
}

test("five and explicit bounded six actuator capabilities share the same profile", () => {
  for (const caps of [fiveCaps(), fiveCaps(120), sixCaps()]) {
    assert.equal(validateCapabilities(caps).joint_limits.length, caps.joint_limits.length);
    assert.equal(validateRobotMotionCapabilities(caps).joint_limits.length, caps.joint_limits.length);
  }
  for (const change of [
    (caps) => { caps.joint_limits[0].min = -1; },
    (caps) => { caps.joint_limits[0].max = 16; },
    (caps) => { caps.joint_limits[0].max_speed_dps = 100; },
    (caps) => { caps.joint_limits[1].id = "arm_negative_x"; },
    (caps) => { caps.joint_limits.pop(); },
    (caps) => { caps.joint_limits[1].id = "unknown"; },
  ]) {
    const caps = sixCaps(); change(caps);
    assert.throws(() => validateCapabilities(caps));
    assert.throws(() => validateRobotMotionCapabilities(caps));
  }
});

test("left inspector is available only from capabilities and retains the anatomical signs", () => {
  for (const count of [5, 6]) {
    const caps = validateCapabilities(count === 6 ? sixCaps() : fiveCaps());
    const state = { state: "armed", caps, telemetry: { commanded_pose: zeroPose() } };
    const view = liveInspectorJointViewModel(state, "arm_negative_x");
    assert.equal(view.available, count === 6);
    assert.equal(view.disabled, count === 5);
    if (count === 6) {
      assert.equal(view.min, 0); assert.equal(view.max, 15);
      assert.equal(liveSliderTargetPose(state, "arm_negative_x", 15).arm_negative_x, 15);
      assert.equal(liveSliderTargetPose(state, "arm_negative_x", -1).arm_negative_x, 0);
    }
  }
  assert.equal(toControlAngle("arm_negative_x", 15), 15);
  assert.equal(toRobotAngle("arm_positive_x", 15), -15);
  assert.equal(toRobotAngle("foot_negative_x", 15), -15);
  assert.equal(toRobotAngle("foot_positive_x", 15), 15);
  assert.ok(Object.values(zeroPose()).every(value => value === 0));
});

test("selected left speed is five while other axes and the shared session retain 120", () => {
  const caps = validateCapabilities(sixCaps());
  assert.equal(liveSpeedLimit(caps), 120);
  assert.equal(liveSpeedLimit(caps, "arm_negative_x"), 5);
  for (const joint of caps.joint_limits.slice(1)) assert.equal(liveSpeedLimit(caps, joint.id), 120);
  const state = { state: "armed", caps, speed: 120 };
  const left = liveSpeedViewModel(state, "arm_negative_x");
  assert.equal(left.max, 5); assert.equal(left.value, 5); assert.match(left.range, /Левая рука/);
  const right = liveSpeedViewModel(state, "arm_positive_x");
  assert.equal(right.max, 120); assert.equal(right.value, 120);
  assert.equal(state.speed, 120, "selection never mutates the shared speed or sends a command");
  const legacy = fiveCaps(120); legacy.joint_limits[1].max_speed_dps = 30;
  assert.equal(liveSpeedLimit(validateCapabilities(legacy)), 30);
});

test("six-axis manual target and STOP preserve the acknowledged pose without returning or rearming", () => {
  const h = session(); h.arm();
  h.live.setSpeed(120);
  assert.deepEqual(h.socket.sent.map(item => item.op), ["hello", "arm"]);
  h.live.moveTo({ ...zeroPose(), arm_negative_x: 15, leg_negative_x: 30 }); h.advance(100);
  const sent = h.socket.sent.at(-1);
  assert.equal(sent.speed_dps, 120); assert.equal(Object.keys(sent.target).length, 6);
  assert.equal(sent.target.arm_negative_x, 15);
  const acknowledged = { ...zeroPose(), arm_negative_x: 1, leg_negative_x: 12 };
  h.ack(acknowledged);
  assert.deepEqual(h.live.lastTelemetry.commanded_pose, acknowledged);
  assert.equal(h.live.lastTelemetry.measured_pose, null);
  h.live.stop(); assert.equal(h.socket.sent.at(-1).op, "stop");
  h.socket.receive({ protocol: LIVE_PROTOCOL, op: "stopped", session_id: h.live.sessionId,
    commanded_pose: acknowledged, measured_pose: null, tilt: null });
  assert.equal(h.live.state, "ready"); assert.equal(h.live.target, null);
  const count = h.socket.sent.length; h.advance(500);
  assert.equal(h.socket.sent.length, count);
  assert.deepEqual(h.live.lastTelemetry.commanded_pose, acknowledged);
  h.live.disconnect();
});

test("six-axis disconnect cancels the target, sends STOP and never resumes after reconnect", () => {
  const h = session(); h.arm();
  h.live.moveTo({ ...zeroPose(), arm_negative_x: 15 }); h.advance(100);
  h.live.requestDisconnect(); assert.equal(h.socket.sent.at(-1).op, "stop");
  h.socket.receive({ protocol: LIVE_PROTOCOL, op: "stopped", session_id: h.live.sessionId,
    commanded_pose: { ...zeroPose(), arm_negative_x: 1 }, measured_pose: null, tilt: null });
  assert.equal(h.live.state, "disconnected"); assert.equal(h.live.target, null);
  assert.equal(h.socket.closed, true); assert.equal(h.live.caps, null);
  const count = h.socket.sent.length; h.advance(1000); assert.equal(h.socket.sent.length, count);
});

test("out of range left manual goals are rejected before a pose is sent", () => {
  for (const value of [-1, 16]) {
    const h = session(); h.arm();
    assert.throws(() => h.live.moveTo({ ...zeroPose(), arm_negative_x: value }), /цель вне/);
    assert.equal(h.socket.sent.some(item => item.op === "pose"), false);
    assert.equal(h.socket.sent.at(-1).op, "stop"); h.live.disconnect();
  }
});

test("six-axis export validates speed individually and preserves file identity through update", () => {
  const project = motion([[0, {}], [3000, { arm_negative_x: 15, leg_negative_x: 30, arm_positive_x: -15 }], [6000, {}]]);
  const before = JSON.stringify(project);
  const prepared = prepareRobotMotionPackage(parseMotion(before), sixCaps());
  assert.equal(prepared.ok, true); assert.equal(prepared.package.active_joints.length, 6);
  assert.equal(prepared.package.constraints[0].max_speed_dps, 5);
  assert.equal(prepared.package.safety.max_required_speed_dps, 10);
  assert.equal(prepared.package.keyframes[1].target.arm_negative_x, 15);
  assert.equal(prepared.package.keyframes[1].target.arm_positive_x, -15);
  assert.equal(JSON.stringify(project), before);
  const first = prepareInstallation(motion([[0, {}], [6000, {}]]), fiveCaps());
  const updated = prepareInstallation(project, sixCaps(), first.record);
  assert.equal(updated.record.package_id, first.record.package_id);
  assert.equal(updated.record.revision, 2);
  const fastLeft = prepareRobotMotionPackage(motion([[0, {}], [2500, { arm_negative_x: 15, leg_negative_x: 30 }]]), sixCaps());
  assert.equal(fastLeft.ok, false);
  assert.deepEqual(fastLeft.issues.map(item => [item.code, item.joint_id]), [["joint_speed_exceeded", "arm_negative_x"]]);
});

test("legacy five-axis export retains the original payload bytes, checksum and package ID", () => {
  const project = motion([[0, {}], [3000, { arm_positive_x: -15, foot_negative_x: -12, foot_positive_x: 12 }], [6000, {}]], "Старый пакет");
  const prepared = prepareRobotMotionPackage(project, fiveCaps());
  const upload = prepareRobotPackageUpload(prepared.package);
  assert.equal(upload.package_id, "motion-stable-five-axis");
  assert.equal(upload.crc32, 1483389911); assert.equal(upload.total_size, 1438);
  assert.equal(prepared.package.active_joints.includes("arm_negative_x"), false);
  const h = session();
  const metadata = h.live.packageMetadataFromRecord({ package_id: upload.package_id,
    profile_id: PROFILE.id, calibration_id: "a".repeat(64), payload_size: upload.total_size,
    crc32: upload.crc32, active_joint_count: 5 });
  assert.equal(metadata.compatible, true); assert.equal(metadata.active_joint_count, 5);
  h.live.disconnect();
});

test("Live playback keeps fast other axes when LEFT is stationary and caps LEFT only when it moves", () => {
  const caps = validateCapabilities(sixCaps());
  const fastLeg = motion([[0, {}], [250, { leg_negative_x: 30 }], [500, {}]]);
  assert.equal(livePlaybackScale(fastLeg, caps, 120), 1);
  const slowLeft = motion([[0, {}], [3000, { arm_negative_x: 15, leg_negative_x: 30 }]]);
  assert.equal(livePlaybackScale(slowLeft, caps, 120), 1);
  const fastLeft = motion([[0, {}], [1000, { arm_negative_x: 15, leg_negative_x: 30 }]]);
  assert.equal(livePlaybackScale(fastLeft, caps, 120), 1 / 3);
});
