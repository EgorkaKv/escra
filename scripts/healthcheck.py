"""Standalone health-check watchdog for the Escra service.

Runs as its own process, independent of app.main — if the event loop that
serves /health is wedged or the whole process is dead, this still runs (it's
started fresh by systemd on a timer, see deploy/escra-healthcheck.{service,timer})
and can still page someone.

State (up/down) persists in a small JSON file next to the DB so we send one
alert per outage and one recovery message, not one per timer tick.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from app.config import settings

_REPO_DIR = Path(__file__).resolve().parent.parent
_STATE_FILE = _REPO_DIR / ".healthcheck_state.json"
_HEALTH_URL = f"http://{settings.host}:{settings.port}/health"
_TIMEOUT = 5


def _load_last_status() -> str:
    try:
        return json.loads(_STATE_FILE.read_text())["status"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return "up"  # first run ever: assume up, don't fire a startup alert


def _save_status(status: str) -> None:
    _STATE_FILE.write_text(json.dumps({"status": status}))


def _is_healthy() -> bool:
    try:
        with urllib.request.urlopen(_HEALTH_URL, timeout=_TIMEOUT) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _send_telegram(text: str) -> None:
    if not settings.bot_token or not settings.alert_chat_id:
        print("BOT_TOKEN or ALERT_CHAT_ID not set — skipping alert", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    body = json.dumps({"chat_id": settings.alert_chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=_TIMEOUT)
    except (urllib.error.URLError, OSError) as exc:
        print(f"Failed to send Telegram alert: {exc}", file=sys.stderr)


def main() -> None:
    was_up = _load_last_status() == "up"
    is_up = _is_healthy()

    if was_up and not is_up:
        _send_telegram(f"🔴 Escra не відповідає: {_HEALTH_URL}")
    elif not was_up and is_up:
        _send_telegram(f"🟢 Escra знову працює: {_HEALTH_URL}")

    _save_status("up" if is_up else "down")


if __name__ == "__main__":
    main()
