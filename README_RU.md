# GOSHA PLATFORM

Отдельный проект для платформы `Гоша`, вынесенный из `AI_ROBOT` в самостоятельный репозиторий и отдельный серверный рабочий контур.
Проект сразу проектируется как масштабируемый: много роботов, много профилей ИИ-агентов и много OpenAI-совместимых провайдеров без жёсткой привязки к одному облаку.

## Что здесь есть

- `platform/`
  - панель, маршруты `mobile/operator API`, отдельный внутренний шлюз ИИ-агентов и слой хранения профилей агентов
- отдельный мобильный контур:
  - `/home/max/GOSHA_MOBILE`
  - это самостоятельный Android-клиент платформы `Гоша`, который использует `mobile API` этой платформы
- `backend/`
  - `docker compose` и шаблон `env` для совместимого серверного узла на базе `xinnan-tech/xiaozhi-esp32-server`
- `ops/`
  - установка на сервер, наблюдатель и unit-файлы `systemd`
- `bin/`
  - локальные вспомогательные скрипты
- `docs/`
  - контрольные точки, рабочие инструкции и документы передачи состояния
  - спецификация собственной панели управления ассистентом `Гоша`
- `docs/GOSHA_PROJECT_MAP_RU.md`
  - общая карта всех контуров: платформа, сервер, голоса, прошивка, мобильный клиент и документы
- `docs/GOSHA_FLEET_SCALE_ARCHITECTURE_RU.md`
  - принятая архитектурная точка перехода от эталонного `gosha-main` к парку из тысяч уникальных роботов
- `local_only/`
  - рабочие данные, снимки состояния, откатные материалы и прочее, что не должно попадать в git

## Главное правило

- GitHub-репозиторий для этого проекта отдельный.
- `AI_ROBOT` не является рабочим корнем этого проекта.
- Для нового чата канонический порядок входа такой:
  - `AGENTS.md`
  - `START_HERE_FOR_NEW_CHAT.md`
  - `docs/GOSHA_PROJECT_MAP_RU.md`
  - `docs/NEW_CHAT_CHECKPOINT_RU.md`
  - `docs/PROJECT_STATUS_RU.md`
- Всё, что относится к локальному состоянию и откату, должно жить только в `local_only/`.
- Внешне проект называется `Гоша`; упоминания `Xiaozhi` допустимы только в технических местах совместимости.
- Внешние OTA-маршруты собственного продукта переводим на `/gosha/...`, а старые `/xiaozhi/...` сохраняем как временные совместимые псевдонимы там, где это уже нужно живому контуру.
- Новые подсистемы нужно строить с расчётом на масштабирование по числу роботов и профилей агентов.
- Для задач массового парка сначала читать `docs/GOSHA_FLEET_SCALE_ARCHITECTURE_RU.md`.
- `gosha-main` задаёт проверенное поведение и конфигурацию, но его идентификаторы, ключи, токены и рабочее состояние никогда не копируются в другие устройства.
- Панель управления должна масштабироваться не только по роботам, но и по:
  - ассистентам;
  - голосам;
  - экранным профилям;
  - памяти;
  - наборам инструментов `MCP`.

## Следующий крупный контур

Следующий большой этап проекта — собственная панель настройки ассистента `Гоша`.

Она должна покрыть:

- имя и роль ассистента;
- язык;
- выбор голоса;
- настройку памяти;
- модель;
- `MCP`-сервисы;
- базу знаний;
- экран и лица робота;
- имя пробуждения `Гоша`.

Спецификация этого этапа зафиксирована здесь:

- `docs/GOSHA_ASSISTANT_CONTROL_PANEL_SPEC_RU.md`

## Быстрый старт локально

Из корня репозитория:

```bash
cd /home/max/GOSHA_PLATFORM
bash bin/init_local_lab.sh
bash bin/run_local_gosha_gateway.sh
```

Во втором терминале:

```bash
bash bin/run_local_gosha_panel.sh
```

Оба скрипта теперь сами проверяют Python-зависимость `websockets`, без которой панель не может выполнять честный live-probe робота.

Из `/home/max` на этой машине тоже можно запускать, потому что добавлены локальные wrapper-скрипты:

```bash
bash bin/run_local_gosha_gateway.sh
bash bin/run_local_gosha_panel.sh
```

`init_local_lab.sh` теперь ещё и автоматически мигрирует legacy карточки локальной лаборатории в self-hosted режим `Платформы Гоша`, чтобы `gosha-main` и похожие тестовые роботы не оставались в старом `xiaozhi_cloud`.

Порты локального контура:

```text
панель: http://127.0.0.1:18876
внутренний шлюз ИИ-агентов: http://127.0.0.1:18110
```

Быстрая честная smoke-проверка всей локальной цепочки:

```bash
cd /home/max/GOSHA_PLATFORM
bash bin/check_local_gosha_stack.sh
```

Или тем же смыслом через более общий alias:

```bash
bash bin/check_gosha_panel_stack.sh
```

Совместимые и продуктовые пути текущего этапа:

```text
OTA: /gosha/ota/ и совместимый псевдоним /xiaozhi/ota/
activate: /gosha/ota/activate и совместимый псевдоним /xiaozhi/ota/activate
WebSocket: пока /xiaozhi/v1/
```

Дополнительный пакет подключения для нового мобильного клиента:

```text
bundle.mobile_profile.brand = GOSHA
bundle.mobile_profile.panel_url = http://151.241.228.232:18876
bundle.mobile_profile.portal_url = http://192.168.4.1
bundle.mobile_profile.robot_wifi_prefixes = [GOSHA-, Xiaozhi-]
```

Что теперь умеет честно различать панель по live-probe:

- робот действительно ответил на активную проверку;
- сессия оборвалась сразу после `initialize`;
- handshake дошёл до `tools/call`, но робот не прислал `ACK`;
- карточка уже в режиме `Платформа Гоша`, но живой endpoint робота всё ещё смотрит на внешний `api.xiaozhi.me`.

Где смотреть живой голосовой контур:

```text
логика профилей: platform/gosha_assistant_store.py
рабочие профили на сервере: /opt/gosha_platform/runtime/app_root/agents
эффективный TTS/ASR/LLM: /opt/gosha_platform/runtime/app_root/selfhost_xiaozhi/backend/data/.config.yaml
секреты поставщиков: /opt/gosha_platform/runtime/env/providers.env
локальные секреты разработчика: /home/max/GOSHA_PLATFORM/GOSHA_API
```

## Что проверять локально

```bash
curl http://127.0.0.1:18876/api/operator/agent-gateway/status
curl http://127.0.0.1:18876/api/operator/agent-profiles
curl http://127.0.0.1:18876/api/operator/robots
```

## Быстрый старт на сервере

1. Клонировать репозиторий в:

```text
/opt/gosha_platform/app
```

2. Выполнить:

```bash
cd /opt/gosha_platform/app
bash ops/install_server.sh
```

3. Проверить:

```bash
systemctl status gosha-panel.service
systemctl status gosha-backend.service
systemctl status gosha-agent-gateway.service
systemctl status gosha-observer.timer
```

## Что не коммитим

- `local_only/`
- рабочие данные
- снимки состояния
- env-файлы и секреты
- временные логи и build-артефакты
