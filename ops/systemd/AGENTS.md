# AGENTS.md

`ops/systemd/` хранит только unit-файлы для server-side контура `GOSHA`.

## Правила

- Не добавляй сюда сервисы, которые мутируют git-репозиторий сами по себе.
- Observer timer должен оставаться read-only.
- Если меняешь имена сервисов или env-пути, синхронно обновляй `ops/install_server.sh` и `docs/GOSHA_SERVER_DEPLOY_RU.md`.
