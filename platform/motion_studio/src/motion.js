export const PROFILE = Object.freeze({
  id: "gosha-preview-v1",
  name: "Гоша",
  version: 1,
  hardware_validated: false,
  joints: [
    {
      id: "arm_negative_x",
      label: "Рука слева",
      short: "Рука слева",
      min: -55,
      max: 70,
      pivot: [-51, 0, -14],
      axis: "y",
      meshes: ["arm_negative_x", "hand_negative_x"],
    },
    {
      id: "arm_positive_x",
      label: "Рука справа",
      short: "Рука справа",
      min: -70,
      max: 55,
      pivot: [51, 0, -14],
      axis: "y",
      meshes: ["arm_positive_x", "hand_positive_x"],
    },
    {
      id: "leg_negative_x",
      label: "Нога слева",
      short: "Нога слева",
      min: -35,
      max: 35,
      pivot: [-22, 0, -41],
      axis: "z",
      meshes: ["leg_negative_x"],
    },
    {
      id: "leg_positive_x",
      label: "Нога справа",
      short: "Нога справа",
      min: -35,
      max: 35,
      pivot: [22, 0, -41],
      axis: "z",
      meshes: ["leg_positive_x"],
    },
    {
      id: "foot_negative_x",
      label: "Стопа слева",
      short: "Стопа слева",
      min: -30,
      max: 30,
      pivot: [-22, 0, -69],
      axis: "y",
      parent: "leg_negative_x",
      meshes: ["foot_negative_x"],
    },
    {
      id: "foot_positive_x",
      label: "Стопа справа",
      short: "Стопа справа",
      min: -30,
      max: 30,
      pivot: [22, 0, -69],
      axis: "y",
      parent: "leg_positive_x",
      meshes: ["foot_positive_x"],
    },
  ],
});

export const MAX_FILE_BYTES = 2_000_000;
export const MAX_FRAMES = 1000;
export const ROBOT_MOTION_PACKAGE_SCHEMA_VERSION = 1;
export const ROBOT_MOTION_PACKAGE_TYPE =
  "gosha.motion.robot-package-draft.v1";
export const ROBOT_PACKAGE_UPLOAD_MAX_BYTES = 12 * 1024;
export const ROBOT_PACKAGE_UPLOAD_CHUNK_BYTES = 2048;
const ROBOT_MOTION_LIVE_MODE = "motion_editor";
const ROBOT_MOTION_ACTIVE_JOINT_IDS = Object.freeze([
  "arm_positive_x",
  "leg_negative_x",
  "leg_positive_x",
  "foot_negative_x",
  "foot_positive_x",
]);
const ROBOT_PACKAGE_ANGLE_EPSILON = 0.1;
export const zeroPose = () =>
  Object.fromEntries(PROFILE.joints.map((j) => [j.id, 0]));
export const clone = (value) => structuredClone(value);
export const newId = () => crypto.randomUUID();
const fail = (message) => {
  throw new Error(message);
};
const object = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const jointById = (id) => PROFILE.joints.find((joint) => joint.id === id);
const round3 = (value) => Number(value.toFixed(3));
const textEncoder = new TextEncoder();

export function validatePose(value) {
  if (!object(value) || Object.keys(value).length !== PROFILE.joints.length)
    fail("В позе должны быть все суставы профиля Гоши.");
  return Object.fromEntries(
    PROFILE.joints.map((j) => {
      const n = value[j.id];
      if (!Number.isFinite(n) || n < j.min || n > j.max)
        fail(`${j.label}: угол должен быть от ${j.min}° до ${j.max}°.`);
      return [j.id, n];
    }),
  );
}

export function validateMotion(value) {
  if (!object(value) || value.schema_version !== 1)
    fail(
      "Неподдерживаемый формат движения. Нужен файл Motion Studio версии 1.",
    );
  if (
    value.profile_id !== PROFILE.id ||
    value.profile_version !== PROFILE.version
  )
    fail("Движение создано для другого профиля робота.");
  if (
    value.units !== "relative_degrees" ||
    value.preview_only !== true ||
    value.hardware_validated !== false
  )
    fail("Нужен проект с относительными углами для 3D-предпросмотра.");
  if (
    typeof value.id !== "string" ||
    !/^[a-zA-Z0-9_-]{1,80}$/.test(value.id) ||
    ["__proto__", "constructor", "prototype"].includes(value.id)
  )
    fail("Некорректный идентификатор движения.");
  if (
    typeof value.name !== "string" ||
    !value.name.trim() ||
    value.name.length > 80
  )
    fail("Название должно содержать от 1 до 80 символов.");
  if (
    !Number.isInteger(value.duration_ms) ||
    value.duration_ms < 500 ||
    value.duration_ms > 120000
  )
    fail("Длительность должна быть от 0,5 до 120 секунд.");
  if (!["smooth", "linear", "hold"].includes(value.interpolation))
    fail("Неизвестный способ перехода между позами.");
  if (
    !Array.isArray(value.keyframes) ||
    !value.keyframes.length ||
    value.keyframes.length > MAX_FRAMES
  )
    fail(`В движении должно быть от 1 до ${MAX_FRAMES} поз.`);
  let previous = -1;
  const keyframes = value.keyframes.map((frame) => {
    if (
      !object(frame) ||
      !Number.isInteger(frame.time_ms) ||
      frame.time_ms <= previous ||
      frame.time_ms > value.duration_ms
    )
      fail(
        "Время поз должно возрастать и оставаться внутри длительности движения.",
      );
    previous = frame.time_ms;
    return { time_ms: frame.time_ms, pose: validatePose(frame.pose) };
  });
  if (keyframes[0].time_ms !== 0)
    fail("Первая поза должна находиться на отметке 0 секунд.");
  return {
    schema_version: 1,
    id: value.id,
    name: value.name.trim(),
    profile_id: PROFILE.id,
    profile_version: PROFILE.version,
    units: "relative_degrees",
    preview_only: true,
    hardware_validated: false,
    duration_ms: value.duration_ms,
    interpolation: value.interpolation,
    keyframes,
  };
}

export function validateRobotMotionCapabilities(value) {
  if (!object(value)) fail("Нужны свежие ограничения робота из Live.");
  if (value.profile_id !== PROFILE.id)
    fail("Ограничения Live относятся к другому профилю робота.");
  if (value.mode !== ROBOT_MOTION_LIVE_MODE)
    fail("Для пакета робота нужен свежий Live-режим motion_editor.");
  if (!/^[a-f0-9]{64}$/.test(value.calibration_id ?? ""))
    fail("Робот не сообщил проверенный идентификатор калибровки.");
  if (
    !Number.isInteger(value.watchdog_ms) ||
    value.watchdog_ms < 100 ||
    value.watchdog_ms > 500 ||
    !Number.isInteger(value.max_rate_hz) ||
    value.max_rate_hz < 5 ||
    value.max_rate_hz > 20
  )
    fail("Live-профиль робота несовместим с безопасным запуском движения.");
  if (
    !Array.isArray(value.joint_limits) ||
    !value.joint_limits.length ||
    value.joint_limits.length > PROFILE.joints.length
  )
    fail("Робот не сообщил доступные приводы.");
  const ids = new Set();
  const joint_limits = value.joint_limits.map((limit) => {
    const joint = jointById(limit?.id);
    if (!joint || ids.has(limit.id))
      fail("Live-профиль содержит неизвестный или повторяющийся привод.");
    ids.add(limit.id);
    if (
      !finite(limit.min) ||
      !finite(limit.max) ||
      limit.min >= limit.max ||
      limit.min < joint.min ||
      limit.max > joint.max ||
      !finite(limit.max_speed_dps) ||
      limit.max_speed_dps <= 0 ||
      limit.max_speed_dps > 30
    )
      fail("Пределы Live не совпадают с профилем модели Гоши.");
    return {
      id: limit.id,
      min: limit.min,
      max: limit.max,
      max_speed_dps: limit.max_speed_dps,
    };
  });
  const commanded_pose = validatePose(value.commanded_pose);
  if (
    ids.size !== ROBOT_MOTION_ACTIVE_JOINT_IDS.length ||
    !ROBOT_MOTION_ACTIVE_JOINT_IDS.every((id) => ids.has(id))
  )
    fail("Live-профиль не совпадает с текущим набором подключённых приводов Гоши.");
  for (const limit of joint_limits)
    if (
      commanded_pose[limit.id] < limit.min ||
      commanded_pose[limit.id] > limit.max
    )
      fail("Текущая команда робота вне пределов Live-профиля.");
  return {
    profile_id: PROFILE.id,
    mode: typeof value.mode === "string" ? value.mode : "verified",
    calibration_id: value.calibration_id,
    calibrated: value.calibrated === true,
    commissioning: value.commissioning === true,
    initialization_required: value.initialization_required === true,
    right_arm_initialized:
      typeof value.right_arm_initialized === "boolean"
        ? value.right_arm_initialized
        : undefined,
    watchdog_ms: value.watchdog_ms,
    max_rate_hz: value.max_rate_hz,
    joint_limits,
    commanded_pose,
  };
}

function issue(message, details) {
  return { message, ...details };
}

export function prepareRobotMotionPackage(motion, capabilities) {
  const validMotion = validateMotion(motion);
  const caps = validateRobotMotionCapabilities(capabilities);
  const limits = new Map(caps.joint_limits.map((limit) => [limit.id, limit]));
  const issues = [];

  if (caps.commissioning)
    issues.push(
      issue(
        "Полные движения недоступны в первичном диагностическом режиме.",
        { code: "commissioning_mode" },
      ),
    );
  if (validMotion.interpolation === "hold")
    issues.push(
      issue("Для робота нужны плавные или линейные переходы, без hold.", {
        code: "unsupported_interpolation",
        interpolation: validMotion.interpolation,
      }),
    );

  for (const [frame_index, frame] of validMotion.keyframes.entries()) {
    for (const joint of PROFILE.joints) {
      const value = frame.pose[joint.id];
      const limit = limits.get(joint.id);
      if (!limit) {
        const expected = caps.commanded_pose[joint.id];
        if (Math.abs(value - expected) > ROBOT_PACKAGE_ANGLE_EPSILON)
          issues.push(
            issue(
              `${joint.label}: кадр ${frame_index} задаёт ${value}°, но привод недоступен в текущем Live-профиле.`,
              {
                code: "joint_unavailable",
                frame_index,
                time_ms: frame.time_ms,
                joint_id: joint.id,
                joint_label: joint.label,
                value,
                expected,
              },
            ),
          );
        continue;
      }
      if (
        value < limit.min - ROBOT_PACKAGE_ANGLE_EPSILON ||
        value > limit.max + ROBOT_PACKAGE_ANGLE_EPSILON
      )
        issues.push(
          issue(
            `${joint.label}: кадр ${frame_index} задаёт ${value}°, предел робота ${limit.min}…${limit.max}°.`,
            {
              code: "joint_out_of_range",
              frame_index,
              time_ms: frame.time_ms,
              joint_id: joint.id,
              joint_label: joint.label,
              value,
              min: limit.min,
              max: limit.max,
            },
          ),
        );
    }
  }

  let maxRequiredSpeedDps = 0;
  for (
    let frame_index = 1;
    frame_index < validMotion.keyframes.length;
    frame_index++
  ) {
    const previous = validMotion.keyframes[frame_index - 1];
    const frame = validMotion.keyframes[frame_index];
    const seconds = (frame.time_ms - previous.time_ms) / 1000;
    const interpolationFactor =
      validMotion.interpolation === "smooth" ? 1.5 : 1;
    for (const limit of caps.joint_limits) {
      const required_speed_dps =
        (Math.abs(frame.pose[limit.id] - previous.pose[limit.id]) / seconds) *
        interpolationFactor;
      maxRequiredSpeedDps = Math.max(maxRequiredSpeedDps, required_speed_dps);
      if (
        required_speed_dps >
        limit.max_speed_dps + ROBOT_PACKAGE_ANGLE_EPSILON
      ) {
        const joint = jointById(limit.id);
        issues.push(
          issue(
            `${joint.label}: участок ${frame_index - 1}→${frame_index} требует ${round3(required_speed_dps)}°/с, предел робота ${limit.max_speed_dps}°/с.`,
            {
              code: "joint_speed_exceeded",
              from_frame_index: frame_index - 1,
              to_frame_index: frame_index,
              from_time_ms: previous.time_ms,
              to_time_ms: frame.time_ms,
              joint_id: limit.id,
              joint_label: joint.label,
              required_speed_dps: round3(required_speed_dps),
              max_speed_dps: limit.max_speed_dps,
            },
          ),
        );
      }
    }
  }

  if (issues.length) return { ok: false, issues, package: null };

  const activeJoints = PROFILE.joints
    .map((joint) => joint.id)
    .filter((id) => limits.has(id));
  return {
    ok: true,
    issues: [],
    package: {
      schema_version: ROBOT_MOTION_PACKAGE_SCHEMA_VERSION,
      package_type: ROBOT_MOTION_PACKAGE_TYPE,
      source_motion_id: validMotion.id,
      name: validMotion.name,
      profile_id: PROFILE.id,
      profile_version: PROFILE.version,
      calibration_id: caps.calibration_id,
      caps_mode: caps.mode,
      units: "relative_degrees",
      source_preview_only: true,
      hardware_validated: false,
      live_compatible: true,
      robot_storage_implemented: false,
      duration_ms: validMotion.duration_ms,
      interpolation: validMotion.interpolation,
      active_joints: activeJoints,
      constraints: activeJoints.map((id) => ({ ...limits.get(id) })),
      safety: {
        watchdog_ms: caps.watchdog_ms,
        max_rate_hz: caps.max_rate_hz,
        max_required_speed_dps: round3(maxRequiredSpeedDps),
        unavailable_joints_baseline: Object.fromEntries(
          PROFILE.joints
            .filter((joint) => !limits.has(joint.id))
            .map((joint) => [joint.id, caps.commanded_pose[joint.id]]),
        ),
      },
      keyframes: validMotion.keyframes.map((frame) => ({
        time_ms: frame.time_ms,
        target: Object.fromEntries(
          activeJoints.map((id) => [id, frame.pose[id]]),
        ),
      })),
    },
  };
}

export function robotPackageCrc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) {
      const mask = -(crc & 1);
      crc = (crc >>> 1) ^ (0xedb88320 & mask);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function bytesToBase64(bytes) {
  if (typeof btoa === "function") {
    let text = "";
    for (const byte of bytes) text += String.fromCharCode(byte);
    return btoa(text);
  }
  return Buffer.from(bytes).toString("base64");
}

function safeRobotPackageId(packageDraft) {
  const raw = String(packageDraft.source_motion_id || "motion");
  if (!/^[a-zA-Z0-9_-]{1,80}$/.test(raw))
    fail("Некорректный постоянный идентификатор движения.");
  if (raw.length <= 56) return `motion-${raw}`;
  const suffix = robotPackageCrc32(textEncoder.encode(raw))
    .toString(16)
    .padStart(8, "0");
  return `motion-${raw.slice(0, 46)}-${suffix}`;
}

export function prepareRobotPackageUpload(packageDraft) {
  if (!object(packageDraft) || packageDraft.package_type !== ROBOT_MOTION_PACKAGE_TYPE)
    fail("Нужен проверенный черновик пакета робота.");
  if (packageDraft.profile_id !== PROFILE.id)
    fail("Пакет относится к другому профилю робота.");
  if (!/^[a-f0-9]{64}$/.test(packageDraft.calibration_id ?? ""))
    fail("Пакет не содержит проверенный идентификатор калибровки.");
  const bytes = textEncoder.encode(JSON.stringify(packageDraft));
  if (!bytes.length || bytes.length > ROBOT_PACKAGE_UPLOAD_MAX_BYTES)
    fail("Пакет робота слишком большой для текущего firmware upload.");
  const crc32 = robotPackageCrc32(bytes);
  const chunks = [];
  for (let offset = 0; offset < bytes.length; offset += ROBOT_PACKAGE_UPLOAD_CHUNK_BYTES) {
    const chunk = bytes.slice(offset, offset + ROBOT_PACKAGE_UPLOAD_CHUNK_BYTES);
    chunks.push({ offset, size: chunk.length, data_b64: bytesToBase64(chunk) });
  }
  return {
    package_id: safeRobotPackageId(packageDraft),
    profile_id: packageDraft.profile_id,
    calibration_id: packageDraft.calibration_id,
    total_size: bytes.length,
    crc32,
    chunks,
  };
}

export function createMotion(name = "Новое движение") {
  return validateMotion({
    schema_version: 1,
    id: newId(),
    name,
    profile_id: PROFILE.id,
    profile_version: 1,
    units: "relative_degrees",
    preview_only: true,
    hardware_validated: false,
    duration_ms: 4000,
    interpolation: "smooth",
    keyframes: [{ time_ms: 0, pose: zeroPose() }],
  });
}

export function poseAt(motion, time) {
  const frames = motion.keyframes;
  const t = Math.max(
    0,
    Math.min(motion.duration_ms, Number.isFinite(time) ? time : 0),
  );
  if (t <= frames[0].time_ms) return { ...frames[0].pose };
  const right = frames.findIndex((frame) => frame.time_ms >= t);
  if (right === -1) return { ...frames.at(-1).pose };
  const a = frames[right - 1];
  const b = frames[right];
  if (t === b.time_ms) return { ...b.pose };
  let mix = (t - a.time_ms) / (b.time_ms - a.time_ms);
  if (motion.interpolation === "smooth") mix = mix * mix * (3 - 2 * mix);
  if (motion.interpolation === "hold") mix = 0;
  return Object.fromEntries(
    PROFILE.joints.map((j) => [
      j.id,
      a.pose[j.id] + (b.pose[j.id] - a.pose[j.id]) * mix,
    ]),
  );
}

export function putPose(motion, time, pose) {
  const updated = clone(motion);
  const frame = { time_ms: Math.round(time), pose: validatePose(pose) };
  updated.keyframes = updated.keyframes
    .filter((f) => f.time_ms !== frame.time_ms)
    .concat(frame)
    .sort((a, b) => a.time_ms - b.time_ms);
  return validateMotion(updated);
}

export function mirrorPose(pose) {
  const valid = validatePose(pose);
  return Object.fromEntries(
    PROFILE.joints.map((j) => {
      const pair = j.id.includes("negative")
        ? j.id.replace("negative", "positive")
        : j.id.replace("positive", "negative");
      return [j.id, -valid[pair] || 0];
    }),
  );
}

export function parseMotion(text) {
  if (new TextEncoder().encode(text).length > MAX_FILE_BYTES)
    fail("Файл слишком большой. Максимум — 2 МБ.");
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    fail("Не удалось прочитать JSON. Выберите экспортированный файл движения.");
  }
  return validateMotion(value);
}

export function examples() {
  const make = (id, name, frames) =>
    validateMotion({
      ...createMotion(name),
      id,
      duration_ms: frames.at(-1)[0],
      keyframes: frames.map(([time_ms, values]) => ({
        time_ms,
        pose: { ...zeroPose(), ...values },
      })),
    });
  return [
    make("example-small-greeting", "Малое приветствие", [
      [0, {}],
      [2500, { arm_positive_x: 15 }],
      [7500, { arm_positive_x: -15 }],
      [10000, {}],
    ]),
    make("example-wave", "Приветствие", [
      [0, {}],
      [600, { arm_positive_x: -55 }],
      [1200, { arm_positive_x: -25 }],
      [1800, { arm_positive_x: -65 }],
      [2400, { arm_positive_x: -25 }],
      [3000, { arm_positive_x: -55 }],
      [4000, {}],
    ]),
    make("example-stretch", "Разминка", [
      [0, {}],
      [1200, { arm_positive_x: -60, arm_negative_x: 60 }],
      [2200, { arm_positive_x: -60, arm_negative_x: 60 }],
      [4000, {}],
    ]),
    make("example-sway", "Покачивание", [
      [0, {}],
      [1000, { foot_positive_x: 12, foot_negative_x: 12 }],
      [2000, { foot_positive_x: -12, foot_negative_x: -12 }],
      [3000, { foot_positive_x: 12, foot_negative_x: 12 }],
      [4000, {}],
    ]),
  ];
}

// Narrow only the former preview arm bounds; all other schema validation stays strict.
export function constrainLegacyArmMotion(value) {
  const next = clone(value);
  let changed = false;
  for (const frame of Array.isArray(next?.keyframes) ? next.keyframes : []) {
    for (const id of ["arm_negative_x", "arm_positive_x"]) {
      const n = frame?.pose?.[id];
      const joint = PROFILE.joints.find((item) => item.id === id);
      if (Number.isFinite(n) && n >= -70 && n <= 70 && (n < joint.min || n > joint.max)) {
        frame.pose[id] = Math.max(joint.min, Math.min(joint.max, n));
        changed = true;
      }
    }
  }
  return {motion: validateMotion(next), changed};
}
