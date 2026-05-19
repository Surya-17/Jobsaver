# Fortune 500 Job Scraper — Plan

## Goal
A web app that scrapes job listings from Fortune 500 company career portals, stores them in a local database, and lets you browse/filter them in a browser.

## Project Structure
```
E:\Projects\Jobsaver\
├── config.py              # Company registry + job titles to search
├── main.py                # FastAPI app + all endpoints
├── database.py            # SQLite schema, upsert, query helpers
├── models.py              # Pydantic response models
├── scrapers/
│   ├── __init__.py
│   ├── greenhouse.py      # Greenhouse public JSON API scraper
│   ├── generic.py         # Playwright + custom API scrapers
│   └── runner.py          # Async orchestrator
├── templates/
│   └── index.html         # Jinja2 main page
├── static/
│   ├── app.js             # Vanilla JS: filtering, pagination, scrape trigger
│   └── style.css
├── jobs.db                # SQLite database (created at runtime)
└── requirements.txt
```

## Data Sources
- `fortune500_job_portals_simple.csv` — 58 companies with career portal URLs (reference only)
- `job_matches_software_engineer.csv` — example of desired output format

## Target Platforms
| Type | How we scrape | Companies |
|---|---|---|
| Greenhouse | Public JSON API (no auth) | ~15 Fortune 500 companies |
| Apple | Semi-public JSON API (aiohttp) | Apple |
| Microsoft | Internal JSON API (aiohttp POST) | Microsoft |
| Workday | Playwright (JS-heavy) | Pfizer, Sysco, others |
| Generic | Playwright fallback | Amazon, Walmart, etc. |

## Job Titles Searched (configurable)
```python
SEARCH_TITLES = [
    "Software Engineer",
    "Data Engineer",
    "ML Engineer",
    "Site Reliability Engineer",
]
```

## Database Schema
```sql
CREATE TABLE jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at      TEXT NOT NULL,
    company_name    TEXT NOT NULL,
    job_title       TEXT NOT NULL,
    location        TEXT,
    job_url         TEXT NOT NULL UNIQUE,  -- deduplication key
    source_url      TEXT,
    ats_type        TEXT,                  -- 'greenhouse' | 'workday' | 'generic'
    requested_title TEXT                   -- which search title matched
);
```
- `UNIQUE(job_url)` handles deduplication
- `INSERT OR REPLACE` refreshes `scraped_at` on re-scrape

## Scrapers

### Greenhouse (`scrapers/greenhouse.py`)
- Endpoint: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
- Returns all jobs in one call — no pagination needed
- Filter results by `SEARCH_TITLES` (case-insensitive substring match)
- HTTP 404 = bad token → log warning and skip (don't crash)

### Generic (`scrapers/generic.py`)
- **Apple**: aiohttp against Apple's semi-public jobs search JSON API
- **Microsoft**: aiohttp POST to Microsoft careers search JSON API
- **Workday**: Playwright, wait for networkidle, parse job list DOM
- **Fallback**: Playwright, extract `<a>` tags with `/job/` or `/careers/` in href
- Dispatches via `CUSTOM_EXTRACTORS` dict, falls back to generic Playwright

### Runner (`scrapers/runner.py`)
- One shared `aiohttp.ClientSession` + one shared Playwright browser for the whole run
- `asyncio.Semaphore(5)` caps concurrent scrapers
- Each company = one `asyncio.create_task`
- Exceptions caught per-company — one failure doesn't abort the run
- Upserts results to DB as each company finishes
- Returns `{total_new, total_updated, companies_scraped, companies_failed, errors}`

## API Endpoints (`main.py`)
| Method | Path | Description |
|---|---|---|
| GET | `/` | Main page (Jinja2 SSR, first 50 jobs) |
| GET | `/api/jobs` | Filterable paginated jobs (`?company=&title=&since=&limit=&offset=`) |
| GET | `/api/companies` | Distinct company names in DB |
| GET | `/api/stats` | `{total_jobs, last_scraped, company_count}` |
| POST | `/scrape` | Trigger background scrape (`?company=` for single company) |
| GET | `/api/scrape/status` | `{running: bool, last_run: {...}}` |

## Frontend
- **Initial render**: Jinja2 SSR so page loads with content (no blank screen)
- **Filter sidebar**: company dropdown, title keyword input, since-date picker
- **Job cards**: company name, job title, location, ATS type badge, timestamp, Apply link
- **Pagination**: "Load More" appends next page
- **Scrape button**: triggers POST /scrape, polls /api/scrape/status every 3s, refreshes results when done
- **Title search**: 300ms debounce for live filtering

## Config Design (`config.py`)
Each company entry:
```python
{
    "company_name": "Goldman Sachs Group",
    "ats_type": "greenhouse",           # greenhouse | workday | generic
    "greenhouse_token": "goldman-sachs", # None if not Greenhouse
    "career_url": "https://www.goldmansachs.com/careers",
    "search_url_template": None,        # used by generic scrapers, {title} placeholder
    "enabled": True,                    # False = skip (bad URL, needs fixing)
}
```
Companies with known-bad portal URLs (`enabled=False`): Walgreens, UPS, Lowe's, MetLife, Comcast.

## Dependencies
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
aiohttp>=3.9.0
playwright>=1.44.0
jinja2>=3.1.0
python-multipart>=0.0.9
```

## Setup
```bash
pip install -r requirements.txt
playwright install chromium
uvicorn main:app --reload
```

## Build Order
1. `config.py` + `database.py` — schema, upsert, queries
2. `scrapers/greenhouse.py` + token verification — validate all Greenhouse board tokens
3. `scrapers/runner.py` (Greenhouse-only) + `main.py` API endpoints
4. `templates/index.html` + `static/app.js` — full UI with Greenhouse data
5. `scrapers/generic.py` — Apple → Microsoft → Workday → generic fallback
6. Polish — error logging, scrape status polling, relative timestamps
