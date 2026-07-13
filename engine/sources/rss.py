"""RSS discovery: scan configured feeds for product keywords (stdlib only)."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

import requests

from ..config import Product
from .base import USER_AGENT, matches_negative


def discover_rss(product: Product, feeds: list[str]) -> list[dict]:
    signals: list[dict] = []
    keywords = [k.lower() for k in product.keywords + product.pain_phrases]

    for feed_url in feeds:
        try:
            resp = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=20)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError):
            continue

        # RSS 2.0 <item> and Atom <entry>
        items = root.findall(".//item") + root.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )
        for item in items:
            title = _text(item, "title") or _text(item, "{http://www.w3.org/2005/Atom}title") or ""
            desc = _text(item, "description") or _text(item, "{http://www.w3.org/2005/Atom}summary") or ""
            link = _text(item, "link") or ""
            if not link:
                atom_link = item.find("{http://www.w3.org/2005/Atom}link")
                link = atom_link.get("href", "") if atom_link is not None else ""

            text = f"{title} {desc}".lower()
            matched = next((k for k in keywords if k in text), None)
            if matched is None:
                continue
            if matches_negative(text, product.negative_keywords):
                continue
            signals.append({
                "product_id": product.id,
                "source": "rss",
                "source_id": "rss:" + hashlib.sha1((link or title).encode()).hexdigest()[:16],
                "url": link,
                "title": title,
                "body": desc[:4000],
                "author": None,
                "posted_at": None,
                "matched_phrase": matched,
            })
    return signals


def _text(el, tag: str) -> str | None:
    child = el.find(tag)
    return child.text if child is not None and child.text else None
