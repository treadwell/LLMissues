# Scripts

Planned CLI utilities:
- `ingest_calibre.py` tag-based ingestion from a Calibre library (reads `metadata.db` and `full-text-search.db`)
- `process_meeting_issues.py` extract issues from meeting transcripts and update the issue register

## Ingest usage

```bash
python scripts/ingest_calibre.py /path/to/Calibre\ Library
```

Or set `CALIBRE_LIBRARY_PATH` in `.env` and run:

```bash
python scripts/ingest_calibre.py
```

## Issue extraction usage

Set `OPENAI_API_KEY` and `CALIBRE_LIBRARY_PATH` in `.env` and run:

```bash
python scripts/process_meeting_issues.py --start 2026-02-01 --end 2026-02-04
```
