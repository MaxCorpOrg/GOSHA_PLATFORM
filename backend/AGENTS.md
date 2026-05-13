# AGENTS.md

`backend/` хранит compose и env-template для отдельного XiaoZhi-compatible backend.

## Правила

- Здесь не хранятся секреты и реальные `.env`.
- Всё stateful должно жить в runtime-каталогах вне git.
- Если меняешь порты или volume-paths, синхронно обновляй:
  - `docs/GOSHA_SERVER_DEPLOY_RU.md`
  - `ops/install_server.sh`
  - systemd units в `ops/systemd/`

