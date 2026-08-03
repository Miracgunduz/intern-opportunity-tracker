"""Hacker News source: the monthly "Ask HN: Who is hiring?" thread, via the
official Algolia HN Search API (hn.algolia.com/api/v1) — free, no auth,
no HTML scraping. Each top-level comment is one company's hiring post; each
becomes a candidate Opportunity for the LLM gatekeeper to judge (a genuine
internship/entry-level opening from a real employer, vs. noise — see
processing/llm_parser.py's distinction between "an org is hiring" and
"a person is looking for work", the latter still rejected).

"Who is hiring?" threads are always posted by the account "whoishiring",
once a month — searching by that author (tags=story,author_whoishiring)
finds the current thread reliably, rather than fuzzy-matching a title that
could also match the same account's "Who wants to be hired?" /
"Who is freelancing?" siblings posted the same day.
"""
from __future__ import annotations

import html
import logging
import re

import requests

from sources.base import Opportunity

log = logging.getLogger(__name__)

ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
REQUEST_TIMEOUT = 15
MAX_COMMENTS = 60  # top-level comments to consider — threads often run 300+ deep

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return html.unescape(_HTML_TAG_RE.sub(" ", text or "")).strip()


def _find_latest_who_is_hiring_thread() -> dict | None:
    params = {"tags": "story,author_whoishiring", "hitsPerPage": 5}
    try:
        resp = requests.get(f"{ALGOLIA_BASE}/search_by_date", params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("HN search for 'who is hiring' thread failed: %s", exc)
        return None

    for hit in hits:
        # "who is hiring" only matches the hiring thread, not that day's
        # "who wants to be hired?" / "who is freelancing?" siblings.
        if "who is hiring" in (hit.get("title") or "").lower():
            return hit
    return None


def fetch_hackernews_opportunities() -> list[Opportunity]:
    thread = _find_latest_who_is_hiring_thread()
    if not thread:
        return []

    story_id = thread["objectID"]
    try:
        resp = requests.get(f"{ALGOLIA_BASE}/items/{story_id}", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        item = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Failed to fetch HN thread %s: %s", story_id, exc)
        return []

    results: list[Opportunity] = []
    for comment in (item.get("children") or [])[:MAX_COMMENTS]:
        text = _strip_html(comment.get("text") or "")
        if not text:
            continue
        comment_id = comment.get("id")
        results.append(
            Opportunity(
                source="hackernews",
                title=text.split("\n", 1)[0][:120],
                url=f"https://news.ycombinator.com/item?id={comment_id}",
                raw_text=text,
                extra={"thread": thread.get("title"), "author": comment.get("author")},
            )
        )
    return results
