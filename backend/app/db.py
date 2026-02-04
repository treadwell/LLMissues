import sqlite3
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "iimcs.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT "General",
    status TEXT NOT NULL DEFAULT "Open",
    confidence REAL NOT NULL DEFAULT 0.5,
    situation TEXT NOT NULL DEFAULT "",
    complication TEXT NOT NULL DEFAULT "",
    resolution TEXT NOT NULL DEFAULT "",
    next_steps TEXT NOT NULL DEFAULT "",
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issue_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(issue_id) REFERENCES issues(id)
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calibre_book_id INTEGER,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT "",
    text_excerpt TEXT NOT NULL DEFAULT "",
    text_size INTEGER NOT NULL DEFAULT 0,
    text_format TEXT NOT NULL DEFAULT "",
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_date TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT "Meeting",
    source_tag TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issue_document_links (
    issue_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    PRIMARY KEY (issue_id, document_id),
    FOREIGN KEY(issue_id) REFERENCES issues(id),
    FOREIGN KEY(document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS issue_meeting_links (
    issue_id INTEGER NOT NULL,
    meeting_id INTEGER NOT NULL,
    PRIMARY KEY (issue_id, meeting_id),
    FOREIGN KEY(issue_id) REFERENCES issues(id),
    FOREIGN KEY(meeting_id) REFERENCES meetings(id)
);

CREATE TABLE IF NOT EXISTS meeting_document_links (
    meeting_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, document_id),
    FOREIGN KEY(meeting_id) REFERENCES meetings(id),
    FOREIGN KEY(document_id) REFERENCES documents(id)
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(
            conn,
            "documents",
            {
                "calibre_book_id": "INTEGER",
                "text_excerpt": "TEXT NOT NULL DEFAULT \"\"",
                "text_size": "INTEGER NOT NULL DEFAULT 0",
                "text_format": "TEXT NOT NULL DEFAULT \"\"",
            },
        )
        conn.commit()


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def fetch_all(conn: sqlite3.Connection, query: str, params: Iterable | None = None):
    cur = conn.execute(query, params or [])
    return cur.fetchall()


def fetch_one(conn: sqlite3.Connection, query: str, params: Iterable | None = None):
    cur = conn.execute(query, params or [])
    return cur.fetchone()
