"""One-shot: copy every row from the old SQLite jobs.db into Postgres.

Run once, after the DB is up:
    docker compose up -d
    python migrate_sqlite_to_pg.py

Safe to re-run — rows whose job_url already exists are skipped.
"""
import sqlite3
from pathlib import Path

import database

SQLITE_PATH = Path(__file__).parent / "jobs.db"

COLUMNS = [
    "id", "first_seen_at", "scraped_at", "company_name", "job_title", "location",
    "job_url", "source_url", "ats_type", "requested_title", "date_posted", "status",
    "years_exp", "full_description", "detail_fetched_at", "resume_path",
    "resume_generated_at", "queued", "applied_at", "notes",
]


def main() -> None:
    if not SQLITE_PATH.exists():
        raise SystemExit(f"No SQLite DB at {SQLITE_PATH} — nothing to migrate.")

    database.init_db()

    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row
    rows = src.execute(f"SELECT {', '.join(COLUMNS)} FROM jobs").fetchall()
    src.close()

    pg = database.get_db()
    collist = ", ".join(COLUMNS)
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    inserted = 0
    with pg.cursor() as cur:
        for r in rows:
            cur.execute(
                f"INSERT INTO jobs ({collist}) VALUES ({placeholders}) "
                "ON CONFLICT (job_url) DO NOTHING",
                tuple(r[c] for c in COLUMNS),
            )
            inserted += cur.rowcount
        # Keep the SERIAL id sequence ahead of the imported (explicit) ids.
        cur.execute(
            "SELECT setval(pg_get_serial_sequence('jobs', 'id'), "
            "COALESCE((SELECT MAX(id) FROM jobs), 1))"
        )
    pg.commit()
    pg.close()
    print(f"Migrated {inserted} of {len(rows)} rows (existing job_urls skipped).")


if __name__ == "__main__":
    main()
