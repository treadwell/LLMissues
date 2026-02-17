import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

TAG_PREFIX = "Meetings."


def _upsert_document(
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
        return int(existing["id"])

    cur = conn.execute(
        """
        INSERT INTO documents (calibre_book_id, title, path, tags, text_excerpt, text_size, text_format, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [calibre_book_id, title, path, tags_text, text_excerpt, text_size, text_format, now],
    )
    return int(cur.lastrowid)


def _upsert_meeting(conn: sqlite3.Connection, meeting_date: str, source_tag: str, title: str) -> tuple[int, bool]:
    existing = conn.execute(
        "SELECT id FROM meetings WHERE meeting_date = ? AND source_tag = ?",
        [meeting_date, source_tag],
    ).fetchone()
    now = datetime.utcnow().isoformat(timespec="seconds")
    if existing:
        return int(existing["id"]), False

    cur = conn.execute(
        """
        INSERT INTO meetings (meeting_date, title, source_tag, created_at)
        VALUES (?, ?, ?, ?)
        """,
        [meeting_date, title, source_tag, now],
    )
    return int(cur.lastrowid), True


def _extract_meeting_tags(tags: Iterable[str]) -> list[str]:
    return [tag for tag in tags if tag.startswith(TAG_PREFIX)]


def _fetch_meeting_books(
    meta_conn: sqlite3.Connection,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT DISTINCT b.id, b.title, b.path
        FROM books b
        INNER JOIN books_tags_link l ON l.book = b.id
        INNER JOIN tags t ON t.id = l.tag
        WHERE t.name LIKE 'Meetings.%'
    """
    params: list[str] = []
    if start_date:
        query += " AND t.name >= ?"
        params.append(f"{TAG_PREFIX}{start_date}")
    if end_date:
        query += " AND t.name <= ?"
        params.append(f"{TAG_PREFIX}{end_date}")
    query += " ORDER BY b.id"
    return meta_conn.execute(query, params).fetchall()


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

    def rank(row: sqlite3.Row) -> int:
        fmt = (row["format"] or "").upper()
        return preferred.index(fmt) if fmt in preferred else len(preferred)

    best = sorted(candidates, key=rank)[0]
    text = best["searchable_text"]
    excerpt = text[:800].strip()
    return excerpt, int(best["text_size"] or 0), best["format"] or ""


def _parse_meeting_date(tag: str) -> str | None:
    raw = tag.replace(TAG_PREFIX, "").strip()
    try:
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    except ValueError:
        return None


def ingest_library(
    library_path: Path,
    app_conn: sqlite3.Connection,
    commit_interval: int = 200,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, int]:
    if not library_path.exists():
        raise FileNotFoundError(f"Library path not found: {library_path}")

    metadata_db = library_path / "metadata.db"
    fts_db = library_path / "full-text-search.db"
    if not metadata_db.exists():
        raise FileNotFoundError(f"metadata.db not found at {metadata_db}")
    if not fts_db.exists():
        raise FileNotFoundError(f"full-text-search.db not found at {fts_db}")

    meta_conn = sqlite3.connect(metadata_db)
    meta_conn.row_factory = sqlite3.Row
    fts_conn = sqlite3.connect(fts_db)
    fts_conn.row_factory = sqlite3.Row

    stats = {
        "books_seen": 0,
        "documents_upserted": 0,
        "meetings_created": 0,
        "meeting_links_added": 0,
        "meeting_tags_invalid": 0,
    }

    try:
        for book in _fetch_meeting_books(meta_conn, start_date=start_date, end_date=end_date):
            stats["books_seen"] += 1
            tags = _fetch_tags(meta_conn, int(book["id"]))
            excerpt, text_size, text_format = _fetch_search_text(fts_conn, int(book["id"]))
            relative_path = book["path"] or ""
            doc_path = str((library_path / relative_path).resolve())

            document_id = _upsert_document(
                app_conn,
                calibre_book_id=int(book["id"]),
                title=book["title"] or "",
                path=doc_path,
                tags=tags,
                text_excerpt=excerpt,
                text_size=text_size,
                text_format=text_format,
            )
            stats["documents_upserted"] += 1

            for meeting_tag in _extract_meeting_tags(tags):
                meeting_date = _parse_meeting_date(meeting_tag)
                if not meeting_date:
                    stats["meeting_tags_invalid"] += 1
                    continue
                meeting_id, created = _upsert_meeting(
                    app_conn,
                    meeting_date=meeting_date,
                    source_tag=meeting_tag,
                    title=f"Meeting {meeting_date}",
                )
                if created:
                    stats["meetings_created"] += 1
                cur = app_conn.execute(
                    """
                    INSERT OR IGNORE INTO meeting_document_links (meeting_id, document_id)
                    VALUES (?, ?)
                    """,
                    [meeting_id, document_id],
                )
                if cur.rowcount and cur.rowcount > 0:
                    stats["meeting_links_added"] += cur.rowcount

            if commit_interval > 0 and stats["books_seen"] % commit_interval == 0:
                app_conn.commit()
    finally:
        meta_conn.close()
        fts_conn.close()

    return stats
