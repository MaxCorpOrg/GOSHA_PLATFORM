# Локальная проверка 2026-05-13

Песочница:

- `/home/max/GOSHA_PLATFORM`
- рабочие данные:
  - `/home/max/GOSHA_PLATFORM/local_only/runtime_lab/app_root`
- локальная панель:
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

- локальный контур `pending -> claim -> activate=200` подтверждён
- `gosha-local` получил MCP-адрес собственной платформы вида:
  - `ws://127.0.0.1:18876/mcp/?token=...&robot_id=gosha-local`
- после привязки `OTA` отдаёт рабочий `websocket.url`:
  - `ws://127.0.0.1:18876/xiaozhi/v1/`

## Важно

- это локальная проверка базового контура;
- этот документ фиксирует состояние до серверного развёртывания;
- живое развёртывание на момент этой локальной проверки ещё не делалось.
