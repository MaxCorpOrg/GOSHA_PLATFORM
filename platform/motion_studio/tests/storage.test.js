import test from "node:test";
import assert from "node:assert/strict";
import { createStore, STORAGE_KEY, LEGACY_BACKUP_KEY } from "../src/storage.js";
import { createMotion, examples, putPose, zeroPose } from "../src/motion.js";

const memory = () => {
  const values = new Map();
  return {
    getItem: (k) => values.get(k) ?? null,
    setItem: (k, v) => values.set(k, v),
  };
};
test("only the untouched 6-degree greeting upgrades to 15 degrees with a recoverable revision", () => {
  const original = examples().find((motion) => motion.id === "example-small-greeting");
  const legacy = {
    ...original,
    duration_ms: 4800,
    keyframes: [[0, 0], [1200, 6], [3600, -6], [4800, 0]].map(
      ([time_ms, angle]) => ({ time_ms, pose: { ...zeroPose(), arm_positive_x: angle } }),
    ),
  };
  const storage = memory();
  storage.setItem(STORAGE_KEY, JSON.stringify({
    schema_version: 1, motions: [legacy], active_id: legacy.id,
    revisions: {}, installed: {},
  }));
  const store = createStore(storage);
  const state = store.load();
  assert.equal(state.templateUpgraded, true);
  assert.equal(state.motions[0].duration_ms, 10000);
  assert.deepEqual(state.motions[0].keyframes.map((frame) => frame.pose.arm_positive_x),
    [0, 15, -15, 0]);
  assert.deepEqual(state.revisions[legacy.id][0].motion, legacy);
  assert.equal(store.save(state).ok, true);
  assert.equal(createStore(storage).load().revisions[legacy.id].length, 1);

  const copiedStorage = memory();
  const copied = { ...legacy, id: "local-copy-of-greeting" };
  copiedStorage.setItem(STORAGE_KEY, JSON.stringify({
    schema_version: 1, motions: [copied], active_id: copied.id,
  }));
  const upgradedCopy = createStore(copiedStorage).load();
  assert.equal(upgradedCopy.templateUpgraded, true);
  assert.equal(upgradedCopy.motions[0].id, copied.id);
  assert.equal(upgradedCopy.motions[0].keyframes[1].pose.arm_positive_x, 15);
  assert.deepEqual(upgradedCopy.revisions[copied.id][0].motion, copied);

  const editedStorage = memory();
  editedStorage.setItem(STORAGE_KEY, JSON.stringify({
    schema_version: 1, motions: [{ ...legacy, name: "Мой жест" }],
    active_id: legacy.id,
  }));
  const edited = createStore(editedStorage).load();
  assert.equal(edited.templateUpgraded, false);
  assert.equal(edited.motions[0].duration_ms, 4800);
});
test("new motion and snapshots survive reopening with a fresh store instance", () => {
  const storage = memory();
  const store = createStore(storage);
  const state = store.load();
  const custom = createMotion("Новый жест");
  const edited = putPose(custom, 1230, { ...zeroPose(), arm_positive_x: -51 });
  state.motions.push(edited);
  state.active_id = edited.id;
  state.revisions[edited.id] = [
    { saved_at: "2026-09-06T09:00:00Z", motion: custom },
  ];
  state.installed[edited.id] = {
    package_id: `motion-${edited.id}`, crc32: 1234, fingerprint: 5678,
  };
  assert.equal(store.save(state).ok, true);
  const restored = createStore(storage).load();
  assert.deepEqual(restored.motions.at(-1), edited);
  assert.equal(restored.active_id, edited.id);
  assert.deepEqual(restored.revisions[edited.id][0].motion, custom);
  assert.deepEqual(restored.installed[edited.id], state.installed[edited.id]);
});
test("corrupt library is preserved verbatim and subsequent saves cannot overwrite it", () => {
  for (const raw of [
    "{broken",
    JSON.stringify({ schema_version: 1, motions: [] }),
    JSON.stringify({ schema_version: 999 }),
  ]) {
    const storage = memory();
    storage.setItem(STORAGE_KEY, raw);
    const store = createStore(storage);
    const state = store.load();
    assert.ok(state.error);
    assert.equal(store.save(state).ok, false);
    assert.equal(storage.getItem(STORAGE_KEY), raw);
  }
});
test("duplicate ids or mismatched snapshots fail closed, preserving original data", () => {
  const m = createMotion();
  const cases = [
    { schema_version: 1, motions: [m, m] },
    {
      schema_version: 1,
      motions: [m],
      revisions: {
        [m.id]: [{ saved_at: "2026-09-06T09:00:00Z", motion: createMotion() }],
      },
    },
  ];
  for (const data of cases) {
    const storage = memory();
    const raw = JSON.stringify(data);
    storage.setItem(STORAGE_KEY, raw);
    const store = createStore(storage);
    const loaded = store.load();
    assert.ok(loaded.error);
    assert.equal(store.save(loaded).ok, false);
    assert.equal(storage.getItem(STORAGE_KEY), raw);
  }
});
test("quota and storage access failures produce explicit unsaved status", () => {
  const store = createStore({
    getItem: () => null,
    setItem() {
      throw new Error("QuotaExceededError");
    },
  });
  assert.equal(store.save(store.load()).ok, false);
  const blocked = createStore({
    getItem() {
      throw new Error("SecurityError");
    },
  });
  const state = blocked.load();
  assert.ok(state.error);
  assert.equal(blocked.save(state).ok, false);
});

test("schema-valid ids matching Object.prototype retain their library and revisions", () => {
  for (const id of ["toString", "valueOf", "hasOwnProperty"]) {
    const storage = memory();
    const motion = { ...createMotion(), id };
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        schema_version: 1,
        active_id: id,
        motions: [motion],
        revisions: {},
      }),
    );
    const store = createStore(storage);
    const state = store.load();
    assert.equal(state.error, null);
    assert.deepEqual(state.motions[0], motion);
    assert.deepEqual(state.revisions[id], []);
    state.revisions[id].push({ saved_at: "2026-09-06T09:00:00Z", motion });
    assert.equal(store.save(state).ok, true);
    const reopened = createStore(storage).load();
    assert.equal(reopened.error, null);
    assert.deepEqual(reopened.revisions[id][0].motion, motion);
  }
});

test("narrowed arm limits migrate motions and revisions while preserving exact original library", () => {
  const storage = memory();
  const original = createMotion(); original.keyframes[0].pose.arm_positive_x=70;
  original.keyframes[0].pose.arm_negative_x=-70;
  const raw=JSON.stringify({schema_version:1,motions:[original],active_id:original.id,revisions:{[original.id]:[{saved_at:"2026-09-08T00:00:00Z",motion:original}]}});
  storage.setItem(STORAGE_KEY,raw);
  const store=createStore(storage), data=store.load();
  assert.equal(data.error,null); assert.equal(data.adjusted,true);
  assert.equal(data.motions[0].keyframes[0].pose.arm_positive_x,55);
  assert.equal(data.revisions[original.id][0].motion.keyframes[0].pose.arm_negative_x,-55);
  assert.equal(storage.getItem(LEGACY_BACKUP_KEY),raw);
  assert.equal(store.save(data).ok,true);
  assert.equal(createStore(storage).load().originalLibrary,raw);
});
