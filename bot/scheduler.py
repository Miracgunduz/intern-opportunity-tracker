"""Background jobs for the 24/7 bot — split into two independent tasks,
registered with their own schedules in bot/app.py:

  - run_opportunity_hunter ("Opportunity Hunter"): scrape -> Gemini
    gatekeeper -> Notion -> immediately broadcast newly discovered
    opportunities. Runs TWICE a day (09:00 & 21:00 Europe/Istanbul) so a
    limited-quota program doesn't sit undiscovered for 12+ hours. Its core
    logic (hunt_and_broadcast) is also what the manual /tara command in
    bot/handlers.py calls, for an on-demand run between schedules.
  - run_morning_briefing ("Morning Briefing"): Notion-only reminders
    (result-day + T-minus-10 countdowns). Runs ONCE a day, morning only
    (09:00 Europe/Istanbul) — deliberately not registered for the evening
    slot too, so reminders can't double up and cause notification fatigue.

Neither function calls the other — a job crashing or being slow in one
never blocks or duplicates the other's schedule.
"""
from __future__ import annotations

import html
import logging
import os

from telegram import Bot
from telegram.ext import ContextTypes

from integrations import check_deadline_countdowns, check_todays_announcements
from main import run_pipeline
from processing.timezone_converter import format_istanbul_display

from .keyboards import applied_keyboard, result_keyboard

log = logging.getLogger(__name__)


def _format_new_opportunity(opp) -> str:
    title = html.escape(opp.program_name or opp.title)
    link = opp.application_link or opp.url
    lines = [f"🆕 <b>{title}</b>"]
    if link:
        lines.append(f"🔗 {link}")
    deadline_display = format_istanbul_display(opp.deadline, opp.deadline_time_istanbul)
    if deadline_display:
        lines.append(f"📅 Deadline: {deadline_display}")
    if opp.turkish_cv_summary:
        lines.append("")
        lines.append(f"🇹🇷 {html.escape(opp.turkish_cv_summary)}")
    return "\n".join(lines)


async def hunt_and_broadcast(bot: Bot, chat_id: str) -> int:
    """The actual Opportunity Hunter work: scrape -> Gemini QA/prestige
    gatekeeper -> Notion -> broadcast each newly accepted opportunity with
    an inline "✅ Başvurdum" button. Returns how many were broadcast.

    Shared by the scheduled run_opportunity_hunter job (09:00 & 21:00) and
    the manual /tara command (bot/handlers.py) — one place, two triggers.
    """
    new_opportunities, page_ids = run_pipeline()

    for opp in new_opportunities:
        page_id = page_ids.get(opp.id)
        markup = applied_keyboard(page_id) if page_id else None
        await bot.send_message(
            chat_id=chat_id,
            text=_format_new_opportunity(opp),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=markup,
        )

    return len(new_opportunities)


async def run_opportunity_hunter(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Task 1 — 'Opportunity Hunter' scheduled job (09:00 & 21:00 Europe/
    Istanbul). No reminders here — see run_morning_briefing for those, on
    its own separate schedule."""
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    log.info("Opportunity Hunter: running the scrape/filter/gatekeeper/Notion pipeline...")
    count = await hunt_and_broadcast(context.bot, chat_id)
    log.info("Opportunity Hunter: %d new opportunities broadcast", count)


async def run_morning_briefing(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Task 2 — 'Morning Briefing'. Notion-only reminders: result-day
    (Announcement Date == today) and the Status="New" T-minus-10 deadline
    countdown. No scraping/gatekeeping here — this only reads what
    run_opportunity_hunter already wrote to Notion.
    """
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    log.info("Morning Briefing: checking Notion for reminders...")

    for item in check_todays_announcements():
        if not item.get("page_id"):
            continue
        title = html.escape(item["title"])
        text = f'🎉 Sonuç günü! <b>{title}</b> için sonuçlar açıklanmış olabilir.\n{item["url"]}'
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=result_keyboard(item["page_id"]),
        )

    for m in check_deadline_countdowns():
        title = html.escape(m["title"])
        lines = [f"⏳ Hatırlatma: <b>{title}</b> başvurusu için son {m['days_remaining']} gün!"]
        if m["url"]:
            lines.append(f"🔗 {m['url']}")
        if m.get("turkish_cv_summary"):
            lines.append("")
            lines.append(f"🇹🇷 {html.escape(m['turkish_cv_summary'])}")
        await context.bot.send_message(
            chat_id=chat_id, text="\n".join(lines), parse_mode="HTML", disable_web_page_preview=True
        )

    log.info("Morning Briefing: done.")
