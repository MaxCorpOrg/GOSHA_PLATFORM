# Инструкция по развёртыванию GOSHA на сервере

## Цель

Поднять отдельный подготовительный контур `GOSHA_PLATFORM` на сервере в `/opt/gosha_platform`, не ломая живой `AI_ROBOT`.
Совместимые технические маршруты `/xiaozhi/*` при этом сохраняются как внутренний слой совместимости.

## Целевые пути

- рабочая копия: `/opt/gosha_platform/app`
- рабочий каталог приложения: `/opt/gosha_platform/runtime/app_root`
- каталог `env`: `/opt/gosha_platform/runtime/env`
- каталог отчётов: `/opt/gosha_platform/runtime/reports`

## Целевые порты

- панель и API: `18876`
- совместимый узел `WebSocket`: `18080`
- внутренний веб-интерфейс серверного узла: `127.0.0.1:18082`
- внутренний HTTP-интерфейс серверного узла: `127.0.0.1:18083`

## Что делает `install_server.sh`

- создаёт рабочее дерево каталогов;
- копирует unit-файлы `systemd`;
- создаёт `panel.env` и `selfhost-backend.env`, если их ещё нет;
- генерирует локальный пароль оператора и пароль базы данных, если они ещё не заданы;
- по возможности одноразово импортирует данные `robots/mobile/share` из `/opt/ai_robot` только в новый рабочий контур;
- включает `gosha-backend.service`, `gosha-panel.service` и `gosha-observer.timer`.

## Что проверять после развёртывания

- `systemctl status gosha-backend.service`
- `systemctl status gosha-panel.service`
- `systemctl status gosha-observer.timer`
- `curl http://127.0.0.1:18876/api/operator/selfhost-xiaozhi`
- `curl http://127.0.0.1:18876/api/mobile/plans`
- наличие `/opt/gosha_platform/runtime/reports/LAST_REPORT_RU.md`

## Если канал слишком медленный

- Образы `xinnan-tech/xiaozhi-esp32-server` тянут тяжёлые слои `docker`.
- В этом случае безопасно:
  - остановить `gosha-backend.service`
  - снять его с автозапуска
  - оставить рабочую копию и рабочие каталоги как есть
- После паузы развёртывание продолжается повторным запуском:

```bash
cd /opt/gosha_platform/app
bash ops/install_server.sh
```
