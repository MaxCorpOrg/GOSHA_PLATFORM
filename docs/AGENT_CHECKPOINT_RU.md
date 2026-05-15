# AGENT CHECKPOINT

## Что это за проект

`GOSHA_PLATFORM` — отдельная масштабируемая платформа `Гоша` для роботов, панели оператора и совместимого голосового серверного узла.

## Как писать новые записи

- Пиши отчёты, планы и контрольные точки русским техническим языком по правилам из `../AGENTS.md`.
- Не используй необъяснённый англо-русский суржик в обычном тексте.

## Текущая рабочая точка

- Ветка:
  - `agent/bootstrap-gosha`
- Последний важный кодовый коммит этого цикла:
  - `f5bbc73` `Добавить панель ассистента Гоша и серверный прокси`
- На сервере рабочая копия `/opt/gosha_platform/app` уже выровнена до этого состояния.

## Что уже работает

- Панель на `151.241.228.232:18876`
- Внутренний шлюз ИИ-агентов на `127.0.0.1:18110`
- Совместимый голосовой `WebSocket` на `151.241.228.232:18080/xiaozhi/v1/`
- Привязка устройства `pending -> claim -> activate`
- Новый верхний сценарий привязки робота в панели
- Новая панель управления ассистентом:
  - поставщики ИИ
  - ассистенты
  - голоса
  - память
  - `MCP`
  - экран и лица
  - пробуждение
  - база знаний
  - привязка к роботу
- Новые маршруты панели:
  - `GET/POST /api/operator/assistant-profiles`
  - `GET/POST /api/operator/voice-profiles`
  - `GET/POST /api/operator/memory-profiles`
  - `GET/POST /api/operator/mcp-bundles`
  - `GET/POST /api/operator/knowledge-profiles`
  - `GET/POST /api/operator/screen-profiles`
  - `GET/POST /api/operator/wake-profiles`
  - `GET/POST /api/operator/robots/<robot_id>/assistant-config`
  - `GET /api/operator/assistant-control/catalog`
- Внутренний `OpenAI`-совместимый прокси панели:
  - `GET /api/internal/openai/v1/models`
  - `POST /api/internal/openai/v1/chat/completions`
- На сервере уже есть базовый набор профилей и привязка `gosha-main` к профилю `assistant-gosha-default`.

## Главный незакрытый блокер

- Реальный голосовой ответ ещё упирается в отсутствие ключа поставщика ИИ в:
  - `/opt/gosha_platform/runtime/env/providers.env`
- Пока этот файл не заполнен, совместимый `backend` и внутренний прокси живы, но вызов модели возвращает ошибку аутентификации поставщика.

## Ближайший приоритет

1. Заполнить `providers.env` рабочим ключом:
   - `OPENAI_API_KEY=...`
   - или `DEEPSEEK_API_KEY=...`
2. Перезапустить лёгкий контур:
   - `bash /opt/gosha_platform/app/ops/install_server.sh --phase panel`
3. Проверить:
   - `POST /api/internal/openai/v1/chat/completions`
   - что `gosha-main` перестал молчать
   - что в панели исчезло предупреждение о секрете поставщика
4. Потом переходить к второй очереди:
   - применение экранных профилей
   - применение пробуждения
   - рабочая база знаний
