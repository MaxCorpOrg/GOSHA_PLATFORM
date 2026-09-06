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
      min: -70,
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
      max: 70,
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
export const zeroPose = () =>
  Object.fromEntries(PROFILE.joints.map((j) => [j.id, 0]));
export const clone = (value) => structuredClone(value);
export const newId = () => crypto.randomUUID();
const fail = (message) => {
  throw new Error(message);
};
const object = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

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
      return [j.id, -valid[pair]];
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
