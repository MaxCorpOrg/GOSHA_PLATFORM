# AGENTS.md

Это главный entry-файл для нового проекта `GOSHA_PLATFORM`.

## Как входить в проект

1. Прочитай `START_HERE_FOR_NEW_CHAT.md`.
2. Затем прочитай:
   - `docs/AGENT_CHECKPOINT_RU.md`
   - `docs/PROJECT_STATUS_RU.md`
3. Если работа идёт по конкретной папке, открой и её локальный `AGENTS.md`.

## Главные правила

- `GOSHA_PLATFORM` живёт отдельно от `AI_ROBOT`.
- Не смешивай runtime, systemd units и env-файлы `GOSHA` с `/opt/ai_robot`.
- В git идут только исходники, документация, deploy/runbook и tracked-скрипты.
- Всё локальное состояние должно жить под `local_only/` и не попадать в коммиты.
- Перед заметными изменениями проверь `git status --short --branch` и последний checkpoint.

## Что обновлять после значимой задачи

- `docs/PROJECT_STATUS_RU.md`
- `docs/AGENT_CHECKPOINT_RU.md`
- локальный `AGENTS.md`, если изменилась зона ответственности папки

## Важные зоны

- `platform/` — панель, mobile/operator API и self-hosted XiaoZhi gateway logic
- `backend/` — compose и env-template для XiaoZhi-compatible backend
- `ops/` — server install, observer и systemd units
- `bin/` — локальные вспомогательные скрипты разработчика
- `docs/` — checkpoint, runbook и handoff

