# Local Smoke 2026-05-13

Песочница:

- `/home/max/GOSHA_PLATFORM`
- runtime state:
  - `/home/max/GOSHA_PLATFORM/local_only/runtime_lab/app_root`
- local panel:
  - `http://127.0.0.1:18876`

## Что проверено

- локальный запуск `bash bin/run_local_gosha_panel.sh`
- `GET /api/mobile/plans`
- `GET /api/operator/selfhost-xiaozhi`
- `POST /xiaozhi/ota/` с тестовым `Device-Id`
- появление устройства в `pending_devices`
- `POST /api/operator/selfhost-xiaozhi/claim`
- `POST /xiaozhi/ota/activate`
- переход устройства в `claimed_devices`

## Итог

- local-only контур `pending -> claim -> activate=200` подтвержден
- `gosha-local` получил self-hosted MCP endpoint вида:
  - `ws://127.0.0.1:18876/mcp/?token=...&robot_id=gosha-local`
- OTA после claim отдает рабочий `websocket.url`:
  - `ws://127.0.0.1:18876/xiaozhi/v1/`

## Важно

- это локальный smoke базового контура;
- этот документ фиксирует pre-server bootstrap состояние;
- live deploy на момент этой локальной проверки ещё не делался.
