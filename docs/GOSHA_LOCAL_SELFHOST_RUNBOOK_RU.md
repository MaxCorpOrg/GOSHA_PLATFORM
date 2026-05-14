# Локальная инструкция по платформе GOSHA

## Что это

Локальный контур для тестов платформы `Гоша` отдельно от `AI_ROBOT`.
Совместимые технические маршруты `/xiaozhi/*` сохраняются здесь только как слой совместимости.

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
bash bin/run_local_gosha_gateway.sh
```

Во втором терминале:

```bash
bash bin/run_local_gosha_panel.sh
```

Адрес:

```text
панель: http://127.0.0.1:18876
шлюз ИИ-агентов: http://127.0.0.1:18110
```

## Что проверять первым

1. `GET /api/operator/selfhost-xiaozhi`
2. `GET /api/mobile/plans`
3. `GET /api/operator/agent-gateway/status`
4. `GET /api/operator/agent-profiles`
5. `GET /xiaozhi/ota/` с `Device-Id` / `Client-Id`
6. `POST /xiaozhi/ota/activate`
7. переход устройства из ожидающего состояния в привязанное через интерфейс панели

## Как теперь привязывать устройство в панели

1. Открой панель `http://127.0.0.1:18876`
2. Смотри верхний блок `Новый робот ждёт привязки`
3. Если устройство уже пришло на панель, этот блок автоматически появится и начнёт мигать
4. Внутри блока выбери нужный `robot_id`
5. Нажми `Привязать сейчас`
6. Для полного журнала можно перейти в нижний блок `Платформа Гоша`

Нижний журнал привязок остаётся рабочим, но основной путь для оператора теперь верхний мигающий блок.

## Где лежит локальное состояние

- `local_only/runtime_lab/app_root`
- `local_only/runtime_lab/app_root/selfhost_xiaozhi/state.json`
- `local_only/runtime_lab/app_root/robots/`

## Зачем нужен снимок состояния

Если потребуется откатить именно сохранённый набор интеграционных файлов в `AI_ROBOT`, можно использовать:

```bash
cd /home/max/GOSHA_PLATFORM
bash local_only/bin/restore_ai_robot_snapshot.sh
```
