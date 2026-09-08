import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import {
  PROFILE,
  createMotion,
  zeroPose,
  poseAt,
  putPose,
  validatePose,
} from "../src/motion.js";

const source = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
function productionFunction(name) {
  const match = source.match(new RegExp(`^function ${name}\\([^]*?^}`, "m"));
  assert.ok(match, `Production function ${name} must be included`);
  return match[0];
}

// Execute the actual input, selection and rendering code without loading WebGL.
// Live's synchronous onUpdate callback must remain present even in 3D mode:
// selecting a joint refreshes the inspector and rewrites both input values.
function editor() {
  const elements = new Map();
  const context = createContext({
    PROFILE,
    poseAt,
    putPose,
    validatePose,
    motion: createMotion(),
    pose: zeroPose(),
    time: 0,
    selectedJoint: PROFILE.joints[0].id,
    playing: true,
    commits: [],
    errors: [],
    seconds: (value) => value / 1000,
    icon: () => "",
    document: { querySelectorAll: () => [], querySelector: () => ({ querySelectorAll: () => [{}, {}, {}] }) },
    $: (id) => {
      if (!elements.has(id)) {
        let value = "";
        elements.set(id, {
          get value() { return value; },
          set value(next) { value = String(next); },
          style: {},
          dataset: {},
          setAttribute() {},
        });
      }
      return elements.get(id);
    },
  });
  context.scene = {
    select() {},
    setPose: (pose) => { context.scenePose = structuredClone(pose); },
  };
  context.live = {
    enabled: false,
    snapshot: { state: "disconnected" },
    refresh: () => context.updateLiveView(),
    requestJointSelection: () => true,
    displayPose: (pose) => pose,
    recordPose: (pose) => pose,
    inspectorView: () => null,
    updateTarget() {},
  };
  context.notify = (message) => context.errors.push(message);
  context.render = () => context.renderPosition();
  context.commit = (next) => {
    context.commits.push(next);
    context.motion = next;
    context.pose = poseAt(next, context.time);
    context.renderPosition();
  };
  const start = source.indexOf("for (const j of PROFILE.joints) {\n  const range");
  const end = source.indexOf("\nfunction travelHistory", start);
  assert.ok(start >= 0 && end > start, "Production joint bindings must be included");
  runInContext([
    ...["attempt", "setPlaying", "selectJoint", "renderPosition", "recordPose", "updateLiveView"].map(productionFunction),
    source.slice(start, end),
  ].join("\n"), context);
  context.updateLiveView();
  return context;
}

test("dragging every joint survives selection refresh and updates the 3D pose", () => {
  const ui = editor();
  const expected = zeroPose();
  for (const [index, joint] of PROFILE.joints.entries()) {
    const range = ui.$("range-" + joint.id);
    for (const value of [12, -17, index + 1]) {
      range.value = value;
      range.oninput();
      expected[joint.id] = value;
      assert.equal(range.value, String(value));
      assert.equal(ui.$("number-" + joint.id).value, String(value));
      assert.deepEqual(ui.scenePose, expected);
      assert.equal(ui.selectedJoint, joint.id);
      assert.equal(ui.playing, false);
    }
    range.onchange();
    assert.deepEqual(poseAt(ui.motion, 0), expected);
  }
  assert.equal(ui.errors.length, 0);
});

test("numeric edits survive selection refresh and are recorded for every joint", () => {
  const ui = editor();
  const expected = zeroPose();
  for (const joint of PROFILE.joints) {
    const number = ui.$("number-" + joint.id);
    number.value = -17.5;
    number.onchange();
    expected[joint.id] = -17.5;
    assert.equal(number.value, "-17.5");
    assert.equal(ui.$("range-" + joint.id).value, "-17.5");
    assert.deepEqual(ui.scenePose, expected);
    assert.deepEqual(poseAt(ui.motion, 0), expected);
  }
  assert.equal(ui.errors.length, 0);
  assert.equal(ui.commits.length, PROFILE.joints.length);
});

test("invalid numeric edits are rejected before a refresh can replace them", () => {
  for (const value of ["", " ", "999", "-999", "not a number"]) {
    const ui = editor();
    const number = ui.$("number-" + PROFILE.joints[0].id);
    number.value = value;
    number.onchange();
    assert.equal(ui.errors.length, 1, `Must reject ${JSON.stringify(value)}`);
    assert.equal(ui.commits.length, 0);
    assert.deepEqual(ui.scenePose, zeroPose());
    assert.equal(number.value, "0");
  }
});

test("active Live joint locks editor selection until the session stops", () => {
  const ui = editor();
  ui.live.requestJointSelection = (id) => id === "arm_positive_x";
  ui.selectJoint("arm_positive_x");
  assert.equal(ui.selectedJoint, "arm_positive_x");
  ui.selectJoint("leg_negative_x");
  assert.equal(ui.selectedJoint, "arm_positive_x");
});

test("recording a pose can save the confirmed Live command explicitly", () => {
  const ui = editor();
  const commanded = { ...zeroPose(), arm_positive_x: -15 };
  ui.live.recordPose = () => commanded;
  ui.recordPose();
  assert.equal(ui.commits.length, 1);
  assert.equal(poseAt(ui.motion, 0).arm_positive_x, -15);
  assert.deepEqual(ui.scenePose, commanded);
});


test("Live inspector render keeps target separate and never records on input release", () => {
  const ui = editor();
  ui.live.enabled = true;
  ui.live.inspectorView = (id) => ({min:-15,max:15,disabled:false,available:true,command:id === "arm_positive_x" ? -2 : 0,value:id === "arm_positive_x" ? -15 : 0,reason:"Держите"});
  ui.updateLiveView();
  assert.equal(ui.$("range-arm_positive_x").value,"-15");
  assert.equal(ui.$("number-arm_positive_x").value,"-2");
  ui.$("range-arm_positive_x").oninput();
  ui.$("range-arm_positive_x").onchange();
  assert.equal(ui.commits.length,0);
  assert.equal(ui.pose.arm_positive_x,0);
});

test("editor numeric draft survives telemetry and commits on blur; STOP clears an unfinished draft", () => {
  const ui=editor(), sent=[];
  ui.live.enabled=true;ui.live.editorMode=true;
  let disabled=false;
  ui.live.inspectorView=()=>({min:-70,max:55,disabled,available:true,numberEditable:true,command:0,value:0,reason:""});
  ui.live.setInspectorAngle=(id,value)=>sent.push([id,value]);
  const number=ui.$("number-arm_positive_x");
  ui.document.activeElement=number;
  number.value="55";number.oninput();
  ui.document.activeElement=null;ui.updateLiveView();
  assert.equal(number.value,"55");number.onblur();
  assert.deepEqual(sent,[["arm_positive_x","55"]]);
  ui.document.activeElement=number;
  number.value="42";number.oninput();disabled=true;ui.updateLiveView();
  assert.equal(number.dataset.liveDraft,undefined);assert.equal(number.value,"0");
  number.onblur();assert.equal(sent.length,1);
});
