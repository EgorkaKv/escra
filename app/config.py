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

    # --- Dev ---
    # Skip Telegram initData verification, so the web app can be opened directly
    # in a browser (no Telegram, no HTTPS tunnel). The browser picks one of the
    # two fixed identities below on first load (see templates/index.html) and
    # sends its id back in X-Dev-User-Id on every request.
    # NEVER enable this in production — it removes all API authentication.
    dev_no_auth: bool = False
    dev_user_vesnushka_id: int = 1001
    dev_user_sladkoezhka_id: int = 1002

    # --- Auto-deploy ---
    # Secret configured on the GitHub webhook (Settings -> Webhooks -> Secret).
    # Empty disables the /github-push endpoint entirely.
    github_webhook_secret: str = ""
    deploy_branch: str = "main"


settings = Settings()
