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
   - `/home/max/GOSHA_PLATFORM/AGENTS.md`
   - `/home/max/GOSHA_PLATFORM/START_HERE_FOR_NEW_CHAT.md`
   - `/home/max/GOSHA_PLATFORM/docs/NEW_CHAT_CHECKPOINT_RU.md`
   - `/home/max/GOSHA_PLATFORM/docs/AGENT_CHECKPOINT_RU.md`
   - `/home/max/GOSHA_PLATFORM/docs/PROJECT_STATUS_RU.md`
2. Затем открыть этот файл:
   - `/home/max/GOSHA_PLATFORM/docs/GOSHA_PROJECT_MAP_RU.md`
3. Если задача уже уходит в прошивку, дальше переходить в:
   - `/home/max/GOSHA_FIRMWARE/START_HERE_FOR_NEW_CHAT.md`
4. Если задача уходит в Android-клиент, дальше переходить в:
   - `/home/max/GOSHA_MOBILE/START_HERE_FOR_NEW_CHAT.md`

## 2. Основные репозитории и их назначение

### `GOSHA_PLATFORM`

- Путь:
  - `/home/max/GOSHA_PLATFORM`
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
  - `/home/max/GOSHA_FIRMWARE`
- Назначение:
  - собственная прошивка робота;
  - профиль платы `gosha-v1`;
  - экран, слово пробуждения, локальный портал, локальный `WebSocket`, аппаратные выводы.

### `GOSHA_MOBILE`

- Путь:
  - `/home/max/GOSHA_MOBILE`
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
  - `/home/max/MAX_CORP_CORE/AI_ROBOT`
- Статус:
  - легаси-контур;
  - источник одноразового импорта и справки;
  - не рабочий корень новых проектов `Гоша`.

## 3. Где живёт серверный контур

### Исходники платформы

- Репозиторий:
  - `/home/max/GOSHA_PLATFORM`
- Ключевые папки:
  - `platform/`
  - `backend/`
  - `ops/`
  - `docs/`

### Рабочая копия на сервере

- Код:
  - `/opt/gosha_platform/app`
- Рабочее состояние:
  - `/opt/gosha_platform/runtime`
- Службы:
  - `gosha-panel.service`
  - `gosha-agent-gateway.service`
  - `gosha-backend.service`
  - `gosha-observer.timer`

### Как поднять локально на этой машине

- Шлюз:
  - `bash /home/max/GOSHA_PLATFORM/bin/run_local_gosha_gateway.sh`
- Панель:
  - `bash /home/max/GOSHA_PLATFORM/bin/run_local_gosha_panel.sh`
- Если запуск идёт прямо из `/home/max`, на этой машине также работают совместимые wrapper-скрипты:
  - `bash /home/max/bin/run_local_gosha_gateway.sh`
  - `bash /home/max/bin/run_local_gosha_panel.sh`
- Локальный smoke-check всей цепочки:
  - `bash /home/max/GOSHA_PLATFORM/bin/check_local_gosha_stack.sh`
- После этого локально открывается:
  - `http://127.0.0.1:18876`
- Важно:
  - локальная панель здесь не висит постоянно как фоновая служба;
  - если `127.0.0.1:18876` не отвечает, сначала нужно поднять эти два процесса.

### Где лежат серверные секреты

- Локальный секретный каталог разработчика:
  - `/home/max/GOSHA_PLATFORM/GOSHA_API`
- Текущий локальный файл ключа `DeepSeek`:
  - `/home/max/GOSHA_PLATFORM/GOSHA_API/GOHA_API_DEEPSEEK.txt`
- Серверный рабочий файл ключей:
  - `/opt/gosha_platform/runtime/env/providers.env`

Важно:
- `GOSHA_API/` не должен попадать в git;
- `providers.env` не должен попадать в git;
- ключи поставщиков ИИ нельзя хранить в обычных JSON-профилях.

## 4. Где живут голоса, распознавание и ассистент

### Логика профилей в исходниках

- Составные профили ассистента:
  - `/home/max/GOSHA_PLATFORM/platform/gosha_assistant_store.py`
- Профили поставщиков ИИ и привязки:
  - `/home/max/GOSHA_PLATFORM/platform/gosha_agent_store.py`
- Внутренний шлюз ИИ-агентов:
  - `/home/max/GOSHA_PLATFORM/platform/gosha_agent_gateway.py`
- Панель:
  - `/home/max/GOSHA_PLATFORM/platform/gui_panel.py`
  - `/home/max/GOSHA_PLATFORM/platform/panel_index.html`

### Где лежат рабочие профили на сервере

- Папка профилей:
  - `/opt/gosha_platform/runtime/app_root/agents`
- Подпапки:
  - `/opt/gosha_platform/runtime/app_root/agents/providers`
  - `/opt/gosha_platform/runtime/app_root/agents/assistants`
  - `/opt/gosha_platform/runtime/app_root/agents/tts_engines`
  - `/opt/gosha_platform/runtime/app_root/agents/voices`
  - `/opt/gosha_platform/runtime/app_root/agents/memory`
  - `/opt/gosha_platform/runtime/app_root/agents/mcp_bundles`
  - `/opt/gosha_platform/runtime/app_root/agents/knowledge`
  - `/opt/gosha_platform/runtime/app_root/agents/screens`
  - `/opt/gosha_platform/runtime/app_root/agents/wake`
  - `/opt/gosha_platform/runtime/app_root/agents/bindings`

### Где смотреть, какой голос реально активен

- Эффективная конфигурация совместимого `backend`:
  - `/opt/gosha_platform/runtime/app_root/selfhost_xiaozhi/backend/data/.config.yaml`

### Где смотреть, какой движок синтеза речи реально выбран

- Каталог профилей движков:
  - `/opt/gosha_platform/runtime/app_root/agents/tts_engines`
- Живой server-side рендер:
  - `/home/max/GOSHA_PLATFORM/ops/render_backend_config.py`
- Серверный вызов рендера и этап установки:
  - `/home/max/GOSHA_PLATFORM/ops/install_server.sh`
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
  - `/home/max/GOSHA_PLATFORM/backend/overrides/edge.py`
  - `/home/max/GOSHA_PLATFORM/backend/overrides/silero_local.py`
- Папка с переопределениями:
  - `/home/max/GOSHA_PLATFORM/backend/overrides`

### Где лежит логика разделения движка и голоса

- слой составных профилей:
  - `/home/max/GOSHA_PLATFORM/platform/gosha_assistant_store.py`
- операторские маршруты:
  - `/home/max/GOSHA_PLATFORM/platform/gui_panel.py`
- интерфейс панели:
  - `/home/max/GOSHA_PLATFORM/platform/panel_index.html`

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
  - `/home/max/GOSHA_PLATFORM/platform/gui_panel.py`
- Интерфейс подписок и подписи слотов:
  - `/home/max/GOSHA_PLATFORM/platform/panel_index.html`
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
  - `/home/max/GOSHA_FIRMWARE`

### Главные точки входа

- `/home/max/GOSHA_FIRMWARE/AGENTS.md`
- `/home/max/GOSHA_FIRMWARE/START_HERE_FOR_NEW_CHAT.md`
- `/home/max/GOSHA_FIRMWARE/docs/NEW_CHAT_CHECKPOINT_RU.md`
- `/home/max/GOSHA_FIRMWARE/docs/FIRMWARE_IMPORT_CHECKPOINT_RU.md`
- `/home/max/GOSHA_FIRMWARE/docs/HARDWARE_MANIFEST_RU.md`
- `/home/max/GOSHA_FIRMWARE/docs/PIN_MAP_RU.md`

### Где исходники платы `Гоша`

- Сборочный корень:
  - `/home/max/GOSHA_FIRMWARE/firmware`
- Профиль платы:
  - `/home/max/GOSHA_FIRMWARE/firmware/main/boards/gosha-v1`
- Где проходила очистка пользовательских китайских строк:
  - `/home/max/GOSHA_FIRMWARE/firmware/main/boards/gosha-v1/otto_controller.cc`
  - `/home/max/GOSHA_FIRMWARE/firmware/main/boards/gosha-v1/otto_robot.cc`
  - `/home/max/GOSHA_FIRMWARE/firmware/main/boards/gosha-v1/otto_emoji_display.cc`
  - `/home/max/GOSHA_FIRMWARE/firmware/main/boards/gosha-v1/power_manager.h`
  - `/home/max/GOSHA_FIRMWARE/firmware/main/boards/common/press_to_talk_mcp_tool.cc`
- Важно:
  - после этого китайские символы в активном пользовательском слое больше не должны доходить до экрана, подсказок и `MCP`-описаний;
  - оставшиеся CJK-символы в репозитории сейчас относятся в основном к комментариям, неактивным платам и `zh-*` ресурсам.

### Где русский пользовательский слой прошивки

- Русская локализация:
  - `/home/max/GOSHA_FIRMWARE/firmware/main/assets/locales/ru-RU`

### Где слово пробуждения и аудиоконтур

- Аудио:
  - `/home/max/GOSHA_FIRMWARE/firmware/main/audio`
- Слово пробуждения:
  - `/home/max/GOSHA_FIRMWARE/firmware/main/audio/wake_words`

### Где лежит собранная прошивка

- Основной merged-образ:
  - `/home/max/GOSHA_FIRMWARE/firmware/build/merged-binary.bin`
- Выпускной архив:
  - `/home/max/GOSHA_FIRMWARE/firmware/releases/v2.2.2_gosha-v1.zip`

## 6. Где живёт Android-клиент

### Репозиторий

- Путь:
  - `/home/max/GOSHA_MOBILE`

### Главные точки входа

- `/home/max/GOSHA_MOBILE/AGENTS.md`
- `/home/max/GOSHA_MOBILE/START_HERE_FOR_NEW_CHAT.md`
- `/home/max/GOSHA_MOBILE/docs/NEW_CHAT_CHECKPOINT_RU.md`
- `/home/max/GOSHA_MOBILE/docs/PROJECT_STATUS_RU.md`

### Где лежит код приложения

- Android-код:
  - `/home/max/GOSHA_MOBILE/app/src/main`
- Текущий исходный пакет в дереве файлов пока ещё лежит в старом пути:
  - `/home/max/GOSHA_MOBILE/app/src/main/java/com/maxcorp/edgeconnector`

Важно:
- это не старое приложение как продукт;
- это унаследованный путь исходников после одноразового копирования;
- канонический новый `applicationId` уже:
  - `com.maxcorp.gosha.mobile`

### Где лежит готовый отладочный `APK`

- `/home/max/GOSHA_MOBILE/app/build/outputs/apk/client/debug/app-client-debug.apk`

## 7. Какие документы считать каноническими

### Для платформы

- Общая карта:
  - `/home/max/GOSHA_PLATFORM/docs/GOSHA_PROJECT_MAP_RU.md`
- Короткая точка входа:
  - `/home/max/GOSHA_PLATFORM/docs/NEW_CHAT_CHECKPOINT_RU.md`
- Подробная рабочая точка:
  - `/home/max/GOSHA_PLATFORM/docs/AGENT_CHECKPOINT_RU.md`
- Живое состояние:
  - `/home/max/GOSHA_PLATFORM/docs/PROJECT_STATUS_RU.md`

### Для прошивки

- Короткая точка входа:
  - `/home/max/GOSHA_FIRMWARE/docs/NEW_CHAT_CHECKPOINT_RU.md`
- Подробная рабочая точка:
  - `/home/max/GOSHA_FIRMWARE/docs/AGENT_CHECKPOINT_RU.md`
- Живое состояние:
  - `/home/max/GOSHA_FIRMWARE/docs/PROJECT_STATUS_RU.md`

### Для мобильного клиента

- Короткая точка входа:
  - `/home/max/GOSHA_MOBILE/docs/NEW_CHAT_CHECKPOINT_RU.md`
- Подробная рабочая точка:
  - `/home/max/GOSHA_MOBILE/docs/AGENT_CHECKPOINT_RU.md`
- Живое состояние:
  - `/home/max/GOSHA_MOBILE/docs/PROJECT_STATUS_RU.md`

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
   - локальный портал `192.168.4.1`.
4. Для мобильного клиента дополировать:
   - сценарий возврата из режима точки доступа;
   - устойчивое открытие локального портала;
   - переход в главное меню без лишних перезапусков.
