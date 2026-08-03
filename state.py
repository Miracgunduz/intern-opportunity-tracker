"""Persisted "have we seen this before" state (data/seen.json).

Without this, every daily run would re-notify and re-add calendar events
for opportunities found on previous runs. The GitHub Actions workflow
commits this file back to the repo after each run so state survives
between cron executions (Actions runners are otherwise stateless).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from config import SEEN_RETENTION_DAYS, STATE_FILE
from sources.base import Opportunity


def load_seen() -> dict[str, str]:
    """Returns {opportunity_id: first_seen_iso_date}. Empty dict if the
    state file doesn't exist yet (i.e. this is the very first run)."""
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}  # corrupt/empty file — treat as fresh state rather than crashing the run


def save_seen(seen: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)


def split_new_vs_seen(
    opportunities: list[Opportunity], seen: dict[str, str]
) -> tuple[list[Opportunity], list[Opportunity]]:
    """Returns (new_opportunities, already_seen_opportunities)."""
    new, already_seen = [], []
    for opp in opportunities:
        (new if opp.id not in seen else already_seen).append(opp)
    return new, already_seen


def mark_seen(opportunities: list[Opportunity], seen: dict[str, str]) -> dict[str, str]:
    """Records today's date for every given opportunity. Mutates and
    returns `seen` for convenience."""
    today = date.today().isoformat()
    for opp in opportunities:
        seen.setdefault(opp.id, today)
    return seen


def prune_old(seen: dict[str, str]) -> dict[str, str]:
    """Drops entries older than SEEN_RETENTION_DAYS so the state file
    doesn't grow forever. An opportunity dropping out of state just means
    it could be re-notified if it somehow reappeared months later — an
    acceptable trade for a file that stays small."""
    cutoff = datetime.now().date() - timedelta(days=SEEN_RETENTION_DAYS)
    return {
        opp_id: first_seen
        for opp_id, first_seen in seen.items()
        if _safe_parse_date(first_seen) >= cutoff
    }


def _safe_parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.today()  # malformed entry — keep it rather than guess it's old
