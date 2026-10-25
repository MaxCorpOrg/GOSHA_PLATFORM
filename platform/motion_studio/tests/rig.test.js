import test from "node:test";
import assert from "node:assert/strict";
import { readFile, access } from "node:fs/promises";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { PROFILE, zeroPose, toRobotAngle } from "../src/motion.js";
import { buildRig, collectMeshes } from "../src/rig.js";

function synthetic() {
  const names = ["head", "body", ...PROFILE.joints.flatMap((j) => j.meshes)];
  return new Map(
    names.map((name) => [
      name,
      new THREE.Mesh(
        new THREE.BoxGeometry(1, 1, 1),
        new THREE.MeshBasicMaterial(),
      ),
    ]),
  );
}
test("parenting preserves every CAD part in the neutral pose", () => {
  const root = new THREE.Group();
  const meshes = synthetic();
  const rig = buildRig(root, meshes);
  rig.setPose(zeroPose());
  root.updateMatrixWorld(true);
  for (const mesh of meshes.values())
    assert.deepEqual(mesh.matrixWorld.elements, new THREE.Matrix4().elements);
});
test("foot follows its leg and retains independent ankle rotation; opposite side stays fixed", () => {
  const root = new THREE.Group();
  const meshes = synthetic();
  const rig = buildRig(root, meshes);
  rig.setPose({ ...zeroPose(), leg_positive_x: 20 });
  root.updateMatrixWorld(true);
  for (const [actual, expected] of meshes.get("foot_negative_x").matrixWorld.elements
    .map((value, index) => [value, meshes.get("leg_negative_x").matrixWorld.elements[index]]))
    assert.ok(Math.abs(actual - expected) < 1e-10);
  assert.deepEqual(
    meshes.get("foot_positive_x").matrixWorld.elements,
    new THREE.Matrix4().elements,
  );
  const legBefore = meshes.get("leg_negative_x").matrixWorld.clone();
  rig.setPose({ ...zeroPose(), leg_positive_x: 20, foot_positive_x: 15 });
  root.updateMatrixWorld(true);
  assert.deepEqual(
    meshes.get("leg_negative_x").matrixWorld.elements,
    legBefore.elements,
  );
  assert.notDeepEqual(
    meshes.get("foot_negative_x").matrixWorld.elements,
    legBefore.elements,
  );
});
test("rotations keep CAD shaft centres and axes fixed", () => {
  const root = new THREE.Group();
  const meshes = synthetic();
  const rig = buildRig(root, meshes);
  // Independent measurements from robot.step, in millimetres.
  // Each point on a shaft must stay fixed when its joint rotates.
  const shafts = [
    ["arm_negative_x", "arm_positive_x", [50, 0, -7], [0, 1, 0]],
    ["arm_positive_x", "arm_negative_x", [-50, 0, -7], [0, 1, 0]],
    ["leg_negative_x", "leg_positive_x", [25.5, 0, -40], [0, 0, 1]],
    ["leg_positive_x", "leg_negative_x", [-25.5, 0, -40], [0, 0, 1]],
    ["foot_negative_x", "foot_positive_x", [25.5, 0, -71.5], [0, 1, 0]],
    ["foot_positive_x", "foot_negative_x", [-25.5, 0, -71.5], [0, 1, 0]],
  ];
  for (const [id, mesh, centre, axis] of shafts) {
    for (const angle of [-15, 15]) {
      rig.setPose({ ...zeroPose(), [id]: angle });
      root.updateMatrixWorld(true);
      for (const offset of [-20, 0, 20]) {
        const point = new THREE.Vector3(...centre).addScaledVector(new THREE.Vector3(...axis), offset);
        assert.ok(meshes.get(mesh).localToWorld(point.clone()).distanceTo(point) < 1e-10,
          `${id}: shaft point must not translate`);
      }
    }
  }
});
test("arms lift, hips turn without lifting, and ankles tilt along their CAD axes", () => {
  const root = new THREE.Group();
  const meshes = synthetic();
  const rig = buildRig(root, meshes);
  const tips = {
    arm_negative_x: ["hand_positive_x", [83.61, 0, -40.86]],
    arm_positive_x: ["hand_negative_x", [-83.61, 0, -40.86]],
    leg_negative_x: ["foot_positive_x", [49, -24, -89]],
    leg_positive_x: ["foot_negative_x", [-49, -24, -89]],
    foot_negative_x: ["foot_positive_x", [49, -24, -89]],
    foot_positive_x: ["foot_negative_x", [-49, -24, -89]],
  };
  for (const joint of PROFILE.joints) {
    const [mesh, tip] = tips[joint.id];
    rig.setPose(zeroPose());
    rig.setPose({ ...zeroPose(), [joint.id]: toRobotAngle(joint.id, 15) });
    root.updateMatrixWorld(true);
    const moved = meshes.get(mesh).localToWorld(new THREE.Vector3(...tip));
    if (joint.id.startsWith("arm")) assert.ok(moved.z > tip[2]);
    if (joint.id.startsWith("leg")) {
      assert.ok(Math.abs(moved.z - tip[2]) < 1e-10);
      assert.ok(Math.abs(moved.y - tip[1]) > 1);
    }
    if (joint.id.startsWith("foot")) {
      assert.ok(Math.abs(moved.y - tip[1]) < 1e-10);
      assert.ok(moved.z > tip[2] + 1, `${joint.label}: rightward control raises the outer edge`);
    }
  }
});
test("candidate model mapping raises both outer foot edges at the reported pose; opposite angles lower them", () => {
  const root = new THREE.Group();
  const meshes = synthetic();
  const rig = buildRig(root, meshes);
  // Reported pose: left -12°, right +12° in the existing robot contract.
  // This checks the model convention, not physical calibration.
  // Compare edges at the same CAD sole height, independently of PROFILE.
  for (const direction of [1, -1]) {
    rig.setPose({ ...zeroPose(), foot_negative_x: -12 * direction, foot_positive_x: 12 * direction });
    root.updateMatrixWorld(true);
    for (const [name, outerX, innerX] of [
      ["foot_positive_x", 49, 17],
      ["foot_negative_x", -49, -17],
    ]) {
      const mesh = meshes.get(name);
      const outer = mesh.localToWorld(new THREE.Vector3(outerX, -24, -89));
      const inner = mesh.localToWorld(new THREE.Vector3(innerX, -24, -89));
      assert.ok(direction * (outer.z - inner.z) > 6, `${name}: sole must follow the candidate model direction`);
    }
  }
});
test("every left/right control selects the corresponding anatomical side in front view", () => {
  const root = new THREE.Group();
  const meshes = synthetic();
  buildRig(root, meshes);
  assert.equal(meshes.get("arm_negative_x").userData.joint, "arm_positive_x");
  assert.equal(meshes.get("hand_negative_x").userData.joint, "arm_positive_x");
  assert.equal(meshes.get("arm_positive_x").userData.joint, "arm_negative_x");
  assert.equal(meshes.get("hand_positive_x").userData.joint, "arm_negative_x");
  for (const part of ["leg", "foot"]) {
    assert.equal(meshes.get(`${part}_negative_x`).userData.joint, `${part}_positive_x`);
    assert.equal(meshes.get(`${part}_positive_x`).userData.joint, `${part}_negative_x`);
  }
});
test("missing part is rejected before changing the scene graph", () => {
  const root = new THREE.Group();
  const meshes = synthetic();
  meshes.delete("foot_negative_x");
  assert.throws(() => buildRig(root, meshes));
  assert.equal(root.children.length, 0);
});

test("extra, transformed or duplicate parts fail before changing the scene graph", () => {
  for (const change of [
    (meshes) => meshes.set("extra_button", new THREE.Mesh()),
    (meshes) => {
      meshes.get("arm_positive_x").rotation.z = 0.25;
    },
    (meshes) => {
      meshes.get("foot_positive_x").scale.set(2, 1, 1);
    },
    (meshes) => {
      meshes.get("body").position.x = 1;
    },
    (meshes) => {
      const parent = new THREE.Group();
      parent.rotation.x = 0.5;
      parent.add(meshes.get("head"));
    },
  ]) {
    const root = new THREE.Group();
    const meshes = synthetic();
    change(meshes);
    assert.throws(() => buildRig(root, meshes));
    assert.equal(root.children.length, 0);
  }
  const source = new THREE.Group();
  const a = new THREE.Mesh();
  const b = new THREE.Mesh();
  a.name = b.name = "head";
  source.add(a, b);
  assert.throws(() => collectMeshes(source));
});
test("actual private GLB parses through the same Three.js loader and preserves neutral bounds", async (t) => {
  const file = new URL(
    "../local_only/public/models/gosha-30092026-208bb99d.glb",
    import.meta.url,
  );
  try {
    await access(file);
  } catch {
    t.skip("Private model is prepared locally and excluded from git.");
    return;
  }
  const bytes = await readFile(file);
  const served = await readFile(new URL("../local_only/public/models/gosha.glb", import.meta.url));
  assert.deepEqual(served, bytes, "Authenticated route must serve the same new assembly");
  const sceneSource = await readFile(new URL("../src/scene.js", import.meta.url), "utf8");
  assert.match(sceneSource, /models\/gosha\.glb\?v=208bb99d/);
  const model = await new GLTFLoader().parseAsync(
    bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    "",
  );
  const meshes = new Map();
  model.scene.traverse((o) => {
    if (o.isMesh) meshes.set(o.name, o);
  });
  assert.equal(meshes.size, 12);
  assert.equal(model.parser.json.extras.sha256, "208bb99daed400921ab14fb5c21c1cbb3f1f798b78f952ca11447c59a709372e");
  const manifest = JSON.parse(await readFile(new URL("../local_only/public/models/gosha-30092026-208bb99d.json", import.meta.url), "utf8"));
  assert.deepEqual([...meshes.keys()].sort(), manifest.parts.slice().sort());
  for (const mesh of meshes.values()) {
    assert.equal(mesh.geometry.getAttribute("color").count, mesh.geometry.getAttribute("position").count);
    assert.equal(mesh.material.vertexColors, true);
  }
  const before = new THREE.Box3().setFromObject(model.scene);
  assert.ok(before.min.distanceTo(new THREE.Vector3(-95.39, -36.51, -89)) < .02);
  assert.ok(before.max.distanceTo(new THREE.Vector3(95.19, 36.5, 36.09)) < .02);
  const root = new THREE.Group();
  const rig = buildRig(root, meshes);
  rig.setPose(zeroPose());
  const after = new THREE.Box3().setFromObject(root);
  assert.ok(before.min.distanceTo(after.min) < 1e-5);
  assert.ok(before.max.distanceTo(after.max) < 1e-5);
  assert.equal(
    [...meshes.values()].reduce(
      (sum, mesh) => sum + mesh.geometry.index.count / 3,
      0,
    ),
    manifest.triangles,
  );
  assert.ok(manifest.triangles < 60000, "Keep the phone geometry budget");
  for (const side of ["negative_x", "positive_x"]) {
    const sign = side === "negative_x" ? -1 : 1;
    for (const part of ["arm", "hand", "leg", "foot"]) {
      const position = meshes.get(`${part}_${side}`).geometry.getAttribute("position");
      for (let i = 0; i < position.count; i++) assert.ok(position.getX(i) * sign > 0, `${part}: no crossed limbs`);
    }
  }
  rig.setPose({ ...zeroPose(), arm_positive_x: -45, foot_negative_x: 20 });
  root.updateMatrixWorld(true);
  for (const mesh of meshes.values())
    assert.ok(mesh.matrixWorld.elements.every(Number.isFinite));
});
