# GOSHA OAUTH REVIEWER RUNBOOK

Этот документ описывает внешний сервис ручной AI-проверки кода для `Pull Request` через `GitHub OAuth`.

## Зачем он нужен

- не хранить модельные ключи в secrets самого репозитория;
- не завязывать второй сервис проверки на `GitHub Actions` внутри проекта;
- дать оператору или разработчику ручной запуск review через отдельный web-сервис;
- брать доступ к PR через `GitHub OAuth`;
- по возможности использовать уже авторизованный локальный `Codex CLI`;
- при необходимости оставлять резервный путь через совместимый HTTP-backend модели.

## Где лежит код

- сервис:
  - `/home/max/GOSHA_PLATFORM/oauth_reviewer`
- локальный запуск:
  - `/home/max/GOSHA_PLATFORM/bin/run_local_oauth_reviewer.sh`

## Что умеет текущая версия

- вход через `GitHub OAuth`;
- хранение самого `GitHub OAuth` токена в серверном файловом хранилище сеансов;
- хранение в cookie только идентификатора серверного сеанса;
- безопасный возврат после OAuth только на внутренние пути этого сервиса;
- ограничение репозиториев через разрешённый список в env;
- загрузка PR и списка изменённых файлов через GitHub REST API;
- загрузка review-правил из `AGENTS.md` репозитория;
- генерация review через один из двух backend-вариантов:
  - локальный `Codex CLI`, если он установлен и уже авторизован;
  - совместимый HTTP-backend модели, если он задан в env;
- режим:
  - `preview` — только показать review;
  - `publish` — опубликовать review в сам `Pull Request`.
- опубликованный review в текущей схеме идёт от имени того `GitHub` пользователя, который вошёл через `OAuth`.
- запуск review теперь идёт как фоновая задача сервиса:
  - страница reviewer показывает текущий статус;
  - видно журнал этапов;
  - видно, что задача всё ещё выполняется, а не зависла без ответа.
- reviewer теперь дополнительно показывает:
  - текущий этап задачи;
  - сколько времени прошло с начала;
  - сколько времени прошло с последнего обновления;
  - явную пометку о возможном зависании, если долго нет новых событий.
- окно `Состояние review` и окно `Результат review` теперь имеют фиксированную высоту и внутренний скролл, поэтому страница не разрастается вниз.
- шумные внутренние предупреждения `Codex CLI` по плагинам и иконкам теперь отфильтрованы из журнала reviewer.
- reviewer теперь может открывать отдельное локальное окно терминала-наблюдателя:
  - в нём виден точный вызов `codex exec`;
  - туда же идёт живой вывод задачи;
  - после завершения окно можно оставить открытым для просмотра хвоста.
- reviewer теперь использует отдельное имя browser cookie:
  - `gosha_oauth_reviewer_session`
  - это нужно, чтобы reviewer и executor на одном `127.0.0.1` не перетирали сеансы друг друга

## Что нужно в env

Минимально:

- `OAUTH_REVIEWER_SESSION_SECRET`
- `OAUTH_REVIEWER_SESSION_STORE_DIR`
- `OAUTH_REVIEWER_SESSION_TTL_SECONDS`
- `GITHUB_OAUTH_CLIENT_ID`
- `GITHUB_OAUTH_CLIENT_SECRET`
- `GITHUB_OAUTH_REDIRECT_URI`
- один из backend-вариантов reviewer:
  - или локально авторизованный `Codex CLI`
  - или `OPENAI_API_KEY` / совместимый ключ для HTTP-backend

Дополнительно важно:

- `GITHUB_OAUTH_SCOPE`
  - для `GOSHA_PLATFORM` лучше сразу использовать `read:user repo`
  - это работает и для приватного репозитория, и для связки reviewer -> executor
- `OAUTH_REVIEWER_ALLOWED_REPOS`
  - например: `MaxCorpOrg/GOSHA_PLATFORM`
- `OAUTH_REVIEWER_REPO_PATH`
  - локальный путь до репозитория, чтобы сервис мог читать `AGENTS.md`
- `OAUTH_REVIEWER_BACKEND`
  - `auto` — сначала локальный `Codex CLI`, потом HTTP-backend
  - `codex_cli` — только локальный `Codex CLI`
  - `openai_api` — только HTTP-backend
- `OAUTH_REVIEWER_CODEX_COMMAND`
  - путь до локального `codex`, если нужен режим через уже авторизованный `Codex CLI`
- `OAUTH_REVIEWER_CODEX_MODEL`
  - рекомендуемое фиксированное значение: `gpt-5.4`
- `OAUTH_REVIEWER_CODEX_REASONING_EFFORT`
  - рекомендуемое фиксированное значение: `xhigh`
- `OAUTH_REVIEWER_OPEN_TERMINAL`
  - `true`, если нужно автоматически открывать локальное окно терминала для каждой задачи review
- `OAUTH_REVIEWER_TERMINAL_COMMAND`
  - явный путь до терминала, например `/usr/bin/gnome-terminal`
- `OAUTH_REVIEWER_TERMINAL_RUNTIME_DIR`
  - каталог живых журналов терминального наблюдения

Пример лежит в:

- `/home/max/GOSHA_PLATFORM/oauth_reviewer/.env.example`

## Локальный запуск

1. Установить зависимости сервиса:

```bash
cd /home/max/GOSHA_PLATFORM
python3 -m pip install -r oauth_reviewer/requirements.txt
```

2. Экспортировать env-переменные.

3. Поднять сервис:

```bash
cd /home/max/GOSHA_PLATFORM
bash bin/run_local_oauth_reviewer.sh
```

4. Открыть:

```text
http://127.0.0.1:18910
```

На экране reviewer теперь видно:

- текущую задачу review;
- статус выполнения;
- прошедшее время;
- журнал этапов;
- итоговый текст review после завершения.
- Если включён `OAUTH_REVIEWER_OPEN_TERMINAL=true`, при старте каждой задачи дополнительно открывается локальное окно терминала с живым выводом `Codex CLI`.
- Для общей проверки reviewer/executor на машине теперь есть отдельная команда:

```bash
bash /home/max/GOSHA_PLATFORM/bin/check_oauth_agents.sh
```

Если нужен постоянный автозапуск reviewer на этой машине без ручного запуска из терминала:

```bash
cd /home/max/GOSHA_PLATFORM
bash bin/install_local_oauth_reviewer_user_service.sh
```

## Как создать GitHub OAuth App

На стороне GitHub:

1. Открыть `Settings -> Developer settings -> OAuth Apps`.
2. Создать новое приложение.
3. Указать:
   - `Homepage URL` — адрес сервиса
   - `Authorization callback URL` — `<адрес сервиса>/auth/github/callback`
4. Получить:
   - `Client ID`
   - `Client Secret`
5. Положить их в env сервиса.

## Ограничения текущей версии

- это ручной сервис проверки, а не полностью автономный merge-бот;
- он не делает коммиты и не правит код сам;
- review публикуется не от отдельного bot-аккаунта, а от вошедшего `GitHub` пользователя;
- он не подменяет официальный `Codex code review` в GitHub, а дополняет его;
- если выбран HTTP-backend модели, доступ к нему всё равно остаётся серверным и требует внешний ключ вне репозитория;
- если выбран локальный `Codex CLI`, reviewer зависит от того, что на машине уже есть рабочий вход `codex login status`;
- в текущей версии автоматический запуск по webhook не включён сознательно, чтобы не потерять управляемость.
- для автоматических правок после review в этом репозитории теперь есть отдельный сервис:
  - `oauth_executor/`
  - `docs/GOSHA_OAUTH_EXECUTOR_RUNBOOK_RU.md`

## Безопасность

- не логировать токены `GitHub OAuth` и `OpenAI`;
- не хранить токены `GitHub OAuth` внутри client-side cookie браузера;
- если используется HTTP-backend, держать его ключ только в env или внешнем секретном хранилище;
- ограничивать сервис разрешённым списком репозиториев;
- для публичного адреса включать `HTTPS` и `OAUTH_REVIEWER_COOKIE_SECURE=true`.
