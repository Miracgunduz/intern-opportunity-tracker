"""Notion pipeline: every new opportunity becomes a page in a Notion
database, and two daily checks turn that into proactive Telegram nudges:
  - an exact-match check: Announcement Date is today -> "results are out" reminder.
  - a countdown check: Deadline (or Start Date, if there's no deadline) is
    1-10 days away -> daily "⏳ N days left" reminder until it passes.

One-time manual setup (Notion has no CLI/API to create a workspace or an
integration from scratch — this is the one unavoidable click-around step
in the whole project; everything past it is automated by setup.sh):
  1. https://www.notion.so/my-integrations -> New integration -> copy the
     "Internal Integration Secret" into NOTION_TOKEN.
  2. In Notion, create (or pick) a page to hold the database -> "..." menu
     -> Connections -> add your integration -> copy the page's ID (the
     32-char hex string at the end of its URL) into NOTION_PARENT_PAGE_ID.
  3. Run `python scripts/notion_create_database.py` once (setup.sh does
     this automatically) — it creates the database with the right schema
     under that page and prints the database ID -> NOTION_DATABASE_ID.

If NOTION_TOKEN/NOTION_DATABASE_ID aren't set, every function here no-ops
(returns 0 / [] ) — same fail-soft pattern as every other integration in
this project.
"""
from __future__ import annotations

import html
import logging
import os
from datetime import datetime

import requests

from processing.timezone_converter import ISTANBUL_TZ
from sources.base import Opportunity

from .telegram_notify import send_telegram_text

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
NOTION_VERSION = "2022-06-28"
API_BASE = "https://api.notion.com/v1"

COUNTDOWN_MIN_DAYS = 1
COUNTDOWN_MAX_DAYS = 10


def _headers() -> dict[str, str] | None:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _page_properties(opportunity: Opportunity) -> dict:
    name = opportunity.program_name or opportunity.title
    link = opportunity.application_link or opportunity.url
    props: dict = {
        "Name": {"title": [{"text": {"content": name[:2000]}}]},
        "Source": {"select": {"name": opportunity.source}},
        "Score": {"number": opportunity.score},
    }
    if link:
        props["Application Link"] = {"url": link}
    if opportunity.eligibility:
        props["Eligibility"] = {"rich_text": [{"text": {"content": opportunity.eligibility[:2000]}}]}
    if opportunity.summary:
        props["Summary"] = {"rich_text": [{"text": {"content": opportunity.summary[:2000]}}]}
    if opportunity.turkish_cv_summary:
        props["Turkish CV Summary"] = {"rich_text": [{"text": {"content": opportunity.turkish_cv_summary[:2000]}}]}
    if opportunity.deadline_time_istanbul:
        # Full ISO datetime with the +03:00 offset — Notion renders the time too.
        props["Deadline"] = {"date": {"start": opportunity.deadline_time_istanbul}}
    elif opportunity.deadline:
        props["Deadline"] = {"date": {"start": opportunity.deadline}}
    if opportunity.start_date:
        props["Start Date"] = {"date": {"start": opportunity.start_date}}
    if opportunity.announcement_date:
        props["Announcement Date"] = {"date": {"start": opportunity.announcement_date}}
    props["Status"] = {"select": {"name": "New"}}  # every page starts here; the bot moves it via buttons
    return props


def find_existing_notion_page(url: str | None, program_name: str | None) -> str | None:
    """Checks Notion for a page whose Application Link or Name already
    matches this opportunity, so the "new opportunity" alert is never sent
    twice for the same event — even if local state (data/seen.json) were
    ever lost, reset, or out of sync, Notion stays the source of truth.
    Matching is exact (Notion's API filter, not fuzzy), so differently
    reworded titles for the same program can still slip through; url
    matching is the more reliable of the two for that reason.

    Returns the existing page's id if a match is found, else None (also
    None if neither url nor program_name is given, or Notion isn't
    configured).
    """
    conditions = []
    if url:
        conditions.append({"property": "Application Link", "url": {"equals": url}})
    if program_name:
        conditions.append({"property": "Name", "title": {"equals": program_name}})
    if not conditions:
        return None

    filter_body = {"filter": {"or": conditions}} if len(conditions) > 1 else {"filter": conditions[0]}
    results = _query_database(filter_body)
    return results[0]["id"] if results else None


def push_opportunity_to_notion(opportunity: Opportunity) -> str | None:
    """Creates one Notion page for this opportunity. Returns the new page's
    id — needed to build the interactive "✅ Başvurdum" button's
    callback_data (see bot/keyboards.py) — or None if not configured or the
    request failed."""
    headers = _headers()
    database_id = os.environ.get("NOTION_DATABASE_ID")
    if not headers or not database_id:
        return None

    body = {"parent": {"database_id": database_id}, "properties": _page_properties(opportunity)}
    try:
        resp = requests.post(f"{API_BASE}/pages", headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["id"]
    except requests.RequestException as exc:
        log.warning("Failed to push %r to Notion: %s", opportunity.title, exc)
        return None


def push_opportunities_to_notion(opportunities: list[Opportunity]) -> int:
    """Batch wrapper around push_opportunity_to_notion — creates one page
    per opportunity, returns how many succeeded. Use the singular function
    directly when you need each new page's id (e.g. for Telegram buttons)."""
    return sum(1 for opp in opportunities if push_opportunity_to_notion(opp) is not None)


def update_notion_status(page_id: str, status: str) -> bool:
    """Sets a page's Status select property (New/Applied/Accepted/Rejected).
    Returns True on success. This is what the Telegram inline buttons call
    when clicked — see bot/handlers.py."""
    headers = _headers()
    if not headers or not page_id:
        return False

    body = {"properties": {"Status": {"select": {"name": status}}}}
    try:
        resp = requests.patch(f"{API_BASE}/pages/{page_id}", headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("Failed to set Notion status=%r on page %s: %s", status, page_id, exc)
        return False


def query_by_status(status: str) -> list[dict]:
    """Queries the Notion database for entries with the given Status.
    Returns a list of {"title", "url", "page_id"} dicts — used by the
    /basvurularim bot command."""
    results = _query_database({"filter": {"property": "Status", "select": {"equals": status}}})

    matches = []
    for page in results:
        link_prop = page.get("properties", {}).get("Application Link", {})
        matches.append(
            {
                "title": _page_title(page),
                "url": link_prop.get("url") or page.get("url", ""),
                "page_id": page["id"],
            }
        )
    return matches


def query_pending_opportunities() -> list[dict]:
    """Queries the Notion database for entries that are still Status="New"
    (not yet applied to) with a Deadline that hasn't passed yet. Returns a
    list of {"title", "url", "deadline_display"} dicts sorted soonest-first
    — used by the /bekleyenler bot command."""
    today = datetime.now(ISTANBUL_TZ).date().isoformat()  # GH Actions runs in UTC — reason in TR calendar days
    results = _query_database(
        {
            "filter": {
                "and": [
                    {"property": "Status", "select": {"equals": "New"}},
                    {"property": "Deadline", "date": {"on_or_after": today}},
                ]
            },
            "sorts": [{"property": "Deadline", "direction": "ascending"}],
        }
    )

    matches = []
    for page in results:
        link_prop = page.get("properties", {}).get("Application Link", {})
        deadline_raw = _page_date(page, "Deadline")
        matches.append(
            {
                "title": _page_title(page),
                "url": link_prop.get("url") or page.get("url", ""),
                "deadline_display": _format_target_date(deadline_raw) if deadline_raw else "Belirtilmemiş",
            }
        )
    return matches


def get_status_counts() -> dict[str, int]:
    """Counts Notion database entries per Status. Returns
    {"New": n, "Applied": n, "Accepted": n, "Rejected": n} (0 for any
    status with no matching pages, or if Notion isn't configured) — used
    by the /rapor bot command."""
    return {
        status: len(_query_database({"filter": {"property": "Status", "select": {"equals": status}}}))
        for status in ("New", "Applied", "Accepted", "Rejected")
    }


def _query_database(filter_body: dict) -> list[dict]:
    """POSTs a query to the configured database. Returns the raw `results`
    list, or [] if not configured or the request fails."""
    headers = _headers()
    database_id = os.environ.get("NOTION_DATABASE_ID")
    if not headers or not database_id:
        return []

    try:
        resp = requests.post(
            f"{API_BASE}/databases/{database_id}/query",
            headers=headers,
            json=filter_body,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.RequestException as exc:
        log.warning("Notion database query failed: %s", exc)
        return []


def _page_title(page: dict) -> str:
    title_prop = page.get("properties", {}).get("Name", {}).get("title", [])
    return title_prop[0]["plain_text"] if title_prop else "(untitled)"


def _page_date(page: dict, property_name: str) -> str | None:
    return (page.get("properties", {}).get(property_name) or {}).get("date", {}).get("start")


def _page_rich_text(page: dict, property_name: str) -> str | None:
    parts = (page.get("properties", {}).get(property_name) or {}).get("rich_text", [])
    return parts[0]["plain_text"] if parts else None


def _format_target_date(raw: str) -> str:
    """Notion's `date.start` is either a bare date ("2026-08-12") or a full
    ISO datetime with a UTC offset ("2026-08-12T06:59:00+03:00" — always
    Istanbul-local by the time it's written here, see llm_parser.py). Shows
    the time (and an explicit "TR Saatiyle" label) only when one is present."""
    dt = datetime.fromisoformat(raw)
    if "T" in raw:
        return f"{dt.strftime('%d.%m.%Y %H:%M')} (TR Saatiyle)"
    return dt.strftime("%d.%m.%Y")


def check_todays_announcements() -> list[dict]:
    """Queries the Notion database for entries whose Announcement Date is
    today. Returns a list of {"title", "url", "page_id"} dicts — empty if
    not configured or nothing matches today."""
    today = datetime.now(ISTANBUL_TZ).date().isoformat()  # GH Actions runs in UTC — reason in TR calendar days
    results = _query_database({"filter": {"property": "Announcement Date", "date": {"equals": today}}})

    matches = []
    for page in results:
        link_prop = page.get("properties", {}).get("Application Link", {})
        url = link_prop.get("url") or page.get("url", "")
        matches.append({"title": _page_title(page), "url": url, "page_id": page["id"]})
    return matches


def check_and_notify_todays_announcements() -> int:
    """Checks Notion for today's Announcement Date matches and sends one
    Telegram reminder if any are found. Returns how many matches were
    found (0 if none, or if Notion/Telegram aren't configured)."""
    matches = check_todays_announcements()
    if not matches:
        return 0

    lines = ["<b>Result day — these may announce today:</b>"]
    for m in matches:
        title = html.escape(m["title"])
        url = m["url"]
        lines.append(f'• <a href="{url}">{title}</a>' if url else f"• {title}")
    send_telegram_text("\n".join(lines))
    return len(matches)


def check_deadline_countdowns() -> list[dict]:
    """Queries the Notion database for entries that are STILL Status="New"
    (i.e. the "✅ Başvurdum" button hasn't been clicked yet) whose Deadline
    (preferred) or Start Date (fallback, for rolling-admission programs
    with no fixed deadline) is between COUNTDOWN_MIN_DAYS and
    COUNTDOWN_MAX_DAYS days from today.

    Status is part of the filter, not a post-hoc check, so this is also
    the "quiet period" enforcement: nothing here fires before the window
    opens (days_remaining > 10 just doesn't match), and nothing fires once
    you've applied — Accepted/Rejected are equally excluded since Status
    is no longer "New", and once a deadline no longer matters the
    Announcement Date reminder (check_todays_announcements) is the only
    thing that should still nudge you.

    Returns a list of {"title", "url", "days_remaining", "target_date",
    "turkish_cv_summary"} dicts, one per matching opportunity — empty if
    not configured or nothing is in the window."""
    results = _query_database(
        {
            "filter": {
                "and": [
                    {"property": "Status", "select": {"equals": "New"}},
                    {
                        "or": [
                            {"property": "Deadline", "date": {"is_not_empty": True}},
                            {"property": "Start Date", "date": {"is_not_empty": True}},
                        ]
                    },
                ]
            }
        }
    )

    today = datetime.now(ISTANBUL_TZ).date()  # GH Actions runs in UTC — reason in TR calendar days
    matches = []
    for page in results:
        target_raw = _page_date(page, "Deadline") or _page_date(page, "Start Date")
        if not target_raw:
            continue
        try:
            # datetime.fromisoformat handles both a bare date ("2026-08-12")
            # and a full datetime+offset ("2026-08-12T06:59:00+03:00") —
            # the latter is already Istanbul-local by the time it's stored
            # here (see processing/timezone_converter.py), so .date() is
            # the correct TR-local calendar date without further conversion.
            target_date = datetime.fromisoformat(target_raw).date()
        except ValueError:
            continue

        days_remaining = (target_date - today).days
        if not (COUNTDOWN_MIN_DAYS <= days_remaining <= COUNTDOWN_MAX_DAYS):
            continue

        link_prop = page.get("properties", {}).get("Application Link", {})
        matches.append(
            {
                "title": _page_title(page),
                "url": link_prop.get("url") or page.get("url", ""),
                "days_remaining": days_remaining,
                "target_date": target_raw,
                "turkish_cv_summary": _page_rich_text(page, "Turkish CV Summary"),
            }
        )
    return matches


def check_and_notify_deadline_countdowns() -> int:
    """Sends one Telegram reminder per still-"New" opportunity whose
    deadline/start date is 1-10 days away — repeats daily until the window
    passes or you click "✅ Başvurdum" (whichever comes first), so a
    time-sensitive opportunity keeps surfacing without turning into spam
    after you've already applied. Returns how many reminders were sent."""
    matches = check_deadline_countdowns()
    for m in matches:
        title = html.escape(m["title"])
        lines = [f"⏳ Hatırlatma: <b>{title}</b> başvurusu için son {m['days_remaining']} gün!"]
        if m["url"]:
            lines.append(f'🔗 <a href="{m["url"]}">{m["url"]}</a>')
        lines.append(f"📅 Deadline: {_format_target_date(m['target_date'])}")
        if m["turkish_cv_summary"]:
            lines.append("")
            lines.append(f"🇹🇷 {html.escape(m['turkish_cv_summary'])}")
        send_telegram_text("\n".join(lines))
    return len(matches)
