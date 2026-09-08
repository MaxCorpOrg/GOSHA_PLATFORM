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
  MAX_FILE_BYTES,
} from "../src/motion.js";

test("JSON round trip preserves every frame, duration, name and profile", () => {
  for (const motion of examples())
    assert.deepEqual(parseMotion(JSON.stringify(motion)), motion);
  const motion = putPose(createMotion("Мой поворот"), 333, {
    ...zeroPose(),
    arm_positive_x: -37.25,
  });
  assert.deepEqual(parseMotion(JSON.stringify(motion)), motion);
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
