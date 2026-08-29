# GOSHA PROJECT MAP

Главная карта входа в текущий контур `Гоша`.

Этот документ нужен, чтобы новый агент не искал наугад:
- где сама платформа;
- где серверный рабочий контур;
- где прошивка;
- где мобильный клиент;
- где лежат голоса, профили, секреты и контрольные документы.

## 1. С чего начинать новый чат

1. Открыть:
   - `<LOCAL_WORKSPACE>/AGENTS.md`
   - `<LOCAL_WORKSPACE>/START_HERE_FOR_NEW_CHAT.md`
   - `<LOCAL_WORKSPACE>/docs/NEW_CHAT_CHECKPOINT_RU.md`
   - `<LOCAL_WORKSPACE>/docs/AGENT_CHECKPOINT_RU.md`
   - `<LOCAL_WORKSPACE>/docs/PROJECT_STATUS_RU.md`
2. Затем открыть этот файл:
   - `<LOCAL_WORKSPACE>/docs/GOSHA_PROJECT_MAP_RU.md`
3. Если задача уже уходит в прошивку, дальше переходить в:
   - `<FIRMWARE_WORKSPACE>/START_HERE_FOR_NEW_CHAT.md`
4. Если задача уходит в Android-клиент, дальше переходить в:
   - `<MOBILE_WORKSPACE>/START_HERE_FOR_NEW_CHAT.md`

## 2. Основные репозитории и их назначение

### `GOSHA_PLATFORM`

- Путь:
  - `<LOCAL_WORKSPACE>`
- Назначение:
  - панель оператора;
  - операторские и мобильные маршруты;
  - внутренний шлюз ИИ-агентов;
  - совместимый голосовой серверный контур;
  - профили ассистента, движков синтеза речи, голоса, памяти, экрана, пробуждения и `MCP`.
- Это текущий главный источник истины для серверного контура `Гоша`.
- Важно для сопровождения:
  - пользовательская панель уже очищена от активных китайских строк;
  - старый музыкальный слот `music-tools` отключён и скрыт;
  - для будущей собственной музыкальной интеграции зарезервирован `gosha.media.stub`.
  - операторский интерфейс уже смещён в понятный сценарий:
    - карточки роботов на главном экране;
    - отдельная рабочая страница выбранного робота;
    - у страницы робота есть собственная прямая ссылка по URL;
    - скрытые служебные панели, которые открываются только по явному действию;
    - прямые прошивочные ссылки и OTA-адреса не выдаются в открытом интерфейсе
  - для роботов на `Платформе Гоша` панель уже умеет показывать прямой след новой прошивки:
    - `last_seen`
    - `board_name`
    - `board_ip`
    - `app_version`
    - `remote_addr`
    - отдельный блок UI: `След устройства в платформе`

### `GOSHA_FIRMWARE`

- Путь:
  - `<FIRMWARE_WORKSPACE>`
- Назначение:
  - собственная прошивка робота;
  - профиль платы `gosha-v1`;
  - экран, слово пробуждения, локальный портал, локальный `WebSocket`, аппаратные выводы.

### `GOSHA_MOBILE`

- Путь:
  - `<MOBILE_WORKSPACE>`
- Git remote:
  - `git@github.com:MaxCorpOrg/GOSHA_MOBILE.git`
- GitHub URL:
  - `https://github.com/MaxCorpOrg/GOSHA_MOBILE`
- Назначение:
  - отдельный Android-клиент `Гоша`;
  - ввод кода подключения;
  - перевод телефона в сеть робота;
  - локальный портал настройки;
  - работа с `mobile API` платформы.

### `AI_ROBOT`

- Путь:
  - `<LEGACY_AI_ROBOT_WORKSPACE>`
- Статус:
  - легаси-контур;
  - источник одноразового импорта и справки;
  - не рабочий корень новых проектов `Гоша`.

## 3. Где живёт серверный контур

### Исходники платформы

- Репозиторий:
  - `<LOCAL_WORKSPACE>`
- Ключевые папки:
  - `platform/`
  - `backend/`
  - `ops/`
  - `docs/`

### Рабочая копия на сервере

- Код:
  - `<SERVER_APP_ROOT>`
- Рабочее состояние:
  - `<SERVER_RUNTIME_ROOT>`
- Службы:
  - `gosha-panel.service`
  - `gosha-agent-gateway.service`
  - `gosha-backend.service`
  - `gosha-observer.timer`

### Как поднять локально на этой машине

- Шлюз:
  - `bash <LOCAL_WORKSPACE>/bin/run_local_gosha_gateway.sh`
- Панель:
  - `bash <LOCAL_WORKSPACE>/bin/run_local_gosha_panel.sh`
  - скрипт сам проверяет Python-модуль `websockets`
- Если запуск идёт прямо из `<HOME_WORKSPACE_ROOT>`, на этой машине также работают совместимые wrapper-скрипты:
  - `bash <LOCAL_BIN>/run_local_gosha_gateway.sh`
  - `bash <LOCAL_BIN>/run_local_gosha_panel.sh`
- Локальный smoke-check всей цепочки:
  - `bash <LOCAL_WORKSPACE>/bin/check_local_gosha_stack.sh`
  - alias:
    - `bash <LOCAL_WORKSPACE>/bin/check_gosha_panel_stack.sh`
- После этого локально открывается:
  - `http://127.0.0.1:18876`
- Важно:
  - локальная панель здесь не висит постоянно как фоновая служба;
  - если `127.0.0.1:18876` не отвечает, сначала нужно поднять эти два процесса.
  - live-probe панели теперь различает не только общее `не отвечает`, но и реальные фазы вроде:
    - разрыв сразу после `initialize`
    - timeout на `tools/call`

### Где лежат серверные секреты

- Локальный секретный каталог разработчика:
  - `<LOCAL_WORKSPACE>/GOSHA_API`
- Текущий локальный файл ключа `DeepSeek`:
  - `<LOCAL_WORKSPACE>/GOSHA_API/GOHA_API_DEEPSEEK.txt`
- Серверный рабочий файл ключей:
  - `<SERVER_PROVIDER_ENV>`

Важно:
- `GOSHA_API/` не должен попадать в git;
- `providers.env` не должен попадать в git;
- ключи поставщиков ИИ нельзя хранить в обычных JSON-профилях.

## 4. Где живут голоса, распознавание и ассистент

### Логика профилей в исходниках

- Составные профили ассистента:
  - `<LOCAL_WORKSPACE>/platform/gosha_assistant_store.py`
- Профили поставщиков ИИ и привязки:
  - `<LOCAL_WORKSPACE>/platform/gosha_agent_store.py`
- Внутренний шлюз ИИ-агентов:
  - `<LOCAL_WORKSPACE>/platform/gosha_agent_gateway.py`
- Панель:
  - `<LOCAL_WORKSPACE>/platform/gui_panel.py`
  - `<LOCAL_WORKSPACE>/platform/panel_index.html`

### Где лежат рабочие профили на сервере

- Папка профилей:
  - `<SERVER_AGENT_PROFILES_ROOT>`
- Подпапки:
  - `<SERVER_AGENT_PROFILES_ROOT>/providers`
  - `<SERVER_AGENT_PROFILES_ROOT>/assistants`
  - `<SERVER_AGENT_PROFILES_ROOT>/tts_engines`
  - `<SERVER_AGENT_PROFILES_ROOT>/voices`
  - `<SERVER_AGENT_PROFILES_ROOT>/memory`
  - `<SERVER_AGENT_PROFILES_ROOT>/mcp_bundles`
  - `<SERVER_AGENT_PROFILES_ROOT>/knowledge`
  - `<SERVER_AGENT_PROFILES_ROOT>/screens`
  - `<SERVER_AGENT_PROFILES_ROOT>/wake`
  - `<SERVER_AGENT_PROFILES_ROOT>/bindings`

### Где смотреть, какой голос реально активен

- Эффективная конфигурация совместимого `backend`:
  - `<SERVER_BACKEND_CONFIG>`

### Где смотреть, какой движок синтеза речи реально выбран

- Каталог профилей движков:
  - `<SERVER_AGENT_PROFILES_ROOT>/tts_engines`
- Живой server-side рендер:
  - `<LOCAL_WORKSPACE>/ops/render_backend_config.py`
- Серверный вызов рендера и этап установки:
  - `<LOCAL_WORKSPACE>/ops/install_server.sh`
- В итоговой конфигурации ищи строки:
  - `requested-tts-engine-profile`
  - `effective-tts-kind`
  - `effective-tts-module`
  - `effective-tts-runtime`
- В операторском API смотри поле:
  - `assistant_control.tts_runtime`
  Оно показывает:
  - какой движок был запрошен;
  - какой живой профиль реально применится;
  - почему сервер временно ушёл в резерв `EdgeTTS`.

### Текущий рабочий голосовой стек

- Распознавание речи:
  - `VoskASR`
- Модель ИИ:
  - `deepseek-v4-flash`
- Синтез речи:
  - `SileroTTS`
- Текущий живой спикер:
  - `kseniya`
- Быстрый резерв:
  - `EdgeTTS`

Важно:
- архитектурно `TTS` уже отделён от голосового профиля;
- текущий живой `EdgeTTS` хранится как профиль `tts-engine-edge-default`;
- следующий русский `TTS` уже встроен в репозиторий как модуль `backend/overrides/silero_local.py`;
- этот модуль уже подключён в `compose`, а серверный рендер умеет собирать для него блок `SileroTTS`;
- текущий живой тестовый профиль на сервере:
  - `tts-engine-silero-live-test`
- живой профиль `tts-engine-silero-prep` пока сознательно остаётся `planned`, поэтому рабочий контур безопасно держится на `EdgeTTS`;
- если у робота явно не записан `ROBOT_BACKEND_MODE`, платформа теперь трактует это как `Платформу Гоша`;
- `platform/add_robot.sh` создаёт новых роботов сразу с `ROBOT_BACKEND_MODE=self_hosted_xiaozhi`.

### Где лежит переопределение синтеза речи

- Репозиторный слой:
  - `<LOCAL_WORKSPACE>/backend/overrides/edge.py`
  - `<LOCAL_WORKSPACE>/backend/overrides/silero_local.py`
- Папка с переопределениями:
  - `<LOCAL_WORKSPACE>/backend/overrides`

### Где лежит логика разделения движка и голоса

- слой составных профилей:
  - `<LOCAL_WORKSPACE>/platform/gosha_assistant_store.py`
- операторские маршруты:
  - `<LOCAL_WORKSPACE>/platform/gui_panel.py`
- интерфейс панели:
  - `<LOCAL_WORKSPACE>/platform/panel_index.html`

### Какие голоса сейчас реально доступны

- `voice-ru-default`
  - Светлана, основной женский голос
- `voice-ru-man`
  - Дмитрий, взрослый мужской голос
- `voice-ru-kid-girl`
  - детский пресет на базе Светланы
- `voice-ru-kid-boy`
  - детский пресет на базе Дмитрия

Важно:
- оба детских варианта пока не нативные детские русские голоса;
- это пресеты скорости и высоты голоса поверх взрослых голосов `EdgeTTS`;
- следующий большой шаг по голосу — смена самого движка синтеза речи на более сильный русский `TTS`.

### Где была старая музыкальная привязка и что с ней теперь

- Логика подписок и пользовательских сервисов:
  - `<LOCAL_WORKSPACE>/platform/gui_panel.py`
- Интерфейс подписок и подписи слотов:
  - `<LOCAL_WORKSPACE>/platform/panel_index.html`
- Что было:
  - переходный слот `music-tools`
  - старый пользовательский сервис `music`
  - пример пользовательского сервиса `music.player`
- Что стало:
  - `music-tools` принудительно отключается в конфигурации
  - чекбокса музыки в панели больше нет
  - внешний чужой музыкальный сервис сейчас не используется
  - нейтральная точка будущего расширения:
    - `gosha.media.stub`

## 5. Где живёт прошивка

### Репозиторий

- Путь:
  - `<FIRMWARE_WORKSPACE>`

### Главные точки входа

- `<FIRMWARE_WORKSPACE>/AGENTS.md`
- `<FIRMWARE_WORKSPACE>/START_HERE_FOR_NEW_CHAT.md`
- `<FIRMWARE_WORKSPACE>/docs/NEW_CHAT_CHECKPOINT_RU.md`
- `<FIRMWARE_WORKSPACE>/docs/FIRMWARE_IMPORT_CHECKPOINT_RU.md`
- `<FIRMWARE_WORKSPACE>/docs/HARDWARE_MANIFEST_RU.md`
- `<FIRMWARE_WORKSPACE>/docs/PIN_MAP_RU.md`

### Где исходники платы `Гоша`

- Сборочный корень:
  - `<FIRMWARE_WORKSPACE>/firmware`
- Профиль платы:
  - `<FIRMWARE_WORKSPACE>/firmware/main/boards/gosha-v1`
- Где проходила очистка пользовательских китайских строк:
  - `<FIRMWARE_WORKSPACE>/firmware/main/boards/gosha-v1/otto_controller.cc`
  - `<FIRMWARE_WORKSPACE>/firmware/main/boards/gosha-v1/otto_robot.cc`
  - `<FIRMWARE_WORKSPACE>/firmware/main/boards/gosha-v1/otto_emoji_display.cc`
  - `<FIRMWARE_WORKSPACE>/firmware/main/boards/gosha-v1/power_manager.h`
  - `<FIRMWARE_WORKSPACE>/firmware/main/boards/common/press_to_talk_mcp_tool.cc`
- Важно:
  - после этого китайские символы в активном пользовательском слое больше не должны доходить до экрана, подсказок и `MCP`-описаний;
  - оставшиеся CJK-символы в репозитории сейчас относятся в основном к комментариям, неактивным платам и `zh-*` ресурсам.

### Где русский пользовательский слой прошивки

- Русская локализация:
  - `<FIRMWARE_WORKSPACE>/firmware/main/assets/locales/ru-RU`

### Где слово пробуждения и аудиоконтур

- Аудио:
  - `<FIRMWARE_WORKSPACE>/firmware/main/audio`
- Слово пробуждения:
  - `<FIRMWARE_WORKSPACE>/firmware/main/audio/wake_words`

### Где лежит собранная прошивка

- Основной merged-образ:
  - `<FIRMWARE_WORKSPACE>/firmware/build/merged-binary.bin`
- Выпускной архив:
  - `<FIRMWARE_WORKSPACE>/firmware/releases/v2.2.2_gosha-v1.zip`

## 6. Где живёт Android-клиент

### Репозиторий

- Путь:
  - `<MOBILE_WORKSPACE>`

### Главные точки входа

- `<MOBILE_WORKSPACE>/AGENTS.md`
- `<MOBILE_WORKSPACE>/START_HERE_FOR_NEW_CHAT.md`
- `<MOBILE_WORKSPACE>/docs/NEW_CHAT_CHECKPOINT_RU.md`
- `<MOBILE_WORKSPACE>/docs/PROJECT_STATUS_RU.md`

### Где лежит код приложения

- Android-код:
  - `<MOBILE_WORKSPACE>/app/src/main`
- Текущий исходный пакет в дереве файлов пока ещё лежит в старом пути:
  - `<MOBILE_WORKSPACE>/app/src/main/java/com/maxcorp/edgeconnector`

Важно:
- это не старое приложение как продукт;
- это унаследованный путь исходников после одноразового копирования;
- канонический новый `applicationId` уже:
  - `com.maxcorp.gosha.mobile`

### Где лежит готовый отладочный `APK`

- `<MOBILE_WORKSPACE>/app/build/outputs/apk/client/debug/app-client-debug.apk`

## 7. Какие документы считать каноническими

### Для платформы

- Общая карта:
  - `<LOCAL_WORKSPACE>/docs/GOSHA_PROJECT_MAP_RU.md`
- Канонический контракт общего состояния робота, мобильного приложения и панели:
  - `<LOCAL_WORKSPACE>/docs/GOSHA_RUNTIME_TRIANGLE_CONTRACT_RU.md`
- Короткая точка входа:
  - `<LOCAL_WORKSPACE>/docs/NEW_CHAT_CHECKPOINT_RU.md`
- Подробная рабочая точка:
  - `<LOCAL_WORKSPACE>/docs/AGENT_CHECKPOINT_RU.md`
- Живое состояние:
  - `<LOCAL_WORKSPACE>/docs/PROJECT_STATUS_RU.md`

### Для прошивки

- Короткая точка входа:
  - `<FIRMWARE_WORKSPACE>/docs/NEW_CHAT_CHECKPOINT_RU.md`
- Подробная рабочая точка:
  - `<FIRMWARE_WORKSPACE>/docs/AGENT_CHECKPOINT_RU.md`
- Живое состояние:
  - `<FIRMWARE_WORKSPACE>/docs/PROJECT_STATUS_RU.md`

### Для мобильного клиента

- Короткая точка входа:
  - `<MOBILE_WORKSPACE>/docs/NEW_CHAT_CHECKPOINT_RU.md`
- Подробная рабочая точка:
  - `<MOBILE_WORKSPACE>/docs/AGENT_CHECKPOINT_RU.md`
- Живое состояние:
  - `<MOBILE_WORKSPACE>/docs/PROJECT_STATUS_RU.md`

## 8. Текущее состояние на 2026-05-15

- Платформа `Гоша` жива на сервере:
  - панель отвечает;
  - шлюз ИИ-агентов отвечает;
  - голосовой `WebSocket` отвечает;
  - `DeepSeek`, `VoskASR` и `EdgeTTS` уже работают в одном контуре.
- Робот `gosha-main`:
  - откликается на слово `Гоша`;
  - распознаёт русскую речь;
  - отвечает голосом.
- Мобильный клиент:
  - уже отделён в самостоятельный проект;
  - видит робота и может довести телефон до сети робота;
  - ещё требует доводки локального портала и плавности сценария подключения.
- Прошивка:
  - уже собрана, прошита и подключается к платформе;
  - слово пробуждения `Гоша` уже включено;
  - текущий прошитый шаг по чувствительности слова пробуждения — `38`;
  - ещё требует доводки качества распознавания, ложных срабатываний и локального портала.

## 9. Что делать следующим пакетом

1. Закрыть документную контрольную точку и использовать этот файл как главный ориентир нового чата.
2. Для голоса решить следующий архитектурный шаг:
   - искать новый движок синтеза речи с более сильными русскими голосами.
3. Для прошивки отдельно разобрать:
   - ложные срабатывания;
   - дальнее распознавание;
   - влияние динамика и микрофона;
   - локальный портал `<LOCAL_DEVICE_PORTAL_HOST>`.
4. Для мобильного клиента дополировать:
   - сценарий возврата из режима точки доступа;
   - устойчивое открытие локального портала;
   - переход в главное меню без лишних перезапусков.
