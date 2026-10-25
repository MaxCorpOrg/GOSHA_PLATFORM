import { PROFILE } from "./motion.js";

export const PHYSICS_SETTINGS_KEY = "ai-robots.motion-studio.physics.v1";
export const DEFAULT_PHYSICS_SETTINGS = Object.freeze({
  total_mass_g: 400, friction: 0.8, torque_nm: 0.18,
  target_speed_dps: 300, settle_ms: 600,
});
const modelId = "gosha-physics-estimated-v1";
const fields = [
  ["total_mass_g", "Масса робота, г", 100, 3000, 10],
  ["friction", "Трение стоп о пол", .05, 2, .05],
  ["torque_nm", "Усилие привода, Н·м", .02, 3, .01],
  ["target_speed_dps", "Скорость цели, °/с", 5, 1200, 5],
  ["settle_ms", "Наблюдение после жеста, мс", 0, 2000, 100],
];
export const physicsFingerprint = (motion, settings) => JSON.stringify({ motion, settings });

export function validatePhysicsResult(value) {
  if (!value || value.schema_version !== 1 || value.preview_only !== true ||
      value.hardware_validated !== false || value.parameter_status !== "assumed" ||
      value.model?.id !== modelId || value.engine?.device !== "cpu" ||
      !Number.isFinite(value.duration_ms) || value.duration_ms <= 0 || value.duration_ms > 122000 ||
      !Array.isArray(value.frames) || !value.frames.length || value.frames.length > 2500)
    throw new Error("Служба вернула несовместимый результат физики.");
  const metrics = value.metrics;
  if (!metrics || !["completed", "numerical_error", "out_of_bounds"].includes(metrics.termination) ||
      !["distance_mm", "max_tilt_deg", "max_tracking_error_deg", "max_effort_nm", "compute_seconds"].every(
        key => Number.isFinite(metrics[key]) && metrics[key] >= 0) ||
      (metrics.first_fall_ms !== null && (!Number.isFinite(metrics.first_fall_ms) ||
        metrics.first_fall_ms < 0 || metrics.first_fall_ms > value.duration_ms)))
    throw new Error("Некорректные показатели физического расчёта.");
  let previous = -1;
  for (const frame of value.frames) {
    if (!Number.isFinite(frame.time_ms) || frame.time_ms <= previous || frame.time_ms > value.duration_ms ||
        !PROFILE.joints.every(j => Number.isFinite(frame.pose?.[j.id])) ||
        frame.base?.position_m?.length !== 3 || frame.base?.quaternion_xyzw?.length !== 4 ||
        ![...frame.base.position_m, ...frame.base.quaternion_xyzw].every(Number.isFinite) ||
        Math.abs(Math.hypot(...frame.base.quaternion_xyzw) - 1) > .001 ||
        !Number.isFinite(frame.tilt_deg) || frame.tilt_deg < 0 || frame.tilt_deg > 180 ||
        !Array.isArray(frame.contacts) || frame.contacts.length > 24)
      throw new Error("Некорректный кадр физического расчёта.");
    for (const contact of frame.contacts)
      if (contact.position_m?.length !== 3 || !contact.position_m.every(Number.isFinite) ||
          !Number.isFinite(contact.normal_force_n) || contact.normal_force_n < 0)
        throw new Error("Некорректный контакт с полом.");
    previous = frame.time_ms;
  }
  if (value.frames[0].time_ms !== 0 || previous !== value.duration_ms)
    throw new Error("Физический расчёт не содержит начала или конца движения.");
  return value;
}

export function physicsFrameAt(result, timeMs) {
  const frames = result.frames;
  if (timeMs <= frames[0].time_ms) return frames[0];
  if (timeMs >= frames.at(-1).time_ms) return frames.at(-1);
  let low = 0, high = frames.length - 1;
  while (high - low > 1) {
    const middle = (low + high) >> 1;
    if (frames[middle].time_ms <= timeMs) low = middle;
    else high = middle;
  }
  const a = frames[low], b = frames[high];
  const t = (timeMs - a.time_ms) / (b.time_ms - a.time_ms);
  let qb = b.base.quaternion_xyzw;
  const qa = a.base.quaternion_xyzw;
  let dot = qa.reduce((sum, n, i) => sum + n * qb[i], 0);
  if (dot < 0) { qb = qb.map(n => -n); dot = -dot; }
  let q;
  if (dot > .9995) q = qa.map((n, i) => n + t * (qb[i] - n));
  else {
    const theta = Math.acos(Math.min(1, dot)), sine = Math.sin(theta);
    q = qa.map((n, i) => (Math.sin((1-t)*theta)*n + Math.sin(t*theta)*qb[i]) / sine);
  }
  const norm = Math.hypot(...q);
  q = q.map(n => n/norm);
  return { ...a, time_ms: timeMs,
    tilt_deg: Math.acos(Math.max(-1, Math.min(1, 1 - 2*(q[0]*q[0] + q[1]*q[1]))))*180/Math.PI,
    pose: Object.fromEntries(PROFILE.joints.map(j => [j.id, a.pose[j.id] + t*(b.pose[j.id]-a.pose[j.id])])),
    base: { position_m: a.base.position_m.map((n, i) => n + t*(b.base.position_m[i]-n)),
      quaternion_xyzw: q },
  };
}

export function mountPhysicsPanel({ getMotion, liveEnabled, pause, onMode, onResult, onDownload, notify }) {
  const button = document.createElement("button");
  button.id = "mode-physics";
  button.textContent = "Физика";
  button.setAttribute("aria-pressed", "false");
  document.querySelector(".mode-switch").append(button);
  const panel = document.createElement("section");
  panel.className = "physics-panel";
  panel.hidden = true;
  panel.setAttribute("aria-label", "Физическая симуляция");
  panel.innerHTML = `
    <span class="eyebrow">ВИРТУАЛЬНЫЙ РОБОТ</span><h2>Физика движения</h2>
    <p class="physics-assumption">Расчётная модель: масса и приводы не измерены. Результат нужно сверить с настоящим роботом.</p>
    <details id="physics-settings" open><summary>Параметры модели <span>изменить</span></summary>
      <div class="physics-fields">${fields.map(([key, label, min, max, step]) => `<label for="physics-${key}">${label}<input id="physics-${key}" type="number" min="${min}" max="${max}" step="${step}" value="${DEFAULT_PHYSICS_SETTINGS[key]}" /></label>`).join("")}</div>
    </details>
    <button id="physics-run" class="button accent" type="button">Рассчитать движение</button>
    <button id="physics-cancel" class="button" type="button" hidden>Отменить расчёт</button>
    <p id="physics-status" class="physics-status" role="status">Проверяем локальный движок…</p>
    <div id="physics-readings" hidden>
      <h3>На текущем кадре</h3><dl class="physics-metrics">
        <dt>Наклон корпуса</dt><dd id="physics-tilt">—</dd>
        <dt>Контакты с полом</dt><dd id="physics-contacts">—</dd>
        <dt>Смещение X / Y</dt><dd id="physics-position">—</dd>
      </dl>
      <h3>Итог расчёта</h3><dl class="physics-metrics">
        <dt>Перемещение</dt><dd id="physics-distance">—</dd>
        <dt>Наибольший наклон</dt><dd id="physics-max-tilt">—</dd>
        <dt>Отставание от цели</dt><dd id="physics-error">—</dd>
        <dt>Пиковое усилие</dt><dd id="physics-effort">—</dd>
        <dt>Время расчёта</dt><dd id="physics-compute">—</dd>
      </dl>
      <button id="physics-step" class="button quiet" type="button">Следующий кадр · 20 мс</button>
      <button id="physics-export" class="button quiet" type="button">Сохранить отчёт</button>
    </div>
    <p class="physics-engine">Newton · MuJoCo · расчёт на процессоре</p>`;
  document.querySelector(".inspector").prepend(panel);
  const el = id => document.getElementById(id);
  let enabled = false, busy = false, available = false, result = null;
  let settings = { ...DEFAULT_PHYSICS_SETTINGS };
  let generation = 0, jobId = null, resultKey = null, cancelling = false;
  let pendingPost = null, cancellation = null;
  let status = "Проверяем локальный движок…";
  let enteredMotion = JSON.stringify(getMotion());
  let disposed = false;
  const activeRequests = new AbortController();

  function saveSettings() {
    try { localStorage.setItem(PHYSICS_SETTINGS_KEY, JSON.stringify(settings)); } catch { /* optional */ }
  }
  try {
    const saved = JSON.parse(localStorage.getItem(PHYSICS_SETTINGS_KEY) || "null");
    for (const [key,,min,max] of fields)
      if (Number.isFinite(saved?.[key]) && saved[key] >= min && saved[key] <= max) settings[key] = saved[key];
  } catch { /* optional */ }
  for (const [key] of fields) el(`physics-${key}`).value = settings[key];

  function render() {
    button.setAttribute("aria-pressed", String(enabled));
    panel.hidden = !enabled;
    document.querySelector(".workspace").classList.toggle("physics-workspace", enabled);
    document.body.classList.toggle("physics-mode", enabled);
    if (enabled) {
      el("mode-preview").setAttribute("aria-pressed", "false");
      document.querySelector(".preview-badge").textContent = "Физика · расчётная модель";
    }
    el("physics-run").disabled = !available || busy || cancelling;
    el("physics-cancel").hidden = !busy;
    el("physics-status").textContent = status;
    el("physics-status").classList.toggle("physics-busy", busy);
    el("physics-readings").hidden = !result;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, signal: activeRequests.signal });
    if (!response.ok) throw new Error((await response.text()).slice(0, 500) || "Служба физики недоступна.");
    return response.json();
  }

  function cancel() {
    if (cancellation) return cancellation;
    generation++;
    const currentId = jobId, post = pendingPost;
    jobId = null;
    busy = false;
    cancelling = true;
    render();
    cancellation = (async () => {
      try {
        const id = currentId ?? (post ? (await post).id : null);
        if (id) await api(`/api/physics/jobs/${id}`, { method: "DELETE" });
      } catch { /* server timeout also bounds the worker */ }
    })().finally(() => { cancelling = false; cancellation = null; render(); });
    return cancellation;
  }

  function invalidate(message = "Движение изменено. Рассчитайте его заново.") {
    pause();
    result = null;
    resultKey = null;
    if (busy) void cancel();
    status = available ? message : "Запустите локальный редактор со службой физики.";
    render();
  }

  async function run() {
    if (busy || cancelling || !available || !enabled || liveEnabled()) return;
    pause();
    const motion = structuredClone(getMotion());
    const key = physicsFingerprint(motion, settings);
    const current = ++generation;
    result = null;
    busy = true;
    status = "Рассчитываем движение… Первый запуск может занять больше времени.";
    render();
    onResult(null);
    let submission;
    try {
      submission = api("/api/physics/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: modelId, motion, settings }) });
      pendingPost = submission;
      const job = await submission;
      if (pendingPost === submission) pendingPost = null;
      // A cancelled POST can finish after the mode has changed. Cancel its
      // worker through cancel(), which retains the submission promise.
      if (current !== generation) return;
      jobId = job.id;
      let value = job;
      while (value.status === "running") {
        await new Promise(resolve => setTimeout(resolve, 250));
        if (current !== generation || disposed) return;
        value = await api(`/api/physics/jobs/${jobId}`);
      }
      jobId = null;
      if (value.status !== "completed") throw new Error(value.error || "Расчёт отменён.");
      if (current !== generation || !enabled || key !== physicsFingerprint(getMotion(), settings)) return;
      result = validatePhysicsResult(value.result);
      resultKey = key;
      const m = result.metrics;
      status = m.termination !== "completed" ? "Расчёт остановлен: проверьте отчёт."
        : m.first_fall_ms !== null ? `Корпус наклонился больше 50° на ${(m.first_fall_ms/1000).toFixed(2)} с.`
        : "Расчёт готов. Шкала показывает симуляцию; ромбы — заданные позы.";
      el("physics-distance").textContent = `${m.distance_mm.toFixed(1)} мм`;
      el("physics-max-tilt").textContent = `${m.max_tilt_deg.toFixed(1)}°`;
      el("physics-error").textContent = `${m.max_tracking_error_deg.toFixed(1)}°`;
      el("physics-effort").textContent = `${m.max_effort_nm.toFixed(3)} Н·м`;
      el("physics-compute").textContent = `${m.compute_seconds.toFixed(2)} с`;
      el("physics-settings").open = false;
      busy = false;
      render();
      onResult(result);
    } catch (error) {
      if (current === generation && !disposed) {
        status = `Не удалось рассчитать: ${error.message}`;
        notify(status, true);
      }
    } finally {
      if (pendingPost === submission) pendingPost = null;
      if (current === generation) { busy = false; render(); }
    }
  }

  button.onclick = () => {
    if (liveEnabled()) { notify("Сначала переключитесь в 3D и отключите Live.", true); return; }
    if (enabled) return;
    pause();
    enabled = true;
    enteredMotion = JSON.stringify(getMotion());
    render();
    onMode(true);
  };
  el("physics-run").onclick = run;
  el("physics-cancel").onclick = async () => { await cancel(); status = "Расчёт отменён."; render(); };
  el("physics-export").onclick = () => { if (result) onDownload(result); };
  for (const [key,,min,max] of fields) el(`physics-${key}`).onchange = () => {
    const value = Number(el(`physics-${key}`).value);
    if (!Number.isFinite(value) || value < min || value > max) {
      el(`physics-${key}`).value = settings[key];
      notify(`Допустимое значение: ${min}…${max}.`, true);
      return;
    }
    settings[key] = value;
    saveSettings();
    invalidate("Параметры изменены. Рассчитайте движение заново.");
    pause();
    onResult(null);
  };
  api("/api/physics/capabilities").then(caps => {
    if (disposed) return;
    available = caps.available === true && caps.model?.id === modelId;
    status = available ? "Готово к расчёту. Выберите движение и нажмите «Рассчитать движение»."
      : "Окружение физики не готово. Проверьте установку requirements-physics.txt.";
    if (caps.simulation_only) {
      el("mode-live").disabled = true;
      el("mode-live").title = "В этой локальной копии доступен только виртуальный робот.";
    }
    render();
  }).catch(() => {
    status = "Для физики запустите локальный редактор командой start-physics.sh.";
    render();
  });
  render();
  return {
    cancel,
    get enabled() { return enabled; }, get busy() { return busy; }, get result() { return result; },
    frame(timeMs) { return result ? physicsFrameAt(result, timeMs) : null; },
    render,
    syncMotion() {
      const next = JSON.stringify(getMotion());
      if (next !== enteredMotion) {
        enteredMotion = next;
        if (result || busy) invalidate();
      }
      if (resultKey && resultKey !== physicsFingerprint(getMotion(), settings)) invalidate();
    },
    showFrame(frame) {
      if (!frame) return;
      el("physics-tilt").textContent = `${frame.tilt_deg.toFixed(1)}°`;
      el("physics-contacts").textContent = String(frame.contacts.length);
      el("physics-position").textContent = frame.base.position_m.slice(0,2).map(n => (n*1000).toFixed(1)).join(" / ") + " мм";
    },
    leave() {
      if (!enabled) return;
      enabled = false;
      void cancel();
      pause();
      el("mode-preview").setAttribute("aria-pressed", "true");
      render();
      onMode(false);
    },
    dispose() { disposed = true; void cancel().finally(() => activeRequests.abort()); },
  };
}
