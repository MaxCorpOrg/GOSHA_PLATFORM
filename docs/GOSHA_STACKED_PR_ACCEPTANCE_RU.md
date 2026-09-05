# Приёмка stacked Draft PR платформы

Дата фиксации: `2026-09-05`.

Этот список нужен для кандидата `task-20260905-gosha-platform-ci-candidate`.
Он задаёт воспроизводимую smoke/read-only приёмку для промежуточных stacked Draft PR, то есть Pull Request, которые временно строятся поверх другой рабочей ветки, а не прямо поверх `main`.

## Область

- База платформенного кандидата: `4375a6d0415c07f9b09c3a1b4e0135857ed5d9e1`.
- Санитарная документация взята только как релевантный безопасный diff из PR58 `9b6e95c603bddf73ab700eb01190f688ce341118` относительно `cf0e685dda4adfdedc8202f01438b72ac1699c4e`.
- Проверяем только `GOSHA_PLATFORM` и контракты с Android/прошивкой.
- Не менять production, relay, controller, hardware, iOS и pins соседних репозиториев.
- Не включать motion, gateway операторских команд и любые новые управляющие маршруты.
- Не записывать в git реальные endpoint, токены, SSID, MAC, IP, аппаратный `device_id`, transcript, prompt или raw audio.

## CI для stacked Draft PR

1. GitHub workflow должен запускаться на `pull_request` без ограничения base-веткой `main`.
2. Workflow не должен использовать `pull_request_target`, потому что этот режим выполняется в доверенном контексте репозитория и опасен для недоверенных PR.
3. Права workflow остаются минимальными: только `contents: read`.
4. `actions/checkout` должен работать без сохранения git credentials: `persist-credentials: false`.
5. Единственная команда проверки репозитория в workflow:

```bash
bash bin/ci_validate.sh
```

6. Workflow не должен читать production secrets и не должен обращаться к owner-only файлам.

## Локальная платформа

Обязательные команды перед передачей reviewer:

```bash
PYTHONDONTWRITEBYTECODE=1 bash bin/ci_validate.sh
git diff --check
```

Дополнительные точечные проверки voice-turn контракта, если нужен быстрый повтор без полного CI:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B platform/test_gosha_runtime_events.py
PYTHONDONTWRITEBYTECODE=1 python3 -B platform/test_runtime_event_http_contract.py
PYTHONDONTWRITEBYTECODE=1 python3 -B platform/test_selfhost_runtime_events.py
```

Ожидаемые свойства:

- `voice.turn.phase` агрегируется в `voice_turns` по `trace.correlation_id` и `task.id`.
- `robot_first_audio_out` принимается только от источника `robot`.
- Воспринимаемая задержка считается только как `user_speech_end -> robot_first_audio_out`.
- Для `warm` действуют пороги `1800/2500 ms`, для `cold` — `4500/6000 ms`.
- Для `unknown` результат сохраняется без оценки порогов.
- Запрещённые поля и значения не попадают во вход события, журнал или runtime-снимок.

## Panel smoke

Проверка выполняется без публикации реальных адресов:

1. Открыть панель на `<PANEL_URL>` и подтвердить, что первый экран начинается с карточек роботов.
2. Открыть рабочую страницу выбранного робота через карточку и прямой URL с `robot=<robot_id>`.
3. Проверить, что рабочая страница не смешивает блоки:
   - главный статус;
   - кнопки управления;
   - подключение;
   - личность, модель и голос;
   - клиент, память и инструменты;
   - след устройства.
4. Убедиться, что открытый интерфейс не показывает прямые OTA-URL, voice WebSocket URL, token, raw `device_id`, MAC или IP.
5. Проверить read-only просмотр runtime-events через операторский маршрут и убедиться, что `source.id` прошивки выглядит как `robot-claim-*`, а не как аппаратный идентификатор.

## Android smoke

Проверка выполняется в соседнем проекте Android, но результат фиксируется только как owner-only evidence вне git:

1. Использовать текущий клиент из `<MOBILE_WORKSPACE>` без изменения исходников и pins.
2. Подтвердить, что приложение видит выбранного робота через `mobile API` платформы.
3. Выполнить сценарий network recovery: временная потеря сети, возврат сети, завершение задачи без перезапуска процесса приложения.
4. Проверить, что события Android идут с одним `trace.correlation_id` для задачи восстановления.
5. Проверить, что очередь событий очищается только после успешной доставки или окончательной ошибки схемы.
6. Убедиться, что Android не считает робота подключённым только по настроенному `control`, без server-side подтверждения `connectivity`.

## Firmware smoke

Проверка прошивки остаётся неподвижной:

1. Не выполнять flash, motion, `set_trim`, servo sequence и ручные подталкивания под питанием.
2. Использовать уже установленную прошивку из `<FIRMWARE_WORKSPACE>` без изменения pins и hardware-файлов.
3. Проверить только read-only события: heartbeat, состояние сети, voice-turn фазы и ошибки.
4. Подтвердить, что прошивочные события приходят через `/gosha/events` или совместимый `/xiaozhi/events` с серверной проверкой привязки.
5. Подтвердить, что в runtime-снимке нет raw `device_id`, MAC, IP, SSID, token, transcript, prompt или raw audio.

## Измерение 20 warm + 5 cold

Измерение не подменяет живую приёмку и не должно выдумываться по локальным тестам.

1. Собрать минимум `20` прогретых голосовых оборотов с `voice.warm_state = warm`.
2. Собрать минимум `5` холодных голосовых оборотов с `voice.warm_state = cold`.
3. Каждый оборот засчитывается только при наличии фаз `user_speech_end` и `robot_first_audio_out` в одной паре `trace.correlation_id` + `task.id`.
4. Фаза `robot_first_audio_out` должна быть получена от источника `robot`; серверная `tts_first_audio` не закрывает измерение.
5. В evidence сохранять только обезличенные `task.id`, `trace.correlation_id`, timestamps, фазы, `warm_state` и рассчитанные миллисекунды.
6. Не сохранять transcript, prompt, raw audio, endpoint, SSID, token, raw `device_id`, MAC или IP.
7. Если холодные обороты невозможно получить без управляющего воздействия на production-сервис, cold-часть записать как не выполненную и не заменять её прогретыми данными.
8. Итоговый отчёт должен отдельно показать `p50`, `p95`, количество засчитанных оборотов и количество отбракованных оборотов по причине отсутствия `robot_first_audio_out`.

## Условия передачи reviewer

- Локальный CI пройден.
- `git diff --check` пройден.
- Workflow безопасен для недоверенного PR и не использует production secrets.
- Документация не содержит новых реальных endpoint и owner-only путей.
- Motion/gateway/operator-command изменения отсутствуют.
- Реальные результаты panel/Android/firmware smoke и измерения `20 warm + 5 cold` не заявлены, пока не приложено owner-only evidence вне git.
