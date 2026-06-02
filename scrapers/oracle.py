"""Oracle HCM Cloud (Recruiting Cloud / ORC) scraper.

Many large employers (JPMorgan, Kroger, Dell, ...) run their careers site on
Oracle's CandidateExperience SPA, which loads jobs from the undocumented
recruitingCEJobRequisitions REST API. Zero browser — pure HTTP JSON.

The host + site number are derived from the company's career_url, which must be
the Oracle CX URL form:
    https://<host>.oraclecloud.com/hcmUI/CandidateExperience/en/sites/<SITE>/jobs
"""
import logging
import re
from urllib.parse import quote, urlparse

import aiohttp

from scrapers.generic import _title_matches, _parse_date, _job

logger = logging.getLogger(__name__)

ORACLE_MAX = 200  # cap results paged through per search title


def _oracle_endpoint(career_url: str) -> tuple[str | None, str | None, str | None]:
    """Derive (api_url, host, site_number) from an Oracle CX career URL."""
    parsed = urlparse(career_url)
    host = parsed.hostname or ""
    m = re.search(r"/sites/([A-Za-z0-9_]+)", career_url)
    if "oraclecloud.com" not in host or not m:
        return None, None, None
    site = m.group(1)
    api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    return api, host, site


async def _scrape_oracle_api(
    session: aiohttp.ClientSession,
    company_cfg: dict,
    search_titles: list[str],
) -> list[dict]:
    api, host, site = _oracle_endpoint(company_cfg["career_url"])
    if not api:
        logger.warning("[Oracle] Cannot derive API URL for %s", company_cfg["company_name"])
        return []

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    jobs: list[dict] = []

    for title in search_titles:
        offset = 0
        total = None
        while offset < ORACLE_MAX:
            finder = f"findReqs;siteNumber={site},keyword={quote(title)},sortBy=POSTING_DATES_DESC"
            url = (f"{api}?onlyData=true"
                   f"&expand=requisitionList.secondaryLocations,flexFieldsFacet.values"
                   f"&finder={finder}&limit=25&offset={offset}")
            try:
                async with session.get(url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.warning("[Oracle] %s HTTP %d for title=%s",
                                       company_cfg["company_name"], resp.status, title)
                        break
                    data = await resp.json(content_type=None)
            except Exception as exc:
                logger.error("[Oracle] %s %s: %s", company_cfg["company_name"], title, exc)
                break

            items = data.get("items", [])
            if not items:
                break
            block = items[0]
            reqs = block.get("requisitionList", [])
            if total is None:
                total = block.get("TotalJobsCount", 0)
            if not reqs:
                break

            for j in reqs:
                job_title = j.get("Title", "")
                matched, keyword = _title_matches(job_title, [title])
                if not matched:
                    continue
                job_id = j.get("Id", "")
                if not job_id:
                    continue
                job_url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}"
                location = j.get("PrimaryLocation", "")
                jobs.append(_job(company_cfg, job_title, location, job_url, keyword,
                                 _parse_date(j.get("PostedDate"))))

            offset += 25
            if offset >= total:
                break

    logger.info("[Oracle] %s → %d matches", company_cfg["company_name"], len(jobs))
    return jobs
