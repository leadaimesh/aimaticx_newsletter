"""Hacker News discovery via the Algolia API (no key needed, generous limits)."""

from __future__ import annotations

import time

from ..config import Product
from .base import http_get, iso_from_ts, matches_negative


def discover_hackernews(product: Product, lookback_hours: int) -> list[dict]:
    signals: list[dict] = []
    cutoff = int(time.time() - lookback_hours * 3600)

    # Keywords cast a wider net than full pain phrases (HN search is literal).
    queries = product.keywords + product.pain_phrases[:2]
    for query in queries:
        data = http_get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": query,
                "tags": "(story,comment)",
                "numericFilters": f"created_at_i>{cutoff}",
                "hitsPerPage": 25,
            },
        )
        if not data:
            continue

        for hit in data.get("hits", []):
            body = hit.get("comment_text") or hit.get("story_text") or ""
            title = hit.get("title") or hit.get("story_title") or ""
            if matches_negative(f"{title} {body}", product.negative_keywords):
                continue
            object_id = hit.get("objectID")
            signals.append({
                "product_id": product.id,
                "source": "hackernews",
                "source_id": f"hn:{object_id}",
                "url": f"https://news.ycombinator.com/item?id={object_id}",
                "title": title,
                "body": body[:4000],
                "author": hit.get("author"),
                "posted_at": iso_from_ts(hit.get("created_at_i")),
            })
    return signals
