# PROJECT STATUS

## Сделано

- `GOSHA_PLATFORM` вынесен в отдельную директорию вне `MAX_CORP_CORE`.
- Подготовлен self-hosted panel/gateway baseline для pending -> claim -> activate потока.
- Введено разделение на tracked-проект и `local_only/` для runtime, snapshots и откатных материалов.
- Подготовлен отдельный server-ops слой под `/opt/gosha_platform`.

## На чем остановились

- Нужны первый git bootstrap, первый push в `agent/bootstrap-gosha` и server deploy в staging-контур.
- После этого надо проверить живой pending -> claim на сервере и подтвердить, что `AI_ROBOT` на `:8876` не задет.

## Что делать дальше

- Держать `GOSHA_PLATFORM` как отдельный проект и не тащить туда `xiaozhi-esp32` firmware tree до отдельного этапа.
- После каждого серверного изменения обновлять этот checkpoint и server runbook.

