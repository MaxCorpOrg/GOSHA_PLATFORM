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

Из корня репозитория:

```bash
cd /home/max/GOSHA_PLATFORM
bash bin/run_local_gosha_gateway.sh
```

Во втором терминале:

```bash
bash bin/run_local_gosha_panel.sh
```

`run_local_gosha_panel.sh` теперь сам проверяет и при необходимости ставит Python-библиотеку `websockets`, без которой live-probe панели не может честно дойти до робота.

Из `/home/max` на этой машине доступны совместимые wrapper-скрипты:

```bash
bash bin/run_local_gosha_gateway.sh
bash bin/run_local_gosha_panel.sh
```

Адрес:

```text
панель: http://127.0.0.1:18876
шлюз ИИ-агентов: http://127.0.0.1:18110
```

Проверка, что вся локальная цепочка реально жива:

```bash
cd /home/max/GOSHA_PLATFORM
bash bin/check_local_gosha_stack.sh
```

То же самое можно запускать и через общий alias:

```bash
bash bin/check_gosha_panel_stack.sh
```

Этот smoke-check честно проверяет:

1. что gateway отвечает на `/healthz`;
2. что панель отвечает на `/api/operator/robots`;
3. что панель отвечает на `/api/operator/assistant-control/catalog`;
4. что по первому роботу уже видны `backend_mode`, `last_seen`, плата и версия прошивки.

Дополнительно live-probe панели теперь различает не только общее `не отвечает`, но и реальные фазы:

- сессия оборвалась сразу после `initialize`;
- handshake дошёл до `tools/call`, но робот не прислал `ACK`;
- карточка в `Платформе Гоша`, но живой endpoint робота всё ещё указывает на внешний `api.xiaozhi.me`.

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
