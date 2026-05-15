# NEW CHAT CHECKPOINT

Короткая точка входа для следующего агента в `GOSHA_PLATFORM`.

## Сначала прочитать

1. `../AGENTS.md`
2. `NEW_CHAT_CHECKPOINT_RU.md`
3. `AGENT_CHECKPOINT_RU.md`
4. `PROJECT_STATUS_RU.md`
5. Если задача про панель:
   - `../platform/AGENTS.md`
   - `GOSHA_ASSISTANT_CONTROL_PANEL_SPEC_RU.md`
6. Если задача про сервер:
   - `GOSHA_SERVER_DEPLOY_RU.md`
   - `../ops/AGENTS.md`

## Последняя зафиксированная точка

- Ветка:
  - `agent/bootstrap-gosha`
- Последний ключевой коммит:
  - `f5bbc73` `Добавить панель ассистента Гоша и серверный прокси`

## Что уже сделано

- Поднят полный подготовительный серверный контур:
  - `gosha-panel.service`
  - `gosha-agent-gateway.service`
  - `gosha-backend.service`
  - `gosha-observer.timer`
- Голосовой `WebSocket` уже слушает `151.241.228.232:18080/xiaozhi/v1/`
- Причина старого падения `backend` устранена:
  - создаётся `data/.config.yaml`
  - загружается `SenseVoiceSmall`
  - модели больше не перекрываются пустым каталогом
- Реализована панель ассистента v1:
  - составные профили
  - новые операторские маршруты
  - привязка профилей к роботу
  - честные отложенные секции для экрана, пробуждения и базы знаний
- Добавлен внутренний `OpenAI`-совместимый прокси панели.
- На сервере посеян базовый профиль `assistant-gosha-default` и связка для `gosha-main`.

## Где остановились

- Панель, шлюз и совместимый `backend` уже живы.
- Главный блокер сейчас один:
  - в `/opt/gosha_platform/runtime/env/providers.env` нет рабочего ключа поставщика ИИ
- Поэтому реальный вызов модели пока возвращает ошибку аутентификации, и робот ещё не доведён до живого голосового ответа.

## Что делать следующим

1. Заполнить `providers.env` рабочим ключом поставщика.
2. Выполнить:

```bash
cd /opt/gosha_platform/app
bash ops/install_server.sh --phase panel
```

3. Проверить:
   - `POST /api/internal/openai/v1/chat/completions`
   - что `gosha-main` отвечает голосом
   - что предупреждение по ключу ушло из панели
4. Потом продолжить вторую очередь панели и связку с `GOSHA_FIRMWARE`.
