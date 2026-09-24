import test from "node:test";
import assert from "node:assert/strict";
import { registerPreviewTools } from "../src/webmcp.js";

test("optional preview tools share state and reject invalid seeks without changing it", () => {
  const registry = new Map();
  const controller = new AbortController();
  let state = { time_ms: 0, duration_ms: 4000, preview_only: true };
  const context = {
    registerTool(tool, options) {
      registry.set(tool.name, tool);
      assert.equal(options.signal, controller.signal);
    },
  };
  registerPreviewTools(
    context,
    {
      read: () => ({ ...state }),
      seek: (time_ms) => {
        state = { ...state, time_ms };
      },
    },
    controller.signal,
  );
  assert.deepEqual(
    [...registry.keys()],
    ["read_motion_preview", "seek_motion_preview"],
  );
  const read = registry.get("read_motion_preview");
  const seek = registry.get("seek_motion_preview");
  assert.equal(read.annotations.readOnlyHint, true);
  assert.equal(seek.annotations.readOnlyHint, false);
  assert.equal(seek.annotations.untrustedContentHint, true);
  assert.equal(seek.execute({ time_ms: 1200 }).time_ms, 1200);
  assert.equal(read.execute({}).time_ms, 1200);
  for (const input of [
    null,
    [],
    {},
    { time_ms: -1 },
    { time_ms: 4001 },
    { time_ms: "20" },
    { time_ms: 1.5 },
    { time_ms: 20, extra: true },
  ])
    assert.throws(() => seek.execute(input));
  assert.equal(read.execute({}).time_ms, 1200);
  assert.throws(() => read.execute({ extra: true }));
  assert.deepEqual(registerPreviewTools(undefined, {}, controller.signal), []);
});
