"""FastAPI app: serves the single-page web app and its JSON API.

Every /api/* request must carry Telegram Web App `initData` in the
`X-Telegram-Init-Data` header. We verify it via HMAC (Telegram's documented
scheme) to authenticate the caller's user.id — this is how we distinguish which
of the two of us reacted, without any separate login.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .. import db
from ..config import settings

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

app = FastAPI(title="Escra — OLX Lviv")

# Reject initData older than this to limit replay of a captured payload.
_MAX_AUTH_AGE = 24 * 3600


def verify_init_data(init_data: str) -> int:
    """Validate Telegram Web App initData and return the authenticated user id.

    Scheme: secret = HMAC_SHA256(key="WebAppData", msg=bot_token); the expected
    hash is HMAC_SHA256(key=secret, msg=data_check_string), where
    data_check_string is the sorted "key=value" lines excluding `hash`.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="missing initData")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="no hash in initData")

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(status_code=401, detail="bad initData signature")

    auth_date = pairs.get("auth_date")
    if auth_date and (time.time() - int(auth_date)) > _MAX_AUTH_AGE:
        raise HTTPException(status_code=401, detail="initData expired")

    try:
        user = json.loads(pairs["user"])
        return int(user["id"])
    except (KeyError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="no user in initData")


async def current_user(x_telegram_init_data: str = Header(default="")) -> int:
    return verify_init_data(x_telegram_init_data)


class ReactBody(BaseModel):
    listing_id: int
    reaction: str  # 'like' | 'hide'


class CriteriaBody(BaseModel):
    rooms_min: int | None = None
    rooms_max: int | None = None
    price_max: int | None = None
    price_currency: str | None = None
    exclude_first_floor: int | None = None


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/listings")
async def api_listings(tab: str = "new", user_id: int = Depends(current_user)):
    if tab not in {"new", "liked", "hidden"}:
        raise HTTPException(status_code=400, detail="bad tab")
    return {"items": db.list_for_tab(user_id, tab)}


@app.post("/api/react")
async def api_react(body: ReactBody, user_id: int = Depends(current_user)):
    if body.reaction not in {"like", "hide"}:
        raise HTTPException(status_code=400, detail="bad reaction")
    db.set_reaction(body.listing_id, user_id, body.reaction)
    return {"ok": True}


@app.get("/api/criteria")
async def api_get_criteria(user_id: int = Depends(current_user)):
    return db.get_criteria()


@app.post("/api/criteria")
async def api_set_criteria(body: CriteriaBody, user_id: int = Depends(current_user)):
    return db.update_criteria(body.model_dump(exclude_none=True))
