import { PROFILE, validatePose } from "./motion.js";

export const LIVE_PROTOCOL = "gosha.motion.live.v1";
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

export function robotSocketUrl(host) {
  host = host.trim().toLowerCase();
  const ipv4 = host.split(".");
  const privateIp =
    ipv4.length === 4 &&
    ipv4.every((p) => /^\d{1,3}$/.test(p) && Number(p) <= 255) &&
    (Number(ipv4[0]) === 10 ||
      (Number(ipv4[0]) === 192 && Number(ipv4[1]) === 168) ||
      (Number(ipv4[0]) === 172 &&
        Number(ipv4[1]) >= 16 &&
        Number(ipv4[1]) <= 31));
  const localName =
    /^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.local$/.test(host);
  assert(
    privateIp || localName,
    "Введите локальный адрес робота: например, gosha.local или адрес в домашней сети.",
  );
  return `ws://${host}:8080/ws`;
}

export function validateCapabilities(data) {
  assert(
    data && data.protocol === LIVE_PROTOCOL && data.op === "capabilities",
    "Прошивка не поддерживает протокол Live.",
  );
  assert(
    data.motion_allowed === true,
    data.reason === "no_motion_profile"
      ? "В прошивке включён режим без движений."
      : "Робот не разрешил живое управление.",
  );
  assert(
    data.profile_id === PROFILE.id &&
      data.calibrated === true &&
      /^[a-f0-9]{64}$/.test(data.calibration_id ?? ""),
    "Нужен проверенный профиль приводов, совпадающий с этой моделью.",
  );
  assert(
    data.stop_mode === "hold_setpoint" &&
      Number.isInteger(data.watchdog_ms) &&
      data.watchdog_ms >= 100 &&
      data.watchdog_ms <= 500,
    "Прошивка должна поддерживать остановку и контроль потери связи.",
  );
  assert(
    Number.isInteger(data.max_rate_hz) &&
      data.max_rate_hz >= 5 &&
      data.max_rate_hz <= 20,
    "Неподдерживаемая частота команд Live.",
  );
  assert(
    1000 / data.max_rate_hz <= data.watchdog_ms / 2,
    "Интервал команд несовместим с контролем потери связи.",
  );
  assert(
    data.auth_required === true,
    "Управление требует защищённой сессии с ключом доступа.",
  );
  assert(
    Array.isArray(data.joint_limits) &&
      data.joint_limits.length > 0 &&
      data.joint_limits.length <= PROFILE.joints.length,
    "Робот не сообщил доступные приводы.",
  );
  const ids = new Set();
  const joint_limits = data.joint_limits.map((limit) => {
    const joint = PROFILE.joints.find((j) => j.id === limit?.id);
    assert(
      joint && !ids.has(limit.id),
      "Неизвестный или повторяющийся привод.",
    );
    ids.add(limit.id);
    assert(
      finite(limit.min) &&
        finite(limit.max) &&
        limit.min < limit.max &&
        limit.min >= joint.min &&
        limit.max <= joint.max,
      "Пределы приводов не совпадают с профилем модели.",
    );
    assert(
      finite(limit.max_speed_dps) &&
        limit.max_speed_dps > 0 &&
        limit.max_speed_dps <= 30,
      "Нужен ограниченный режим скорости приводов.",
    );
    return {
      id: limit.id,
      min: limit.min,
      max: limit.max,
      max_speed_dps: limit.max_speed_dps,
    };
  });
  const commanded_pose = validatePose(data.commanded_pose);
  for (const limit of joint_limits)
    assert(
      commanded_pose[limit.id] >= limit.min &&
        commanded_pose[limit.id] <= limit.max,
      "Начальная команда привода вне проверенных пределов.",
    );
  return {
    profile_id: PROFILE.id,
    calibration_id: data.calibration_id,
    watchdog_ms: data.watchdog_ms,
    max_rate_hz: data.max_rate_hz,
    joint_limits,
    commanded_pose,
    feedback: {
      measured_position: data.feedback?.measured_position === true,
      imu: data.feedback?.imu === true,
    },
  };
}

// Only this class can emit a live command. Playback/rendering alone never send.
// The robot must enforce limits, ownership and the watchdog independently.
export class LiveSession {
  constructor({
    socketFactory = (url) => new WebSocket(url),
    now = () => performance.now(),
    makeId = () => crypto.randomUUID(),
    onChange = () => {},
    onTelemetry = () => {},
  } = {}) {
    Object.assign(this, { socketFactory, now, makeId, onChange, onTelemetry });
    this.generation = 0;
    this.state = "disconnected";
    this.reason = "";
    this.caps = null;
    this.socket = null;
    this.holding = false;
    this.sessionId = null;
    this.pending = null;
    this.lastTelemetry = null;
    this.speed = 10;
    this.target = null;
    this.seq = 0;
    this.lastSentAt = 0;
    this.rtt = null;
    this.closeAfterStop = false;
  }
  snapshot() {
    return {
      state: this.state,
      reason: this.reason,
      caps: this.caps,
      holding: this.holding,
      rtt: this.rtt,
      telemetry: this.lastTelemetry,
      speed: this.speed,
      pending: Boolean(this.pending),
    };
  }
  change(state, reason = "") {
    this.state = state;
    this.reason = reason;
    this.onChange(this.snapshot());
  }
  send(message) {
    assert(this.socket?.readyState === 1, "Соединение с роботом потеряно.");
    assert(this.socket.bufferedAmount < 4096, "Канал управления перегружен.");
    this.socket.send(JSON.stringify({ protocol: LIVE_PROTOCOL, ...message }));
  }
  connect(host) {
    const url = robotSocketUrl(host);
    this.disconnect();
    const generation = ++this.generation;
    this.caps = null;
    this.lastTelemetry = null;
    this.rtt = null;
    this.requestId = this.makeId();
    this.deadline = this.now() + 3000;
    this.change("connecting", "Проверяем поддержку Live…");
    let socket;
    try {
      socket = this.socketFactory(url);
    } catch {
      this.fail("Браузер не разрешил подключение к роботу.");
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      if (generation !== this.generation) return;
      try {
        this.send({ op: "hello", request_id: this.requestId });
      } catch (e) {
        this.fail(e.message);
      }
    };
    socket.onmessage = (event) => {
      if (generation !== this.generation) return;
      try {
        assert(
          typeof event.data === "string" && event.data.length <= 16384,
          "Некорректный ответ робота.",
        );
        const data = JSON.parse(event.data);
        if (data.protocol !== LIVE_PROTOCOL) return;
        this.receive(data);
      } catch (e) {
        this.fail(e.message);
      }
    };
    socket.onerror = () => {
      if (generation === this.generation)
        this.fail(
          "Не удалось соединиться. Проверьте адрес, Wi-Fi и поддержку Live в прошивке.",
        );
    };
    socket.onclose = () => {
      if (generation === this.generation)
        this.fail(
          "Связь с роботом потеряна. Повторное движение требует новой сессии.",
        );
    };
  }
  receive(data) {
    if (
      this.state === "connecting" &&
      data.op === "capabilities" &&
      data.request_id === this.requestId
    ) {
      this.caps = validateCapabilities(data);
      this.deadline = null;
      this.lastTelemetry = {
        commanded_pose: this.caps.commanded_pose,
        measured_pose: null,
        tilt: null,
      };
      this.change("ready", "Робот совместим. Для движения откройте сессию.");
      return;
    }
    if (
      this.state === "arming" &&
      data.op === "armed" &&
      data.request_id === this.requestId
    ) {
      assert(
        typeof data.session_id === "string" &&
          /^[a-zA-Z0-9_-]{16,128}$/.test(data.session_id),
        "Робот не выдал корректную сессию.",
      );
      assert(
        data.calibration_id === this.caps.calibration_id,
        "Профиль робота изменился. Подключитесь заново.",
      );
      this.sessionId = data.session_id;
      this.deadline = null;
      this.seq = 0;
      this.pending = null;
      this.lastSentAt = this.now();
      this.lastReplyAt = this.now();
      this.change("armed", "Сессия открыта. Удерживайте разрешение движения.");
      return;
    }
    if (
      data.op === "error" &&
      (data.request_id === this.requestId || data.session_id === this.sessionId)
    ) {
      this.fail(
        data.code === "auth_failed"
          ? "Робот отклонил ключ доступа."
          : "Робот отклонил команду. Сессия остановлена.",
      );
      return;
    }
    if (
      this.sessionId &&
      data.session_id === this.sessionId &&
      data.op === "stopped"
    ) {
      this.lastTelemetry = this.validateTelemetry(data);
      this.onTelemetry(this.lastTelemetry);
      this.holding = false;
      this.pending = null;
      this.sessionId = null;
      this.deadline = null;
      if (this.closeAfterStop) {
        this.disconnect("Робот подтвердил остановку. Соединение закрыто.");
        return;
      }
      this.change(
        "ready",
        "Робот подтвердил остановку. Для движения откройте новую сессию.",
      );
      return;
    }
    if (
      data.op === "ack" &&
      this.state === "armed" &&
      data.session_id === this.sessionId
    ) {
      if (!this.pending || data.seq !== this.pending.seq) return;
      this.lastTelemetry = this.validateTelemetry(data);
      this.rtt = Math.max(0, Math.round(this.now() - this.pending.sentAt));
      this.lastReplyAt = this.now();
      this.pending = null;
      this.onTelemetry(this.lastTelemetry);
      this.onChange(this.snapshot());
    }
  }
  validateTelemetry(data) {
    const commanded_pose = validatePose(data.commanded_pose);
    for (const limit of this.caps.joint_limits)
      assert(
        commanded_pose[limit.id] >= limit.min &&
          commanded_pose[limit.id] <= limit.max,
        "Робот сообщил команду вне проверенных пределов.",
      );
    let measured_pose = null;
    let tilt = null;
    if (
      this.caps.feedback.measured_position &&
      data.measured_pose !== null &&
      data.measured_pose !== undefined
    )
      measured_pose = validatePose(data.measured_pose);
    if (this.caps.feedback.imu && data.tilt) {
      assert(
        finite(data.tilt.roll) &&
          finite(data.tilt.pitch) &&
          Math.abs(data.tilt.roll) <= 180 &&
          Math.abs(data.tilt.pitch) <= 180,
        "Некорректные показания наклона.",
      );
      tilt = { roll: data.tilt.roll, pitch: data.tilt.pitch };
    }
    return { commanded_pose, measured_pose, tilt };
  }
  arm(key) {
    assert(
      this.state === "ready" && this.caps,
      "Сначала подключите совместимого робота.",
    );
    assert(
      typeof key === "string" && key.length >= 16 && key.length <= 128,
      "Нужен ключ доступа, выданный при настройке Live на роботе.",
    );
    this.requestId = this.makeId();
    this.deadline = this.now() + 2000;
    this.change("arming", "Робот проверяет доступ…");
    try {
      this.send({
        op: "arm",
        request_id: this.requestId,
        calibration_id: this.caps.calibration_id,
        access_key: key,
      });
    } catch (e) {
      this.fail(e.message);
    }
  }
  setSpeed(value) {
    assert(
      finite(value) && value >= 1 && value <= 15,
      "Скорость настройки должна быть от 1 до 15°/с.",
    );
    this.speed = value;
    this.onChange(this.snapshot());
  }
  validateTarget(pose) {
    pose = validatePose(pose);
    for (const limit of this.caps.joint_limits)
      assert(
        pose[limit.id] >= limit.min && pose[limit.id] <= limit.max,
        `${PROFILE.joints.find((j) => j.id === limit.id).label}: цель вне проверенных пределов ${limit.min}…${limit.max}°.`,
      );
    return pose;
  }
  setTarget(pose) {
    assert(this.state === "armed", "Сессия Live не открыта.");
    try {
      this.target = this.validateTarget(pose);
    } catch (e) {
      this.stop(e.message);
      throw e;
    }
  }
  hold(pose) {
    assert(this.state === "armed", "Откройте сессию Live.");
    this.setTarget(pose);
    this.holding = true;
    this.onChange(this.snapshot());
  }
  release(reason = "Разрешение движения отпущено.") {
    this.holding = false;
    if (this.state === "armed") this.stop(reason);
  }
  tick() {
    const now = this.now();
    if (this.deadline && now >= this.deadline) {
      this.fail(
        this.state === "connecting"
          ? "Эта прошивка не ответила на запрос Live. Нужен совместимый модуль на роботе."
          : "Нет подтверждения робота. Сессия закрыта; состояние робота неизвестно.",
      );
      return;
    }
    if (this.state !== "armed") return;
    if (
      this.pending &&
      now - this.pending.sentAt >= Math.min(this.caps.watchdog_ms, 300)
    ) {
      this.fail(
        "Нет подтверждения команд. Сессия закрыта; состояние робота неизвестно.",
      );
      return;
    }
    if (this.pending || now - this.lastSentAt < 1000 / this.caps.max_rate_hz)
      return;
    const seq = ++this.seq;
    const op = this.holding && this.target ? "pose" : "keepalive";
    const message = { op, session_id: this.sessionId, seq };
    if (op === "pose") {
      message.target = Object.fromEntries(
        this.caps.joint_limits.map((j) => [j.id, this.target[j.id]]),
      );
      message.speed_dps = Math.min(
        this.speed,
        ...this.caps.joint_limits.map((joint) => joint.max_speed_dps),
      );
    }
    this.pending = { seq, sentAt: now };
    this.lastSentAt = now;
    try {
      this.send(message);
    } catch (e) {
      this.fail(e.message);
    }
  }
  stop(reason = "Остановка запрошена.") {
    this.holding = false;
    this.target = null;
    this.pending = null;
    if (this.state === "arming") {
      this.disconnect();
      return;
    }
    if (this.state === "stopping") return;
    if (!this.sessionId) return;
    this.deadline = this.now() + 700;
    this.change("stopping", `${reason} Ждём подтверждения.`);
    try {
      this.send({ op: "stop", session_id: this.sessionId, seq: ++this.seq });
    } catch (e) {
      this.fail(e.message);
    }
  }
  fail(reason, emitChange = true) {
    const socket = this.socket;
    const sessionId = this.sessionId;
    this.generation++;
    this.holding = false;
    this.target = null;
    this.pending = null;
    this.sessionId = null;
    this.deadline = null;
    this.socket = null;
    this.closeAfterStop = false;
    if (socket?.readyState === 1 && sessionId) {
      try {
        socket.send(
          JSON.stringify({
            protocol: LIVE_PROTOCOL,
            op: "stop",
            session_id: sessionId,
            seq: ++this.seq,
          }),
        );
      } catch {}
    }
    try {
      socket?.close();
    } catch {}
    if (emitChange) this.change("fault", reason);
  }
  requestDisconnect() {
    if (this.sessionId) {
      this.closeAfterStop = true;
      this.stop("Отключение.");
    } else {
      this.disconnect();
    }
  }
  // Forced teardown is reserved for page disposal and cancelling a pending ARM.
  // User-initiated exits use requestDisconnect and wait for STOP acknowledgement.
  disconnect(reason = "Робот не подключён.") {
    if (this.socket) this.fail("Соединение закрыто.", false);
    this.generation++;
    this.caps = null;
    this.sessionId = null;
    this.deadline = null;
    this.lastTelemetry = null;
    this.rtt = null;
    this.target = null;
    this.pending = null;
    this.holding = false;
    this.closeAfterStop = false;
    this.change("disconnected", reason);
  }
}
