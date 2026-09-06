import test from "node:test";
import assert from "node:assert/strict";
import { createStore, STORAGE_KEY } from "../src/storage.js";
import { createMotion, putPose, zeroPose } from "../src/motion.js";

const memory = () => {
  const values = new Map();
  return {
    getItem: (k) => values.get(k) ?? null,
    setItem: (k, v) => values.set(k, v),
  };
};
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
  assert.equal(store.save(state).ok, true);
  const restored = createStore(storage).load();
  assert.deepEqual(restored.motions.at(-1), edited);
  assert.equal(restored.active_id, edited.id);
  assert.deepEqual(restored.revisions[edited.id][0].motion, custom);
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
