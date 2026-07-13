"""Google discovery via Serper.dev (2,500 free credits; skipped without a key).

Finds intent on surfaces we can't query directly (Quora, niche forums, blogs)
using site-scoped pain-phrase searches restricted to the last week.
"""

from __future__ import annotations

import hashlib

import requests

from ..config import Product
from .base import USER_AGENT

SITES = ["quora.com", "reddit.com"]


def discover_google(product: Product, lookback_hours: int, api_key: str | None) -> list[dict]:
    if not api_key:
        return []

    signals: list[dict] = []
    # Two highest-value phrases per product keeps us well inside free credits.
    for phrase in product.pain_phrases[:2]:
        for site in SITES:
            try:
                resp = requests.post(
                    "https://google.serper.dev/search",
                    headers={
                        "X-API-KEY": api_key,
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                    json={"q": f'site:{site} "{phrase}"', "tbs": "qdr:w", "num": 10},
                    timeout=20,
                )
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError):
                continue

            for item in data.get("organic", []):
                url = item.get("link", "")
                if not url:
                    continue
                signals.append({
                    "product_id": product.id,
                    "source": "google",
                    "source_id": "g:" + hashlib.sha1(url.encode()).hexdigest()[:16],
                    "url": url,
                    "title": item.get("title", ""),
                    "body": item.get("snippet", ""),
                    "author": None,
                    "posted_at": None,
                })
    return signals
