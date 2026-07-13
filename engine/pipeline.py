"""Daily pipeline orchestrator: discover → score → draft → send → report."""

from __future__ import annotations

import sqlite3

from .ai import AI
from .config import Config
from .db import bump_metric, insert_signal, log_event
from .outreach import create_drafts
from .report import send_digest, write_dashboard
from .scoring import score_new_signals
from .sender import process_email_queue
from .sources import discover_google, discover_hackernews, discover_reddit, discover_rss


def run_discovery(conn: sqlite3.Connection, cfg: Config) -> dict:
    d = cfg.settings.get("discovery", {})
    lookback = d.get("lookback_hours", 26)
    per_product_cap = d.get("max_signals_per_product_per_day", 40)
    enabled = d.get("sources", {})
    stats = {"found": 0, "new": 0}

    for product in cfg.products:
        found: list[dict] = []
        if enabled.get("reddit", True):
            found += discover_reddit(product, lookback)
        if enabled.get("hackernews", True):
            found += discover_hackernews(product, lookback)
        if enabled.get("google", True):
            found += discover_google(product, lookback, cfg.serper_api_key)
        if enabled.get("rss", True) and d.get("rss_feeds"):
            found += discover_rss(product, d["rss_feeds"])

        stats["found"] += len(found)
        new_count = 0
        for sig in found:
            if new_count >= per_product_cap:
                break
            if insert_signal(conn, sig):
                new_count += 1
        stats["new"] += new_count
        if new_count:
            bump_metric(conn, product.id, "signals_found", new_count)

    log_event(conn, "discovery_done", str(stats))
    return stats


def run_daily(conn: sqlite3.Connection, cfg: Config) -> dict:
    """The full daily autopilot run. Each stage is independent — a failure in
    one stage never blocks the report at the end."""
    _purge_demo_data(conn)
    ai = AI(cfg)
    results: dict = {}

    results["discovery"] = _safe(lambda: run_discovery(conn, cfg), conn, "discovery")
    results["scoring"] = _safe(lambda: score_new_signals(conn, cfg, ai), conn, "scoring")
    results["outreach"] = _safe(lambda: create_drafts(conn, cfg, ai), conn, "outreach")
    results["email"] = _safe(lambda: process_email_queue(conn, cfg, ai), conn, "email")

    data = write_dashboard(conn, cfg)
    _safe(lambda: send_digest(conn, cfg, ai, data), conn, "digest")
    return results


def _purge_demo_data(conn: sqlite3.Connection) -> None:
    """The first real run wipes seeded sample data so real metrics start clean."""
    has_demo = conn.execute(
        "SELECT 1 FROM signals WHERE source_id LIKE 'demo:%' LIMIT 1"
    ).fetchone()
    if not has_demo:
        return
    for table in ("drafts", "signals", "metrics_daily", "events"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    log_event(conn, "demo_purged", "sample data removed before first real run")


def _safe(fn, conn: sqlite3.Connection, stage: str):
    try:
        return fn()
    except Exception as exc:
        log_event(conn, f"{stage}_failed", repr(exc))
        return {"error": repr(exc)}
