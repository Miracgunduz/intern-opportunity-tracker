"""24/7 interactive Telegram bot entry point.

Runs the bot's command/callback handlers (long-lived polling) AND two
independent background jobs — see bot/scheduler.py — in one process, via
python-telegram-bot's built-in JobQueue.

JobQueue is itself a thin wrapper around APScheduler's AsyncIOScheduler +
CronTrigger, sharing the bot's own asyncio event loop — that's why this
uses `app.job_queue.run_daily(...)` rather than standing up a second,
independent APScheduler instance: two schedulers in one process would just
be two competing event-loop integrations for no benefit. Every schedule
below is anchored to Europe/Istanbul via the stdlib `zoneinfo`
(`processing.timezone_converter.ISTANBUL_TZ`, already used project-wide)
rather than `pytz` — zoneinfo attaches to a `datetime.time` directly via
`tzinfo=`, with none of pytz's `tz.localize()`/`normalize()` footguns.

This is an alternative to the GitHub Actions daily cron for anyone who
wants the interactive buttons — GH Actions can't run a long-lived polling
process, so this needs an always-on host instead. If you deploy this,
disable/delete .github/workflows/daily.yml so the pipeline doesn't run
twice a day from two different places. See README "Deploying the 24/7 bot"
for free-tier hosting (Render/PythonAnywhere).

Run: python -m bot.app
"""
from __future__ import annotations

import logging
import os
from datetime import time as dt_time

from dotenv import load_dotenv

load_dotenv()

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from processing.timezone_converter import ISTANBUL_TZ

from .handlers import (
    handle_basvurularim,
    handle_bekleyenler,
    handle_rapor,
    handle_status_button,
    handle_tara,
    handle_yardim,
)
from .scheduler import run_morning_briefing, run_opportunity_hunter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot.app")

# Task 1 — Opportunity Hunter: twice a day, 09:00 & 21:00 Istanbul.
OPPORTUNITY_HUNTER_TIMES = (
    dt_time(hour=9, minute=0, tzinfo=ISTANBUL_TZ),
    dt_time(hour=21, minute=0, tzinfo=ISTANBUL_TZ),
)
# Task 2 — Morning Briefing: exactly once a day, 09:00 Istanbul — NOT also
# registered for 21:00, by design (see bot/scheduler.py's docstring).
MORNING_BRIEFING_TIME = dt_time(hour=9, minute=0, tzinfo=ISTANBUL_TZ)

# The single source of truth for "what commands does this bot have" — used
# both to register handlers below and to populate Telegram's native menu
# button (the blue button next to the chat input) via set_my_commands().
# Command names must be lowercase a-z/0-9/underscore only (Telegram API
# constraint), which is why these are ASCII even though the descriptions
# are Turkish.
BOT_COMMANDS = [
    BotCommand("basvurularim", "Aktif olarak sonucunu beklediğin başvuruları listeler"),
    BotCommand("bekleyenler", "Henüz başvurmadığın ama süresi geçmemiş fırsatları gösterir"),
    BotCommand("rapor", "Başvuru ve kabul/ret istatistiklerini özetler"),
    BotCommand("tara", "Zamanlayıcıyı beklemeden interneti anında manuel tarar"),
    BotCommand("yardim", "Bu komut menüsünü gösterir"),
]


async def _post_init(application: Application) -> None:
    """Runs once, right after the bot starts — registers BOT_COMMANDS with
    Telegram so they show in the native menu button. This is separate from
    add_handler() below, which only wires up what happens when a command is
    actually sent; without this call the commands still work, they just
    don't appear in the menu's autocomplete list."""
    await application.bot.set_my_commands(BOT_COMMANDS)


def build_application() -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).post_init(_post_init).build()

    app.add_handler(CommandHandler("basvurularim", handle_basvurularim))
    app.add_handler(CommandHandler("bekleyenler", handle_bekleyenler))
    app.add_handler(CommandHandler("rapor", handle_rapor))
    app.add_handler(CommandHandler("tara", handle_tara))
    app.add_handler(CommandHandler(["yardim", "start"], handle_yardim))
    app.add_handler(CallbackQueryHandler(handle_status_button))

    if app.job_queue is None:
        raise RuntimeError(
            'JobQueue support is not installed. Run: pip install "python-telegram-bot[job-queue]"'
        )

    # run_daily() schedules exactly one time-of-day per call (it's a single
    # CronTrigger under the hood) — "twice a day" means calling it twice
    # with the same callback, i.e. two independent jobs that happen to
    # share code, not one job with two trigger times.
    for hunt_time in OPPORTUNITY_HUNTER_TIMES:
        app.job_queue.run_daily(
            run_opportunity_hunter,
            time=hunt_time,
            name=f"opportunity_hunter_{hunt_time.strftime('%H%M')}",
        )

    app.job_queue.run_daily(run_morning_briefing, time=MORNING_BRIEFING_TIME, name="morning_briefing")

    return app


def main() -> None:
    app = build_application()
    log.info(
        "Bot starting — polling for commands/buttons. Opportunity Hunter: 09:00 & 21:00 Europe/Istanbul. "
        "Morning Briefing: 09:00 Europe/Istanbul only."
    )
    app.run_polling()


if __name__ == "__main__":
    main()
