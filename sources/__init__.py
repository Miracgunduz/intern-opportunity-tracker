from .base import Opportunity
from .reddit_source import fetch_reddit_opportunities
from .devpost_source import fetch_devpost_opportunities
from .github_source import fetch_github_opportunities
from .hackernews_source import fetch_hackernews_opportunities

__all__ = [
    "Opportunity",
    "fetch_reddit_opportunities",
    "fetch_devpost_opportunities",
    "fetch_github_opportunities",
    "fetch_hackernews_opportunities",
]
