import { clone, validateMotion, prepareRobotMotionPackage, prepareRobotPackageUpload, robotPackageCrc32 } from "./motion.js";

export function motionFingerprint(value) {
  return robotPackageCrc32(new TextEncoder().encode(JSON.stringify(validateMotion(value))));
}

export function prepareInstallation(value, caps, previous) {
  const motion = clone(validateMotion(value));
  const result = prepareRobotMotionPackage(motion, caps);
  if (!result.ok) {
    const rest = result.issues.length > 1 ? ` Ещё: ${result.issues.length - 1}.` : "";
    throw new Error(`Пакет робота не готов: ${result.issues[0].message}${rest}`);
  }
  const revision = (previous?.revision || 0) + 1;
  const packageDraft = { ...result.package, revision };
  const upload = prepareRobotPackageUpload(packageDraft);
  return { packageDraft, record: {
    package_id: upload.package_id,
    crc32: upload.crc32,
    fingerprint: motionFingerprint(motion),
    revision,
    motion_snapshot: motion,
  } };
}

export function rememberInstallation(library, record, now = new Date().toISOString()) {
  const id = record.motion_snapshot.id;
  const previous = library.installed[id];
  if (previous?.motion_snapshot && previous.fingerprint !== record.fingerprint) {
    library.revisions[id] = [
      ...(library.revisions[id] || []),
      { saved_at: now, motion: clone(previous.motion_snapshot) },
    ].slice(-20);
  }
  library.installed[id] = clone(record);
}

// File round trips retain the robot package identity. Copies are a separate UI action.
export function importProject(library, value, now = new Date().toISOString()) {
  const motion = clone(validateMotion(value));
  const index = library.motions.findIndex((item) => item.id === motion.id);
  if (index < 0) {
    if (library.motions.length >= 100) throw new Error("В библиотеке уже 100 движений. Скачайте и удалите ненужные.");
    library.motions.push(motion);
  } else {
    if (motionFingerprint(library.motions[index]) !== motionFingerprint(motion)) {
      library.revisions[motion.id] = [
        ...(library.revisions[motion.id] || []),
        { saved_at: now, motion: clone(library.motions[index]) },
      ].slice(-20);
    }
    library.motions[index] = motion;
  }
  return motion;
}
