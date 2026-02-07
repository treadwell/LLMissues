from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config
from app.db import fetch_all, fetch_one, get_connection, init_db
from app.llm import extract_issues
from app.meeting_analysis import apply_updates, select_issue_candidates

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="IIMCS")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
async def index(
    request: Request,
    status: str = Query("Open", max_length=20),
    domain: str = Query("", max_length=100),
    owner: str = Query("", max_length=100),
):
    filters = {
        "status": status.strip(),
        "domain": domain.strip(),
        "owner": owner.strip(),
    }
    with get_connection() as conn:
        domain_options = _fetch_domain_options(conn)
        owner_options = _fetch_owner_options(conn)
        query = """
            SELECT id, title, domain, status, owner, confidence, updated_at
            FROM issues
            WHERE 1=1
        """
        params: list[str] = []
        if filters["status"]:
            query += " AND status = ?"
            params.append(filters["status"])
        if filters["domain"]:
            query += " AND domain LIKE ?"
            params.append(f"%{filters['domain']}%")
        if filters["owner"]:
            query += " AND owner LIKE ?"
            params.append(f"%{filters['owner']}%")
        query += " ORDER BY datetime(updated_at) DESC"

        issues = fetch_all(conn, query, params)
        last_run = fetch_one(
            conn,
            "SELECT value FROM app_state WHERE key = 'last_meeting_run_end'",
        )
        if not last_run or not last_run["value"]:
            last_run = fetch_one(
                conn,
                "SELECT MAX(meeting_date) AS value FROM meetings",
            )
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "issues": issues,
            "filters": filters,
            "last_meeting_run_end": last_run["value"] if last_run else None,
            "domain_options": domain_options,
            "owner_options": owner_options,
        },
    )


@app.get("/issues", response_class=HTMLResponse)
async def issues_list(
    request: Request,
    status: str = Query("Open", max_length=20),
    domain: str = Query("", max_length=100),
    owner: str = Query("", max_length=100),
):
    filters = {
        "status": status.strip(),
        "domain": domain.strip(),
        "owner": owner.strip(),
    }
    with get_connection() as conn:
        domain_options = _fetch_domain_options(conn)
        owner_options = _fetch_owner_options(conn)
        query = """
            SELECT id, title, domain, status, owner, confidence, updated_at
            FROM issues
            WHERE 1=1
        """
        params: list[str] = []
        if filters["status"]:
            query += " AND status = ?"
            params.append(filters["status"])
        if filters["domain"]:
            query += " AND domain LIKE ?"
            params.append(f"%{filters['domain']}%")
        if filters["owner"]:
            query += " AND owner LIKE ?"
            params.append(f"%{filters['owner']}%")
        query += " ORDER BY datetime(updated_at) DESC"

        issues = fetch_all(conn, query, params)
    return templates.TemplateResponse(
        "partials/issues_list.html",
        {
            "request": request,
            "issues": issues,
            "filters": filters,
            "domain_options": domain_options,
            "owner_options": owner_options,
        },
    )


def _fetch_steps(conn, issue_id: int):
    return fetch_all(
        conn,
        """
        SELECT id, issue_id, description, owner, due_date, status, position, suggested, created_at
        FROM issue_next_steps
        WHERE issue_id = ?
        ORDER BY position ASC, datetime(created_at) ASC
        """,
        [issue_id],
    )


def _fetch_domain_options(conn):
    return fetch_all(
        conn,
        "SELECT id, name FROM domain_options ORDER BY name COLLATE NOCASE",
    )


def _fetch_owner_options(conn):
    return fetch_all(
        conn,
        "SELECT id, name FROM owner_options ORDER BY name COLLATE NOCASE",
    )


@app.get("/issues/{issue_id}")
async def issue_detail(request: Request, issue_id: int):
    with get_connection() as conn:
        issue = fetch_one(
            conn,
            """
            SELECT *
            FROM issues
            WHERE id = ?
            """,
            [issue_id],
        )
        revisions = fetch_all(
            conn,
            """
            SELECT field, old_value, new_value, actor, created_at
            FROM issue_revisions
            WHERE issue_id = ?
            ORDER BY datetime(created_at) DESC
            """,
            [issue_id],
        )
        documents = fetch_all(
            conn,
            """
            SELECT d.id, d.title, d.path, d.tags, d.created_at, d.text_size, d.text_format
            FROM documents d
            INNER JOIN issue_document_links l ON l.document_id = d.id
            WHERE l.issue_id = ?
            ORDER BY datetime(d.created_at) DESC
            """,
            [issue_id],
        )
        available_documents = fetch_all(
            conn,
            """
            SELECT id, title
            FROM documents
            WHERE id NOT IN (
                SELECT document_id FROM issue_document_links WHERE issue_id = ?
            )
            ORDER BY datetime(created_at) DESC
            LIMIT 50
            """,
            [issue_id],
        )
        domain_options = _fetch_domain_options(conn)
        owner_options = _fetch_owner_options(conn)
        steps = _fetch_steps(conn, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return templates.TemplateResponse(
        "issue_detail.html",
        {
            "request": request,
            "issue": issue,
            "revisions": revisions,
            "documents": documents,
            "available_documents": available_documents,
            "steps": steps,
            "domain_options": domain_options,
            "owner_options": owner_options,
        },
    )


@app.post("/issues", response_class=HTMLResponse)
async def create_issue(
    request: Request,
    title: str = Form(...),
    domain: str = Form("General"),
    owner: str = Form(""),
):
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as conn:
        if domain.strip():
            conn.execute(
                "INSERT OR IGNORE INTO domain_options (name, created_at) VALUES (?, ?)",
                [domain.strip(), now],
            )
        if owner.strip():
            conn.execute(
                "INSERT OR IGNORE INTO owner_options (name, created_at) VALUES (?, ?)",
                [owner.strip(), now],
            )
        cur = conn.execute(
            """
            INSERT INTO issues (
                title, domain, status, owner, confidence, situation, complication, resolution,
                next_steps, suggested_next_steps, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                title.strip(),
                domain.strip() or "General",
                "Open",
                owner.strip(),
                0.5,
                "",
                "",
                "",
                "",
                "",
                now,
                now,
            ],
        )
        issue_id = cur.lastrowid
        conn.commit()
        issue = fetch_one(
            conn,
            """
            SELECT id, title, domain, status, owner, confidence, updated_at
            FROM issues
            WHERE id = ?
            """,
            [issue_id],
        )
    return templates.TemplateResponse(
        "partials/issue_row.html",
        {
            "request": request,
            "issue": issue,
        },
        headers={"HX-Trigger": "issue-created"},
    )


def _log_revisions(conn, issue_id: int, changes: dict[str, Any], actor: str) -> None:
    if not changes:
        return
    now = datetime.utcnow().isoformat(timespec="seconds")
    for field, values in changes.items():
        conn.execute(
            """
            INSERT INTO issue_revisions (issue_id, field, old_value, new_value, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [issue_id, field, values["old"], values["new"], actor, now],
        )


@app.post("/issues/merge")
async def merge_issues(
    request: Request,
    target_id: int | None = Form(None),
    source_ids: str = Form(""),
    return_to: str = Form("/"),
):
    if not target_id:
        return RedirectResponse(url=return_to, status_code=303)

    ids = [int(i) for i in source_ids.split(",") if i.strip().isdigit()]
    ids = [i for i in ids if i != target_id]
    if not ids:
        return RedirectResponse(url=return_to, status_code=303)

    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as conn:
        target = fetch_one(conn, "SELECT * FROM issues WHERE id = ?", [target_id])
        if not target:
            raise HTTPException(status_code=404, detail="Target issue not found")

        for source_id in ids:
            source = fetch_one(conn, "SELECT * FROM issues WHERE id = ?", [source_id])
            if not source:
                continue

            def merge_field(field: str, label: str) -> str:
                if not source[field]:
                    return target[field]
                return (target[field] or "") + f"\n\n[Merged from #{source_id} {label}]\n" + source[field]

            target_update = {
                "situation": merge_field("situation", "Situation"),
                "complication": merge_field("complication", "Complication"),
                "resolution": merge_field("resolution", "Resolution"),
                "next_steps": merge_field("next_steps", "Next steps"),
            }

            for field, new_value in target_update.items():
                if str(target[field]) != str(new_value):
                    conn.execute(
                        """
                        INSERT INTO issue_revisions (issue_id, field, old_value, new_value, actor, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        [target_id, field, str(target[field]), str(new_value), "merge", now],
                    )

            conn.execute(
                """
                UPDATE issues
                SET situation = ?, complication = ?, resolution = ?, next_steps = ?, updated_at = ?
                WHERE id = ?
                """,
                [
                    target_update["situation"],
                    target_update["complication"],
                    target_update["resolution"],
                    target_update["next_steps"],
                    now,
                    target_id,
                ],
            )

            conn.execute(
                """
                INSERT OR IGNORE INTO issue_document_links (issue_id, document_id)
                SELECT ?, document_id FROM issue_document_links WHERE issue_id = ?
                """,
                [target_id, source_id],
            )
            conn.execute(
                "DELETE FROM issue_document_links WHERE issue_id = ?",
                [source_id],
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO issue_meeting_links (issue_id, meeting_id)
                SELECT ?, meeting_id FROM issue_meeting_links WHERE issue_id = ?
                """,
                [target_id, source_id],
            )
            conn.execute(
                "DELETE FROM issue_meeting_links WHERE issue_id = ?",
                [source_id],
            )
            conn.execute(
                "UPDATE issue_next_steps SET issue_id = ? WHERE issue_id = ?",
                [target_id, source_id],
            )
            conn.execute(
                "UPDATE issue_revisions SET issue_id = ? WHERE issue_id = ?",
                [target_id, source_id],
            )
            conn.execute("DELETE FROM issues WHERE id = ?", [source_id])

        conn.commit()

    return RedirectResponse(url=return_to, status_code=303)


@app.post("/issues/{issue_id}", response_class=HTMLResponse)
async def update_issue(
    request: Request,
    issue_id: int,
    title: str = Form(...),
    domain: str = Form(...),
    owner: str = Form(""),
    status: str = Form(...),
    confidence: float = Form(...),
    situation: str = Form(""),
    complication: str = Form(""),
    resolution: str = Form(""),
):
    with get_connection() as conn:
        existing = fetch_one(
            conn,
            """
            SELECT * FROM issues WHERE id = ?
            """,
            [issue_id],
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Issue not found")

        updates = {
            "title": title.strip(),
            "domain": domain.strip() or "General",
            "owner": owner.strip(),
            "status": status.strip(),
            "confidence": float(confidence),
            "situation": situation.strip(),
            "complication": complication.strip(),
            "resolution": resolution.strip(),
        }

        changes: dict[str, Any] = {}
        for field, new_value in updates.items():
            old_value = existing[field]
            if str(old_value) != str(new_value):
                changes[field] = {"old": str(old_value), "new": str(new_value)}

        now = datetime.utcnow().isoformat(timespec="seconds")
        if updates["domain"]:
            conn.execute(
                "INSERT OR IGNORE INTO domain_options (name, created_at) VALUES (?, ?)",
                [updates["domain"], now],
            )
        if updates["owner"]:
            conn.execute(
                "INSERT OR IGNORE INTO owner_options (name, created_at) VALUES (?, ?)",
                [updates["owner"], now],
            )
        conn.execute(
            """
            UPDATE issues
            SET title = ?, domain = ?, owner = ?, status = ?, confidence = ?,
                situation = ?, complication = ?, resolution = ?,
                updated_at = ?
            WHERE id = ?
            """,
            [
                updates["title"],
                updates["domain"],
                updates["owner"],
                updates["status"],
                updates["confidence"],
                updates["situation"],
                updates["complication"],
                updates["resolution"],
                now,
                issue_id,
            ],
        )
        _log_revisions(conn, issue_id, changes, actor="user")
        conn.commit()
        issue = fetch_one(
            conn,
            """
            SELECT * FROM issues WHERE id = ?
            """,
            [issue_id],
        )
        revisions = fetch_all(
            conn,
            """
            SELECT field, old_value, new_value, actor, created_at
            FROM issue_revisions
            WHERE issue_id = ?
            ORDER BY datetime(created_at) DESC
            """,
            [issue_id],
        )

    return templates.TemplateResponse(
        "partials/issue_detail_form.html",
        {
            "request": request,
            "issue": issue,
            "revisions": revisions,
            "saved": True,
        },
    )


@app.post("/issues/{issue_id}/delete")
async def delete_issue(request: Request, issue_id: int, return_to: str = Form("/")):
    with get_connection() as conn:
        conn.execute("DELETE FROM issue_document_links WHERE issue_id = ?", [issue_id])
        conn.execute("DELETE FROM issue_meeting_links WHERE issue_id = ?", [issue_id])
        conn.execute("DELETE FROM issue_next_steps WHERE issue_id = ?", [issue_id])
        conn.execute("DELETE FROM issue_revisions WHERE issue_id = ?", [issue_id])
        conn.execute("DELETE FROM issues WHERE id = ?", [issue_id])
        conn.commit()
    return RedirectResponse(url=return_to, status_code=303)


@app.post("/issues/{issue_id}/steps", response_class=HTMLResponse)
async def add_step(
    request: Request,
    issue_id: int,
    description: str = Form(...),
    owner: str = Form(""),
    due_date: str = Form(""),
    status: str = Form("Open"),
    position: int = Form(0),
):
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as conn:
        max_pos_row = fetch_one(
            conn,
            "SELECT COALESCE(MAX(position), 0) AS max_pos FROM issue_next_steps WHERE issue_id = ?",
            [issue_id],
        )
        max_pos = int(max_pos_row["max_pos"] or 0)
        desired = int(position or 0)
        if desired <= 0:
            desired = max_pos + 1
        else:
            conn.execute(
                """
                UPDATE issue_next_steps
                SET position = position + 1
                WHERE issue_id = ? AND position >= ?
                """,
                [issue_id, desired],
            )
        conn.execute(
            """
            INSERT INTO issue_next_steps (issue_id, description, owner, due_date, status, position, suggested, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [issue_id, description.strip(), owner.strip(), due_date.strip(), status.strip(), desired, 0, now, now],
        )
        conn.commit()
        steps = _fetch_steps(conn, issue_id)

    return templates.TemplateResponse(
        "partials/issue_steps.html",
        {
            "request": request,
            "issue_id": issue_id,
            "steps": steps,
            "owner_options": _fetch_owner_options(conn),
        },
    )


@app.post("/issues/{issue_id}/steps/{step_id}/accept", response_class=HTMLResponse)
async def accept_step(request: Request, issue_id: int, step_id: int):
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE issue_next_steps
            SET suggested = 0, updated_at = ?
            WHERE id = ? AND issue_id = ?
            """,
            [now, step_id, issue_id],
        )
        conn.commit()
        steps = _fetch_steps(conn, issue_id)

    return templates.TemplateResponse(
        "partials/issue_steps.html",
        {
            "request": request,
            "issue_id": issue_id,
            "steps": steps,
            "owner_options": _fetch_owner_options(conn),
        },
    )


@app.post("/issues/{issue_id}/steps/{step_id}", response_class=HTMLResponse)
async def update_step(
    request: Request,
    issue_id: int,
    step_id: int,
    description: str = Form(...),
    owner: str = Form(""),
    due_date: str = Form(""),
    status: str = Form("Open"),
    position: int = Form(1),
):
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as conn:
        existing = fetch_one(
            conn,
            """
            SELECT position FROM issue_next_steps
            WHERE id = ? AND issue_id = ?
            """,
            [step_id, issue_id],
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Step not found")

        desired = int(position or 1)
        if desired < 1:
            desired = 1

        if desired != existing["position"]:
            if desired > existing["position"]:
                conn.execute(
                    """
                    UPDATE issue_next_steps
                    SET position = position - 1
                    WHERE issue_id = ? AND position > ? AND position <= ?
                    """,
                    [issue_id, existing["position"], desired],
                )
            else:
                conn.execute(
                    """
                    UPDATE issue_next_steps
                    SET position = position + 1
                    WHERE issue_id = ? AND position >= ? AND position < ?
                    """,
                    [issue_id, desired, existing["position"]],
                )

        conn.execute(
            """
            UPDATE issue_next_steps
            SET description = ?, owner = ?, due_date = ?, status = ?, position = ?, updated_at = ?
            WHERE id = ? AND issue_id = ?
            """,
            [
                description.strip(),
                owner.strip(),
                due_date.strip(),
                status.strip(),
                desired,
                now,
                step_id,
                issue_id,
            ],
        )
        conn.commit()
        steps = _fetch_steps(conn, issue_id)

    return templates.TemplateResponse(
        "partials/issue_steps.html",
        {
            "request": request,
            "issue_id": issue_id,
            "steps": steps,
            "owner_options": _fetch_owner_options(conn),
        },
    )


@app.post("/issues/{issue_id}/steps/{step_id}/delete", response_class=HTMLResponse)
async def delete_step(request: Request, issue_id: int, step_id: int):
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM issue_next_steps WHERE id = ? AND issue_id = ?
            """,
            [step_id, issue_id],
        )
        conn.commit()
        steps = _fetch_steps(conn, issue_id)

    return templates.TemplateResponse(
        "partials/issue_steps.html",
        {
            "request": request,
            "issue_id": issue_id,
            "steps": steps,
            "owner_options": _fetch_owner_options(conn),
        },
    )


@app.post("/options/domain")
async def add_domain_option(request: Request, name: str = Form(...), return_to: str = Form("/")):
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO domain_options (name, created_at) VALUES (?, ?)",
            [name.strip(), now],
        )
        conn.commit()
    return RedirectResponse(url=return_to, status_code=303)


@app.post("/options/owner")
async def add_owner_option(request: Request, name: str = Form(...), return_to: str = Form("/")):
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO owner_options (name, created_at) VALUES (?, ?)",
            [name.strip(), now],
        )
        conn.commit()
    return RedirectResponse(url=return_to, status_code=303)


@app.get("/options")
async def options_index(request: Request):
    with get_connection() as conn:
        domains = _fetch_domain_options(conn)
        owners = _fetch_owner_options(conn)
    return templates.TemplateResponse(
        "options.html",
        {
            "request": request,
            "domains": domains,
            "owners": owners,
        },
    )


@app.post("/options/domain/{domain_id}")
async def update_domain_option(
    request: Request,
    domain_id: int,
    name: str = Form(...),
    return_to: str = Form("/options"),
):
    with get_connection() as conn:
        conn.execute(
            "UPDATE domain_options SET name = ? WHERE id = ?",
            [name.strip(), domain_id],
        )
        conn.commit()
    return RedirectResponse(url=return_to, status_code=303)


@app.post("/options/owner/{owner_id}")
async def update_owner_option(
    request: Request,
    owner_id: int,
    name: str = Form(...),
    return_to: str = Form("/options"),
):
    with get_connection() as conn:
        conn.execute(
            "UPDATE owner_options SET name = ? WHERE id = ?",
            [name.strip(), owner_id],
        )
        conn.commit()
    return RedirectResponse(url=return_to, status_code=303)


@app.post("/options/domain/{domain_id}/delete")
async def delete_domain_option(
    request: Request,
    domain_id: int,
    return_to: str = Form("/options"),
):
    with get_connection() as conn:
        conn.execute("DELETE FROM domain_options WHERE id = ?", [domain_id])
        conn.commit()
    return RedirectResponse(url=return_to, status_code=303)


@app.post("/options/owner/{owner_id}/delete")
async def delete_owner_option(
    request: Request,
    owner_id: int,
    return_to: str = Form("/options"),
):
    with get_connection() as conn:
        conn.execute("DELETE FROM owner_options WHERE id = ?", [owner_id])
        conn.commit()
    return RedirectResponse(url=return_to, status_code=303)


@app.post("/analysis/meetings")
async def analyze_meetings(
    request: Request,
    start: str = Form(...),
    end: str = Form(...),
    return_to: str = Form("/"),
):
    with get_connection() as conn:
        meetings = fetch_all(
            conn,
            """
            SELECT id, meeting_date
            FROM meetings
            WHERE meeting_date BETWEEN ? AND ?
            ORDER BY meeting_date ASC
            """,
            [start, end],
        )

        for meeting in meetings:
            documents = fetch_all(
                conn,
                """
                SELECT id, title, path, calibre_book_id, text_excerpt
                FROM documents
                WHERE id IN (
                    SELECT document_id FROM meeting_document_links WHERE meeting_id = ?
                )
                ORDER BY datetime(created_at) DESC
                """,
                [meeting["id"]],
            )

            doc_payloads = []
            for doc in documents:
                text = doc["text_excerpt"] or ""
                if not text:
                    continue
                doc_payloads.append(
                    {
                        "id": doc["id"],
                        "title": doc["title"],
                        "path": doc["path"],
                        "text": text,
                    }
                )

            if not doc_payloads:
                continue

            issues = fetch_all(
                conn,
                """
                SELECT id, title, domain, status, confidence, situation, complication, resolution, next_steps
                FROM issues
                ORDER BY datetime(updated_at) DESC
                LIMIT 200
                """,
            )
            steps_map = {}
            for issue in issues:
                steps_map[issue["id"]] = fetch_all(
                    conn,
                    """
                    SELECT description, owner, due_date, status, position, suggested
                    FROM issue_next_steps
                    WHERE issue_id = ?
                    ORDER BY position ASC, datetime(created_at) ASC
                    """,
                    [issue["id"]],
                )

            meeting_text = "\n\n".join(doc["text"] for doc in doc_payloads)
            candidate_issues = select_issue_candidates(conn, issues, steps_map, meeting_text, limit=50)

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
            [end],
        )
        conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES ('last_meeting_run_start', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [start],
        )
        conn.commit()

    return RedirectResponse(url=return_to, status_code=303)


@app.post("/issues/{issue_id}/documents", response_class=HTMLResponse)
async def link_document(request: Request, issue_id: int, document_id: int = Form(...)):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO issue_document_links (issue_id, document_id)
            VALUES (?, ?)
            """,
            [issue_id, document_id],
        )
        conn.commit()
        documents = fetch_all(
            conn,
            """
            SELECT d.id, d.title, d.path, d.tags, d.created_at
            FROM documents d
            INNER JOIN issue_document_links l ON l.document_id = d.id
            WHERE l.issue_id = ?
            ORDER BY datetime(d.created_at) DESC
            """,
            [issue_id],
        )
        available_documents = fetch_all(
            conn,
            """
            SELECT id, title
            FROM documents
            WHERE id NOT IN (
                SELECT document_id FROM issue_document_links WHERE issue_id = ?
            )
            ORDER BY datetime(created_at) DESC
            LIMIT 50
            """,
            [issue_id],
        )

    return templates.TemplateResponse(
        "partials/issue_documents.html",
        {
            "request": request,
            "issue_id": issue_id,
            "documents": documents,
            "available_documents": available_documents,
        },
    )


@app.post("/issues/{issue_id}/documents/{document_id}/remove", response_class=HTMLResponse)
async def unlink_document(request: Request, issue_id: int, document_id: int):
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM issue_document_links
            WHERE issue_id = ? AND document_id = ?
            """,
            [issue_id, document_id],
        )
        conn.commit()
        documents = fetch_all(
            conn,
            """
            SELECT d.id, d.title, d.path, d.tags, d.created_at
            FROM documents d
            INNER JOIN issue_document_links l ON l.document_id = d.id
            WHERE l.issue_id = ?
            ORDER BY datetime(d.created_at) DESC
            """,
            [issue_id],
        )
        available_documents = fetch_all(
            conn,
            """
            SELECT id, title
            FROM documents
            WHERE id NOT IN (
                SELECT document_id FROM issue_document_links WHERE issue_id = ?
            )
            ORDER BY datetime(created_at) DESC
            LIMIT 50
            """,
            [issue_id],
        )

    return templates.TemplateResponse(
        "partials/issue_documents.html",
        {
            "request": request,
            "issue_id": issue_id,
            "documents": documents,
            "available_documents": available_documents,
        },
    )


@app.get("/documents")
async def documents_index(request: Request):
    with get_connection() as conn:
        documents = fetch_all(
            conn,
            """
            SELECT d.id, d.title, d.path, d.tags, d.created_at,
                   d.text_size, d.text_format,
                   COUNT(l.issue_id) AS linked_issues
            FROM documents d
            LEFT JOIN issue_document_links l ON l.document_id = d.id
            GROUP BY d.id
            ORDER BY datetime(d.created_at) DESC
            """,
        )
    return templates.TemplateResponse(
        "documents.html",
        {
            "request": request,
            "documents": documents,
        },
    )


@app.get("/documents/{document_id}")
async def document_detail(request: Request, document_id: int):
    with get_connection() as conn:
        document = fetch_one(
            conn,
            """
            SELECT d.id, d.title, d.path, d.tags, d.created_at, d.text_excerpt,
                   d.text_size, d.text_format, COUNT(l.issue_id) AS linked_issues
            FROM documents d
            LEFT JOIN issue_document_links l ON l.document_id = d.id
            WHERE d.id = ?
            GROUP BY d.id
            """,
            [document_id],
        )
        issues = fetch_all(
            conn,
            """
            SELECT i.id, i.title, i.status, i.domain, i.confidence
            FROM issues i
            INNER JOIN issue_document_links l ON l.issue_id = i.id
            WHERE l.document_id = ?
            ORDER BY datetime(i.updated_at) DESC
            """,
            [document_id],
        )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return templates.TemplateResponse(
        "document_detail.html",
        {
            "request": request,
            "document": document,
            "issues": issues,
        },
    )


@app.get("/meetings")
async def meetings_index(request: Request):
    with get_connection() as conn:
        meetings = fetch_all(
            conn,
            """
            SELECT m.id, m.meeting_date, m.title, m.source_tag, m.created_at,
                   COUNT(md.document_id) AS document_count
            FROM meetings m
            LEFT JOIN meeting_document_links md ON md.meeting_id = m.id
            GROUP BY m.id
            ORDER BY m.meeting_date DESC
            """,
        )
    return templates.TemplateResponse(
        "meetings.html",
        {
            "request": request,
            "meetings": meetings,
        },
    )


@app.get("/meetings/{meeting_id}")
async def meeting_detail(request: Request, meeting_id: int):
    with get_connection() as conn:
        meeting = fetch_one(
            conn,
            """
            SELECT m.id, m.meeting_date, m.title, m.source_tag, m.created_at,
                   COUNT(md.document_id) AS document_count
            FROM meetings m
            LEFT JOIN meeting_document_links md ON md.meeting_id = m.id
            WHERE m.id = ?
            GROUP BY m.id
            """,
            [meeting_id],
        )
        documents = fetch_all(
            conn,
            """
            SELECT d.id, d.title, d.path, d.tags, d.created_at, d.text_size, d.text_format
            FROM documents d
            INNER JOIN meeting_document_links md ON md.document_id = d.id
            WHERE md.meeting_id = ?
            ORDER BY datetime(d.created_at) DESC
            """,
            [meeting_id],
        )
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return templates.TemplateResponse(
        "meeting_detail.html",
        {
            "request": request,
            "meeting": meeting,
            "documents": documents,
        },
    )
