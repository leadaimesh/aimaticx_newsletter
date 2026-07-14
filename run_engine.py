#!/usr/bin/env python3
"""AiMaticX Sales Engine — command line.

Daily autopilot (what GitHub Actions runs):
    python run_engine.py daily

Individual stages:
    python run_engine.py discover | score | outreach | send | report

Working the queue:
    python run_engine.py queue                      # list pending drafts
    python run_engine.py show <draft_id>            # full draft text
    python run_engine.py approve <draft_id> [--email who@example.com]
    python run_engine.py posted <draft_id>          # you posted a reply yourself
    python run_engine.py dismiss <draft_id>
    python run_engine.py replied <draft_id>         # lead replied — stops follow-ups
    python run_engine.py suppress <email-or-handle> [--reason unsubscribed]

Setup check & local dashboard:
    python run_engine.py doctor                     # is everything configured?
    python run_engine.py demo [--reset]             # seed sample data
    python run_engine.py serve                      # http://localhost:8422
"""

from __future__ import annotations

import argparse
import json
import sys

from engine.ai import AI
from engine.config import DASHBOARD_DIR, load_config
from engine.db import bump_metric, connect, now_iso, suppress
from engine.demo import reset_db, seed_demo
from engine.outreach import create_drafts
from engine.pipeline import run_daily, run_discovery
from engine.report import write_dashboard
from engine.scoring import score_new_signals
from engine.sender import process_email_queue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command")
    parser.add_argument("arg", nargs="?")
    parser.add_argument("--email")
    parser.add_argument("--reason", default="manual")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    conn = connect()

    if args.command == "daily":
        _require_api_key(cfg)
        print(json.dumps(run_daily(conn, cfg), indent=2))

    elif args.command == "discover":
        print(json.dumps(run_discovery(conn, cfg), indent=2))

    elif args.command == "score":
        _require_api_key(cfg)
        print(json.dumps(score_new_signals(conn, cfg, AI(cfg)), indent=2))

    elif args.command == "outreach":
        _require_api_key(cfg)
        print(json.dumps(create_drafts(conn, cfg, AI(cfg)), indent=2))

    elif args.command == "send":
        _require_api_key(cfg)
        print(json.dumps(process_email_queue(conn, cfg, AI(cfg)), indent=2))

    elif args.command == "report":
        write_dashboard(conn, cfg)
        print(f"Wrote {DASHBOARD_DIR / 'data.json'}")

    elif args.command == "queue":
        rows = conn.execute(
            """SELECT d.id, d.kind, d.touch, s.product_id, s.score, s.url
               FROM drafts d JOIN signals s ON s.id=d.signal_id
               WHERE d.status='pending' ORDER BY s.score DESC"""
        ).fetchall()
        for r in rows:
            print(f"#{r['id']:>4}  {r['kind']:<5} t{r['touch']}  score {r['score']:>3}  "
                  f"{r['product_id']:<14} {r['url']}")
        print(f"{len(rows)} pending draft(s)")

    elif args.command == "show":
        r = conn.execute(
            """SELECT d.*, s.url, s.title FROM drafts d
               JOIN signals s ON s.id=d.signal_id WHERE d.id=?""",
            (_draft_id(args),),
        ).fetchone()
        if not r:
            print("No such draft"); return 1
        print(f"Draft #{r['id']} ({r['kind']}, touch {r['touch']}, status {r['status']})")
        print(f"Lead: {r['title']}\n{r['url']}\n")
        if r["subject"]:
            print(f"Subject: {r['subject']}")
        print(r["body"])

    elif args.command == "approve":
        draft_id = _draft_id(args)
        if args.email:
            conn.execute("UPDATE drafts SET to_email=? WHERE id=?", (args.email, draft_id))
            conn.commit()
            print(f"Draft #{draft_id}: email set to {args.email}. "
                  "It sends on the next run when SEND_ENABLED=true, "
                  "or use `show` to copy and send it yourself.")
        else:
            print("For email drafts pass --email. Reply drafts: use `show` to copy "
                  "the text, post it, then run `posted <id>`.")

    elif args.command == "posted":
        draft_id = _draft_id(args)
        conn.execute("UPDATE drafts SET status='posted', sent_at=? WHERE id=?",
                     (now_iso(), draft_id))
        conn.commit()
        row = conn.execute(
            "SELECT s.product_id FROM drafts d JOIN signals s ON s.id=d.signal_id WHERE d.id=?",
            (draft_id,),
        ).fetchone()
        if row:
            bump_metric(conn, row["product_id"], "emails_sent")
        print(f"Draft #{draft_id} marked posted.")

    elif args.command == "dismiss":
        conn.execute("UPDATE drafts SET status='dismissed' WHERE id=?", (_draft_id(args),))
        conn.commit()
        print("Dismissed.")

    elif args.command == "replied":
        draft_id = _draft_id(args)
        row = conn.execute(
            "SELECT d.signal_id, s.product_id FROM drafts d JOIN signals s ON s.id=d.signal_id WHERE d.id=?",
            (draft_id,),
        ).fetchone()
        if not row:
            print("No such draft"); return 1
        conn.execute(
            "UPDATE drafts SET status='dismissed' WHERE signal_id=? AND status='pending'",
            (row["signal_id"],),
        )
        conn.commit()
        bump_metric(conn, row["product_id"], "replies")
        print("Reply recorded — pending follow-ups for this lead cancelled.")

    elif args.command == "suppress":
        if not args.arg:
            print("Usage: suppress <email-or-handle> [--reason unsubscribed]"); return 1
        suppress(conn, args.arg, args.reason)
        print(f"{args.arg} suppressed ({args.reason}).")

    elif args.command == "doctor":
        return _doctor(cfg)

    elif args.command == "demo":
        if args.reset:
            reset_db(conn)
        seed_demo(conn, cfg)
        write_dashboard(conn, cfg, demo=True)
        print("Demo data seeded and dashboard written. Run: python run_engine.py serve")

    elif args.command == "serve":
        import http.server
        import functools
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(DASHBOARD_DIR)
        )
        print("Dashboard at http://localhost:8422 (Ctrl+C to stop)")
        http.server.HTTPServer(("", 8422), handler).serve_forever()

    else:
        parser.print_help()
        return 1
    return 0


def _doctor(cfg) -> int:
    """Pre-flight check: is the engine ready for a real run?"""
    from pathlib import Path
    problems = 0

    def check(ok: bool, label: str, hint: str = "", warn: bool = False) -> None:
        nonlocal problems
        mark = "PASS" if ok else ("WARN" if warn else "FAIL")
        if not ok and not warn:
            problems += 1
        print(f"[{mark}] {label}" + (f" — {hint}" if not ok and hint else ""))

    check(bool(cfg.anthropic_api_key), "ANTHROPIC_API_KEY set",
          "required: scoring and outreach can't run without it")
    check(bool(cfg.serper_api_key), "SERPER_API_KEY set",
          "optional: adds Google/Quora discovery (serper.dev)", warn=True)
    check(cfg.email_configured, "Resend configured (RESEND_API_KEY + FROM_EMAIL)",
          "optional: enables the daily digest email (and outreach in resend mode)",
          warn=True)
    check(cfg.email_provider in ("resend", "instantly"),
          f"EMAIL_PROVIDER valid ({cfg.email_provider})",
          "must be 'resend' or 'instantly'")
    if cfg.email_provider == "instantly":
        check(cfg.instantly_configured,
              "Instantly configured (INSTANTLY_API_KEY + INSTANTLY_CAMPAIGN_ID)",
              "required because EMAIL_PROVIDER=instantly")
    check(bool(cfg.owner_email), "OWNER_EMAIL set", "digest destination", warn=True)
    check(len(cfg.products) > 0, "products.yaml has products")
    for p in cfg.products:
        check(bool(p.pain_phrases), f"{p.name}: has pain_phrases",
              "discovery needs search phrases")
    raw = Path("config/products.yaml").read_text(encoding="utf-8")
    check("TODO(Dan)" not in raw, "products.yaml placeholders replaced",
          "URLs/pricing marked TODO(Dan) are quoted verbatim in outreach", warn=True)
    if cfg.send_enabled and not cfg.email_configured:
        check(False, "SEND_ENABLED=true but email not configured",
              "set RESEND_API_KEY + FROM_EMAIL or flip SEND_ENABLED back to false")
    mode = "EMAIL AUTOPILOT" if (cfg.send_enabled and cfg.email_configured) else "draft-only"
    print(f"\nMode: {mode} · daily email cap {cfg.daily_email_cap}")
    print("Ready." if problems == 0 else f"{problems} blocking problem(s) above.")
    return 0 if problems == 0 else 1


def _require_api_key(cfg) -> None:
    if not cfg.anthropic_api_key:
        sys.exit("ANTHROPIC_API_KEY is not set — copy .env.example to .env or add it "
                 "as a GitHub Actions secret.")


def _draft_id(args) -> int:
    if not args.arg or not args.arg.isdigit():
        sys.exit("Expected a numeric draft id")
    return int(args.arg)


if __name__ == "__main__":
    raise SystemExit(main())
