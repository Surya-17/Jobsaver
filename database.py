import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "jobs.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

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
    "ALTER TABLE jobs ADD COLUMN status TEXT",
    "ALTER TABLE jobs ADD COLUMN years_exp INTEGER",
    "ALTER TABLE jobs ADD COLUMN full_description TEXT",
    "ALTER TABLE jobs ADD COLUMN detail_fetched_at TEXT",
    "ALTER TABLE jobs ADD COLUMN resume_path TEXT",
    "ALTER TABLE jobs ADD COLUMN resume_generated_at TEXT",
    "ALTER TABLE jobs ADD COLUMN queued INTEGER DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN applied_at TEXT",
    "ALTER TABLE jobs ADD COLUMN notes TEXT",
    # Index created here (not in SCHEMA) so it runs after the queued column exists.
    "CREATE INDEX IF NOT EXISTS idx_queued ON jobs(queued)",
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
    """INSERT OR IGNORE — only inserts if job_url is new. Returns True if inserted.
    For existing jobs, updates years_exp if it was previously unknown."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO jobs
            (first_seen_at, scraped_at, company_name, job_title, location,
             job_url, source_url, ats_type, requested_title, date_posted, years_exp,
             full_description)
        VALUES
            (:scraped_at, :scraped_at, :company_name, :job_title, :location,
             :job_url, :source_url, :ats_type, :requested_title, :date_posted, :years_exp,
             :full_description)
        """,
        {**job, "years_exp": job.get("years_exp", 0),
         "full_description": job.get("full_description")},
    )
    if cursor.rowcount == 0:
        conn.execute(
            "UPDATE jobs SET years_exp = ? WHERE job_url = ? AND years_exp IS NULL",
            (job.get("years_exp", 0), job["job_url"]),
        )
    conn.commit()
    return cursor.rowcount == 1


def update_job_status(conn: sqlite3.Connection, job_id: int, status: str | None) -> bool:
    if status == "applied":
        # Stamp applied_at once (preserve the first time it was marked applied).
        cursor = conn.execute(
            "UPDATE jobs SET status = 'applied', "
            "applied_at = COALESCE(applied_at, datetime('now')) WHERE id = ?",
            (job_id,),
        )
    else:
        cursor = conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    conn.commit()
    return cursor.rowcount == 1


def set_job_detail(conn: sqlite3.Connection, job_id: int, full_description: str) -> bool:
    cursor = conn.execute(
        "UPDATE jobs SET full_description = ?, detail_fetched_at = datetime('now') WHERE id = ?",
        (full_description, job_id),
    )
    conn.commit()
    return cursor.rowcount == 1


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["source"] = _job_source(d)
    return d


def set_job_queued(conn: sqlite3.Connection, job_id: int, queued: bool) -> bool:
    cursor = conn.execute(
        "UPDATE jobs SET queued = ? WHERE id = ?", (1 if queued else 0, job_id)
    )
    conn.commit()
    return cursor.rowcount == 1


def get_queued_jobs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE queued = 1 ORDER BY first_seen_at DESC"
    ).fetchall()
    jobs = []
    for r in rows:
        d = dict(r)
        d["source"] = _job_source(d)
        jobs.append(d)
    return jobs


def set_resume_path(conn: sqlite3.Connection, job_id: int, path: str) -> bool:
    cursor = conn.execute(
        "UPDATE jobs SET resume_path = ?, resume_generated_at = datetime('now') WHERE id = ?",
        (path, job_id),
    )
    conn.commit()
    return cursor.rowcount == 1


def insert_manual_job(conn: sqlite3.Connection, data: dict) -> int:
    """Insert a manually-added (applied-elsewhere) job. Returns new row id."""
    now = data.get("scraped_at") or _now()
    cursor = conn.execute(
        """
        INSERT INTO jobs
            (first_seen_at, scraped_at, company_name, job_title, location,
             job_url, source_url, ats_type, status, applied_at, queued, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', 'applied', ?, 0, ?)
        """,
        (now, now, data["company_name"], data["job_title"], data.get("location"),
         data["job_url"], data.get("job_url"), now, data.get("notes")),
    )
    conn.commit()
    return cursor.lastrowid


def get_applied_jobs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'applied' ORDER BY applied_at DESC"
    ).fetchall()
    jobs = []
    for r in rows:
        d = dict(r)
        d["source"] = _job_source(d)
        jobs.append(d)
    return jobs


def get_applied_stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'").fetchone()[0]
    by_company = [
        {"company": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT company_name, COUNT(*) c FROM jobs WHERE status = 'applied' "
            "GROUP BY company_name ORDER BY c DESC"
        ).fetchall()
    ]
    by_week = [
        {"week": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT strftime('%Y-W%W', applied_at) w, COUNT(*) c FROM jobs "
            "WHERE status = 'applied' AND applied_at IS NOT NULL GROUP BY w ORDER BY w DESC"
        ).fetchall()
    ]
    return {"total": total, "by_company": by_company, "by_week": by_week}


def query_jobs(
    conn: sqlite3.Connection,
    company: str | None = None,
    companies: list[str] | None = None,
    sources: list[str] | None = None,
    title_keyword: str | None = None,
    since: str | None = None,
    posted_since: str | None = None,
    limit: int = 50,
    offset: int = 0,
    view: str = "active",
    sort: str = "posted",
    max_exp: int | None = None,
) -> tuple[list[dict], int]:
    conditions: list[str] = []
    params: list = []

    if view == "skipped":
        conditions.append("status = 'skipped'")
    else:
        conditions.append("(status IS NULL OR status != 'skipped')")

    if company:
        conditions.append("company_name = ?")
        params.append(company)
    if companies:
        conditions.append(f"company_name IN ({','.join('?' * len(companies))})")
        params.extend(companies)
    if sources:
        # A source is either an ATS platform (ats_type) or a JobSpy board (source_url).
        clauses = ["(ats_type = ? OR (ats_type = 'jobspy' AND LOWER(COALESCE(source_url, '')) = ?))"] * len(sources)
        conditions.append("(" + " OR ".join(clauses) + ")")
        for s in sources:
            params.extend([s, s])
    if title_keyword:
        conditions.append("(job_title LIKE ? OR requested_title LIKE ?)")
        params.extend([f"%{title_keyword}%", f"%{title_keyword}%"])
    if since:
        conditions.append("first_seen_at >= ?")
        params.append(since)
    if posted_since:
        conditions.append("date_posted >= ?")
        params.append(posted_since)
    if max_exp is not None:
        conditions.append("COALESCE(years_exp, 0) < ?")
        params.append(max_exp)

    where = "WHERE " + " AND ".join(conditions)
    order = "first_seen_at DESC" if sort == "found" else "COALESCE(date_posted, first_seen_at) DESC"

    total = conn.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM jobs {where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    jobs = []
    for r in rows:
        d = dict(r)
        d["source"] = _job_source(d)
        jobs.append(d)
    return jobs, total


def _job_source(job: dict) -> str:
    """Where the job came from: the board name for aggregator (JobSpy) rows,
    otherwise the ATS platform (workday, greenhouse, oracle, ...)."""
    if job.get("ats_type") == "jobspy":
        return (job.get("source_url") or "jobspy").lower()
    return job.get("ats_type") or "unknown"


def get_companies(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT company_name FROM jobs ORDER BY company_name"
    ).fetchall()
    return [r[0] for r in rows]


def get_sources(conn: sqlite3.Connection) -> list[str]:
    """Distinct job sources: JobSpy board names + ATS platform names."""
    rows = conn.execute(
        "SELECT DISTINCT CASE WHEN ats_type = 'jobspy' THEN LOWER(source_url) "
        "ELSE ats_type END AS src FROM jobs ORDER BY src"
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def get_stats(conn: sqlite3.Connection) -> dict:
    active = "(status IS NULL OR status != 'skipped')"
    total = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {active}").fetchone()[0]
    company_count = conn.execute(f"SELECT COUNT(DISTINCT company_name) FROM jobs WHERE {active}").fetchone()[0]
    last_scraped = conn.execute("SELECT MAX(scraped_at) FROM jobs").fetchone()[0]
    skipped_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'skipped'").fetchone()[0]
    return {
        "total_jobs": total,
        "company_count": company_count,
        "last_scraped": last_scraped,
        "skipped_count": skipped_count,
    }
