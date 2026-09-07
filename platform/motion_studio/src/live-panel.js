import { LiveSession, RIGHT_ARM_COMMISSIONING_MODE } from "./live.js";
import { PROFILE } from "./motion.js";

const formatDegrees = (value) =>
  `${value > 0 ? "+" : ""}${Number(value.toFixed(1))}°`;
const jointById = (id) => PROFILE.joints.find((joint) => joint.id === id);

export function liveStepSizeForJoint(caps, jointId) {
  if (!caps?.joint_limits.some((limit) => limit.id === jointId))
    throw new Error("Выберите доступный сустав Live.");
  if (caps.mode === RIGHT_ARM_COMMISSIONING_MODE && jointId === "arm_positive_x")
    return 5;
  return 1;
}

export function prepareLiveStepTarget(commandedPose, caps, jointId, direction) {
  const limit = caps?.joint_limits.find((item) => item.id === jointId);
  if (!limit) throw new Error("Выберите доступный сустав Live.");
  if (![1, -1].includes(direction))
    throw new Error("Выберите направление шага.");
  const step = liveStepSizeForJoint(caps, jointId) * direction;
  const target = { ...commandedPose, [jointId]: commandedPose[jointId] + step };
  if (target[jointId] < limit.min || target[jointId] > limit.max)
    throw new Error(
      `${jointById(jointId).label}: выбранный шаг выходит за предел ${limit.min}…${limit.max}°.`,
    );
  return target;
}

export function createLiveStepPlan(commandedPose, caps, jointId, direction) {
  const stepSize = liveStepSizeForJoint(caps, jointId);
  return {
    jointId,
    direction,
    stepSize,
    target: prepareLiveStepTarget(commandedPose, caps, jointId, direction),
  };
}

export function liveStepViewModel(state, selectedJoint, direction, activeStep) {
  const active = state.caps?.joint_limits.map((limit) => limit.id) ?? [];
  const visible =
    Boolean(state.caps) &&
    !state.caps.initialization_required &&
    state.state !== "disconnected" &&
    state.state !== "fault";
  if (!visible || !active.length)
    return { visible, active, holdDisabled: true, locked: false };
  const pose = state.telemetry?.commanded_pose ?? state.caps.commanded_pose;
  const plan =
    activeStep && active.includes(activeStep.jointId) ? activeStep : null;
  const jointId =
    plan?.jointId ?? (active.includes(selectedJoint) ? selectedJoint : active[0]);
  const shownDirection = plan?.direction ?? direction;
  const stepSize = plan?.stepSize ?? liveStepSizeForJoint(state.caps, jointId);
  const locked = Boolean(plan && (state.holding || state.state === "stopping"));
  let target = plan?.target ?? null;
  let targetError = "";
  if (!target) {
    try {
      target = prepareLiveStepTarget(pose, state.caps, jointId, shownDirection);
    } catch (e) {
      targetError = e.message;
    }
  }
  return {
    visible,
    active,
    jointId,
    direction: shownDirection,
    stepSize,
    current: pose[jointId],
    target,
    targetError,
    locked,
    holdDisabled: state.state !== "armed" || !target,
  };
}

export function livePlaybackScale(motion, caps, speed) {
  if (caps?.commissioning)
    throw new Error("При первичной проверке полные движения недоступны. Проверяйте по одному суставу.");
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
  onSelectJoint = () => {},
  onUpdate,
  onPlayback,
  onPose,
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
    <div id="live-auth" hidden><label class="live-field">Ключ доступа Live<input id="live-key" type="password" autocomplete="off" placeholder="Выдан при настройке робота" maxlength="128" /></label><button id="live-init-right-arm" class="button accent" hidden>Включить правую руку</button><button id="live-arm" class="button accent">Открыть сессию</button></div>
    <p id="live-commissioning-note" class="live-reason" hidden>Первичная проверка приводов: один сустав за сессию, только ±1°. Сторона и направление в модели ещё не подтверждены. Поддерживайте корпус и наблюдайте реальный привод.</p>
    <button id="live-copy-pose" class="button quiet" hidden>Взять текущие команды робота</button>
    <div id="live-step" class="live-step" hidden>
      <h3>Пошаговый тест</h3>
      <div id="live-step-joints" class="live-step-joints"></div>
      <dl class="live-step-status">
        <dt>Сустав</dt><dd id="live-step-joint">—</dd>
        <dt>Команда</dt><dd id="live-step-current">—</dd>
        <dt>Цель</dt><dd id="live-step-target">—</dd>
        <dt>Шаг</dt><dd id="live-step-size">—</dd>
      </dl>
      <div class="live-step-actions"><button id="live-step-minus" class="button quiet" aria-pressed="true">−</button><button id="live-step-plus" class="button quiet" aria-pressed="false">+</button><button id="live-step-hold" class="button live-hold" disabled>Удерживать шаг</button></div>
      <small>Подготовка цели не двигает робота и не меняет библиотеку. Движение идёт только пока удерживается кнопка.</small>
    </div>
    <div class="live-speed"><label>Скорость настройки <output id="live-speed-value">10°/с</output></label><input id="live-speed" type="range" min="1" max="15" step="1" value="10" aria-label="Скорость Live в градусах в секунду" /></div>
    <div class="live-hold-controls"><button id="live-hold" class="button live-hold" disabled>Удерживать → текущая поза</button><button id="live-run" class="button quiet" disabled>Удерживать → всё движение</button><small>Удерживайте пробел, чтобы менять ползунки с движением робота. Отпускание завершает сессию.</small></div>
    <button id="live-stop" class="button live-stop" disabled>■ СТОП</button>
    <div class="live-readings"><h3>Обратная связь</h3><dl><dt>Подтверждение</dt><dd id="live-latency">—</dd><dt>Команда суставу</dt><dd id="live-commanded">—</dd><dt>Измеренный угол</dt><dd id="live-measured">Нет данных</dd><dt>Наклон корпуса</dt><dd id="live-tilt">Нет данных</dd></dl><p id="live-feedback-note">Датчики и пределы будут проверены при подключении. 3D-модель сама не определяет равновесие.</p></div>
    <div id="live-limits" class="live-limits"></div>
    <div class="live-log"><h3>Журнал команд</h3><ol id="live-command-log"></ol></div>
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
  let stepDirection = -1;
  let activeStep = null;
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
    init_required: "Нужно включить руку",
    initializing_right_arm: "Включаем руку",
    ready: "Готов к сессии",
    arming: "Проверка доступа",
    armed: "Сессия открыта",
    stopping: "Останавливаем",
    fault: "Live недоступен",
  };
  const isRightArmMode = (state) =>
    state.caps?.mode === RIGHT_ARM_COMMISSIONING_MODE;
  const activeJointIds = (state) =>
    state.caps?.joint_limits.map((limit) => limit.id) ?? [];
  const selectedLiveJoint = (state) => {
    const active = activeJointIds(state);
    const current = getJoint();
    return active.includes(current) ? current : active[0] ?? current;
  };
  const currentCommandedPose = (state) =>
    state.telemetry?.commanded_pose ?? state.caps?.commanded_pose;
  const reusableStepPlan = (state) =>
    activeStep && activeJointIds(state).includes(activeStep.jointId)
      ? activeStep
      : null;
  const buildStepPlan = (state) =>
    reusableStepPlan(state) ??
    createLiveStepPlan(
      currentCommandedPose(state),
      state.caps,
      selectedLiveJoint(state),
      stepDirection,
    );
  function renderStep(state) {
    const step = byId("live-step");
    const view = liveStepViewModel(
      state,
      getJoint(),
      stepDirection,
      activeStep,
    );
    step.hidden = !view.visible;
    byId("live-step-joints").replaceChildren();
    if (!view.visible || !view.active.length) {
      activeStep = null;
      byId("live-step-joint").textContent = "—";
      byId("live-step-current").textContent = "—";
      byId("live-step-target").textContent = "—";
      byId("live-step-size").textContent = "—";
      byId("live-step-hold").disabled = true;
      return;
    }
    const selected = view.jointId;
    for (const id of view.active) {
      const joint = jointById(id);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "text-button";
      button.textContent = joint.short;
      button.dataset.liveJoint = id;
      button.classList.toggle("selected", id === selected);
      button.disabled = view.locked;
      button.onclick = () => {
        if (view.locked) return;
        activeStep = null;
        onSelectJoint(id);
      };
      byId("live-step-joints").append(button);
    }
    const targetText = view.target
      ? formatDegrees(view.target[selected])
      : "Вне предела";
    byId("live-step-joint").textContent = jointById(selected).label;
    byId("live-step-current").textContent = formatDegrees(view.current);
    byId("live-step-target").textContent = targetText;
    byId("live-step-target").title = view.targetError;
    byId("live-step-size").textContent =
      `${view.direction > 0 ? "+" : "−"}${view.stepSize}°`;
    byId("live-step-minus").setAttribute("aria-pressed", view.direction === -1);
    byId("live-step-plus").setAttribute("aria-pressed", view.direction === 1);
    byId("live-step-minus").disabled = view.locked;
    byId("live-step-plus").disabled = view.locked;
    byId("live-step-minus").textContent = `−${view.stepSize}°`;
    byId("live-step-plus").textContent = `+${view.stepSize}°`;
    byId("live-step-hold").disabled = view.holdDisabled;
  }
  function renderCommandLog(state) {
    const list = byId("live-command-log");
    list.replaceChildren();
    for (const entry of state.commandLog ?? []) {
      const item = document.createElement("li");
      item.className = `live-log-${entry.kind}`;
      const op = entry.op === "initialize_right_arm" ? "INIT" : entry.op.toUpperCase();
      item.textContent = `${op}: ${entry.detail}`;
      list.append(item);
    }
    if (!list.children.length) {
      const item = document.createElement("li");
      item.textContent = "Пока нет команд";
      list.append(item);
    }
  }
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
    const keyReady = byId("live-key").value.length >= 16;
    const rightArmInitState = ["init_required", "initializing_right_arm"].includes(
      state.state,
    );
    byId("live-auth").hidden = ![
      "init_required",
      "initializing_right_arm",
      "ready",
      "arming",
    ].includes(state.state);
    byId("live-init-right-arm").hidden = !(
      rightArmInitState && keyReady
    );
    byId("live-init-right-arm").disabled =
      state.state !== "init_required" || !keyReady;
    byId("live-arm").hidden = rightArmInitState;
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
    byId("live-hold").textContent = isRightArmMode(state)
      ? "Текущая поза недоступна"
      : "Удерживать → текущая поза";
    byId("live-hold").disabled = state.state !== "armed" || isRightArmMode(state);
    byId("live-run").disabled = state.state !== "armed" || Boolean(state.caps?.commissioning);
    byId("live-commissioning-note").hidden = !state.caps?.commissioning;
    byId("live-commissioning-note").textContent = isRightArmMode(state)
      ? "Проверка правой руки: перед первым тестом рука включается отдельной кнопкой. Направление модели ещё не подтверждено: отрицательный шаг соответствует старому примеру приветствия, но физику проверяет оператор."
      : "Первичная проверка приводов: один сустав за сессию, только ±1°. Сторона и направление в модели ещё не подтверждены. Поддерживайте корпус и наблюдайте реальный привод.";
    byId("live-copy-pose").hidden =
      !state.caps?.commissioning || state.caps.initialization_required;
    byId("live-copy-pose").disabled =
      !["ready", "armed"].includes(state.state) || state.holding;
    byId("live-hold").classList.toggle("holding", state.holding);
    byId("live-run").classList.toggle("holding", state.holding && running);
    byId("live-step-hold").classList.toggle("holding", state.holding);
    byId("live-stop").disabled = ![
      "armed",
      "arming",
      "initializing_right_arm",
      "stopping",
    ].includes(state.state);
    byId("live-speed-value").textContent = `${state.speed}°/с`;
    byId("live-speed").value = state.speed;
    byId("live-speed").disabled = Boolean(state.caps?.commissioning);
    byId("live-latency").textContent =
      state.rtt === null ? "—" : `${state.rtt} мс`;
    const joint = getJoint();
    const telemetry = ["disconnected", "fault"].includes(state.state)
      ? null
      : state.telemetry;
    const initializationPlaceholder =
      state.caps?.initialization_required && joint === "arm_positive_x";
    byId("live-commanded").textContent = telemetry
      ? initializationPlaceholder
        ? "Нет команды / не включена"
        : state.caps?.joint_limits.some((limit) => limit.id === joint)
          ? `${telemetry.commanded_pose[joint].toFixed(1)}°`
          : "Недоступен"
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
      label.textContent = isRightArmMode(state)
        ? "Ограничения правой руки"
        : state.caps.commissioning
          ? "Ограничения первого теста"
          : "Проверенные пределы";
      byId("live-limits").append(label);
      for (const j of PROFILE.joints) {
        const limit = state.caps.joint_limits.find((l) => l.id === j.id);
        const row = document.createElement("p");
        const note =
          isRightArmMode(state) && j.id === "arm_positive_x"
            ? " · GPIO12, нейтраль 135°, первый шаг −5°"
            : "";
        row.textContent = limit
          ? `${j.label}: ${limit.min}…${limit.max}° · до ${limit.max_speed_dps}°/с${note}`
          : `${j.label}: недоступен`;
        byId("live-limits").append(row);
      }
    }
    const initializing = ["arming", "initializing_right_arm"].includes(state.state);
    byId("live-key").disabled = initializing;
    renderStep(state);
    renderCommandLog(state);
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
              : state.state === "init_required"
                ? "Live · включение руки"
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
      if (!run && isRightArmMode(session.snapshot()))
        throw new Error(
          "В режиме правой руки используйте только пошаговый тест.",
        );
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
    if (["arming", "initializing_right_arm"].includes(session.state))
      session.requestDisconnect();
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
  byId("live-init-right-arm").onclick = () =>
    guarded(() => session.initializeRightArm(byId("live-key").value));
  byId("live-key").oninput = () => render(session.snapshot());
  byId("live-copy-pose").onclick = () => guarded(() => {
    if (!session.lastTelemetry || session.holding) return;
    onPose(session.lastTelemetry.commanded_pose);
  });
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
  for (const [id, direction] of [
    ["live-step-minus", -1],
    ["live-step-plus", 1],
  ]) {
    byId(id).onclick = () => {
      const view = liveStepViewModel(
        session.snapshot(),
        getJoint(),
        stepDirection,
        activeStep,
      );
      if (view.locked) return;
      activeStep = null;
      stepDirection = direction;
      render(session.snapshot());
    };
  }
  const stepHold = byId("live-step-hold");
  const startStep = () =>
    guarded(() => {
      const plan = buildStepPlan(session.snapshot());
      activeStep = plan;
      try {
        session.hold(plan.target);
      } catch (e) {
        activeStep = null;
        throw e;
      }
      running = false;
      onPlayback(false);
      render(session.snapshot());
    });
  stepHold.onpointerdown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    stepHold.setPointerCapture(e.pointerId);
    startStep();
  };
  stepHold.onpointerup = () => release("Пошаговая кнопка отпущена.");
  stepHold.onpointercancel = () => release("Пошаговое удержание отменено.");
  stepHold.onlostpointercapture = () => {
    if (session.holding) release("Пошаговое удержание завершено.");
  };
  stepHold.onkeydown = (e) => {
    if (e.code === "Enter" && !e.repeat) {
      e.preventDefault();
      startStep();
    }
  };
  stepHold.onkeyup = (e) => {
    if (e.code === "Enter") {
      e.preventDefault();
      release("Пошаговая кнопка отпущена.");
    }
  };
  byId("live-stop").onclick = () => {
    running = false;
    onPlayback(false);
    if (["arming", "initializing_right_arm"].includes(session.state))
      session.requestDisconnect();
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
      if (["arming", "initializing_right_arm"].includes(session.state))
        session.requestDisconnect();
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
      if (!e.repeat && !isRightArmMode(session.snapshot())) start(false);
      return true;
    },
    updateTarget() {
      if (session.holding && !isRightArmMode(session.snapshot()))
        guarded(() => session.setTarget(getPose()));
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
