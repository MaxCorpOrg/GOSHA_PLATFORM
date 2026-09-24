import test from "node:test";
import assert from "node:assert/strict";
import {
  PROFILE,
  zeroPose,
  createMotion,
  examples,
  poseAt,
  putPose,
  mirrorPose,
  validateMotion,
  parseMotion,
  prepareRobotMotionPackage,
  prepareRobotPackageUpload,
  robotPackageCrc32,
  ROBOT_PACKAGE_UPLOAD_CHUNK_BYTES,
  MAX_FILE_BYTES,
} from "../src/motion.js";

const liveEditorCaps = () => ({
  profile_id: PROFILE.id,
  mode: "motion_editor",
  calibration_id: "a".repeat(64),
  calibrated: false,
  commissioning: false,
  initialization_required: false,
  right_arm_initialized: true,
  watchdog_ms: 300,
  max_rate_hz: 10,
  commanded_pose: zeroPose(),
  joint_limits: [
    { id: "arm_positive_x", min: -70, max: 45, max_speed_dps: 10 },
    { id: "leg_negative_x", min: -35, max: 35, max_speed_dps: 10 },
    { id: "leg_positive_x", min: -35, max: 35, max_speed_dps: 10 },
    { id: "foot_negative_x", min: -30, max: 30, max_speed_dps: 10 },
    { id: "foot_positive_x", min: -30, max: 30, max_speed_dps: 10 },
  ],
});

function motionFrom(frames, options = {}) {
  return validateMotion({
    ...createMotion("Проверка робота"),
    ...options,
    duration_ms: options.duration_ms ?? frames.at(-1)[0],
    keyframes: frames.map(([time_ms, values]) => ({
      time_ms,
      pose: { ...zeroPose(), ...values },
    })),
  });
}

test("JSON round trip preserves every frame, duration, name and profile", () => {
  for (const motion of examples())
    assert.deepEqual(parseMotion(JSON.stringify(motion)), motion);
  const motion = putPose(createMotion("Мой поворот"), 333, {
    ...zeroPose(),
    arm_positive_x: -37.25,
  });
  assert.deepEqual(parseMotion(JSON.stringify(motion)), motion);
});
test("small greeting example fits the current robot package limits", () => {
  const greeting = examples().find((motion) => motion.id === "example-small-greeting");
  const result = prepareRobotMotionPackage(greeting, liveEditorCaps());
  assert.equal(result.ok, true);
  assert.equal(result.package.duration_ms, 4800);
  assert.equal(result.package.safety.max_required_speed_dps, 7.5);
  assert.equal(prepareRobotPackageUpload(result.package).package_id,
    "motion-example-small-greeting");
});
test("pose edits retain surrounding frames and never mutate the original motion", () => {
  const original = examples()[0];
  const baseline = structuredClone(original);
  const pose = { ...zeroPose(), arm_negative_x: 52 };
  const updated = putPose(original, 900, pose);
  assert.deepEqual(original, baseline);
  assert.equal(updated.keyframes.length, original.keyframes.length + 1);
  assert.deepEqual(poseAt(updated, 900), pose);
  const replaced = putPose(updated, 900, zeroPose());
  assert.equal(replaced.keyframes.length, updated.keyframes.length);
  assert.deepEqual(poseAt(replaced, 900), zeroPose());
});
test("linear, smooth and hold interpolation honor exact keyframes and do not overshoot", () => {
  let motion = putPose(createMotion(), 1000, {
    ...zeroPose(),
    arm_positive_x: 40,
  });
  for (const interpolation of ["linear", "smooth", "hold"]) {
    motion = { ...motion, interpolation };
    assert.equal(poseAt(motion, -100).arm_positive_x, 0);
    assert.equal(poseAt(motion, 1000).arm_positive_x, 40);
    assert.equal(poseAt(motion, 9000).arm_positive_x, 40);
    let previous = 0;
    for (let t = 0; t <= 1000; t++) {
      const angle = poseAt(motion, t).arm_positive_x;
      assert.ok(angle >= previous && angle <= 40);
      previous = angle;
    }
  }
  assert.equal(
    poseAt({ ...motion, interpolation: "linear" }, 250).arm_positive_x,
    10,
  );
  assert.equal(
    poseAt({ ...motion, interpolation: "smooth" }, 250).arm_positive_x,
    6.25,
  );
  assert.equal(
    poseAt({ ...motion, interpolation: "hold" }, 999).arm_positive_x,
    0,
  );
});
test("mirror swaps corresponding joints with correct signs and is reversible", () => {
  const pose = Object.fromEntries(
    PROFILE.joints.map((j, i) => [j.id, (i + 1) * 3]),
  );
  const mirrored = mirrorPose(pose);
  assert.equal(mirrored.arm_negative_x, -pose.arm_positive_x);
  assert.equal(mirrored.foot_positive_x, -pose.foot_negative_x);
  assert.deepEqual(mirrorPose(mirrored), pose);
});
test("rejects invalid imports instead of silently changing the motion", () => {
  const cases = [
    (m) => {
      m.schema_version = 2;
    },
    (m) => {
      m.profile_id = "another-robot";
    },
    (m) => {
      m.profile_version = 2;
    },
    (m) => {
      m.units = "servo_degrees";
    },
    (m) => {
      m.preview_only = false;
    },
    (m) => {
      m.hardware_validated = true;
    },
    (m) => {
      m.name = "";
    },
    (m) => {
      m.name = "a".repeat(81);
    },
    (m) => {
      m.id = "__proto__";
    },
    (m) => {
      m.id = "constructor";
    },
    (m) => {
      m.duration_ms = 0;
    },
    (m) => {
      m.duration_ms = 121000;
    },
    (m) => {
      m.duration_ms = 501.1;
    },
    (m) => {
      m.interpolation = "cubic-unbounded";
    },
    (m) => {
      m.keyframes = [];
    },
    (m) => {
      m.keyframes[0].time_ms = 1;
    },
    (m) => {
      m.keyframes.push({ time_ms: 0, pose: zeroPose() });
    },
    (m) => {
      m.keyframes.push({ time_ms: 9000, pose: zeroPose() });
    },
    (m) => {
      delete m.keyframes[0].pose.foot_positive_x;
    },
    (m) => {
      m.keyframes[0].pose.foot_positive_x = Infinity;
    },
    (m) => {
      m.keyframes[0].pose.foot_positive_x = 31;
    },
    (m) => {
      m.keyframes[0].pose.extra = 0;
    },
    (m) => {
      m.keyframes[0].pose.foot_positive_x = "20";
    },
    (m) => {
      m.keyframes = Array.from({ length: 1001 }, (_, time_ms) => ({
        time_ms,
        pose: zeroPose(),
      }));
    },
  ];
  for (const change of cases) {
    const m = createMotion();
    change(m);
    assert.throws(() => validateMotion(m));
  }
  assert.throws(() => parseMotion("{broken"));
  assert.throws(() => parseMotion("x".repeat(MAX_FILE_BYTES + 1)));
});

test("both arms mirror the complete 70-up and 55-down endpoints", () => {
  for (const pose of [{...zeroPose(),arm_negative_x:70,arm_positive_x:-70},{...zeroPose(),arm_negative_x:-55,arm_positive_x:55}]) {
    assert.deepEqual(mirrorPose(pose),pose);
    assert.deepEqual(mirrorPose(mirrorPose(pose)),pose);
  }
  assert.throws(()=>putPose(createMotion(),100,{...zeroPose(),arm_positive_x:56}));
  assert.throws(()=>putPose(createMotion(),100,{...zeroPose(),arm_negative_x:-56}));
});

test("robot package draft rejects right arm frames beyond current Live limits without clamping", () => {
  const motion = motionFrom([
    [0, {}],
    [8000, { arm_positive_x: 55 }],
  ]);
  const result = prepareRobotMotionPackage(motion, liveEditorCaps());
  assert.equal(result.ok, false);
  assert.equal(result.package, null);
  assert.ok(
    result.issues.some(
      (item) =>
        item.code === "joint_out_of_range" &&
        item.joint_id === "arm_positive_x" &&
        item.value === 55 &&
        item.max === 45,
    ),
  );
  assert.equal(motion.keyframes[1].pose.arm_positive_x, 55);
});

test("robot package draft requires current motion_editor capabilities", () => {
  const motion = motionFrom([
    [0, {}],
    [8000, { leg_negative_x: 10 }],
  ]);
  assert.throws(
    () =>
      prepareRobotMotionPackage(motion, {
        ...liveEditorCaps(),
        mode: "verified",
      }),
    /motion_editor/,
  );
  assert.throws(
    () =>
      prepareRobotMotionPackage(motion, {
        ...liveEditorCaps(),
        joint_limits: [
          { id: "leg_negative_x", min: -35, max: 35, max_speed_dps: 10 },
        ],
      }),
    /набором подключённых приводов/,
  );
  assert.throws(
    () =>
      prepareRobotMotionPackage(motion, {
        ...liveEditorCaps(),
        joint_limits: [
          ...liveEditorCaps().joint_limits,
          { id: "arm_negative_x", min: -55, max: 70, max_speed_dps: 10 },
        ],
      }),
    /набором подключённых приводов/,
  );
});

test("robot package draft rejects movement on unavailable left arm", () => {
  const neutralLeft = motionFrom([
    [0, {}],
    [8000, { arm_positive_x: 40 }],
  ]);
  const neutralResult = prepareRobotMotionPackage(neutralLeft, liveEditorCaps());
  assert.equal(neutralResult.ok, true);
  assert.equal(
    neutralResult.package.active_joints.includes("arm_negative_x"),
    false,
  );
  assert.equal(
    Object.hasOwn(neutralResult.package.keyframes[1].target, "arm_negative_x"),
    false,
  );

  const movingLeft = motionFrom([
    [0, {}],
    [5000, { arm_negative_x: 10 }],
  ]);
  const blocked = prepareRobotMotionPackage(movingLeft, liveEditorCaps());
  assert.equal(blocked.ok, false);
  assert.ok(
    blocked.issues.some(
      (item) =>
        item.code === "joint_unavailable" &&
        item.joint_id === "arm_negative_x" &&
        item.value === 10,
    ),
  );
});

test("robot package draft reports speed and interpolation blockers explicitly", () => {
  const tooFast = motionFrom([
    [0, {}],
    [1000, { arm_positive_x: 20 }],
  ]);
  const fastResult = prepareRobotMotionPackage(tooFast, liveEditorCaps());
  assert.equal(fastResult.ok, false);
  assert.ok(
    fastResult.issues.some(
      (item) =>
        item.code === "joint_speed_exceeded" &&
        item.joint_id === "arm_positive_x" &&
        item.required_speed_dps === 30,
    ),
  );

  const hold = motionFrom(
    [
      [0, {}],
      [5000, { foot_positive_x: 5 }],
    ],
    { interpolation: "hold" },
  );
  const holdResult = prepareRobotMotionPackage(hold, liveEditorCaps());
  assert.equal(holdResult.ok, false);
  assert.ok(
    holdResult.issues.some(
      (item) => item.code === "unsupported_interpolation",
    ),
  );
});

test("robot package draft preserves preview flags and active joint constraints", () => {
  const motion = motionFrom([
    [0, {}],
    [6000, { arm_positive_x: 40, leg_negative_x: -12, foot_positive_x: 8 }],
  ]);
  const result = prepareRobotMotionPackage(motion, liveEditorCaps());
  assert.equal(result.ok, true);
  assert.deepEqual(result.issues, []);
  assert.equal(result.package.package_type, "gosha.motion.robot-package-draft.v1");
  assert.equal(result.package.source_preview_only, true);
  assert.equal(result.package.hardware_validated, false);
  assert.equal(result.package.robot_storage_implemented, false);
  assert.deepEqual(result.package.active_joints, [
    "arm_positive_x",
    "leg_negative_x",
    "leg_positive_x",
    "foot_negative_x",
    "foot_positive_x",
  ]);
  assert.deepEqual(result.package.keyframes[1].target, {
    arm_positive_x: 40,
    leg_negative_x: -12,
    leg_positive_x: 0,
    foot_negative_x: 0,
    foot_positive_x: 8,
  });
});

test("robot package upload plan is bounded, deterministic and CRC checked", () => {
  const motion = motionFrom([
    [0, {}],
    [6000, { arm_positive_x: 40, leg_negative_x: -12, foot_positive_x: 8 }],
  ]);
  const result = prepareRobotMotionPackage(motion, liveEditorCaps());
  const upload = prepareRobotPackageUpload(result.package);
  const payload = Buffer.from(JSON.stringify(result.package), "utf8");
  const decoded = Buffer.concat(
    upload.chunks.map((chunk) => Buffer.from(chunk.data_b64, "base64")),
  );
  assert.match(upload.package_id, /^[a-zA-Z0-9_.-]{1,63}$/);
  assert.equal(upload.profile_id, PROFILE.id);
  assert.equal(upload.calibration_id, "a".repeat(64));
  assert.equal(upload.total_size, payload.length);
  assert.equal(upload.crc32, robotPackageCrc32(payload));
  const changed = prepareRobotPackageUpload({ ...result.package, name: "Новая версия" });
  assert.equal(changed.package_id, upload.package_id);
  assert.notEqual(changed.crc32, upload.crc32);
  assert.deepEqual(decoded, payload);
  assert.ok(
    upload.chunks.every(
      (chunk) => chunk.size > 0 && chunk.size <= ROBOT_PACKAGE_UPLOAD_CHUNK_BYTES,
    ),
  );
});
