# GOSHA PLATFORM

Отдельный проект для self-hosted платформы `Гоша`, вынесенный из `AI_ROBOT` в самостоятельный репозиторий и отдельный server-runtime контур.

## Что здесь есть

- `platform/`
  - панель, mobile/operator API и self-hosted XiaoZhi gateway
- `backend/`
  - compose и env-template для `xinnan-tech/xiaozhi-esp32-server`
- `ops/`
  - server bootstrap, observer и systemd units
- `bin/`
  - локальные helper-скрипты
- `docs/`
  - checkpoint, runbook и handoff
- `local_only/`
  - runtime state, snapshots, откатные материалы и прочее, что не должно попадать в git

## Главное правило

- GitHub-репозиторий для этого проекта отдельный.
- `AI_ROBOT` не является рабочим корнем этого проекта.
- Всё, что относится к локальному состоянию и откату, должно жить только в `local_only/`.

## Быстрый старт локально

```bash
cd /home/max/GOSHA_PLATFORM
bash bin/init_local_lab.sh
bash bin/run_local_gosha_panel.sh
```

Панель поднимется на:

```text
http://127.0.0.1:18876
```

## Быстрый старт на сервере

1. Клонировать репозиторий в:

```text
/opt/gosha_platform/app
```

2. Выполнить:

```bash
cd /opt/gosha_platform/app
bash ops/install_server.sh
```

3. Проверить:

```bash
systemctl status gosha-panel.service
systemctl status gosha-backend.service
systemctl status gosha-observer.timer
```

## Что не коммитим

- `local_only/`
- runtime state
- snapshots
- env-файлы и секреты
- временные логи и build-артефакты
