import { clone, validateMotion, examples } from "./motion.js";

export const STORAGE_KEY = "ai-robots.motion-studio.v1";

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
        const motions = data.motions.map(validateMotion);
        if (new Set(motions.map((m) => m.id)).size !== motions.length)
          throw new Error("Duplicate ids");
        const revisions = Object.create(null);
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
            const snapshot = validateMotion(r.motion);
            if (snapshot.id !== motion.id)
              throw new Error("Invalid revision identity");
            return { saved_at: r.saved_at, motion: snapshot };
          });
        }
        return {
          motions,
          active_id: motions.some((m) => m.id === data.active_id)
            ? data.active_id
            : motions[0].id,
          revisions,
          error: null,
        };
      } catch {
        writable = false;
        const motions = examples();
        return {
          motions,
          active_id: motions[0].id,
          revisions: {},
          error:
            "Локальная библиотека недоступна или повреждена. Исходные данные сохранены без изменений. Работайте с экспортом JSON.",
        };
      }
    },
    save({ motions, active_id, revisions }) {
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
