# Scripts

Planned CLI utilities:
- `ingest_calibre.py` tag-based ingestion from a Calibre library (reads `metadata.db` and `full-text-search.db`)

## Ingest usage

```bash
python scripts/ingest_calibre.py /path/to/Calibre\ Library
```

Or set `CALIBRE_LIBRARY_PATH` in `.env` and run:

```bash
python scripts/ingest_calibre.py
```
