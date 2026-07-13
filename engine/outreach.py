"""Generate grounded outreach drafts for qualified leads.

Forum replies are ALWAYS draft-only — a human approves and posts them (this is
what keeps Reddit/HN accounts alive and is the single biggest lesson from the
2025 AI-SDR failures). Emails are drafted here and sent by sender.py only when
autopilot is enabled.
"""

from __future__ import annotations

import sqlite3

from .ai import AI
from .config import Config
from .db import bump_metric, is_suppressed, log_event, now_iso


def create_drafts(conn: sqlite3.Connection, cfg: Config, ai: AI) -> dict:
    max_drafts = cfg.settings.get("outreach", {}).get("max_drafts_per_day", 25)
    footer = cfg.settings.get("email", {}).get("unsubscribe_footer", "").strip()

    stats = {"drafts": 0, "suppressed": 0}

    rows = conn.execute(
        """SELECT s.* FROM signals s
           WHERE s.status='qualified'
             AND s.channel != 'skip'
             AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.signal_id = s.id)
           ORDER BY s.score DESC, s.posted_at DESC
           LIMIT ?""",
        (max_drafts,),
    ).fetchall()

    for row in rows:
        sig = dict(row)
        product = cfg.product(sig["product_id"])
        if product is None:
            continue
        if is_suppressed(conn, sig.get("author")):
            stats["suppressed"] += 1
            continue

        kind = "email" if sig["channel"] == "email" else "reply"
        try:
            draft = ai.draft_outreach(product, sig, kind, unsubscribe_footer=footer)
        except Exception as exc:
            log_event(conn, "draft_error", f"signal {sig['id']}: {exc}")
            continue

        conn.execute(
            """INSERT INTO drafts (signal_id, kind, touch, subject, body, status, created_at)
               VALUES (?, ?, 1, ?, ?, 'pending', ?)""",
            (sig["id"], kind, draft.subject or None, draft.reply_text, now_iso()),
        )
        conn.commit()
        stats["drafts"] += 1
        bump_metric(conn, product.id, "drafts_created")

    log_event(conn, "outreach_done", str(stats))
    return stats
