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


def merge_delta(existing: str, delta: str, label: str, meeting_date: str) -> str:
    delta = (delta or "").strip()
    if not delta:
        return existing
    prefix = f"\n\n[{label} from meeting {meeting_date}]\n"
    return (existing or "") + prefix + delta


def _next_position(conn, issue_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(position), 0) AS max_pos FROM issue_next_steps WHERE issue_id = ?",
        [issue_id],
    ).fetchone()
    return int(row["max_pos"] or 0)


def insert_suggested_steps(conn, issue_id: int, steps: list[dict[str, str]]) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds")
    position = _next_position(conn, issue_id)
    for step in steps:
        description = (step.get("description") or "").strip()
        if not description:
            continue
        position += 1
        conn.execute(
            """
            INSERT INTO issue_next_steps (issue_id, description, owner, due_date, status, position, suggested, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                issue_id,
                description,
                (step.get("owner") or "").strip(),
                (step.get("due_date") or "").strip(),
                (step.get("status") or "Open").strip(),
                position,
                1,
                now,
                now,
            ],
        )


def apply_updates(conn, meeting_id: int, meeting_date: str, llm_result):
    for issue in llm_result.new_issues:
        now = datetime.utcnow().isoformat(timespec="seconds")
        cur = conn.execute(
            """
            INSERT INTO issues (
                title, domain, status, confidence, situation, complication, resolution,
                next_steps, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                issue["title"].strip(),
                (issue["domain"] or "General").strip() or "General",
                "Open",
                float(issue["confidence"]),
                issue["situation"].strip(),
                issue["complication"].strip(),
                issue["resolution"].strip(),
                "",
                now,
                now,
            ],
        )
        issue_id = cur.lastrowid
        insert_suggested_steps(conn, issue_id, issue["suggested_steps"])

        conn.execute(
            """
            INSERT OR IGNORE INTO issue_meeting_links (issue_id, meeting_id)
            VALUES (?, ?)
            """,
            [issue_id, meeting_id],
        )

        for doc_id in issue["document_ids"]:
            conn.execute(
                """
                INSERT OR IGNORE INTO issue_document_links (issue_id, document_id)
                VALUES (?, ?)
                """,
                [issue_id, doc_id],
            )

    for update in llm_result.updates:
        issue_id = update["issue_id"]
        existing = fetch_one(conn, "SELECT * FROM issues WHERE id = ?", [issue_id])
        if not existing:
            continue

        new_title = update["title"].strip() or existing["title"]
        new_domain = update["domain"].strip() or existing["domain"]
        new_status = update["status"].strip() or existing["status"]
        new_confidence = float(update["confidence"]) if update["confidence"] is not None else existing["confidence"]

        new_situation = merge_delta(existing["situation"], update["situation_delta"], "Situation", meeting_date)
        new_complication = merge_delta(existing["complication"], update["complication_delta"], "Complication", meeting_date)
        new_resolution = merge_delta(existing["resolution"], update["resolution_delta"], "Resolution", meeting_date)
        new_next_steps = existing["next_steps"]

        changes = {}
        for field, new_value in {
            "title": new_title,
            "domain": new_domain,
            "status": new_status,
            "confidence": new_confidence,
            "situation": new_situation,
            "complication": new_complication,
            "resolution": new_resolution,
            "next_steps": new_next_steps,
        }.items():
            if str(existing[field]) != str(new_value):
                changes[field] = {"old": str(existing[field]), "new": str(new_value)}

        now = datetime.utcnow().isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE issues
            SET title = ?, domain = ?, status = ?, confidence = ?,
                situation = ?, complication = ?, resolution = ?, next_steps = ?,
                updated_at = ?
            WHERE id = ?
            """,
            [
                new_title,
                new_domain,
                new_status,
                new_confidence,
                new_situation,
                new_complication,
                new_resolution,
                new_next_steps,
                now,
                issue_id,
            ],
        )

        for field, values in changes.items():
            conn.execute(
                """
                INSERT INTO issue_revisions (issue_id, field, old_value, new_value, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [issue_id, field, values["old"], values["new"], "llm", now],
            )

        conn.execute(
            """
            INSERT OR IGNORE INTO issue_meeting_links (issue_id, meeting_id)
            VALUES (?, ?)
            """,
            [issue_id, meeting_id],
        )

        for doc_id in update["document_ids"]:
            conn.execute(
                """
                INSERT OR IGNORE INTO issue_document_links (issue_id, document_id)
                VALUES (?, ?)
                """,
                [issue_id, doc_id],
            )

        insert_suggested_steps(conn, issue_id, update["suggested_steps"])


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
                    "steps": conn.execute(
                        """
                        SELECT description, owner, due_date, status, position, suggested
                        FROM issue_next_steps
                        WHERE issue_id = ?
                        ORDER BY position ASC, datetime(created_at) ASC
                        """,
                        [issue["id"]],
                    ).fetchall(),
                }
                for issue in issues
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
