from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config
from app.db import fetch_all, fetch_one, get_connection, init_db

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
    status: str = Query("", max_length=20),
    domain: str = Query("", max_length=100),
    owner: str = Query("", max_length=100),
):
    filters = {
        "status": status.strip(),
        "domain": domain.strip(),
        "owner": owner.strip(),
    }
    with get_connection() as conn:
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
        },
    )


@app.get("/issues", response_class=HTMLResponse)
async def issues_list(
    request: Request,
    status: str = Query("", max_length=20),
    domain: str = Query("", max_length=100),
    owner: str = Query("", max_length=100),
):
    filters = {
        "status": status.strip(),
        "domain": domain.strip(),
        "owner": owner.strip(),
    }
    with get_connection() as conn:
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
        },
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
    next_steps: str = Form(""),
    suggested_next_steps: str = Form(""),
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
            "next_steps": next_steps.strip(),
            "suggested_next_steps": suggested_next_steps.strip(),
        }

        changes: dict[str, Any] = {}
        for field, new_value in updates.items():
            old_value = existing[field]
            if str(old_value) != str(new_value):
                changes[field] = {"old": str(old_value), "new": str(new_value)}

        now = datetime.utcnow().isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE issues
            SET title = ?, domain = ?, owner = ?, status = ?, confidence = ?,
                situation = ?, complication = ?, resolution = ?, next_steps = ?,
                suggested_next_steps = ?,
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
                updates["next_steps"],
                updates["suggested_next_steps"],
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


@app.post("/issues/{issue_id}/accept-next-steps", response_class=HTMLResponse)
async def accept_next_steps(request: Request, issue_id: int):
    with get_connection() as conn:
        issue = fetch_one(
            conn,
            """
            SELECT * FROM issues WHERE id = ?
            """,
            [issue_id],
        )
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")

        suggested = (issue["suggested_next_steps"] or "").strip()
        if not suggested:
            return templates.TemplateResponse(
                "partials/issue_detail_form.html",
                {
                    "request": request,
                    "issue": issue,
                    "saved": False,
                },
            )

        now = datetime.utcnow().isoformat(timespec="seconds")
        updated_next_steps = (issue["next_steps"] or "")
        if updated_next_steps:
            updated_next_steps = f"{updated_next_steps}\n\n{suggested}"
        else:
            updated_next_steps = suggested

        conn.execute(
            """
            UPDATE issues
            SET next_steps = ?, suggested_next_steps = ?, updated_at = ?
            WHERE id = ?
            """,
            [updated_next_steps, "", now, issue_id],
        )
        conn.execute(
            """
            INSERT INTO issue_revisions (issue_id, field, old_value, new_value, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [issue_id, "next_steps", issue["next_steps"], updated_next_steps, "user", now],
        )
        conn.execute(
            """
            INSERT INTO issue_revisions (issue_id, field, old_value, new_value, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [issue_id, "suggested_next_steps", issue["suggested_next_steps"], "", "user", now],
        )
        conn.commit()

        updated_issue = fetch_one(
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
            "issue": updated_issue,
            "revisions": revisions,
            "saved": True,
        },
    )


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
