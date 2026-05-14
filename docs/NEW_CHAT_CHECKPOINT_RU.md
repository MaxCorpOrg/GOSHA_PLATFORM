# NEW CHAT CHECKPOINT

Короткая контрольная точка для следующего агента в `GOSHA_PLATFORM`.

## Сначала прочитать

1. `../AGENTS.md`
2. `NEW_CHAT_CHECKPOINT_RU.md`
3. `AGENT_CHECKPOINT_RU.md`
4. `PROJECT_STATUS_RU.md`
5. Если задача про сервер:
   - `GOSHA_SERVER_DEPLOY_RU.md`
   - `../ops/AGENTS.md`
6. Если задача про панель и совместимый шлюз платформы `Гоша`:
   - `GOSHA_LOCAL_SELFHOST_RUNBOOK_RU.md`
   - `../platform/AGENTS.md`

## Как писать новые записи

- Все новые контрольные точки, утренние брифы, планы и пояснения пиши русским техническим языком по правилам из `../AGENTS.md`.
- Не используй необъяснённый английский жаргон в обычном тексте.
- Английский оставляй только для команд, путей, имён файлов, веток, коммитов и кода.

## Последняя зафиксированная точка

- Ветка: `agent/bootstrap-gosha`
- Актуальный коммит смотреть через:
  - `git log --oneline -5`
- GitHub-репозиторий: `git@github.com:MaxCorpOrg/GOSHA_PLATFORM.git`

## Что переделали с прошлой точки

- Вынесли `GOSHA_PLATFORM` в отдельный самостоятельный git-репозиторий.
- Разделили публикуемый слой и локальный слой:
  - отслеживаемые каталоги: `platform/`, `backend/`, `ops/`, `docs/`, `bin/`
  - локальный слой: `local_only/`
- Добавили `AGENTS.md` по рабочим папкам и оформили отдельный маршрут входа для следующего агента.
- Подготовили отдельный слой развёртывания на сервере под `/opt/gosha_platform`, не смешивая его с `AI_ROBOT`.

## Что уже сделано

- Первая отправка изменений в `origin/agent/bootstrap-gosha` выполнена.
- На сервере уже созданы:
  - `/opt/gosha_platform/app`
  - `/opt/gosha_platform/runtime/app_root`
  - `/opt/gosha_platform/runtime/env`
  - `/opt/gosha_platform/runtime/reports`
- Локально подтверждены:
  - `GET /api/mobile/plans`
  - `GET /api/operator/selfhost-xiaozhi`
  - `pending -> claim -> activate=200`
  - `python3 platform/check_gosha_mobile_contract.py --base-url http://127.0.0.1:18876`
- В правилах проекта закреплён единый русский технический язык для отчётов, планов и контрольных точек.

## Где остановились

- Развёртывание на сервере остановлено сознательно, потому что получение образов `backend` слишком тяжёлое для текущей скорости канала.
- На сервере сейчас:
  - `gosha-backend.service` -> `failed/disabled`
  - `gosha-panel.service` -> `inactive/disabled`
  - `gosha-observer.timer` -> `inactive/disabled`
- Ничего не должно тянуться в фоне, но рабочая копия и рабочие каталоги уже готовы.

## Что делать следующим

- Когда канал позволит, продолжить развёртывание командой:

```bash
cd /opt/gosha_platform/app
bash ops/install_server.sh
```

- После завершения получения образов проверить:
  - `systemctl status gosha-backend.service`
  - `systemctl status gosha-panel.service`
  - `systemctl status gosha-observer.timer`
  - `curl http://127.0.0.1:18876/api/operator/selfhost-xiaozhi`
  - `curl http://127.0.0.1:18876/api/mobile/plans`
- После любого заметного шага обновить:
  - `PROJECT_STATUS_RU.md`
  - `AGENT_CHECKPOINT_RU.md`
  - этот `NEW_CHAT_CHECKPOINT_RU.md`, если изменилась рабочая точка входа
