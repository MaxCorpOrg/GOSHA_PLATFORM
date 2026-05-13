# AGENTS.md

`ops/` отвечает за deploy в `/opt/gosha_platform`, observer и systemd units.

## Что здесь важно

- Ничего из `ops/` не должно трогать `/opt/ai_robot` деструктивно.
- Допустимо только read-only чтение или одноразовый импорт данных в новый runtime-контур.
- Observer обязан быть read-only: без автокоммитов, автопушей и автоперезапусков.

## Перед правками

- Прочитай `../docs/GOSHA_SERVER_DEPLOY_RU.md`.
- Убедись, что порты staging-контуров не конфликтуют с живым `AI_ROBOT`.

