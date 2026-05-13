# AGENT CHECKPOINT

## Что это за проект

`GOSHA_PLATFORM` — отдельный self-hosted staging-контур для `Гоша`, который больше не должен зависеть от `xiaozhi.me` как от обязательной платформы привязки.

## Текущая точка

- Локальный panel/gateway baseline уже существует.
- Папка уже превращена в самостоятельный git-репозиторий.
- Ветка публикации сейчас:
  - `agent/bootstrap-gosha`
- Первый push в GitHub уже сделан.
- Короткая каноническая точка входа для следующего агента теперь вынесена в:
  - `docs/NEW_CHAT_CHECKPOINT_RU.md`
- Для сервера выбран отдельный корень `/opt/gosha_platform`.
- Для staging-подъёма выбраны:
  - panel/API: `151.241.228.232:18876`
  - websocket backend: `151.241.228.232:18080`
- Server checkout уже существует в `/opt/gosha_platform/app`, но backend deploy сознательно paused из-за большого image pull и медленного канала.

## Главные ограничения

- Не трогать `/opt/ai_robot` деструктивно.
- Не публиковать `local_only/`.
- Не возвращать жёсткие зависимости на `xiaozhi.me`.

## Ближайший приоритет

- При нормальном канале вернуться к server deploy:
  - завершить pull backend image
  - включить `gosha-backend.service`
  - после этого включить `gosha-panel.service`
  - затем включить `gosha-observer.timer`
