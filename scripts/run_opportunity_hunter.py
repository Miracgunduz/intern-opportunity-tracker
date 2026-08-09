"""Standalone entry point for GitHub Actions: runs the Opportunity Hunter
(scrape -> Gemini gatekeeper -> Notion -> broadcast) once and exits.

Doesn't use python-telegram-bot's Application/JobQueue — a plain telegram.Bot
is all hunt_and_broadcast() needs, and GH Actions cron already provides the
scheduling (see .github/workflows/opportunity_hunter.yml), so there's no
long-lived process to manage here.

Run (from the repo root, so `bot`/`main` are importable):
    python -m scripts.run_opportunity_hunter

Always sends something — new opportunities, or a "nothing new" message
when the count is 0 — on every run, scheduled or manual (/tara). Originally
the scheduled runs stayed silent on 0 ("nobody wants a 'nothing new' ping
twice a day"), but that made a genuinely silent failure (delivery broken)
indistinguishable from a quiet day (nothing qualified) — the user couldn't
tell which one they were looking at. A message every run, even an empty
one, doubles as a heartbeat.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from telegram import Bot

from bot.scheduler import hunt_and_broadcast

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scripts.run_opportunity_hunter")


async def main() -> int:
    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    log.info("Opportunity Hunter: running the scrape/filter/gatekeeper/Notion pipeline...")
    count = await hunt_and_broadcast(bot, chat_id)
    log.info("Opportunity Hunter: %d new opportunities broadcast", count)

    if count == 0:
        await bot.send_message(chat_id=chat_id, text="Şu an için yeni bir fırsat bulunamadı.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
