"""
Scrapers for non-Greenhouse companies.
  - amazon:    Amazon Jobs JSON API
  - workday:   Workday REST JSON API (no Playwright needed)
  - generic:   Playwright fallback (Apple, Microsoft, etc.)
"""
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import aiohttp

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title_matches(job_title: str, search_titles: list[str]) -> tuple[bool, str]:
    lower = job_title.lower()
    for title in search_titles:
        if re.search(r'\b' + re.escape(title.lower()) + r'\b', lower):
            return True, title
    return False, ""


def _parse_date(value: str | None) -> str | None:
    """Try to extract YYYY-MM-DD from various date string formats."""
    if not value:
        return None
    # Already ISO-like: "2024-03-15" or "2024-03-15T..."
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    if m:
        return m.group(1)
    # "March 15, 2024" or "Mar 15, 2024"
    try:
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
    except Exception:
        pass
    return None


def _job(company_cfg: dict, title: str, location: str, url: str,
         requested: str, date_posted: str | None = None) -> dict:
    return {
        "scraped_at": _now(),
        "company_name": company_cfg["company_name"],
        "job_title": title,
        "location": location,
        "job_url": url,
        "source_url": company_cfg["career_url"],
        "ats_type": company_cfg["ats_type"],
        "requested_title": requested,
        "date_posted": date_posted,
    }


# ── Amazon ────────────────────────────────────────────────────────────────────

async def _scrape_amazon(
    session: aiohttp.ClientSession,
    company_cfg: dict,
    search_titles: list[str],
) -> list[dict]:
    jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for title in search_titles:
        url = company_cfg["search_url_template"].format(title=quote(title))
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning("[Amazon] HTTP %d for title=%s", resp.status, title)
                    continue
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("[Amazon] %s: %s", title, exc)
            continue

        for raw in data.get("jobs", []):
            job_title = raw.get("title", "")
            matched, keyword = _title_matches(job_title, [title])
            if not matched:
                continue
            job_url = f"https://www.amazon.jobs{raw.get('job_path', '')}" if raw.get("job_path") else ""
            if not job_url:
                continue
            location = raw.get("normalized_location") or raw.get("location", "")
            date_posted = _parse_date(raw.get("posted_date") or raw.get("updated_time"))
            jobs.append(_job(company_cfg, job_title, location, job_url, keyword, date_posted))

    logger.info("[Amazon] %d matches", len(jobs))
    return jobs


# ── Workday JSON API ──────────────────────────────────────────────────────────

def _location_from_workday_url(job_url: str) -> str:
    """Extract location from Workday URL slug when API returns 'X Locations'.

    .../job/Greece-Thessaloniki-Chortiatis/Sr-Manager... → "Greece Thessaloniki Chortiatis"
    .../job/United-States---Massachusetts---Cambridge/... → "United States   Massachusetts   Cambridge"
    """
    m = re.search(r'/job/([^/]+)/', job_url)
    if not m:
        return ""
    return m.group(1).replace("-", " ")


def _workday_api_url(career_url: str) -> str | None:
    """Derive the Workday JSON search endpoint from a career portal URL.

    https://fanniemae.wd1.myworkdayjobs.com/FannieMaeCareers
    → https://fanniemae.wd1.myworkdayjobs.com/wday/cxs/fanniemae/FannieMaeCareers/jobs
    """
    parsed = urlparse(career_url)
    if "myworkdayjobs.com" not in (parsed.hostname or ""):
        return None
    tenant = parsed.hostname.split(".")[0]
    board = parsed.path.strip("/").split("/")[0]
    return f"{parsed.scheme}://{parsed.hostname}/wday/cxs/{tenant}/{board}/jobs"


async def _scrape_workday_api(
    session: aiohttp.ClientSession,
    company_cfg: dict,
    search_titles: list[str],
) -> list[dict]:
    api_url = _workday_api_url(company_cfg["career_url"])
    if not api_url:
        logger.warning("[Workday] Cannot derive API URL for %s", company_cfg["company_name"])
        return []

    jobs = []
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    for title in search_titles:
        payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": title}
        try:
            async with session.post(
                api_url, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    logger.warning("[Workday] %s HTTP %d for title=%s", company_cfg["company_name"], resp.status, title)
                    continue
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("[Workday] %s %s: %s", company_cfg["company_name"], title, exc)
            continue

        for raw in data.get("jobPostings", []):
            job_title = raw.get("title", "")
            matched, keyword = _title_matches(job_title, [title])
            if not matched:
                continue
            path = raw.get("externalPath", "")
            if not path:
                continue
            job_url = company_cfg["career_url"].rstrip("/") + path
            location = raw.get("locationsText", "")
            if re.match(r'^\d+\s+Locations?$', location, re.IGNORECASE):
                location = _location_from_workday_url(job_url)
            date_raw = raw.get("postedOn", "")
            date_posted = _parse_date(date_raw) or _workday_relative_date(date_raw)
            jobs.append(_job(company_cfg, job_title, location, job_url, keyword, date_posted))

    logger.info("[Workday] %s → %d matches", company_cfg["company_name"], len(jobs))
    return jobs


# ── Apple ─────────────────────────────────────────────────────────────────────

async def _scrape_apple(
    session: aiohttp.ClientSession,
    company_cfg: dict,
    search_titles: list[str],
) -> list[dict]:
    jobs = []
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://jobs.apple.com/",
    }

    for title in search_titles:
        url = company_cfg["search_url_template"].format(title=quote(title))
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning("[Apple] HTTP %d for title=%s", resp.status, title)
                    continue
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("[Apple] %s: %s", title, exc)
            continue

        for raw in data.get("searchResults", []):
            job_title = raw.get("postingTitle") or raw.get("title", "")
            matched, keyword = _title_matches(job_title, [title])
            if not matched:
                continue
            posting_id = raw.get("positionId", "")
            job_url = f"https://jobs.apple.com/en-us/details/{posting_id}" if posting_id else ""
            if not job_url:
                continue
            loc = raw.get("location", {})
            location = loc.get("name", "") if isinstance(loc, dict) else str(loc)
            date_posted = _parse_date(
                raw.get("postingDate") or raw.get("publishedDate") or raw.get("modifiedDate")
            )
            jobs.append(_job(company_cfg, job_title, location, job_url, keyword, date_posted))

    logger.info("[Apple] %d matches", len(jobs))
    return jobs


# ── Microsoft ─────────────────────────────────────────────────────────────────

async def _scrape_microsoft(
    session: aiohttp.ClientSession,
    company_cfg: dict,
    search_titles: list[str],
) -> list[dict]:
    jobs = []
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    for title in search_titles:
        url = company_cfg["search_url_template"].format(title=quote(title))
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning("[Microsoft] HTTP %d for title=%s", resp.status, title)
                    continue
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("[Microsoft] %s: %s", title, exc)
            continue

        result = data.get("operationResult", {}).get("result", {})
        for raw in result.get("jobs", []):
            job_title = raw.get("title", "")
            matched, keyword = _title_matches(job_title, [title])
            if not matched:
                continue
            job_id = raw.get("jobId", "")
            job_url = f"https://jobs.careers.microsoft.com/global/en/job/{job_id}/" if job_id else ""
            if not job_url:
                continue
            location = raw.get("primaryWorkLocation", "") or raw.get("workSiteFlexibility", "")
            date_posted = _parse_date(raw.get("postedDate") or raw.get("postDate"))
            jobs.append(_job(company_cfg, job_title, location, job_url, keyword, date_posted))

    logger.info("[Microsoft] %d matches", len(jobs))
    return jobs


# ── Workday (Playwright) ──────────────────────────────────────────────────────

async def _scrape_workday(page, company_cfg: dict, search_titles: list[str]) -> list[dict]:
    jobs = []

    for title in search_titles:
        url = company_cfg["search_url_template"].format(title=quote(title))
        try:
            await page.goto(url, timeout=45000, wait_until="networkidle")
            await page.wait_for_timeout(2000)

            cards = await page.query_selector_all("li[data-automation-id='compositeContainer']")
            if not cards:
                # Fallback: grab individual jobTitle elements as single-item "cards"
                cards = await page.query_selector_all("[data-automation-id='jobTitle']")

            for card in cards:
                title_el = await card.query_selector("[data-automation-id='jobTitle']") or card
                job_title = (await title_el.inner_text()).strip()

                matched, keyword = _title_matches(job_title, [title])
                if not matched:
                    continue

                link = await card.query_selector("a")
                href = await link.get_attribute("href") if link else None
                if not href:
                    continue
                job_url = href if href.startswith("http") else company_cfg["career_url"].rstrip("/") + href

                loc_el = await card.query_selector(
                    "[data-automation-id='workerSubtypeProfile'], "
                    "[data-automation-id='location'], "
                    "[data-automation-id='jobPostingLocationText']"
                )
                location = (await loc_el.inner_text()).strip() if loc_el else ""

                # Workday sometimes shows "Posted X days ago" or a date string
                date_el = await card.query_selector(
                    "[data-automation-id='postedOn'], "
                    "[data-automation-id='date']"
                )
                date_posted = None
                if date_el:
                    date_text = (await date_el.inner_text()).strip()
                    date_posted = _parse_date(date_text) or _workday_relative_date(date_text)

                jobs.append(_job(company_cfg, job_title, location, job_url, keyword, date_posted))
        except Exception as exc:
            logger.error("[Workday] %s title=%s: %s", company_cfg["company_name"], title, exc)

    logger.info("[Workday] %s → %d matches", company_cfg["company_name"], len(jobs))
    return jobs


def _workday_relative_date(text: str) -> str | None:
    """Convert 'Posted 3 days ago' → approximate YYYY-MM-DD."""
    from datetime import timedelta
    text = text.lower()
    m = re.search(r"(\d+)\s+day", text)
    if m:
        return (datetime.now(timezone.utc) - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    if "today" in text or "just now" in text:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "yesterday" in text:
        from datetime import timedelta
        return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    return None


# ── Generic Playwright fallback ───────────────────────────────────────────────

async def _scrape_generic_playwright(
    page, company_cfg: dict, search_titles: list[str]
) -> list[dict]:
    jobs = []
    template = company_cfg.get("search_url_template")
    if not template:
        logger.info("[Generic] No search_url_template for %s, skipping", company_cfg["company_name"])
        return []

    for title in search_titles:
        url = template.format(title=quote(title))
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            links = await page.query_selector_all("a[href]")
            seen: set[str] = set()

            for link in links:
                href = await link.get_attribute("href") or ""
                if not any(k in href for k in ("/job/", "/jobs/", "/careers/", "/position/", "/opening/")):
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

                jobs.append(_job(company_cfg, job_title, "", job_url, keyword))
        except Exception as exc:
            logger.error("[Generic] %s title=%s: %s", company_cfg["company_name"], title, exc)

    logger.info("[Generic] %s → %d matches", company_cfg["company_name"], len(jobs))
    return jobs


# ── Ashby ─────────────────────────────────────────────────────────────────────

async def _scrape_ashby(
    session: aiohttp.ClientSession,
    company_cfg: dict,
    search_titles: list[str],
) -> list[dict]:
    from urllib.parse import urlparse, unquote
    slug = unquote(urlparse(company_cfg["career_url"]).path.lstrip("/"))
    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"

    try:
        async with session.get(
            api_url,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                logger.warning("[Ashby] HTTP %d for %s", resp.status, company_cfg["company_name"])
                return []
            data = await resp.json(content_type=None)
    except Exception as exc:
        logger.error("[Ashby] %s: %s", company_cfg["company_name"], exc)
        return []

    jobs = []
    for raw in data.get("jobs", []):
        job_title = raw.get("title", "")
        matched, keyword = _title_matches(job_title, search_titles)
        if not matched:
            continue
        job_url = raw.get("jobUrl", "")
        if not job_url:
            continue
        location = raw.get("location", "")
        date_posted = _parse_date(raw.get("publishedAt") or raw.get("updatedAt"))
        jobs.append(_job(company_cfg, job_title, location, job_url, keyword, date_posted))

    logger.info("[Ashby] %s → %d matches", company_cfg["company_name"], len(jobs))
    return jobs


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def scrape_company(
    session: aiohttp.ClientSession,
    playwright_browser,
    company_cfg: dict,
    search_titles: list[str],
) -> list[dict]:
    ats = company_cfg["ats_type"]

    if ats == "amazon":
        return await _scrape_amazon(session, company_cfg, search_titles)
    if ats == "workday":
        return await _scrape_workday_api(session, company_cfg, search_titles)
    if ats == "ashby":
        return await _scrape_ashby(session, company_cfg, search_titles)

    if playwright_browser is None:
        logger.warning("[Generic] No Playwright browser for %s, skipping", company_cfg["company_name"])
        return []

    context = await playwright_browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )
    page = await context.new_page()
    try:
        if ats == "workday":
            return await _scrape_workday(page, company_cfg, search_titles)
        return await _scrape_generic_playwright(page, company_cfg, search_titles)
    finally:
        await context.close()
