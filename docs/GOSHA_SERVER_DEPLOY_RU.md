# GOSHA Server Deploy Runbook

## Цель

Поднять отдельный staging-контур `GOSHA_PLATFORM` на сервере в `/opt/gosha_platform`, не ломая живой `AI_ROBOT`.

## Целевые пути

- checkout: `/opt/gosha_platform/app`
- runtime app root: `/opt/gosha_platform/runtime/app_root`
- env: `/opt/gosha_platform/runtime/env`
- reports: `/opt/gosha_platform/runtime/reports`

## Целевые порты

- panel/API: `18876`
- XiaoZhi websocket backend: `18080`
- backend internal web: `127.0.0.1:18082`
- backend internal http: `127.0.0.1:18083`

## Что делает install

- создаёт runtime-дерево;
- копирует systemd units;
- создаёт `panel.env` и `selfhost-backend.env`, если их ещё нет;
- генерирует локальный operator password и db password, если они ещё не заданы;
- по возможности импортирует robots/mobile/share assets из `/opt/ai_robot` только в новый runtime-контур;
- включает `gosha-backend.service`, `gosha-panel.service` и `gosha-observer.timer`.

## Что проверять после deploy

- `systemctl status gosha-backend.service`
- `systemctl status gosha-panel.service`
- `systemctl status gosha-observer.timer`
- `curl http://127.0.0.1:18876/api/operator/selfhost-xiaozhi`
- `curl http://127.0.0.1:18876/api/mobile/plans`
- наличие `/opt/gosha_platform/runtime/reports/LAST_REPORT_RU.md`

## Если канал слишком медленный

- `xinnan-tech/xiaozhi-esp32-server` тянет тяжёлые docker layers.
- В этом случае безопасно:
  - остановить `gosha-backend.service`
  - снять его с автозапуска
  - оставить checkout и runtime-папки как есть
- После паузы deploy продолжается повторным запуском:

```bash
cd /opt/gosha_platform/app
bash ops/install_server.sh
```
