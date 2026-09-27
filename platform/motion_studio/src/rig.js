import * as THREE from "three";
import { PROFILE } from "./motion.js";

export function collectMeshes(root) {
  const named = new Map();
  root.traverse((object) => {
    if (!object.isMesh) return;
    if (named.has(object.name))
      throw new Error("В модели повторяются имена деталей.");
    named.set(object.name, object);
  });
  return named;
}

// The source GLB stores every part in the same CAD coordinate system.
// Subtracting the global pivot preserves that neutral pose when parenting.
export function buildRig(root, named, profile = PROFILE) {
  const expected = ["head", "body", ...profile.joints.flatMap((j) => j.meshes)];
  if (
    named.size !== expected.length ||
    expected.some((name) => !named.has(name))
  )
    throw new Error(
      "Модель не содержит нужных деталей. Повторите подготовку модели.",
    );
  const identity = new THREE.Matrix4();
  for (const mesh of named.values()) {
    mesh.updateWorldMatrix(true, false);
    if (
      !mesh.isMesh ||
      !mesh.matrix.equals(identity) ||
      !mesh.matrixWorld.equals(identity)
    )
      throw new Error(
        "Детали должны быть в исходных координатах CAD. Повторите подготовку модели.",
      );
  }
  const groups = new Map();
  for (const joint of profile.joints) {
    const group = new THREE.Group();
    group.name = `joint_${joint.id}`;
    const parent = profile.joints.find((p) => p.id === joint.parent);
    group.position.fromArray(joint.pivot);
    if (parent) group.position.sub(new THREE.Vector3(...parent.pivot));
    (parent ? groups.get(parent.id) : root).add(group);
    groups.set(joint.id, group);
    for (const name of joint.meshes) {
      const mesh = named.get(name);
      mesh.removeFromParent();
      mesh.position.set(...joint.pivot.map((n) => -n));
      group.add(mesh);
      mesh.userData.joint = joint.id;
    }
  }
  for (const name of ["head", "body"]) root.add(named.get(name));
  return {
    groups,
    setPose(pose) {
      for (const joint of profile.joints)
        groups.get(joint.id).rotation[joint.axis] = THREE.MathUtils.degToRad(
          pose[joint.id] * (joint.modelDirection ?? 1),
        );
    },
  };
}
