"""Discord webhook notification: one compact daily summary message.

Setup: Discord server -> channel Settings -> Integrations -> Webhooks ->
New Webhook -> copy URL into DISCORD_WEBHOOK_URL. No bot, no OAuth.
"""
from __future__ import annotations

import logging
import os

import requests

from sources.base import Opportunity

log = logging.getLogger(__name__)
REQUEST_TIMEOUT = 10
MAX_ITEMS_IN_MESSAGE = 10
DISCORD_CONTENT_LIMIT = 2000


def send_discord_summary(opportunities: list[Opportunity]) -> bool:
    """Returns True if a message was actually sent."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url or not opportunities:
        return False  # not configured, or nothing new — don't spam a "0 found" ping

    noun = "opportunity" if len(opportunities) == 1 else "opportunities"
    lines = [f"**{len(opportunities)} new {noun} found today:**"]
    for opp in opportunities[:MAX_ITEMS_IN_MESSAGE]:
        deadline_note = f" — deadline {opp.deadline}" if opp.deadline else ""
        lines.append(f"• [{opp.title}]({opp.url}){deadline_note} `({opp.source}, score {opp.score:.1f})`")
    if len(opportunities) > MAX_ITEMS_IN_MESSAGE:
        lines.append(f"…and {len(opportunities) - MAX_ITEMS_IN_MESSAGE} more — see data/opportunities.ics")

    content = "\n".join(lines)
    if len(content) > DISCORD_CONTENT_LIMIT - 20:
        content = content[: DISCORD_CONTENT_LIMIT - 20] + "\n… (truncated)"

    try:
        resp = requests.post(webhook_url, json={"content": content}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("Discord notification failed: %s", exc)
        return False
