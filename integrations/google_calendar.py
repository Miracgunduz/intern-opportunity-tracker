"""Optional: push events directly into a live Google Calendar using a
service account.

A service account is used instead of regular user OAuth specifically
*because* this runs unattended in GitHub Actions — user OAuth needs a
one-time interactive consent screen and its refresh token can expire or
get revoked, which would silently break a cron job with no one watching.
A service account's key doesn't expire that way.

One-time setup (~5 minutes):
  1. https://console.cloud.google.com -> new project (free) -> enable the
     "Google Calendar API".
  2. IAM & Admin -> Service Accounts -> create one -> Keys -> "Add key" ->
     JSON. This downloads a JSON file.
  3. Open your target Google Calendar's Settings -> "Share with specific
     people" -> add the service account's email (the "client_email" field
     inside the JSON) with "Make changes to events" permission.
  4. Calendar Settings -> "Integrate calendar" -> copy the Calendar ID.
  5. Put the JSON file's full contents (as one line) in the
     GOOGLE_SERVICE_ACCOUNT_JSON secret, and the Calendar ID in
     GOOGLE_CALENDAR_ID.

If those two env vars aren't set, this integration silently no-ops — the
.ics feed (integrations/ics_calendar.py) works completely standalone
without any of this and is the recommended default.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta

from sources.base import Opportunity

log = logging.getLogger(__name__)


def _get_calendar_service():
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID")
    if not creds_json or not calendar_id:
        return None, None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        log.warning(
            "GOOGLE_SERVICE_ACCOUNT_JSON is set but google-api-python-client isn't "
            "installed. Run: pip install -r requirements-google.txt"
        )
        return None, None

    info = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/calendar.events"]
    )
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    return service, calendar_id


def push_opportunities_to_google_calendar(opportunities: list[Opportunity]) -> int:
    """Best-effort: pushes one all-day event per parsed deadline.

    Returns how many events were pushed. Returns 0 (no-op, no error) if
    the integration isn't configured — callers don't need to check first.
    """
    service, calendar_id = _get_calendar_service()
    if service is None:
        return 0

    pushed = 0
    for opp in opportunities:
        if not opp.deadline:
            continue
        start = date.fromisoformat(opp.deadline)
        end = start + timedelta(days=1)
        event_body = {
            "summary": f"[Deadline] {opp.title}",
            "description": f"{opp.raw_text[:1000]}\n\nLink: {opp.url}",
            "start": {"date": start.isoformat()},
            "end": {"date": end.isoformat()},
            # Google event IDs must be lowercase a-v / 0-9 only, 5-1024
            # chars. Deriving it from the opportunity's own id keeps
            # repeated runs idempotent: inserting the same id twice fails
            # with 409, which we just treat as "already there".
            "id": f"intern{opp.id}deadline",
        }
        try:
            service.events().insert(calendarId=calendar_id, body=event_body).execute()
            pushed += 1
        except Exception as exc:  # noqa: BLE001 - googleapiclient raises HttpError, not ours to narrow
            if "409" in str(exc):
                continue
            log.warning("Failed to push event %r to Google Calendar: %s", opp.title, exc)

    return pushed
