"""FastAPI app: serves the single-page web app and its JSON API.

Every /api/* request must carry Telegram Web App `initData` in the
`X-Telegram-Init-Data` header. We verify it via HMAC (Telegram's documented
scheme) to authenticate the caller's user.id — this is how we distinguish which
of the two of us reacted, without any separate login.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .. import db
from ..config import settings

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_REPO_DIR = Path(__file__).resolve().parents[2]
_DEPLOY_SCRIPT = _REPO_DIR / "deploy" / "deploy.sh"

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


_DEV_USER_IDS = {settings.dev_user_vesnushka_id, settings.dev_user_sladkoezhka_id}


async def current_user(
    x_telegram_init_data: str = Header(default=""),
    x_dev_user_id: str = Header(default=""),
) -> int:
    if settings.dev_no_auth:
        try:
            dev_user_id = int(x_dev_user_id)
        except ValueError:
            raise HTTPException(status_code=401, detail="missing X-Dev-User-Id")
        if dev_user_id not in _DEV_USER_IDS:
            raise HTTPException(status_code=401, detail="unknown dev user id")
        logger.warning("DEV_NO_AUTH is on — skipping initData verification (user=%s)", dev_user_id)
        return dev_user_id
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


@app.get("/api/dev-identities")
async def api_dev_identities() -> dict:
    """Public: lets the browser offer an identity picker when opened outside
    Telegram. Returns nothing useful unless DEV_NO_AUTH is on."""
    if not settings.dev_no_auth:
        return {"enabled": False, "users": []}
    return {
        "enabled": True,
        "users": [
            {"id": settings.dev_user_vesnushka_id, "name": "Я Веснушка"},
            {"id": settings.dev_user_sladkoezhka_id, "name": "Я Сладкоєжка"},
        ],
    }


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


def _verify_github_signature(body: bytes, signature: str) -> bool:
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


async def _run_deploy() -> None:
    proc = await asyncio.create_subprocess_exec(
        "bash", str(_DEPLOY_SCRIPT),
        cwd=str(_REPO_DIR),
        env={**os.environ, "DEPLOY_BRANCH": settings.deploy_branch},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await proc.communicate()
    logger.info(
        "deploy.sh finished (rc=%s):\n%s", proc.returncode, output.decode(errors="replace")
    )


@app.post("/github-push")
async def github_push(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> dict:
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=503, detail="webhook not configured")

    body = await request.body()
    if not _verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="bad signature")

    if x_github_event == "ping":
        return {"ok": True, "pong": True}
    if x_github_event != "push":
        return {"ok": True, "skipped": f"event {x_github_event!r} ignored"}

    payload = json.loads(body)
    ref = payload.get("ref", "")
    if ref != f"refs/heads/{settings.deploy_branch}":
        return {"ok": True, "skipped": f"ref {ref!r} != {settings.deploy_branch}"}

    logger.warning("GitHub push on %s — deploying", ref)
    asyncio.create_task(_run_deploy())
    return {"ok": True, "deploying": True}
