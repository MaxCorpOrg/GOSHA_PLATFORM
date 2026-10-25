// The robot must explicitly advertise this separate speed policy. It never
// changes geometric calibration or grants higher speed to legacy firmware.
export const WORKING_SPEED_PROFILE = "working-v1";
export const MAX_WORKING_SPEED_DPS = 120;

export function advertisedSpeedCeiling(caps, legacyCeiling = 30) {
  if (caps.speed_profile === undefined) return legacyCeiling;
  if (caps.mode !== "motion_editor" || caps.speed_profile !== WORKING_SPEED_PROFILE)
    throw new Error("Робот сообщил неподдерживаемый профиль скорости.");
  return MAX_WORKING_SPEED_DPS;
}

export function liveSpeedLimit(caps, jointId = null) {
  if (caps?.commissioning) return 1;
  if (caps?.mode !== "motion_editor") return 15;
  const speeds = caps.joint_limits.map(joint => joint.max_speed_dps);
  // Six-axis firmware enforces each actuator's speed independently. Keep the
  // legacy five-axis scalar contract until that firmware advertises LEFT.
  const sharedLimit = Math.min(advertisedSpeedCeiling(caps, 10),
    caps.joint_limits.some(joint => joint.id === "arm_negative_x")
      ? Math.max(...speeds) : Math.min(...speeds));
  if (jointId === null) return sharedLimit;
  const joint = caps.joint_limits.find(joint => joint.id === jointId);
  return joint ? Math.min(sharedLimit, joint.max_speed_dps) : 0;
}
