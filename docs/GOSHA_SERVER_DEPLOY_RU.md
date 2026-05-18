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
- внутренний шлюз ИИ-агентов: `127.0.0.1:18110`
- совместимый узел `WebSocket`: `18080`
- внутренний веб-интерфейс серверного узла: `127.0.0.1:18082`
- внутренний HTTP-интерфейс серверного узла: `127.0.0.1:18083`

## Режимы `install_server.sh`

- `bash ops/install_server.sh --phase panel`
  - создаёт рабочее дерево каталогов;
  - копирует unit-файлы `systemd`;
  - создаёт и дополняет `panel.env`, `selfhost-backend.env` и `agent-gateway.env`;
  - генерирует локальный пароль оператора и пароль базы данных, если они ещё не заданы;
  - по возможности одноразово импортирует данные `robots/mobile/share` из `/opt/ai_robot` только в новый рабочий контур;
  - включает:
    - `gosha-agent-gateway.service`
    - `gosha-panel.service`
    - `gosha-observer.timer`
  - не запускает `gosha-backend.service`
- `bash ops/install_server.sh --phase backend`
  - работает только с тяжёлой серверной частью;
  - запускает или перезапускает `gosha-backend.service`;
  - не требует повторной переустановки панели как обязательной части
- `bash ops/install_server.sh --phase all`
  - полный сценарий, совместимый со старым единым запуском;
  - включает все службы

## Текущее подтверждённое состояние

- Серверная рабочая копия находится в `/opt/gosha_platform/app`.
- Коммит, на котором был подтверждён живой лёгкий контур:
  - `579c1ae`
- Лёгкая фаза уже поднята и проверена:
  - `gosha-agent-gateway.service` -> `active`
  - `gosha-panel.service` -> `active`
  - `gosha-observer.timer` -> `active`
  - `gosha-backend.service` -> `failed`, но это допустимо до отдельного запуска `--phase backend`
- Наблюдатель на сервере даёт итог `OK`; отсутствие `backend` и порта `18080` помечается как необязательное предупреждение текущей фазы.
- Подтверждены:
  - `curl http://127.0.0.1:18110/healthz` -> `200`
  - `curl http://127.0.0.1:18876/api/operator/selfhost-xiaozhi` -> `200`
  - `curl http://127.0.0.1:18876/api/mobile/plans` -> `200`
  - `POST http://151.241.228.232:18876/hooks/oauth-executor/github` доходит до локального executor через серверный прокси-маршрут панели
  - `GET http://127.0.0.1:18876/api/operator/oauth-executor/healthz` -> `200` после входа оператора
  - серверный сценарий `pending -> claim -> activate`
  - наследование профиля по умолчанию и явное переключение профиля робота без перепрошивки

## Что проверять после `--phase panel`

- `systemctl status gosha-agent-gateway.service`
- `systemctl status gosha-panel.service`
- `systemctl status gosha-observer.timer`
- `curl http://127.0.0.1:18110/healthz`
- `curl http://127.0.0.1:18876/api/operator/selfhost-xiaozhi`
- `curl http://127.0.0.1:18876/api/operator/agent-profiles`
- `curl http://127.0.0.1:18876/api/mobile/plans`
- `curl http://127.0.0.1:18876/api/operator/oauth-executor/healthz`
- наличие `/opt/gosha_platform/runtime/reports/LAST_REPORT_RU.md`

## Что проверять после `--phase backend`

- `systemctl status gosha-backend.service`
- доступность порта `18080`
- повторную сводку наблюдателя
- повторно операторский сценарий:
  - `pending -> claim -> activate`
  - наследование профиля по умолчанию
  - явное переключение профиля робота без перепрошивки

## Если канал слишком медленный

- Образы `xinnan-tech/xiaozhi-esp32-server` тянут тяжёлые слои `docker`.
- В этом случае безопасно остановиться на фазе `panel` и оставить работающими:
  - `gosha-agent-gateway.service`
  - `gosha-panel.service`
  - `gosha-observer.timer`
- При этом допустимо, что:
  - `gosha-backend.service` находится в состоянии `failed`
  - порт `18080` не слушается
  - наблюдатель помечает эти два пункта как необязательные предупреждения
- После паузы тяжёлая часть продолжается отдельно:

```bash
cd /opt/gosha_platform/app
bash ops/install_server.sh --phase backend
```
