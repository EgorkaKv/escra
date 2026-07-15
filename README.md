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
  (HMAC-проверка), поэтому видно, кто из двоих что отметил.

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
    api.py           FastAPI роуты + HMAC-валидация initData
    templates/index.html   одностраничный Web App (инлайн JS)
deploy/
  escra.service      systemd unit
  Caddyfile          reverse proxy + авто-TLS
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

Если после запуска поля объявлений (комнаты/этаж/цена) приходят пустыми — значит
olx.ua использует другие ключи `params`, чем olx.pl. Распечатайте сырой ответ
(`OlxClient._fetch_page`) и поправьте ключи в `app/olx.py`
(`rooms`, `floor_select`, `m`, `price`).

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

Критерии поиска (комнаты, макс. цена, валюта, «не первый этаж») хранятся в БД и
**меняются через вкладку «Настройки» в веб-аппе** — не в `.env`. Дефолт при
первом запуске: 2 комнаты, до 16000 UAH, не первый этаж.

## Деплой на дроплет (DigitalOcean)

```bash
# на дроплете
git clone <repo> /opt/escra && cd /opt/escra
uv sync
cp .env.example .env && nano .env        # заполнить

# systemd
sudo cp deploy/escra.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now escra
sudo journalctl -u escra -f              # логи

# Caddy (TLS + reverse proxy). DNS A-запись домена -> IP дроплета.
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # заменить домен внутри
sudo systemctl reload caddy
```

Один процесс, один деплой, один лог. Бэкап базы — обычный `cp escra.db` по крону.

[python-telegram-bot]: https://python-telegram-bot.org/
[uv]: https://docs.astral.sh/uv/
[@BotFather]: https://t.me/BotFather
