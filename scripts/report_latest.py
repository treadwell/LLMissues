import argparse
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "iimcs.sqlite3"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def print_section(title: str) -> None:
    print("\n" + title)
    print("-" * len(title))


def main() -> None:
    parser = argparse.ArgumentParser(description="Report latest issue activity.")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=50, help="Max rows per section")
    args = parser.parse_args()

    with get_connection() as conn:
        print_section("Meetings in range")
        meetings = conn.execute(
            """
            SELECT id, meeting_date, title, source_tag
            FROM meetings
            WHERE meeting_date BETWEEN ? AND ?
            ORDER BY meeting_date ASC
            """,
            [args.start, args.end],
        ).fetchall()
        if not meetings:
            print("(none)")
        else:
            for m in meetings:
                print(f"{m['meeting_date']} | {m['title']} | {m['source_tag']}")

        print_section("LLM revisions")
        revisions = conn.execute(
            """
            SELECT i.id, i.title, r.field, r.created_at
            FROM issue_revisions r
            JOIN issues i ON i.id = r.issue_id
            WHERE r.actor = 'llm'
              AND date(r.created_at) BETWEEN ? AND ?
            ORDER BY datetime(r.created_at) DESC
            LIMIT ?
            """,
            [args.start, args.end, args.limit],
        ).fetchall()
        if not revisions:
            print("(none)")
        else:
            for r in revisions:
                print(f"{r['created_at']} | #{r['id']} {r['title']} | {r['field']}")

        print_section("Issues linked to meetings")
        linked = conn.execute(
            """
            SELECT DISTINCT i.id, i.title, m.meeting_date
            FROM issues i
            JOIN issue_meeting_links l ON l.issue_id = i.id
            JOIN meetings m ON m.id = l.meeting_id
            WHERE m.meeting_date BETWEEN ? AND ?
            ORDER BY m.meeting_date, i.id
            LIMIT ?
            """,
            [args.start, args.end, args.limit],
        ).fetchall()
        if not linked:
            print("(none)")
        else:
            for row in linked:
                print(f"{row['meeting_date']} | #{row['id']} {row['title']}")

        print_section("Most recently updated issues")
        recent = conn.execute(
            """
            SELECT id, title, status, updated_at
            FROM issues
            ORDER BY datetime(updated_at) DESC
            LIMIT ?
            """,
            [args.limit],
        ).fetchall()
        if not recent:
            print("(none)")
        else:
            for row in recent:
                print(f"{row['updated_at']} | #{row['id']} {row['title']} | {row['status']}")


if __name__ == "__main__":
    main()
