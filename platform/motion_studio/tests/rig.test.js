import test from "node:test";
import assert from "node:assert/strict";
import { readFile, access } from "node:fs/promises";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { PROFILE, zeroPose } from "../src/motion.js";
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
  for (const [actual, expected] of meshes.get("foot_positive_x").matrixWorld.elements
    .map((value, index) => [value, meshes.get("leg_positive_x").matrixWorld.elements[index]]))
    assert.ok(Math.abs(actual - expected) < 1e-10);
  assert.deepEqual(
    meshes.get("foot_negative_x").matrixWorld.elements,
    new THREE.Matrix4().elements,
  );
  const legBefore = meshes.get("leg_positive_x").matrixWorld.clone();
  rig.setPose({ ...zeroPose(), leg_positive_x: 20, foot_positive_x: 15 });
  root.updateMatrixWorld(true);
  assert.deepEqual(
    meshes.get("leg_positive_x").matrixWorld.elements,
    legBefore.elements,
  );
  assert.notDeepEqual(
    meshes.get("foot_positive_x").matrixWorld.elements,
    legBefore.elements,
  );
});
test("moving every joint slider right raises its visible 3D limb", () => {
  const root = new THREE.Group();
  const meshes = synthetic();
  const rig = buildRig(root, meshes);
  const tips = {
    arm_negative_x: ["hand_positive_x", [59, 0, -78]],
    arm_positive_x: ["hand_negative_x", [-59, 0, -78]],
    leg_negative_x: ["leg_negative_x", [-23, 0, -87]],
    leg_positive_x: ["leg_positive_x", [23, 0, -87]],
    foot_negative_x: ["foot_negative_x", [-23, -36, -88]],
    foot_positive_x: ["foot_positive_x", [23, -36, -88]],
  };
  const height = (mesh, point) => {
    root.updateMatrixWorld(true);
    return meshes.get(mesh).localToWorld(new THREE.Vector3(...point)).z;
  };
  for (const joint of PROFILE.joints) {
    const [mesh, tip] = tips[joint.id];
    rig.setPose(zeroPose());
    const neutral = height(mesh, tip);
    rig.setPose({ ...zeroPose(), [joint.id]: joint.id === "arm_positive_x" ? -15 : 15 });
    assert.ok(height(mesh, tip) > neutral, `${joint.label} must rise at +15°`);
  }
});
test("the connected right arm selects the robot's right mesh in front view", () => {
  const root = new THREE.Group();
  const meshes = synthetic();
  buildRig(root, meshes);
  assert.equal(meshes.get("arm_negative_x").userData.joint, "arm_positive_x");
  assert.equal(meshes.get("hand_negative_x").userData.joint, "arm_positive_x");
  assert.equal(meshes.get("arm_positive_x").userData.joint, "arm_negative_x");
  assert.equal(meshes.get("hand_positive_x").userData.joint, "arm_negative_x");
  for (const side of ["negative_x", "positive_x"])
    assert.equal(meshes.get(`leg_${side}`).userData.joint, `leg_${side}`);
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
    "../local_only/public/models/gosha.glb",
    import.meta.url,
  );
  try {
    await access(file);
  } catch {
    t.skip("Private model is prepared locally and excluded from git.");
    return;
  }
  const bytes = await readFile(file);
  const model = await new GLTFLoader().parseAsync(
    bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    "",
  );
  const meshes = new Map();
  model.scene.traverse((o) => {
    if (o.isMesh) meshes.set(o.name, o);
  });
  assert.equal(meshes.size, 10);
  const before = new THREE.Box3().setFromObject(model.scene);
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
    567818,
  );
  rig.setPose({ ...zeroPose(), arm_positive_x: -45, foot_negative_x: 20 });
  root.updateMatrixWorld(true);
  for (const mesh of meshes.values())
    assert.ok(mesh.matrixWorld.elements.every(Number.isFinite));
});
