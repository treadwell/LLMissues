import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
CALIBRE_LIBRARY_ENV = "CALIBRE_LIBRARY_PATH"

sys.path.append(str(BASE_DIR / "backend"))

from app.db import fetch_all, fetch_one, get_connection, init_db  # noqa: E402
from app.llm import extract_issues  # noqa: E402
from app.meeting_analysis import apply_updates, select_issue_candidates  # noqa: E402

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    load_dotenv = None

if load_dotenv:
    load_dotenv(dotenv_path=BASE_DIR / ".env")


def get_fts_connection(fts_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(fts_path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_full_text(fts_conn: sqlite3.Connection, calibre_book_id: int) -> tuple[str, str, int]:
    rows = fts_conn.execute(
        """
        SELECT format, searchable_text, text_size, err_msg
        FROM books_text
        WHERE book = ?
        """,
        [calibre_book_id],
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
        return "", "", 0

    def rank(row):
        fmt = (row["format"] or "").upper()
        return preferred.index(fmt) if fmt in preferred else len(preferred)

    best = sorted(candidates, key=rank)[0]
    return best["searchable_text"], best["format"], int(best["text_size"] or 0)




def main() -> None:
    parser = argparse.ArgumentParser(description="Extract issues from meeting transcripts.")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--max-chars", type=int, default=120000, help="Max chars per meeting")
    parser.add_argument("--max-issues", type=int, default=200, help="Max existing issues to include")
    args = parser.parse_args()

    calibre_library = os.environ.get(CALIBRE_LIBRARY_ENV)
    if not calibre_library:
        raise SystemExit("Set CALIBRE_LIBRARY_PATH in .env to locate Calibre library")

    fts_path = Path(calibre_library) / "full-text-search.db"
    if not fts_path.exists():
        raise SystemExit("full-text-search.db not found in Calibre library")

    init_db()

    with get_connection() as conn, get_fts_connection(fts_path) as fts_conn:
        meetings = fetch_all(
            conn,
            """
            SELECT id, meeting_date
            FROM meetings
            WHERE meeting_date BETWEEN ? AND ?
            ORDER BY meeting_date ASC
            """,
            [args.start, args.end],
        )

        for meeting in meetings:
            documents = fetch_all(
                conn,
                """
                SELECT d.id, d.title, d.path, d.calibre_book_id
                FROM documents d
                INNER JOIN meeting_document_links md ON md.document_id = d.id
                WHERE md.meeting_id = ?
                ORDER BY datetime(d.created_at) DESC
                """,
                [meeting["id"]],
            )

            doc_payloads = []
            char_budget = args.max_chars
            for doc in documents:
                if doc["calibre_book_id"] is None:
                    continue
                text, fmt, text_size = fetch_full_text(fts_conn, doc["calibre_book_id"])
                if not text:
                    continue
                if len(text) > char_budget:
                    text = text[:char_budget]
                char_budget -= len(text)
                doc_payloads.append(
                    {
                        "id": doc["id"],
                        "title": doc["title"],
                        "path": doc["path"],
                        "text": text,
                    }
                )
                if char_budget <= 0:
                    break

            if not doc_payloads:
                continue

            issues = fetch_all(
                conn,
                """
                SELECT id, title, domain, status, confidence, situation, complication, resolution, next_steps
                FROM issues
                ORDER BY datetime(updated_at) DESC
                LIMIT ?
                """,
                [args.max_issues],
            )
            steps_map = {}
            for issue in issues:
                steps_map[issue["id"]] = conn.execute(
                    """
                    SELECT description, owner, due_date, status, position, suggested
                    FROM issue_next_steps
                    WHERE issue_id = ?
                    ORDER BY position ASC, datetime(created_at) ASC
                    """,
                    [issue["id"]],
                ).fetchall()

            meeting_text = "\n\n".join(doc["text"] for doc in doc_payloads)
            candidate_issues = select_issue_candidates(conn, issues, steps_map, meeting_text, limit=args.max_issues)

            issue_payloads = [
                {
                    "id": issue["id"],
                    "title": issue["title"],
                    "domain": issue["domain"],
                    "status": issue["status"],
                    "confidence": issue["confidence"],
                    "situation": issue["situation"],
                    "complication": issue["complication"],
                    "resolution": issue["resolution"],
                    "next_steps": issue["next_steps"],
                    "steps": steps_map[issue["id"]],
                }
                for issue in candidate_issues
            ]

            llm_result = extract_issues(
                meeting_date=meeting["meeting_date"],
                documents=doc_payloads,
                existing_issues=issue_payloads,
            )

            apply_updates(conn, meeting["id"], meeting["meeting_date"], llm_result)

        conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES ('last_meeting_run_end', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [args.end],
        )
        conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES ('last_meeting_run_start', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [args.start],
        )
        conn.commit()


if __name__ == "__main__":
    main()
