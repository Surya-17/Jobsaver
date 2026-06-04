"""On-demand full job-description fetcher.

Given a stored job row, fetch its full JD text. Dispatches by ats_type to the
source's detail API where one exists (greenhouse/workday/oracle), uses the
description already stored for jobspy rows, and falls back to fetching the
job_url page for everything else (generic/autodiscover/manual).
"""
import asyncio
import html
import logging
import re
from urllib.parse import urlparse

import aiohttp

from scrapers import llm
from scrapers.exp_parser import strip_html
from scrapers.oracle import _oracle_endpoint

logger = logging.getLogger(__name__)

_JD_CLEAN_PROMPT = (
    "Below is the raw text of a job-posting web page. It includes site navigation, "
    "buttons, cookie/login notices and other boilerplate around the actual posting. "
    "Extract ONLY the job description: the role summary, responsibilities, "
    "requirements/qualifications, and any benefits or about-the-role text that is "
    "part of the posting. Drop all site chrome, navigation, and unrelated links. "
    "Return clean plain text with simple line breaks and no markdown. "
    "If the text contains no actual job description, reply with exactly: NONE\n\n"
    "=== RAW PAGE TEXT ===\n"
)


async def _llm_clean_jd(raw_text: str) -> str | None:
    """Use Gemini to pull the real job description out of a noisy page.

    Falls back to the raw stripped text if Gemini isn't configured or errors,
    and returns None if the model reports there's no description present."""
    if not llm.have_key():
        return raw_text
    try:
        out = await asyncio.to_thread(llm.ask, _JD_CLEAN_PROMPT + raw_text[:30000])
    except Exception as exc:  # noqa: BLE001 — degrade to raw text on any LLM error
        logger.warning("[Detail] LLM JD cleanup failed: %s", exc)
        return raw_text
    out = (out or "").strip()
    if not out or out.upper() == "NONE" or len(out) < 80:
        return None
    return out

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept": "application/json, text/html",
            "Accept-Language": "en-US,en;q=0.9"}


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    out = strip_html(html.unescape(text)).strip()
    return out or None


async def fetch_job_detail(job: dict) -> str | None:
    """Return cleaned full JD text for a job row, or None on failure."""
    ats = (job.get("ats_type") or "").lower()
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=_HEADERS) as session:
        try:
            if ats == "greenhouse":
                return await _greenhouse_detail(session, job)
            if ats == "workday":
                return await _workday_detail(session, job)
            if ats == "oracle":
                return await _oracle_detail(session, job)
            if ats == "jobspy":
                return job.get("full_description") or await _generic_detail(session, job)
            return await _generic_detail(session, job)
        except Exception as exc:
            logger.error("[Detail] %s id=%s: %s", ats, job.get("id"), exc)
            return None


async def _greenhouse_detail(session, job) -> str | None:
    # token from source_url (career_url = boards.greenhouse.io/{token}); id from job_url.
    # The id may be in a gh_jid= param (custom domains) or a /jobs/{id} / numeric path.
    token = urlparse(job.get("source_url") or "").path.strip("/").split("/")[0]
    job_url = job.get("job_url", "")
    m = re.search(r"gh_jid=(\d+)", job_url) or re.search(r"/(\d{4,})(?:[/?#]|$)", job_url)
    if not token or not m:
        return None
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{m.group(1)}?content=true"
    async with session.get(url) as resp:
        if resp.status != 200:
            return None
        data = await resp.json(content_type=None)
    return _clean(data.get("content"))


async def _workday_detail(session, job) -> str | None:
    career_url = job.get("source_url") or ""
    parsed = urlparse(career_url)
    host = parsed.hostname or ""
    if "myworkdayjobs.com" not in host:
        return None
    tenant = host.split(".")[0]
    board = parsed.path.strip("/").split("/")[0]
    external_path = job.get("job_url", "")[len(career_url.rstrip("/")):]
    if not external_path:
        return None
    # Workday's detail endpoint often 403s without a prior board visit; prime cookies.
    try:
        async with session.get(career_url):
            pass
    except Exception:
        pass
    url = f"https://{host}/wday/cxs/{tenant}/{board}{external_path}"
    async with session.get(url, headers={"Referer": career_url}) as resp:
        if resp.status != 200:
            return None
        data = await resp.json(content_type=None)
    return _clean((data.get("jobPostingInfo") or {}).get("jobDescription"))


async def _oracle_detail(session, job) -> str | None:
    api, host, site = _oracle_endpoint(job.get("source_url") or "")
    if not api:
        return None
    job_id = job.get("job_url", "").rstrip("/").split("/")[-1]
    if not job_id:
        return None
    url = (f"{api}/{job_id}?expand=requisitionDescription"
           f"&onlyData=true")
    async with session.get(url) as resp:
        if resp.status != 200:
            return None
        data = await resp.json(content_type=None)
    # Single-resource GET returns the requisition fields at top level.
    parts = [data.get("ShortDescriptionStr"), data.get("ExternalResponsibilitiesStr"),
             data.get("ExternalQualificationsStr")]
    joined = "\n\n".join(p for p in parts if p)
    return _clean(joined) if joined else None


async def _generic_detail(session, job) -> str | None:
    url = job.get("job_url", "")
    if not url.startswith("http"):
        return None
    async with session.get(url) as resp:
        if resp.status != 200:
            return None
        body = await resp.text()
    text = _clean(body)
    # JS-rendered SPA shells produce little usable text — treat as a miss.
    if not text or len(text) < 200:
        return None
    # The page text is full of nav/boilerplate; let Gemini extract the real JD.
    return await _llm_clean_jd(text[:30000])
