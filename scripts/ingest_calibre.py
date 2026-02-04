import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - fallback if python-dotenv isn't installed
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "iimcs.sqlite3"
TAG_PREFIX = "Meetings."

sys.path.append(str(BASE_DIR / "backend"))
from app.db import init_db  # noqa: E402

if load_dotenv:
    load_dotenv(dotenv_path=BASE_DIR / ".env")
else:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_app_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_document(
    conn: sqlite3.Connection,
    calibre_book_id: int,
    title: str,
    path: str,
    tags: list[str],
    text_excerpt: str,
    text_size: int,
    text_format: str,
) -> int:
    existing = conn.execute("SELECT id FROM documents WHERE path = ?", [path]).fetchone()
    now = datetime.utcnow().isoformat(timespec="seconds")
    tags_text = ",".join(tags)
    if existing:
        conn.execute(
            """
            UPDATE documents
            SET calibre_book_id = ?, title = ?, tags = ?, text_excerpt = ?, text_size = ?, text_format = ?, created_at = ?
            WHERE id = ?
            """,
            [calibre_book_id, title, tags_text, text_excerpt, text_size, text_format, now, existing["id"]],
        )
        return existing["id"]

    cur = conn.execute(
        """
        INSERT INTO documents (calibre_book_id, title, path, tags, text_excerpt, text_size, text_format, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [calibre_book_id, title, path, tags_text, text_excerpt, text_size, text_format, now],
    )
    return cur.lastrowid


def upsert_meeting(conn: sqlite3.Connection, meeting_date: str, source_tag: str, title: str) -> int:
    existing = conn.execute(
        "SELECT id FROM meetings WHERE meeting_date = ? AND source_tag = ?",
        [meeting_date, source_tag],
    ).fetchone()
    now = datetime.utcnow().isoformat(timespec="seconds")
    if existing:
        return existing["id"]

    cur = conn.execute(
        """
        INSERT INTO meetings (meeting_date, title, source_tag, created_at)
        VALUES (?, ?, ?, ?)
        """,
        [meeting_date, title, source_tag, now],
    )
    return cur.lastrowid


def extract_meeting_tags(tags: Iterable[str]) -> list[str]:
    return [tag for tag in tags if tag.startswith(TAG_PREFIX)]


def _fetch_books(meta_conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return meta_conn.execute("SELECT id, title, path FROM books").fetchall()


def _fetch_tags(meta_conn: sqlite3.Connection, book_id: int) -> list[str]:
    rows = meta_conn.execute(
        """
        SELECT t.name
        FROM tags t
        INNER JOIN books_tags_link l ON l.tag = t.id
        WHERE l.book = ?
        ORDER BY t.name COLLATE NOCASE
        """,
        [book_id],
    ).fetchall()
    return [row["name"] for row in rows]


def _fetch_search_text(fts_conn: sqlite3.Connection, book_id: int) -> tuple[str, int, str]:
    rows = fts_conn.execute(
        """
        SELECT format, searchable_text, text_size, err_msg
        FROM books_text
        WHERE book = ?
        """,
        [book_id],
    ).fetchall()

    preferred = ["EPUB", "PDF", "MOBI", "TXT", "AZW3", "DOCX"]
    candidates = []
    for row in rows:
        if row["err_msg"]:
            continue
        if not row["searchable_text"]:
            continue
        candidates.append(row)

    if not candidates:
        return "", 0, ""

    def rank(row):
        fmt = (row["format"] or "").upper()
        return preferred.index(fmt) if fmt in preferred else len(preferred)

    best = sorted(candidates, key=rank)[0]
    text = best["searchable_text"]
    excerpt = text[:800].strip()
    return excerpt, int(best["text_size"] or 0), best["format"]


def ingest_library(library_path: Path) -> None:
    if not library_path.exists():
        raise FileNotFoundError(f"Library path not found: {library_path}")
    metadata_db = library_path / "metadata.db"
    fts_db = library_path / "full-text-search.db"
    if not metadata_db.exists():
        raise FileNotFoundError(f"metadata.db not found at {metadata_db}")
    if not fts_db.exists():
        raise FileNotFoundError(f"full-text-search.db not found at {fts_db}")

    meta_conn = get_connection(metadata_db)
    fts_conn = get_connection(fts_db)

    with get_app_connection() as conn:
        for book in _fetch_books(meta_conn):
            tags = _fetch_tags(meta_conn, book["id"])
            excerpt, text_size, text_format = _fetch_search_text(fts_conn, book["id"])
            relative_path = book["path"] or ""
            doc_path = str(library_path / relative_path)

            document_id = upsert_document(
                conn,
                calibre_book_id=book["id"],
                title=book["title"],
                path=doc_path,
                tags=tags,
                text_excerpt=excerpt,
                text_size=text_size,
                text_format=text_format,
            )

            meeting_tags = extract_meeting_tags(tags)
            for meeting_tag in meeting_tags:
                meeting_date = meeting_tag.replace(TAG_PREFIX, "").strip()
                meeting_id = upsert_meeting(conn, meeting_date, meeting_tag, f"Meeting {meeting_date}")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO meeting_document_links (meeting_id, document_id)
                    VALUES (?, ?)
                    """,
                    [meeting_id, document_id],
                )

        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Calibre library metadata.")
    parser.add_argument("library", type=Path, nargs="?", help="Path to Calibre library")
    args = parser.parse_args()

    library = args.library or os.environ.get("CALIBRE_LIBRARY_PATH")
    if not library:
        raise SystemExit("Provide a Calibre library path or set CALIBRE_LIBRARY_PATH in .env")

    init_db()
    ingest_library(Path(library))


if __name__ == "__main__":
    main()
