"""Lead-signal discovery sources."""

from .reddit import discover_reddit
from .hackernews import discover_hackernews
from .google import discover_google
from .rss import discover_rss

__all__ = ["discover_reddit", "discover_hackernews", "discover_google", "discover_rss"]
