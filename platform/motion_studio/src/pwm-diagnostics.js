import { PROFILE } from "./motion.js";

// Optional board register diagnostics. These are not servo angle measurements.
export function normalizePwmDiagnostics(value) {
  if (!value || !Array.isArray(value.servos) || value.servos.length > 6) return null;
  const seen = new Set();
  const servos = [];
  const integer = (n, min, max) => Number.isInteger(n) && n >= min && n <= max;
  for (const item of value.servos) {
    if (item && Object.hasOwn(item, "joint_id") && item.joint_id === null) continue;
    const id = item?.joint_id ?? item?.id;
    if (!item || !PROFILE.joints.some(j => j.id === id) || seen.has(id) ||
        typeof item.available !== "boolean" || typeof item.attached !== "boolean") return null;
    seen.add(id);
    servos.push({
      id,
      available: item.available,
      attached: item.attached,
      pin: integer(item.pin, 0, 48) ? item.pin : null,
      channel: integer(item.channel, 0, 7) ? item.channel : null,
      frequency_hz: item.frequency_available === true && integer(item.freq_hz, 0, 40000000) ? item.freq_hz : null,
      duty: item.duty_available === true && integer(item.duty, 0, 8191) ? item.duty : null,
      last_write_ok: item.last_write_available === true && typeof item.last_write_ok === "boolean" ? item.last_write_ok : null,
      skipped_unattached: item.skipped_unattached === true,
    });
  }
  return { servos };
}

export function pwmDiagnosticText(diagnostics, jointId) {
  const servo = diagnostics?.servos.find(item => item.id === jointId);
  if (!servo?.available) return "Нет данных драйвера";
  if (!servo.attached || servo.skipped_unattached) return "Канал привода отключён";
  const parts = [servo.last_write_ok === false ? "Драйвер не подтвердил запись" : "Канал включён"];
  if (servo.frequency_hz !== null) parts.push(`${servo.frequency_hz} Гц${servo.frequency_hz === 50 ? "" : " — ожидается 50 Гц"}`);
  if (servo.duty !== null) parts.push(`регистр PWM ${servo.duty}/8191`);
  return parts.join(" · ");
}
