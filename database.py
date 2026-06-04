import os
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DATABASE_URL = (os.environ.get("DATABASE_URL")
                or "postgresql://jobsaver:jobsaver@localhost:5432/jobsaver")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  SERIAL PRIMARY KEY,
    first_seen_at       TEXT NOT NULL,
    scraped_at          TEXT NOT NULL,
    company_name        TEXT NOT NULL,
    job_title           TEXT NOT NULL,
    location            TEXT,
    job_url             TEXT NOT NULL UNIQUE,
    source_url          TEXT,
    ats_type            TEXT,
    requested_title     TEXT,
    date_posted         TEXT,
    status              TEXT,
    years_exp           INTEGER,
    full_description    TEXT,
    detail_fetched_at   TEXT,
    resume_path         TEXT,
    resume_generated_at TEXT,
    queued              INTEGER DEFAULT 0,
    applied_at          TEXT,
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS idx_company     ON jobs(company_name);
CREATE INDEX IF NOT EXISTS idx_first_seen  ON jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_date_posted ON jobs(date_posted);
CREATE INDEX IF NOT EXISTS idx_job_title   ON jobs(job_title);
CREATE INDEX IF NOT EXISTS idx_queued      ON jobs(queued);
"""


def get_db() -> psycopg2.extensions.connection:
    """Open a new Postgres connection. Callers own it and must .close() it."""
    return psycopg2.connect(DATABASE_URL)


def init_db() -> None:
    """Create the schema if it doesn't exist. Call once at startup."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ── Small query helpers ────────────────────────────────────────────────────────

def _dict_rows(conn, sql: str, params=()) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _dict_row(conn, sql: str, params=()) -> dict | None:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        r = cur.fetchone()
        return dict(r) if r else None


def _scalar(conn, sql: str, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


# ── Writes ──────────────────────────────────────────────────────────────────────

def insert_job(conn, job: dict) -> bool:
    """Insert only if job_url is new (ON CONFLICT DO NOTHING). Returns True if
    inserted. For existing jobs, fills in years_exp if it was previously unknown."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs
                (first_seen_at, scraped_at, company_name, job_title, location,
                 job_url, source_url, ats_type, requested_title, date_posted, years_exp,
                 full_description)
            VALUES
                (%(scraped_at)s, %(scraped_at)s, %(company_name)s, %(job_title)s, %(location)s,
                 %(job_url)s, %(source_url)s, %(ats_type)s, %(requested_title)s, %(date_posted)s,
                 %(years_exp)s, %(full_description)s)
            ON CONFLICT (job_url) DO NOTHING
            """,
            {**job, "years_exp": job.get("years_exp", 0),
             "full_description": job.get("full_description")},
        )
        inserted = cur.rowcount == 1
        if not inserted:
            cur.execute(
                "UPDATE jobs SET years_exp = %s WHERE job_url = %s AND years_exp IS NULL",
                (job.get("years_exp", 0), job["job_url"]),
            )
    conn.commit()
    return inserted


def update_job_status(conn, job_id: int, status: str | None) -> bool:
    with conn.cursor() as cur:
        if status == "applied":
            # Stamp applied_at once (preserve the first time it was marked applied).
            cur.execute(
                "UPDATE jobs SET status = 'applied', "
                "applied_at = COALESCE(applied_at, %s) WHERE id = %s",
                (_now(), job_id),
            )
        else:
            cur.execute("UPDATE jobs SET status = %s WHERE id = %s", (status, job_id))
        ok = cur.rowcount == 1
    conn.commit()
    return ok


def set_job_detail(conn, job_id: int, full_description: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET full_description = %s, detail_fetched_at = %s WHERE id = %s",
            (full_description, _now(), job_id),
        )
        ok = cur.rowcount == 1
    conn.commit()
    return ok


def set_job_queued(conn, job_id: int, queued: bool) -> bool:
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET queued = %s WHERE id = %s",
                    (1 if queued else 0, job_id))
        ok = cur.rowcount == 1
    conn.commit()
    return ok


def set_resume_path(conn, job_id: int, path: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET resume_path = %s, resume_generated_at = %s WHERE id = %s",
            (path, _now(), job_id),
        )
        ok = cur.rowcount == 1
    conn.commit()
    return ok


def insert_manual_job(conn, data: dict) -> int:
    """Insert a manually-added (applied-elsewhere) job. Returns new row id."""
    now = data.get("scraped_at") or _now()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs
                (first_seen_at, scraped_at, company_name, job_title, location,
                 job_url, source_url, ats_type, status, applied_at, queued, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'manual', 'applied', %s, 0, %s)
            RETURNING id
            """,
            (now, now, data["company_name"], data["job_title"], data.get("location"),
             data["job_url"], data.get("job_url"), now, data.get("notes")),
        )
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


# ── Reads ─────────────────────────────────────────────────────────────────────

def get_job(conn, job_id: int) -> dict | None:
    d = _dict_row(conn, "SELECT * FROM jobs WHERE id = %s", (job_id,))
    if d:
        d["source"] = _job_source(d)
    return d


def get_queued_jobs(conn) -> list[dict]:
    rows = _dict_rows(conn, "SELECT * FROM jobs WHERE queued = 1 ORDER BY first_seen_at DESC")
    for d in rows:
        d["source"] = _job_source(d)
    return rows


def get_applied_jobs(conn) -> list[dict]:
    rows = _dict_rows(conn, "SELECT * FROM jobs WHERE status = 'applied' ORDER BY applied_at DESC")
    for d in rows:
        d["source"] = _job_source(d)
    return rows


def get_applied_stats(conn) -> dict:
    total = _scalar(conn, "SELECT COUNT(*) FROM jobs WHERE status = 'applied'")
    by_company = _dict_rows(
        conn,
        "SELECT company_name AS company, COUNT(*) AS count FROM jobs "
        "WHERE status = 'applied' GROUP BY company_name ORDER BY count DESC",
    )
    # Bucket by week in Python so the format matches the dashboard's
    # datetime.strftime('%Y-W%W') comparison (avoids SQL-dialect week funcs).
    weeks: dict[str, int] = {}
    for d in _dict_rows(conn, "SELECT applied_at FROM jobs "
                              "WHERE status = 'applied' AND applied_at IS NOT NULL"):
        try:
            wk = datetime.fromisoformat(d["applied_at"]).strftime("%Y-W%W")
        except (ValueError, TypeError):
            continue
        weeks[wk] = weeks.get(wk, 0) + 1
    by_week = [{"week": w, "count": c} for w, c in sorted(weeks.items(), reverse=True)]
    return {"total": total, "by_company": by_company, "by_week": by_week}


def query_jobs(
    conn,
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
        conditions.append("company_name = %s")
        params.append(company)
    if companies:
        conditions.append(f"company_name IN ({','.join(['%s'] * len(companies))})")
        params.extend(companies)
    if sources:
        # A source is either an ATS platform (ats_type) or a JobSpy board (source_url).
        clauses = ["(ats_type = %s OR (ats_type = 'jobspy' AND LOWER(COALESCE(source_url, '')) = %s))"] * len(sources)
        conditions.append("(" + " OR ".join(clauses) + ")")
        for s in sources:
            params.extend([s, s])
    if title_keyword:
        # ILIKE keeps the case-insensitive matching SQLite's LIKE gave us.
        conditions.append("(job_title ILIKE %s OR requested_title ILIKE %s)")
        params.extend([f"%{title_keyword}%", f"%{title_keyword}%"])
    if since:
        conditions.append("first_seen_at >= %s")
        params.append(since)
    if posted_since:
        conditions.append("date_posted >= %s")
        params.append(posted_since)
    if max_exp is not None:
        conditions.append("COALESCE(years_exp, 0) < %s")
        params.append(max_exp)

    where = "WHERE " + " AND ".join(conditions)
    order = "first_seen_at DESC" if sort == "found" else "COALESCE(date_posted, first_seen_at) DESC"

    total = _scalar(conn, f"SELECT COUNT(*) FROM jobs {where}", params)
    rows = _dict_rows(
        conn,
        f"SELECT * FROM jobs {where} ORDER BY {order} LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    for d in rows:
        d["source"] = _job_source(d)
    return rows, total


def _job_source(job: dict) -> str:
    """Where the job came from: the board name for aggregator (JobSpy) rows,
    otherwise the ATS platform (workday, greenhouse, oracle, ...)."""
    if job.get("ats_type") == "jobspy":
        return (job.get("source_url") or "jobspy").lower()
    return job.get("ats_type") or "unknown"


def get_companies(conn) -> list[str]:
    rows = _dict_rows(conn, "SELECT DISTINCT company_name FROM jobs ORDER BY company_name")
    return [r["company_name"] for r in rows]


def get_sources(conn) -> list[str]:
    """Distinct job sources: JobSpy board names + ATS platform names."""
    rows = _dict_rows(
        conn,
        "SELECT DISTINCT CASE WHEN ats_type = 'jobspy' THEN LOWER(source_url) "
        "ELSE ats_type END AS src FROM jobs ORDER BY src",
    )
    return [r["src"] for r in rows if r["src"]]


def get_stats(conn) -> dict:
    active = "(status IS NULL OR status != 'skipped')"
    total = _scalar(conn, f"SELECT COUNT(*) FROM jobs WHERE {active}")
    company_count = _scalar(conn, f"SELECT COUNT(DISTINCT company_name) FROM jobs WHERE {active}")
    last_scraped = _scalar(conn, "SELECT MAX(scraped_at) FROM jobs")
    skipped_count = _scalar(conn, "SELECT COUNT(*) FROM jobs WHERE status = 'skipped'")
    return {
        "total_jobs": total,
        "company_count": company_count,
        "last_scraped": last_scraped,
        "skipped_count": skipped_count,
    }
