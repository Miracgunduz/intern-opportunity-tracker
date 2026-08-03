"""Devpost source: pulls upcoming/open hackathons from Devpost's search API.

NOTE: `devpost.com/api/hackathons` is the JSON endpoint Devpost's own
website uses to power its hackathon search page — it isn't a formally
documented public API, so treat it as best-effort: if Devpost changes it,
this source will start returning nothing (fails soft, doesn't crash the
pipeline) rather than raising.
"""
from __future__ import annotations

import logging
import re

import requests

from sources.base import Opportunity

log = logging.getLogger(__name__)

DEVPOST_API_URL = "https://devpost.com/api/hackathons"
REQUEST_TIMEOUT = 15


def fetch_devpost_opportunities(pages: int = 2) -> list[Opportunity]:
    """Fetch open + upcoming hackathons, filtered to online ones.

    Devpost lets you filter by status and challenge_type via query params;
    we ask for "open" (accepting submissions now) so we don't waste time on
    ones the user can no longer join.
    """
    results: list[Opportunity] = []
    headers = {"User-Agent": "intern-opportunity-tracker/1.0 (personal automation script)"}

    for page in range(1, pages + 1):
        params = {
            "status[]": "open",
            "challenge_type[]": "online",
            "page": page,
        }
        try:
            resp = requests.get(DEVPOST_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Devpost fetch failed on page %d: %s", page, exc)
            break

        hackathons = data.get("hackathons", [])
        if not hackathons:
            break

        for h in hackathons:
            title = h.get("title", "")
            url = h.get("url", "")
            themes = ", ".join(t.get("name", "") for t in h.get("themes", []) or [])
            prize = re.sub(r"<[^>]+>", "", h.get("prize_amount", "") or "")  # strip embedded <span> tags
            submission_period = h.get("submission_period_dates", "")
            time_left = h.get("time_left_to_submission", "")
            organization = h.get("organization_name", "")
            location = (h.get("displayed_location") or {}).get("location", "")
            raw_text = (
                f"{title}\n"
                f"Organizer: {organization}\n"
                f"Location: {location}\n"
                f"Themes: {themes}\n"
                f"Prize: {prize}\n"
                f"Submission period: {submission_period} ({time_left})\n"
                f"Open online hackathon on Devpost. Free to enter, no fee required."
            )
            results.append(
                Opportunity(
                    source="devpost",
                    title=title,
                    url=url,
                    raw_text=raw_text,
                    extra={"submission_period": submission_period, "organization": organization},
                )
            )

    return results
