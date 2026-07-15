"""Telegram bot: posts listing cards to the group and handles like/hide callbacks.

Runs inside the same asyncio process as the scraper and web app. A media group
(up to N photos) carries the caption; inline buttons go in a separate follow-up
message because Telegram media groups can't carry an inline keyboard.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
    WebAppInfo,
)
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from . import db
from .config import settings

logger = logging.getLogger(__name__)

_REACTION_EMOJI = {"like": "👍", "hide": "🙈"}


def _format_caption(listing: dict[str, Any]) -> str:
    """Human-readable card text used as the media-group caption."""
    parts = [f"🏠 <b>{_esc(listing.get('title') or 'Оголошення')}</b>"]

    price = listing.get("price_value")
    if price:
        parts.append(f"💰 {price} {listing.get('price_currency') or ''}".strip())
    if listing.get("district"):
        parts.append(f"📍 {_esc(listing['district'])}")

    meta = []
    if listing.get("rooms"):
        meta.append(f"{listing['rooms']} кімн.")
    if listing.get("area"):
        meta.append(f"{int(listing['area'])} м²")
    if listing.get("floor") is not None:
        meta.append(f"поверх {listing['floor']}")
    if meta:
        parts.append(" · ".join(meta))

    parts.append("🏢 Агенція/бізнес" if listing.get("is_business") else "👤 Власник")
    return "\n".join(parts)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _buttons(listing_id: int, url: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔗 Відкрити на OLX", url=url)] if url else [],
        [
            InlineKeyboardButton("👍 Подобається", callback_data=f"react:like:{listing_id}"),
            InlineKeyboardButton("🙈 Сховати", callback_data=f"react:hide:{listing_id}"),
        ],
    ]
    if settings.webapp_url:
        rows.append(
            [InlineKeyboardButton("📱 У застосунку", web_app=WebAppInfo(url=settings.webapp_url))]
        )
    return InlineKeyboardMarkup([r for r in rows if r])


def _reactions_line(listing_id: int) -> str:
    reactions = db.get_reactions(listing_id)
    if not reactions:
        return ""
    marks = [f"{_REACTION_EMOJI.get(r['reaction'], '?')} {r['user_id']}" for r in reactions]
    return "\n\n" + " | ".join(marks)


async def send_listing(app: Application, listing_id: int, listing: dict[str, Any]) -> None:
    """Post a listing card (photos + caption) then a buttons message to the group."""
    bot = app.bot
    caption = _format_caption(listing)
    images = (listing.get("image_urls") or [])[: settings.max_photos]

    try:
        if len(images) > 1:
            media = [
                InputMediaPhoto(media=url, caption=caption if i == 0 else None,
                                parse_mode="HTML" if i == 0 else None)
                for i, url in enumerate(images)
            ]
            await bot.send_media_group(chat_id=settings.group_chat_id, media=media)
        elif len(images) == 1:
            await bot.send_photo(
                chat_id=settings.group_chat_id, photo=images[0],
                caption=caption, parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=settings.group_chat_id, text=caption, parse_mode="HTML"
            )

        # Buttons in a follow-up message (media groups can't carry a keyboard).
        await bot.send_message(
            chat_id=settings.group_chat_id,
            text="Що робимо з цим варіантом?",
            reply_markup=_buttons(listing_id, listing.get("url") or ""),
        )
    except Exception as exc:
        logger.error("Failed to send listing %s: %s", listing_id, exc)


async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle like/hide taps: record reaction by user.id, reflect who reacted."""
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        _, reaction, listing_id_str = query.data.split(":")
        listing_id = int(listing_id_str)
    except ValueError:
        await query.answer()
        return

    user_id = query.from_user.id
    await asyncio.to_thread(db.set_reaction, listing_id, user_id, reaction)

    verb = "подобається" if reaction == "like" else "сховано"
    await query.answer(f"Записав: {verb}")

    listing = await asyncio.to_thread(db.get_listing, listing_id)
    url = listing.get("url") if listing else ""
    line = await asyncio.to_thread(_reactions_line, listing_id)
    try:
        await query.edit_message_text(
            text="Що робимо з цим варіантом?" + line,
            reply_markup=_buttons(listing_id, url or ""),
        )
    except Exception:
        pass  # message unchanged / too old to edit — reaction is already saved


def build_application() -> Application:
    """Create the PTB Application and register the callback handler."""
    app = Application.builder().token(settings.bot_token).build()
    app.add_handler(CallbackQueryHandler(_on_callback, pattern=r"^react:"))
    return app
