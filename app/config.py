"""Runtime configuration loaded from environment / .env via pydantic-settings.

Search *criteria* (rooms, price, floor) intentionally live in SQLite, not here —
they are edited through the web app at runtime. This file only holds deploy-level
settings (tokens, ids, URLs, polling cadence).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Telegram ---
    bot_token: str = ""
    # Common group chat id where new listings are posted (usually negative for groups).
    group_chat_id: int = 0
    # Public HTTPS URL of the web app (served behind Caddy). Required by Telegram Web Apps.
    webapp_url: str = ""

    # --- Storage ---
    db_path: str = "escra.db"

    # --- OLX ---
    olx_base_url: str = "https://www.olx.ua/api/v1/offers/"
    # Numeric city id for Lviv on olx.ua. Read off a listing page ("city_id" query
    # param / "cityId":<n> in HTML). Verify before deploy — see README.
    olx_city_id: int = 5008  # Lviv (verify)
    # Category id for apartment long-term rentals on olx.ua. Verify before deploy.
    olx_category_id: int = 1760
    olx_language: str = "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7"

    # --- Scraper cadence ---
    scrape_interval: int = 180  # seconds between scrape cycles
    page_limit: int = 3         # OLX pages to walk per cycle (40 offers each)
    max_photos: int = 5         # photos per Telegram card

    # --- Web server ---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Web (browser) identities ---
    # The app works both inside Telegram (authenticated via signed initData) and
    # in a plain browser. There's no flag: the mode is chosen per request by
    # whether initData is present. In a browser there is no initData, so the page
    # shows a role picker and the chosen id is sent in the X-User-Id header.
    #
    # Set these to each person's REAL Telegram user id so that reactions made in
    # the browser and inside Telegram count as the SAME person (otherwise the DB
    # would hold two separate identities per person). Find an id via getUpdates
    # after they DM the bot.
    #
    # SECURITY: the browser path is unsigned — anyone who reaches the site and
    # sends one of these two ids in X-User-Id can read/react/edit criteria. Fine
    # for a private two-person tool; do not expose ids you consider secret.
    user_vesnushka_id: int = 1001
    user_sladkoezhka_id: int = 1002

    # --- Auto-deploy ---
    # Secret configured on the GitHub webhook (Settings -> Webhooks -> Secret).
    # Empty disables the /github-push endpoint entirely.
    github_webhook_secret: str = ""
    deploy_branch: str = "main"

    # --- Health-check watchdog ---
    # Real Telegram user id to DM when scripts/healthcheck.py finds /health down
    # (and again when it recovers). That person must have messaged the bot at
    # least once — Telegram won't let a bot open a DM first. Find the id via
    # https://api.telegram.org/bot<token>/getUpdates after they do. 0 disables
    # alerting (the watchdog still runs and logs, it just won't message anyone).
    alert_chat_id: int = 0


settings = Settings()
