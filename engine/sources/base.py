"""Shared helpers for discovery sources."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

# Unique, descriptive UA — Reddit blocks generic ones near-instantly.
USER_AGENT = "aimaticx-sales-engine/1.0 (lead research; contact: owner via repo)"


def http_get(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: int = 20) -> dict | None:
    """GET returning parsed JSON, or None on any failure (sources are best-effort)."""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    try:
        resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        if resp.status_code == 429:
            time.sleep(10)
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def iso_from_ts(ts: float | int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def matches_negative(text: str, negative_keywords: list[str]) -> bool:
    low = text.lower()
    return any(nk.lower() in low for nk in negative_keywords)
