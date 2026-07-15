"""Async OLX (olx.ua) offers client + offer parser.

Adapted from the reference scraper (reference/sources/olx.py, reference/http.py):
same request shape and param-map parsing, rewritten on httpx.AsyncClient for the
async process and pointed at olx.ua with Ukrainian language headers.

NOTE: olx.ua may use slightly different param keys than olx.pl. The keys below
(price, rooms, floor_select, m) match olx.pl; verify against a live olx.ua
response and adjust if the parsed fields come back empty (see README).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)

# Browser-like headers; OLX serves its public offers API to ordinary clients.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) "
        "Gecko/20100101 Firefox/111.0"
    ),
    "Accept": "application/json, text/plain, */*",
}

ROOMS_MAP = {
    "odnokomnatnye": 1,
    "dvuhkomnatnye": 2,
    "trehkomnatnye": 3,
    "chetyrehkomnatnye": 4,  # "4+" — actual API has no separate 5-or-more bucket
}
_PAGE_SIZE = 40
SLEEP_BETWEEN_PAGES = 2.0


@dataclass
class Listing:
    """Normalized OLX offer, ready for the DB and the Telegram card."""

    external_id: str
    url: str
    title: str
    description: str
    price_value: int | None = None
    price_currency: str | None = None
    district: str | None = None
    rooms: int | None = None
    area: float | None = None
    floor: int | None = None
    is_business: bool = False
    contact_name: str | None = None
    image_urls: list[str] = field(default_factory=list)
    created_time: str | None = None

    def to_row(self) -> dict[str, Any]:
        """Shape matching db.insert_listing() (image_urls serialized there)."""
        return {
            "external_id": self.external_id,
            "url": self.url,
            "title": self.title,
            "price_value": self.price_value,
            "price_currency": self.price_currency,
            "district": self.district,
            "rooms": self.rooms,
            "area": self.area,
            "floor": self.floor,
            "is_business": self.is_business,
            "contact_name": self.contact_name,
            "image_urls": self.image_urls,
            "created_time": self.created_time,
        }


def _param_map(offer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["key"]: p.get("value") or {} for p in offer.get("params") or []}


def _parse_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clean_description(raw: str) -> str:
    return re.sub(r"<br\s*/?>", "\n", raw).strip()


def parse_offer(offer: dict[str, Any], max_photos: int) -> Listing | None:
    """Convert a raw OLX offer into a Listing. Returns None if unusable."""
    offer_id = offer.get("id")
    if offer_id is None:
        return None

    params = _param_map(offer)
    price = params.get("price") or {}
    location = offer.get("location") or {}
    contact = offer.get("contact") or {}

    rooms_key = (params.get("number_of_rooms_string") or {}).get("key")
    area = _parse_int((params.get("total_area") or {}).get("key"))
    floor = _parse_int((params.get("floor") or {}).get("key"))

    photos: list[str] = []
    for photo in offer.get("photos") or []:
        link = photo.get("link") or ""
        if link:
            photos.append(
                link.format(width=photo.get("width", 1024), height=photo.get("height", 768))
            )
        if len(photos) >= max_photos:
            break

    district = (location.get("district") or {}).get("name") or (
        location.get("city") or {}
    ).get("name")

    return Listing(
        external_id=str(offer_id),
        url=offer.get("url", "") or "",
        title=offer.get("title", "") or "",
        description=_clean_description(offer.get("description", "") or ""),
        price_value=_parse_int(price.get("value")),
        price_currency=price.get("currency"),
        district=district,
        rooms=ROOMS_MAP.get(rooms_key),
        area=float(area) if area is not None else None,
        floor=floor,
        is_business=bool(offer.get("business")),
        contact_name=contact.get("name"),
        image_urls=photos,
        created_time=offer.get("last_refresh_time") or offer.get("created_time"),
    )


class OlxClient:
    """Fetches recent apartment-rental offers from the olx.ua JSON API."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        headers = {**DEFAULT_HEADERS, "Accept-Language": settings.olx_language}
        self._client = client or httpx.AsyncClient(headers=headers, timeout=15.0)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _fetch_page(self, offset: int) -> list[dict[str, Any]]:
        resp = await self._client.get(
            settings.olx_base_url,
            params={
                "category_id": settings.olx_category_id,
                "city_id": settings.olx_city_id,
                "offset": offset,
                "limit": _PAGE_SIZE,
                "sort_by": "created_at:desc",
            },
        )
        resp.raise_for_status()
        logger.debug("OLX response status=%s url=%s", resp.status_code, resp.request.url)
        data = resp.json()
        offers = data.get("data") or []
        logger.debug("OLX raw payload keys=%s total_count=%s offers_in_page=%s",
                      list(data.keys()), data.get("total_count"), len(offers))
        return offers

    async def fetch_recent(self, page_limit: int | None = None) -> list[Listing]:
        """Walk newest-first pages and return parsed listings."""
        page_limit = page_limit or settings.page_limit
        listings: list[Listing] = []
        for page in range(page_limit):
            offset = page * _PAGE_SIZE
            try:
                offers = await self._fetch_page(offset)
            except Exception as exc:  # network / decode — stop this cycle
                logger.error("OLX fetch failed at offset=%s: %s", offset, exc)
                break

            logger.info("OLX page %s: %s offers", page + 1, len(offers))
            if not offers:
                break

            for offer in offers:
                listing = parse_offer(offer, settings.max_photos)
                if listing is None:
                    logger.debug("OLX offer %s dropped by parse_offer (no id)", offer.get("id"))
                    continue
                logger.debug(
                    "OLX parsed offer id=%s rooms=%s price=%s %s floor=%s title=%r",
                    listing.external_id, listing.rooms, listing.price_value,
                    listing.price_currency, listing.floor, listing.title[:60],
                )
                listings.append(listing)

            if len(offers) < _PAGE_SIZE:
                break
            await asyncio.sleep(SLEEP_BETWEEN_PAGES)
        return listings
