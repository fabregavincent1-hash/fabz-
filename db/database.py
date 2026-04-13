"""
SQLite database layer — tracks which clips have been processed and posted
to avoid duplicate uploads across runs.
"""

import sqlite3
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Create tables if they don't exist."""
    with get_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source        TEXT NOT NULL,       -- 'twitch' or 'youtube'
                clip_id       TEXT NOT NULL,        -- platform clip ID or URL
                clip_url      TEXT NOT NULL,
                streamer      TEXT NOT NULL,
                title         TEXT,
                view_count    INTEGER DEFAULT 0,
                duration      REAL DEFAULT 0,
                discovered_at TEXT NOT NULL,
                processed_at  TEXT,
                UNIQUE(source, clip_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS uploads (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                clip_id      INTEGER NOT NULL REFERENCES clips(id),
                destination  TEXT NOT NULL,   -- 'youtube_shorts', 'tiktok', 'instagram_reels'
                post_id      TEXT,            -- ID returned by destination platform
                uploaded_at  TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'success',  -- 'success' | 'failed'
                error        TEXT
            )
        """)
        conn.commit()
    logger.info("Database initialised at %s", db_path)


def is_clip_known(db_path: str, source: str, clip_id: str) -> bool:
    """Return True if this clip has already been discovered (regardless of upload status)."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM clips WHERE source=? AND clip_id=?",
            (source, clip_id),
        ).fetchone()
    return row is not None


def save_clip(
    db_path: str,
    source: str,
    clip_id: str,
    clip_url: str,
    streamer: str,
    title: str = "",
    view_count: int = 0,
    duration: float = 0.0,
) -> int:
    """Insert a new clip record. Returns the new row id."""
    now = datetime.utcnow().isoformat()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO clips
                (source, clip_id, clip_url, streamer, title, view_count, duration, discovered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source, clip_id, clip_url, streamer, title, view_count, duration, now),
        )
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        # Already existed — fetch its id
        row = conn.execute(
            "SELECT id FROM clips WHERE source=? AND clip_id=?",
            (source, clip_id),
        ).fetchone()
        return row["id"]


def mark_clip_processed(db_path: str, row_id: int) -> None:
    now = datetime.utcnow().isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET processed_at=? WHERE id=?",
            (now, row_id),
        )
        conn.commit()


def record_upload(
    db_path: str,
    clip_row_id: int,
    destination: str,
    post_id: Optional[str] = None,
    status: str = "success",
    error: Optional[str] = None,
) -> None:
    now = datetime.utcnow().isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO uploads (clip_id, destination, post_id, uploaded_at, status, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (clip_row_id, destination, post_id, now, status, error),
        )
        conn.commit()


def has_been_uploaded(db_path: str, clip_row_id: int, destination: str) -> bool:
    """Return True if this clip was already successfully uploaded to the given destination."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM uploads WHERE clip_id=? AND destination=? AND status='success'",
            (clip_row_id, destination),
        ).fetchone()
    return row is not None
