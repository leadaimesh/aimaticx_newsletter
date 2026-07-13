"""Score new signals with the Claude rubric and mark qualified leads."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .ai import AI
from .config import Config
from .db import bump_metric, log_event


def score_new_signals(conn: sqlite3.Connection, cfg: Config, ai: AI) -> dict:
    threshold = cfg.settings.get("scoring", {}).get("qualify_threshold", 60)
    batch_size = cfg.settings.get("scoring", {}).get("batch_size", 10)
    max_age_days = cfg.settings.get("scoring", {}).get("max_signal_age_days", 7)
    stale_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max_age_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    stats = {"scored": 0, "qualified": 0, "skipped_stale": 0}

    for product in cfg.products:
        # Age out stale signals so we never pay to score dead threads.
        cur = conn.execute(
            """UPDATE signals SET status='skipped', channel='skip', score=0
               WHERE status='new' AND product_id=? AND posted_at IS NOT NULL AND posted_at < ?""",
            (product.id, stale_cutoff),
        )
        stats["skipped_stale"] += cur.rowcount
        conn.commit()

        rows = conn.execute(
            "SELECT * FROM signals WHERE status='new' AND product_id=? ORDER BY posted_at DESC",
            (product.id,),
        ).fetchall()
        if not rows:
            continue

        for start in range(0, len(rows), batch_size):
            batch = [dict(r) for r in rows[start:start + batch_size]]
            try:
                results = ai.score_signals(product, batch)
            except Exception as exc:  # API errors shouldn't kill the pipeline
                log_event(conn, "score_error", f"{product.id}: {exc}")
                continue

            for res in results:
                idx = res["signal_index"]
                if idx < 0 or idx >= len(batch):
                    continue
                sig = batch[idx]
                qualified = res["score"] >= threshold and res["channel"] != "skip"
                conn.execute(
                    "UPDATE signals SET status=?, score=?, score_detail=?, channel=? WHERE id=?",
                    (
                        "qualified" if qualified else "scored",
                        res["score"], res["detail"], res["channel"], sig["id"],
                    ),
                )
                stats["scored"] += 1
                if qualified:
                    stats["qualified"] += 1
                    bump_metric(conn, product.id, "qualified")
            conn.commit()

    log_event(conn, "scoring_done", str(stats))
    return stats
