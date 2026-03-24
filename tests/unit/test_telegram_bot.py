"""Unit tests for telegram_bot.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from config import Settings
from telegram_bot import is_authorized, extract_url, handle_message, start_command


# ---------------------------------------------------------------------------
# is_authorized
# ---------------------------------------------------------------------------

def test_is_authorized_allows_all_when_no_ids_configured():
    settings = Settings()
    assert is_authorized(settings, 12345) is True


def test_is_authorized_allows_listed_user():
    settings = Settings()
    settings.telegram_allowed_user_ids = "111,222,333"
    assert is_authorized(settings, 222) is True


def test_is_authorized_rejects_unlisted_user():
    settings = Settings()
    settings.telegram_allowed_user_ids = "111,222"
    assert is_authorized(settings, 999) is False


# ---------------------------------------------------------------------------
# Helpers for message handler tests
# ---------------------------------------------------------------------------

def _make_entity(type, offset, length, url=None):
    """Create a mock Telegram MessageEntity."""
    entity = MagicMock()
    entity.type = type
    entity.offset = offset
    entity.length = length
    entity.url = url
    return entity


def _make_update_and_context(text, user_id=1, settings=None, store=None, entities=None):
    """Create mock Update and context for testing handle_message."""
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.entities = entities or []
    update.effective_user.id = user_id

    context = MagicMock()
    context.bot_data = {
        "settings": settings or Settings(),
        "store": store or MagicMock(),
    }
    return update, context


# ---------------------------------------------------------------------------
# extract_url
# ---------------------------------------------------------------------------

def test_extract_url_from_entity():
    msg = MagicMock()
    msg.text = "Check this https://example.com/article please"
    msg.entities = [_make_entity("url", 11, 27)]
    assert extract_url(msg) == "https://example.com/article"


def test_extract_url_from_text_link_entity():
    msg = MagicMock()
    msg.text = "Check this link please"
    msg.entities = [_make_entity("text_link", 11, 4, url="https://example.com/article")]
    assert extract_url(msg) == "https://example.com/article"


def test_extract_url_regex_fallback():
    msg = MagicMock()
    msg.text = "Read this https://example.com/article it's great"
    msg.entities = []
    assert extract_url(msg) == "https://example.com/article"


def test_extract_url_no_url_found():
    msg = MagicMock()
    msg.text = "hello world no links here"
    msg.entities = []
    assert extract_url(msg) is None


def test_extract_url_bare_url():
    msg = MagicMock()
    msg.text = "https://example.com/article"
    msg.entities = [_make_entity("url", 0, 27)]
    assert extract_url(msg) == "https://example.com/article"


# ---------------------------------------------------------------------------
# start_command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_command_replies():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    await start_command(update, MagicMock())
    update.message.reply_text.assert_called_once()
    assert "URL" in update.message.reply_text.call_args[0][0]


# ---------------------------------------------------------------------------
# handle_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_message_queues_valid_url():
    store = MagicMock()
    store.is_processed.return_value = False
    entities = [_make_entity("url", 0, 27)]

    update, context = _make_update_and_context("https://example.com/article", store=store, entities=entities)

    with patch("telegram_bot.enqueue_url", return_value=True):
        await handle_message(update, context)

    update.message.reply_text.assert_called_once()
    assert "Queued" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_message_extracts_url_from_surrounding_text():
    store = MagicMock()
    store.is_processed.return_value = False
    entities = [_make_entity("url", 10, 27)]

    update, context = _make_update_and_context("Check out https://example.com/article please", store=store, entities=entities)

    with patch("telegram_bot.enqueue_url", return_value=True):
        await handle_message(update, context)

    assert "Queued" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_message_already_queued():
    store = MagicMock()
    store.is_processed.return_value = False
    entities = [_make_entity("url", 0, 27)]

    update, context = _make_update_and_context("https://example.com/article", store=store, entities=entities)

    with patch("telegram_bot.enqueue_url", return_value=False):
        await handle_message(update, context)

    assert "Already queued" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_message_already_processed():
    store = MagicMock()
    store.is_processed.return_value = True
    entities = [_make_entity("url", 0, 27)]

    update, context = _make_update_and_context("https://example.com/article", store=store, entities=entities)

    await handle_message(update, context)

    assert "Already processed" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_message_no_url_found():
    store = MagicMock()

    update, context = _make_update_and_context("hello no links here", store=store)

    await handle_message(update, context)

    assert "No URL found" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_message_invalid_url():
    store = MagicMock()
    store.is_processed.return_value = False

    update, context = _make_update_and_context("check http://bad", store=store)

    with patch("telegram_bot.extract_url", return_value="http://bad"), \
         patch("telegram_bot.enqueue_url", side_effect=ValueError("Invalid URL")):
        await handle_message(update, context)

    assert "Invalid URL" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_message_unauthorized_user():
    settings = Settings()
    settings.telegram_allowed_user_ids = "111,222"
    entities = [_make_entity("url", 0, 27)]

    update, context = _make_update_and_context(
        "https://example.com/article", user_id=999, settings=settings, entities=entities,
    )

    await handle_message(update, context)

    assert "not authorized" in update.message.reply_text.call_args[0][0]
