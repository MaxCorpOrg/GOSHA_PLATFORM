# Local-Only Policy

## Что считается local-only

- `local_only/runtime_lab/`
- `local_only/snapshots/`
- `local_only/bin/restore_ai_robot_snapshot.sh`
- любые runtime reports, логи, generated state и приватные временные материалы

## Правило

- Эти материалы не должны попадать в git и на GitHub.
- Они нужны для локального тестирования, отката и сравнения с `AI_ROBOT`.
- Если появляется новый локальный stateful-каталог, он должен жить под `local_only/`.

## Зачем это нужно

- сохранить чистый publishable репозиторий;
- не смешивать серверный runtime и исходники;
- не потерять локальные recovery-материалы для старого `AI_ROBOT`.
