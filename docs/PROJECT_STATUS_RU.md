# PROJECT STATUS

## Сделано

- `GOSHA_PLATFORM` вынесен в отдельную директорию вне `MAX_CORP_CORE`.
- Подготовлен self-hosted panel/gateway baseline для pending -> claim -> activate потока.
- Введено разделение на tracked-проект и `local_only/` для runtime, snapshots и откатных материалов.
- Подготовлен отдельный server-ops слой под `/opt/gosha_platform`.
- Инициализирован git-репозиторий и выполнен первый push в `origin/agent/bootstrap-gosha`.
- На сервере создан отдельный checkout:
  - `/opt/gosha_platform/app`
  - `/opt/gosha_platform/runtime/*`
- Локально подтверждены:
  - `GET /api/mobile/plans`
  - `GET /api/operator/selfhost-xiaozhi`
  - `pending -> claim -> activate=200`
  - `check_gosha_mobile_contract.py --base-url http://127.0.0.1:18876`

## На чем остановились

- Server deploy остановлен на этапе backend image pull.
- `gosha-backend.service` снят с автозапуска и остановлен, потому что upstream image слишком тяжёлый для текущей скорости канала.
- `gosha-panel.service` и `gosha-observer.timer` тоже не оставлены включёнными, чтобы staging-контур не завис в полуподнятом состоянии.

## Что делать дальше

- Держать `GOSHA_PLATFORM` как отдельный проект и не тащить туда `xiaozhi-esp32` firmware tree до отдельного этапа.
- Когда канал позволит:
  - повторно запустить `bash /opt/gosha_platform/app/ops/install_server.sh`
  - дождаться полного pull backend images
  - затем проверить `gosha-backend.service`, `gosha-panel.service`, `gosha-observer.timer`
- После каждого серверного изменения обновлять этот checkpoint и server runbook.
