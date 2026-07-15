"""Single-process entrypoint: Telegram bot + scraper loop + FastAPI, one asyncio loop.

Run with:  uv run python -m app.main
Under systemd this is the ExecStart target.
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from . import bot, db, scraper
from .config import settings
from .webapp.api import app as web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("escra")


async def main() -> None:
    db.init_db()

    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not set (see .env / .env.example)")

    application = bot.build_application()
    await application.initialize()
    await application.start()
    # Long-polling to receive callback queries (like/hide taps in the group).
    await application.updater.start_polling(drop_pending_updates=True)

    scraper_task = asyncio.create_task(scraper.run_loop(application))

    config = uvicorn.Config(
        web_app, host=settings.host, port=settings.port, log_level="info", loop="asyncio"
    )
    server = uvicorn.Server(config)

    logger.info("Escra started on %s:%s", settings.host, settings.port)
    try:
        await server.serve()  # blocks until the process is asked to stop
    finally:
        logger.info("Shutting down…")
        scraper_task.cancel()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
