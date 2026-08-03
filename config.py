"""Central configuration: search terms, keyword filters, thresholds.

Tune these lists over time — they're the whole "brain" of the filter and
are meant to be edited without touching the pipeline code.
"""
import os

# ---------------------------------------------------------------------------
# What to search for
# ---------------------------------------------------------------------------

# Used by every source to build its search queries.
SEARCH_TERMS = [
    "free certification developers",
    "scholarship program software engineers",
    "GitHub externship",
    "Microsoft internship program students",
    "AWS free certification challenge",
    "Google Cloud free certification challenge",
    "junior developer bootcamp free",
    "open source internship remote",
    "global hackathon students free",
]

REDDIT_SUBREDDITS = [
    "cscareerquestions",
    "csMajors",
    "developersIndia",
    "learnprogramming",
    "opensource",
]

# GitHub Search API: repo topics worth scanning for curated opportunity lists.
# Verified live against the Search API — each of these returns real, actively
# maintained repos (e.g. "internships" surfaces SimplifyJobs' well-known
# internship-listing repos). "student-programs" was tried and dropped: 0 hits.
GITHUB_SEARCH_TOPICS = [
    "internships",
    "hackathons",
    "free-certifications",
    "certifications",
    "open-source-programs",
]

# ---------------------------------------------------------------------------
# Filtering — lightweight keyword scoring, not a full NLP model on purpose
# (the whole system has to run comfortably inside a free GitHub Actions
# minute budget, so we avoid heavy ML dependencies).
# ---------------------------------------------------------------------------

# Each hit adds its weight to the opportunity's score.
POSITIVE_KEYWORDS: dict[str, float] = {
    # Cost / access
    "free": 2.0, "no cost": 2.0, "fully funded": 2.0, "scholarship": 2.0,
    "no fee": 1.5, "zero cost": 1.5,
    # Remote / global reach (important: user is Turkey-based)
    "remote": 1.5, "virtual": 1.5, "online": 1.0, "global": 1.5,
    "worldwide": 1.5, "international": 1.0, "anywhere": 1.0,
    # Seniority fit
    "student": 1.5, "junior": 1.5, "beginner": 1.0, "entry level": 1.5,
    "entry-level": 1.5, "no experience required": 1.5, "early career": 1.5,
    "undergraduate": 1.0, "new grad": 1.0,
    # Opportunity type
    "certification": 1.0, "certificate": 1.0, "bootcamp": 1.0,
    "internship": 1.0, "externship": 1.5, "hackathon": 1.0, "challenge": 0.5,
    "fellowship": 1.0, "mentorship": 0.5,
    # CV-value brands (kept short and specific on purpose — this list is
    # a bonus signal, not a gate; unlisted orgs can still qualify on other
    # keywords).
    "microsoft": 1.5, "github": 1.0, "aws": 1.5, "amazon web services": 1.5,
    "google cloud": 1.5, "meta": 1.0, "linkedin learning": 0.5,
}

# Any of these push the score down hard — they're the "this doesn't fit
# the user's constraints" signals (paid, US-only physical, senior-only).
NEGATIVE_KEYWORDS: dict[str, float] = {
    "tuition required": 3.0, "tuition fee": 3.0, "paid program only": 2.0,
    "registration fee": 2.0, "$": 0.5,  # cheap heuristic for "costs money"
    "in-person only": 2.5, "onsite only": 2.5, "on-site only": 2.5,
    "us citizens only": 3.0, "u.s. citizens only": 3.0,
    "united states only": 2.5, "us only": 2.0,
    "visa sponsorship required": 1.0,
    "senior": 1.5, "5+ years": 2.0, "10+ years": 2.0,
    "phd required": 2.0, "must relocate": 1.5,
}

# Minimum net score (sum of positive hits minus negative hits) required to
# keep an opportunity. Tuned empirically — raise it if you get too much
# noise, lower it if good opportunities are getting filtered out.
MIN_SCORE = 3.0

# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

# Phrases that usually precede a real deadline/start date in the wild —
# used to prioritize which date-like substring in a wall of text is the
# one that actually matters.
DEADLINE_TRIGGER_WORDS = [
    "deadline", "apply by", "applications close", "closes on", "due by",
    "due date", "last date", "son başvuru", "son tarih",
]
START_TRIGGER_WORDS = [
    "starts on", "starting", "begins on", "kicks off", "program starts",
    "start date",
]

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "seen.json")
ICS_FILE = os.path.join(os.path.dirname(__file__), "data", "opportunities.ics")

# How many days to keep a seen-item hash before it can be pruned from state
# (keeps data/seen.json from growing forever).
SEEN_RETENTION_DAYS = 180
