import logging
from datetime import datetime, timezone

import aiohttp

logger = logging.getLogger(__name__)

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def _title_matches(job_title: str, search_titles: list[str]) -> tuple[bool, str]:
    lower = job_title.lower()
    for title in search_titles:
        if title.lower() in lower:
            return True, title
    return False, ""


def _parse_date(raw_date: str | None) -> str | None:
    """Normalize Greenhouse ISO datetime to YYYY-MM-DD."""
    if not raw_date:
        return None
    try:
        return raw_date[:10]  # "2024-03-15T18:55:26.000Z" → "2024-03-15"
    except Exception:
        return None


def _normalize(raw: dict, company_cfg: dict, requested_title: str) -> dict:
    return {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "company_name": company_cfg["company_name"],
        "job_title": raw.get("title", ""),
        "location": (raw.get("location") or {}).get("name") or "",
        "job_url": raw.get("absolute_url", ""),
        "source_url": company_cfg["career_url"],
        "ats_type": "greenhouse",
        "requested_title": requested_title,
        "date_posted": _parse_date(raw.get("created_at") or raw.get("updated_at")),
    }


async def fetch_greenhouse_jobs(
    session: aiohttp.ClientSession,
    company_cfg: dict,
    search_titles: list[str],
) -> list[dict]:
    token = company_cfg["greenhouse_token"]
    url = GREENHOUSE_API.format(token=token)

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 404:
                logger.warning("[Greenhouse] Bad token for %s: %s", company_cfg["company_name"], token)
                return []
            if resp.status != 200:
                logger.warning("[Greenhouse] HTTP %d for %s", resp.status, company_cfg["company_name"])
                return []
            data = await resp.json()
    except Exception as exc:
        logger.error("[Greenhouse] Request failed for %s: %s", company_cfg["company_name"], exc)
        return []

    jobs = []
    for raw in data.get("jobs", []):
        matched, keyword = _title_matches(raw.get("title", ""), search_titles)
        if matched and raw.get("absolute_url"):
            jobs.append(_normalize(raw, company_cfg, keyword))

    logger.info("[Greenhouse] %s → %d matches", company_cfg["company_name"], len(jobs))
    return jobs
