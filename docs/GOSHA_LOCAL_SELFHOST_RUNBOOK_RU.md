# GOSHA Local Self-Hosted Runbook

## Что это

Локальный контур для тестов новой платформы `Гоша` отдельно от `AI_ROBOT`.

## Что уже лежит в песочнице

- `platform/gui_panel.py`
- `platform/panel_index.html`
- `platform/selfhost_xiaozhi_common.py`
- `platform/check_gosha_mobile_contract.py`
- `backend/selfhost-backend.compose.yml`
- `backend/selfhost-backend.env.example`

## Локальный запуск панели

```bash
cd /home/max/GOSHA_PLATFORM
bash bin/run_local_gosha_panel.sh
```

Адрес:

```text
http://127.0.0.1:18876
```

## Что проверять первым

1. `GET /api/operator/selfhost-xiaozhi`
2. `GET /api/mobile/plans`
3. `GET /xiaozhi/ota/` с `Device-Id` / `Client-Id`
4. `POST /xiaozhi/ota/activate`
5. pending -> claim в UI

## Где лежит локальное состояние

- `local_only/runtime_lab/app_root`
- `local_only/runtime_lab/app_root/selfhost_xiaozhi/state.json`
- `local_only/runtime_lab/app_root/robots/`

## Зачем нужен snapshot

Если потребуется откатить именно сохранённый набор интеграционных файлов в `AI_ROBOT`, можно использовать:

```bash
cd /home/max/GOSHA_PLATFORM
bash local_only/bin/restore_ai_robot_snapshot.sh
```
