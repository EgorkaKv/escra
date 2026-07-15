"""Scraper task: fetch OLX offers, filter by the DB criteria, store new ones,
and push each newcomer to the Telegram group.

`scrape_once()` runs a single cycle (used by tests / manual runs). `run_loop()`
is the long-lived background task started from main.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram.ext import Application

from . import bot, db
from .config import settings
from .olx import Listing, OlxClient

logger = logging.getLogger(__name__)


def _passes(listing: Listing, criteria: dict[str, Any]) -> bool:
    """Apply the editable search criteria. Missing data errs toward exclusion
    for hard filters (rooms), and toward inclusion when we can't compare (price
    in a different currency)."""
    rooms_min = criteria.get("rooms_min")
    rooms_max = criteria.get("rooms_max")
    if rooms_min is not None or rooms_max is not None:
        if listing.rooms is None:
            return False
        if rooms_min is not None and listing.rooms < rooms_min:
            return False
        if rooms_max is not None and listing.rooms > rooms_max:
            return False

    price_max = criteria.get("price_max")
    if price_max and listing.price_value is not None:
        same_currency = (
            not criteria.get("price_currency")
            or listing.price_currency == criteria.get("price_currency")
        )
        if same_currency and listing.price_value > price_max:
            return False

    # "не перший поверх": treat ground (0) and 1st as first floor.
    if criteria.get("exclude_first_floor") and listing.floor is not None:
        if listing.floor <= 1:
            return False

    return True


async def scrape_once(app: Application | None = None, notify: bool = True) -> int:
    """Run one scrape cycle. Returns the count of newly stored listings."""
    criteria = await asyncio.to_thread(db.get_criteria)
    client = OlxClient()
    try:
        listings = await client.fetch_recent()
    finally:
        await client.aclose()

    new_count = 0
    for listing in listings:
        if not _passes(listing, criteria):
            continue
        if await asyncio.to_thread(db.listing_exists, listing.external_id):
            continue
        listing_id = await asyncio.to_thread(db.insert_listing, listing.to_row())
        if listing_id is None:
            continue  # raced with another insert
        new_count += 1
        if notify and app is not None:
            row = await asyncio.to_thread(db.get_listing, listing_id)
            if row:
                await bot.send_listing(app, listing_id, row)

    logger.info("Scrape cycle done: %s new listings", new_count)
    return new_count


async def run_loop(app: Application) -> None:
    """Background loop: scrape every SCRAPE_INTERVAL seconds, forever."""
    logger.info("Scraper loop started (interval=%ss)", settings.scrape_interval)
    while True:
        try:
            await scrape_once(app=app, notify=True)
        except Exception as exc:
            logger.exception("Scrape cycle failed: %s", exc)
        await asyncio.sleep(settings.scrape_interval)
