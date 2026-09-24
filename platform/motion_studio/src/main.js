import "./style.css";
import "./live.css";
import { mountLivePanel } from "./live-panel.js";
import {
  PROFILE,
  clone,
  zeroPose,
  createMotion,
  examples,
  poseAt,
  putPose,
  mirrorPose,
  validateMotion,
  validatePose,
  parseMotion,
  prepareRobotMotionPackage,
  prepareRobotPackageUpload,
  robotPackageCrc32,
  newId,
  MAX_FILE_BYTES,
} from "./motion.js";
import { createStore } from "./storage.js";
import { registerPreviewTools } from "./webmcp.js";

const icons = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  play: '<path d="m8 5 11 7-11 7Z"/>',
  pause: '<path d="M9 5v14M15 5v14"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5"/>',
  upload: '<path d="M12 16V4m-5 5 5-5 5 5M5 16v5h14v-5"/>',
  cube: '<path d="m12 3 9 5v9l-9 5-9-5V8Zm0 0v10m9-5-9 5-9-5m9 5v9"/>',
  undo: '<path d="M9 5 4 10l5 5M4 10h10a6 6 0 0 1 0 12"/>',
  redo: '<path d="m15 5 5 5-5 5m5-5H10a6 6 0 0 0 0 12"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V3H3v12h5"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
  save: '<path d="M5 3h12l4 4v14H3V3Zm2 0v6h10V3M7 21v-8h10v8"/>',
  reset: '<path d="M3 10a9 9 0 1 1 1 7M3 3v7h7"/>',
  mirror: '<path d="M12 2v3m0 3v3m0 3v3m0 3v2M8 6v12H2Zm8 0v12h6Z"/>',
  "shield-check": '<path d="M12 2 20 5v7c0 5-3.5 8-8 10-4.5-2-8-5-8-10V5Z"/><path d="m9 12 2 2 4-5"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
const $ = (id) => document.getElementById(id);
const seconds = (ms) => (ms / 1000).toFixed(Math.round(ms) % 10 === 0 ? 2 : 3);
let storage;
try {
  storage = window.localStorage;
} catch {
  storage = {
    getItem() {
      throw new Error("Unavailable");
    },
  };
}
const store = createStore(storage);
const library = store.load();
let motion = clone(library.motions.find((m) => m.id === library.active_id));
let time = 0;
let pose = poseAt(motion, time);
let selectedJoint = PROFILE.joints[1].id;
let playing = false;
let loop = true;
let speed = 1;
let undo = [];
let redo = [];
let scene;
let live;
let disposed = false;
let toastTimer;

document.querySelector("#app").innerHTML = `
  <header class="topbar">
    <a class="brand" href="./" aria-label="Motion Studio, главная"><span class="brand-symbol">${icon("cube")}</span><span>AI ROBOTS <strong>Motion Studio</strong></span></a>
    <span class="version">ПРОТОТИП 01</span>
    <div class="top-spacer"></div>
    <span class="save-state" id="save-status" role="status">Сохранено в браузере</span>
    <span class="save-state" id="robot-install-status" role="status">Черновик</span>
    <button id="export-original" class="button quiet" hidden>Исходная библиотека</button>
    <button id="export-robot-package" class="button quiet">${icon("download")}Пакет робота</button>
    <button id="upload-robot-package" class="button quiet">${icon("upload")}Записать в робота</button>
    <button id="verify-robot-package" class="button quiet">${icon("shield-check")}Проверить в роботе</button>
    <button id="run-robot-package" class="button quiet">${icon("play")}Запустить в роботе</button>
    <button id="delete-robot-package" class="button quiet">${icon("trash")}Удалить в роботе</button>
    <button id="import" class="button quiet">${icon("upload")}Импорт</button>
    <button id="export" class="button">${icon("download")}Экспорт JSON</button>
    <input type="file" id="file-input" accept=".json,application/json" hidden />
  </header>
  <div class="workspace">
    <aside class="library panel">
      <div class="panel-heading"><span class="eyebrow">БИБЛИОТЕКА</span><span class="count" id="library-count"></span></div>
      <h1>Движения</h1>
      <button class="button new-motion" id="new">${icon("plus")}Новое движение</button>
      <button class="button quiet new-motion" id="new-greeting">${icon("copy")}Малое приветствие</button>
      <label class="search-label"><span class="sr-only">Найти движение</span><input id="search" type="search" placeholder="Найти движение…" /></label>
      <div id="motion-list" class="motion-list"></div>
      <div class="library-footer"><span class="local-dot"></span><div>Локальная библиотека<small>Движения остаются в этом браузере. Для копии скачайте JSON.</small></div></div>
    </aside>
    <main class="editor">
      <section class="scene-panel">
        <div class="scene-heading"><div><span class="eyebrow">РЕДАКТОР ДВИЖЕНИЯ</span><input id="motion-name" maxlength="80" aria-label="Название движения" /></div><span class="preview-badge"><span></span>3D-предпросмотр</span></div>
        <div id="viewport" class="viewport"></div>
        <div class="model-note"><span class="model-chip">ГОША <span>01</span></span><span id="model-status">Загружаем вашу модель…</span></div>
        <div class="view-controls"><button id="view-home" title="Вернуть вид" aria-label="Вернуть вид">${icon("cube")}</button><button id="view-front">Спереди</button><button id="view-grid" aria-pressed="true">Сетка</button></div>
        <div class="view-help">Потяните для вращения · колесо для масштаба · нажмите на деталь</div>
        <div class="axis-key"><span class="axis-x">X</span><span class="axis-y">Y</span><span class="axis-z">Z ↑</span></div>
        <div id="model-error" class="model-error" hidden></div>
      </section>
      <section class="timeline-panel" aria-label="Временная шкала">
        <div class="transport">
          <button class="play-button" id="play" title="Воспроизвести в 3D" aria-label="Воспроизвести в 3D">${icon("play")}</button>
          <button class="icon-button" id="stop" title="В начало" aria-label="В начало">${icon("stop")}</button>
          <output id="time-output" class="time-output">0.00 <span>/ 4.00 с</span></output>
          <label class="speed"><span class="sr-only">Скорость воспроизведения</span><select id="speed"><option value="0.5">0,5×</option><option value="1" selected>1×</option><option value="2">2×</option></select></label>
          <button class="text-button loop-button active" id="loop" aria-pressed="true">↻ Цикл</button>
          <span class="top-spacer"></span>
          <button class="icon-button" id="undo" title="Отменить (Ctrl+Z)" aria-label="Отменить">${icon("undo")}</button>
          <button class="icon-button" id="redo" title="Повторить (Ctrl+Shift+Z)" aria-label="Повторить">${icon("redo")}</button>
          <button class="button accent" id="add-frame">${icon("plus")}Записать позу</button>
        </div>
        <div class="timeline" id="timeline"><div class="track-labels"><div class="ruler-label">СУСТАВЫ</div>${PROFILE.joints.map((j) => `<button data-select="${j.id}"><span class="track-dot"></span>${j.short}</button>`).join("")}</div><div class="tracks-area" id="tracks-area"><div class="ruler" id="ruler"></div><div id="tracks"></div><div id="playhead" class="playhead"><span></span></div></div></div>
        <div class="timeline-footer"><span id="frame-count"></span><label>Позиция <input id="time-input" type="number" min="0" step="0.001" aria-label="Позиция на шкале в секундах" /> с</label><input id="scrubber" type="range" min="0" step="1" aria-label="Позиция на временной шкале"/><button class="text-button" id="delete-frame">Удалить позу</button></div>
      </section>
    </main>
    <aside class="inspector panel">
      <div class="panel-heading"><span class="eyebrow">НАСТРОЙКА ПОЗЫ</span><span class="count">6</span></div>
      <h2>Суставы</h2>
      <p class="inspector-intro">Измените угол — поза запишется на текущей отметке времени.</p>
      <div id="joint-controls" class="joint-controls">${PROFILE.joints.map((j) => `<div class="joint-control" data-joint="${j.id}"><div class="joint-title"><button data-select="${j.id}">${j.label}</button><label><input type="number" id="number-${j.id}" min="${j.min}" max="${j.max}" step="1" value="0" aria-label="${j.label}, градусы"/><span>°</span></label></div><input type="range" id="range-${j.id}" min="${j.min}" max="${j.max}" step="1" value="0" aria-label="${j.label}"/><div class="range-ends"><span>${j.min}°</span><span class="range-zero"><button type="button" id="zero-${j.id}" title="Установить 0°" aria-label="${j.label}: установить 0 градусов">0</button></span><span>+${j.max}°</span></div><small id="live-row-${j.id}" class="live-row-note" hidden></small></div>`).join("")}</div>
      <div class="pose-actions"><button class="button quiet" id="neutral">${icon("reset")}Нулевая поза</button><button class="icon-button" id="mirror" title="Отразить позу" aria-label="Отразить позу">${icon("mirror")}</button></div>
      <div class="sequence-settings"><h3>Параметры движения</h3><label>Длительность <span><input id="duration" type="number" min="0.5" max="120" step="0.1" /> с</span></label><label>Переходы <select id="interpolation"><option value="smooth">Плавные</option><option value="linear">Линейные</option><option value="hold">Без перехода</option></select></label></div>
      <div class="version-actions"><button class="button quiet" id="save-version">${icon("save")}Сохранить версию</button><select id="versions" aria-label="Восстановить сохранённую версию"><option value="">История версий</option></select></div>
      <p class="calibration-note">Оси и пределы предварительные. Углы отсчитываются от позы модели. Подключения к роботу нет.</p>
    </aside>
  </div>
  <footer class="statusbar"><span><span class="status-dot"></span>На компьютере · робот не подключён</span><span id="selection-status"></span><span>JSON v1 · Гоша</span></footer>
  <div id="toast" class="toast" role="status" hidden></div>
  <dialog id="delete-dialog"><h2>Удалить движение?</h2><p id="delete-name"></p><p>Сначала можно скачать JSON, чтобы оставить копию.</p><div class="dialog-actions"><button class="button" id="cancel-delete">Отмена</button><button class="button danger" id="confirm-delete">Удалить</button></div></dialog>
`;

function notify(message, error = false) {
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(
    () => {
      $("toast").hidden = true;
    },
    error ? 9000 : 3500,
  );
}
function attempt(action) {
  try {
    const result = action();
    if (result?.then) {
      return result.catch((error) => {
        pose = poseAt(motion, time);
        notify(error.message, true);
        render();
      });
    }
    return result;
  } catch (error) {
    pose = poseAt(motion, time);
    notify(error.message, true);
    render();
  }
}
function motionFingerprint(value) {
  return robotPackageCrc32(
    new TextEncoder().encode(JSON.stringify(validateMotion(value))),
  );
}
function updateRobotInstallStatus() {
  const record = library.installed[motion.id];
  const current = record && record.fingerprint === motionFingerprint(motion);
  const freshCatalog = ["ready", "init_required"].includes(live?.snapshot?.state);
  const packages = freshCatalog ? live.snapshot.storedPackages : null;
  const onRobot = Array.isArray(packages)
    ? packages.find((item) => item.package_id === record?.package_id)
    : null;
  let label = "Черновик";
  if (record && !current) label = "Изменено";
  else if (record && Array.isArray(packages) && !onRobot) label = "Нет в роботе";
  else if (record && onRobot && onRobot.crc32 !== record.crc32)
    label = "Изменено в роботе";
  else if (record) label = freshCatalog ? "Установлено" : "Записано ранее";
  $("robot-install-status").textContent = label;
  $("upload-robot-package").innerHTML =
    `${icon("upload")}${record ? "Обновить в роботе" : "Записать в робота"}`;
}
function persist() {
  library.active_id = motion.id;
  const index = library.motions.findIndex((m) => m.id === motion.id);
  if (index === -1) library.motions.push(clone(motion));
  else library.motions[index] = clone(motion);
  const result = store.save(library);
  $("save-status").textContent = result.ok
    ? "Сохранено в браузере"
    : "Сохраните JSON";
  $("save-status").classList.toggle("failed", !result.ok);
  updateRobotInstallStatus();
  if (!result.ok) notify(result.error, true);
  return result.ok;
}
function downloadJsonFile(name, value) {
  const blob = new Blob([JSON.stringify(value, null, 2) + "\n"], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function safeMotionFileName(suffix) {
  const name =
    motion.name.replace(/[^\p{L}\p{N}_ -]/gu, "").slice(0, 60) || "motion";
  return `${name}${suffix}`;
}
function commit(next, keepLive = false) {
  next = validateMotion(next);
  if (!keepLive) live?.stop("Настройки движения изменены.");
  if (JSON.stringify(next) === JSON.stringify(motion)) {
    render();
    return;
  }
  undo.push(clone(motion));
  if (undo.length > 60) undo.shift();
  redo = [];
  motion = next;
  pose = poseAt(motion, time);
  persist();
  render();
}
function setPlaying(value) {
  playing = value;
  $("play").innerHTML = icon(playing ? "pause" : "play");
  $("play").setAttribute(
    "aria-label",
    playing ? "Пауза" : "Воспроизвести в 3D",
  );
  $("play").title = playing ? "Пауза" : "Воспроизвести в 3D";
}
function seek(value) {
  if (!Number.isFinite(value)) throw new Error("Введите время в секундах.");
  live?.stop("Позиция на шкале изменена.");
  setPlaying(false);
  time = Math.max(0, Math.min(motion.duration_ms, Math.round(value)));
  pose = poseAt(motion, time);
  renderPosition();
}
function selectJoint(id) {
  if (!PROFILE.joints.some((j) => j.id === id))
    throw new Error("Неизвестный сустав.");
  if (live?.requestJointSelection?.(id) === false) {
    live.refresh();
    return;
  }
  selectedJoint = id;
  scene?.select(id);
  document
    .querySelectorAll("[data-joint]")
    .forEach((el) => el.classList.toggle("selected", el.dataset.joint === id));
  document
    .querySelectorAll("[data-select]")
    .forEach((el) => el.classList.toggle("selected", el.dataset.select === id));
  $("selection-status").textContent =
    `${PROFILE.joints.find((j) => j.id === id).label} · относительные углы`;
  live?.refresh();
}
function renderLibrary() {
  const list = $("motion-list");
  list.replaceChildren();
  const query = $("search").value.trim().toLocaleLowerCase("ru");
  $("library-count").textContent = library.motions.length;
  const filtered = library.motions.filter((m) =>
    m.name.toLocaleLowerCase("ru").includes(query),
  );
  for (const item of filtered) {
    const row = document.createElement("div");
    row.className = `motion-card${item.id === motion.id ? " current" : ""}`;
    const button = document.createElement("button");
    button.className = "motion-select";
    button.innerHTML = `<span class="motion-glyph">${icon(item.id.startsWith("example") ? "cube" : "play")}</span><span class="motion-info"><strong></strong><small></small></span>`;
    button.querySelector("strong").textContent = item.name;
    button.querySelector("small").textContent =
      `${seconds(item.duration_ms)} с · ${item.keyframes.length} поз`;
    button.onclick = () => {
      live?.stop("Выбрано другое движение.");
      setPlaying(false);
      motion = clone(item);
      time = 0;
      pose = poseAt(motion, time);
      undo = [];
      redo = [];
      persist();
      render();
    };
    row.append(button);
    if (item.id === motion.id) {
      const actions = document.createElement("div");
      actions.className = "card-actions";
      const copy = document.createElement("button");
      copy.className = "text-button";
      copy.innerHTML = `${icon("copy")}Копия`;
      copy.onclick = () =>
        attempt(() =>
          addMotion({
            ...clone(motion),
            id: newId(),
            name: `${motion.name.slice(0, 70)} · копия`,
          }),
        );
      const remove = document.createElement("button");
      remove.className = "icon-button";
      remove.setAttribute("aria-label", "Удалить движение");
      remove.innerHTML = icon("trash");
      remove.onclick = () => {
        $("delete-name").textContent = motion.name;
        $("delete-dialog").showModal();
      };
      actions.append(copy, remove);
      row.append(actions);
    }
    list.append(row);
  }
  if (!filtered.length) {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = "Движения не найдены";
    list.append(empty);
  }
}
function renderTimeline() {
  $("ruler").replaceChildren();
  for (let i = 0; i <= 8; i++) {
    const label = document.createElement("span");
    label.style.left = `${i * 12.5}%`;
    label.textContent = `${+(((motion.duration_ms / 1000) * i) / 8).toFixed(2)}с`;
    $("ruler").append(label);
  }
  $("tracks").replaceChildren();
  for (const joint of PROFILE.joints) {
    const track = document.createElement("div");
    track.className = "track";
    for (const frame of motion.keyframes) {
      const key = document.createElement("button");
      key.className = "keyframe";
      key.style.left = `${(frame.time_ms / motion.duration_ms) * 100}%`;
      key.dataset.time = frame.time_ms;
      key.title = `${joint.label}: ${frame.pose[joint.id]}° · ${seconds(frame.time_ms)} с`;
      key.setAttribute("aria-label", key.title);
      key.onclick = (e) => {
        e.stopPropagation();
        selectJoint(joint.id);
        seek(frame.time_ms);
      };
      track.append(key);
    }
    $("tracks").append(track);
  }
  $("frame-count").textContent = `${motion.keyframes.length} поз`;
  $("scrubber").max = motion.duration_ms;
  $("time-input").max = motion.duration_ms / 1000;
}
function renderPosition() {
  scene?.setPose(live?.displayPose(pose) ?? pose);
  $("time-output").innerHTML =
    `${seconds(time)} <span>/ ${seconds(motion.duration_ms)} с</span>`;
  $("time-input").value = seconds(time);
  $("scrubber").value = time;
  $("playhead").style.left = `${(time / motion.duration_ms) * 100}%`;
  const atFrame = motion.keyframes.some((f) => f.time_ms === time);
  $("delete-frame").disabled = !atFrame || time === 0;
  $("add-frame").innerHTML =
    `${icon("plus")}${atFrame ? "Обновить позу" : "Записать позу"}`;
  document
    .querySelectorAll(".keyframe")
    .forEach((el) =>
      el.classList.toggle("at-time", Number(el.dataset.time) === time),
    );
  for (const joint of PROFILE.joints) {
    const view = live?.inspectorView(joint.id);
    $("range-" + joint.id).value = view?.value ?? +pose[joint.id].toFixed(1);
    const number = $("number-" + joint.id);
    if (!view?.numberEditable || view.disabled || (document.activeElement !== number && number.dataset.liveDraft === undefined))
      number.value = view?.numberEditable ? +view.value.toFixed(1) : (view?.command ?? +pose[joint.id].toFixed(1));
  }
}
function renderVersions() {
  const select = $("versions");
  select.replaceChildren(new Option("История версий", ""));
  (library.revisions[motion.id] || []).forEach((r, i) =>
    select.add(
      new Option(
        `${i + 1}. ${new Date(r.saved_at).toLocaleString("ru", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })}`,
        String(i),
      ),
    ),
  );
  select.disabled = !library.revisions[motion.id]?.length;
}
function render() {
  $("motion-name").value = motion.name;
  $("duration").value = motion.duration_ms / 1000;
  $("interpolation").value = motion.interpolation;
  $("undo").disabled = !undo.length;
  $("redo").disabled = !redo.length;
  renderLibrary();
  renderTimeline();
  renderVersions();
  selectJoint(selectedJoint);
  renderPosition();
  updateRobotInstallStatus();
}
function addMotion(value) {
  if (library.motions.length >= 100)
    throw new Error(
      "В библиотеке уже 100 движений. Скачайте и удалите ненужные.",
    );
  live?.stop("Открыто другое движение.");
  setPlaying(false);
  motion = validateMotion(value);
  time = 0;
  pose = poseAt(motion, time);
  undo = [];
  redo = [];
  $("search").value = "";
  persist();
  render();
}
function recordPose() {
  setPlaying(false);
  pose = validatePose(live?.recordPose?.(pose) ?? pose);
  const next = putPose(motion, time, pose);
  time = Math.round(time);
  commit(next, true);
}

$("new").onclick = () =>
  attempt(() => {
    addMotion(createMotion());
    $("motion-name").focus();
    $("motion-name").select();
  });
$("new-greeting").onclick = () =>
  attempt(() => {
    const template = examples().find((item) => item.id === "example-small-greeting");
    addMotion({ ...template, id: newId() });
  });
$("search").oninput = renderLibrary;
$("motion-name").onchange = () =>
  attempt(() => commit({ ...motion, name: $("motion-name").value }));
$("add-frame").onclick = () =>
  attempt(() => {
    recordPose();
    notify(`Поза записана · ${seconds(time)} с`);
  });
$("neutral").onclick = () =>
  attempt(() => {
    live?.stop("Выбрана нулевая поза в редакторе.");
    pose = zeroPose();
    recordPose();
  });
$("mirror").onclick = () =>
  attempt(() => {
    live?.stop("Выбрано отражение позы в редакторе.");
    pose = mirrorPose(pose);
    recordPose();
  });
$("delete-frame").onclick = () =>
  attempt(() => {
    setPlaying(false);
    if (!time) return;
    commit({
      ...motion,
      keyframes: motion.keyframes.filter((f) => f.time_ms !== time),
    });
  });
$("duration").onchange = () =>
  attempt(() => {
    const duration_ms = Math.round(Number($("duration").value) * 1000);
    if (duration_ms < motion.keyframes.at(-1).time_ms)
      throw new Error(
        "За новой границей есть позы. Сначала удалите их или выберите большую длительность.",
      );
    const next = validateMotion({ ...motion, duration_ms });
    setPlaying(false);
    time = Math.min(time, duration_ms);
    commit(next);
  });
$("interpolation").onchange = () =>
  attempt(() => {
    setPlaying(false);
    commit({ ...motion, interpolation: $("interpolation").value });
  });
$("play").onclick = () => {
  if (live?.editorMode) { live.play(); return; }
  live?.stop("Запущен отдельный 3D-предпросмотр.");
  if (time >= motion.duration_ms) seek(0);
  setPlaying(!playing);
};
$("stop").onclick = () => seek(0);
$("speed").onchange = () => {
  speed = Number($("speed").value);
};
$("loop").onclick = () => {
  loop = !loop;
  $("loop").setAttribute("aria-pressed", loop);
  $("loop").classList.toggle("active", loop);
};
$("scrubber").oninput = () => seek(Number($("scrubber").value));
$("time-input").onchange = () =>
  attempt(() => {
    const n = Number($("time-input").value);
    if (!Number.isFinite(n)) throw new Error("Введите время в секундах.");
    seek(n * 1000);
  });
$("tracks-area").onclick = (e) => {
  const rect = $("tracks-area").getBoundingClientRect();
  seek(((e.clientX - rect.left) / rect.width) * motion.duration_ms);
};
document.querySelectorAll("[data-select]").forEach((el) => {
  el.onclick = () => selectJoint(el.dataset.select);
});
for (const j of PROFILE.joints) {
  const range = $("range-" + j.id);
  const number = $("number-" + j.id);
  $("zero-" + j.id).onclick = () => {
    if (number.disabled) return;
    delete number.dataset.liveDraft;
    number.value = "0";
    number.onchange();
  };
  range.oninput = () => {
    if (live?.enabled) return;
    const value = Number(range.value);
    setPlaying(false);
    time = Math.round(time);
    // Selection refreshes the inspector, so apply the captured value first.
    pose[j.id] = value;
    selectJoint(j.id);
    live?.updateTarget();
    renderPosition();
  };
  range.onchange = () => { if (!live?.enabled) attempt(recordPose); };
  number.oninput = () => { if (live?.editorMode) number.dataset.liveDraft = number.value; };
  number.onkeydown = (event) => {
    if (live?.editorMode && event.code === "Enter") { event.preventDefault(); number.onchange(); }
  };
  number.onblur = () => {
    if (live?.editorMode && number.dataset.liveDraft !== undefined) number.onchange();
  };
  number.onchange = () =>
    attempt(() => {
      if (live?.enabled) {
        const value = number.dataset.liveDraft ?? number.value;
        delete number.dataset.liveDraft;
        live.setInspectorAngle(j.id, value); return;
      }
      const value = Number(number.value);
      if (number.value.trim() === "")
        throw new Error("Введите угол в градусах.");
      pose = validatePose({ ...pose, [j.id]: value });
      setPlaying(false);
      selectJoint(j.id);
      live?.updateTarget();
      recordPose();
    });
}
function travelHistory(backward) {
  live?.stop("Поза изменена из истории.");
  setPlaying(false);
  const source = backward ? undo : redo;
  const destination = backward ? redo : undo;
  if (!source.length) return;
  destination.push(clone(motion));
  motion = source.pop();
  time = Math.min(time, motion.duration_ms);
  pose = poseAt(motion, time);
  persist();
  render();
}
$("undo").onclick = () => travelHistory(true);
$("redo").onclick = () => travelHistory(false);
$("save-version").onclick = () => {
  const revisions = library.revisions[motion.id] || [];
  revisions.push({ saved_at: new Date().toISOString(), motion: clone(motion) });
  library.revisions[motion.id] = revisions.slice(-20);
  const ok = persist();
  renderVersions();
  if (ok) notify("Версия сохранена. Вернуться к ней можно в истории.");
};
$("versions").onchange = () => {
  if ($("versions").value === "") return;
  const snapshot = library.revisions[motion.id][Number($("versions").value)];
  setPlaying(false);
  time = Math.min(time, snapshot.motion.duration_ms);
  commit(clone(snapshot.motion));
  notify("Версия восстановлена. Действие можно отменить.");
};
$("cancel-delete").onclick = () => $("delete-dialog").close();
$("confirm-delete").onclick = () => {
  live?.stop("Движение удалено из редактора.");
  library.motions = library.motions.filter((m) => m.id !== motion.id);
  delete library.revisions[motion.id];
  delete library.installed[motion.id];
  if (!library.motions.length) library.motions.push(createMotion());
  motion = clone(library.motions[0]);
  time = 0;
  pose = poseAt(motion, 0);
  undo = [];
  redo = [];
  setPlaying(false);
  persist();
  render();
  $("delete-dialog").close();
};
$("export").onclick = () => {
  downloadJsonFile(safeMotionFileName(".motion.json"), validateMotion(motion));
  notify(
    "Скачан проект движения. Загрузка в прошивку появится на следующем этапе.",
  );
};
$("export-robot-package").onclick = () =>
  attempt(() => {
    const caps = live?.snapshot?.caps;
    if (!caps)
      throw new Error(
        "Подключите Live, чтобы проверить движение против реальных пределов робота.",
    );
    const result = prepareRobotMotionPackage(motion, caps);
    if (!result.ok) {
      const rest =
        result.issues.length > 1 ? ` Ещё: ${result.issues.length - 1}.` : "";
      throw new Error(
        `Пакет робота не готов: ${result.issues[0].message}${rest}`,
      );
    }
    downloadJsonFile(
      safeMotionFileName(".robot-package-draft.json"),
      result.package,
    );
    notify(
      "Скачан черновик пакета робота. Для записи используйте «Записать в робота».",
    );
  });
$("upload-robot-package").onclick = () =>
  attempt(async () => {
    const caps = live?.snapshot?.caps;
    if (!caps)
      throw new Error(
        "Подключите Live, чтобы проверить движение против реальных пределов робота.",
      );
    const result = prepareRobotMotionPackage(motion, caps);
    if (!result.ok) {
      const rest =
        result.issues.length > 1 ? ` Ещё: ${result.issues.length - 1}.` : "";
      throw new Error(
        `Пакет робота не готов: ${result.issues[0].message}${rest}`,
      );
    }
    const previous = library.installed[motion.id];
    const revision = (previous?.revision || 0) + 1;
    const packageDraft = { ...result.package, revision };
    const upload = prepareRobotPackageUpload(packageDraft);
    await live.uploadPackage(packageDraft);
    if (previous?.motion_snapshot &&
        previous.fingerprint !== motionFingerprint(motion)) {
      const revisions = library.revisions[motion.id] || [];
      revisions.push({ saved_at: new Date().toISOString(), motion: previous.motion_snapshot });
      library.revisions[motion.id] = revisions.slice(-20);
    }
    library.installed[motion.id] = {
      package_id: upload.package_id,
      crc32: upload.crc32,
      fingerprint: motionFingerprint(motion),
      revision,
      motion_snapshot: clone(motion),
    };
    if (!persist()) throw new Error("Пакет записан, но локальная история не сохранилась. Скачайте JSON движения.");
    renderVersions();
    notify(
      "Пакет записан, прочитан и программно проверен без движения приводов.",
    );
  });
$("verify-robot-package").onclick = () =>
  attempt(async () => {
    const caps = live?.snapshot?.caps;
    if (!caps)
      throw new Error(
        "Подключите Live, чтобы проверить сохранённый пакет в роботе.",
      );
    await live.verifyStoredPackage();
    notify(
      "Сохранённый пакет прочитан и программно проверен без движения приводов.",
    );
  });
$("run-robot-package").onclick = () =>
  attempt(async () => {
    const caps = live?.snapshot?.caps;
    if (!caps)
      throw new Error(
        "Подключите Live, чтобы запустить сохранённый пакет в роботе.",
      );
    if (
      !window.confirm(
        "Запустить сохранённый пакет в роботе? Приводы будут двигаться.",
      )
    )
      return;
    await live.runStoredPackageInHardware();
    notify("Сохранённый пакет выполнен в роботе.");
  });
$("delete-robot-package").onclick = () =>
  attempt(async () => {
    const caps = live?.snapshot?.caps;
    if (!caps)
      throw new Error(
        "Подключите Live, чтобы удалить сохранённый пакет в роботе.",
      );
    if (
      !window.confirm(
        "Удалить сохранённый пакет из робота? Движение не запускается.",
      )
    )
      return;
    await live.deleteStoredPackage();
    notify(
      "Сохранённый пакет удалён из робота. Движение не запускалось.",
    );
  });
$("export-original").hidden = !library.originalLibrary;
$("export-original").onclick = () => {
  const url = URL.createObjectURL(new Blob([library.originalLibrary], {type:"application/json"}));
  const link = document.createElement("a");
  link.href = url; link.download = "motion-library-before-arm55.json"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$("import").onclick = () => $("file-input").click();
$("file-input").onchange = async () => {
  const file = $("file-input").files[0];
  if (!file) return;
  try {
    if (file.size > MAX_FILE_BYTES)
      throw new Error("Файл слишком большой. Максимум — 2 МБ.");
    const imported = parseMotion(await file.text());
    addMotion({ ...imported, id: newId() });
    notify("Движение добавлено в библиотеку отдельной копией.");
  } catch (e) {
    notify(e.message, true);
  }
  $("file-input").value = "";
};
const lifecycle = new AbortController();
document.addEventListener(
  "keydown",
  (e) => {
    if (live?.keydown(e)) return;
    if (
      ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) ||
      e.target.isContentEditable ||
      $("delete-dialog").open
    )
      return;
    if (e.code === "Space") {
      e.preventDefault();
      $("play").click();
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      travelHistory(!e.shiftKey);
    }
  },
  { signal: lifecycle.signal },
);

function updateLiveView() {
  for (const joint of PROFILE.joints) {
    const view = live?.inspectorView(joint.id);
    const range = $("range-" + joint.id), number = $("number-" + joint.id);
    range.min = number.min = view?.min ?? joint.min;
    range.max = number.max = view?.max ?? joint.max;
    range.disabled = view?.disabled ?? false;
    number.disabled = view ? (!view.numberEditable || view.disabled) : false;
    if (view?.disabled) delete number.dataset.liveDraft;
    const row = document.querySelector(`.joint-control[data-joint="${joint.id}"]`);
    const ends = row.querySelectorAll(".range-ends span");
    ends[0].textContent = view && !view.available ? "—" : `${range.min}°`;
    const min = Number(range.min), max = Number(range.max);
    const fraction = max > min ? (0 - min) / (max - min) : 0.5;
    // Native range thumb travels within half a 16px thumb at either edge.
    ends[1].style.left = `calc(${100 * fraction}% + ${8 * (1 - 2 * fraction)}px)`;
    ends[1].hidden = Boolean(view && !view.available) || min > 0 || max < 0;
    $("zero-" + joint.id).disabled = number.disabled;
    ends[2].textContent = view && !view.available ? "—" : `${Number(range.max) > 0 ? "+" : ""}${range.max}°`;
    const note = $("live-row-" + joint.id);
    note.hidden = !view;
    note.textContent = view ? (view.available ? `Команда ${view.command}° · цель ${+view.value.toFixed(1)}°. ${view.reason}` : view.reason) : "";
  }
  document.querySelector(".inspector-intro").textContent = live?.enabled
    ? "Откройте сессию слева и выберите угол. Робот плавно дойдёт до цели — удерживать мышь не нужно. STOP завершает сессию."
    : "Измените угол — поза запишется на текущей отметке времени.";
  document.querySelector(".calibration-note").textContent = live?.enabled
    ? "Диапазоны получены от прошивки. 3D показывает подтверждённую команду, измерения угла нет."
    : "Оси и пределы предварительные. Углы отсчитываются от позы модели. Подключения к роботу нет.";
  $("neutral").disabled = $("mirror").disabled = Boolean(live?.enabled);
  renderPosition();
  if (typeof updateRobotInstallStatus === "function") updateRobotInstallStatus();
}
live = mountLivePanel({
  getPose: () => pose,
  getMotion: () => motion,
  getJoint: () => selectedJoint,
  onSelectJoint: selectJoint,
  onUpdate: updateLiveView,
  onPose: (value) => {
    setPlaying(false);
    pose = validatePose(value);
    renderPosition();
  },
  onPlayback: (value) => {
    if (value && time >= motion.duration_ms) {
      time = 0;
      pose = poseAt(motion, 0);
    }
    setPlaying(value);
  },
  notify,
  signal: lifecycle.signal,
});
live.refresh();

async function loadScene() {
  try {
    const { createScene } = await import("./scene.js");
    if (disposed) return;
    scene = createScene($("viewport"), selectJoint, (ok, message) => {
      $("model-status").textContent = ok ? message : "Модель недоступна";
      $("model-error").textContent = message;
      $("model-error").hidden = ok;
    });
    scene.setPose(pose);
    scene.select(selectedJoint);
  } catch {
    if (!disposed) {
      $("model-error").textContent =
        "Не удалось запустить 3D. Проверьте поддержку WebGL в браузере. Редактирование и экспорт поз остаются доступны.";
      $("model-error").hidden = false;
    }
  }
}
loadScene();
$("view-home").onclick = () => scene?.resetView();
$("view-front").onclick = () => scene?.resetView(true);
$("view-grid").onclick = () =>
  $("view-grid").setAttribute("aria-pressed", scene?.toggleGrid() ?? false);
let previous = performance.now();
let animation;
function tick(now) {
  const elapsed = Math.min(now - previous, 100);
  previous = now;
  if (playing) {
    time += elapsed * (live?.running ? live.playbackScale : speed);
    if (time >= motion.duration_ms) {
      if (live?.running) {
        time = motion.duration_ms;
        pose = poseAt(motion, time);
        live.updateTarget();
        live.endTimeline();
      } else {
        time = loop ? time % motion.duration_ms : motion.duration_ms;
        if (!loop) setPlaying(false);
      }
    }
    pose = poseAt(motion, time);
    if (live?.running) live.updateTarget();
    renderPosition();
  }
  animation = requestAnimationFrame(tick);
}
render();
persist();
if (library.error) notify(library.error, true);
if (library.adjusted) notify("Предел опускания рук обновлён до 55°. Исходная библиотека сохранена отдельной копией.");
animation = requestAnimationFrame(tick);
registerPreviewTools(
  document.modelContext,
  {
    read: () => ({
      id: motion.id,
      name: motion.name,
      profile_id: motion.profile_id,
      duration_ms: motion.duration_ms,
      time_ms: Math.round(time),
      pose: { ...pose },
      playing,
      preview_only: true,
      hardware_connected: [
        "init_required",
        "initializing_right_arm",
        "ready",
        "arming",
        "armed",
        "stopping",
      ].includes(live?.snapshot.state),
      live_state: live?.snapshot.state ?? "disconnected",
    }),
    seek: (value) => {
      if (live?.enabled)
        throw new Error(
          "Управление через помощника недоступно в Live. Переключитесь в 3D.",
        );
      seek(value);
    },
  },
  lifecycle.signal,
  () => {
    /* Optional browser capability; editing remains available. */
  },
);
if (import.meta.hot)
  import.meta.hot.dispose(() => {
    disposed = true;
    lifecycle.abort();
    clearTimeout(toastTimer);
    cancelAnimationFrame(animation);
    scene?.dispose();
  });
