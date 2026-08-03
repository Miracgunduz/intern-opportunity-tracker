"""Best-effort deadline/start-date extraction from free text.

Strategy: look for date-like phrases near "trigger words" (deadline, apply
by, starts on, ...) first, since a date sitting right next to one of those
is far more likely to be the date that matters than some random date
mentioned elsewhere in the post. Falls back to "the first future date
anywhere in the text" if no trigger word is found nearby.

This is inherently fuzzy — free-text announcements don't follow a schema.
Treat `deadline`/`start_date` as "best guess, worth a human glance," not
ground truth, and that's exactly why every opportunity keeps its full
`raw_text` and `url` downstream (the .ics description links back to the
source so the user can verify before relying on the parsed date).
"""
from __future__ import annotations

from datetime import datetime

from dateparser.search import search_dates

from config import DEADLINE_TRIGGER_WORDS, START_TRIGGER_WORDS
from sources.base import Opportunity

_SEARCH_SETTINGS = {
    "PREFER_DATES_FROM": "future",
    "RETURN_AS_TIMEZONE_AWARE": False,
}
_TRIGGER_WINDOW_CHARS = 80  # how far past a trigger word to look for a date


def _dates_near_triggers(text: str, trigger_words: list[str]) -> list[datetime]:
    lower = text.lower()
    found_dates: list[datetime] = []

    for trigger in trigger_words:
        idx = lower.find(trigger.lower())
        if idx == -1:
            continue
        window = text[max(0, idx - 10): idx + len(trigger) + _TRIGGER_WINDOW_CHARS]
        matches = search_dates(window, settings=_SEARCH_SETTINGS) or []
        found_dates.extend(dt for _, dt in matches)

    return found_dates


def _earliest_future(dates: list[datetime]) -> datetime | None:
    now = datetime.now()
    future = sorted(d for d in dates if d >= now)
    return future[0] if future else None


def parse_dates(opportunity: Opportunity) -> None:
    """Fills in opportunity.deadline / opportunity.start_date (ISO date
    strings) in place. Leaves them as None if nothing plausible is found —
    callers should not assume every opportunity has a date.
    """
    text = opportunity.raw_text

    deadline_candidates = _dates_near_triggers(text, DEADLINE_TRIGGER_WORDS)
    start_candidates = _dates_near_triggers(text, START_TRIGGER_WORDS)

    # Nothing anchored to a trigger word? Fall back to any future date
    # mentioned in the text at all, and treat it as the deadline (the more
    # common thing people care about tracking).
    if not deadline_candidates and not start_candidates:
        matches = search_dates(text, settings=_SEARCH_SETTINGS) or []
        deadline_candidates = [dt for _, dt in matches]

    deadline = _earliest_future(deadline_candidates)
    start = _earliest_future(start_candidates)

    if deadline:
        opportunity.deadline = deadline.date().isoformat()
    if start:
        opportunity.start_date = start.date().isoformat()
