"""SQLite storage for the sales engine. One file: data/engine.db (committed)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR

DB_PATH = DATA_DIR / "engine.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    product_id TEXT NOT NULL,
    source TEXT NOT NULL,               -- reddit | hackernews | google | rss
    source_id TEXT NOT NULL,            -- stable id at the source (dedupe key)
    url TEXT,
    title TEXT,
    body TEXT,
    author TEXT,
    posted_at TEXT,                     -- when the person posted (UTC ISO)
    found_at TEXT NOT NULL,             -- when we discovered it
    matched_phrase TEXT,                -- which search phrase found this lead
    status TEXT NOT NULL DEFAULT 'new', -- new | scored | qualified | skipped
    score INTEGER,                      -- 0-100 (rubric subscores summed in code)
    score_detail TEXT,                  -- JSON: per-criterion subscores + rationale
    channel TEXT,                       -- reddit_reply | hn_reply | email | skip
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    kind TEXT NOT NULL,                 -- reply | email
    touch INTEGER NOT NULL DEFAULT 1,   -- 1 = first touch, 2/3 = follow-ups
    to_email TEXT,                      -- only for kind=email
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | sent | posted | dismissed
    created_at TEXT NOT NULL,
    sent_at TEXT,
    scheduled_for TEXT                  -- follow-ups wait until this date
);

CREATE TABLE IF NOT EXISTS suppression (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,    -- email address or author handle
    reason TEXT NOT NULL,               -- unsubscribed | bounced | complained | not_interested | manual
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics_daily (
    date TEXT NOT NULL,
    product_id TEXT NOT NULL,
    signals_found INTEGER DEFAULT 0,
    qualified INTEGER DEFAULT 0,
    drafts_created INTEGER DEFAULT 0,
    emails_sent INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0,          -- manual entry / future inbound tracking
    PRIMARY KEY (date, product_id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:  # migration for DBs created before phrase tracking
        conn.execute("ALTER TABLE signals ADD COLUMN matched_phrase TEXT")
    except sqlite3.OperationalError:
        pass
    return conn


def log_event(conn: sqlite3.Connection, kind: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
        (now_iso(), kind, detail),
    )
    conn.commit()


def insert_signal(conn: sqlite3.Connection, sig: dict) -> bool:
    """Insert a discovered signal; returns False if we already had it."""
    try:
        conn.execute(
            """INSERT INTO signals
               (product_id, source, source_id, url, title, body, author, posted_at,
                found_at, matched_phrase)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sig["product_id"], sig["source"], sig["source_id"],
                sig.get("url"), sig.get("title"), sig.get("body"),
                sig.get("author"), sig.get("posted_at"), now_iso(),
                sig.get("matched_phrase"),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def bump_metric(conn: sqlite3.Connection, product_id: str, column: str, n: int = 1) -> None:
    assert column in {"signals_found", "qualified", "drafts_created", "emails_sent", "replies"}
    conn.execute(
        f"""INSERT INTO metrics_daily (date, product_id, {column}) VALUES (?, ?, ?)
            ON CONFLICT(date, product_id) DO UPDATE SET {column} = {column} + ?""",
        (today(), product_id, n, n),
    )
    conn.commit()


def is_suppressed(conn: sqlite3.Connection, identifier: str | None) -> bool:
    if not identifier:
        return False
    row = conn.execute(
        "SELECT 1 FROM suppression WHERE identifier = ?", (identifier.lower(),)
    ).fetchone()
    return row is not None


def suppress(conn: sqlite3.Connection, identifier: str, reason: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO suppression (identifier, reason, added_at) VALUES (?, ?, ?)",
        (identifier.lower(), reason, now_iso()),
    )
    conn.commit()


def drafts_created_today(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM drafts WHERE touch=1 AND created_at LIKE ?",
        (today() + "%",),
    ).fetchone()
    return row["n"]


def emails_sent_today(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM drafts WHERE kind='email' AND status='sent' AND sent_at LIKE ?",
        (today() + "%",),
    ).fetchone()
    return row["n"]
