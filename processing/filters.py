"""Lightweight keyword-based scoring & filtering.

Deliberately not a full NLP/ML classifier: a free GitHub Actions runner
needs to install its dependencies and finish well within its per-job time
budget, so a heavy model (spaCy, transformers, ...) is the wrong trade
here. Plain keyword scoring gets most of the value for a tiny fraction of
the complexity and install time — see config.py for the tunable lists.
"""
from __future__ import annotations

from config import MIN_SCORE, NEGATIVE_KEYWORDS, POSITIVE_KEYWORDS
from sources.base import Opportunity


def score_opportunity(opportunity: Opportunity) -> float:
    """Sum of positive keyword weights minus negative keyword weights,
    scanned over the title + raw text (case-insensitive substring match)."""
    text = f"{opportunity.title}\n{opportunity.raw_text}".lower()
    score = 0.0
    for keyword, weight in POSITIVE_KEYWORDS.items():
        if keyword in text:
            score += weight
    for keyword, weight in NEGATIVE_KEYWORDS.items():
        if keyword in text:
            score -= weight
    return score


def filter_opportunities(opportunities: list[Opportunity]) -> list[Opportunity]:
    """Scores every opportunity (mutates .score in place), keeps only ones
    at/above MIN_SCORE, and returns them sorted best-first."""
    for opp in opportunities:
        opp.score = score_opportunity(opp)

    kept = [o for o in opportunities if o.score >= MIN_SCORE]
    kept.sort(key=lambda o: o.score, reverse=True)
    return kept
