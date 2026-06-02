"""LLM-driven auto-discovery scraper for JS-heavy career sites.

For SPAs with no known API, this loads the search page with Playwright, captures
every JSON response, and asks an LLM to identify which response holds the job
array and how to extract each field. The resulting "plan" is cached per company,
so the LLM only runs on first discovery — subsequent scrapes just replay the plan
against freshly captured JSON (no LLM call, no cost).

Runs sync Playwright (call from a thread-pool executor) to avoid the Windows
ProactorEventLoop subprocess limitation.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

_PLAN_CACHE = Path(__file__).parent.parent / "autodiscover_plans.json"


# ── Plan cache ─────────────────────────────────────────────────────────────────

def _load_plans() -> dict:
    if _PLAN_CACHE.exists():
        try:
            return json.loads(_PLAN_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_plan(company: str, plan: dict) -> None:
    plans = _load_plans()
    plans[company] = plan
    _PLAN_CACHE.write_text(json.dumps(plans, indent=2), encoding="utf-8")


# ── JSON path resolution ────────────────────────────────────────────────────────

def _resolve(data, path: str):
    """Navigate a dot/bracket path (e.g. 'results[0].hits') into JSON."""
    if not path:
        return None
    cur = data
    for part in path.replace("[", ".[").split("."):
        if not part:
            continue
        try:
            if part.startswith("[") and part.endswith("]"):
                cur = cur[int(part[1:-1])]
            else:
                cur = cur[part]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def _as_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for k in ("name", "text", "label", "value"):
            if isinstance(value.get(k), str):
                return value[k].strip()
    if isinstance(value, list) and value:
        return ", ".join(_as_text(v) for v in value[:3] if _as_text(v))
    if isinstance(value, (int, float)):
        return str(value)
    return ""


# ── Network intelligence ─────────────────────────────────────────────────────────

def _capture(page, url: str) -> list[dict]:
    """Load the page and return captured JSON responses as [{url, data}]."""
    captured: list[dict] = []

    def on_response(resp):
        u = resp.url
        ct = resp.headers.get("content-type", "")
        if any(x in u for x in (".js", ".css", ".png", ".svg", ".woff", ".gif", ".ico", ".jpg")):
            return
        if "json" not in ct and "/api/" not in u and "graphql" not in u and "widgets" not in u:
            return
        try:
            captured.append({"url": u, "data": resp.json()})
        except Exception:
            pass

    page.on("response", on_response)
    page.goto(url, timeout=40000, wait_until="domcontentloaded")
    # SPAs fire their jobs XHR after hydration — wait for the network to settle.
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    return captured


def _candidate_arrays(data, path="", depth=0):
    """Yield (path, array) for every list-of-dicts in the JSON, deepest-first."""
    if depth > 6:
        return
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            yield path, data
        for i, x in enumerate(data[:5]):
            yield from _candidate_arrays(x, f"{path}[{i}]", depth + 1)
    elif isinstance(data, dict):
        for k, v in data.items():
            yield from _candidate_arrays(v, f"{path}.{k}" if path else k, depth + 1)


def _briefing(captured: list[dict]) -> str:
    """Compact summary of captured responses + their candidate job arrays."""
    lines: list[str] = []
    for resp in captured:
        arrays = list(_candidate_arrays(resp["data"]))
        arrays = [a for a in arrays if len(a[1]) >= 2][:6]
        if not arrays:
            continue
        lines.append(f"\nAPI URL: {resp['url'][:140]}")
        for apath, arr in arrays:
            item = arr[0]
            sample = {k: str(v)[:70] for k, v in list(item.items())[:14] if not isinstance(v, (dict, list))}
            lines.append(f"  items_path={apath or '(root)'} count={len(arr)} "
                         f"item_keys={list(item.keys())[:14]}")
            lines.append(f"    sample={json.dumps(sample)[:600]}")
    return "\n".join(lines) if lines else "(no JSON arrays captured)"


_PROMPT = """You are analyzing intercepted API responses from a job-listings page.
Pick the ONE response + array that holds the actual JOB POSTINGS (not nav menus,
filters, or related-jobs). Then give the field paths RELATIVE TO EACH ITEM.

PAGE URL: {page_url}

Return ONLY this JSON, no markdown:
{{"found": true, "url_pattern": "<unique substring of the chosen API URL>",
  "items_path": "<path to the jobs array>",
  "title": "<item field for job title>",
  "location": "<item field for location, or null>",
  "url": "<item field holding a full job URL or path, or null>",
  "id": "<item field holding the job id/slug, or null>",
  "url_template": "<URL with {{id}} placeholder built from the PAGE URL domain, e.g. https://site.com/jobs/{{id}}, or null>",
  "date": "<item field for posting date, or null>"}}
or {{"found": false}} if none contain job postings.

Field paths are relative to one item and may be nested (e.g. "_source.Title",
"location.name"). If items have a direct URL/path field, set "url". Otherwise set
"id" to the id field and "url_template" so a clickable URL can be built.

BRIEFING:
{briefing}"""


def _extract_json(text: str) -> dict:
    if "```" in text:
        text = re.sub(r"^.*?```(?:json)?", "", text, flags=re.S)
        text = text.split("```")[0]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}


def _discover_plan(captured: list[dict], company: str, page_url: str) -> dict | None:
    from scrapers import llm
    briefing = _briefing(captured)
    if briefing.startswith("(no"):
        return None
    try:
        raw = llm.ask(_PROMPT.format(page_url=page_url, briefing=briefing[:14000]))
        plan = _extract_json(raw)
    except Exception as exc:
        logger.error("[Auto] %s LLM discovery failed: %s", company, exc)
        return None
    if not plan.get("found") or not plan.get("items_path") or not plan.get("title"):
        logger.info("[Auto] %s: LLM found no job array", company)
        return None
    logger.info("[Auto] %s: discovered plan %s", company, {k: plan.get(k) for k in ("url_pattern", "items_path", "title")})
    return plan


# ── Plan execution ───────────────────────────────────────────────────────────────

def _execute_plan(captured: list[dict], plan: dict, company_cfg: dict, title: str) -> list[dict]:
    from scrapers.generic import _title_matches, _parse_date

    pattern = plan.get("url_pattern", "")
    # The same API URL can be hit multiple times (e.g. two GraphQL calls); try
    # every URL match and fall back to all responses, picking the one where the
    # items_path actually resolves to a non-empty list.
    matches = [r["data"] for r in captured if pattern and pattern in r["url"]]
    items = None
    for data in matches or [r["data"] for r in captured]:
        got = _resolve(data, plan["items_path"])
        if isinstance(got, list) and got:
            items = got
            break
    if not items:
        return []

    origin = "{0.scheme}://{0.netloc}".format(urlparse(company_cfg["career_url"]))
    now = datetime.now(timezone.utc).isoformat()
    jobs: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        job_title = _as_text(_resolve(item, plan["title"]))
        if not job_title:
            continue
        matched, keyword = _title_matches(job_title, [title])
        if not matched:
            continue
        raw_url = _as_text(_resolve(item, plan["url"])) if plan.get("url") else ""
        if raw_url.startswith("/"):
            raw_url = origin + raw_url
        # Items with only an id: build the URL from the template the LLM provided.
        if not raw_url.startswith("http") and plan.get("url_template") and plan.get("id"):
            job_id = _as_text(_resolve(item, plan["id"]))
            if job_id:
                raw_url = plan["url_template"].replace("{id}", job_id)
        if not raw_url.startswith("http"):
            continue
        jobs.append({
            "scraped_at": now,
            "company_name": company_cfg["company_name"],
            "job_title": job_title,
            "location": _as_text(_resolve(item, plan["location"])) if plan.get("location") else "",
            "job_url": raw_url,
            "source_url": company_cfg["career_url"],
            "ats_type": company_cfg["ats_type"],
            "requested_title": keyword,
            "date_posted": _parse_date(_as_text(_resolve(item, plan["date"])) or None) if plan.get("date") else None,
        })
    return jobs


# ── Entry point ──────────────────────────────────────────────────────────────────

def scrape_company_autodiscover(company_cfg: dict, search_titles: list[str]) -> list[dict]:
    """Sync. Discover (or replay cached) extraction plan and pull jobs. Dedup by URL."""
    from playwright.sync_api import sync_playwright

    company = company_cfg["company_name"]
    template = company_cfg.get("search_url_template") or company_cfg["career_url"]
    plan = _load_plans().get(company)

    jobs: list[dict] = []
    seen: set[str] = set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            for title in search_titles:
                url = template.format(title=quote(title)) if "{title}" in template else template
                page = context.new_page()
                try:
                    captured = _capture(page, url)
                except Exception as exc:
                    logger.error("[Auto] %s title=%s load failed: %s", company, title, exc)
                    page.close()
                    continue

                if plan is None:
                    plan = _discover_plan(captured, company, url)
                    if plan:
                        _save_plan(company, plan)
                    else:
                        page.close()
                        break  # no discoverable API — give up for this company

                for job in _execute_plan(captured, plan, company_cfg, title):
                    if job["job_url"] not in seen:
                        seen.add(job["job_url"])
                        jobs.append(job)
                page.close()
        finally:
            context.close()
            browser.close()

    logger.info("[Auto] %s → %d matches", company, len(jobs))
    return jobs
