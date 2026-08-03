"""Shared data model for every source module.

Every source (Reddit, Devpost, GitHub, ...) normalizes whatever it fetches
into this one shape, so the rest of the pipeline (filtering, date parsing,
calendar/notification output) never has to know where an opportunity came
from.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class Opportunity:
    source: str          # e.g. "reddit", "devpost", "github"
    title: str
    url: str
    raw_text: str         # full text used for keyword scoring + date extraction
    score: float = 0.0
    deadline: "str | None" = None    # ISO date string, filled in by llm_parser (Gemini is required — see main.py)
    start_date: "str | None" = None  # ISO date string; processing/date_parser.py can fill this but main.py no
                                      # longer calls it — the pipeline rejects anything Gemini couldn't verify
                                      # rather than falling back to a non-LLM heuristic
    extra: dict = field(default_factory=dict)

    # Filled in by processing/llm_parser.py — never falls back to title/url
    # when the LLM step is unavailable or fails; main.py rejects the
    # opportunity outright in that case (fail closed, not fail open).
    program_name: "str | None" = None
    application_link: "str | None" = None
    eligibility: "str | None" = None
    announcement_date: "str | None" = None  # ISO date string
    summary: "str | None" = None
    turkish_cv_summary: "str | None" = None  # 2-sentence Turkish "what it is + why it helps your CV"

    # Full ISO 8601 datetime (with +03:00 offset) — `deadline` above is
    # derived from this (its date component), converted from whatever
    # time/timezone the LLM extracted. See processing/timezone_converter.py.
    deadline_time_istanbul: "str | None" = None

    # Gemini's gatekeeper verdict: is this actually an actionable opportunity
    # (course/bootcamp/certification/hackathon/etc.) rather than general
    # discussion, a question, news, or opinion? None until llm_parser runs;
    # main.py drops anything the LLM marked False before Notion/Telegram/ics.
    is_valid_opportunity: "bool | None" = None

    @property
    def id(self) -> str:
        """Stable identity for dedup across daily runs.

        Based on source+url (or source+title if a source has no stable URL)
        rather than content, so the id doesn't change if wording is edited
        upstream after we've already seen it.
        """
        key = f"{self.source}:{self.url or self.title}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
