import test from "node:test";
import assert from "node:assert/strict";
import {
  LIVE_PROTOCOL,
  LiveSession,
  robotSocketUrl,
  validateCapabilities,
} from "../src/live.js";
import { livePlaybackScale } from "../src/live-panel.js";
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
function harness() {
  let now = 1000;
  let id = 0;
  const sockets = [];
  const factory = () => {
    const socket = {
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
    socket.receive({ ...capabilities(), request_id: live.requestId });
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
  const ack = (socket) =>
    socket.receive({
      protocol: LIVE_PROTOCOL,
      op: "ack",
      session_id: live.sessionId,
      seq: live.pending.seq,
      commanded_pose: zeroPose(),
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
