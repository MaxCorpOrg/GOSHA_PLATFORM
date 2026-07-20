# PR1 fleet-contract-schema

Итоговая спецификация контрактной схемы флота `Гоша` для первого P0-пакета.

Дата фиксации: 2026-07-11.

Этот документ является документационным контрактом. Он не описывает уже выполненную миграцию хранения, не вводит PostgreSQL, не вводит двойную запись, не добавляет пагинацию и не требует правок продуктового кода в рамках PR1.

## Источники

- Аудит текущих API и файловых хранилищ:
  - `task-20260711T065323Z-gosha-platf`
- Архитектурный план PR1:
  - `task-20260711T065326Z-pr1-fleet-contract-schema`
- Review findings:
  - `task-20260711T065834Z-analyst-task-20260711t065323z-gosha-pl`
- Главная архитектурная контрольная точка:
  - `/home/max/worktrees/gosha/platform-integration-checkpoint-20260710/docs/GOSHA_FLEET_SCALE_ARCHITECTURE_RU.md`
- Текущая репозиторная реализация:
  - `platform/gui_panel.py`
  - `platform/selfhost_xiaozhi_common.py`
  - `platform/gosha_assistant_store.py`
  - `platform/gosha_agent_store.py`
  - `platform/add_robot.sh`

## Цель PR1

PR1 фиксирует единую схему понятий и запретов для будущего масштабирования парка:

```text
account -> organization -> fleet -> robot
```

На уровне `robot` обязательно разделяются:

- `robot_template` - шаблон поведения и конфигурации;
- `robot_instance` - конкретный робот с уникальной идентичностью, устройством, статусами, привязками и аудитом.

Термины в этом документе:

- identity - поля идентичности: `robot_id`, MAC, UUID, `device_id`, `client_id`, `serial_number`;
- secrets - секреты: claim-коды, токены, ключи, endpoint-адреса с токеном;
- status - живое или производное состояние: `last_seen`, presence, detection, activity, OTA-история.

Главная защита PR1: нельзя использовать `gosha-main` как источник уникальной идентичности. `gosha-main` является только эталоном конфигурации и проверенного поведения. `gosha-01` считается штатно выключенным, если отдельная задача явно не включает его в проверку.

## Текущее состояние, которое нельзя искажать

- Источник истины в текущем коде файловый:
  - `APP_ROOT/robots/<robot_id>/robot.env`
  - `APP_ROOT/robots/<robot_id>/mcp_endpoint.txt`
  - `APP_ROOT/robots/<robot_id>/mcp_config.json`
  - `APP_ROOT/robots/<robot_id>/subscription.json`
  - `APP_ROOT/robots/<robot_id>/owner.json`
  - `APP_ROOT/robots/<robot_id>/users.json`
  - `APP_ROOT/robots/<robot_id>/panel_detection.json`
  - `APP_ROOT/robots/<robot_id>/mobile_presence.json`
  - `APP_ROOT/mobile/onboarding_codes.json`
  - `APP_ROOT/mobile/panel_client_tokens.json`
  - `APP_ROOT/selfhost_xiaozhi/state.json`
  - `APP_ROOT/agents/...`
- В коде уже есть переходный признак `ROBOT_RUNTIME_CLASS` со значениями `runtime` и `template`, но это ещё не полноценная доменная модель `robot_template/robot_instance`.
- Текущий `claim` реализован как привязка `device_id` к `robot_id` через `APP_ROOT/selfhost_xiaozhi/state.json` и обновление `robot.env`.
- Отдельных публичных контрактов `reclaim` и `unclaim` сейчас нет. В PR1 они фиксируются как будущий безопасный контракт, а не как уже реализованный API.
- Текущие списки роботов строятся обходом каталогов и возвращаются полным массивом. PR1 не вводит пагинацию.

## Таблица сущностей

| Сущность | Назначение | Обязательность в целевой модели | Владелец | Источник истины PR1 | Уникальность |
|---|---|---:|---|---|---|
| `account` | Учётная запись человека или сервисного пользователя | Да | Платформа | Контрактная схема PR1 | `account_id` глобально уникален |
| `organization` | Владелец данных, роботов, секретов и политик | Да | Платформа и владелец организации | Контрактная схема PR1 | `organization_id` глобально уникален |
| `fleet` | Группа роботов внутри организации | Да | Организация | Контрактная схема PR1 | `fleet_id` уникален внутри платформы, имя уникально внутри организации по политике |
| `robot_template` | Шаблон конфигурации и поведения | Да | Организация и оператор платформы | Контрактная схема PR1 | `template_id` глобально уникален, версия уникальна внутри шаблона |
| `robot_instance` | Конкретный робот во флоте | Да | Организация и оператор платформы | Текущий переходный источник - каталог `APP_ROOT/robots/<robot_id>`; целевой - будущий слой хранения | `robot_id` глобально уникален |
| `device_identity` | Идентичность физического устройства из прошивки и OTA | Да для привязанного робота | Прошивка, платформа проверяет и хранит | Текущий переходный источник - `APP_ROOT/selfhost_xiaozhi/state.json` | `device_id` глобально уникален |
| `claim_state` | Состояние привязки устройства к роботу | Да для self-hosted робота | Платформа | Текущий переходный источник - `APP_ROOT/selfhost_xiaozhi/state.json`; PR1 фиксирует будущий контракт | Одновременно один активный `device_id` на один `robot_id` |
| `mobile_binding` | Связь мобильного клиента, кода и робота | Да для мобильного сценария | Платформа и Android-клиент | Текущий переходный источник - `APP_ROOT/mobile/*.json` | Активный токен уникален; код подключения одноразовый или явно отозванный |
| `assistant_binding` | Привязка профилей ассистента, голоса, памяти и инструментов к роботу | Да | Платформа | Текущий переходный источник - `APP_ROOT/agents/bindings/<robot_id>.json` | Один активный набор привязок на `robot_id` |
| `status_snapshot` | Производная сводка живости, диагностики и presence | Да как производное чтение | Платформа, Android, прошивка | Производно из `panel_detection.json`, `mobile_presence.json`, self-hosted claim и activity | Не является источником идентичности; уникальна по `robot_id` и времени обновления |
| `ota_state` | OTA-состояние устройства и совместимый payload | Да для устройства на платформе | Платформа и прошивка | Текущий переходный источник - self-hosted state и OTA-запросы | Активное OTA-состояние уникально на `device_id` |
| `audit_event` | Неизменяемая запись действия оператора или платформы | Да для будущих claim/reclaim/unclaim | Платформа | В текущем коде полноценного долговечного аудита нет; PR1 фиксирует требование | `event_id` глобально уникален |

## Таблица полей

| Поле | Обяз. | Владелец поля | Источник истины | Уникальность и ограничения | Примечание |
|---|---:|---|---|---|---|
| `account.account_id` | Да | Платформа | Будущий слой хранения | Глобально уникален | Не выводится из `robot_id` |
| `account.kind` | Да | Платформа | Будущий слой хранения | Значения: `human`, `service` | Нужен для аудита действий |
| `account.display_name` | Да | Оператор или владелец | Будущий слой хранения | Не уникален | Не является правом доступа |
| `organization.organization_id` | Да | Платформа | Будущий слой хранения | Глобально уникален | Граница владения секретами |
| `organization.owner_account_id` | Да | Платформа | Будущий слой хранения | Ссылка на `account.account_id` | Может быть сервисным владельцем |
| `organization.name` | Да | Владелец организации | Будущий слой хранения | Уникальность только в рамках политики UI | Не использовать как ключ интеграций |
| `fleet.fleet_id` | Да | Платформа | Будущий слой хранения | Глобально уникален | Группа внутри организации |
| `fleet.organization_id` | Да | Платформа | Будущий слой хранения | Ссылка обязательна | Нельзя переносить флот между организациями без аудита |
| `fleet.name` | Да | Организация | Будущий слой хранения | Уникальность внутри организации по политике | Человеческое имя |
| `fleet.default_template_id` | Условно | Организация | Будущий слой хранения | Ссылка на `robot_template` | Может отсутствовать у смешанного флота |
| `robot_template.template_id` | Да | Платформа | Будущий слой хранения | Глобально уникален | Не равен `robot_id` |
| `robot_template.organization_id` | Да | Платформа | Будущий слой хранения | Ссылка обязательна | Шаблон принадлежит организации или системной области |
| `robot_template.version` | Да | Платформа | Будущий слой хранения | Уникальна внутри `template_id` | Нужна для воспроизводимого выпуска |
| `robot_template.name` | Да | Оператор | Будущий слой хранения | Не является ключом | Например, эталонная конфигурация `gosha-main` |
| `robot_template.assistant_profile_id` | Да | Платформа | `APP_ROOT/agents/assistants` в текущем переходном слое | Ссылка на профиль | Можно наследовать |
| `robot_template.provider_profile_id` | Да | Платформа | `APP_ROOT/agents/providers` в текущем переходном слое | Ссылка на профиль | Можно наследовать, секреты провайдера не входят в шаблон |
| `robot_template.tts_engine_profile_id` | Да | Платформа | `APP_ROOT/agents/tts_engines` | Ссылка на профиль | Можно наследовать |
| `robot_template.voice_profile_id` | Да | Платформа | `APP_ROOT/agents/voices` | Ссылка на профиль | Можно наследовать |
| `robot_template.memory_profile_id` | Условно | Платформа | `APP_ROOT/agents/memory` | Ссылка на профиль | Можно наследовать как профиль, но не содержимое памяти клиента |
| `robot_template.mcp_bundle_id` | Условно | Платформа | `APP_ROOT/agents/mcp_bundles` | Ссылка на профиль | Можно наследовать набор инструментов, но не токены |
| `robot_template.knowledge_profile_id` | Условно | Платформа | `APP_ROOT/agents/knowledge` | Ссылка на профиль | Сейчас отложенный контур |
| `robot_template.screen_profile_id` | Условно | Платформа | `APP_ROOT/agents/screens` | Ссылка на профиль | Сейчас отложенный контур |
| `robot_template.wake_profile_id` | Условно | Платформа | `APP_ROOT/agents/wake` | Ссылка на профиль | Сейчас отложенный контур |
| `robot_template.backend_mode` | Да | Платформа | Текущий `robot.env` для экземпляра, будущий шаблон | Допустимые значения совместимы с текущими режимами | Можно наследовать как режим по умолчанию |
| `robot_template.control_transport` | Да | Платформа | Текущий `robot.env` и `mcp_endpoint.txt` для экземпляра | Значения совместимы с `cloud-mcp`, `edge-hub`, `local-ws` | Endpoint с токеном не наследуется |
| `robot_instance.robot_id` | Да | Платформа | Сейчас имя каталога и `ROBOT_ID` в `robot.env` | Глобально уникален, формат `a-z A-Z 0-9 . _ -` | Не копировать из `gosha-main` |
| `robot_instance.organization_id` | Да | Платформа | Будущий слой хранения | Ссылка обязательна | Сейчас явно не реализовано |
| `robot_instance.fleet_id` | Да | Организация | Будущий слой хранения | Ссылка обязательна | Сейчас явно не реализовано |
| `robot_instance.template_id` | Да | Организация | Будущий слой хранения | Ссылка на шаблон | Может ссылаться на эталонную конфигурацию |
| `robot_instance.template_version` | Да | Платформа | Будущий слой хранения | Версия фиксируется при выпуске | Нужна для воспроизводимого сравнения |
| `robot_instance.robot_name` | Да | Оператор | Сейчас `ROBOT_NAME` в `robot.env` | Не уникален | Человеческое имя |
| `robot_instance.runtime_class` | Да | Платформа | Сейчас `ROBOT_RUNTIME_CLASS` в `robot.env` | Значения: `runtime`, `template` | Экземпляры флота должны быть `runtime` |
| `robot_instance.backend_mode` | Да | Платформа | Сейчас `ROBOT_BACKEND_MODE` в `robot.env` | Совместимые значения сохраняются | По умолчанию для новых роботов сейчас `self_hosted_xiaozhi` |
| `robot_instance.control_endpoint_ref` | Условно | Платформа | Сейчас `mcp_endpoint.txt` | Не должен содержать переносимый секрет в шаблоне | Для экземпляра может быть endpoint с токеном |
| `device_identity.device_id` | Да | Прошивка | Сейчас OTA-заголовки и `state.json` | Глобально уникален | Обычно MAC или стабильный идентификатор устройства |
| `device_identity.client_id` | Условно | Прошивка | Сейчас OTA-заголовки и `state.json` | Не использовать как единственный ключ | Может быть UUID клиента |
| `device_identity.serial_number` | Условно | Прошивка | Сейчас OTA payload и `state.json` | Уникальность зависит от платы | Не заменяет `device_id` |
| `device_identity.board_name` | Условно | Прошивка | Сейчас OTA payload | Не уникален | Диагностическое поле |
| `device_identity.board_ip` | Условно | Прошивка | Сейчас OTA payload | Не уникален | Не является живым доказательством связи сам по себе |
| `device_identity.app_version` | Условно | Прошивка | Сейчас OTA payload | Не уникален | Версия приложения прошивки |
| `claim_state.state` | Да | Платформа | Сейчас `pending_devices` и `claims` в `state.json` | Один активный state на пару `robot_id/device_id` | Целевые значения описаны ниже |
| `claim_state.claimed_at` | Условно | Платформа | Сейчас `state.json` | Время события | Обязателен для claimed |
| `claim_state.reclaimed_at` | Условно | Платформа | Будущий аудит | Время события | Сейчас не реализовано отдельно |
| `claim_state.unclaimed_at` | Условно | Платформа | Будущий аудит | Время события | Сейчас не реализовано отдельно |
| `claim_state.secret_version` | Да для целевого контракта | Платформа | Будущий слой хранения | Увеличивается при перевыпуске | Нужен для отзыва старых токенов |
| `mobile_binding.code` | Условно | Платформа | Сейчас `onboarding_codes.json` | Одноразовый, с TTL и отзывом | Нельзя логировать в открытых отчётах |
| `mobile_binding.panel_client_token` | Условно | Платформа | Сейчас `panel_client_tokens.json` | Секрет, уникален | Выдаётся после активации кода |
| `mobile_binding.owner` | Условно | Android и оператор | Сейчас `owner.json` | Не уникален | Данные клиента, не identity робота |
| `status_snapshot.last_seen` | Условно | Платформа | Сейчас self-hosted state, detection и activity | Не является идентичностью | Только диагностический сигнал |
| `status_snapshot.mobile_presence` | Условно | Android | Сейчас `mobile_presence.json` | Производно по `robot_id` и времени | TTL по умолчанию 180 секунд; технический минимум через окружение 30 секунд |
| `status_snapshot.detection` | Условно | Панель | Сейчас `panel_detection.json` | Производно по `robot_id` и времени | Содержит phase/error для live-probe |
| `ota_state.firmware.version` | Да в payload | Платформа | Сейчас OTA payload | Не уникален | Даже пустое значение сохраняет совместимость |
| `ota_state.firmware.url` | Да в payload | Платформа | Сейчас OTA payload | Не уникален | Сейчас может быть пустым |
| `audit_event.event_id` | Да для будущего контракта | Платформа | Будущий аудит | Глобально уникален | Сейчас полноценного append-only аудита нет |
| `audit_event.actor_account_id` | Да для будущего контракта | Платформа | Будущий аудит | Ссылка на `account` | Нужен для claim/reclaim/unclaim |
| `audit_event.reason` | Да для reclaim/unclaim | Оператор | Будущий аудит | Не уникален | Обязателен при переносе и отвязке |

## Матрица наследования `robot_template` -> `robot_instance`

| Блок | Наследуется | Как применять | Запрет |
|---|---:|---|---|
| Профиль ассистента | Да | Ссылка на `assistant_profile_id` копируется как ссылка, не как содержимое runtime | Нельзя переносить историю диалогов |
| Профиль поставщика ИИ | Да | Ссылка на профиль копируется как ссылка | Нельзя копировать ключи поставщика из env или runtime |
| Модель и системный промпт | Да | Через профиль ассистента или override экземпляра | Нельзя считать `gosha-main` единственным допустимым источником |
| Движок синтеза речи | Да | Ссылка на `tts_engine_profile_id` | Нельзя смешивать движок с голосовым профилем |
| Голос | Да | Ссылка на `voice_profile_id` | Нельзя переносить живое состояние TTS |
| Память | Только профиль | Ссылка на `memory_profile_id` | Нельзя копировать содержимое памяти клиента |
| Инструменты и MCP | Только набор | Ссылка на `mcp_bundle_id` | Нельзя копировать токены MCP и endpoint с query-токеном |
| Экран и лица | Да, как отложенный профиль | Ссылка на `screen_profile_id` | Нельзя считать применённым на прошивке без отдельной синхронизации |
| Пробуждение | Да, как отложенный профиль | Ссылка на `wake_profile_id` | Нельзя менять прошивку только фактом наследования |
| База знаний | Да, как профиль | Ссылка на `knowledge_profile_id` | Нельзя копировать пользовательские файлы клиента |
| Режим подключения | Да | Значение по умолчанию, например `self_hosted_xiaozhi` | Нельзя копировать endpoint с токеном |
| Транспорт управления | Да | Значение по умолчанию, например `cloud-mcp` | Нельзя копировать активную сессию |
| Тариф или подписка | Условно | Можно задать план по умолчанию | Нельзя копировать владельца и пользователей |
| `robot_id` | Нет | Генерируется или задаётся отдельно | Нельзя копировать из `gosha-main` |
| MAC, UUID, `device_id` | Нет | Приходят от физического устройства | Нельзя хранить в шаблоне |
| Claim-коды | Нет | Генерируются на экземпляр или устройство | Нельзя переносить между роботами |
| `websocket_token` | Нет | Генерируется на экземпляр и claim | Нельзя наследовать |
| `panel_client_token` | Нет | Выдаётся после активации мобильного кода | Нельзя наследовать |
| `mcp_endpoint.txt` с токеном | Нет | Формируется на экземпляр | Нельзя хранить в шаблоне |
| `last_seen` | Нет | Производится из живых сигналов | Нельзя копировать в новые карточки |
| `mobile_presence` | Нет | Производится Android-клиентом | Нельзя наследовать |
| `panel_detection` | Нет | Производится live-probe панели | Нельзя наследовать |
| Activity и trace | Нет | Производятся runtime | Нельзя наследовать |
| OTA-история | Нет | Производится устройством и платформой | Нельзя копировать как состояние нового робота |
| Журнал команд | Нет | Будущий отдельный контур | Нельзя копировать |
| Аудит | Нет | Только append-only история событий | Нельзя редактировать как шаблон |

## Жёсткие запреты identity, secrets, status

### Identity

Запрещено переносить из `gosha-main`, `gosha-01` или любого другого робота:

- `robot_id`;
- MAC-адрес;
- UUID;
- `device_id`;
- `client_id`, если он является стабильной идентичностью устройства;
- `serial_number`, если он уникален для платы;
- имя каталога `APP_ROOT/robots/<robot_id>` как шаблон для нового экземпляра.

### Secrets

Запрещено хранить в `robot_template` и в документации:

- claim-коды;
- `websocket_token`;
- `panel_client_token`;
- токены в `mcp_endpoint.txt`;
- ключи поставщиков ИИ;
- endpoint-адреса, если они содержат токен, query-параметр секрета или привязку к конкретному роботу;
- значения из `/opt/gosha_platform/runtime/env/providers.env`;
- значения из `GOSHA_API/`.

Секреты должны выпускаться на конкретный `robot_instance`, конкретный `device_id` или конкретную мобильную привязку.

### Status

Запрещено наследовать или копировать в новый робот:

- `last_seen`;
- `last_seen_iso`;
- `mobile_presence`;
- `panel_detection`;
- `activity_presence`;
- состояние `service_state`;
- результат live-probe;
- OTA-историю;
- текущую версию устройства как доказательство живости;
- историю команд и ошибок.

Статусы являются производными наблюдениями. Они не подтверждают идентичность и не входят в шаблон.

## Состояния `claim`

`claim` - первичная привязка свободного устройства к `robot_instance`.

| Состояние | Вход | Выход | Действия платформы | Аудит | Секреты |
|---|---|---|---|---|---|
| `device_unseen` | Устройство ещё не приходило в OTA | OTA contact | Нет записи claim | Не обязателен | Нет |
| `pending` | OTA contact с `device_id`, но без активной привязки | `claim_requested` или повторный OTA | Записать устройство в ожидание | `device_seen` в будущем аудите | Сгенерировать claim challenge, не выдавать рабочий websocket token |
| `claim_requested` | Оператор выбрал `robot_id` для `device_id` | `claimed` или `claim_failed` | Проверить робота, организацию, флот и отсутствие конфликтов | `claim_requested` | Подготовить новые секреты экземпляра |
| `claimed` | Проверки прошли | OTA выдаёт рабочий websocket payload | Связать `device_id` и `robot_id`, записать `claimed_at` | `claim_succeeded` | Выпустить активный websocket token и endpoint только для этого экземпляра |
| `claim_failed` | Проверки не прошли | Повторная попытка или возврат в `pending` | Не менять старые рабочие привязки | `claim_failed` с причиной | Не выпускать активные секреты |

Обязательные проверки перед `claimed`:

- `robot_instance` существует;
- `robot_instance.runtime_class = runtime`;
- робот находится в той же организации и флоте, где оператор имеет право привязки;
- `device_id` не находится в активной привязке без явного `reclaim`;
- новый `robot_id` не получает identity или секреты из шаблона;
- действие создаёт событие аудита.

## Состояния `reclaim`

`reclaim` - явный перенос устройства или замена устройства у робота. В текущем коде есть только частичное поведение: при claim нового `device_id` для того же `robot_id` старая claim-запись удаляется. PR1 фиксирует безопасный будущий контракт, в котором такое действие не должно быть неявным.

| Состояние | Вход | Выход | Действия платформы | Аудит | Секреты |
|---|---|---|---|---|---|
| `claimed_current` | Устройство уже активно привязано | `reclaim_requested` | Найти текущую пару `robot_id/device_id` | Не менять | Не менять |
| `reclaim_requested` | Оператор запросил перенос с причиной | `reclaim_validated` или `reclaim_rejected` | Проверить права, организацию, флот и ожидаемую старую привязку | `reclaim_requested` с reason | Заморозить старую секретную версию для отзыва |
| `reclaim_validated` | Проверки прошли | `reclaimed` | Перевести старую привязку в revoked/replaced | `claim_revoked` и `reclaim_succeeded` | Отозвать старый websocket token, endpoint token, panel token при необходимости |
| `reclaimed` | Новая связь активна | OTA выдаёт новый payload | Привязать новый `device_id` или новый `robot_id` | `reclaimed` | Выпустить новые секреты и увеличить `secret_version` |
| `reclaim_rejected` | Проверки не прошли | Старая привязка остаётся | Не менять runtime | `reclaim_rejected` с причиной | Не менять |

Минимальные требования:

- `reclaim` не должен молча удалять старую запись без события аудита;
- причина переноса обязательна;
- старые секреты должны быть отозваны до выдачи новых;
- переход между организациями требует отдельного явного права;
- `gosha-main` не должен быть источником identity для нового робота даже при reclaim.

## Состояния `unclaim`

`unclaim` - отвязка устройства от робота без немедленного назначения нового робота.

| Состояние | Вход | Выход | Действия платформы | Аудит | Секреты |
|---|---|---|---|---|---|
| `claimed` | Активная привязка существует | `unclaim_requested` | Найти claim | Не менять | Не менять |
| `unclaim_requested` | Оператор запросил отвязку с причиной | `unclaim_validated` или `unclaim_rejected` | Проверить права и состояние | `unclaim_requested` | Подготовить отзыв секретов |
| `unclaim_validated` | Проверки прошли | `unclaimed_revoked` | Убрать активную связь робота и устройства | `unclaim_succeeded` | Отозвать websocket token, endpoint token, claim code и при необходимости mobile token |
| `unclaimed_revoked` | Отвязка завершена | Новый OTA contact переводит устройство в `pending` | Робот остаётся без устройства; статусы становятся stale/expired | `device_detached` | Новые секреты не выдаются до нового claim |
| `unclaim_rejected` | Проверки не прошли | `claimed` | Runtime не меняется | `unclaim_rejected` | Не менять |

После `unclaim`:

- `robot_instance` сохраняется в системе;
- `device_identity` сохраняется в истории;
- старые статусы не удаляются, но становятся историей;
- новый живой статус должен появиться только после нового контакта устройства или мобильного presence;
- OTA до новой привязки должна возвращать activation-блок, а не рабочий websocket token.

## Матрица совместимости API

| API | Текущий маршрут | Текущая авторизация | Текущий смысл | PR1 сохраняет | Разрешённые добавления после PR1 | Запрещено в PR1 |
|---|---|---|---|---|---|---|
| Mobile plans | `GET /api/mobile/plans` | Публичный | Каталог мобильных планов | Формат `ok`, `plans` | Новые поля плана без удаления старых | Удалять маршрут или менять тип `plans` |
| Mobile resolve | `POST /api/mobile/resolve-code` | Код подключения | Возвращает `bundle` без `panel_client_token` | `code`, `panel_url`, `robot_id`, `robot_name`, `subscription`, `owner`, `users`, `mobile_profile` | Добавить `organization_id`, `fleet_id`, `template_id` как необязательные поля | Требовать новую авторизацию или убрать старые поля |
| Mobile legacy code | `GET /api/mobile/code?value=...` | Код подключения | Совместимое чтение bundle | Сохраняется как совместимый путь | Только необязательные поля | Ломать Android, который ещё читает этот путь |
| Mobile activate | `POST /api/mobile/activate-code` | Код подключения | Активирует код и возвращает `panel_client_token` | `bundle`, `activated_at`, `panel_client_token` внутри bundle | Добавить сведения организации и флота | Печатать токен в открытых логах и документации |
| Mobile runtime | `GET /api/mobile/robots/<robot_id>/runtime` | `X-Mobile-Token` или активированный `X-Mobile-Code` | Возвращает живой снимок робота | `robot_id`, `robot_name`, `runtime_class`, `backend_mode`, `service_state`, `activity`, `activity_presence`, `fleet`, `cloud_console`, `control`, `diagnostics`, `detection`, `mobile_presence`, `connectivity` | Добавить поля account/org/fleet, не меняя старые | Считать `control` единственным доказательством связи |
| Mobile presence | `POST /api/mobile/robots/<robot_id>/presence` | `X-Mobile-Token` или активированный `X-Mobile-Code` | Android сообщает локальное состояние | Состояния `home_wifi_local`, `robot_hotspot_visible`, `phone_on_robot_wifi`, `not_found`; источник `android_local_discovery` | Добавить новые необязательные состояния только после согласования Android | Удалять текущие состояния или менять TTL без документации |
| Mobile subscription | `GET/POST /api/mobile/robots/<robot_id>/subscription` | Мобильный токен или код | Чтение и обновление подписки | Текущая обёртка ответа `ok`, `data` или результат обновления | Связь с org/fleet политикой | Привязывать подписку к шаблону как секрет |
| Mobile owner | `GET/POST /api/mobile/robots/<robot_id>/owner` | Мобильный токен или код | Клиентская карточка владельца | Текущие owner-поля | Связь с `account` после отдельной миграции | Считать owner-поля идентичностью робота |
| Mobile users | `GET/POST /api/mobile/robots/<robot_id>/users` | Мобильный токен или код | Пользователи клиента | Текущий список users | Роли после отдельной RBAC-задачи | Использовать users как организационную модель PR1 |
| Operator session | `GET /api/operator/session`, `POST /api/operator/login`, `POST /api/operator/logout` | Cookie-сессия, если включена | Операторская авторизация | Текущая обёртка ответа | Связь с `account` после отдельной миграции | Ломать открытый локальный режим без отдельного решения |
| Operator robots | `GET /api/operator/robots` | Оператор | Полный массив роботов | Полный массив сохраняется | После PR1 можно добавить `organization_id`, `fleet_id`, `template_id` | Вводить пагинацию в PR1 |
| Operator create robot | `POST /api/operator/robots/create` | Оператор | Создаёт файловую карточку робота | Текущий `robot_id`, `robot_name`, `plan_code`, `endpoint`, `owner` | Валидация запретов копирования identity | Менять файловую механику в PR1 |
| Operator selfhost state | `GET /api/operator/selfhost-xiaozhi` | Оператор | Возвращает ожидающие и привязанные устройства | Публичные поля `pending_devices`, `claimed_devices`, `pending_count`, `claimed_count`, `provider`, `transport`, `backend`; внутреннее хранилище по-прежнему использует `state.json.claims` | Добавить безопасную сводку аудита | Подменять публичное `claimed_devices` внутренним именем `claims` или показывать дополнительные секреты |
| Operator claim | `POST /api/operator/selfhost-xiaozhi/claim` | Оператор | Привязка `device_id` к `robot_id` | Текущий маршрут сохраняется | После PR1 добавить отдельные маршруты `reclaim/unclaim` или явное действие | Маскировать reclaim под обычный claim без audit |
| Operator assistant profiles | `GET/POST /api/operator/*profiles`, `GET/POST /api/operator/robots/<robot_id>/assistant-config` | Оператор | Профили и привязки ассистента | Текущие идентификаторы профилей и привязка | Привязать к `robot_template` как наследуемые ссылки | Копировать секреты поставщиков в шаблон |
| Operator detect/probe | `GET/POST /api/operator/robots/<robot_id>/detect`, `GET /api/operator/robots/<robot_id>/probe` | Оператор | Live-probe и запись `panel_detection.json` | `protocol_phase`, `error_type`, `verified_now` сохраняются | Добавить TTL-классификацию | Считать detection шаблонным полем |
| OTA config | `GET/POST /gosha/ota/`, `GET/POST /xiaozhi/ota/` | Идентичность устройства в headers/payload | До claim возвращает activation; после claim websocket payload | Оба маршрута сохраняются; `firmware.version` и `firmware.url` остаются | Добавить метаданные выпуска после отдельного OTA PR | Удалять совместимый `/xiaozhi/ota/` |
| OTA activate | `POST /gosha/ota/activate`, `POST /xiaozhi/ota/activate` | Идентичность устройства | `200 claimed` или `202 pending` | Оба маршрута и статусы сохраняются | Добавить событие аудита после отдельной реализации | Менять смысл `200/202` в PR1 |
| Voice WebSocket | `/xiaozhi/v1/` | Token в совместимом контуре | Голосовой websocket совместимого backend | Путь сохраняется | Собственный путь можно добавить позднее как alias | Резко переименовывать путь |

## Текущие файловые соответствия

| Текущий файл | Что содержит сейчас | Целевая сущность | Статус PR1 |
|---|---|---|---|
| `APP_ROOT/robots/<robot_id>/robot.env` | `ROBOT_ID`, `ROBOT_NAME`, `ROBOT_RUNTIME_CLASS`, режимы подключения, поля self-hosted устройства | `robot_instance`, часть `claim_state` | Описать как переходный источник, не менять |
| `APP_ROOT/robots/<robot_id>/mcp_endpoint.txt` | Endpoint управления, иногда с токеном | Endpoint экземпляра с секретом | Не наследовать в шаблон |
| `APP_ROOT/robots/<robot_id>/mcp_config.json` | Набор инструментов | `assistant_binding` или `mcp_bundle` | Наследовать только профиль/набор, не секреты |
| `APP_ROOT/robots/<robot_id>/subscription.json` | Подписка клиента | `mobile_binding` или политика экземпляра | Не считать identity |
| `APP_ROOT/robots/<robot_id>/owner.json` | Карточка клиента | Будущий `account` только после миграции | В PR1 не подменяет account/org |
| `APP_ROOT/robots/<robot_id>/users.json` | Пользователи клиента | Будущие accounts/roles | В PR1 не подменяет RBAC |
| `APP_ROOT/robots/<robot_id>/panel_detection.json` | Последний live-probe | `status_snapshot` | Производное состояние, не шаблон |
| `APP_ROOT/robots/<robot_id>/mobile_presence.json` | Android presence | `status_snapshot` | Производное состояние, не шаблон |
| `APP_ROOT/mobile/onboarding_codes.json` | Коды подключения, TTL, revoked/activated | `mobile_binding` | Секретный переходный источник |
| `APP_ROOT/mobile/panel_client_tokens.json` | Мобильные токены | `mobile_binding` secret | Не публиковать и не наследовать |
| `APP_ROOT/selfhost_xiaozhi/state.json` | `backend`, `pending_devices`, `claims` | `device_identity`, `claim_state`, `ota_state` | Переходный источник для claim |
| `APP_ROOT/agents/bindings/<robot_id>.json` | Привязки профилей к роботу | `assistant_binding` | Может стать частью template/instance разделения позднее |

## Строгая граница PR1

В PR1 входит:

1. Документальная фиксация доменной модели.
2. Таблицы сущностей и полей.
3. Матрица наследования `robot_template` -> `robot_instance`.
4. Запреты копирования identity, secrets и status.
5. Контрактные конечные автоматы `claim/reclaim/unclaim`.
6. Матрица совместимости текущих mobile/operator/OTA API.
7. Измеримые критерии приёмки для будущего тестирования на 1000 тестовых роботов.

В PR1 не входит:

- PostgreSQL;
- миграции базы данных;
- двойная запись;
- импорт из JSON;
- переключение чтения;
- пагинация, поиск и фильтры;
- новые продуктовые маршруты;
- изменения `platform/`, `backend/`, `ops/`;
- изменения `GOSHA_MOBILE`, `GOSHA_FIRMWARE`, iOS или `AI_OFFICE`;
- изменение живого поведения `gosha-main` или `gosha-01`;
- вывод секретов или прямых служебных URL в открытую панель.

## Малое разбиение задач

| Шаг | Роль | Результат | Критерий готовности |
|---:|---|---|---|
| 1 | `documenter` | Зафиксировать этот документ | В репозитории есть `docs/GOSHA_FLEET_CONTRACT_SCHEMA_PR1_RU.md` |
| 2 | `reviewer` | Проверить полноту таблиц и запретов | Нет пропусков по identity/secrets/status и совместимости API |
| 3 | `planner` | Разложить следующий PR после PR1 | Есть отдельные задачи для PostgreSQL, двойной записи, пагинации, статусов, команд, многороботного Android-сценария и OTA |
| 4 | `architect` | Уточнить будущую схему хранения | Есть схема для PR2, но без правок в PR1 |
| 5 | `developer` | Реализовывать только после review и отдельной задачи | Не начинать код по этой PR1-ветке |

Минимальные подзадачи для следующего пакета планирования:

1. Описать генератор 1000 тестовых `robot_instance` без копирования identity.
2. Описать проверку уникальности `robot_id`, `device_id`, `websocket_token`, `panel_client_token`.
3. Описать совместимые снимки ответов mobile/operator/OTA API.
4. Описать событие аудита для `claim`, `reclaim`, `unclaim`.
5. Описать отзыв секретов при `reclaim` и `unclaim`.
6. Описать правила, по которым `gosha-main` превращается в `robot_template` только как конфигурационный эталон.
7. Описать, что выключенный `gosha-01` не проваливает проверку живости флота.

## Измеримые критерии приёмки

Документ считается достаточным для PR1, если:

1. В спецификации есть таблица сущностей с обязательностью, владельцем, источником истины и уникальностью.
2. В спецификации есть таблица полей с обязательностью, владельцем, источником истины и уникальностью.
3. Для каждого поля identity указано, что оно не наследуется из шаблона.
4. Для каждого поля secret указано, что оно не хранится в `robot_template` и не попадает в документацию.
5. Для каждого поля status указано, что оно является производным и не наследуется.
6. Матрица `robot_template` -> `robot_instance` явно разрешает наследовать только профильные ссылки и настройки поведения.
7. `claim`, `reclaim`, `unclaim` описаны как конечные автоматы с аудитом и отзывом секретов.
8. Матрица mobile/operator/OTA API перечисляет текущие маршруты и запреты PR1.
9. Граница PR1 явно запрещает PostgreSQL, двойную запись, пагинацию и продуктовый код.
10. В документе явно написано, что `gosha-main` - только эталон конфигурации.
11. В документе явно написано, что штатно выключенный `gosha-01` не является дефектом.

Будущая проверка на 1000 тестовых роботов должна считаться пройденной только если:

1. Созданы или смоделированы 1000 разных `robot_instance`.
2. Все 1000 имеют уникальные `robot_id`.
3. Ни один `robot_id` не равен `gosha-main`, кроме самого эталонного робота.
4. Ни один новый робот не копирует MAC, UUID, `device_id`, claim-код, `websocket_token`, `panel_client_token` или endpoint с токеном из `gosha-main`.
5. Для каждого робота известны `organization_id`, `fleet_id`, `template_id` и `template_version`.
6. У каждого привязанного устройства есть ровно один активный `claim_state`.
7. У каждого активного `robot_instance` есть не более одного активного `device_id`.
8. Старые мобильные маршруты возвращают совместимую обёртку ответа и не требуют новых обязательных полей.
9. OTA до claim возвращает activation-блок.
10. OTA после claim возвращает websocket-блок.
11. `firmware.version` и `firmware.url` присутствуют в OTA payload даже при пустых значениях.
12. `runtime.connectivity` не считает робота подключённым только по настроенному `control`.
13. `mobile_presence` принимает текущие состояния Android без изменения контракта.
14. `gosha-01` может быть вне связи или выключен без провала общей проверки `gosha-main`.
15. Проверка не требует вывода или публикации секретов.

## Передача следующей роли

Следующая роль: `reviewer`.

Что проверить:

- таблицы сущностей и полей;
- полноту запретов identity/secrets/status;
- конечные автоматы `claim/reclaim/unclaim`;
- совместимость текущих mobile/operator/OTA API;
- отсутствие выхода за границу PR1;
- измеримость критериев для 1000 тестовых роботов.
