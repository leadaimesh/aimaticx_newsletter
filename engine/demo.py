"""Seed the database with realistic sample data so the dashboard renders
before the first real run. Wiped automatically the first time real discovery
inserts data on top? No — demo data lives in the same DB; reset with:
    python run_engine.py demo --reset
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import now_iso

SAMPLE_POSTS = {
    "doc2translate": [
        ("Need to translate a 40-page PDF contract to Spanish, keep formatting?",
         "Agency quoted me $900 and 5 business days. Is there a tool that keeps the tables and layout intact?", "reddit"),
        ("Best way to translate Word docs for immigration paperwork?",
         "USCIS needs certified-style translations and I have 12 documents. Copy-pasting into Google Translate destroys the formatting.", "reddit"),
        ("Translating product spec sheets (PPTX) for EU distributors",
         "We ship spec decks to distributors in 6 languages. Doing this by hand every quarter is killing us.", "hackernews"),
    ],
    "yt2translate": [
        ("How do you all handle subtitles in other languages?",
         "My channel is 60% non-US viewers now. Auto-captions in English are fine but translated subs seem to really boost watch time. What's the workflow?", "reddit"),
        ("Dubbing my tutorials into Spanish and Portuguese — worth it?",
         "Saw MrBeast-style multi-language audio tracks are open to everyone now. Anyone have a cheap pipeline for this?", "reddit"),
        ("Show HN: I analyzed 1M YouTube channels — translated subs correlate with 2.3x sub growth",
         "Interesting discussion about localization workflows in the comments.", "hackernews"),
    ],
    "aimaticx": [
        ("What AI tools are actually worth it for a 3-person business?",
         "I run a small landscaping company. Everyone says 'use AI' but I don't have time to test 50 tools. Where do I even start?", "reddit"),
        ("How are you automating lead follow-up?",
         "I lose leads because I don't follow up fast enough. Looking for something simple that doesn't need a developer.", "reddit"),
        ("Ask HN: Solo founders — what did you automate first?",
         "Trying to decide between automating content, lead gen, or support first.", "hackernews"),
    ],
}

RATIONALES = [
    "Explicitly asking for a tool recommendation with a stated budget pain.",
    "Clear timeline pressure and active evaluation of alternatives.",
    "Strong ICP fit; pain stated concretely but no explicit ask yet.",
    "Recurring workflow pain with volume — high LTV potential.",
]


def seed_demo(conn: sqlite3.Connection, cfg: Config, days: int = 35) -> None:
    rng = random.Random(42)
    now = datetime.now(timezone.utc)

    for day_offset in range(days, 0, -1):
        day = now - timedelta(days=day_offset)
        for product in cfg.products:
            n_signals = rng.randint(3, 14)
            n_qualified = max(0, int(n_signals * rng.uniform(0.08, 0.22)))
            for i in range(n_signals):
                qualified = i < n_qualified
                score = rng.randint(62, 94) if qualified else rng.randint(8, 55)
                title, body, source = rng.choice(SAMPLE_POSTS[product.id]) if product.id in SAMPLE_POSTS \
                    else ("Sample signal", "Sample body", "reddit")
                channel = rng.choice(["reddit_reply", "hn_reply", "email"]) if qualified else "skip"
                posted = day - timedelta(hours=rng.randint(0, 20))
                cur = conn.execute(
                    """INSERT INTO signals (product_id, source, source_id, url, title, body,
                              author, posted_at, found_at, status, score, score_detail, channel,
                              matched_phrase)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        product.id, source,
                        f"demo:{product.id}:{day_offset}:{i}",
                        "https://www.reddit.com/r/smallbusiness/" if source == "reddit"
                        else "https://news.ycombinator.com/item?id=demo",
                        title, body, f"user_{rng.randint(100,999)}",
                        posted.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        day.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "qualified" if qualified else "scored",
                        score,
                        json.dumps({"rationale": rng.choice(RATIONALES)}),
                        channel,
                        rng.choice(product.pain_phrases) if product.pain_phrases else None,
                    ),
                )
                signal_id = cur.lastrowid

                if qualified and rng.random() < 0.8:
                    kind = "email" if channel == "email" else "reply"
                    sent = rng.random() < 0.55
                    conn.execute(
                        """INSERT INTO drafts (signal_id, kind, touch, to_email, subject, body,
                                  status, created_at, sent_at)
                           VALUES (?,?,1,?,?,?,?,?,?)""",
                        (
                            signal_id, kind,
                            "lead@example.com" if kind == "email" else None,
                            "Saw your post about document translation" if kind == "email" else None,
                            "Hey — saw your post about translating that contract while keeping "
                            "the layout. One tip that helps regardless of tool: export to DOCX "
                            "first, translation engines handle it far better than raw PDF. "
                            "I actually built Doc2Translate for exactly this — it keeps tables "
                            "and formatting intact. Happy to run your first doc free if you "
                            "want to sanity-check it.",
                            "sent" if sent else "pending",
                            day.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            day.strftime("%Y-%m-%dT%H:%M:%SZ") if sent else None,
                        ),
                    )
            # daily metrics incl. occasional replies
            conn.execute(
                """INSERT INTO metrics_daily (date, product_id, signals_found, qualified,
                          drafts_created, emails_sent, replies)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(date, product_id) DO NOTHING""",
                (
                    day.strftime("%Y-%m-%d"), product.id, n_signals, n_qualified,
                    n_qualified, max(0, n_qualified - 1),
                    1 if rng.random() < 0.18 else 0,
                ),
            )
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    for table in ("signals", "drafts", "suppression", "metrics_daily", "events"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
