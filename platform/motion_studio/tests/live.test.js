import test from "node:test";
import assert from "node:assert/strict";
import {
  LIVE_PROTOCOL,
  LiveSession,
  RIGHT_ARM_COMMISSIONING_MODE,
  RIGHT_ARM_INITIALIZATION_REASON,
  listUsbBridgePorts,
  robotSocketUrl,
  usbBridgeSocketUrl,
  validateCapabilities,
} from "../src/live.js";
import {
  createLiveStepPlan,
  liveSafeIntervalForJoint,
  liveSliderViewModel,
  liveInspectorJointViewModel,
  livePlaybackScale,
  liveStepSizeForJoint,
  liveStepViewModel,
  prepareLiveStepTarget,
} from "../src/live-panel.js";
import { PROFILE, createMotion, putPose, zeroPose } from "../src/motion.js";

const capabilities = () => ({
  protocol: LIVE_PROTOCOL,
  op: "capabilities",
  profile_id: PROFILE.id,
  motion_allowed: true,
  calibrated: true,
  calibration_id: "a".repeat(64),
  watchdog_ms: 300,
  max_rate_hz: 20,
  stop_mode: "hold_setpoint",
  auth_required: true,
  joint_limits: PROFILE.joints
    .slice(2)
    .map((j) => ({ id: j.id, min: -10, max: 10, max_speed_dps: 15 })),
  commanded_pose: zeroPose(),
  feedback: { measured_position: false, imu: false },
});
function harness(caps = capabilities) {
  let now = 1000;
  let id = 0;
  const sockets = [];
  const factory = (url) => {
    const socket = {
      url,
      readyState: 0,
      bufferedAmount: 0,
      sent: [],
      closed: false,
      send(raw) {
        this.sent.push(JSON.parse(raw));
      },
      close() {
        this.readyState = 3;
        this.closed = true;
      },
      receive(data) {
        this.onmessage({
          data: typeof data === "string" ? data : JSON.stringify(data),
        });
      },
    };
    sockets.push(socket);
    return socket;
  };
  const live = new LiveSession({
    socketFactory: factory,
    now: () => now,
    makeId: () => `request-${++id}`,
  });
  const open = () => {
    live.connect("192.168.1.123");
    const socket = sockets.at(-1);
    socket.readyState = 1;
    socket.onopen();
    return socket;
  };
  const ready = () => {
    const socket = open();
    socket.receive({ ...caps(), request_id: live.requestId });
    return socket;
  };
  const arm = () => {
    const socket = ready();
    live.arm("synthetic-test-key-only");
    socket.receive({
      protocol: LIVE_PROTOCOL,
      op: "armed",
      request_id: live.requestId,
      session_id: "session-00000000001",
      calibration_id: "a".repeat(64),
    });
    return socket;
  };
  const ack = (socket, commanded_pose = zeroPose()) =>
    socket.receive({
      protocol: LIVE_PROTOCOL,
      op: "ack",
      session_id: live.sessionId,
      seq: live.pending.seq,
      commanded_pose,
      measured_pose: null,
      tilt: null,
    });
  return {
    live,
    sockets,
    open,
    ready,
    arm,
    ack,
    advance(ms) {
      now += ms;
      live.tick();
    },
  };
}

test("USB bridge client accepts only opaque listed ports and hides filesystem paths", async () => {
  const port_id = "a".repeat(24);
  assert.match(
    usbBridgeSocketUrl(port_id),
    /^ws:\/\/127\.0\.0\.1:5177\/live\?port_id=a{24}$/,
  );
  for (const invalid of ["", "/dev/ttyACM0", "a".repeat(23), "g".repeat(24)])
    assert.throws(() => usbBridgeSocketUrl(invalid));

  const ports = await listUsbBridgePorts({
    fetchImpl: async (_url, options) => {
      assert.ok(options.signal instanceof AbortSignal);
      return {
        ok: true,
        async json() {
          return {
            protocol: LIVE_PROTOCOL,
            ports: [{ port_id, label: "ESP32-S3 USB 303a:1001", vid: "303a", pid: "1001" }],
          };
        },
      };
    },
  });
  assert.deepEqual(ports, [
    { port_id, label: "ESP32-S3 USB 303a:1001", vid: "303a", pid: "1001", busy: false },
  ]);

  await assert.rejects(
    () =>
      listUsbBridgePorts({
        fetchImpl: async () => ({
          ok: true,
          async json() {
            return {
              protocol: LIVE_PROTOCOL,
              ports: [
                {
                  port_id,
                  label: "ESP32-S3 USB 303a:1001",
                  vid: "303a",
                  pid: "1001",
                  path: "/dev/ttyACM0",
                },
              ],
            };
          },
        }),
      }),
    /неподдерживаемое устройство/,
  );
});

test("USB bridge port listing uses a bounded fetch timeout", async () => {
  await assert.rejects(
    () =>
      listUsbBridgePorts({
        timeoutMs: 1,
        fetchImpl: (_url, options) =>
          new Promise((resolve, reject) => {
            options.signal.addEventListener("abort", () =>
              reject(new Error("aborted")),
            );
            setTimeout(() => resolve({ ok: true, json: async () => ({}) }), 50);
          }),
      }),
    /служба USB недоступна/,
  );
  await assert.rejects(
    () =>
      listUsbBridgePorts({
        timeoutMs: 1,
        fetchImpl: async (_url, options) => ({
          ok: true,
          json: () =>
            new Promise((_resolve, reject) => {
              options.signal.addEventListener("abort", () =>
                reject(new Error("body aborted")),
              );
            }),
        }),
      }),
    /служба USB недоступна/,
  );
});

test("USB Live connection uses the local bridge and still starts with hello only", () => {
  const h = harness();
  const port_id = "b".repeat(24);
  h.live.connectUsb(port_id);
  const socket = h.sockets.at(-1);
  assert.equal(
    socket.url,
    `ws://127.0.0.1:5177/live?port_id=${port_id}`,
  );
  socket.readyState = 1;
  socket.onopen();
  assert.equal(socket.sent.length, 1);
  assert.equal(socket.sent[0].op, "hello");
  assert.deepEqual(Object.keys(socket.sent[0]).sort(), [
    "op",
    "protocol",
    "request_id",
  ]);
});

test("connection accepts LAN addresses only, never credentials, paths or public services", () => {
  assert.equal(robotSocketUrl("010.000.0.1"), "ws://10.0.0.1:8080/ws");
  for (const host of [
    "gosha.local",
    "192.168.1.7",
    "10.0.0.2",
    "172.16.0.8",
    "172.31.255.1",
  ])
    assert.match(robotSocketUrl(host), /^ws:\/\/.*:8080\/ws$/);
  for (const host of [
    "https://gosha.local",
    "user:pass@gosha.local",
    "8.8.8.8",
    "172.32.1.2",
    "192.168.1.999",
    "gosha.local/path",
    "gosha.local:80",
    "example.com",
    "",
  ])
    assert.throws(() => robotSocketUrl(host));
});
test("capabilities reject unsupported, uncalibrated, ungated or incompatible devices", () => {
  const mutations = [
    (c) => {
      c.protocol = "legacy";
    },
    (c) => {
      c.motion_allowed = false;
    },
    (c) => {
      c.calibrated = false;
    },
    (c) => {
      c.calibration_id = "";
    },
    (c) => {
      c.profile_id = "other";
    },
    (c) => {
      c.auth_required = false;
    },
    (c) => {
      c.stop_mode = "Home";
    },
    (c) => {
      c.watchdog_ms = 1000;
    },
    (c) => {
      c.max_rate_hz = 21;
    },
    (c) => {
      c.max_rate_hz = 5;
      c.watchdog_ms = 100;
    },
    (c) => {
      c.joint_limits = [];
    },
    (c) => {
      c.joint_limits[0].id = "arm-unrecognised";
    },
    (c) => {
      c.joint_limits[0].max_speed_dps = 31;
    },
    (c) => {
      c.joint_limits[0].max = 90;
    },
    (c) => {
      c.joint_limits.push(c.joint_limits[0]);
    },
    (c) => {
      c.commanded_pose.leg_negative_x = 11;
    },
    (c) => {
      c.commanded_pose.leg_negative_x = NaN;
    },
  ];
  assert.equal(validateCapabilities(capabilities()).joint_limits.length, 4);
  for (const change of mutations) {
    const c = capabilities();
    change(c);
    assert.throws(() => validateCapabilities(c));
  }
});
test("old firmware receives only hello; timeout never falls back to a motion or MCP call", () => {
  const h = harness();
  const socket = h.open();
  socket.receive({ jsonrpc: "2.0", error: { code: -32600 } });
  h.advance(3000);
  assert.equal(h.live.state, "fault");
  assert.equal(socket.sent.length, 1);
  assert.deepEqual(Object.keys(socket.sent[0]).sort(), [
    "op",
    "protocol",
    "request_id",
  ]);
  assert.equal(socket.sent[0].op, "hello");
  assert.equal(socket.closed, true);
});
test("no-motion firmware refuses before ARM and preserves the precise reason", () => {
  const h = harness();
  const socket = h.open();
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "capabilities",
    request_id: h.live.requestId,
    motion_allowed: false,
    reason: "no_motion_profile",
  });
  assert.equal(h.live.state, "fault");
  assert.match(h.live.reason, /без движений/);
  assert.throws(() => h.live.arm("synthetic-test-key-only"));
  assert.equal(socket.sent.length, 1);
});
test("armed idle sends no pose; held edits coalesce into the latest target with one outstanding ACK", () => {
  const h = harness();
  const socket = h.arm();
  h.advance(50);
  assert.equal(socket.sent.at(-1).op, "keepalive");
  h.ack(socket);
  h.live.hold({ ...zeroPose(), leg_positive_x: 1, arm_positive_x: 50 });
  h.advance(50);
  const first = socket.sent.at(-1);
  assert.equal(first.op, "pose");
  assert.deepEqual(
    Object.keys(first.target).sort(),
    PROFILE.joints
      .slice(2)
      .map((j) => j.id)
      .sort(),
  );
  assert.equal(first.target.leg_positive_x, 1);
  const sent = socket.sent.length;
  for (let n = 2; n <= 10; n++)
    h.live.setTarget({ ...zeroPose(), leg_positive_x: n });
  h.advance(150);
  assert.equal(socket.sent.length, sent);
  h.ack(socket);
  h.advance(50);
  assert.equal(socket.sent.at(-1).target.leg_positive_x, 10);
});
test("out-of-envelope pose cannot send and requests stop instead", () => {
  const h = harness();
  const socket = h.arm();
  assert.throws(() => h.live.hold({ ...zeroPose(), foot_positive_x: 11 }));
  assert.equal(h.live.state, "stopping");
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.equal(socket.sent.filter((m) => m.op === "pose").length, 0);
});
test("release sends STOP before further targets, requires confirmation, then a fresh arm", () => {
  const h = harness();
  const socket = h.arm();
  h.live.hold(zeroPose());
  h.advance(50);
  const sid = h.live.sessionId;
  h.live.release();
  const sent = socket.sent.length;
  assert.equal(h.live.state, "stopping");
  assert.equal(socket.sent.at(-1).op, "stop");
  h.advance(200);
  assert.equal(socket.sent.length, sent);
  assert.throws(() => h.live.hold(zeroPose()));
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "stopped",
    session_id: sid,
    commanded_pose: zeroPose(),
    measured_pose: null,
    tilt: null,
  });
  assert.equal(h.live.state, "ready");
  assert.equal(h.live.sessionId, null);
  assert.throws(() => h.live.hold(zeroPose()));
});
test("unacknowledged stop is a fault, never a claim that physical stopping succeeded", () => {
  const h = harness();
  h.arm();
  h.live.stop();
  h.advance(701);
  assert.equal(h.live.state, "fault");
  assert.match(h.live.reason, /состояние робота неизвестно/);
});
test("manual disconnect keeps the socket open until the robot confirms STOP", () => {
  const h = harness();
  const socket = h.arm();
  h.live.hold(zeroPose());
  h.advance(50);
  const sid = h.live.sessionId;
  h.live.requestDisconnect();
  assert.equal(h.live.state, "stopping");
  assert.equal(socket.closed, false);
  assert.equal(socket.sent.at(-1).op, "stop");
  h.advance(300);
  h.live.requestDisconnect();
  assert.equal(h.live.state, "stopping");
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "stopped",
    session_id: sid,
    commanded_pose: zeroPose(),
    measured_pose: null,
    tilt: null,
  });
  assert.equal(h.live.state, "disconnected");
  assert.equal(socket.closed, true);
  assert.match(h.live.reason, /подтвердил остановку/);
  assert.equal(h.live.lastTelemetry, null);
});
test("manual disconnect timeout retains an unknown-state fault and ignores a late STOP", () => {
  const h = harness();
  const socket = h.arm();
  const sid = h.live.sessionId;
  h.live.requestDisconnect();
  h.advance(600);
  h.live.requestDisconnect();
  h.advance(101);
  assert.equal(h.live.state, "fault");
  assert.match(h.live.reason, /состояние робота неизвестно/);
  assert.equal(socket.closed, true);
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "stopped",
    session_id: sid,
    commanded_pose: zeroPose(),
    measured_pose: null,
    tilt: null,
  });
  assert.equal(h.live.state, "fault");
});
test("a chosen speed is reduced to the slowest calibrated active joint", () => {
  const h = harness();
  const socket = h.arm();
  h.live.caps.joint_limits[0].max_speed_dps = 3;
  h.live.setSpeed(15);
  h.live.hold(zeroPose());
  h.advance(50);
  assert.equal(socket.sent.at(-1).speed_dps, 3);
});
test("missed, stale and out-of-order acknowledgements cannot renew the lease", () => {
  const h = harness();
  const socket = h.arm();
  h.live.hold(zeroPose());
  h.advance(50);
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "ack",
    session_id: h.live.sessionId,
    seq: 9999,
    commanded_pose: zeroPose(),
  });
  assert.ok(h.live.pending);
  h.advance(300);
  assert.equal(h.live.state, "fault");
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.equal(socket.closed, true);
});
test("late response after disconnect cannot resurrect an armed session", () => {
  const h = harness();
  const socket = h.ready();
  h.live.arm("synthetic-test-key-only");
  const request_id = h.live.requestId;
  h.live.disconnect();
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "armed",
    request_id,
    session_id: "session-00000000001",
    calibration_id: "a".repeat(64),
  });
  assert.equal(h.live.state, "disconnected");
  assert.equal(h.live.sessionId, null);
});
test("a pending ARM can be cancelled and can never start motion on its own", () => {
  const h = harness();
  const socket = h.ready();
  h.live.arm("synthetic-test-key-only");
  h.live.stop();
  assert.equal(h.live.state, "disconnected");
  assert.equal(socket.closed, true);
});
test("invalid telemetry and socket backpressure stop the session", () => {
  for (const action of [
    (h) => {
      h.sockets.at(-1).bufferedAmount = 5000;
      h.advance(50);
    },
    (h) => {
      h.advance(50);
      h.sockets.at(-1).receive({
        protocol: LIVE_PROTOCOL,
        op: "ack",
        session_id: h.live.sessionId,
        seq: h.live.pending.seq,
        commanded_pose: { ...zeroPose(), foot_negative_x: 25 },
      });
    },
  ]) {
    const h = harness();
    const socket = h.arm();
    h.live.hold(zeroPose());
    action(h);
    assert.equal(h.live.state, "fault");
    assert.equal(socket.closed, true);
  }
});
test("commanded pose is retained separately from absent measurements and tilt", () => {
  const h = harness();
  const socket = h.arm();
  h.live.hold(zeroPose());
  h.advance(50);
  h.ack(socket);
  assert.deepEqual(h.live.lastTelemetry.commanded_pose, zeroPose());
  assert.equal(h.live.lastTelemetry.measured_pose, null);
  assert.equal(h.live.lastTelemetry.tilt, null);
  h.live.disconnect();
  assert.equal(h.live.lastTelemetry, null);
});
test("live playback slows the timeline enough for the chosen joint speed, and rejects discontinuities", () => {
  const caps = validateCapabilities(capabilities());
  const motion = putPose(createMotion(), 1000, {
    ...zeroPose(),
    foot_positive_x: 10,
  });
  assert.equal(
    livePlaybackScale({ ...motion, interpolation: "linear" }, caps, 5),
    0.5,
  );
  assert.equal(livePlaybackScale(motion, caps, 5), 1 / 3);
  assert.throws(() =>
    livePlaybackScale({ ...motion, interpolation: "hold" }, caps, 5),
  );
  assert.throws(() =>
    livePlaybackScale(
      putPose(motion, 2000, { ...zeroPose(), foot_positive_x: 15 }),
      caps,
      5,
    ),
  );
  assert.throws(() =>
    livePlaybackScale(
      putPose(motion, 2000, { ...zeroPose(), arm_positive_x: 30 }),
      caps,
      5,
    ),
  );
});

test("STOP reports the final applied command even when it differs from the previous ACK", () => {
  const h = harness();
  const socket = h.arm();
  h.live.hold({ ...zeroPose(), foot_negative_x: 10 });
  h.advance(50);
  h.ack(socket);
  const sid = h.live.sessionId;
  h.live.stop();
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "stopped",
    session_id: sid,
    commanded_pose: { ...zeroPose(), foot_negative_x: 1 },
    measured_pose: null,
    tilt: null,
  });
  assert.equal(h.live.state, "ready");
  assert.equal(h.live.lastTelemetry.commanded_pose.foot_negative_x, 1);
  assert.equal(h.live.lastTelemetry.measured_pose, null);
});

const commissioningCaps = () => ({
  ...capabilities(),
  mode: "commissioning",
  calibrated: false,
  commissioning: true,
  joint_limits: PROFILE.joints.slice(2).map((j) => ({
    id: j.id, min: -1, max: 1, max_speed_dps: 1,
  })),
});

const rightArmCaps = (initialized = true, rightExtent = 5) => ({
  ...capabilities(),
  mode: RIGHT_ARM_COMMISSIONING_MODE,
  calibrated: false,
  commissioning: true,
  motion_allowed: initialized,
  reason: initialized ? undefined : RIGHT_ARM_INITIALIZATION_REASON,
  right_arm_initialized: initialized,
  joint_limits: [
    {
      id: "arm_positive_x",
      min: -rightExtent,
      max: rightExtent,
      max_speed_dps: 1,
    },
    ...PROFILE.joints.slice(2).map((j) => ({
      id: j.id, min: -1, max: 1, max_speed_dps: 1,
    })),
  ],
});

test("commissioning stays explicitly uncalibrated and rejects wider or ambiguous permissions", () => {
  const caps = validateCapabilities(commissioningCaps());
  assert.equal(caps.commissioning, true);
  assert.equal(caps.calibrated, false);
  for (const mutate of [
    c => { c.calibrated = true; },
    c => { c.commissioning = "true"; },
    c => { delete c.mode; },
    c => { c.mode = "verified"; },
    c => { c.joint_limits[0].max = 2; },
    c => { c.joint_limits[0].min = -2; },
    c => { c.joint_limits[0].max_speed_dps = 2; },
    c => { c.joint_limits[0].id = "arm_positive_x"; },
    c => { c.joint_limits.pop(); },
    c => { c.watchdog_ms = 500; },
  ]) {
    const data = commissioningCaps();
    mutate(data);
    assert.throws(() => validateCapabilities(data));
  }
  assert.throws(() => livePlaybackScale(createMotion(), caps, 1), /по одному суставу/);
});

test("commissioning can send one slow joint step but stops before a second joint", () => {
  const h = harness(commissioningCaps);
  const socket = h.arm();
  assert.equal(h.live.speed, 1);
  assert.throws(() => h.live.setSpeed(2));
  h.live.hold({ ...zeroPose(), leg_negative_x: 1 });
  h.advance(50);
  assert.equal(socket.sent.at(-1).op, "pose");
  assert.equal(socket.sent.at(-1).speed_dps, 1);
  assert.deepEqual(socket.sent.at(-1).target, {
    leg_negative_x: 1, leg_positive_x: 0, foot_negative_x: 0, foot_positive_x: 0,
  });
  h.ack(socket);
  h.live.setTarget(zeroPose());
  assert.throws(() => h.live.setTarget({ ...zeroPose(), foot_positive_x: 1 }));
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.equal(socket.sent.filter(m => m.op === "pose").length, 1);
});

test("commissioning rejects multiple joints, inactive arms, and a two-degree session step", () => {
  for (const target of [
    { ...zeroPose(), leg_negative_x: 1, leg_positive_x: 1 },
    { ...zeroPose(), arm_positive_x: 1 },
  ]) {
    const h = harness(commissioningCaps);
    const socket = h.arm();
    assert.throws(() => h.live.hold(target));
    assert.equal(socket.sent.at(-1).op, "stop");
    assert.ok(socket.sent.every(m => m.op !== "pose"));
  }
  const h = harness(() => ({
    ...commissioningCaps(), commanded_pose: { ...zeroPose(), leg_negative_x: -1 },
  }));
  const socket = h.arm();
  assert.throws(() => h.live.hold({ ...zeroPose(), leg_negative_x: 1 }));
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.ok(socket.sent.every(m => m.op !== "pose"));
});

test("commissioning rejects ACK and STOP that report a different or extra moving joint", () => {
  for (const op of ["ack", "stopped"]) {
    for (const reported of [
      { ...zeroPose(), leg_negative_x: 1, foot_positive_x: 1 },
      { ...zeroPose(), foot_positive_x: 1 },
      { ...zeroPose(), arm_positive_x: 1 },
    ]) {
      const h = harness(commissioningCaps);
      const socket = h.arm();
      h.live.hold({ ...zeroPose(), leg_negative_x: 1 });
      h.advance(50);
      const session_id = h.live.sessionId;
      const seq = h.live.pending.seq;
      if (op === "stopped") h.live.release();
      socket.receive({ protocol: LIVE_PROTOCOL, op, session_id, seq,
        commanded_pose: reported, measured_pose: null, tilt: null });
      assert.equal(h.live.state, "fault");
      assert.equal(socket.closed, true);
      assert.deepEqual(h.live.lastTelemetry.commanded_pose, zeroPose());
    }
  }
  const h = harness(commissioningCaps);
  const socket = h.arm();
  h.advance(50);
  socket.receive({ protocol: LIVE_PROTOCOL, op: "ack", session_id: h.live.sessionId,
    seq: h.live.pending.seq, commanded_pose: { ...zeroPose(), leg_negative_x: 1 },
    measured_pose: null, tilt: null });
  assert.equal(h.live.state, "fault", "Idle session cannot report an unrequested movement");
});

test("right-arm commissioning waits for explicit initialization and ignores commands before init", () => {
  const h = harness(() => rightArmCaps(false));
  const socket = h.ready();
  assert.equal(h.live.state, "init_required");
  assert.equal(h.live.caps.initialization_required, true);
  assert.equal(h.live.caps.right_arm_initialized, false);
  assert.throws(() => h.live.arm("synthetic-test-key-only"));
  assert.throws(() => h.live.hold(zeroPose()));
  h.advance(50);
  assert.equal(socket.sent.length, 1);
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "ack",
    session_id: "session-ignored-before-init",
    seq: 1,
    commanded_pose: { ...zeroPose(), arm_positive_x: -5 },
    measured_pose: null,
    tilt: null,
  });
  socket.receive({ protocol: LIVE_PROTOCOL, op: "legacy_motion" });
  assert.equal(h.live.state, "init_required");
  assert.equal(socket.sent.length, 1);
});

test("right-arm initialization has a distinct wire step and never arms automatically", () => {
  const h = harness(() => rightArmCaps(false));
  const socket = h.ready();
  h.live.initializeRightArm("synthetic-test-key-only");
  const init = socket.sent.at(-1);
  assert.equal(init.op, "initialize_right_arm");
  assert.equal(init.calibration_id, "a".repeat(64));
  assert.equal(init.access_key, "synthetic-test-key-only");
  assert.equal(h.live.state, "initializing_right_arm");
  const request_id = h.live.requestId;
  socket.receive({ ...rightArmCaps(true), request_id });
  assert.equal(h.live.state, "ready");
  assert.equal(h.live.caps.right_arm_initialized, true);
  h.advance(50);
  assert.equal(socket.sent.at(-1).op, "initialize_right_arm");
  h.live.arm("synthetic-test-key-only");
  assert.equal(socket.sent.at(-1).op, "arm");
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "armed",
    request_id: h.live.requestId,
    session_id: "session-00000000001",
    calibration_id: "a".repeat(64),
  });
  h.advance(50);
  assert.equal(socket.sent.at(-1).op, "keepalive");
  assert.equal(socket.sent.filter((message) => message.op === "pose").length, 0);
  for (let i = 0; i < 20; i++) {
    h.ack(socket);
    h.advance(50);
  }
  const log = JSON.stringify(h.live.snapshot().commandLog);
  assert.ok(h.live.snapshot().commandLog.length <= 16);
  assert.doesNotMatch(log, /synthetic-test-key-only/);
  assert.doesNotMatch(log, /session-00000000001/);
});

test("right-arm initialization timeout does not retry and reports unknown hold state", () => {
  const h = harness(() => rightArmCaps(false));
  const socket = h.ready();
  h.live.initializeRightArm("synthetic-test-key-only");
  const sent = socket.sent.length;
  h.advance(1999);
  assert.equal(h.live.state, "initializing_right_arm");
  h.advance(1);
  assert.equal(h.live.state, "fault");
  assert.match(h.live.reason, /состояние неизвестно/);
  assert.equal(socket.closed, true);
  h.advance(5000);
  assert.equal(socket.sent.length, sent);
});

test("right-arm initialization cancel closes as unknown without retrying or posing", () => {
  for (const cancel of [
    (live) => live.stop(),
    (live) => live.requestDisconnect(),
  ]) {
    const h = harness(() => rightArmCaps(false));
    const socket = h.ready();
    h.live.initializeRightArm("synthetic-test-key-only");
    const sent = socket.sent.length;
    cancel(h.live);
    assert.equal(h.live.state, "fault");
    assert.match(h.live.reason, /состояние неизвестно/);
    assert.equal(socket.closed, true);
    h.advance(5000);
    assert.equal(socket.sent.length, sent);
    assert.equal(socket.sent.filter((message) => message.op === "pose").length, 0);
  }
});

test("right-arm commissioning capabilities accept only symmetric five or fifteen degree arm envelopes", () => {
  for (const extent of [5, 15]) {
    const caps = validateCapabilities(rightArmCaps(true, extent));
    assert.equal(caps.mode, RIGHT_ARM_COMMISSIONING_MODE);
    assert.equal(caps.commissioning, true);
    assert.equal(caps.calibrated, false);
    assert.equal(caps.right_arm_initialized, true);
    assert.equal(liveStepSizeForJoint(caps, "arm_positive_x"), extent);
    assert.deepEqual(
      caps.joint_limits.map((limit) => limit.id),
      [
        "arm_positive_x",
        "leg_negative_x",
        "leg_positive_x",
        "foot_negative_x",
        "foot_positive_x",
      ],
    );
    const waiting = validateCapabilities(rightArmCaps(false, extent));
    assert.equal(waiting.initialization_required, true);
    assert.equal(waiting.right_arm_initialized, false);
  }
  for (const mutate of [
    (c) => { c.right_arm_initialized = false; },
    (c) => { c.motion_allowed = false; c.reason = "no_motion_profile"; },
    (c) => { c.joint_limits[0].id = "arm_negative_x"; },
    (c) => { c.joint_limits[0].min = -15; c.joint_limits[0].max = 5; },
    (c) => { c.joint_limits[0].min = -10; c.joint_limits[0].max = 10; },
    (c) => { c.joint_limits[0].min = -16; c.joint_limits[0].max = 16; },
    (c) => { c.joint_limits[0].max_speed_dps = 2; },
    (c) => { c.joint_limits.pop(); },
    (c) => { c.watchdog_ms = 500; },
  ]) {
    const data = rightArmCaps(true);
    mutate(data);
    assert.throws(() => validateCapabilities(data));
  }
  const caps = validateCapabilities(rightArmCaps(true));
  assert.throws(() => livePlaybackScale(createMotion(), caps, 1), /по одному суставу/);
});

test("right-arm commissioning sends one five-degree arm step and blocks a second joint", () => {
  const h = harness(() => rightArmCaps(true));
  const socket = h.arm();
  assert.equal(h.live.speed, 1);
  h.live.hold({ ...zeroPose(), arm_positive_x: -5 });
  h.advance(50);
  assert.equal(socket.sent.at(-1).op, "pose");
  assert.equal(socket.sent.at(-1).speed_dps, 1);
  assert.deepEqual(socket.sent.at(-1).target, {
    arm_positive_x: -5,
    leg_negative_x: 0,
    leg_positive_x: 0,
    foot_negative_x: 0,
    foot_positive_x: 0,
  });
  assert.equal(Object.hasOwn(socket.sent.at(-1).target, "arm_negative_x"), false);
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "ack",
    session_id: h.live.sessionId,
    seq: h.live.pending.seq,
    commanded_pose: { ...zeroPose(), arm_positive_x: -5 },
    measured_pose: null,
    tilt: null,
  });
  h.live.setTarget(zeroPose());
  assert.throws(() => h.live.setTarget({ ...zeroPose(), leg_negative_x: 1 }));
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.equal(socket.sent.filter((message) => message.op === "pose").length, 1);
});

test("right-arm commissioning uses the negotiated fifteen-degree extent without widening old caps", () => {
  const caps = validateCapabilities(rightArmCaps(true, 15));
  const plan = createLiveStepPlan(zeroPose(), caps, "arm_positive_x", -1);
  assert.equal(plan.stepSize, 15);
  assert.equal(plan.target.arm_positive_x, -15);
  assert.equal(prepareLiveStepTarget(zeroPose(), caps, "arm_positive_x", 1).arm_positive_x, 15);

  const wide = harness(() => rightArmCaps(true, 15));
  const wideSocket = wide.arm();
  wide.live.hold({ ...zeroPose(), arm_positive_x: -15 });
  wide.advance(50);
  assert.equal(wideSocket.sent.at(-1).op, "pose");
  assert.equal(wideSocket.sent.at(-1).target.arm_positive_x, -15);

  const old = harness(() => rightArmCaps(true, 5));
  const oldSocket = old.arm();
  assert.throws(() => old.live.hold({ ...zeroPose(), arm_positive_x: -15 }));
  assert.equal(oldSocket.sent.at(-1).op, "stop");
  assert.ok(oldSocket.sent.every((message) => message.op !== "pose"));
});

test("right-arm commissioning session delta follows the negotiated extent from the ARM baseline", () => {
  const h = harness(() => ({
    ...rightArmCaps(true, 15),
    commanded_pose: { ...zeroPose(), arm_positive_x: 5 },
  }));
  const socket = h.arm();
  h.live.hold({ ...zeroPose(), arm_positive_x: -10 });
  h.advance(50);
  assert.equal(socket.sent.at(-1).op, "pose");
  assert.equal(socket.sent.at(-1).target.arm_positive_x, -10);
  assert.throws(() => h.live.setTarget({ ...zeroPose(), arm_positive_x: -11 }));
  assert.equal(socket.sent.at(-1).op, "stop");
});

test("right-arm slider bounds come from the current negotiated extent and ARM baseline", () => {
  const oldCaps = validateCapabilities(rightArmCaps(true, 5));
  assert.deepEqual(
    liveSafeIntervalForJoint(oldCaps, "arm_positive_x", zeroPose()),
    { min: -5, max: 5 },
  );
  const oldView = liveSliderViewModel(
    {
      state: "armed",
      caps: oldCaps,
      holding: false,
      telemetry: { commanded_pose: zeroPose() },
      commissioning_start_pose: zeroPose(),
    },
    "arm_positive_x",
  );
  assert.equal(oldView.disabled, false);
  assert.equal(oldView.min, -5);
  assert.equal(oldView.max, 5);

  const baseline = { ...zeroPose(), arm_positive_x: 5 };
  const newCaps = validateCapabilities({
    ...rightArmCaps(true, 15),
    commanded_pose: baseline,
  });
  assert.deepEqual(
    liveSafeIntervalForJoint(newCaps, "arm_positive_x", baseline),
    { min: -10, max: 15 },
  );
  const newView = liveSliderViewModel(
    {
      state: "armed",
      caps: newCaps,
      holding: false,
      telemetry: { commanded_pose: baseline },
      commissioning_start_pose: baseline,
    },
    "arm_positive_x",
  );
  assert.equal(newView.min, -10);
  assert.equal(newView.max, 15);

  const waiting = liveSliderViewModel(
    {
      state: "init_required",
      caps: validateCapabilities(rightArmCaps(false, 15)),
      holding: false,
      telemetry: { commanded_pose: zeroPose() },
    },
    "arm_positive_x",
  );
  assert.equal(waiting.visible, false);
  assert.equal(waiting.disabled, true);
});

test("right-arm slider separates target from commanded pose and snaps back after release", () => {
  const caps = validateCapabilities(rightArmCaps(true, 15));
  const moving = liveSliderViewModel(
    {
      state: "armed",
      caps,
      holding: true,
      telemetry: {
        commanded_pose: { ...zeroPose(), arm_positive_x: -3 },
      },
      target: { ...zeroPose(), arm_positive_x: -15 },
      commissioning_start_pose: zeroPose(),
      commissioning_joint: "arm_positive_x",
    },
    "leg_negative_x",
    null,
    "arm_positive_x",
  );
  assert.equal(moving.jointId, "arm_positive_x");
  assert.equal(moving.command, -3);
  assert.equal(moving.target, -15);
  assert.equal(moving.value, -15);
  assert.equal(moving.locked, true);
  assert.equal(moving.disabled, false);

  const stopped = liveSliderViewModel(
    {
      state: "stopping",
      caps,
      holding: false,
      telemetry: {
        commanded_pose: { ...zeroPose(), arm_positive_x: -3 },
      },
      target: null,
      commissioning_start_pose: zeroPose(),
      commissioning_joint: "arm_positive_x",
    },
    "foot_positive_x",
    null,
    "arm_positive_x",
  );
  assert.equal(stopped.jointId, "arm_positive_x");
  assert.equal(stopped.command, -3);
  assert.equal(stopped.target, -3);
  assert.equal(stopped.value, -3);
  assert.equal(stopped.disabled, true);
});

test("right-arm slider hold updates the held target and releases through STOP", () => {
  const h = harness(() => rightArmCaps(true, 15));
  const socket = h.arm();
  h.advance(50);
  assert.equal(socket.sent.at(-1).op, "keepalive");
  assert.equal(socket.sent.filter((message) => message.op === "pose").length, 0);
  h.ack(socket);

  h.live.beginHold(zeroPose());
  h.live.updatePose({ ...zeroPose(), arm_positive_x: -15 });
  assert.equal(h.live.snapshot().target.arm_positive_x, -15);
  h.advance(50);
  const pose = socket.sent.at(-1);
  assert.equal(pose.op, "pose");
  assert.equal(pose.speed_dps, 1);
  assert.equal(pose.target.arm_positive_x, -15);
  h.ack(socket, { ...zeroPose(), arm_positive_x: -3 });
  assert.equal(h.live.lastTelemetry.commanded_pose.arm_positive_x, -3);

  const session_id = h.live.sessionId;
  h.live.release("Ползунок отпущен.");
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.equal(h.live.state, "stopping");
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "stopped",
    session_id,
    commanded_pose: { ...zeroPose(), arm_positive_x: -3 },
    measured_pose: null,
    tilt: null,
  });
  assert.equal(h.live.state, "ready");
  assert.equal(h.live.snapshot().target, null);
  assert.equal(h.live.lastTelemetry.commanded_pose.arm_positive_x, -3);
});

test("old right-arm caps cannot be widened to a fifteen-degree slider target", () => {
  const h = harness(() => rightArmCaps(true, 5));
  const socket = h.arm();
  h.live.beginHold(zeroPose());
  assert.throws(() =>
    h.live.updatePose({ ...zeroPose(), arm_positive_x: -15 }),
  );
  assert.equal(h.live.state, "stopping");
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.equal(
    socket.sent.some(
      (message) =>
        message.op === "pose" && message.target?.arm_positive_x === -15,
    ),
    false,
  );
});

test("right-arm commissioning release sends STOP and keeps the final command explicit", () => {
  const h = harness(() => rightArmCaps(true));
  const socket = h.arm();
  h.live.hold({ ...zeroPose(), arm_positive_x: -5 });
  h.advance(50);
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "ack",
    session_id: h.live.sessionId,
    seq: h.live.pending.seq,
    commanded_pose: { ...zeroPose(), arm_positive_x: -5 },
    measured_pose: null,
    tilt: null,
  });
  const session_id = h.live.sessionId;
  h.live.release();
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.equal(h.live.state, "stopping");
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "stopped",
    session_id,
    commanded_pose: { ...zeroPose(), arm_positive_x: -5 },
    measured_pose: null,
    tilt: null,
  });
  assert.equal(h.live.state, "ready");
  assert.equal(h.live.lastTelemetry.commanded_pose.arm_positive_x, -5);
  const sent = socket.sent.length;
  h.advance(500);
  assert.equal(socket.sent.length, sent);
});

test("right-arm step display keeps the armed joint and absolute target through partial ACKs", () => {
  const caps = validateCapabilities(rightArmCaps(true));
  const activeStep = createLiveStepPlan(
    zeroPose(),
    caps,
    "arm_positive_x",
    -1,
  );
  for (const value of [0, -1, -2, -3, -4, -5]) {
    const view = liveStepViewModel(
      {
        state: "armed",
        caps,
        holding: true,
        telemetry: {
          commanded_pose: { ...zeroPose(), arm_positive_x: value },
        },
      },
      "leg_negative_x",
      1,
      activeStep,
    );
    assert.equal(view.jointId, "arm_positive_x");
    assert.equal(view.direction, -1);
    assert.equal(view.stepSize, 5);
    assert.equal(view.current, value);
    assert.equal(view.target.arm_positive_x, -5);
    assert.equal(view.holdDisabled, false);
    assert.equal(view.locked, true);
  }
  const stopping = liveStepViewModel(
    {
      state: "stopping",
      caps,
      holding: false,
      telemetry: {
        commanded_pose: { ...zeroPose(), arm_positive_x: -3 },
      },
    },
    "foot_positive_x",
    1,
    activeStep,
  );
  assert.equal(stopping.jointId, "arm_positive_x");
  assert.equal(stopping.target.arm_positive_x, -5);
  assert.equal(stopping.locked, true);
  assert.equal(stopping.holdDisabled, true);
});

test("right-arm step display shows the negotiated fifteen-degree target", () => {
  const caps = validateCapabilities(rightArmCaps(true, 15));
  const activeStep = createLiveStepPlan(
    zeroPose(),
    caps,
    "arm_positive_x",
    -1,
  );
  for (const value of [0, -5, -10, -15]) {
    const view = liveStepViewModel(
      {
        state: "armed",
        caps,
        holding: true,
        telemetry: {
          commanded_pose: { ...zeroPose(), arm_positive_x: value },
        },
      },
      "leg_negative_x",
      1,
      activeStep,
    );
    assert.equal(view.jointId, "arm_positive_x");
    assert.equal(view.direction, -1);
    assert.equal(view.stepSize, 15);
    assert.equal(view.current, value);
    assert.equal(view.target.arm_positive_x, -15);
    assert.equal(view.locked, true);
  }
});

test("right-arm held step keeps sending the fixed target until release follows STOP", () => {
  const h = harness(() => rightArmCaps(true));
  const socket = h.arm();
  h.live.hold({ ...zeroPose(), arm_positive_x: -5 });
  for (const value of [-1, -2, -3, -4, -5]) {
    h.advance(50);
    const poseMessage = socket.sent.at(-1);
    assert.equal(poseMessage.op, "pose");
    assert.equal(poseMessage.target.arm_positive_x, -5);
    socket.receive({
      protocol: LIVE_PROTOCOL,
      op: "ack",
      session_id: h.live.sessionId,
      seq: h.live.pending.seq,
      commanded_pose: { ...zeroPose(), arm_positive_x: value },
      measured_pose: null,
      tilt: null,
    });
  }
  const session_id = h.live.sessionId;
  h.live.release();
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.equal(h.live.state, "stopping");
  socket.receive({
    protocol: LIVE_PROTOCOL,
    op: "stopped",
    session_id,
    commanded_pose: { ...zeroPose(), arm_positive_x: -5 },
    measured_pose: null,
    tilt: null,
  });
  assert.equal(h.live.state, "ready");
  assert.equal(h.live.lastTelemetry.commanded_pose.arm_positive_x, -5);
});

test("right-arm commissioning keeps leg steps to one degree from the ARM baseline", () => {
  const h = harness(() => ({
    ...rightArmCaps(true),
    commanded_pose: { ...zeroPose(), leg_negative_x: -1 },
  }));
  const socket = h.arm();
  assert.throws(() => h.live.hold({ ...zeroPose(), leg_negative_x: 1 }));
  assert.equal(socket.sent.at(-1).op, "stop");
  assert.ok(socket.sent.every((message) => message.op !== "pose"));
});

test("right-arm commissioning rejects ACK and STOP outside the selected joint envelope", () => {
  for (const op of ["ack", "stopped"]) {
    for (const reported of [
      { ...zeroPose(), arm_positive_x: -6 },
      { ...zeroPose(), arm_positive_x: -5, leg_negative_x: 1 },
      { ...zeroPose(), arm_positive_x: -5, arm_negative_x: 1 },
    ]) {
      const h = harness(() => rightArmCaps(true));
      const socket = h.arm();
      h.live.hold({ ...zeroPose(), arm_positive_x: -5 });
      h.advance(50);
      const session_id = h.live.sessionId;
      const seq = h.live.pending.seq;
      if (op === "stopped") h.live.release();
      socket.receive({
        protocol: LIVE_PROTOCOL,
        op,
        session_id,
        seq,
        commanded_pose: reported,
        measured_pose: null,
        tilt: null,
      });
      assert.equal(h.live.state, "fault");
      assert.equal(socket.closed, true);
      assert.deepEqual(h.live.lastTelemetry.commanded_pose, zeroPose());
    }
  }
});

test("preparing a step target never changes saved keyframes or the library pose", () => {
  const motion = createMotion("Приветствие");
  motion.keyframes[0].pose.leg_negative_x = 1;
  const before = JSON.stringify(motion);
  const caps = validateCapabilities(rightArmCaps(true));
  const target = prepareLiveStepTarget(
    zeroPose(),
    caps,
    "arm_positive_x",
    -1,
  );
  assert.equal(target.arm_positive_x, -5);
  assert.equal(JSON.stringify(motion), before);
  assert.equal(motion.keyframes[0].pose.leg_negative_x, 1);
  assert.throws(() =>
    prepareLiveStepTarget(zeroPose(), caps, "arm_negative_x", -1),
  );
  assert.throws(() =>
    prepareLiveStepTarget(
      { ...zeroPose(), arm_positive_x: 5 },
      caps,
      "arm_positive_x",
      1,
    ),
  );
  assert.equal(
    prepareLiveStepTarget(zeroPose(), caps, "leg_positive_x", 1).leg_positive_x,
    1,
  );
});


test("inspector rows are unavailable before connection and preserve actual caps", () => {
  assert.equal(liveInspectorJointViewModel({state:"disconnected"}, "arm_positive_x").disabled, true);
  const caps = validateCapabilities(rightArmCaps(true, 15));
  const ready = {state:"ready", caps};
  const right = liveInspectorJointViewModel(ready,"arm_positive_x");
  assert.deepEqual([right.min,right.max,right.disabled],[-15,15,true]);
  assert.equal(liveInspectorJointViewModel(ready,"arm_negative_x").available,false);
  const old = liveInspectorJointViewModel({state:"armed",caps:validateCapabilities(rightArmCaps(true,5))},"arm_positive_x");
  assert.deepEqual([old.min,old.max],[-5,5]);
});

test("inspector only enables the held joint and shows target separately from command", () => {
  const caps = validateCapabilities(rightArmCaps(true,15));
  const state = {state:"armed",caps,holding:true,commissioning_start_pose:{...zeroPose(),arm_positive_x:-10},commissioning_joint:"arm_positive_x",telemetry:{commanded_pose:{...zeroPose(),arm_positive_x:-12}},target:{...zeroPose(),arm_positive_x:-15}};
  const arm = liveInspectorJointViewModel(state,"arm_positive_x",null,"arm_positive_x");
  assert.deepEqual([arm.min,arm.max,arm.command,arm.value,arm.disabled],[-15,5,-12,-15,false]);
  assert.equal(liveInspectorJointViewModel(state,"leg_negative_x",null,"arm_positive_x").disabled,true);
  const stopped = liveInspectorJointViewModel({...state,state:"ready",target:null,commissioning_joint:null},"arm_positive_x");
  assert.equal(stopped.value,-12);
  assert.equal(stopped.disabled,true);
});
