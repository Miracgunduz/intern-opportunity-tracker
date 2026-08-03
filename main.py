"""Entry point: fetch -> filter -> gatekeep -> dedupe -> output.

Two ways to run this pipeline — pick ONE, not both, or you'll get every
notification twice:
  - `python main.py`: one-shot batch run with a plain-text Telegram/Discord
    summary. This is what .github/workflows/daily.yml calls.
  - `python -m bot.app`: a 24/7 interactive bot (inline "✅ Başvurdum" /
    "🎉 Kabul Edildim" / "❌ Reddedildim" buttons, /basvurularim) that calls
    run_pipeline() below from its own daily background job instead of
    main() — see bot/scheduler.py. Needs an always-on host (GitHub Actions
    can't run a long-lived polling process); see README "Deploying the
    24/7 bot".
"""
from __future__ import annotations

import logging
import sys
import time

from dotenv import load_dotenv

load_dotenv()  # picks up a local .env file if present; harmless no-op in CI, where secrets are already env vars

from integrations import (
    add_opportunities_to_ics,
    check_and_notify_deadline_countdowns,
    check_and_notify_todays_announcements,
    find_existing_notion_page,
    push_opportunities_to_google_calendar,
    push_opportunity_to_notion,
    send_discord_summary,
    send_telegram_summary,
)
from processing import filter_opportunities, parse_with_llm
from sources import (
    fetch_devpost_opportunities,
    fetch_github_opportunities,
    fetch_hackernews_opportunities,
    fetch_reddit_opportunities,
)
from sources.base import Opportunity
from state import load_seen, mark_seen, prune_old, save_seen, split_new_vs_seen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

LLM_CALL_DELAY = 2.0  # seconds between successive Gemini calls — see run_pipeline()


def collect_all_opportunities() -> list[Opportunity]:
    all_opps: list[Opportunity] = []
    for name, fetch_fn in (
        ("reddit", fetch_reddit_opportunities),
        ("devpost", fetch_devpost_opportunities),
        ("github", fetch_github_opportunities),
        ("hackernews", fetch_hackernews_opportunities),
    ):
        try:
            fetched = fetch_fn()
            log.info("%s: fetched %d raw items", name, len(fetched))
            all_opps.extend(fetched)
        except Exception:  # noqa: BLE001 - one flaky source must not take down the whole daily run
            log.exception("%s source raised an unhandled exception — skipping it for today", name)
    return all_opps


def run_pipeline() -> tuple[list[Opportunity], dict[str, str]]:
    """Fetches, filters, gatekeeps via Gemini, dedupes against Notion, and
    pushes what's left to the calendar feed + Notion (one page at a time,
    so each new page's id can be captured). Marks every *kept* item
    (accepted, gatekept, or a Notion duplicate) seen, so nothing is
    re-fetched/re-billed against the LLM quota tomorrow.

    Returns (accepted_opportunities, {opportunity.id: notion_page_id}) —
    accepted_opportunities is exactly what should trigger a "new
    opportunity" alert; anything already in Notion never reaches this list.
    Does NOT send any notifications — that's the caller's job: main()
    below sends the plain-text batch summary, bot/scheduler.py sends
    interactive per-opportunity messages with inline buttons instead.
    """
    raw = collect_all_opportunities()
    if not raw:
        log.warning("No opportunities fetched from any source — check credentials/network.")

    kept = filter_opportunities(raw)
    log.info("%d/%d opportunities passed the keyword filter (config.MIN_SCORE)", len(kept), len(raw))

    seen = prune_old(load_seen())
    new_opportunities, already_seen = split_new_vs_seen(kept, seen)
    log.info("%d new, %d already seen on a previous run", len(new_opportunities), len(already_seen))

    llm_used = 0
    llm_unreachable = 0
    gatekept: list[Opportunity] = []
    duplicates: list[Opportunity] = []
    accepted: list[Opportunity] = []
    for i, opp in enumerate(new_opportunities):
        if i > 0:
            # Paced, not bursty: firing N Gemini calls back-to-back with no
            # gap is exactly what trips a per-minute rate limit even when
            # the *daily* quota has plenty of headroom left (this is
            # separate from — and more common than — the daily cap; see
            # processing/llm_parser.py's retry logic for that case). This
            # matters more now that the bot runs twice a day, since each
            # run's new-item batch fires in one tight burst.
            time.sleep(LLM_CALL_DELAY)
        if parse_with_llm(opp):
            llm_used += 1
        else:
            # GEMINI_API_KEY unset, or the call failed even after retries —
            # fail CLOSED: nothing ships unless the LLM actually verified
            # it against the QA/prestige rules. An earlier version defaulted
            # this to "assume valid," which is exactly how an unrelated
            # job-referral Reddit post once slipped past the gatekeeper and
            # got broadcast as a "new opportunity" — see
            # processing/llm_parser.py's module docstring.
            opp.is_valid_opportunity = False
            llm_unreachable += 1

        if opp.is_valid_opportunity is False:
            gatekept.append(opp)
            continue

        opp.program_name = opp.program_name or opp.title
        opp.application_link = opp.application_link or opp.url

        # Notion is the source of truth for "have we already alerted on
        # this?" — data/seen.json (below) is the fast/cheap first pass, but
        # checking Notion by URL/program name too means the "new
        # opportunity" alert still can't fire twice even if local state
        # were ever lost, reset, or out of sync.
        if find_existing_notion_page(opp.application_link, opp.program_name):
            duplicates.append(opp)
            continue

        accepted.append(opp)

    if new_opportunities:
        log.info("%d/%d new opportunities verified by Gemini", llm_used, len(new_opportunities))
    if llm_unreachable:
        log.warning("%d opportunity(ies) rejected because Gemini was unreachable after retries "
                    "(GEMINI_API_KEY quota/outage) — nothing ships unverified", llm_unreachable)
    if gatekept:
        log.info("Gemini gatekeeper rejected %d post(s) (not a real/prestige-quality opportunity, "
                  "or Gemini was unreachable — see above)", len(gatekept))
    if duplicates:
        log.info("Skipped %d opportunity(ies) already present in Notion (URL/name match)", len(duplicates))

    ics_added = add_opportunities_to_ics(accepted)
    log.info("Added %d events to data/opportunities.ics", ics_added)

    gcal_pushed = push_opportunities_to_google_calendar(accepted)
    if gcal_pushed:
        log.info("Pushed %d events directly to Google Calendar", gcal_pushed)

    page_ids: dict[str, str] = {}
    for opp in accepted:
        page_id = push_opportunity_to_notion(opp)
        if page_id:
            page_ids[opp.id] = page_id
    if page_ids:
        log.info("Pushed %d opportunities to Notion", len(page_ids))

    # Mark every *kept* item seen (not just the accepted ones) so a re-run
    # on the same day — or one that crashed after this point last time —
    # doesn't re-fetch/re-notify/re-spend LLM quota on things already
    # recorded (this includes gatekept junk too).
    seen = mark_seen(kept, seen)
    save_seen(seen)

    return accepted, page_ids


def main() -> int:
    new_opportunities, page_ids = run_pipeline()

    discord_sent = send_discord_summary(new_opportunities)
    telegram_sent = send_telegram_summary(new_opportunities)
    if new_opportunities and not (discord_sent or telegram_sent):
        log.info("No notification channel configured — set DISCORD_WEBHOOK_URL or TELEGRAM_BOT_TOKEN/CHAT_ID")

    reminders_sent = check_and_notify_todays_announcements()
    if reminders_sent:
        log.info("Sent a Telegram reminder for %d opportunity(ies) announcing results today", reminders_sent)

    countdowns_sent = check_and_notify_deadline_countdowns()
    if countdowns_sent:
        log.info("Sent %d deadline-countdown reminder(s) (1-10 days out) via Telegram", countdowns_sent)

    log.info("Done. %d new opportunities today.", len(new_opportunities))
    return 0


if __name__ == "__main__":
    sys.exit(main())
