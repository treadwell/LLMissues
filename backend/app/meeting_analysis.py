from datetime import datetime
import math

from app.db import fetch_all, fetch_one
from app.embeddings import (
    embed_texts,
    serialize_vector,
    deserialize_vector,
    now_iso,
    resolve_embedding_model,
)


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


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def build_issue_text(issue, steps: list[dict]) -> str:
    parts = [
        f"Title: {issue['title'] if 'title' in issue else ''}",
        f"Domain: {issue['domain'] if 'domain' in issue else ''}",
        f"Status: {issue['status'] if 'status' in issue else ''}",
        f"Situation: {issue['situation'] if 'situation' in issue else ''}",
        f"Complication: {issue['complication'] if 'complication' in issue else ''}",
        f"Resolution: {issue['resolution'] if 'resolution' in issue else ''}",
    ]
    if steps:
        steps_text = "; ".join(
            f"{s['description'] if 'description' in s else ''}|{s['owner'] if 'owner' in s else ''}|{s['due_date'] if 'due_date' in s else ''}|{s['status'] if 'status' in s else ''}"
            for s in steps
        )
        parts.append(f"Steps: {steps_text}")
    return "\n".join(parts).strip()


def select_issue_candidates(conn, issues: list[dict], steps_map: dict[int, list[dict]], meeting_text: str, limit: int = 50):
    if not issues:
        return []
    embed_model = resolve_embedding_model()

    # Load existing embeddings
    issue_ids = [issue["id"] for issue in issues]
    rows = fetch_all(
        conn,
        f"SELECT issue_id, model, vector FROM issue_embeddings WHERE issue_id IN ({','.join('?' for _ in issue_ids)})",
        issue_ids,
    )
    emb_map = {row["issue_id"]: row for row in rows}

    # Compute missing or stale embeddings
    missing_or_stale = [
        issue
        for issue in issues
        if issue["id"] not in emb_map or (emb_map[issue["id"]]["model"] or "") != embed_model
    ]
    if missing_or_stale:
        texts = [build_issue_text(issue, steps_map.get(issue["id"], [])) for issue in missing_or_stale]
        vectors = embed_texts(texts, model=embed_model)
        for issue, vec in zip(missing_or_stale, vectors):
            conn.execute(
                """
                INSERT OR REPLACE INTO issue_embeddings (issue_id, model, vector, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                [issue["id"], embed_model, serialize_vector(vec), now_iso()],
            )
            emb_map[issue["id"]] = {"model": embed_model, "vector": serialize_vector(vec)}

    meeting_vec = embed_texts([meeting_text], model=embed_model)[0]

    scored = []
    for issue in issues:
        vec_payload = emb_map.get(issue["id"])
        if not vec_payload:
            continue
        vec = deserialize_vector(vec_payload["vector"])
        scored.append((issue["id"], _cosine_similarity(meeting_vec, vec)))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = {issue_id for issue_id, _ in scored[:limit]}
    return [issue for issue in issues if issue["id"] in top_ids]
