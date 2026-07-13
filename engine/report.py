"""Reporting: dashboard/data.json for the UI + daily owner digest."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from .ai import AI
from .config import Config, DASHBOARD_DIR, DATA_DIR
from .db import log_event, now_iso, today
from .sender import send_owner_email

DASHBOARD_JSON = DASHBOARD_DIR / "data.json"
DIGEST_MD = DATA_DIR / "digest.md"

# Honest funnel targets from 2026 outbound benchmarks (see docs/PLAYBOOK.md).
BENCHMARKS = {"reply_rate_good": 0.05, "reply_to_trial_good": 0.25}


def build_dashboard_data(conn: sqlite3.Connection, cfg: Config, demo: bool = False) -> dict:
    days = 90
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    daily: dict[tuple[str, str], dict] = {}

    def bucket(date: str, product_id: str) -> dict:
        return daily.setdefault((date, product_id), {
            "date": date, "product_id": product_id,
            "signals": 0, "qualified": 0, "drafted": 0, "contacted": 0, "replies": 0,
        })

    for row in conn.execute(
        """SELECT substr(found_at,1,10) AS d, product_id,
                  COUNT(*) AS n,
                  SUM(CASE WHEN status='qualified' THEN 1 ELSE 0 END) AS q
           FROM signals WHERE found_at >= ? GROUP BY d, product_id""",
        (since,),
    ):
        b = bucket(row["d"], row["product_id"])
        b["signals"] = row["n"]
        b["qualified"] = row["q"]

    for row in conn.execute(
        """SELECT substr(d.created_at,1,10) AS day, s.product_id, COUNT(*) AS n
           FROM drafts d JOIN signals s ON s.id=d.signal_id
           WHERE d.created_at >= ? AND d.touch = 1 GROUP BY day, s.product_id""",
        (since,),
    ):
        bucket(row["day"], row["product_id"])["drafted"] = row["n"]

    for row in conn.execute(
        """SELECT substr(d.sent_at,1,10) AS day, s.product_id, COUNT(*) AS n
           FROM drafts d JOIN signals s ON s.id=d.signal_id
           WHERE d.status IN ('sent','posted') AND d.sent_at >= ? GROUP BY day, s.product_id""",
        (since,),
    ):
        bucket(row["day"], row["product_id"])["contacted"] = row["n"]

    for row in conn.execute(
        "SELECT date, product_id, replies FROM metrics_daily WHERE date >= ? AND replies > 0",
        (since,),
    ):
        bucket(row["date"], row["product_id"])["replies"] = row["replies"]

    top_leads = [
        {
            "id": row["id"],
            "product_id": row["product_id"],
            "source": row["source"],
            "title": row["title"] or (row["body"] or "")[:120],
            "url": row["url"],
            "score": row["score"],
            "channel": row["channel"],
            "posted_at": row["posted_at"],
            "rationale": _rationale(row["score_detail"]),
            "draft_status": row["draft_status"],
        }
        for row in conn.execute(
            """SELECT s.*, (SELECT d.status FROM drafts d WHERE d.signal_id=s.id
                            ORDER BY d.touch DESC LIMIT 1) AS draft_status
               FROM signals s WHERE s.status='qualified'
               ORDER BY s.score DESC, s.found_at DESC LIMIT 50"""
        )
    ]

    pending_drafts = [
        {
            "id": row["id"],
            "signal_id": row["signal_id"],
            "product_id": row["product_id"],
            "kind": row["kind"],
            "touch": row["touch"],
            "subject": row["subject"],
            "body": row["body"],
            "url": row["url"],
            "source": row["source"],
            "score": row["score"],
            "created_at": row["created_at"],
        }
        for row in conn.execute(
            """SELECT d.*, s.product_id, s.url, s.source, s.score
               FROM drafts d JOIN signals s ON s.id=d.signal_id
               WHERE d.status='pending' ORDER BY s.score DESC, d.created_at DESC LIMIT 40"""
        )
    ]

    by_source = [
        dict(row)
        for row in conn.execute(
            """SELECT source, COUNT(*) AS signals,
                      SUM(CASE WHEN status='qualified' THEN 1 ELSE 0 END) AS qualified
               FROM signals WHERE found_at >= ? GROUP BY source ORDER BY signals DESC""",
            (since,),
        )
    ]

    # Which search phrases actually find buyers — the main tuning signal.
    by_phrase = [
        dict(row)
        for row in conn.execute(
            """SELECT matched_phrase AS phrase, product_id, COUNT(*) AS signals,
                      SUM(CASE WHEN status='qualified' THEN 1 ELSE 0 END) AS qualified
               FROM signals WHERE found_at >= ? AND matched_phrase IS NOT NULL
               GROUP BY matched_phrase, product_id
               ORDER BY qualified DESC, signals DESC LIMIT 12""",
            (since,),
        )
    ]

    return {
        "generated_at": now_iso(),
        "demo": demo,
        "window_days": days,
        "products": [{"id": p.id, "name": p.name} for p in cfg.products],
        "daily": sorted(daily.values(), key=lambda r: r["date"]),
        "top_leads": top_leads,
        "pending_drafts": pending_drafts,
        "by_source": by_source,
        "by_phrase": by_phrase,
        "benchmarks": BENCHMARKS,
        "autopilot": {
            "send_enabled": cfg.send_enabled,
            "email_configured": cfg.email_configured,
            "daily_email_cap": cfg.daily_email_cap,
        },
        # Set automatically when run inside GitHub Actions — lets the
        # dashboard deep-link to the phone-friendly Engine Action workflow.
        "engine_action_url": (
            f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/workflows/engine-action.yml"
            if os.environ.get("GITHUB_REPOSITORY") else None
        ),
    }


def write_dashboard(conn: sqlite3.Connection, cfg: Config, demo: bool = False) -> dict:
    data = build_dashboard_data(conn, cfg, demo=demo)
    DASHBOARD_JSON.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_JSON.write_text(json.dumps(data, indent=1), encoding="utf-8")
    log_event(conn, "dashboard_written", str(DASHBOARD_JSON))
    return data


def send_digest(conn: sqlite3.Connection, cfg: Config, ai: AI, data: dict) -> None:
    if not cfg.settings.get("digest", {}).get("enabled", True):
        return
    # The engine runs every few hours; the digest goes out once per day, on
    # the first run at/after the configured hour (overnight runs stay quiet).
    send_after = cfg.settings.get("digest", {}).get("send_after_utc_hour", 13)
    if datetime.now(timezone.utc).hour < send_after:
        return
    already = conn.execute(
        """SELECT 1 FROM events
           WHERE kind IN ('digest_emailed','digest_written') AND at LIKE ?""",
        (today() + "%",),
    ).fetchone()
    if already:
        return
    top_n = cfg.settings.get("digest", {}).get("top_leads", 10)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_rows = [d for d in data["daily"] if d["date"] == day]

    summary = {
        "date": day,
        "today": {
            "signals_found": sum(d["signals"] for d in today_rows),
            "qualified_leads": sum(d["qualified"] for d in today_rows),
            "drafts_created": sum(d["drafted"] for d in today_rows),
            "emails_sent": sum(d["contacted"] for d in today_rows),
        },
        "pending_drafts_awaiting_approval": len(data["pending_drafts"]),
        "top_leads": data["top_leads"][:top_n],
        "best_search_phrases_90d": data.get("by_phrase", [])[:5],
        "autopilot": data["autopilot"],
    }

    try:
        digest = ai.write_digest(json.dumps(summary, indent=1))
    except Exception as exc:
        digest = f"(Digest generation failed: {exc})\n\n{json.dumps(summary, indent=1)}"

    DIGEST_MD.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_MD.write_text(f"# Sales engine digest — {day}\n\n{digest}\n", encoding="utf-8")

    if send_owner_email(cfg, f"Sales engine digest — {day}", digest):
        log_event(conn, "digest_emailed", cfg.owner_email or "")
    else:
        log_event(conn, "digest_written", str(DIGEST_MD))


def _rationale(score_detail: str | None) -> str:
    if not score_detail:
        return ""
    try:
        return json.loads(score_detail).get("rationale", "")
    except (ValueError, AttributeError):
        return ""
