"""JobSpy aggregator source: pulls jobs from Indeed, LinkedIn, Glassdoor,
Google, and ZipRecruiter.

Unlike the per-company scrapers, this hits job-board aggregators that already
index most employers, so one source covers far more ground. JobSpy is a blocking
(requests-based) library — call fetch_jobspy_jobs from a thread-pool executor.

Each (site, title) pair is queried separately and wrapped in retry/try-except,
so one board throttling doesn't lose results from the others. Returns the same
normalized job dicts the rest of the pipeline uses, so the runner's
location/eligibility filters and insert_job path apply unchanged.
"""
import logging
import time
from datetime import datetime, timezone

from scrapers.exp_parser import infer_exp
from scrapers.generic import _title_matches

logger = logging.getLogger(__name__)

# Indeed and LinkedIn work without a proxy. Glassdoor/Google/ZipRecruiter are
# blocked (Cloudflare 403 / empty results) from a plain residential/datacenter IP,
# so they're only attempted when JOBSPY_PROXIES is set — otherwise they'd add a
# minute of guaranteed-empty, error-spamming calls to every scrape.
JOBSPY_SITES = ["indeed", "linkedin"]
JOBSPY_PROXY_SITES = ["glassdoor", "google", "zip_recruiter"]
JOBSPY_LOCATION = "United States"
JOBSPY_RESULTS_PER_TITLE = 100
JOBSPY_HOURS_OLD = 168  # 7 days
JOBSPY_PROXIES: list[str] | None = None  # e.g. ["user:pass@host:port"]

_MAX_RETRIES = 2
_BACKOFF = 5.0


def _clean(value) -> str:
    """pandas cells come back as float('nan') when empty — normalize to ''."""
    s = str(value) if value is not None else ""
    return "" if s == "nan" else s.strip()


def _date_posted(value) -> str | None:
    """JobSpy 'date_posted' is a date/Timestamp/NaT — return YYYY-MM-DD or None."""
    import pandas as pd

    if value is None or pd.isna(value):
        return None
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return None


def _scrape_site_title(site: str, title: str):
    """Call scrape_jobs for one board + title, retrying transient failures.

    Returns a JobSpy DataFrame, or None if the board failed.
    """
    from jobspy import scrape_jobs

    kwargs = {
        "site_name": [site],
        "search_term": title,
        "location": JOBSPY_LOCATION,
        "results_wanted": JOBSPY_RESULTS_PER_TITLE,
        "hours_old": JOBSPY_HOURS_OLD,
        "country_indeed": "usa",
        "description_format": "markdown",
        "verbose": 0,
    }
    if site == "google":
        # Google's scraper needs a full natural-language query to return much.
        kwargs["google_search_term"] = f"{title} jobs in {JOBSPY_LOCATION}"
    if JOBSPY_PROXIES:
        kwargs["proxies"] = JOBSPY_PROXIES

    for attempt in range(_MAX_RETRIES + 1):
        try:
            return scrape_jobs(**kwargs)
        except Exception as exc:
            err = str(exc).lower()
            transient = any(k in err for k in
                            ("timeout", "429", "proxy", "connection", "reset", "refused"))
            if transient and attempt < _MAX_RETRIES:
                wait = _BACKOFF * (attempt + 1)
                logger.warning("[JobSpy] %s '%s' retry %d/%d in %.0fs: %s",
                               site, title, attempt + 1, _MAX_RETRIES, wait, exc)
                time.sleep(wait)
            else:
                logger.error("[JobSpy] %s '%s' failed: %s", site, title, exc)
                return None


def fetch_jobspy_jobs(search_titles: list[str]) -> list[dict]:
    """Blocking. Search each title across all boards and return normalized dicts.

    Applies the same word-boundary title match as the other scrapers, so the
    boards' fuzzy search results are held to the same standard. Deduplicates by
    URL within the run (the same posting can surface for multiple titles/boards).
    """
    now = datetime.now(timezone.utc).isoformat()
    jobs: list[dict] = []
    seen: set[str] = set()

    sites = JOBSPY_SITES + (JOBSPY_PROXY_SITES if JOBSPY_PROXIES else [])
    for site in sites:
        site_count = 0
        for title in search_titles:
            df = _scrape_site_title(site, title)
            if df is None:
                continue
            for _, row in df.iterrows():
                url = _clean(row.get("job_url"))
                job_title = _clean(row.get("title"))
                if not url or not job_title or url in seen:
                    continue
                matched, keyword = _title_matches(job_title, [title])
                if not matched:
                    continue
                seen.add(url)
                description = _clean(row.get("description"))
                jobs.append({
                    "scraped_at": now,
                    "company_name": _clean(row.get("company")) or "Unknown",
                    "job_title": job_title,
                    "location": _clean(row.get("location")),
                    "job_url": url,
                    "source_url": _clean(row.get("site")) or site,
                    "ats_type": "jobspy",
                    "requested_title": keyword,
                    "date_posted": _date_posted(row.get("date_posted")),
                    "years_exp": infer_exp(job_title, description or None),
                    "full_description": description or None,
                })
                site_count += 1
        logger.info("[JobSpy] %s → %d matches", site, site_count)

    logger.info("[JobSpy] %d total matches across %d boards", len(jobs), len(sites))
    return jobs
