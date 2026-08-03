"""Reddit source: searches a fixed set of subreddits via Reddit's public
search RSS (Atom) feed — no API app / OAuth credentials needed.

Originally written against PRAW (the official API wrapper), but Reddit
closed self-service app registration under its "Responsible Builder
Policy" in 2026 — new API apps now require a manual approval request with
an uncertain wait, which isn't a good fit for a small personal script.
The public `search.rss` endpoint is still open and unauthenticated as of
this writing (verified live), so this source uses that instead. If Reddit
ever locks that down too, this source fails soft (logs a warning, returns
an empty list) rather than breaking the whole daily run — see main.py's
per-source try/except.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

from config import REDDIT_SUBREDDITS, SEARCH_TERMS
from sources.base import Opportunity

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
RESULTS_PER_QUERY = 15
REQUEST_DELAY = 3.0  # seconds between requests, unauthenticated reddit.com is 429-happy
MAX_RETRIES = 2
MAX_BACKOFF = 20.0  # cap per-retry wait so one stubborn subreddit can't eat the whole budget
TIME_BUDGET_SECONDS = 240  # stop starting new requests past this; a free CI runner shouldn't
# burn 30+ minutes waiting out Reddit's rate limit — whatever's collected by then is returned
# and the other sources (Devpost, GitHub) still get to run.
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Reddit's RSS <content> is HTML — good enough to just strip tags for
    keyword scoring / date parsing, we don't need to render it."""
    return _HTML_TAG_RE.sub(" ", text or "").strip()


def _search_subreddit(subreddit: str, query: str) -> list[Opportunity]:
    url = f"https://www.reddit.com/r/{subreddit}/search.rss"
    params = {"q": query, "restrict_sr": "1", "sort": "new", "limit": RESULTS_PER_QUERY}
    headers = {"User-Agent": "intern-opportunity-tracker/1.0 (personal automation script)"}

    root = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429 and attempt < MAX_RETRIES:
                wait = min(REQUEST_DELAY * (2**attempt) * 3, MAX_BACKOFF)
                log.info("Reddit RSS rate-limited for r/%s, backing off %.0fs", subreddit, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            break
        except (requests.RequestException, ET.ParseError) as exc:
            log.warning("Reddit RSS search failed for r/%s q=%r: %s", subreddit, query, exc)
            return []

    if root is None:
        return []

    opportunities = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title_el = entry.find("atom:title", ATOM_NS)
        content_el = entry.find("atom:content", ATOM_NS)
        link_el = entry.find("atom:link", ATOM_NS)

        title = (title_el.text or "").strip() if title_el is not None else ""
        body = _strip_html(content_el.text if content_el is not None else "")
        permalink = link_el.get("href") if link_el is not None else ""
        if not title or not permalink:
            continue

        opportunities.append(
            Opportunity(
                source="reddit",
                title=title,
                url=permalink,
                raw_text=f"{title}\n{body}",
                extra={"subreddit": subreddit, "query": query},
            )
        )
    return opportunities


def fetch_reddit_opportunities() -> list[Opportunity]:
    results: list[Opportunity] = []
    start = time.monotonic()
    first = True
    for subreddit in REDDIT_SUBREDDITS:
        for term in SEARCH_TERMS:
            if time.monotonic() - start > TIME_BUDGET_SECONDS:
                log.warning(
                    "Reddit source hit its %.0fs time budget (Reddit rate-limiting harder than "
                    "usual) — returning %d items collected so far, skipping the rest.",
                    TIME_BUDGET_SECONDS,
                    len(results),
                )
                return results
            if not first:
                time.sleep(REQUEST_DELAY)
            first = False
            results.extend(_search_subreddit(subreddit, term))
    return results
