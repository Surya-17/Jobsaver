import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    first_seen_at   TEXT NOT NULL,
    scraped_at      TEXT NOT NULL,
    company_name    TEXT NOT NULL,
    job_title       TEXT NOT NULL,
    location        TEXT,
    job_url         TEXT NOT NULL UNIQUE,
    source_url      TEXT,
    ats_type        TEXT,
    requested_title TEXT,
    date_posted     TEXT
);
CREATE INDEX IF NOT EXISTS idx_company      ON jobs(company_name);
CREATE INDEX IF NOT EXISTS idx_first_seen   ON jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_date_posted  ON jobs(date_posted);
CREATE INDEX IF NOT EXISTS idx_job_title    ON jobs(job_title);
"""

# Migration: add columns that may not exist in older DBs
MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN date_posted TEXT",
    "ALTER TABLE jobs ADD COLUMN first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))",
]


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    _run_migrations(conn)
    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists


def insert_job(conn: sqlite3.Connection, job: dict) -> bool:
    """INSERT OR IGNORE — only inserts if job_url is new. Returns True if inserted."""
    now = job.get("scraped_at", "")
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO jobs
            (first_seen_at, scraped_at, company_name, job_title, location,
             job_url, source_url, ats_type, requested_title, date_posted)
        VALUES
            (:scraped_at, :scraped_at, :company_name, :job_title, :location,
             :job_url, :source_url, :ats_type, :requested_title, :date_posted)
        """,
        job,
    )
    conn.commit()
    return cursor.rowcount == 1


def query_jobs(
    conn: sqlite3.Connection,
    company: str | None = None,
    title_keyword: str | None = None,
    since: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (jobs, total_count) ordered by date_posted desc, then first_seen_at desc."""
    conditions = []
    params: list = []

    if company:
        conditions.append("company_name = ?")
        params.append(company)
    if title_keyword:
        conditions.append("(job_title LIKE ? OR requested_title LIKE ?)")
        params.extend([f"%{title_keyword}%", f"%{title_keyword}%"])
    if since:
        conditions.append("first_seen_at >= ?")
        params.append(since)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]

    rows = conn.execute(
        f"""SELECT * FROM jobs {where}
            ORDER BY COALESCE(date_posted, first_seen_at) DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()

    return [dict(r) for r in rows], total


def get_companies(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT company_name FROM jobs ORDER BY company_name"
    ).fetchall()
    return [r[0] for r in rows]


def get_stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    company_count = conn.execute("SELECT COUNT(DISTINCT company_name) FROM jobs").fetchone()[0]
    last_scraped = conn.execute("SELECT MAX(scraped_at) FROM jobs").fetchone()[0]
    return {
        "total_jobs": total,
        "company_count": company_count,
        "last_scraped": last_scraped,
    }
