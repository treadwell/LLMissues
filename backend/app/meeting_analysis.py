from datetime import datetime

from app.db import fetch_one


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


def merge_delta(existing: str, delta: str, label: str, meeting_date: str) -> str:
    delta = (delta or "").strip()
    if not delta:
        return existing
    prefix = f"\n\n[{label} from meeting {meeting_date}]\n"
    return (existing or "") + prefix + delta


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
