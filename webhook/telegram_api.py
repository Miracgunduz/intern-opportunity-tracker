"""Small synchronous Telegram Bot API helpers not already covered by
integrations/telegram_notify.py (which only sends new messages). The
webhook needs to also answer callback queries and edit existing messages —
both are simple enough to hit directly with `requests` rather than pulling
in python-telegram-bot's async Application machinery, which doesn't embed
cleanly in a sync per-request WSGI app like PythonAnywhere's free tier.
"""
from __future__ import annotations

import logging
import os

import requests

from integrations.http_retry import post_with_retry

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10


def _api_url(method: str) -> str:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    return f"https://api.telegram.org/bot{token}/{method}"


def answer_callback_query(callback_query_id: str) -> None:
    """Acks a button tap immediately — Telegram shows a loading spinner on
    the button until this fires."""
    try:
        post_with_retry(_api_url("answerCallbackQuery"), json={"callback_query_id": callback_query_id},
                         timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        log.warning("answerCallbackQuery failed: %s", exc)


def edit_message_text(chat_id: int | str, message_id: int, text: str, reply_markup: dict | None = None) -> bool:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        resp = post_with_retry(_api_url("editMessageText"), json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("editMessageText failed: %s", exc)
        return False
