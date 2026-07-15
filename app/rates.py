"""NBU exchange rates (UAH per unit of foreign currency).

OLX rental listings are quoted in UAH, USD, or EUR; the price criteria in the
DB is one currency too. To compare them at all, everything is converted to
UAH here first. Cached in-memory since the NBU rate only changes once a day —
a fetch failure reuses the last known rate rather than breaking price
filtering for the whole scrape cycle.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

_NBU_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchangenew"
_CACHE_TTL = 12 * 3600  # NBU publishes once a day; refresh a couple of times a day

_cache: dict[str, tuple[float, float]] = {}  # currency -> (rate, fetched_at monotonic)


async def get_rate(currency: str) -> float | None:
    """UAH per 1 unit of `currency`. Returns 1.0 for UAH. Returns None only if
    no rate — fresh or stale — has ever been fetched for this currency."""
    if currency == "UAH":
        return 1.0

    cached = _cache.get(currency)
    if cached and time.monotonic() - cached[1] < _CACHE_TTL:
        return cached[0]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_NBU_URL, params={"json": "", "valcode": currency})
            resp.raise_for_status()
            data = resp.json()
        rate = float(data[0]["rate"])
    except Exception as exc:
        if cached:
            logger.warning("NBU rate fetch failed for %s, reusing stale rate: %s", currency, exc)
            return cached[0]
        logger.error("NBU rate fetch failed for %s, no cached rate available: %s", currency, exc)
        return None

    _cache[currency] = (rate, time.monotonic())
    logger.debug("NBU rate for %s: %s UAH", currency, rate)
    return rate
