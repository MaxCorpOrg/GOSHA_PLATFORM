import { clone, validateMotion, examples, constrainLegacyArmMotion } from "./motion.js";

export const STORAGE_KEY = "ai-robots.motion-studio.v1";

export const LEGACY_BACKUP_KEY = `${STORAGE_KEY}.before-arm55`;

export function createStore(storage) {
  let writable = true;
  return {
    load() {
      let raw;
      try {
        raw = storage.getItem(STORAGE_KEY);
        if (!raw) {
          const motions = examples();
          return {
            motions,
            active_id: motions[0].id,
            revisions: {},
            installed: {},
            error: null,
          };
        }
        const data = JSON.parse(raw);
        if (
          data.schema_version !== 1 ||
          !Array.isArray(data.motions) ||
          !data.motions.length ||
          data.motions.length > 100
        )
          throw new Error("Invalid library");
        let adjusted = false;
        const normalize = (value) => {
          const result = constrainLegacyArmMotion(value);
          adjusted ||= result.changed;
          return result.motion;
        };
        const motions = data.motions.map(normalize);
        if (new Set(motions.map((m) => m.id)).size !== motions.length)
          throw new Error("Duplicate ids");
        const revisions = Object.create(null);
        const installed = Object.create(null);
        for (const motion of motions) {
          const saved = Object.hasOwn(data.revisions ?? {}, motion.id)
            ? data.revisions[motion.id]
            : [];
          if (!Array.isArray(saved) || saved.length > 20)
            throw new Error("Invalid revisions");
          revisions[motion.id] = saved.map((r) => {
            if (
              typeof r.saved_at !== "string" ||
              !Number.isFinite(Date.parse(r.saved_at))
            )
              throw new Error("Invalid date");
            const snapshot = normalize(r.motion);
            if (snapshot.id !== motion.id)
              throw new Error("Invalid revision identity");
            return { saved_at: r.saved_at, motion: snapshot };
          });
          const record = Object.hasOwn(data.installed ?? {}, motion.id)
            ? data.installed[motion.id]
            : undefined;
          if (record !== undefined) {
            if (
              !record ||
              !/^[a-zA-Z0-9_.-]{1,64}$/.test(record.package_id ?? "") ||
              !Number.isInteger(record.crc32) ||
              record.crc32 < 0 || record.crc32 > 0xffffffff ||
              !Number.isInteger(record.fingerprint) ||
              record.fingerprint < 0 || record.fingerprint > 0xffffffff
            ) throw new Error("Invalid installation record");
            if (record.motion_snapshot) {
              const snapshot = validateMotion(record.motion_snapshot);
              if (snapshot.id !== motion.id) throw new Error("Invalid installation identity");
            }
            if (record.revision !== undefined &&
                (!Number.isInteger(record.revision) || record.revision < 1))
              throw new Error("Invalid installation revision");
            installed[motion.id] = record;
          }
        }
        let originalLibrary = storage.getItem(LEGACY_BACKUP_KEY);
        if (adjusted && !originalLibrary) {
          storage.setItem(LEGACY_BACKUP_KEY, raw);
          originalLibrary = raw;
        }
        return {
          originalLibrary,
          adjusted,
          motions,
          active_id: motions.some((m) => m.id === data.active_id)
            ? data.active_id
            : motions[0].id,
          revisions,
          installed,
          error: null,
        };
      } catch {
        writable = false;
        const motions = examples();
        return {
          motions,
          active_id: motions[0].id,
          revisions: {},
          installed: {},
          error:
            "Локальная библиотека недоступна или повреждена. Исходные данные сохранены без изменений. Работайте с экспортом JSON.",
        };
      }
    },
    save({ motions, active_id, revisions, installed = {} }) {
      if (!writable)
        return {
          ok: false,
          error:
            "Автосохранение недоступно. Скачайте JSON, чтобы сохранить работу.",
        };
      try {
        if (motions.length > 100) throw new Error("Library limit");
        const data = {
          schema_version: 1,
          active_id,
          motions: motions.map(validateMotion),
          revisions: clone(revisions),
          installed: clone(installed),
        };
        storage.setItem(STORAGE_KEY, JSON.stringify(data));
        return { ok: true };
      } catch {
        return {
          ok: false,
          error:
            "Не удалось сохранить в браузере. Возможно, закончилось место. Скачайте JSON движения.",
        };
      }
    },
  };
}
