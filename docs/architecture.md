# Architecture Notes

## MVP Shape
- FastAPI server with HTMX + Jinja templates
- SQLite for issue register + provenance + revisions
- Calibre ingestion via CLI scripts

## Near-term endpoints
- GET `/` issue register
- GET `/issues/:id` issue detail
- POST `/issues` create draft
- POST `/issues/:id` update (SCR edits)

## Data model (draft)
- `issues`
- `issue_revisions`
- `documents`
- `meetings`
- `issue_document_links`
- `issue_meeting_links`
- `meeting_document_links`
