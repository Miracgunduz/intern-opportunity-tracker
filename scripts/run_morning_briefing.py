"""Standalone entry point for GitHub Actions: sends the Morning Briefing
(result-day + T-minus-10 countdown reminders, read from Notion) once and
exits. See scripts/run_opportunity_hunter.py for why this doesn't use
python-telegram-bot's Application/JobQueue — GH Actions cron is the
scheduler (see .github/workflows/morning_briefing.yml).

Run (from the repo root, so `bot`/`main` are importable):
    python -m scripts.run_morning_briefing
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from telegram import Bot

from bot.scheduler import send_morning_briefing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scripts.run_morning_briefing")


async def main() -> int:
    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    log.info("Morning Briefing: checking Notion for reminders...")
    sent = await send_morning_briefing(bot, chat_id)
    log.info("Morning Briefing: done, %d reminder(s) sent.", sent)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
