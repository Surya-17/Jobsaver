import asyncio
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import aiohttp

from config import COMPANIES, CONCURRENCY_LIMIT, SEARCH_TITLES, TEST_COMPANIES
from database import get_db, insert_job
from scrapers.greenhouse import fetch_greenhouse_jobs
from scrapers.generic import scrape_company
from scrapers.exp_parser import infer_exp

logger = logging.getLogger(__name__)

_scrape_lock = asyncio.Lock()

_NON_US = [
    "india", "uk", "united kingdom", "canada", "australia", "germany",
    "france", "spain", "ireland", "poland", "luxembourg", "singapore",
    "netherlands", "sweden", "switzerland", "italy", "japan", "china",
    "brazil", "mexico", "sri lanka", "hyderabad", "bengaluru", "bangalore",
    "mumbai", "pune", "delhi", "chennai", "kolkata", "noida",
    "sydney", "toronto", "london", "paris", "berlin", "amsterdam",
    "madrid", "dublin", "warsaw", "zurich", "prague", "budapest",
    "greece", "thessaloniki", "athens", "belgium", "brussels", "austria",
    "vienna", "denmark", "copenhagen", "finland", "helsinki", "norway",
    "oslo", "portugal", "lisbon", "romania", "bucharest", "turkey",
    "istanbul", "israel", "tel aviv", "south korea", "seoul", "taiwan",
    "taipei", "hong kong", "dubai", "uae", "russia", "moscow",
]

# Titles that strongly signal director-level+ (typically 8-10+ yrs required)
_SENIOR_PAT = re.compile(
    r'\b(director|vice\s+president|\bvp\b|svp|evp|chief\b|cto|cio|cdo|ciso|'
    r'head\s+of|managing\s+director|managing\s+partner)\b',
    re.IGNORECASE,
)

# Titles that are explicitly not full-time
_NON_FULLTIME_PAT = re.compile(
    r'\b(contract|contractor|part[- ]time|temporary|\btemp\b|intern\b|'
    r'internship|co-?op|freelance|contingent)\b',
    re.IGNORECASE,
)


def _is_us_location(location: str, job_url: str = "") -> bool:
    check = (location + " " + job_url).lower()
    if not check.strip():
        return True
    return not any(kw in check for kw in _NON_US)


def _is_eligible(job_title: str) -> bool:
    """Return False for director-and-above or non-full-time roles."""
    return not _SENIOR_PAT.search(job_title) and not _NON_FULLTIME_PAT.search(job_title)
_scrape_running = False
_last_run: dict | None = None


def is_running() -> bool:
    return _scrape_running


def get_last_run() -> dict | None:
    return _last_run


async def _scrape_playwright_title(
    context,
    company_cfg: dict,
    title: str,
) -> list[dict]:
    from urllib.parse import quote as _quote
    from scrapers.generic import _title_matches

    url = company_cfg["search_url_template"].format(title=_quote(title))
    page = await context.new_page()
    jobs = []
    _job_patterns = ("/job/", "/jobs/", "/careers/", "/position/", "/details/", "/opening/", "/requisition/")
    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(
                "a[href*='/job/'],a[href*='/jobs/'],a[href*='/careers/'],a[href*='/position/']",
                timeout=8000,
            )
        except Exception:
            pass

        links = await page.query_selector_all("a[href]")
        seen: set[str] = set()
        for link in links:
            href = await link.get_attribute("href") or ""
            if not any(k in href for k in _job_patterns):
                continue
            job_url = href if href.startswith("http") else company_cfg["career_url"].rstrip("/") + href
            if job_url in seen:
                continue
            seen.add(job_url)
            job_title = (await link.inner_text()).strip()
            if not job_title or len(job_title) > 200:
                continue
            matched, keyword = _title_matches(job_title, [title])
            if not matched:
                continue
            jobs.append({
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "company_name": company_cfg["company_name"],
                "job_title": job_title,
                "location": "",
                "job_url": job_url,
                "source_url": company_cfg["career_url"],
                "ats_type": company_cfg["ats_type"],
                "requested_title": keyword,
                "date_posted": None,
            })
    except Exception as exc:
        logger.error("[Playwright] %s title=%s: %s", company_cfg["company_name"], title, exc)
    finally:
        await page.close()
    return jobs


async def _scrape_playwright_company_async(
    company_cfg: dict,
    search_titles: list[str],
) -> list[dict]:
    from playwright.async_api import async_playwright

    if not company_cfg.get("search_url_template"):
        logger.info("[Playwright] No search_url_template for %s, skipping", company_cfg["company_name"])
        return []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            results = await asyncio.gather(
                *[_scrape_playwright_title(context, company_cfg, t) for t in search_titles],
                return_exceptions=True,
            )
        finally:
            await context.close()
            await browser.close()

    jobs: list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("[Playwright] %s: %s", company_cfg["company_name"], r)
        else:
            jobs.extend(r)

    logger.info("[Playwright] %s → %d matches", company_cfg["company_name"], len(jobs))
    return jobs


def _run_playwright_company_sync(company_cfg: dict, search_titles: list[str]) -> list[dict]:
    """Runs in a ThreadPoolExecutor thread with its own event loop (avoids Windows ProactorEventLoop)."""
    return asyncio.run(_scrape_playwright_company_async(company_cfg, search_titles))


async def _scrape_one(
    sem: asyncio.Semaphore,
    session: aiohttp.ClientSession,
    executor: ThreadPoolExecutor,
    company_cfg: dict,
    search_titles: list[str],
    db_conn,
) -> tuple[int, int, str | None]:
    async with sem:
        try:
            ats = company_cfg["ats_type"]
            if ats == "greenhouse":
                jobs = await fetch_greenhouse_jobs(session, company_cfg, search_titles)
            elif ats == "generic":
                # Generic pages are JS-heavy SPAs — run sync Playwright in thread pool
                # to avoid Windows ProactorEventLoop subprocess limitation
                jobs = await asyncio.get_event_loop().run_in_executor(
                    executor,
                    _run_playwright_company_sync,
                    company_cfg,
                    search_titles,
                )
            else:
                # amazon, workday → async JSON API scrapers (no Playwright needed)
                jobs = await scrape_company(session, None, company_cfg, search_titles)

            jobs = [j for j in jobs if _is_us_location(j.get("location", ""), j.get("job_url", ""))
                    and _is_eligible(j.get("job_title", ""))]
            for job in jobs:
                if "years_exp" not in job:
                    job["years_exp"] = infer_exp(job.get("job_title", ""))
            new_count = sum(1 for job in jobs if insert_job(db_conn, job))
            return new_count, len(jobs) - new_count, None

        except Exception as exc:
            logger.error("[Runner] %s failed: %s", company_cfg["company_name"], exc)
            return 0, 0, str(exc)


async def run_scrape(
    company_filter: str | None = None,
    titles: list[str] | None = None,
    test_mode: bool = False,
) -> dict:
    global _scrape_running, _last_run

    async with _scrape_lock:
        if _scrape_running:
            return {"error": "Scrape already running"}
        _scrape_running = True

    started_at = datetime.now(timezone.utc)
    search_titles = titles or SEARCH_TITLES
    companies = [c for c in COMPANIES if c["enabled"]]
    if test_mode:
        companies = [c for c in companies if c["company_name"] in TEST_COMPANIES]
        logger.info("[Runner] Test mode: scraping %d companies", len(companies))
    elif company_filter:
        companies = [c for c in companies if c["company_name"].lower() == company_filter.lower()]

    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
    db_conn = get_db()
    executor = ThreadPoolExecutor(max_workers=CONCURRENCY_LIMIT)

    total_new = 0
    total_updated = 0
    errors: list[dict] = []

    try:
        async with aiohttp.ClientSession() as session:
            tasks = [
                _scrape_one(sem, session, executor, company, search_titles, db_conn)
                for company in companies
            ]
            results = await asyncio.gather(*tasks, return_exceptions=False)

        for company, (new, updated, err) in zip(companies, results):
            total_new += new
            total_updated += updated
            if err:
                errors.append({"company": company["company_name"], "error": err})

    finally:
        executor.shutdown(wait=False)
        db_conn.close()
        _scrape_running = False

    _last_run = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "companies_scraped": len(companies) - len(errors),
        "companies_failed": len(errors),
        "total_new": total_new,
        "total_updated": total_updated,
        "errors": errors,
    }
    logger.info("[Runner] Done. new=%d updated=%d failed=%d", total_new, total_updated, len(errors))
    return _last_run
