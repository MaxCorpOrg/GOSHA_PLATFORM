# AGENTS.md

Эта папка отвечает за репозиторные переопределения совместимого серверного узла `Гоша`.

## Что здесь лежит

- `edge.py`
  - расширение синтеза речи `EdgeTTS`
  - применение имени голоса, скорости речи и высоты голоса
- `fun_local.py`
  - локальное переопределение для совместимого контура, если понадобится воспроизводимое исправление через репозиторий

## Как здесь работать

1. Сначала прочитай:
   - `/home/max/GOSHA_PLATFORM/AGENTS.md`
   - `/home/max/GOSHA_PLATFORM/docs/GOSHA_PROJECT_MAP_RU.md`
   - `/home/max/GOSHA_PLATFORM/docs/PROJECT_STATUS_RU.md`
2. Не исправляй `TTS`, `ASR` или `LLM` ручными правками внутри контейнера.
3. Все изменения должны быть воспроизводимы из git:
   - через эту папку;
   - через `backend/selfhost-backend.compose.yml`;
   - через `ops/install_server.sh`.

## Что обновлять после значимой правки

- `/home/max/GOSHA_PLATFORM/docs/PROJECT_STATUS_RU.md`
- `/home/max/GOSHA_PLATFORM/docs/AGENT_CHECKPOINT_RU.md`
- `/home/max/GOSHA_PLATFORM/docs/GOSHA_PROJECT_MAP_RU.md`, если изменилась карта голосового контура
