"""Telegram Bot notification: daily summary + result-day reminders.

Setup: message @BotFather on Telegram -> /newbot -> copy the token into
TELEGRAM_BOT_TOKEN. Then message your new bot once (anything) and open
https://api.telegram.org/bot<token>/getUpdates to find your numeric chat
id -> TELEGRAM_CHAT_ID.
"""
from __future__ import annotations

import html
import logging
import os

import requests

from sources.base import Opportunity

log = logging.getLogger(__name__)
REQUEST_TIMEOUT = 10
MAX_ITEMS_IN_MESSAGE = 10
TELEGRAM_TEXT_LIMIT = 4096


def send_telegram_text(text: str) -> bool:
    """Low-level send: posts pre-formatted HTML text to TELEGRAM_CHAT_ID.

    Returns True if a message was actually sent (credentials configured and
    the request succeeded). Used directly for one-off messages (e.g. the
    result-announcement reminder) and internally by send_telegram_summary().
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id or not text:
        return False

    if len(text) > TELEGRAM_TEXT_LIMIT - 20:
        text = text[: TELEGRAM_TEXT_LIMIT - 20] + "\n… (truncated)"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("Telegram notification failed: %s", exc)
        return False


def send_telegram_summary(opportunities: list[Opportunity]) -> bool:
    """Returns True if a message was actually sent."""
    if not opportunities:
        return False

    noun = "opportunity" if len(opportunities) == 1 else "opportunities"
    lines = [f"<b>{len(opportunities)} new {noun} found today</b>"]
    for opp in opportunities[:MAX_ITEMS_IN_MESSAGE]:
        title = html.escape(opp.program_name or opp.title)
        deadline_note = f" — deadline {opp.deadline}" if opp.deadline else ""
        link = opp.application_link or opp.url
        lines.append(f'• <a href="{link}">{title}</a>{deadline_note} ({opp.source}, score {opp.score:.1f})')
    if len(opportunities) > MAX_ITEMS_IN_MESSAGE:
        lines.append(f"…and {len(opportunities) - MAX_ITEMS_IN_MESSAGE} more — see data/opportunities.ics")

    return send_telegram_text("\n".join(lines))
