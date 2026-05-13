# AGENTS.md

`platform/` — это панель `Гоша`, mobile/operator API и self-hosted XiaoZhi gateway.

## Перед правками

1. Прочитай:
   - `../AGENTS.md`
   - `../docs/AGENT_CHECKPOINT_RU.md`
   - `../docs/PROJECT_STATUS_RU.md`
2. Проверь, не сломает ли изменение:
   - `/api/mobile/resolve-code`
   - `/api/mobile/activate-code`
   - `/api/operator/selfhost-xiaozhi`
   - `/xiaozhi/ota/`
   - `/xiaozhi/ota/activate`

## Правила

- Не возвращай зависимости на `xiaozhi.me`.
- Не смешивай staging-настройки `GOSHA` с live `AI_ROBOT`.
- Если меняешь public/mobile contract, обязательно прогоняй smoke-check.

