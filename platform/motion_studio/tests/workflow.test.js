import test from "node:test";
import assert from "node:assert/strict";
import { createStore } from "../src/storage.js";
import { createMotion, putPose, zeroPose, PROFILE, prepareRobotPackageUpload } from "../src/motion.js";
import { prepareInstallation, rememberInstallation, importProject, motionFingerprint } from "../src/workflow.js";

const caps = {
  profile_id: PROFILE.id, calibration_id: "a".repeat(64), mode: "motion_editor",
  watchdog_ms: 300, max_rate_hz: 20,
  commanded_pose: zeroPose(),
  joint_limits: PROFILE.joints.slice(1).map((j) => ({ id: j.id, min: j.min, max: j.max, max_speed_dps: 10 })),
};
const empty = () => ({ motions: [], installed: Object.create(null), revisions: Object.create(null) });

test("editing or selecting another project during upload cannot change its recorded identity", () => {
  const library = empty();
  const first = createMotion("Первое");
  const original = structuredClone(first);
  const { packageDraft, record } = prepareInstallation(first, caps);
  // A delayed device response arrives after the editor switched and mutated its draft.
  first.name = "Изменённый черновик";
  first.keyframes[0].pose.arm_positive_x = 25;
  library.motions.push(first, createMotion("Второе"));
  library.active_id = library.motions[1].id;
  rememberInstallation(library, record);
  assert.equal(library.installed[library.active_id], undefined);
  assert.deepEqual(library.installed[first.id].motion_snapshot, original);
  assert.equal(record.fingerprint, motionFingerprint(original));
  assert.equal(record.crc32, prepareRobotPackageUpload(packageDraft).crc32);
});

test("export/import on a fresh browser updates the same robot package and preserves other projects", () => {
  const first = createMotion("Жест");
  const original = prepareInstallation(first, caps);
  const file = JSON.stringify(first);
  const library = empty();
  importProject(library, createMotion("Другой жест"));
  const restored = importProject(library, JSON.parse(file));
  const next = prepareInstallation(putPose(restored, 3000, { ...zeroPose(), arm_positive_x: -15 }), caps);
  assert.equal(library.motions.length, 2);
  assert.equal(next.record.package_id, original.record.package_id);
  assert.notEqual(next.record.crc32, original.record.crc32);
});

test("reimporting the same ID keeps installation metadata and a recoverable previous draft", () => {
  const library = empty();
  const first = importProject(library, createMotion("Исходный"));
  const original = structuredClone(first);
  rememberInstallation(library, prepareInstallation(first, caps).record);
  const installed = structuredClone(library.installed[first.id]);
  const changed = { ...first, name: "Из файла" };
  importProject(library, changed, "2026-10-03T00:00:00Z");
  assert.equal(library.motions.length, 1);
  assert.deepEqual(library.revisions[first.id][0].motion, original);
  assert.deepEqual(library.installed[first.id], installed);
  importProject(library, changed);
  assert.equal(library.revisions[first.id].length, 1);
});

test("update and restore retain ID, increment revision, and survive reopening the library", () => {
  let raw;
  const storage = { getItem: () => raw ?? null, setItem: (_, value) => { raw = value; } };
  const library = empty();
  const original = importProject(library, createMotion("Проба"));
  library.active_id = original.id;
  const first = prepareInstallation(original, caps);
  rememberInstallation(library, first.record);
  const changed = putPose(original, 3000, { ...zeroPose(), arm_positive_x: -15 });
  library.motions[0] = changed;
  const second = prepareInstallation(changed, caps, library.installed[original.id]);
  rememberInstallation(library, second.record);
  assert.equal(second.record.package_id, first.record.package_id);
  assert.equal(second.record.revision, 2);
  assert.deepEqual(library.revisions[original.id][0].motion, original);
  assert.equal(createStore(storage).save(library).ok, true);
  const reopened = createStore(storage).load();
  assert.equal(reopened.error, null);
  const restored = prepareInstallation(reopened.revisions[original.id][0].motion, caps, reopened.installed[original.id]);
  assert.equal(restored.record.revision, 3);
  assert.equal(restored.record.package_id, first.record.package_id);
});

test("a full local library still accepts an update by ID but refuses a new project", () => {
  const library = empty();
  library.motions = Array.from({ length: 100 }, () => createMotion());
  assert.throws(() => importProject(library, createMotion()), /100/);
  importProject(library, { ...library.motions[50], name: "Обновлено" });
  assert.equal(library.motions.length, 100);
  assert.equal(library.motions[50].name, "Обновлено");
});
