"""LLM-based gatekeeper + structured extraction via Google Gemini.

Runs only on already-filtered NEW opportunities (see main.py) — the cheap
keyword filter in processing/filters.py does the first pass, so this
network+quota-consuming step never runs against the whole raw firehose.
That keyword pass is loose by design (it's just "does this text contain
words like junior/remote/free/certification"), so it still lets through a
lot of general discussion threads ("which language should I learn?") that
merely mention those words. Gemini is the real gate: it's asked to judge
is_valid_opportunity first, and main.py drops anything marked False before
it ever reaches Notion/Telegram/the calendar feed.

Retries transient errors (429/503/timeout) a few times with backoff before
giving up, since a rate-limited free-tier key is common in normal
operation, not exceptional. If it still can't get an answer, or
GEMINI_API_KEY isn't set, parse_with_llm() returns False — and main.py
treats that as "reject" (fail CLOSED), not "assume valid." An earlier
version defaulted an unreachable LLM to "keep it," which is exactly what
let an unrelated job-referral Reddit post slip past the QA/prestige filter
and get broadcast as a "new opportunity" in production — the whole point
of this gatekeeper is that nothing goes out unless the LLM actually
verified it, so "couldn't verify" has to mean "don't send," never "send
anyway."

Setup: free API key at https://aistudio.google.com/apikey -> GEMINI_API_KEY.
"""
from __future__ import annotations

import json
import logging
import os
import time

import requests
from dateparser.search import search_dates

from sources.base import Opportunity

from .timezone_converter import convert_deadline_to_istanbul

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 25
# "-lite" gets a much higher free-tier daily request quota than the full
# "flash" model (observed live: the full model's free tier is capped at
# 20 requests/DAY, project-wide — nowhere near enough for ~40 new
# opportunities/day; lite has comfortably handled this project's volume in
# testing). Override via GEMINI_MODEL if you're on a paid tier and want
# the full model's quality instead.
DEFAULT_MODEL = "gemini-flash-lite-latest"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
RAW_TEXT_CHAR_LIMIT = 4000  # keep prompts small — cheap, fast, plenty for a Reddit/GitHub post
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5  # doubles each retry: 5s, 10s — a 429/503 is usually gone by then
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_valid_opportunity": {"type": "BOOLEAN"},
        "rejection_reason": {"type": "STRING"},
        "program_name": {"type": "STRING"},
        "application_link": {"type": "STRING"},
        "eligibility": {"type": "STRING"},
        "deadline": {"type": "STRING"},
        "deadline_time": {"type": "STRING"},
        "deadline_timezone": {"type": "STRING"},
        "announcement_date": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "turkish_cv_summary": {"type": "STRING"},
    },
    "required": ["is_valid_opportunity"],
}

_PROMPT_TEMPLATE = """You are a strict Quality Assurance gatekeeper for a tracker that only wants \
real, high-caliber, actionable CV-boosting opportunities suitable for a junior software developer \
based in Turkey — found in a raw post scraped from Reddit/Devpost/GitHub/Hacker News. This user has \
explicitly asked for zero spam and zero low-effort/paid programs disguised as free — when in doubt, \
reject.

Source: {source}
Title: {title}
URL: {url}
Raw text:
---
{raw_text}
---

Step 1 — decide is_valid_opportunity. First work out which TYPE the text is, then check ALL of that \
type's criteria. If it doesn't clearly fit TYPE A or TYPE B, or you can't confidently verify a \
required criterion from the text, reject (false).

TYPE A — a program (free certification, scholarship, bootcamp, hackathon, or fellowship) with \
something to apply to. ALL FOUR must hold:
(a) Reputable organizer: run by a recognized tech giant (Microsoft, Google, Meta, AWS, Apple, IBM, \
Adobe, Salesforce, NVIDIA, ...), a recognized foundation (Linux Foundation, CNCF, Apache, Mozilla, \
...), a major hackathon organizer/platform (MLH, Devpost, official university hackathons, ...), or a \
well-known, credible open-source project or university. A vague "we're launching a program", an \
unverified startup, or a low-effort/unknown bootcamp does NOT qualify.
(b) Genuinely free or fully funded: no tuition, no "pay to get the final certificate", no freemium \
upsell, no hidden fee anywhere in the described flow. A "free trial" that requires payment to \
actually finish or get certified does NOT qualify.
(c) Remote/global accessibility: explicitly open to remote/global participants — not restricted to \
physical presence in one country (especially US-only/EU-only) or citizenship/visa/work-authorization \
that would exclude someone based in Turkey.
(d) Legitimate link: points to a recognizable, official domain for the organizer (the org's own \
site, github.com, a known platform like devpost.com) — not an unclear shortener or a domain \
mismatched with the claimed organizer.

TYPE B — a genuine internship/entry-level job opening actually being OFFERED by a real employer \
(e.g. a company's own hiring post from an "Ask HN: Who is hiring?" thread, or an entry from a \
curated internship-list repo). This is NOT the same as someone individually asking for a referral, \
looking for work themselves, or posting career advice/questions — those are always false, no matter \
how they're worded. ALL THREE must hold for TYPE B:
(a) Legitimate employer: names an identifiable company/organization actually offering the role — not \
an anonymous, vague, or scammy-sounding pitch ("DM me for details", crypto/MLM-flavored, unrealistic \
equity-only offers with no real product).
(b) Suitable for a junior/entry-level candidate: explicitly an internship, new-grad, or junior/ \
entry-level role — not one requiring years of professional experience. Being PAID is completely \
normal and expected here — never reject a real job/internship for offering compensation; the \
"genuinely free" rule above is for TYPE A programs only, not employment.
(c) Not obviously closed to someone based in Turkey: reject only if the post explicitly requires \
U.S./EU work authorization, on-site presence in a specific country, or citizenship a Turkey-based \
applicant wouldn't have. If remote-friendliness just isn't mentioned either way, don't reject solely \
for that — most real listings simply don't say.

Also false (regardless of type) if this is general discussion, a question ("which language should I \
learn?", "is X worth it?"), career advice, news, a rant/opinion, or an individual seeking work/ \
referrals for themselves rather than an org offering something.

If is_valid_opportunity is false, return ONLY {{"is_valid_opportunity": false, "rejection_reason": "..."}} \
— rejection_reason must be one short phrase naming exactly which check failed (e.g. "TYPE A: organizer \
not a recognized/reputable org", "TYPE B: individual seeking work, not an employer offering a role", \
"TYPE A: requires payment for the certificate", "neither type: general discussion/career question"). \
No other keys.

If is_valid_opportunity is true, also return:
- program_name: clean name of the program/opportunity, or "Company — Role" for a TYPE B posting \
(not the raw post title if it's noisy).
- application_link: the direct application/info URL if one is visible in the text, otherwise "{url}".
- eligibility: one short sentence on who can apply (e.g. "students worldwide, no experience required").
- deadline: the application deadline date exactly as written in the text (just the date part, e.g. \
"August 12, 2026"), or "" if not mentioned.
- deadline_time: the exact deadline time as a 24-hour "HH:MM" string, converted from whatever format \
the text uses (e.g. "11:59 PM" -> "23:59"). If no time is mentioned, default to "23:59".
- deadline_timezone: the timezone the deadline time is in, as a short abbreviation (UTC, PST, PDT, \
MST, MDT, CST, CDT, EST, EDT, CET, BST, IST, ...). If no timezone is mentioned, default to "UTC".
- announcement_date: the expected results/decision date exactly as written in the text, or "" if not mentioned.
- summary: a punchy 2-sentence summary (in English) of why this is worth applying to.
- turkish_cv_summary: exactly 2 sentences, written entirely in Turkish, explaining (1) what this \
opportunity actually is and (2) why it is valuable to add to a Junior Software Developer's CV. This \
must be natural, fluent Turkish — not a literal translation of the English summary.

If a field truly isn't in the text, use an empty string for it rather than guessing."""


def _normalize_date(raw: str | None) -> str | None:
    """LLM extracts the date phrase as written (often with filler words like
    "around" or "on" that dateparser.parse() chokes on since it requires the
    whole string to be a clean date) — search_dates() scans for the date
    substring instead, same approach as processing/date_parser.py. Returns
    None if raw is empty or nothing parseable is found."""
    if not raw or not raw.strip():
        return None
    matches = search_dates(raw, settings={"PREFER_DATES_FROM": "future"}) or []
    return matches[0][1].date().isoformat() if matches else None


def parse_with_llm(opportunity: Opportunity) -> bool:
    """Asks Gemini to gatekeep + extract, in place, on `opportunity`:

    - Always sets opportunity.is_valid_opportunity (True/False) on success.
    - If True, also fills program_name/application_link/eligibility/summary/
      turkish_cv_summary and (if found) deadline/deadline_time_istanbul/
      announcement_date. `deadline` is the Europe/Istanbul-local *date*
      (already converted from whatever time/timezone was in the text —
      see processing/timezone_converter.py); `deadline_time_istanbul` is
      the full datetime for display.
    - If False, leaves those fields alone — main.py drops the opportunity
      before it reaches Notion/Telegram/the calendar feed.

    Returns True if the LLM call succeeded (regardless of the verdict —
    check opportunity.is_valid_opportunity for that). Returns False if the
    LLM step didn't run or failed after retries (bad key, persistent
    network error, malformed response, ...) — callers must treat that as a
    reject, NOT as "assume valid" (see the module docstring for why).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return False

    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    prompt = _PROMPT_TEMPLATE.format(
        source=opportunity.source,
        title=opportunity.title,
        url=opportunity.url,
        raw_text=opportunity.raw_text[:RAW_TEXT_CHAR_LIMIT],
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
            "temperature": 0.1,
        },
    }
    url = f"{API_BASE}/{model}:generateContent"

    fields = None
    for attempt in range(MAX_RETRIES + 1):
        # Network-level failure (timeout, connection reset, DNS, ...) —
        # always transient, always worth retrying.
        try:
            resp = requests.post(url, params={"key": api_key}, json=payload, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            log.warning("Gemini request failed for %r (attempt %d/%d): %s",
                        opportunity.title, attempt + 1, MAX_RETRIES + 1, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
            continue

        # Retryable HTTP status (rate limit / server-side hiccup) — retry.
        if resp.status_code in _RETRYABLE_STATUS_CODES:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS * (2**attempt)
                log.info("Gemini call for %r hit %d, retrying in %ds", opportunity.title, resp.status_code, wait)
                time.sleep(wait)
                continue
            log.warning("Gemini call for %r still %d after %d attempts",
                        opportunity.title, resp.status_code, MAX_RETRIES + 1)
            break  # retries exhausted — fields stays None

        # Non-retryable HTTP error (bad key, malformed request, ...) — fail
        # fast, retrying won't fix a permanent problem.
        if not resp.ok:
            log.warning("Gemini call for %r failed with non-retryable status %d: %s",
                        opportunity.title, resp.status_code, resp.text[:300])
            break

        # Malformed response shape — also won't fix itself on retry.
        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            fields = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            log.warning("Gemini response malformed for %r: %s", opportunity.title, exc)
        break

    if fields is None:
        log.warning("Gemini gave up on %r — rejecting rather than assuming valid", opportunity.title)
        return False

    opportunity.is_valid_opportunity = bool(fields.get("is_valid_opportunity"))
    if not opportunity.is_valid_opportunity:
        # Logged here (not just counted in main.py's aggregate) so a run's
        # log can actually be read to judge whether the gatekeeper is
        # over-rejecting real opportunities or correctly killing spam —
        # without this, "N rejected" gave no way to tell which.
        reason = fields.get("rejection_reason") or "no reason given"
        log.info("Gemini rejected %r (%s): %s", opportunity.title, opportunity.source, reason)
        return True  # LLM ran fine; it just isn't a real opportunity — caller skips it

    opportunity.program_name = fields.get("program_name") or opportunity.title
    opportunity.application_link = fields.get("application_link") or opportunity.url
    opportunity.eligibility = fields.get("eligibility") or None
    opportunity.summary = fields.get("summary") or None
    opportunity.turkish_cv_summary = fields.get("turkish_cv_summary") or None

    istanbul_deadline = convert_deadline_to_istanbul(
        fields.get("deadline"), fields.get("deadline_time"), fields.get("deadline_timezone")
    )
    if istanbul_deadline:
        opportunity.deadline_time_istanbul = istanbul_deadline
        opportunity.deadline = istanbul_deadline.split("T", 1)[0]  # date-only, for ics/gcal/state/filters

    announcement = _normalize_date(fields.get("announcement_date"))
    if announcement:
        opportunity.announcement_date = announcement

    return True
