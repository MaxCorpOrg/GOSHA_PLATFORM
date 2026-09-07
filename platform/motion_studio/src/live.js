import { PROFILE, validatePose } from "./motion.js";

export const LIVE_PROTOCOL = "gosha.motion.live.v1";
export const USB_BRIDGE_HTTP_URL = "http://127.0.0.1:5177";
export const USB_BRIDGE_WS_URL = "ws://127.0.0.1:5177/live";
export const RIGHT_ARM_INITIALIZATION_REASON =
  "right_arm_initialization_required";
export const RIGHT_ARM_COMMISSIONING_MODE = "commissioning_right_arm";
const LEG_COMMISSIONING_MODE = "commissioning";
const LEG_COMMISSIONING_JOINTS = Object.freeze([
  "leg_negative_x",
  "leg_positive_x",
  "foot_negative_x",
  "foot_positive_x",
]);
const RIGHT_ARM_COMMISSIONING_JOINTS = Object.freeze([
  "arm_positive_x",
  ...LEG_COMMISSIONING_JOINTS,
]);
const COMMAND_LOG_LIMIT = 16;
const USB_PORT_ID_PATTERN = /^[a-f0-9]{24}$/;
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};
const jointById = (id) => PROFILE.joints.find((joint) => joint.id === id);
const formatDegrees = (value) =>
  `${value > 0 ? "+" : ""}${Number(value.toFixed(2))}°`;

function commissioningJointIds(mode) {
  if (mode === RIGHT_ARM_COMMISSIONING_MODE) return RIGHT_ARM_COMMISSIONING_JOINTS;
  if (mode === LEG_COMMISSIONING_MODE) return LEG_COMMISSIONING_JOINTS;
  return [];
}

function commissioningDeltaLimit(mode, jointId) {
  if (mode === RIGHT_ARM_COMMISSIONING_MODE && jointId === "arm_positive_x")
    return 5;
  if (mode === RIGHT_ARM_COMMISSIONING_MODE) return 1;
  if (mode === LEG_COMMISSIONING_MODE) return 1;
  return 0;
}

function validateCommissioningLimits(mode, joint_limits, watchdog_ms) {
  const allowed = commissioningJointIds(mode);
  assert(
    watchdog_ms === 300 &&
      joint_limits.length === allowed.length &&
      joint_limits.every((limit) => {
        const delta = commissioningDeltaLimit(mode, limit.id);
        return (
          allowed.includes(limit.id) &&
          limit.min === -delta &&
          limit.max === delta &&
          limit.max_speed_dps === 1
        );
      }),
    mode === RIGHT_ARM_COMMISSIONING_MODE
      ? "Проверка правой руки допускает только правую руку ±5°, ноги и стопы ±1°, скорость 1°/с."
      : "Первичная проверка допускает только ноги и стопы, ±1° и скорость 1°/с.",
  );
}

function summarizeMessage(message) {
  if (message.op === "pose")
    return summarizePose(message.target, Object.keys(message.target ?? {}));
  if (message.op === "stop") return "запрошена остановка";
  if (message.op === "arm") return "запрошена сессия";
  if (message.op === "initialize_right_arm")
    return "запрошено включение правой руки";
  if (message.op === "keepalive") return `seq ${message.seq}`;
  if (message.op === "hello") return "проверка протокола";
  return message.op;
}

function summarizePose(pose, ids) {
  return ids
    .filter((id) => Object.hasOwn(pose ?? {}, id))
    .map((id) => `${jointById(id)?.short ?? id} ${formatDegrees(pose[id])}`)
    .join(", ");
}

function summarizeIncoming(data, caps) {
  if (data.op === "capabilities" && data.mode === RIGHT_ARM_COMMISSIONING_MODE)
    return data.right_arm_initialized
      ? "правая рука включена, движение ждёт сессию"
      : "правая рука ждёт явного включения";
  if (data.op === "capabilities") return "приняты ограничения робота";
  if (data.op === "ack")
    return summarizePose(
      data.commanded_pose,
      caps?.joint_limits.map((limit) => limit.id) ?? [],
    );
  if (data.op === "stopped") return "робот подтвердил остановку";
  if (data.op === "armed") return "сессия подтверждена";
  if (data.op === "error") return data.code ?? "ошибка робота";
  return data.op;
}

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
  // Normalize decimal octets before URL parsing (which otherwise accepts octal).
  return `ws://${privateIp ? ipv4.map(Number).join(".") : host}:8080/ws`;
}

export function usbBridgeSocketUrl(portId) {
  assert(
    typeof portId === "string" && USB_PORT_ID_PATTERN.test(portId),
    "Выберите USB-порт из списка найденных устройств.",
  );
  const url = new URL(USB_BRIDGE_WS_URL);
  url.searchParams.set("port_id", portId);
  return url.toString();
}

function normalizeUsbBridgePort(port) {
  assert(
    port &&
      typeof port === "object" &&
      !Object.hasOwn(port, "path") &&
      !Object.hasOwn(port, "device") &&
      !Object.hasOwn(port, "raw") &&
      typeof port.port_id === "string" &&
      USB_PORT_ID_PATTERN.test(port.port_id) &&
      typeof port.label === "string" &&
      port.label.length >= 3 &&
      port.label.length <= 120 &&
      !port.label.includes("/") &&
      port.vid === "303a" &&
      port.pid === "1001" &&
      (port.busy === undefined || typeof port.busy === "boolean"),
    "Локальная служба USB вернула неподдерживаемое устройство.",
  );
  return {
    port_id: port.port_id,
    label: port.label,
    vid: port.vid,
    pid: port.pid,
    busy: port.busy === true,
  };
}

export async function listUsbBridgePorts({
  fetchImpl = fetch,
  bridgeUrl = USB_BRIDGE_HTTP_URL,
  timeoutMs = 1200,
} = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${bridgeUrl}/ports`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response?.ok)
      throw new Error(
        "Локальная служба USB недоступна. Запустите редактор через bash start.sh.",
      );
    const data = await response.json();
    assert(
      data?.protocol === LIVE_PROTOCOL && Array.isArray(data.ports),
      "Локальная служба USB вернула неожиданный ответ.",
    );
    return data.ports.map(normalizeUsbBridgePort);
  } catch (e) {
    if (controller.signal.aborted)
      throw new Error(
        "Локальная служба USB недоступна. Запустите редактор через bash start.sh.",
      );
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function bridgeErrorMessage(code) {
  const messages = {
    usb_device_absent:
      "USB-устройство 303a:1001 не найдено. Обновите список и проверьте кабель.",
    usb_port_busy:
      "USB-порт уже занят другой программой или сессией Live.",
    firmware_nohello:
      "USB-порт открыт, но прошивка не ответила на Live hello.",
    usb_bridge_request_too_large:
      "Служба USB отклонила слишком большой запрос.",
    usb_bridge_queue_full:
      "Служба USB остановила переполненную очередь команд.",
    usb_bridge_queue_stale:
      "Служба USB закрыла устаревшую очередь команд. Повторное движение требует новой сессии.",
    usb_bridge_invalid_request:
      "Служба USB получила некорректный Live-запрос.",
    usb_bridge_invalid_response:
      "Служба USB получила некорректный ответ прошивки.",
    usb_bridge_response_too_large:
      "Служба USB остановила слишком большой ответ прошивки.",
    serial_error:
      "USB-соединение с устройством прервано. Повторное движение требует новой сессии.",
    usb_serial_safety:
      "Служба USB не смогла настроить порт без управляющих сигналов перезапуска.",
  };
  return messages[code] ?? null;
}

export function validateCapabilities(data) {
  assert(
    data && data.protocol === LIVE_PROTOCOL && data.op === "capabilities",
    "Прошивка не поддерживает протокол Live.",
  );
  const mode = data.mode ?? "verified";
  const commissioning = data.commissioning === true;
  const rightArmMode = mode === RIGHT_ARM_COMMISSIONING_MODE;
  const initializationRequired =
    rightArmMode &&
    data.right_arm_initialized === false &&
    data.motion_allowed === false &&
    data.reason === RIGHT_ARM_INITIALIZATION_REASON;
  assert(
    data.motion_allowed === true || initializationRequired,
    data.reason === "no_motion_profile"
      ? "В прошивке включён режим без движений."
      : "Робот не разрешил живое управление.",
  );
  assert(
    (data.commissioning === undefined || typeof data.commissioning === "boolean") &&
      (commissioning
        ? mode === LEG_COMMISSIONING_MODE || rightArmMode
        : mode === "verified") &&
      (!rightArmMode || typeof data.right_arm_initialized === "boolean") &&
      (!rightArmMode ||
        data.right_arm_initialized === (data.motion_allowed === true)) &&
    data.profile_id === PROFILE.id &&
      data.calibrated === !commissioning &&
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
  if (commissioning) {
    validateCommissioningLimits(mode, joint_limits, data.watchdog_ms);
  }
  for (const limit of joint_limits)
    assert(
      commanded_pose[limit.id] >= limit.min &&
        commanded_pose[limit.id] <= limit.max,
      "Начальная команда привода вне проверенных пределов.",
    );
  return {
    profile_id: PROFILE.id,
    mode,
    calibration_id: data.calibration_id,
    calibrated: !commissioning,
    commissioning,
    initialization_required: initializationRequired,
    right_arm_initialized: rightArmMode
      ? data.right_arm_initialized === true
      : undefined,
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
    this.commandLog = [];
    this.transport = "wifi";
    this.socketErrorReason =
      "Не удалось соединиться. Проверьте адрес, Wi-Fi и поддержку Live в прошивке.";
    this.socketCloseReason =
      "Связь с роботом потеряна. Повторное движение требует новой сессии.";
  }
  snapshot() {
    return {
      state: this.state,
      reason: this.reason,
      transport: this.transport,
      caps: this.caps,
      holding: this.holding,
      rtt: this.rtt,
      telemetry: this.lastTelemetry,
      speed: this.speed,
      pending: Boolean(this.pending),
      commandLog: this.commandLog.map((entry) => ({ ...entry })),
    };
  }
  appendLog(kind, op, detail = "") {
    this.commandLog = this.commandLog
      .concat({
        at: Math.round(this.now()),
        kind,
        op,
        detail,
      })
      .slice(-COMMAND_LOG_LIMIT);
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
    this.appendLog("sent", message.op, summarizeMessage(message));
    this.onChange(this.snapshot());
  }
  connect(host) {
    this.connectUrl(robotSocketUrl(host), {
      transport: "wifi",
      connectingReason: "Проверяем поддержку Live…",
      openErrorReason: "Браузер не разрешил подключение к роботу.",
      socketErrorReason:
        "Не удалось соединиться. Проверьте адрес, Wi-Fi и поддержку Live в прошивке.",
      socketCloseReason:
        "Связь с роботом потеряна. Повторное движение требует новой сессии.",
    });
  }
  connectUsb(portId) {
    this.connectUrl(usbBridgeSocketUrl(portId), {
      transport: "usb",
      connectingReason: "Проверяем USB Live через локальную службу…",
      openErrorReason: "Браузер не смог открыть локальную службу USB.",
      socketErrorReason:
        "Локальная служба USB недоступна. Запустите редактор через bash start.sh.",
      socketCloseReason:
        "USB Live закрыт. Повторное движение требует новой сессии.",
    });
  }
  connectUrl(url, {
    transport = "wifi",
    connectingReason = "Проверяем поддержку Live…",
    openErrorReason = "Браузер не разрешил подключение к роботу.",
    socketErrorReason = this.socketErrorReason,
    socketCloseReason = this.socketCloseReason,
  } = {}) {
    this.disconnect();
    const generation = ++this.generation;
    this.transport = transport;
    this.socketErrorReason = socketErrorReason;
    this.socketCloseReason = socketCloseReason;
    this.caps = null;
    this.lastTelemetry = null;
    this.rtt = null;
    this.commandLog = [];
    this.requestId = this.makeId();
    this.deadline = this.now() + 3000;
    this.change("connecting", connectingReason);
    let socket;
    try {
      socket = this.socketFactory(url);
    } catch {
      this.fail(openErrorReason);
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
      if (generation === this.generation) this.fail(this.socketErrorReason);
    };
    socket.onclose = () => {
      if (generation === this.generation) this.fail(this.socketCloseReason);
    };
  }
  receive(data) {
    if (
      data.op === "error" &&
      (data.request_id === this.requestId ||
        data.session_id === this.sessionId ||
        (this.transport === "usb" &&
          ["connecting", "arming", "initializing_right_arm", "armed"].includes(
            this.state,
          )))
    ) {
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      const bridgeMessage = bridgeErrorMessage(data.code);
      this.fail(
        bridgeMessage ??
          (data.code === "auth_failed"
            ? "Робот отклонил ключ доступа."
            : this.state === "initializing_right_arm"
              ? "Робот отклонил включение правой руки."
              : "Робот отклонил команду. Сессия остановлена."),
      );
      return;
    }
    if (
      this.state === "connecting" &&
      data.op === "capabilities" &&
      data.request_id === this.requestId
    ) {
      this.caps = validateCapabilities(data);
      if (this.caps.commissioning) this.speed = 1;
      this.deadline = null;
      this.lastTelemetry = {
        commanded_pose: this.caps.commanded_pose,
        measured_pose: null,
        tilt: null,
      };
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      if (this.caps.initialization_required) {
        this.change(
          "init_required",
          "Правая рука не включена. Введите ключ и нажмите «Включить правую руку»; начальное удержание 135° может сдвинуть свободную правую руку.",
        );
        return;
      }
      const transportNote =
        this.transport === "usb"
          ? " USB отвечает через локальную службу."
          : "";
      this.change(
        "ready",
        (this.caps.commissioning
          ? this.caps.mode === RIGHT_ARM_COMMISSIONING_MODE
            ? "Правая рука включена. Один выбранный сустав за сессию: правая рука до 5°, ноги и стопы до 1° при 1°/с."
            : "Первичная проверка: один сустав за сессию, ±1° при 1°/с. Привязка модели и механические пределы ещё не проверены."
          : "Робот совместим. Для движения откройте сессию.") +
          transportNote,
      );
      return;
    }
    if (
      this.state === "initializing_right_arm" &&
      data.op === "capabilities" &&
      data.request_id === this.requestId
    ) {
      const caps = validateCapabilities(data);
      assert(
        caps.mode === RIGHT_ARM_COMMISSIONING_MODE &&
          caps.right_arm_initialized === true &&
          !caps.initialization_required,
        "Робот не подтвердил включение правой руки.",
      );
      this.caps = caps;
      this.speed = 1;
      this.deadline = null;
      this.lastTelemetry = {
        commanded_pose: this.caps.commanded_pose,
        measured_pose: null,
        tilt: null,
      };
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      this.change(
        "ready",
        "Правая рука включена. Сессия ещё не открыта; движение начнётся только при удержании команды.",
      );
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
      this.commissioningStartPose = { ...this.lastTelemetry.commanded_pose };
      this.commissioningJoint = null;
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      this.change("armed", "Сессия открыта. Удерживайте разрешение движения.");
      return;
    }
    if (
      this.sessionId &&
      data.session_id === this.sessionId &&
      data.op === "stopped"
    ) {
      this.lastTelemetry = this.validateTelemetry(data);
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
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
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      this.rtt = Math.max(0, Math.round(this.now() - this.pending.sentAt));
      this.lastReplyAt = this.now();
      this.pending = null;
      this.onTelemetry(this.lastTelemetry);
      this.onChange(this.snapshot());
    }
  }
  validateTelemetry(data) {
    const commanded_pose = validatePose(data.commanded_pose);
    this.validateCommissioningPose(commanded_pose);
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
  initializeRightArm(key) {
    assert(
      this.state === "init_required" &&
        this.caps?.mode === RIGHT_ARM_COMMISSIONING_MODE,
      "Правая рука не ждёт включения.",
    );
    assert(
      typeof key === "string" && key.length >= 16 && key.length <= 128,
      "Нужен ключ доступа, выданный при настройке Live на роботе.",
    );
    this.requestId = this.makeId();
    this.deadline = this.now() + 2000;
    this.change("initializing_right_arm", "Включаем правую руку…");
    try {
      this.send({
        op: "initialize_right_arm",
        request_id: this.requestId,
        calibration_id: this.caps.calibration_id,
        access_key: key,
      });
    } catch (e) {
      this.fail(e.message);
    }
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
    assert(!this.caps?.commissioning || value === 1,
      "При первичной проверке скорость фиксирована: 1°/с.");
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
    this.validateCommissioningPose(pose, true);
    return pose;
  }
  validateCommissioningPose(pose, selectJoint = false) {
    if (!this.caps.commissioning) return;
    const changed = PROFILE.joints.filter((joint) =>
      Math.abs(pose[joint.id] - this.commissioningStartPose[joint.id]) > 0.000001);
    assert(changed.length <= 1 && changed.every((joint) =>
      this.caps.joint_limits.some((limit) => limit.id === joint.id) &&
      Math.abs(pose[joint.id] - this.commissioningStartPose[joint.id]) <=
        commissioningDeltaLimit(this.caps.mode, joint.id) &&
      (joint.id === this.commissioningJoint || (selectJoint && !this.commissioningJoint))),
    this.caps.mode === RIGHT_ARM_COMMISSIONING_MODE
      ? "Проверка правой руки: допустим один выбранный сустав, правая рука до 5°, ноги и стопы до 1°. Остальные должны сохранять начальные команды."
      : "Первичная проверка: допустим шаг одного выбранного сустава до 1°. Остальные должны сохранять начальные команды.");
    if (selectJoint && changed.length) this.commissioningJoint = changed[0].id;
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
          : this.state === "initializing_right_arm"
            ? "Сессия движения не открыта; подтверждение удержания правой руки не получено, состояние неизвестно."
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
    if (this.state === "initializing_right_arm") {
      this.fail(
        "Сессия движения не открыта; подтверждение удержания правой руки не получено, состояние неизвестно.",
      );
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
    if (emitChange) this.appendLog("error", "fault", reason);
    if (socket?.readyState === 1 && sessionId) {
      try {
        this.appendLog("sent", "stop", "аварийная остановка");
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
    if (this.state === "initializing_right_arm") {
      this.fail(
        "Сессия движения не открыта; подтверждение удержания правой руки не получено, состояние неизвестно.",
      );
      return;
    }
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
