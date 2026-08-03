"""Generates/updates a single .ics calendar feed (data/opportunities.ics).

This is the primary calendar output: the user subscribes to it once in
Google Calendar (Settings -> Add calendar -> From URL, pointing at the raw
GitHub URL of this file) and it updates automatically on every push — no
OAuth flow needed, which is exactly what makes it work reliably from a
fully unattended CI job (see integrations/google_calendar.py for why a
*live* Google Calendar push is trickier).
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from icalendar import Calendar, Event

from config import ICS_FILE
from sources.base import Opportunity


def _load_or_create_calendar() -> Calendar:
    if os.path.exists(ICS_FILE):
        with open(ICS_FILE, "rb") as f:
            return Calendar.from_ical(f.read())

    cal = Calendar()
    cal.add("prodid", "-//intern-opportunity-tracker//github-actions//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Dev Opportunities Tracker")
    cal.add("x-wr-caldesc", "Auto-tracked free certifications, hackathons, and CV-boosting programs.")
    return cal


def _existing_uids(cal: Calendar) -> set[str]:
    return {str(component.get("uid")) for component in cal.walk("VEVENT")}


def _make_event(opportunity: Opportunity, event_date: date, label: str) -> Event:
    event = Event()
    event.add("uid", f"{opportunity.id}-{label}@intern-tracker")
    event.add("summary", f"[{label.title()}] {opportunity.title}")
    event.add("dtstart", event_date)
    event.add("dtend", event_date + timedelta(days=1))
    event.add("dtstamp", datetime.utcnow())
    event.add(
        "description",
        f"{opportunity.raw_text[:500]}\n\n"
        f"Source: {opportunity.source}\n"
        f"Link: {opportunity.url}\n"
        f"Relevance score: {opportunity.score:.1f}",
    )
    event.add("url", opportunity.url)
    return event


def add_opportunities_to_ics(opportunities: list[Opportunity]) -> int:
    """Appends a calendar event for every opportunity with a parsed
    deadline and/or start_date. Returns how many events were added.

    Idempotent by design: each event's UID is derived from the
    opportunity's stable id, so calling this again with the same
    opportunity is a no-op instead of a duplicate — a second safety net on
    top of state.py's seen-item tracking.
    """
    cal = _load_or_create_calendar()
    existing = _existing_uids(cal)
    added = 0

    for opp in opportunities:
        if opp.deadline:
            event = _make_event(opp, date.fromisoformat(opp.deadline), "deadline")
            if str(event.get("uid")) not in existing:
                cal.add_component(event)
                existing.add(str(event.get("uid")))
                added += 1
        if opp.start_date:
            event = _make_event(opp, date.fromisoformat(opp.start_date), "start")
            if str(event.get("uid")) not in existing:
                cal.add_component(event)
                existing.add(str(event.get("uid")))
                added += 1

    os.makedirs(os.path.dirname(ICS_FILE), exist_ok=True)
    with open(ICS_FILE, "wb") as f:
        f.write(cal.to_ical())

    return added
