import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PROFILE, createMotion, poseAt, zeroPose } from "../src/motion.js";
import { physicsFrameAt, validatePhysicsResult, physicsFingerprint, DEFAULT_PHYSICS_SETTINGS, mountPhysicsPanel } from "../src/physics.js";

const profile = JSON.parse(readFileSync(new URL("../physics/gosha-preview-v1.json", import.meta.url)));
const pose = value => Object.fromEntries(PROFILE.joints.map(j => [j.id, value]));
function result() {
  return {
    schema_version: 1, preview_only: true, hardware_validated: false, parameter_status: "assumed",
    model: { id: profile.id }, engine: { device: "cpu" }, duration_ms: 1000,
    metrics: { termination: "completed", first_fall_ms: null, distance_mm: 10, max_tilt_deg: 90,
      max_tracking_error_deg: 2, max_effort_nm: .18, compute_seconds: 1 },
    frames: [0, 1000].map((time_ms, i) => ({ time_ms, pose: pose(i*10), tilt_deg: i*90,
      base: { position_m: [i*.01, 0, 0], quaternion_xyzw: i ? [Math.SQRT1_2, 0, 0, Math.SQRT1_2] : [0, 0, 0, 1] },
      contacts: [{ position_m: [0, 0, -.09], normal_force_n: 2 }],
    })),
  };
}

test("physical axes, directions, parents and ranges exactly match the authoring profile", () => {
  assert.equal(profile.profile_id, PROFILE.id);
  assert.equal(profile.geometry_source_sha256, "208bb99daed400921ab14fb5c21c1cbb3f1f798b78f952ca11447c59a709372e");
  assert.deepEqual(profile.joints, PROFILE.joints.map(({ id, label, pivot, axis, modelDirection, parent, min, max }) =>
    ({ id, label, pivot, axis, modelDirection, ...(parent ? { parent } : {}), min, max })));
});

test("seeking physics interpolates actual pose and free base without mutating source movement", () => {
  const data = validatePhysicsResult(result()), before = structuredClone(data);
  const frame = physicsFrameAt(data, 500);
  assert.deepEqual(frame.pose, pose(5));
  assert.equal(frame.base.position_m[0], .005);
  assert.ok(Math.abs(frame.tilt_deg - 45) < 1e-10);
  assert.ok(Math.abs(Math.hypot(...frame.base.quaternion_xyzw) - 1) < 1e-12);
  assert.deepEqual(data, before);
  assert.equal(physicsFrameAt(data, -1), data.frames[0]);
  assert.equal(physicsFrameAt(data, 1100), data.frames[1]);
});

test("equivalent opposite-sign quaternions never produce a zero or spinning interpolation", () => {
  const data = result();
  data.frames[1].base.quaternion_xyzw = [0, 0, 0, -1];
  const frame = physicsFrameAt(data, 500);
  assert.deepEqual(frame.base.quaternion_xyzw, [0, 0, 0, 1]);
  assert.equal(frame.tilt_deg, 0);
});

test("incompatible, nonfinite, incomplete and malformed physical reports are rejected", () => {
  const changes = [d => d.preview_only = false, d => d.hardware_validated = true,
    d => d.parameter_status = "measured", d => d.model.id = "other",
    d => d.frames[1].time_ms = 0, d => d.frames[0].time_ms = 1,
    d => d.frames[1].time_ms = 999, d => delete d.frames[0].pose.arm_negative_x,
    d => d.frames[0].tilt_deg = NaN, d => d.frames[0].base.position_m[0] = Infinity,
    d => d.frames[0].base.quaternion_xyzw = [0, 0, 0, 0],
    d => d.frames[0].contacts[0].normal_force_n = -1,
    d => d.metrics.distance_mm = NaN, d => d.metrics.first_fall_ms = 1001];
  for (const change of changes) {
    const data = result(); change(data);
    assert.throws(() => validatePhysicsResult(data));
  }
});

test("a parameter or authoring change invalidates the cached physical result", () => {
  const motion = createMotion(), original = structuredClone(motion), settings = { ...DEFAULT_PHYSICS_SETTINGS };
  const key = physicsFingerprint(motion, settings);
  assert.notEqual(key, physicsFingerprint(motion, { ...settings, total_mass_g: 800 }));
  assert.notEqual(key, physicsFingerprint({ ...motion, interpolation: "linear" }, settings));
  assert.deepEqual(motion, original);
  assert.deepEqual(poseAt(motion, 0), zeroPose());
});

async function panelFixture(t, respond) {
  const saved = new Map(["document", "localStorage", "fetch"].map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  const elements = new Map(), calls = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, { style: {}, value: "", classList: { toggle() {} },
      setAttribute() {}, append() {}, prepend() {} });
    return elements.get(id);
  };
  globalThis.document = { body: element("body"), createElement: tag => element("created-" + tag),
    querySelector: element, getElementById: element };
  globalThis.localStorage = { getItem: () => null, setItem() {} };
  const response = data => ({ ok: true, json: async () => data });
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, ...options });
    if (path.endsWith("capabilities")) return response({ available: true, model: { id: profile.id }, simulation_only: true });
    return respond(path, options, response);
  };
  let motion = createMotion(), pauses = 0;
  const panel = mountPhysicsPanel({ getMotion: () => motion, liveEnabled: () => false,
    pause: () => pauses++, onMode() {}, onResult() {}, onDownload() {}, notify() {} });
  t.after(async () => {
    panel.dispose();
    await new Promise(setImmediate);
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  await new Promise(setImmediate);
  element("created-button").onclick();
  return { panel, element, calls, get pauses() { return pauses; },
    changeMotion(next) { motion = next; panel.syncMotion(); } };
}

test("cancelling a still-pending submission also cancels its late worker and blocks stale playback", async t => {
  let submit;
  const pending = new Promise(resolve => { submit = resolve; });
  const ui = await panelFixture(t, (path, options, response) =>
    options.method === "POST" ? pending : response({ id: "late", status: "cancelled" }));
  const running = ui.element("physics-run").onclick();
  assert.equal(ui.panel.busy, true);
  const cancelled = ui.panel.cancel();
  assert.equal(ui.element("physics-run").disabled, true);
  submit({ ok: true, json: async () => ({ id: "late", status: "running" }) });
  await Promise.all([running, cancelled]);
  assert.ok(ui.calls.some(c => c.path === "/api/physics/jobs/late" && c.method === "DELETE"));
  assert.equal(ui.panel.result, null);
  assert.equal(ui.panel.busy, false);
  assert.equal(ui.element("physics-run").disabled, false);
  assert.ok(ui.calls.every(c => c.path.startsWith("/api/physics/")));
});

test("changing the source movement pauses and removes a completed cached simulation", async t => {
  const ui = await panelFixture(t, (_path, _options, response) =>
    response({ id: "complete", status: "completed", result: result() }));
  await ui.element("physics-run").onclick();
  assert.ok(ui.panel.result);
  const pauses = ui.pauses;
  ui.changeMotion({ ...createMotion(), name: "Другое движение" });
  assert.equal(ui.panel.result, null);
  assert.ok(ui.pauses > pauses);
  assert.equal(ui.element("physics-readings").hidden, true);
});
