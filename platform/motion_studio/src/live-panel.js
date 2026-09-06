import { LiveSession } from "./live.js";
import { PROFILE } from "./motion.js";

export function livePlaybackScale(motion, caps, speed) {
  if (motion.interpolation === "hold")
    throw new Error(
      "Для Live-воспроизведения выберите плавные или линейные переходы.",
    );
  for (const joint of PROFILE.joints) {
    if (caps.joint_limits.some((limit) => limit.id === joint.id)) continue;
    if (
      motion.keyframes.some(
        (frame) =>
          Math.abs(frame.pose[joint.id] - caps.commanded_pose[joint.id]) > 0.1,
      )
    )
      throw new Error(
        `Движение использует недоступный сустав: ${joint.label}. Измените его позы в режиме 3D.`,
      );
  }
  let scale = 1;
  for (let i = 1; i < motion.keyframes.length; i++) {
    const a = motion.keyframes[i - 1];
    const b = motion.keyframes[i];
    for (const joint of caps.joint_limits) {
      for (const frame of [a, b])
        if (
          frame.pose[joint.id] < joint.min ||
          frame.pose[joint.id] > joint.max
        )
          throw new Error(
            "В движении есть позы вне проверенных пределов робота.",
          );
      const slope =
        (Math.abs(b.pose[joint.id] - a.pose[joint.id]) /
          ((b.time_ms - a.time_ms) / 1000)) *
        (motion.interpolation === "smooth" ? 1.5 : 1);
      if (slope > 0)
        scale = Math.min(scale, Math.min(speed, joint.max_speed_dps) / slope);
    }
  }
  return scale;
}

export function mountLivePanel({
  getPose,
  getMotion,
  getJoint,
  onUpdate,
  onPlayback,
  notify,
  signal,
}) {
  const sidebar = document.querySelector(".library");
  const panel = document.createElement("section");
  panel.id = "live-panel";
  panel.className = "live-panel";
  panel.hidden = true;
  panel.innerHTML = `
    <div class="live-heading"><span class="eyebrow">РЕАЛЬНЫЙ РОБОТ</span><h2>Live-настройка</h2><p>Проверяйте движение на роботе и наблюдайте его опору.</p></div>
    <div class="live-connection-state"><span id="live-dot"></span><strong id="live-state">Не подключён</strong></div>
    <p id="live-reason" class="live-reason">Подключите робота с поддержкой Live.</p>
    <label class="live-field">Адрес в домашней сети<input id="live-host" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="gosha.local" /></label>
    <button id="live-connect" class="button">Подключить робота</button>
    <div id="live-auth" hidden><label class="live-field">Ключ доступа Live<input id="live-key" type="password" autocomplete="off" placeholder="Выдан при настройке робота" maxlength="128" /></label><button id="live-arm" class="button accent">Открыть сессию</button></div>
    <div class="live-speed"><label>Скорость настройки <output id="live-speed-value">10°/с</output></label><input id="live-speed" type="range" min="1" max="15" step="1" value="10" aria-label="Скорость Live в градусах в секунду" /></div>
    <div class="live-hold-controls"><button id="live-hold" class="button live-hold" disabled>Удерживать → текущая поза</button><button id="live-run" class="button quiet" disabled>Удерживать → всё движение</button><small>Удерживайте пробел, чтобы менять ползунки с движением робота. Отпускание завершает сессию.</small></div>
    <button id="live-stop" class="button live-stop" disabled>■ СТОП</button>
    <div class="live-readings"><h3>Обратная связь</h3><dl><dt>Подтверждение</dt><dd id="live-latency">—</dd><dt>Команда суставу</dt><dd id="live-commanded">—</dd><dt>Измеренный угол</dt><dd id="live-measured">Нет данных</dd><dt>Наклон корпуса</dt><dd id="live-tilt">Нет данных</dd></dl><p id="live-feedback-note">Датчики и пределы будут проверены при подключении. 3D-модель сама не определяет равновесие.</p></div>
    <div id="live-limits" class="live-limits"></div>
    <p class="live-final-note">Остановка удерживает последнюю команду приводам. Она не выравнивает робота и не заменяет физическое отключение питания.</p>
  `;
  sidebar.prepend(panel);
  const toggle = document.createElement("div");
  toggle.className = "mode-switch";
  toggle.setAttribute("aria-label", "Режим редактора");
  toggle.innerHTML =
    '<button id="mode-preview" aria-pressed="true">3D</button><button id="mode-live" aria-pressed="false"><span></span>Live</button>';
  document.querySelector(".topbar .version").after(toggle);
  const byId = (id) => document.getElementById(id);
  let enabled = false;
  let pendingMode = null;
  let running = false;
  let playbackScale = 1;
  let connectionState;
  const abort = new AbortController();
  signal.addEventListener(
    "abort",
    () => {
      abort.abort();
      session.disconnect();
      clearInterval(interval);
    },
    { once: true },
  );
  const guarded = (action) => {
    try {
      return action();
    } catch (e) {
      session.release("Ошибка настройки.");
      notify(e.message, true);
    }
  };
  const states = {
    disconnected: "Не подключён",
    connecting: "Проверка связи",
    ready: "Готов к сессии",
    arming: "Проверка доступа",
    armed: "Сессия открыта",
    stopping: "Останавливаем",
    fault: "Live недоступен",
  };
  function render(state) {
    connectionState = state;
    if (pendingMode !== null && state.state === "disconnected") {
      applyMode(pendingMode);
      pendingMode = null;
    } else if (state.state === "fault") {
      pendingMode = null;
    }
    if (state.state !== "armed" || !state.holding) {
      if (running) {
        running = false;
        onPlayback(false);
      }
    }
    byId("live-state").textContent = states[state.state];
    byId("live-dot").className = ["ready", "armed"].includes(state.state)
      ? "connected"
      : state.state === "fault"
        ? "fault"
        : "";
    byId("live-reason").textContent = state.reason;
    byId("live-auth").hidden = !["ready", "arming"].includes(state.state);
    byId("live-arm").disabled = state.state !== "ready";
    byId("live-host").disabled = !["disconnected", "fault"].includes(
      state.state,
    );
    byId("live-connect").textContent = ["disconnected", "fault"].includes(
      state.state,
    )
      ? "Подключить робота"
      : "Отключить";
    byId("live-connect").disabled = state.state === "stopping";
    byId("live-hold").disabled = state.state !== "armed";
    byId("live-run").disabled = state.state !== "armed";
    byId("live-hold").classList.toggle("holding", state.holding);
    byId("live-run").classList.toggle("holding", state.holding && running);
    byId("live-stop").disabled = !["armed", "arming", "stopping"].includes(
      state.state,
    );
    byId("live-speed-value").textContent = `${state.speed}°/с`;
    byId("live-latency").textContent =
      state.rtt === null ? "—" : `${state.rtt} мс`;
    const joint = getJoint();
    const telemetry = ["disconnected", "fault"].includes(state.state)
      ? null
      : state.telemetry;
    byId("live-commanded").textContent = telemetry
      ? `${telemetry.commanded_pose[joint].toFixed(1)}°`
      : "—";
    byId("live-measured").textContent = telemetry?.measured_pose
      ? `${telemetry.measured_pose[joint].toFixed(1)}°`
      : state.caps && !state.caps.feedback.measured_position
        ? "Не измеряется"
        : "Нет данных";
    byId("live-tilt").textContent = telemetry?.tilt
      ? `${telemetry.tilt.roll.toFixed(1)}° / ${telemetry.tilt.pitch.toFixed(1)}°`
      : state.caps && !state.caps.feedback.imu
        ? "Не измеряется"
        : "Нет данных";
    byId("live-feedback-note").textContent = state.caps
      ? "Команда приводу не подтверждает его фактический угол. Отсутствующие измерения не заменяются расчётом."
      : "Датчики и пределы будут проверены при подключении. 3D-модель сама не определяет равновесие.";
    byId("live-limits").replaceChildren();
    if (state.caps) {
      const label = document.createElement("h3");
      label.textContent = "Проверенные пределы";
      byId("live-limits").append(label);
      for (const j of PROFILE.joints) {
        const limit = state.caps.joint_limits.find((l) => l.id === j.id);
        const row = document.createElement("p");
        row.textContent = limit
          ? `${j.label}: ${limit.min}…${limit.max}°`
          : `${j.label}: недоступен`;
        byId("live-limits").append(row);
      }
    }
    byId("live-key").disabled = state.state === "arming";
    const badge = document.querySelector(".preview-badge");
    badge.replaceChildren();
    const dot = document.createElement("span");
    badge.append(
      dot,
      enabled
        ? state.state === "armed"
          ? "Live · команда приводу"
          : state.state === "stopping"
            ? "Live · ждём остановку"
            : state.state === "fault"
              ? "Live · нет подтверждения"
              : "Live · ожидание сессии"
        : "3D-предпросмотр",
    );
    const status = document.querySelector(".statusbar > span:first-child");
    status.textContent = enabled
      ? `Live · ${states[state.state]}`
      : "На компьютере · робот не подключён";
    onUpdate();
  }
  const session = new LiveSession({ onChange: render });
  function applyMode(value) {
    enabled = value;
    running = false;
    onPlayback(false);
    panel.hidden = !value;
    sidebar.classList.toggle("show-live", value);
    byId("mode-live").setAttribute("aria-pressed", value);
    byId("mode-preview").setAttribute("aria-pressed", !value);
  }
  function mode(value) {
    if (value === enabled) return;
    pendingMode = value;
    session.requestDisconnect();
  }
  function start(run = false) {
    guarded(() => {
      if (run)
        playbackScale = livePlaybackScale(
          getMotion(),
          session.caps,
          session.speed,
        );
      session.hold(getPose());
      running = run;
      if (run) onPlayback(true);
      render(session.snapshot());
    });
  }
  function release(reason) {
    if (session.state === "arming") session.disconnect();
    else session.release(reason);
    running = false;
    onPlayback(false);
  }
  byId("mode-preview").onclick = () => mode(false);
  byId("mode-live").onclick = () => mode(true);
  byId("live-connect").onclick = () =>
    guarded(() => {
      if (!["disconnected", "fault"].includes(session.state))
        session.requestDisconnect();
      else session.connect(byId("live-host").value);
    });
  byId("live-arm").onclick = () =>
    guarded(() => session.arm(byId("live-key").value));
  byId("live-speed").oninput = () =>
    guarded(() => {
      session.setSpeed(Number(byId("live-speed").value));
      if (running)
        playbackScale = livePlaybackScale(
          getMotion(),
          session.caps,
          session.speed,
        );
    });
  for (const [id, run] of [
    ["live-hold", false],
    ["live-run", true],
  ]) {
    const button = byId(id);
    button.onpointerdown = (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      button.setPointerCapture(e.pointerId);
      start(run);
    };
    button.onpointerup = () => release("Кнопка отпущена.");
    button.onpointercancel = () => release("Удержание отменено.");
    button.onlostpointercapture = () => {
      if (session.holding) release("Удержание завершено.");
    };
    button.onkeydown = (e) => {
      if (e.code === "Enter" && !e.repeat) {
        e.preventDefault();
        start(run);
      }
    };
    button.onkeyup = (e) => {
      if (e.code === "Enter") {
        e.preventDefault();
        release("Кнопка отпущена.");
      }
    };
  }
  byId("live-stop").onclick = () => {
    running = false;
    onPlayback(false);
    if (session.state === "arming") session.disconnect();
    else session.stop("СТОП.");
  };
  document.addEventListener(
    "visibilitychange",
    () => {
      if (document.hidden) {
        session.requestDisconnect();
        onPlayback(false);
      }
    },
    { signal: abort.signal },
  );
  window.addEventListener(
    "blur",
    () => {
      if (session.state === "arming") session.disconnect();
      else release("Окно потеряло фокус.");
    },
    { signal: abort.signal },
  );
  window.addEventListener("pagehide", () => session.disconnect(), {
    signal: abort.signal,
  });
  document.addEventListener(
    "keyup",
    (e) => {
      if (enabled && e.code === "Space") {
        e.preventDefault();
        release("Пробел отпущен.");
      }
    },
    { signal: abort.signal },
  );
  const interval = setInterval(() => session.tick(), 25);
  render(session.snapshot());
  return {
    get enabled() {
      return enabled;
    },
    get running() {
      return running;
    },
    get holding() {
      return session.holding;
    },
    get playbackScale() {
      return playbackScale;
    },
    endTimeline() {
      running = false;
      onPlayback(false);
      if (session.state === "armed" && session.holding)
        session.change(
          "armed",
          "Шкала завершена. Удерживайте до нужной позы; отпустите для остановки.",
        );
    },
    get snapshot() {
      return session.snapshot();
    },
    keydown(e) {
      if (!enabled || !["Space", "Escape"].includes(e.code)) return false;
      if (e.code === "Escape") {
        release("Остановка клавишей Esc.");
        return true;
      }
      if (document.querySelector("dialog[open]") || e.target.isContentEditable)
        return false;
      if (
        ["TEXTAREA", "SELECT"].includes(e.target.tagName) ||
        (e.target.tagName === "INPUT" && e.target.type !== "range")
      )
        return false;
      e.preventDefault();
      if (!e.repeat) start(false);
      return true;
    },
    updateTarget() {
      if (session.holding) guarded(() => session.setTarget(getPose()));
    },
    stop: (reason) => release(reason),
    displayPose(fallback) {
      return enabled &&
        ["ready", "armed", "stopping"].includes(session.state) &&
        session.lastTelemetry
        ? (session.lastTelemetry.measured_pose ??
            session.lastTelemetry.commanded_pose)
        : fallback;
    },
    refresh() {
      if (connectionState) render(connectionState);
    },
  };
}
