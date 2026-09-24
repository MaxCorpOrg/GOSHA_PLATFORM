import { normalizePwmDiagnostics } from "./pwm-diagnostics.js";
import { PROFILE, prepareRobotPackageUpload, validatePose } from "./motion.js";

export const LIVE_PROTOCOL = "gosha.motion.live.v1";
export const USB_BRIDGE_HTTP_URL = "http://127.0.0.1:5177";
export const USB_BRIDGE_WS_URL = "ws://127.0.0.1:5177/live";
export const RIGHT_ARM_INITIALIZATION_REASON =
  "right_arm_initialization_required";
export const MOTION_EDITOR_MODE = "motion_editor";
export const RIGHT_ARM_COMMISSIONING_MODE = "commissioning_right_arm";
export const RIGHT_ARM_COMMISSIONING_EXTENTS = Object.freeze([5, 15]);
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
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function commissioningJointIds(mode) {
  if (mode === RIGHT_ARM_COMMISSIONING_MODE) return RIGHT_ARM_COMMISSIONING_JOINTS;
  if (mode === LEG_COMMISSIONING_MODE) return LEG_COMMISSIONING_JOINTS;
  return [];
}

function rightArmCommissioningExtent(limit) {
  if (limit?.id === "arm_positive_x" && limit.min === -70 && limit.max === 15) return 70;
  if (
    limit?.id === "arm_positive_x" &&
    RIGHT_ARM_COMMISSIONING_EXTENTS.includes(limit.max) &&
    limit.min === -limit.max
  )
    return limit.max;
  return null;
}

export function commissioningDeltaLimit(caps, jointId) {
  const limit = caps?.joint_limits.find((item) => item.id === jointId);
  if (!limit) return 0;
  if (caps.mode === RIGHT_ARM_COMMISSIONING_MODE && jointId === "arm_positive_x")
    return rightArmCommissioningExtent(limit) ?? 0;
  if (
    (caps.mode === RIGHT_ARM_COMMISSIONING_MODE ||
      caps.mode === LEG_COMMISSIONING_MODE) &&
    LEG_COMMISSIONING_JOINTS.includes(jointId) &&
    limit.min === -1 &&
    limit.max === 1 &&
    limit.max_speed_dps === 1
  )
    return 1;
  return 0;
}

function validateCommissioningLimits(mode, joint_limits, watchdog_ms) {
  const allowed = commissioningJointIds(mode);
  assert(
    watchdog_ms === 300 &&
      joint_limits.length === allowed.length &&
      joint_limits.every((limit) => {
        if (!allowed.includes(limit.id) || limit.max_speed_dps !== 1)
          return false;
        if (mode === RIGHT_ARM_COMMISSIONING_MODE && limit.id === "arm_positive_x")
          return rightArmCommissioningExtent(limit) !== null;
        return limit.min === -1 && limit.max === 1;
      }),
    mode === RIGHT_ARM_COMMISSIONING_MODE
      ? "Проверка правой руки допускает только правую руку ±5°, ±15° или −70…+15°, ноги и стопы ±1°, скорость 1°/с."
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
  if (message.op === "package_upload_begin") return "начало записи пакета";
  if (message.op === "package_upload_chunk")
    return `chunk ${message.offset}`;
  if (message.op === "package_upload_finish") return "завершение записи пакета";
  if (message.op === "package_upload_abort") return "отмена записи пакета";
  if (message.op === "package_list") return "список пакетов робота";
  if (message.op === "package_load") return "проверка сохранённого пакета";
  if (message.op === "package_select") return "выбор пакета робота";
  if (message.op === "package_prepare") return "подготовка сохранённого пакета";
  if (message.op === "package_sample")
    return `проверка кадра ${message.elapsed_ms} мс`;
  if (message.op === "package_run_start")
    return "программная проверка запуска пакета";
  if (message.op === "package_run_status")
    return "статус программной проверки пакета";
  if (message.op === "package_run_stop")
    return "остановка программной проверки пакета";
  if (message.op === "package_hardware_run_start")
    return "аппаратный запуск пакета";
  if (message.op === "package_hardware_run_status")
    return "статус аппаратного запуска пакета";
  if (message.op === "package_hardware_run_stop")
    return "остановка аппаратного запуска пакета";
  if (message.op === "package_delete")
    return "удаление сохранённого пакета";
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
  if (data.op === "package_status") return data.status ?? "статус пакета";
  if (data.op === "error") return data.code ?? "ошибка робота";
  return data.op;
}

function normalizePackageFeatures(features) {
  const source =
    features && typeof features === "object" && !Array.isArray(features)
      ? features
      : {};
  return {
    store_slots:
      Number.isInteger(source.store_slots) && source.store_slots >= 0
        ? source.store_slots
        : 0,
    list: source.list === true,
    select: source.select === true,
    delete_all: source.delete_all === true,
    delete_by_id: source.delete_by_id === true,
    hardware_run: source.hardware_run === true,
  };
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
  const editorMode = mode === MOTION_EDITOR_MODE;
  const rightArmMode = mode === RIGHT_ARM_COMMISSIONING_MODE || editorMode;
  const initializationRequired =
    rightArmMode &&
    data.right_arm_initialized === false &&
    data.motion_allowed === false &&
    data.reason === RIGHT_ARM_INITIALIZATION_REASON;
  assert(
    data.motion_allowed === true || initializationRequired,
    data.reason === "ordinary_pose_outside_editor"
      ? "Поза после обычного движения выходит за пределы редактора. Скажите роботу «Вернись в нейтральную стойку», затем подключитесь снова."
      : data.reason === "robot_movement_active"
        ? "Робот выполняет обычное движение. Дождитесь окончания или скажите «Стоп», затем подключитесь снова."
        : data.reason === "no_motion_profile"
          ? "В прошивке включён режим без движений."
          : "Робот не разрешил живое управление.",
  );
  assert(
    (data.commissioning === undefined || typeof data.commissioning === "boolean") &&
      (commissioning
        ? mode === LEG_COMMISSIONING_MODE || mode === RIGHT_ARM_COMMISSIONING_MODE
        : mode === "verified" || editorMode) &&
      (!rightArmMode || typeof data.right_arm_initialized === "boolean") &&
      (!rightArmMode ||
        data.right_arm_initialized === (data.motion_allowed === true)) &&
    data.profile_id === PROFILE.id &&
      data.calibrated === (!commissioning && !editorMode) &&
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
  if (editorMode) {
    assert(data.watchdog_ms === 300 && joint_limits.length === RIGHT_ARM_COMMISSIONING_JOINTS.length &&
      joint_limits.every((limit) => RIGHT_ARM_COMMISSIONING_JOINTS.includes(limit.id) && limit.max_speed_dps >= 1 && limit.max_speed_dps <= 10),
      "Профиль редактора должен содержать подключённую правую руку, ноги и стопы, скорость до 10°/с.");
  }
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
    calibrated: !commissioning && !editorMode,
    commissioning,
    initialization_required: initializationRequired,
    right_arm_initialized: rightArmMode
      ? data.right_arm_initialized === true
      : undefined,
    watchdog_ms: data.watchdog_ms,
    max_rate_hz: data.max_rate_hz,
    joint_limits,
    commanded_pose,
    pwm_diagnostics: normalizePwmDiagnostics(data.pwm_diagnostics),
    package_features: normalizePackageFeatures(data.package_features),
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
    this.stopReason = "";
    this.caps = null;
    this.socket = null;
    this.holding = false;
    this.following = false;
    this.needsFreshPoseClock = false;
    this.sessionId = null;
    this.pending = null;
    this.packageRequest = null;
    this.storedPackage = null;
    this.storedPackages = null;
    this.lastTelemetry = null;
    this.speed = 10;
    this.target = null;
    this.seq = 0;
    this.lastSentAt = 0;
    this.lastPoseAt = 0;
    this.rtt = null;
    this.closeAfterStop = false;
    this.commissioningStartPose = null;
    this.commissioningJoint = null;
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
      following: this.following,
      rtt: this.rtt,
      telemetry: this.lastTelemetry,
      speed: this.speed,
      target: this.target ? { ...this.target } : null,
      commissioning_start_pose: this.commissioningStartPose
        ? { ...this.commissioningStartPose }
        : null,
      commissioning_joint: this.commissioningJoint,
      pending: Boolean(this.pending),
      packagePending: Boolean(this.packageRequest),
      storedPackage: this.storedPackage ? { ...this.storedPackage } : null,
      storedPackages: Array.isArray(this.storedPackages)
        ? this.storedPackages.map((item) => ({ ...item }))
        : null,
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
  clearPackageRequest(reason = "Операция пакета прервана.") {
    if (!this.packageRequest) return;
    const request = this.packageRequest;
    clearTimeout(request.timer);
    this.packageRequest = null;
    request.reject(new Error(reason));
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
      this.packageRequest &&
      data.request_id === this.packageRequest.requestId &&
      (data.op === "package_status" || data.op === "error")
    ) {
      const request = this.packageRequest;
      clearTimeout(request.timer);
      this.packageRequest = null;
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      if (data.op === "error") {
        request.reject(
          new Error(
            data.message || data.code || "Робот отклонил операцию пакета.",
          ),
        );
      } else if (!request.statuses.includes(data.status)) {
        request.reject(
          new Error("Робот вернул неожиданный статус операции пакета."),
        );
      } else {
        request.resolve(data);
      }
      this.onChange(this.snapshot());
      return;
    }
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
      else if (this.caps.mode === MOTION_EDITOR_MODE) this.speed = Math.min(5,...this.caps.joint_limits.map(j=>j.max_speed_dps));
      this.deadline = null;
      this.lastTelemetry = {
        commanded_pose: this.caps.commanded_pose,
        pwm_diagnostics: this.caps.pwm_diagnostics,
        measured_pose: null,
        tilt: null,
      };
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      if (this.caps.initialization_required) {
        this.change(
          "init_required",
          "Правая рука не включена. Введите ключ и нажмите «Включить правую руку»; начальное удержание может сдвинуть свободную правую руку.",
        );
        return;
      }
      const rightArmLimit = this.caps.joint_limits.find((item) => item.id === "arm_positive_x");
      const transportNote =
        this.transport === "usb"
          ? " USB отвечает через локальную службу."
          : "";
      this.change(
        "ready",
        (this.caps.commissioning
          ? this.caps.mode === RIGHT_ARM_COMMISSIONING_MODE
            ? `Правая рука включена. Один выбранный сустав за сессию: правая рука ${rightArmLimit.min}…${rightArmLimit.max}°, ноги и стопы до 1° при 1°/с.`
            : "Первичная проверка: один сустав за сессию, ±1° при 1°/с. Привязка модели и механические пределы ещё не проверены."
          : this.caps.mode === MOTION_EDITOR_MODE ? "Редактор подключён. Все доступные суставы управляются в одной сессии." : "Робот совместим. Для движения откройте сессию.") +
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
        [RIGHT_ARM_COMMISSIONING_MODE, MOTION_EDITOR_MODE].includes(caps.mode) &&
          caps.right_arm_initialized === true &&
          !caps.initialization_required,
        "Робот не подтвердил включение правой руки.",
      );
      this.caps = caps;
      this.speed = caps.mode === MOTION_EDITOR_MODE ? Math.min(5,...caps.joint_limits.map(j=>j.max_speed_dps)) : 1;
      this.deadline = null;
      this.lastTelemetry = {
        commanded_pose: this.caps.commanded_pose,
        pwm_diagnostics: this.caps.pwm_diagnostics,
        measured_pose: null,
        tilt: null,
      };
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      this.change(
        "ready",
        "Правая рука включена. Сессия ещё не открыта; движение начнётся после выбора цели.",
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
      this.stopReason = "";
      this.deadline = null;
      this.seq = 0;
      this.pending = null;
      this.lastSentAt = this.now();
      this.lastPoseAt = this.now();
      this.lastReplyAt = this.now();
      this.commissioningStartPose = { ...this.lastTelemetry.commanded_pose };
      this.commissioningJoint = null;
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      this.change("armed", "Сессия открыта. Выберите угол ползунком справа.");
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
      this.following = false;
      this.needsFreshPoseClock = false;
      this.pending = null;
      this.sessionId = null;
      this.deadline = null;
      this.commissioningStartPose = null;
      this.commissioningJoint = null;
      if (this.closeAfterStop) {
        this.disconnect("Робот подтвердил остановку. Соединение закрыто.");
        return;
      }
      this.change(
        "ready",
        `${this.stopReason || "Сессия закрыта."} Робот подтвердил остановку. Для движения откройте новую сессию.`,
      );
      return;
    }
    if (
      data.op === "ack" &&
      this.state === "armed" &&
      data.session_id === this.sessionId
    ) {
      if (!this.pending || data.seq !== this.pending.seq) return;
      const acknowledged = this.pending;
      this.lastTelemetry = this.validateTelemetry(data);
      this.appendLog("received", data.op, summarizeIncoming(data, this.caps));
      this.rtt = Math.max(0, Math.round(this.now() - this.pending.sentAt));
      this.lastReplyAt = this.now();
      this.pending = null;
      // Finish only an ACK for the latest target actually sent, never an old
      // command that happens to pass through the newly requested position.
      if (this.following && acknowledged.op === "pose" && this.target &&
          PROFILE.joints.every((joint) =>
            acknowledged.target?.[joint.id] === this.target[joint.id] &&
            Math.abs(this.lastTelemetry.commanded_pose[joint.id] - this.target[joint.id]) <= (this.caps.mode === MOTION_EDITOR_MODE ? 0.5 : 0))) {
        this.holding = false;
        this.following = false;
        this.target = null;
        this.needsFreshPoseClock = false;
        this.reason = "Целевая команда подтверждена. Фактический угол не измеряется; сессия открыта.";
      }
      this.onTelemetry(this.lastTelemetry);
      this.onChange(this.snapshot());
    }
  }
  requestPackageStatus(message, statuses, timeoutMs = 2500) {
    assert(!this.packageRequest, "Предыдущая операция пакета ещё не завершена.");
    const requestId = this.makeId();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.packageRequest?.requestId === requestId) {
          this.packageRequest = null;
          this.onChange(this.snapshot());
        }
        reject(new Error("Робот не подтвердил операцию пакета."));
      }, timeoutMs);
      this.packageRequest = { requestId, statuses, resolve, reject, timer };
      try {
        this.send({ ...message, request_id: requestId });
      } catch (error) {
        clearTimeout(timer);
        this.packageRequest = null;
        reject(error);
      }
    });
  }
  assertPackageOperationReady(key, action = "Операция с пакетом", requireInitialized = true) {
    this.assertPackageReadReady(action);
    assert(
      !requireInitialized || this.state === "ready",
      `${action} требует включенной правой руки и состояния готовности.`,
    );
    assert(
      typeof key === "string" && key.length >= 16 && key.length <= 128,
      "Нужен ключ доступа, выданный при настройке Live на роботе.",
    );
  }
  assertPackageReadReady(action = "Операция с пакетом") {
    assert(
      ["ready", "init_required"].includes(this.state) && this.caps,
      "Подключите робота и дождитесь состояния готовности.",
    );
    assert(
      this.caps.mode === MOTION_EDITOR_MODE && !this.caps.commissioning,
      `${action} доступна только в Live-режиме motion_editor.`,
    );
  }
  packageMetadataFromRecord(record, { active = false } = {}) {
    assert(
      record &&
        typeof record.package_id === "string" &&
        record.package_id.length > 0 &&
        typeof record.profile_id === "string" &&
        typeof record.calibration_id === "string" &&
        Number.isFinite(record.payload_size) &&
        record.payload_size > 0 &&
        Number.isFinite(record.crc32),
      "Робот вернул некорректные сведения о сохранённом пакете.",
    );
    return {
      present: true,
      package_id: record.package_id,
      name:
        typeof record.name === "string" && record.name.trim()
          ? record.name
          : record.package_id,
      profile_id: record.profile_id,
      calibration_id: record.calibration_id,
      payload_size: record.payload_size,
      crc32: record.crc32,
      duration_ms: Number.isFinite(record.duration_ms)
        ? record.duration_ms
        : null,
      interpolation:
        typeof record.interpolation === "string" && record.interpolation
          ? record.interpolation
          : null,
      keyframe_count: Number.isInteger(record.keyframe_count)
        ? record.keyframe_count
        : null,
      active_joint_count: Number.isInteger(record.active_joint_count)
        ? record.active_joint_count
        : null,
      active,
      compatible:
        record.profile_id === this.caps?.profile_id &&
        record.calibration_id === this.caps?.calibration_id,
    };
  }
  setStoredPackage(metadata, packages = null) {
    this.storedPackage = metadata;
    if (packages) {
      this.storedPackages = packages;
    } else if (metadata?.present && Array.isArray(this.storedPackages)) {
      const found = this.storedPackages.some(
        (item) => item.package_id === metadata.package_id,
      );
      this.storedPackages = found
        ? this.storedPackages.map((item) =>
            item.package_id === metadata.package_id
              ? { ...metadata, active: true }
              : { ...item, active: false },
          )
        : [
            { ...metadata, active: true },
            ...this.storedPackages.map((item) => ({
              ...item,
              active: false,
            })),
          ];
    } else if (metadata?.present === false) {
      this.storedPackages = [];
    }
    this.onChange(this.snapshot());
    return metadata;
  }
  setStoredPackageList(listed) {
    assert(
      Number.isInteger(listed.count) &&
        Array.isArray(listed.packages) &&
        listed.packages.length === listed.count,
      "Робот вернул некорректный список сохранённых пакетов.",
    );
    if (listed.packages.length === 0) {
      const empty = { present: false };
      this.setStoredPackage(empty, []);
      return {
        storedPackage: empty,
        storedPackages: [],
      };
    }
    const activePackages = listed.packages.filter((item) => item.active === true);
    const active = activePackages[0];
    assert(
      active && activePackages.length === 1,
      "Робот вернул список пакетов без активного payload.",
    );
    const storedPackages = listed.packages.map((item) =>
      this.packageMetadataFromRecord(item, { active: item.active === true }),
    );
    const storedPackage = this.setStoredPackage(
      this.packageMetadataFromRecord(active, { active: true }),
      storedPackages,
    );
    return { storedPackage, storedPackages };
  }
  async readStoredPackage() {
    this.assertPackageReadReady("Статус пакета робота");
    const listed = await this.requestPackageStatus(
      { op: "package_list" },
      ["listed"],
    );
    return { listed, ...this.setStoredPackageList(listed) };
  }
  async selectStoredPackage(packageId, key) {
    this.assertPackageOperationReady(key, "Выбор пакета робота", false);
    assert(
      typeof packageId === "string" && packageId.length > 0,
      "Выберите пакет робота из списка.",
    );
    const selected = await this.requestPackageStatus(
      {
        op: "package_select",
        package_id: packageId,
        access_key: key,
      },
      ["selected"],
      5000,
    );
    const storedPackage = this.setStoredPackage(
      this.packageMetadataFromRecord(selected, { active: true }),
    );
    return { selected, storedPackage };
  }
  packageSampleWithinCaps(sample, packageId, elapsedMs = null) {
    if (
      sample.package_id !== packageId ||
      (elapsedMs !== null && sample.elapsed_ms !== elapsedMs) ||
      !Number.isFinite(sample.elapsed_ms) ||
      !sample.target
    )
      return false;
    return this.caps.joint_limits.every(
      (limit) =>
        Number.isFinite(sample.target[limit.id]) &&
        sample.target[limit.id] >= limit.min &&
        sample.target[limit.id] <= limit.max,
    );
  }
  async verifyPackageSoftwareRun(packageId, key, firstFrameMatches) {
    let runSessionId = null;
    try {
      const runStarted = await this.requestPackageStatus(
        {
          op: "package_run_start",
          access_key: key,
        },
        ["run_started"],
      );
      runSessionId = runStarted.run_session_id;
      assert(
        typeof runSessionId === "string" &&
          runSessionId.length >= 16 &&
          runStarted.hardware_apply === false &&
          firstFrameMatches(runStarted),
        "Робот не подтвердил безопасный программный запуск пакета.",
      );
      const runStatus = await this.requestPackageStatus(
        {
          op: "package_run_status",
          run_session_id: runSessionId,
        },
        ["run_running", "run_finished"],
      );
      assert(
        runStatus.run_session_id === runSessionId &&
          runStatus.hardware_apply === false &&
          this.packageSampleWithinCaps(runStatus, packageId),
        "Робот не подтвердил программное исполнение пакета без hardware apply.",
      );
      const runStopped = await this.requestPackageStatus(
        {
          op: "package_run_stop",
          run_session_id: runSessionId,
        },
        ["run_stopped"],
      );
      assert(
        runStopped.run_session_id === runSessionId &&
          runStopped.hardware_apply === false,
        "Робот не подтвердил остановку программной проверки пакета.",
      );
      runSessionId = null;
      return { runStarted, runStatus, runStopped };
    } catch (error) {
      if (runSessionId) {
        try {
          await this.requestPackageStatus(
            {
              op: "package_run_stop",
              run_session_id: runSessionId,
            },
            ["run_stopped"],
            1000,
          );
        } catch {}
      }
      throw error;
    }
  }
  hardwareRunStatusWithinCaps(status, packageId) {
    if (!["hardware_run_running", "hardware_run_finished"].includes(status.status)) {
      return false;
    }
    if (status.hardware_apply === false) {
      return Boolean(status.run_session_id) && !status.target;
    }
    return (
      status.hardware_apply === true &&
      this.packageSampleWithinCaps(status, packageId)
    );
  }
  async runStoredPackageInHardware(key, { pollIntervalMs = 500 } = {}) {
    this.assertPackageOperationReady(key, "Аппаратный запуск сохранённого пакета");
    const loaded = await this.requestPackageStatus(
      { op: "package_load" },
      ["loaded"],
    );
    const storedPackage = this.setStoredPackage(
      this.packageMetadataFromRecord(loaded),
    );
    assert(
      storedPackage.compatible,
      "Сохранённый пакет не совпал с текущей калибровкой робота.",
    );
    const prepared = await this.requestPackageStatus(
      { op: "package_prepare" },
      ["prepared"],
    );
    assert(
      prepared.package_id === loaded.package_id &&
        Number.isFinite(prepared.duration_ms) &&
        prepared.duration_ms > 0,
      "Робот не подготовил сохранённый пакет к запуску.",
    );
    const sampled = await this.requestPackageStatus(
      { op: "package_sample", elapsed_ms: 0 },
      ["sampled"],
    );
    assert(
      this.packageSampleWithinCaps(sampled, loaded.package_id, 0) &&
        this.caps.joint_limits.every(
          (limit) => sampled.target[limit.id] === this.caps.commanded_pose[limit.id],
        ),
      "Первый кадр пакета не совпадает с текущей командой робота.",
    );
    let runSessionId = null;
    try {
      const runStarted = await this.requestPackageStatus(
        {
          op: "package_hardware_run_start",
          access_key: key,
          speed_dps: this.speed,
        },
        ["hardware_run_started"],
        5000,
      );
      runSessionId = runStarted.run_session_id;
      assert(
        typeof runSessionId === "string" &&
          runSessionId.length >= 16 &&
          runStarted.hardware_apply === false,
        "Робот не подтвердил безопасный старт аппаратного запуска.",
      );
      const deadline = this.now() + prepared.duration_ms + 8000;
      let runStatus = null;
      do {
        runStatus = await this.requestPackageStatus(
          {
            op: "package_hardware_run_status",
            run_session_id: runSessionId,
          },
          ["hardware_run_running", "hardware_run_finished"],
          5000,
        );
        assert(
          runStatus.run_session_id === runSessionId &&
            this.hardwareRunStatusWithinCaps(runStatus, loaded.package_id),
          "Робот сообщил некорректный статус аппаратного запуска.",
        );
        if (runStatus.status === "hardware_run_finished") {
          runSessionId = null;
          return { loaded, prepared, sampled, runStarted, runStatus };
        }
        await delay(pollIntervalMs);
      } while (this.now() < deadline);
      throw new Error("Робот не завершил аппаратный запуск пакета вовремя.");
    } catch (error) {
      if (runSessionId) {
        try {
          await this.requestPackageStatus(
            {
              op: "package_hardware_run_stop",
              run_session_id: runSessionId,
            },
            ["hardware_run_stopped"],
            1500,
          );
        } catch {}
      }
      throw error;
    }
  }
  async verifyStoredPackage(key) {
    this.assertPackageOperationReady(key, "Проверка сохранённого пакета");
    const loaded = await this.requestPackageStatus(
      { op: "package_load" },
      ["loaded"],
    );
    const storedPackage = this.setStoredPackage(
      this.packageMetadataFromRecord(loaded),
    );
    assert(
      storedPackage.compatible,
      "Сохранённый пакет не совпал с текущей калибровкой робота.",
    );
    const prepared = await this.requestPackageStatus(
      { op: "package_prepare" },
      ["prepared"],
    );
    assert(
      prepared.package_id === loaded.package_id &&
        Number.isFinite(prepared.duration_ms) &&
        prepared.duration_ms > 0,
      "Робот не подготовил сохранённый пакет к проверке.",
    );
    const sampled = await this.requestPackageStatus(
      { op: "package_sample", elapsed_ms: 0 },
      ["sampled"],
    );
    assert(
      this.packageSampleWithinCaps(sampled, loaded.package_id, 0),
      "Робот не смог прочитать первый кадр сохранённого пакета.",
    );
    const firstFrameMatches = (sample) =>
      this.packageSampleWithinCaps(sample, loaded.package_id, 0) &&
      this.caps.joint_limits.every(
        (limit) => sample.target[limit.id] === sampled.target[limit.id],
      );
    const run = await this.verifyPackageSoftwareRun(
      loaded.package_id,
      key,
      firstFrameMatches,
    );
    return { loaded, prepared, sampled, ...run };
  }
  async deleteStoredPackage(key, packageId = null) {
    this.assertPackageOperationReady(key, "Удаление сохранённого пакета", false);
    const targeted = packageId !== null && packageId !== undefined;
    if (targeted) {
      assert(
        this.caps?.package_features?.delete_by_id === true,
        "Прошивка робота не поддерживает безопасное удаление выбранного пакета.",
      );
      assert(
        typeof packageId === "string" && packageId.length > 0,
        "Выберите пакет робота из списка.",
      );
    }
    const deleted = await this.requestPackageStatus(
      {
        op: "package_delete",
        ...(targeted ? { package_id: packageId } : {}),
        access_key: key,
      },
      ["deleted"],
      5000,
    );
    if (targeted) {
      const { listed, storedPackage, storedPackages } =
        await this.readStoredPackage();
      return { deleted, listed, storedPackage, storedPackages };
    }
    this.setStoredPackage({ present: false });
    this.storedPackages = [];
    return { deleted };
  }
  async uploadPackageDraft(packageDraft, key) {
    this.assertPackageOperationReady(key, "Запись пакета", false);
    assert(
      this.caps.package_features?.store_slots >= 3,
      "Эта прошивка хранит только два пакета. Для сохранной записи нужна обновлённая прошивка Motion Studio.",
    );
    const upload = prepareRobotPackageUpload(packageDraft);
    assert(
      upload.profile_id === this.caps.profile_id &&
        upload.calibration_id === this.caps.calibration_id,
      "Пакет подготовлен не для текущей калибровки робота.",
    );
    let uploadSessionId = null;
    try {
      const begin = await this.requestPackageStatus(
        {
          op: "package_upload_begin",
          package_id: upload.package_id,
          profile_id: upload.profile_id,
          calibration_id: upload.calibration_id,
          total_size: upload.total_size,
          crc32: upload.crc32,
          access_key: key,
        },
        ["upload_started"],
      );
      uploadSessionId = begin.upload_session_id;
      assert(
        typeof uploadSessionId === "string" && uploadSessionId.length >= 16,
        "Робот не выдал корректную upload-сессию.",
      );
      for (const chunk of upload.chunks) {
        await this.requestPackageStatus(
          {
            op: "package_upload_chunk",
            upload_session_id: uploadSessionId,
            offset: chunk.offset,
            data_b64: chunk.data_b64,
          },
          ["upload_chunk"],
        );
      }
      await this.requestPackageStatus(
        {
          op: "package_upload_finish",
          upload_session_id: uploadSessionId,
          access_key: key,
        },
        ["stored"],
        5000,
      );
      uploadSessionId = null;
      const loaded = await this.requestPackageStatus(
        { op: "package_load" },
        ["loaded"],
      );
      const storedPackage = this.setStoredPackage(
        this.packageMetadataFromRecord(loaded),
      );
      assert(
        loaded.package_id === upload.package_id &&
          loaded.profile_id === upload.profile_id &&
          loaded.calibration_id === upload.calibration_id &&
          loaded.payload_size === upload.total_size &&
          loaded.crc32 === upload.crc32,
        "Сохранённый пакет не совпал с отправленным.",
      );
      const prepared = await this.requestPackageStatus(
        { op: "package_prepare" },
        ["prepared"],
      );
      assert(
        prepared.package_id === upload.package_id &&
          prepared.duration_ms === packageDraft.duration_ms,
        "Робот не подготовил сохранённый пакет к проверке.",
      );
      const sampled = await this.requestPackageStatus(
        { op: "package_sample", elapsed_ms: 0 },
        ["sampled"],
      );
      const firstTarget = packageDraft.keyframes?.[0]?.target ?? {};
      const firstFrameMatches = (sample) =>
        this.packageSampleWithinCaps(sample, upload.package_id, 0) &&
        packageDraft.active_joints.every(
          (id) => sample.target[id] === firstTarget[id],
        );
      assert(
        firstFrameMatches(sampled),
        "Робот не смог прочитать первый кадр сохранённого пакета.",
      );
      const run = await this.verifyPackageSoftwareRun(
        upload.package_id,
        key,
        firstFrameMatches,
      );
      const catalog = await this.readStoredPackage();
      assert(
        catalog.storedPackages.some((item) =>
          item.package_id === upload.package_id &&
          item.crc32 === upload.crc32 && item.active === true),
        "Робот не подтвердил пакет в каталоге после записи.",
      );
      return { loaded, prepared, sampled, storedPackage, catalog, ...run };
    } catch (error) {
      if (uploadSessionId) {
        try {
          await this.requestPackageStatus(
            {
              op: "package_upload_abort",
              upload_session_id: uploadSessionId,
            },
            ["upload_aborted"],
            1000,
          );
        } catch {}
      }
      throw error;
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
    return { commanded_pose, measured_pose, tilt, pwm_diagnostics: normalizePwmDiagnostics(data.pwm_diagnostics) };
  }
  initializeRightArm(key) {
    assert(
      this.state === "init_required" &&
        [RIGHT_ARM_COMMISSIONING_MODE, MOTION_EDITOR_MODE].includes(this.caps?.mode),
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
    assert(this.caps?.mode !== MOTION_EDITOR_MODE || value <= Math.min(10,...this.caps.joint_limits.map(j=>j.max_speed_dps)), "Скорость выше предела редактора.");
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
    for (const joint of this.caps.mode === MOTION_EDITOR_MODE ? PROFILE.joints : [])
      if (!this.caps.joint_limits.some(limit => limit.id === joint.id))
        assert(pose[joint.id] === this.lastTelemetry.commanded_pose[joint.id], `${joint.label}: привод недоступен.`);
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
        commissioningDeltaLimit(this.caps, joint.id) &&
      (joint.id === this.commissioningJoint || (selectJoint && !this.commissioningJoint))),
    this.caps.mode === RIGHT_ARM_COMMISSIONING_MODE
      ? "Проверка правой руки: допустим один выбранный сустав, правая рука по текущему пределу, ноги и стопы до 1°. Остальные должны сохранять начальные команды."
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
    const starting = !this.holding;
    assert(this.state === "armed", "Откройте сессию Live.");
    this.setTarget(pose);
    if (starting) this.needsFreshPoseClock = this.caps.mode === RIGHT_ARM_COMMISSIONING_MODE;
    this.following = false;
    this.holding = true;
    this.onChange(this.snapshot());
  }
  moveTo(pose) {
    assert(this.state === "armed", "Откройте сессию Live.");
    const starting = !this.holding;
    this.setTarget(pose);
    if (starting) this.needsFreshPoseClock = this.caps.mode === RIGHT_ARM_COMMISSIONING_MODE;
    this.holding = true;
    this.following = true;
    this.change("armed", "Робот идёт к выбранному углу. STOP — остановить и закрыть сессию.");
  }
  beginHold(pose) {
    this.hold(pose);
  }
  updatePose(pose) {
    assert(
      this.state === "armed" && this.holding,
      "Удерживайте ползунок или кнопку, чтобы менять цель Live.",
    );
    this.setTarget(pose);
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
    let sentTarget = null;
    if (op === "pose") {
      // Right-arm commissioning keepalive does not advance its motion clock.
      // Re-prime it at the confirmed position after an idle gap, then wait for
      // that ACK before dispatching the requested target. Never auto-arm.
      const prime = this.needsFreshPoseClock && now - this.lastPoseAt > 100;
      sentTarget = { ...(prime ? this.lastTelemetry.commanded_pose : this.target) };
      message.target = Object.fromEntries(
        this.caps.joint_limits.map((j) => [j.id, sentTarget[j.id]]),
      );
      message.speed_dps = Math.min(
        this.speed,
        ...this.caps.joint_limits.map((joint) => joint.max_speed_dps),
      );
    }
    this.pending = { seq, sentAt: now, op, target: op === "pose" ? sentTarget : null };
    if (op === "pose") {
      this.lastPoseAt = now;
      this.needsFreshPoseClock = false;
    }
    this.lastSentAt = now;
    try {
      this.send(message);
    } catch (e) {
      this.fail(e.message);
    }
  }
  stop(reason = "Остановка запрошена.") {
    this.stopReason = reason;
    this.holding = false;
    this.following = false;
    this.needsFreshPoseClock = false;
    this.target = null;
    this.pending = null;
    this.clearPackageRequest(reason);
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
    this.following = false;
    this.needsFreshPoseClock = false;
    this.target = null;
    this.pending = null;
    this.sessionId = null;
    this.deadline = null;
    this.clearPackageRequest(reason);
    this.socket = null;
    this.closeAfterStop = false;
    this.commissioningStartPose = null;
    this.commissioningJoint = null;
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
    this.following = false;
    this.needsFreshPoseClock = false;
    this.clearPackageRequest(reason);
    this.closeAfterStop = false;
    this.commissioningStartPose = null;
    this.commissioningJoint = null;
    this.change("disconnected", reason);
  }
}
