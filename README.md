# LLMissues (IIMCS)

Issue Intelligence & Meeting Continuity System.

## Local dev (backend)

1. Create a virtual environment and install deps.
2. Run the server.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open: http://127.0.0.1:8000

## Environment

Copy `.env.example` to `.env` and fill in values as needed.

## Structure

- `backend/` FastAPI server + HTMX templates
- `data/` Local SQLite DB (git-ignored)
- `docs/` Design notes
- `scripts/` Utilities
