"""Reddit discovery via public .json endpoints.

Unauthenticated .json is throttled (~10 req/min) — we sleep between requests
and search each product's subreddits as one multireddit query per pain phrase.
Discovery only; the engine never posts to Reddit (drafts are approved and
posted by a human, which is what keeps accounts alive).
"""

from __future__ import annotations

import time

from ..config import Product
from .base import http_get, iso_from_ts, matches_negative

REQUEST_GAP_SECONDS = 7


def discover_reddit(product: Product, lookback_hours: int) -> list[dict]:
    signals: list[dict] = []
    if not product.subreddits:
        return signals

    multireddit = "+".join(product.subreddits)
    cutoff = time.time() - lookback_hours * 3600

    for phrase in product.pain_phrases:
        data = http_get(
            f"https://www.reddit.com/r/{multireddit}/search.json",
            params={
                "q": phrase,
                "restrict_sr": "on",
                "sort": "new",
                "t": "week",
                "limit": 25,
            },
        )
        time.sleep(REQUEST_GAP_SECONDS)
        if not data:
            continue

        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            created = post.get("created_utc") or 0
            if created < cutoff:
                continue
            text = f"{post.get('title', '')} {post.get('selftext', '')}"
            if matches_negative(text, product.negative_keywords):
                continue
            signals.append({
                "product_id": product.id,
                "source": "reddit",
                "source_id": f"reddit:{post.get('id')}",
                "url": f"https://www.reddit.com{post.get('permalink', '')}",
                "title": post.get("title", ""),
                "body": (post.get("selftext") or "")[:4000],
                "author": post.get("author"),
                "posted_at": iso_from_ts(created),
                "matched_phrase": phrase,
            })
    return signals
