"""GitHub source: discovers curated opportunity-list repos via GitHub's
Search API (by topic), then extracts individual entries from their READMEs.
Also directly targets known high-signal internship-list repos (currently
SimplifyJobs, which absorbed PittCSC's list — same repo, jointly maintained,
verified live) by searching *within* that org rather than hardcoding an
exact repo name, since these repos get renamed every recruiting season
(e.g. Summer2026-Internships -> Summer2027-Internships).

Topic search deliberately does NOT hardcode a specific repo name either.
Curated "awesome list"-style repos come and go / get renamed / get
abandoned; searching each run means the source self-updates instead of
silently going stale.
"""
from __future__ import annotations

import base64
import logging
import os
import re

import requests

from config import GITHUB_SEARCH_TOPICS
from sources.base import Opportunity

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
REQUEST_TIMEOUT = 15
REPOS_PER_TOPIC = 5       # how many top (most recently updated) repos to pull per topic
MAX_ENTRIES_PER_REPO = 40  # cap so one huge README can't dominate a run

# High-signal internship-list orgs to search directly, by *name pattern*
# rather than an exact repo name — SimplifyJobs/PittCSC rename their main
# repo every season (verified live: currently "Summer2027-Internships").
CURATED_ORGS = ["SimplifyJobs"]
_CURATED_NAME_PATTERNS = (
    re.compile(r"^Summer\d{4}-Internships$"),
    re.compile(r"^New-Grad-Positions$"),
)

# Matches a markdown link: [link text](https://example.com)
_LINK_RE = re.compile(r"\[([^\]]{3,120})\]\((https?://[^\s)]+)\)")

# Some curated internship-list READMEs (verified live: SimplifyJobs/PittCSC's
# lists) render their table as raw HTML rather than markdown pipe-tables —
# <tr><td>Company</td><td>Role</td><td>Location</td><td>...apply links...</td></tr>
# — which _LINK_RE's markdown-only syntax can't see at all.
_TABLE_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TABLE_CELL_RE = re.compile(r"<td>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_ANCHOR_HREF_RE = re.compile(r'<a\s+[^>]*href="([^"]+)"', re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Curated-list READMEs are full of shields.io/badge-style images used as
# decoration (stat counters, "PRs welcome" banners, etc.) — these match the
# same [text](url) syntax as real links but aren't opportunities.
_BADGE_HOST_HINTS = (
    "shields.io", "badgen.net", "badge.fury.io", "camo.githubusercontent.com",
    "img.shields", "visitor-badge", "counter.", "hits.sh",
)


def _looks_like_badge(line: str, match: re.Match) -> bool:
    url = match.group(2)
    if any(hint in url for hint in _BADGE_HOST_HINTS):
        return True
    # Image-link syntax: ![alt](badge_url) — the "!" sits right before the "["
    # our _LINK_RE match starts at, whether it's the whole image link or the
    # target-URL half of a linked badge like [![alt](img)](target).
    return match.start() > 0 and line[match.start() - 1] == "!"


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _search_repos_by_topic(topic: str, per_page: int) -> list[dict]:
    params = {"q": f"topic:{topic} fork:false", "sort": "updated", "order": "desc", "per_page": per_page}
    try:
        resp = requests.get(f"{API_BASE}/search/repositories", params=params, headers=_headers(), timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("items", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("GitHub repo search failed for topic %r: %s", topic, exc)
        return []


def _find_curated_repos() -> list[str]:
    """Looks up the current names of the curated internship repos we track
    directly (see CURATED_ORGS/_CURATED_NAME_PATTERNS), rather than
    hardcoding a season-specific name that goes stale in a year."""
    full_names: list[str] = []
    for org in CURATED_ORGS:
        try:
            resp = requests.get(
                f"{API_BASE}/orgs/{org}/repos",
                params={"sort": "updated", "per_page": 30},
                headers=_headers(),
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            for repo in resp.json():
                if any(p.match(repo["name"]) for p in _CURATED_NAME_PATTERNS):
                    full_names.append(repo["full_name"])
        except (requests.RequestException, ValueError) as exc:
            log.warning("Failed to list curated repos for org %r: %s", org, exc)
    return full_names


def _fetch_readme_text(full_name: str) -> str | None:
    try:
        resp = requests.get(f"{API_BASE}/repos/{full_name}/readme", headers=_headers(), timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("encoding") != "base64":
            return None
        return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
    except (requests.RequestException, ValueError) as exc:
        log.warning("Failed to fetch README for %s: %s", full_name, exc)
        return None


def _extract_html_table_rows(readme_text: str, repo_full_name: str) -> list[Opportunity]:
    """Parses raw HTML <table> rows: Company | Role | Location | Application
    (icon links) | Age. The Application cell typically has the real
    employer/ATS apply link first, then a tracking-site icon link second —
    the first <a href> is the one worth keeping."""
    entries: list[Opportunity] = []
    for row_match in _TABLE_ROW_RE.finditer(readme_text):
        cells = _TABLE_CELL_RE.findall(row_match.group(1))
        if len(cells) < 3:
            continue  # not a data row (header, malformed, or a nested table)

        company = _HTML_TAG_RE.sub(" ", cells[0]).strip()
        role = _HTML_TAG_RE.sub(" ", cells[1]).strip() if len(cells) > 1 else ""
        location = _HTML_TAG_RE.sub(" ", cells[2]).strip() if len(cells) > 2 else ""
        if not company or company.lower() == "company":
            continue  # header row

        apply_url = None
        if len(cells) > 3:
            anchor = _ANCHOR_HREF_RE.search(cells[3])
            apply_url = anchor.group(1) if anchor else None
        if not apply_url:
            continue

        entries.append(
            Opportunity(
                source="github",
                title=f"{company} — {role}".strip(" —"),
                url=apply_url,
                raw_text=f"{company} {role} {location}".strip(),
                extra={"repo": repo_full_name},
            )
        )
        if len(entries) >= MAX_ENTRIES_PER_REPO:
            break
    return entries


def _extract_entries(readme_text: str, repo_full_name: str) -> list[Opportunity]:
    """Pull one Opportunity per link found, merged (deduped by URL) across
    every curated-list format seen in the wild:
      - Raw HTML tables:  <tr><td>Company</td><td>Role</td>...</tr>
      - Markdown tables:  | Name | Provider | ... | [Link](url) | Expiry |
      - Bullet lists:     - [Name](url) — free, remote, ends March 2026
    """
    entries: list[Opportunity] = []
    seen_urls: set[str] = set()

    for entry in _extract_html_table_rows(readme_text, repo_full_name):
        if entry.url in seen_urls:
            continue
        seen_urls.add(entry.url)
        entries.append(entry)
        if len(entries) >= MAX_ENTRIES_PER_REPO:
            return entries

    for line in readme_text.splitlines():
        for match in _LINK_RE.finditer(line):
            if _looks_like_badge(line, match):
                continue
            link_text, url = match.group(1).strip(), match.group(2).strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Use the whole line as context — for a table row that includes
            # the other columns (provider, description, expiration date);
            # for a list item it's the bullet's full text.
            context = line.strip().lstrip("-*| ").strip()
            entries.append(
                Opportunity(
                    source="github",
                    title=link_text,
                    url=url,
                    raw_text=f"{link_text}\n{context}",
                    extra={"repo": repo_full_name},
                )
            )
            if len(entries) >= MAX_ENTRIES_PER_REPO:
                return entries

    return entries


def fetch_github_opportunities() -> list[Opportunity]:
    results: list[Opportunity] = []
    seen_repos: set[str] = set()

    for full_name in _find_curated_repos():
        seen_repos.add(full_name)
        readme = _fetch_readme_text(full_name)
        if readme:
            results.extend(_extract_entries(readme, full_name))

    for topic in GITHUB_SEARCH_TOPICS:
        for repo in _search_repos_by_topic(topic, REPOS_PER_TOPIC):
            full_name = repo["full_name"]
            if full_name in seen_repos:
                continue
            seen_repos.add(full_name)

            readme = _fetch_readme_text(full_name)
            if not readme:
                continue
            results.extend(_extract_entries(readme, full_name))

    return results
