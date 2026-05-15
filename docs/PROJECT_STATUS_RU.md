# PROJECT STATUS

## Сделано

- `GOSHA_PLATFORM` ведётся как отдельный масштабируемый проект вне `AI_ROBOT`.
- Внешний пользовательский слой переведён на бренд `Гоша`.
- Сохранены совместимые технические псевдонимы:
  - `/xiaozhi/ota/`
  - `/xiaozhi/ota/activate`
  - `/xiaozhi/v1/`
- Добавлены собственные продуктовые OTA-маршруты:
  - `/gosha/ota/`
  - `/gosha/ota/activate`
- Поднят отдельный серверный контур под `/opt/gosha_platform`.
- На сервере активны:
  - `gosha-agent-gateway.service`
  - `gosha-panel.service`
  - `gosha-observer.timer`
  - `gosha-backend.service`
- На сервере подтверждены живые порты:
  - `127.0.0.1:18110` — внутренний шлюз ИИ-агентов
  - `0.0.0.0:18876` — панель и операторские маршруты
  - `0.0.0.0:18080` — совместимый голосовой `WebSocket`
- Найдена и закрыта причина прежнего отказа `backend`:
  - не хватало `data/.config.yaml`
  - прежнее монтирование каталога `models/` перекрывало встроенные модели совместимого узла
  - в установочный сценарий добавлены загрузка `SenseVoiceSmall` и генерация серверной конфигурации
- Реализована первая версия панели управления ассистентом `Гоша`.
- Добавлен новый слой составных профилей:
  - `platform/gosha_assistant_store.py`
- Сохранён совместимый слой профилей поставщиков ИИ:
  - `platform/gosha_agent_store.py`
- Расширен внутренний шлюз ИИ-агентов:
  - умеет разрешать составную конфигурацию робота
  - умеет брать профиль по умолчанию, если `robot_id` не передан
- В панель добавлены новые операторские маршруты:
  - `GET/POST /api/operator/assistant-profiles`
  - `GET/POST /api/operator/voice-profiles`
  - `GET/POST /api/operator/memory-profiles`
  - `GET/POST /api/operator/mcp-bundles`
  - `GET/POST /api/operator/knowledge-profiles`
  - `GET/POST /api/operator/screen-profiles`
  - `GET/POST /api/operator/wake-profiles`
  - `GET/POST /api/operator/robots/<robot_id>/assistant-config`
  - `GET /api/operator/assistant-control/catalog`
- В панели появился большой блок `Управление ассистентом Гоша`:
  - поставщики ИИ
  - ассистенты
  - голоса
  - память
  - инструменты `MCP`
  - экран и лица
  - пробуждение
  - база знаний
  - привязка к роботу
- Для `Экран и лица`, `Пробуждение` и `База знаний` панель честно показывает, что это отложенные части, требующие отдельной синхронизации или следующего серверного контура.
- Добавлен внутренний `OpenAI`-совместимый прокси панели:
  - `GET /api/internal/openai/v1/models`
  - `POST /api/internal/openai/v1/chat/completions`
- Этот прокси использует внутренний токен и обращается к `gosha-agent-gateway`, не выставляя сам шлюз наружу.
- На сервере уже посеян базовый набор профилей:
  - `assistant-gosha-default`
  - `voice-ru-default`
  - `memory-short-default`
  - `mcp-basic-default`
  - `screen-gosha-default`
  - `wake-gosha-default`
- `gosha-main` уже привязан к составному профилю ассистента.
- Локально подтверждены:
  - `python3 -m py_compile` для изменённых файлов
  - `bash -n ops/install_server.sh`
  - проверка встроенного JavaScript панели
  - создание профилей и чтение составной конфигурации робота
  - работа внутреннего маршрута `/api/internal/openai/v1/models`
- На сервере подтверждены:
  - `GET /api/operator/selfhost-xiaozhi` -> `200`
  - `GET /api/mobile/plans` -> `200`
  - `GET /api/operator/assistant-control/catalog` -> `200`
  - `GET http://127.0.0.1:18110/healthz` -> `200`
  - `GET http://127.0.0.1:18876/gosha/ota/` -> `200`
  - `GET http://127.0.0.1:18876/api/internal/openai/v1/models` -> `200` при внутреннем токене
  - сценарий `pending -> claim -> activate`

## На чем остановились

- Совместимый голосовой узел уже поднят, но живой ответ ассистента ещё блокируется секретом поставщика ИИ.
- На сервере сейчас нет рабочего значения для:
  - `OPENAI_API_KEY`
  - или `DEEPSEEK_API_KEY`
  в файле `/opt/gosha_platform/runtime/env/providers.env`.
- Поэтому:
  - панель жива
  - `backend` жив
  - внутренний прокси жив
  - но реальный запрос к модели возвращает ошибку аутентификации поставщика
- Экранные профили, имя пробуждения и база знаний уже отображаются в панели, но ещё не доведены до реального применения на устройстве.
- Голосовой путь `/xiaozhi/v1/` пока сознательно оставлен как совместимый технический путь.

## Что делать дальше

1. На сервере заполнить `/opt/gosha_platform/runtime/env/providers.env` рабочим ключом хотя бы одного поставщика:
   - `OPENAI_API_KEY=...`
   - или `DEEPSEEK_API_KEY=...`
2. После этого перезапустить лёгкий контур:
   - `cd /opt/gosha_platform/app`
   - `bash ops/install_server.sh --phase panel`
3. Проверить:
   - `POST /api/internal/openai/v1/chat/completions`
   - что `gosha-main` перестал молчать и отвечает голосом
   - что карточка робота в панели показывает составной профиль без критичной ошибки по ключу
4. Затем довести вторую очередь панели:
   - применение экранных профилей
   - применение профилей пробуждения
   - загрузку и индексацию базы знаний
5. После стабилизации голосового контура перейти к следующему шагу в `/home/max/GOSHA_FIRMWARE`:
   - подтвердить русский пользовательский слой на устройстве
   - подтвердить OTA через `/gosha/ota/`
   - отдельно решить вопрос безопасного псевдонима `/gosha/v1/`
