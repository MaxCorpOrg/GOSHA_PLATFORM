# GOSHA PLATFORM

Отдельный проект для платформы `Гоша`, вынесенный из `AI_ROBOT` в самостоятельный репозиторий и отдельный серверный рабочий контур.

## Что здесь есть

- `platform/`
  - панель, маршруты `mobile/operator API` и совместимый шлюз платформы `Гоша`
- `backend/`
  - `docker compose` и шаблон `env` для совместимого серверного узла на базе `xinnan-tech/xiaozhi-esp32-server`
- `ops/`
  - установка на сервер, наблюдатель и unit-файлы `systemd`
- `bin/`
  - локальные вспомогательные скрипты
- `docs/`
  - контрольные точки, рабочие инструкции и документы передачи состояния
- `local_only/`
  - рабочие данные, снимки состояния, откатные материалы и прочее, что не должно попадать в git

## Главное правило

- GitHub-репозиторий для этого проекта отдельный.
- `AI_ROBOT` не является рабочим корнем этого проекта.
- Всё, что относится к локальному состоянию и откату, должно жить только в `local_only/`.
- Внешне проект называется `Гоша`; упоминания `Xiaozhi` допустимы только в технических местах совместимости.

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
- рабочие данные
- снимки состояния
- env-файлы и секреты
- временные логи и build-артефакты
