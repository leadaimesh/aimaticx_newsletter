"""Gmail inbox monitoring: the reply-detection half of "controlling an inbox".

Every run, for each configured Gmail account, the engine reads NEW mail
(via IMAP, read-only — it never marks anything read or touches your mail)
and closes the loop automatically:

- A reply from someone we contacted  -> replies metric +1, all pending
  follow-ups for that lead cancelled (nobody gets chased after answering).
- An opt-out ("unsubscribe", "not interested", "stop") -> suppressed forever.
- A bounce (mailer-daemon)           -> address suppressed, drafts dismissed.

Monitoring starts from the moment an account is first seen: historical mail
is never scanned, so your existing inbox contents stay private to you.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import re
import sqlite3

from .config import Config
from .db import bump_metric, log_event, suppress

OPTOUT_PATTERNS = re.compile(
    r"unsubscribe|not interested|no thanks|stop emailing|remove me|opt me out",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def check_inboxes(conn: sqlite3.Connection, cfg: Config) -> dict:
    stats = {"accounts": 0, "replies": 0, "bounces": 0, "optouts": 0}
    accounts = cfg.gmail_accounts()
    if not accounts:
        return {"skipped": "no gmail accounts configured"}

    contacted = _contacted_map(conn)

    for address, password in accounts:
        try:
            _check_one(conn, address, password, contacted, stats)
            stats["accounts"] += 1
        except (imaplib.IMAP4.error, OSError) as exc:
            log_event(conn, "inbox_error", f"{address}: {exc}")
    return stats


def _check_one(conn: sqlite3.Connection, address: str, password: str,
               contacted: dict, stats: dict) -> None:
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)   # read-only: flags never change

        typ, data = imap.uid("search", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return
        uids = [int(u) for u in data[0].split()]
        max_uid = max(uids)

        row = conn.execute(
            "SELECT last_uid FROM inbox_state WHERE account=?", (address,)
        ).fetchone()
        if row is None:
            # First sighting: start monitoring from now, never scan history.
            conn.execute(
                "INSERT INTO inbox_state (account, last_uid) VALUES (?, ?)",
                (address, max_uid),
            )
            conn.commit()
            log_event(conn, "inbox_registered", f"{address} from uid {max_uid}")
            return

        new_uids = [u for u in uids if u > row["last_uid"]]
        for uid in new_uids:
            typ, msg_data = imap.uid("fetch", str(uid), "(RFC822)")
            if typ != "OK" or not msg_data or msg_data[0] is None:
                continue
            _process_message(conn, email.message_from_bytes(msg_data[0][1]),
                             contacted, stats)

        if new_uids:
            conn.execute(
                "UPDATE inbox_state SET last_uid=? WHERE account=?",
                (max(new_uids), address),
            )
            conn.commit()
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _process_message(conn: sqlite3.Connection, msg, contacted: dict, stats: dict) -> None:
    from_addr = (email.utils.parseaddr(msg.get("From", ""))[1] or "").lower()
    body = _body_text(msg)

    # Bounces: suppress the failed recipient so we never retry a dead address.
    if from_addr.startswith(("mailer-daemon", "postmaster")):
        failed = (msg.get("X-Failed-Recipients") or "").lower().strip()
        if not failed:
            hits = [e.lower() for e in EMAIL_RE.findall(body) if e.lower() in contacted]
            failed = hits[0] if hits else ""
        if failed and failed in contacted:
            suppress(conn, failed, "bounced")
            _cancel_pending(conn, failed)
            stats["bounces"] += 1
            log_event(conn, "inbox_bounce", failed)
        return

    if from_addr not in contacted:
        return   # not one of ours; never touched

    signal_id, product_id = contacted[from_addr]
    _cancel_pending(conn, from_addr)
    bump_metric(conn, product_id, "replies")
    stats["replies"] += 1

    if OPTOUT_PATTERNS.search(f"{msg.get('Subject', '')} {body[:1500]}"):
        suppress(conn, from_addr, "unsubscribed")
        stats["optouts"] += 1
        log_event(conn, "inbox_optout", from_addr)
    else:
        log_event(conn, "inbox_reply", f"{from_addr} (signal {signal_id})")


def _cancel_pending(conn: sqlite3.Connection, to_email: str) -> None:
    conn.execute(
        "UPDATE drafts SET status='dismissed' WHERE to_email=? AND status='pending'",
        (to_email,),
    )
    conn.commit()


def _contacted_map(conn: sqlite3.Connection) -> dict:
    """email -> (signal_id, product_id) for every lead we've emailed."""
    out: dict = {}
    for row in conn.execute(
        """SELECT d.to_email, d.signal_id, s.product_id
           FROM drafts d JOIN signals s ON s.id=d.signal_id
           WHERE d.kind='email' AND d.status='sent' AND d.to_email IS NOT NULL"""
    ):
        out[row["to_email"].lower()] = (row["signal_id"], row["product_id"])
    return out


def _body_text(msg) -> str:
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode("utf-8", errors="ignore")[:4000]
            return ""
        payload = msg.get_payload(decode=True)
        return payload.decode("utf-8", errors="ignore")[:4000] if payload else ""
    except Exception:
        return ""
