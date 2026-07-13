"""Email sending via Resend, with the safety rails that keep deliverability alive:

- Nothing sends unless SEND_ENABLED=true AND Resend is configured.
- Hard daily cap (DAILY_EMAIL_CAP), send-window hours, suppression check.
- 3-touch sequence max; follow-ups are drafted lazily when due (day 0/3/10-style
  spacing from settings.yaml) and stop instantly for suppressed contacts.
- Every send is logged; a bounce/complaint should be added to suppression via
  `python run_engine.py suppress <email> --reason bounced`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import requests

from .ai import AI
from .config import Config
from .db import bump_metric, emails_sent_today, is_suppressed, log_event, now_iso


def process_email_queue(conn: sqlite3.Connection, cfg: Config, ai: AI) -> dict:
    stats = {"sent": 0, "followups_drafted": 0, "blocked": 0}

    _draft_due_followups(conn, cfg, ai, stats)

    if not (cfg.send_enabled and cfg.email_configured):
        log_event(conn, "send_skipped", "autopilot off or email not configured")
        return stats

    window = cfg.settings.get("email", {}).get("send_window_utc", [13, 21])
    hour = datetime.now(timezone.utc).hour
    if not (window[0] <= hour < window[1]):
        log_event(conn, "send_skipped", f"outside send window (hour={hour} UTC)")
        return stats

    rows = conn.execute(
        """SELECT d.*, s.author, s.product_id FROM drafts d
           JOIN signals s ON s.id = d.signal_id
           WHERE d.kind='email' AND d.status='pending' AND d.to_email IS NOT NULL
             AND (d.scheduled_for IS NULL OR d.scheduled_for <= ?)
           ORDER BY d.created_at""",
        (now_iso(),),
    ).fetchall()

    for row in rows:
        if emails_sent_today(conn) >= cfg.daily_email_cap:
            log_event(conn, "send_capped", f"daily cap {cfg.daily_email_cap} reached")
            break
        draft = dict(row)
        if is_suppressed(conn, draft["to_email"]) or is_suppressed(conn, draft.get("author")):
            conn.execute("UPDATE drafts SET status='dismissed' WHERE id=?", (draft["id"],))
            conn.commit()
            stats["blocked"] += 1
            continue

        if _send_via_resend(cfg, draft["to_email"], draft["subject"] or "Quick note", draft["body"]):
            conn.execute(
                "UPDATE drafts SET status='sent', sent_at=? WHERE id=?",
                (now_iso(), draft["id"]),
            )
            conn.commit()
            stats["sent"] += 1
            bump_metric(conn, draft["product_id"], "emails_sent")
        else:
            log_event(conn, "send_error", f"draft {draft['id']} to {draft['to_email']}")

    return stats


def _draft_due_followups(conn: sqlite3.Connection, cfg: Config, ai: AI, stats: dict) -> None:
    followup_days = cfg.settings.get("outreach", {}).get("followup_days", [3, 7])
    max_touches = cfg.settings.get("outreach", {}).get("max_touches", 3)
    footer = cfg.settings.get("email", {}).get("unsubscribe_footer", "").strip()

    sent = conn.execute(
        """SELECT d.*, s.product_id, s.title, s.body AS signal_body, s.source, s.author
           FROM drafts d JOIN signals s ON s.id = d.signal_id
           WHERE d.kind='email' AND d.status='sent' AND d.touch < ?""",
        (max_touches,),
    ).fetchall()

    for row in sent:
        prev = dict(row)
        next_touch = prev["touch"] + 1
        exists = conn.execute(
            "SELECT 1 FROM drafts WHERE signal_id=? AND touch=?",
            (prev["signal_id"], next_touch),
        ).fetchone()
        if exists:
            continue

        wait_days = followup_days[min(prev["touch"] - 1, len(followup_days) - 1)]
        due_at = datetime.strptime(prev["sent_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ) + timedelta(days=wait_days)
        if datetime.now(timezone.utc) < due_at:
            continue
        if is_suppressed(conn, prev["to_email"]):
            continue

        product = cfg.product(prev["product_id"])
        if product is None:
            continue
        signal = {"title": prev["title"], "body": prev["signal_body"], "source": prev["source"]}
        try:
            fu = ai.draft_followup(product, signal, prev["body"], next_touch, footer)
        except Exception as exc:
            log_event(conn, "followup_error", f"draft {prev['id']}: {exc}")
            continue

        conn.execute(
            """INSERT INTO drafts (signal_id, kind, touch, to_email, subject, body,
                                   status, created_at, scheduled_for)
               VALUES (?, 'email', ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                prev["signal_id"], next_touch, prev["to_email"],
                f"Re: {prev['subject']}" if prev["subject"] else None,
                fu.reply_text, now_iso(), now_iso(),
            ),
        )
        conn.commit()
        stats["followups_drafted"] += 1


def _send_via_resend(cfg: Config, to_email: str, subject: str, body: str) -> bool:
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {cfg.resend_api_key}"},
            json={"from": cfg.from_email, "to": [to_email], "subject": subject, "text": body},
            timeout=20,
        )
        return resp.status_code in (200, 201)
    except requests.RequestException:
        return False


def send_owner_email(cfg: Config, subject: str, body: str) -> bool:
    """Digest to the owner — allowed even when outreach autopilot is off."""
    if not (cfg.email_configured and cfg.owner_email):
        return False
    return _send_via_resend(cfg, cfg.owner_email, subject, body)
