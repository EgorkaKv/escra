"""SQLite storage: schema, connection helper, and typed access functions.

The DB holds three tables:
  * listings  — scraped OLX offers (deduped by external_id)
  * reactions — per-user like/hide, keyed by (listing_id, user_id)
  * criteria  — a single editable row of search filters (edited via the web app)

Access is synchronous sqlite3; callers in the async process wrap blocking calls
with asyncio.to_thread. sqlite3 connections are per-call (cheap) to stay
thread-safe across the bot/scraper/web tasks.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id   TEXT UNIQUE NOT NULL,
    url           TEXT,
    title         TEXT,
    price_value   INTEGER,
    price_currency TEXT,
    district      TEXT,
    rooms         INTEGER,
    area          REAL,
    floor         INTEGER,
    is_business   INTEGER DEFAULT 0,
    contact_name  TEXT,
    image_urls    TEXT,          -- JSON array of CDN links (up to max_photos)
    created_time  TEXT,
    fetched_at    TEXT
);

CREATE TABLE IF NOT EXISTS reactions (
    listing_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    reaction    TEXT NOT NULL,   -- 'like' | 'hide'
    updated_at  TEXT,
    PRIMARY KEY (listing_id, user_id)
);

CREATE TABLE IF NOT EXISTS criteria (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    rooms_min           INTEGER,
    rooms_max           INTEGER,
    price_max           INTEGER,
    price_currency      TEXT,
    exclude_first_floor INTEGER DEFAULT 1,
    updated_at          TEXT
);
"""

DEFAULT_CRITERIA = {
    "rooms_min": 2,
    "rooms_max": 2,
    "price_max": 16000,
    "price_currency": "UAH",
    "exclude_first_floor": 1,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables and seed the single criteria row if missing."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT id FROM criteria WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO criteria
                   (id, rooms_min, rooms_max, price_max, price_currency,
                    exclude_first_floor, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?)""",
                (
                    DEFAULT_CRITERIA["rooms_min"],
                    DEFAULT_CRITERIA["rooms_max"],
                    DEFAULT_CRITERIA["price_max"],
                    DEFAULT_CRITERIA["price_currency"],
                    DEFAULT_CRITERIA["exclude_first_floor"],
                    _now(),
                ),
            )
        conn.commit()


# --- criteria ---------------------------------------------------------------

def get_criteria() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM criteria WHERE id = 1").fetchone()
    return dict(row) if row else dict(DEFAULT_CRITERIA)


def update_criteria(data: dict[str, Any]) -> dict[str, Any]:
    """Update editable criteria fields; ignores unknown keys."""
    fields = ["rooms_min", "rooms_max", "price_max", "price_currency", "exclude_first_floor"]
    updates = {k: data[k] for k in fields if k in data}
    if updates:
        assignments = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [_now()]
        with connect() as conn:
            conn.execute(
                f"UPDATE criteria SET {assignments}, updated_at = ? WHERE id = 1", values
            )
            conn.commit()
    return get_criteria()


# --- listings ---------------------------------------------------------------

def listing_exists(external_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM listings WHERE external_id = ?", (external_id,)
        ).fetchone()
    return row is not None


def insert_listing(listing: dict[str, Any]) -> int | None:
    """Insert a new listing. Returns its row id, or None if it already existed."""
    with connect() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO listings
                   (external_id, url, title, price_value, price_currency, district,
                    rooms, area, floor, is_business, contact_name, image_urls,
                    created_time, fetched_at)
                   VALUES (:external_id, :url, :title, :price_value, :price_currency,
                    :district, :rooms, :area, :floor, :is_business, :contact_name,
                    :image_urls, :created_time, :fetched_at)""",
                {
                    **listing,
                    "image_urls": json.dumps(listing.get("image_urls") or []),
                    "is_business": int(bool(listing.get("is_business"))),
                    "fetched_at": _now(),
                },
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # UNIQUE(external_id) — already seen


def _row_to_listing(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["image_urls"] = json.loads(d.get("image_urls") or "[]")
    d["is_business"] = bool(d.get("is_business"))
    return d


def get_listing(listing_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    return _row_to_listing(row) if row else None


def list_for_tab(user_id: int, tab: str, limit: int = 100) -> list[dict[str, Any]]:
    """Return listings for a tab from the current user's perspective.

    new    -> current user has no reaction
    liked  -> current user reacted 'like'
    hidden -> current user reacted 'hide'
    Each listing includes `my_reaction` and `partner_reactions` (other users).
    """
    with connect() as conn:
        my = conn.execute(
            "SELECT listing_id, reaction FROM reactions WHERE user_id = ?", (user_id,)
        ).fetchall()
        my_map = {r["listing_id"]: r["reaction"] for r in my}

        rows = conn.execute(
            "SELECT * FROM listings ORDER BY id DESC LIMIT ?", (limit * 4,)
        ).fetchall()

        # partner reactions = everyone who is not the current user
        partner = conn.execute(
            "SELECT listing_id, user_id, reaction FROM reactions WHERE user_id != ?",
            (user_id,),
        ).fetchall()
        partner_map: dict[int, list[dict[str, Any]]] = {}
        for r in partner:
            partner_map.setdefault(r["listing_id"], []).append(
                {"user_id": r["user_id"], "reaction": r["reaction"]}
            )

    result = []
    for row in rows:
        lid = row["id"]
        mine = my_map.get(lid)
        if tab == "new" and mine is not None:
            continue
        if tab == "liked" and mine != "like":
            continue
        if tab == "hidden" and mine != "hide":
            continue
        item = _row_to_listing(row)
        item["my_reaction"] = mine
        item["partner_reactions"] = partner_map.get(lid, [])
        result.append(item)
        if len(result) >= limit:
            break
    return result


# --- reactions --------------------------------------------------------------

def set_reaction(listing_id: int, user_id: int, reaction: str) -> None:
    """Upsert a like/hide reaction for (listing, user)."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO reactions (listing_id, user_id, reaction, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(listing_id, user_id)
               DO UPDATE SET reaction = excluded.reaction, updated_at = excluded.updated_at""",
            (listing_id, user_id, reaction, _now()),
        )
        conn.commit()


def get_reactions(listing_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT user_id, reaction FROM reactions WHERE listing_id = ?", (listing_id,)
        ).fetchall()
    return [dict(r) for r in rows]
