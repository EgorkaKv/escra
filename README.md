# Escra — OLX Lviv rental scraper + Telegram Web App

Автоматически следит за арендой квартир во Львове на [OLX](https://www.olx.ua),
фильтрует по вашим критериям и присылает новинки в общий Telegram-чат. Дальнейшая
работа с вариантом (лайк / скрыть, кто что отметил) идёт в Telegram Web App поверх
общей базы — нас двое, и оба видим решения друг друга.

## Как это работает

Один Python-процесс на дроплете, внутри — три задачи в одном asyncio event loop:

- **Скрапер** — раз в `SCRAPE_INTERVAL` секунд дёргает внутренний JSON API OLX
  (`/api/v1/offers/`), парсит объявления, фильтрует по критериям из БД и сохраняет
  новые (дедуп по `external_id`).
- **Бот** ([python-telegram-bot]) — на каждую новинку шлёт в группу карточку с
  фото (до `MAX_PHOTOS`) и inline-кнопками «Открыть / Нравится / Скрыть / В
  приложении». Лайки/скрытия пишутся в БД по `user.id`.
- **FastAPI** — отдаёт одностраничный Web App (3 вкладки: Новые / Понравилось /
  Скрытые + Настройки) и JSON API. Аутентификация — по Telegram `initData`
  (HMAC-проверка), поэтому видно, кто из двоих что отметил. Фото в карточке —
  карусель (свайп/точки) с полноэкранным лайтбоксом по тапу. Тот же процесс
  отдаёт служебные `/health` (для watchdog) и `/github-push` (автодеплой).

```
OLX API ──> scraper ──> SQLite ──> bot ──> Telegram group
                          ▲          │
                          └── FastAPI Web App (like/hide, criteria)
```

Фото не скачиваются — в базе хранятся только CDN-ссылки OLX, а Telegram и браузер
тянут их сами.

## Стек

Python 3.12 · [uv] · FastAPI + uvicorn · python-telegram-bot · httpx ·
SQLite (stdlib) · Jinja2 · Caddy (TLS) + systemd на дроплете.

## Структура

```
app/
  main.py            точка входа: bot + scraper + uvicorn в одном loop
  config.py          настройки из .env (pydantic-settings)
  db.py              SQLite: схема + доступ (listings / reactions / criteria)
  olx.py             async-клиент OLX + парсер offer -> Listing
  scraper.py         цикл: критерии -> fetch -> фильтр -> сохранить -> уведомить
  bot.py             отправка карточек, inline-кнопки, callback лайк/скрыть
  webapp/
    api.py           FastAPI роуты, HMAC-валидация initData, /health, /github-push
    templates/index.html   одностраничный Web App (инлайн JS): карусель фото,
                            fullscreen-лайтбокс, dev-режим без Telegram
scripts/
  healthcheck.py     отдельный процесс: пингует /health, шлёт алерт в Telegram
deploy/
  escra.service            systemd unit самого приложения
  escra-healthcheck.service / .timer   systemd-таймер для healthcheck.py
  deploy.sh                git pull + uv sync + restart, вызывается вебхуком
  Caddyfile                reverse proxy + авто-TLS
reference/           исходный скрапер (olx.pl), взят за образец
```

## Локальный запуск

```bash
uv sync
cp .env.example .env      # заполнить BOT_TOKEN, GROUP_CHAT_ID, WEBAPP_URL, OLX_*
uv run python -m app.main
```

Проверить один цикл скрапера без бота:

```bash
uv run python -c "import asyncio; from app import db, scraper; db.init_db(); print(asyncio.run(scraper.scrape_once(notify=False)))"
```

### Открыть Web App без Telegram (dev-режим)

По умолчанию `/api/*` требует заголовок `X-Telegram-Init-Data`, который
подставляет `telegram-web-app.js` только когда страница открыта изнутри
Telegram (кнопка Web App). Открытие `http://127.0.0.1:8000` в обычном браузере
без этого даёт `401`.

Чтобы работать с интерфейсом локально без Telegram, включите в `.env`:

```
DEV_NO_AUTH=true
DEV_USER_VESNUSHKA_ID=1001
DEV_USER_SLADKOEZHKA_ID=1002
```

При первом открытии страница без `initData` покажет экран выбора: «Я Веснушка» /
«Я Сладкоєжка». Выбор сохраняется в `localStorage` браузера и дальше шлётся в
заголовке `X-Dev-User-Id` на каждый запрос — так лайки/скрытия и в dev-режиме
привязываются к конкретному человеку. Внутри Telegram (когда есть настоящий
`initData`) всё работает как раньше, через HMAC-проверку.

**`DEV_NO_AUTH=true` на проде включать нельзя** — это отключает всю
аутентификацию API.

## Настройка Telegram

1. Создайте бота у [@BotFather], получите `BOT_TOKEN`.
2. Добавьте бота в общую группу. Узнайте `GROUP_CHAT_ID`:
   добавьте бота, напишите в группу, откройте
   `https://api.telegram.org/bot<token>/getUpdates` и возьмите `chat.id`
   (у супергрупп начинается с `-100`).
3. В @BotFather → *Bot Settings → Menu Button* (или *Configure Web App*) задайте
   URL веб-аппа = ваш `WEBAPP_URL` (обязательно HTTPS).

## Как определить `OLX_CITY_ID` и `OLX_CATEGORY_ID`

Значения в `.env.example` — ориентировочные, **проверьте перед деплоем**:

1. Откройте на olx.ua страницу *Львов → Недвижимость → Квартиры → Долгосрочная
   аренда*.
2. В DevTools → Network найдите запрос к `api/v1/offers/` и посмотрите
   query-параметры `city_id` и `category_id`. Либо поищите `"city_id"` в HTML
   страницы.
3. Впишите их в `.env`.

Если после запуска поля объявлений (комнаты/этаж/площадь) приходят пустыми —
значит olx.ua поменял ключи `params` (так уже было: реальные ключи оказались
`number_of_rooms_string` / `floor` / `total_area`, а не `rooms` / `floor_select`
/ `m`, скопированные с olx.pl). Распечатайте сырой ответ (`OlxClient._fetch_page`,
поле `params` объекта offer) и поправьте ключи в `parse_offer()` (`app/olx.py`).

## Переменные окружения

| Переменная | Назначение | Дефолт |
|---|---|---|
| `BOT_TOKEN` | токен бота от BotFather | — |
| `GROUP_CHAT_ID` | id общей группы | — |
| `WEBAPP_URL` | HTTPS-URL веб-аппа | — |
| `DB_PATH` | путь к файлу SQLite | `escra.db` |
| `OLX_CITY_ID` | numeric id Львова | `5008` |
| `OLX_CATEGORY_ID` | id категории аренды | `1760` |
| `SCRAPE_INTERVAL` | сек между циклами | `180` |
| `PAGE_LIMIT` | страниц OLX за цикл (по 40) | `3` |
| `MAX_PHOTOS` | фото в карточке | `5` |
| `HOST` / `PORT` | адрес uvicorn (за Caddy) | `127.0.0.1` / `8000` |
| `DEV_NO_AUTH` | пропускать проверку initData (см. «dev-режим» выше) | `false` |
| `DEV_USER_VESNUSHKA_ID` / `DEV_USER_SLADKOEZHKA_ID` | синтетические id для dev-picker'а в браузере | `1001` / `1002` |
| `GITHUB_WEBHOOK_SECRET` | секрет вебхука GitHub, пусто = `/github-push` выключен | — |
| `DEPLOY_BRANCH` | ветка, пуш в которую триггерит автодеплой | `main` |
| `ALERT_CHAT_ID` | настоящий Telegram user id, куда `healthcheck.py` шлёт алерты | `0` (алерты выключены) |

Критерии поиска (комнаты, макс. цена, валюта, «не первый этаж») хранятся в БД и
**меняются через вкладку «Настройки» в веб-аппе** — не в `.env`. Дефолт при
первом запуске: 2 комнаты, до 16000 UAH, не первый этаж.

## Автодеплой по пушу в GitHub

`POST /github-push` принимает вебхук GitHub, проверяет HMAC-подпись тела
(`X-Hub-Signature-256`, секрет = `GITHUB_WEBHOOK_SECRET`) и на push в
`DEPLOY_BRANCH` асинхронно запускает `deploy/deploy.sh` в фоне, не блокируя
ответ вебхуку:

```
git fetch + git merge --ff-only origin/<branch>  →  uv sync  →  systemctl restart escra
```

`systemctl restart` — последняя команда: к этому моменту git pull и uv sync уже
выполнены, поэтому даже когда systemd убивает cgroup со старым процессом (а
внутри него — сам ещё выполняющийся `deploy.sh`), перезапуск уже поставлен в
очередь и происходит независимо.

Настройка:

1. На GitHub — репозиторий → Settings → Webhooks → ваш хук на
   `https://<домен>/github-push`: задайте **Secret**, content type
   `application/json`, событие — только **push**.
2. На дроплете впишите тот же секрет в `.env` (`GITHUB_WEBHOOK_SECRET=...`) и
   один раз перезапустите сервис: `systemctl restart escra`.
3. Дальше `git push` в `DEPLOY_BRANCH` сам подтягивает код и перезапускает
   сервис. Смотреть процесс — `journalctl -u escra -f`.

Без секрета в `.env` эндпоинт отвечает `503` — деплой по вебхуку не работает,
пока секрет не задан на обеих сторонах.

## Health-check и алерты о падении

`GET /health` — публичный, без авторизации, дёргает БД и отвечает `200
{"status": "ok"}` либо `503`, если БД недоступна.

`scripts/healthcheck.py` — отдельный процесс (не часть `app.main`, не делит с
ним event loop), запускается по расписанию systemd-таймером
(`deploy/escra-healthcheck.timer`, раз в минуту). Он:

- дёргает `/health`;
- сравнивает с прошлым состоянием (хранится в `.healthcheck_state.json` рядом
  с БД), чтобы слать **одно** сообщение на переход "работал → упал" и одно на
  "упал → снова работает" — а не спамить каждую минуту, пока приложение лежит;
- на изменение состояния шлёт сообщение через Bot API напрямую (`sendMessage`,
  без зависимости от `python-telegram-bot`) в приватный чат с
  `ALERT_CHAT_ID`.

`ALERT_CHAT_ID` — это **настоящий** Telegram user id получателя алертов, не
`DEV_USER_*_ID` из dev-режима (те синтетические и ни на что в Telegram не
указывают). Получатель должен хотя бы раз написать боту в личку — иначе бот не
может первым открыть DM. Узнать id: после того как он напишет,
откройте `https://api.telegram.org/bot<token>/getUpdates` и возьмите `message.from.id`.

Установка на дроплете:

```bash
cp deploy/escra-healthcheck.service deploy/escra-healthcheck.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now escra-healthcheck.timer
systemctl list-timers | grep escra   # проверить, что таймер взведён
```

## Деплой на дроплет (DigitalOcean)

Ниже — под тот же layout, что настроен сейчас: репозиторий клонирован в
`/root/escra` под пользователем `root` (пути в `deploy/escra*.service` на это
рассчитаны; при другом пользователе/пути поправьте их).

```bash
# на дроплете
git clone <repo> /root/escra && cd /root/escra
uv sync
cp .env.example .env && nano .env        # заполнить

# systemd: сам сервис
cp deploy/escra.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now escra
journalctl -u escra -f                   # логи

# systemd: health-check таймер (см. «Health-check и алерты о падении» выше)
cp deploy/escra-healthcheck.service deploy/escra-healthcheck.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now escra-healthcheck.timer

# Caddy (TLS + reverse proxy). DNS A-запись домена -> IP дроплета,
# порты 80/443 открыты в файрволе.
cp deploy/Caddyfile /etc/caddy/Caddyfile   # заменить домен внутри, если другой
systemctl reload caddy
```

Дальнейшие деплои — просто `git push` в `DEPLOY_BRANCH`, если настроен вебхук
(см. «Автодеплой по пушу в GitHub» выше); вручную — `git pull && uv sync &&
systemctl restart escra`.

Один процесс, один деплой, один лог. Бэкап базы — обычный `cp escra.db` по крону.

[python-telegram-bot]: https://python-telegram-bot.org/
[uv]: https://docs.astral.sh/uv/
[@BotFather]: https://t.me/BotFather
