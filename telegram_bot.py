"""Telegram bot: accepts URLs and queues them for podcast processing."""
from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import Settings, load_settings
from metadata import MetadataStore
from watcher import URLS_FILE, enqueue_url

logger = logging.getLogger(__name__)


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_url(message) -> str | None:
    """Extract the first URL from a Telegram message.

    Uses Telegram's entity detection first (most reliable), falls back to regex.
    """
    # Telegram parses URLs into entities automatically
    if message.entities:
        for entity in message.entities:
            if entity.type in ("url", "text_link"):
                if entity.type == "text_link":
                    return entity.url
                return message.text[entity.offset:entity.offset + entity.length]

    # Fallback: regex extraction from raw text
    match = _URL_RE.search(message.text or "")
    return match.group(0) if match else None


def is_authorized(settings: Settings, user_id: int) -> bool:
    """Check if a Telegram user is allowed to use the bot."""
    allowed = settings.telegram_allowed_users
    return not allowed or user_id in allowed


async def start_command(update: Update, context) -> None:
    """Handle the /start command."""
    await update.message.reply_text(
        "Send me a URL and I'll queue it for podcast processing."
    )


async def handle_message(update: Update, context) -> None:
    """Handle incoming text messages — expects a URL."""
    settings: Settings = context.bot_data["settings"]
    store: MetadataStore = context.bot_data["store"]

    if not is_authorized(settings, update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    url = extract_url(update.message)
    if not url:
        await update.message.reply_text("No URL found in your message. Send an HTTP or HTTPS link.")
        return

    if store.is_processed(url):
        await update.message.reply_text(f"Already processed: {url}")
        return

    try:
        added = enqueue_url(URLS_FILE, url)
    except ValueError:
        await update.message.reply_text(f"Invalid URL — must be HTTP or HTTPS: {url}")
        return

    if not added:
        await update.message.reply_text(f"Already queued: {url}")
        return

    await update.message.reply_text(f"Queued for processing: {url}")


def run(settings: Settings | None = None) -> None:
    """Start the Telegram bot with long-polling."""
    if settings is None:
        settings = load_settings()

    if not settings.telegram_enabled:
        logger.warning("Telegram bot token not configured — exiting.")
        return

    store = MetadataStore()

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["settings"] = settings
    app.bot_data["store"] = store

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Telegram bot started (polling)")
    app.run_polling(allowed_updates=["message"], poll_interval=settings.telegram_poll_interval_sec)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run()
