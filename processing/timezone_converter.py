"""Converts an LLM-extracted deadline (date + time + source timezone) into
Europe/Istanbul local time, so a deadline like "11:59 PM PST" doesn't quietly
turn into a missed application for a Turkey-based user reasoning in TR time.
Crossing midnight during the conversion can shift the *calendar date* too
(e.g. 11:59 PM EST on Aug 11 is already Aug 12 morning in Istanbul) — that's
exactly the kind of mistake this module exists to prevent.

Uses the stdlib `zoneinfo` (Python 3.9+) rather than adding `pytz` as a new
dependency. `zoneinfo` needs the IANA tz database, which Windows doesn't
ship — see `tzdata` in requirements.txt.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from dateparser.search import search_dates

log = logging.getLogger(__name__)

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")

# Common abbreviations the LLM is likely to return, mapped to an IANA zone
# with a matching current UTC offset. US abbreviations are inherently
# ambiguous about DST (PST vs PDT depend on the date) — mapping to the
# region and letting zoneinfo apply the correct rule for that specific date
# is more reliable than hardcoding a fixed offset per abbreviation.
_TZ_ALIASES: dict[str, str] = {
    "UTC": "UTC", "GMT": "UTC",
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles", "PT": "America/Los_Angeles",
    "MST": "America/Denver", "MDT": "America/Denver", "MT": "America/Denver",
    "CST": "America/Chicago", "CDT": "America/Chicago", "CT": "America/Chicago",
    "EST": "America/New_York", "EDT": "America/New_York", "ET": "America/New_York",
    "CET": "Europe/Paris", "CEST": "Europe/Paris",
    "BST": "Europe/London",
    "IST": "Asia/Kolkata",
}

DEFAULT_TIME = time(23, 59)
DEFAULT_TZ_ABBR = "UTC"


def _resolve_zone(tz_hint: str | None) -> ZoneInfo:
    iana_name = _TZ_ALIASES.get((tz_hint or "").strip().upper(), "UTC")
    try:
        return ZoneInfo(iana_name)
    except Exception:  # unrecognized/corrupt zone data — fall back to UTC rather than crash the run
        return ZoneInfo("UTC")


def _parse_time(raw: str | None) -> time:
    """Parses an "HH:MM" (24h) string. Falls back to DEFAULT_TIME (23:59)
    if missing or malformed."""
    if not raw or not raw.strip():
        return DEFAULT_TIME
    try:
        hour, minute = raw.strip().split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        return DEFAULT_TIME


def convert_deadline_to_istanbul(date_phrase: str | None, time_str: str | None, tz_hint: str | None) -> str | None:
    """Combines a free-text date phrase (e.g. "August 12, 2026") with an
    explicit 24h time and source timezone abbreviation into a
    timezone-aware datetime, and converts it to Europe/Istanbul local time.

    Returns an ISO 8601 datetime string carrying the Istanbul UTC offset,
    e.g. "2026-08-12T06:59:00+03:00" — the date component already reflects
    any midnight-crossing shift, so callers can treat it as authoritative
    Turkey-local. Returns None if date_phrase has no parseable date.
    """
    if not date_phrase or not date_phrase.strip():
        return None

    matches = search_dates(date_phrase, settings={"PREFER_DATES_FROM": "future"}) or []
    if not matches:
        return None
    naive_date: date = matches[0][1].date()

    source_dt = datetime.combine(naive_date, _parse_time(time_str), tzinfo=_resolve_zone(tz_hint))
    return source_dt.astimezone(ISTANBUL_TZ).isoformat()


def format_istanbul_display(deadline_date: str | None, deadline_time_istanbul: str | None) -> str | None:
    """Friendly TR-local display string for a deadline: "12.08.2026 09:59
    (TR Saatiyle)" when a precise time is known (deadline_time_istanbul —
    see Opportunity), else just "12.08.2026" from the bare date. Returns
    None if neither is available."""
    if deadline_time_istanbul:
        dt = datetime.fromisoformat(deadline_time_istanbul)
        return f"{dt.strftime('%d.%m.%Y %H:%M')} (TR Saatiyle)"
    if deadline_date:
        return datetime.fromisoformat(deadline_date).strftime("%d.%m.%Y")
    return None
