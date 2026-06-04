# Jobsaver — Code Guide

A from-scratch walkthrough of the whole codebase: the architecture, the end-to-end
flow, every function that matters, how each website is scraped (and which sites
share a pattern), the tools we use, the database schema and why it's shaped that
way, and the choices we'd reconsider.

Read this top to bottom once and you'll understand the system. After that, use the
section headers as a map.

---

## 1. The 30-second mental model

Jobsaver is a personal job-hunting cockpit. It does three things:

1. **Scrape** Data/AI Engineer jobs from ~110 company career sites + the big job
   boards (Indeed, LinkedIn) into one Postgres table.
2. **Browse & triage** them in a web UI — filter, skip, queue, mark applied.
3. **Tailor a résumé** per job: feed the job description + your base LaTeX résumé
   to a local LLM, recompile to PDF, download.

Everything is one FastAPI process talking to one Postgres table. No ORM, no build
step, no message queue. Background work (scraping, batch tailoring) runs in plain
threads. The frontend is one `index.html` + one `app.js` (vanilla JS, no framework).

```
                          ┌─────────────────────────────────────────┐
                          │              main.py (FastAPI)            │
   Browser  ───HTTP──▶    │  pages (/, /dashboard, /job/{id})         │
   app.js                 │  api  (/api/jobs, /scrape, /tailor, ...)  │
                          └───────┬───────────────┬──────────────────┘
                                  │               │
                    ┌─────────────▼──┐      ┌─────▼───────────────┐
                    │  scrapers/     │      │  resume.py          │
                    │  runner.py     │      │  tailor_client.py   │
                    │  + backends    │      │  (Ollama → LaTeX)   │
                    └───────┬────────┘      └─────┬───────────────┘
                            │                     │
                            ▼                     ▼
                    ┌───────────────┐      ┌──────────────┐
                    │ database.py   │      │ output PDF    │
                    │  (Postgres)   │      │  on disk      │
                    └───────────────┘      └──────────────┘
```

---

## 2. The tech stack and why

| Layer            | Tool                                   | Why this one |
|------------------|----------------------------------------|--------------|
| Web framework    | **FastAPI** + uvicorn                  | async-native (the scrapers are async), Pydantic response validation for free, tiny boilerplate. |
| DB               | **Postgres 16** (Docker), `psycopg2`   | one real DB, no ORM. Migrated off SQLite for concurrency (the scraper writes from many threads while the UI reads). |
| HTTP (scraping)  | **aiohttp**                            | fan out 100+ company API calls concurrently in one event loop. |
| Browser scraping | **Playwright** (Chromium, headless)    | the only way to get jobs out of JS-only SPAs that have no usable API. |
| Job aggregator   | **JobSpy**                             | one library that already indexes Indeed/LinkedIn — covers employers we don't scrape directly. |
| Scraping LLM     | **Gemini** (free tier) via `google-genai` | cleans noisy HTML into a real JD, and auto-discovers SPA APIs. Used rarely, so free quota is fine. |
| Tailoring LLM    | **Ollama `qwen3:8b`** (local, GPU)     | runs on the RTX 4060, no API cost, no data leaving the machine. OpenAI-compatible API so the backend is swappable. |
| PDF              | **pdflatex (MiKTeX)**, Tectonic fallback | the base résumé is real LaTeX; we compile it to a real PDF. |
| Frontend         | vanilla JS + Jinja2 templates          | no build step, no node_modules, trivially hackable. |

The two LLMs are **deliberately separate** (`scrapers/llm.py` = Gemini,
`tailor_client.py` = Ollama) so scraping and tailoring never share a provider or a
quota, and either can be swapped without touching the other.

---

## 3. The end-to-end flow

### A. Startup
`python main.py` → uvicorn boots `main:app`. The `lifespan` context manager calls
`database.init_db()`, which runs the `CREATE TABLE IF NOT EXISTS` schema. Idempotent,
so it's safe on every boot.

### B. Scrape (the data-in path)
1. User clicks **Scrape All** → `POST /scrape` (`main.trigger_scrape`).
2. Because Windows' `ProactorEventLoop` breaks aiohttp POSTs on uvicorn's shared
   loop, the scrape runs in a **dedicated thread with its own event loop**
   (`threading.Thread(target=_run)` where `_run` calls `asyncio.run(run_scrape(...))`).
3. `scrapers/runner.run_scrape` is the orchestrator:
   - Builds the list of enabled companies (or the 10 `TEST_COMPANIES`, or one
     company filter).
   - Creates an `asyncio.Semaphore(15)` to cap concurrency and a
     `ThreadPoolExecutor` for the blocking/Playwright scrapers.
   - Fires `_scrape_one(...)` for every company via `asyncio.gather`.
   - On a full run, then calls `_scrape_jobspy(...)` for the aggregator.
4. `_scrape_one` dispatches by `ats_type` to the right backend (see §5), then runs
   every returned job through two filters — `_is_us_location` and `_is_eligible` —
   stamps `years_exp` via `infer_exp`, and calls `database.insert_job` (which is an
   `INSERT ... ON CONFLICT (job_url) DO NOTHING`, so re-scraping is deduped for free).
5. `_last_run` records counts; the UI polls `/api/scrape/status` to show progress.

### C. Browse (the data-out path)
- `GET /` renders `index.html` with the first 50 jobs.
- `app.js` then drives everything via `GET /api/jobs` with query params for
  filters/sort/paging. `database.query_jobs` builds the parametrized SQL.
- Skip / apply / queue are `PATCH`/`POST`/`DELETE` calls that update single columns.

### D. Fetch JD (on demand)
- A job row from the list scrape usually has **no full description** (we only stored
  title/location/url to keep scraping fast). Clicking **Fetch JD** →
  `POST /api/jobs/{id}/fetch-jd` → `scrapers/detail.fetch_job_detail` pulls the real
  JD (see §6) and stores it in `full_description`.
- JobSpy rows are the exception: they arrive **with** a description already.

### E. Tailor (the résumé path)
- **Tailor Resume** → `POST /api/jobs/{id}/tailor-resume`. If there's no JD yet, it
  fetches one first. Then `resume.tailor_resume_for_job` runs the LLM + LaTeX
  pipeline (see §8) in a worker thread (`asyncio.to_thread`) so the event loop isn't
  blocked. The PDF path is stored on the row.
- **Tailor All** → `POST /api/tailor-queue` runs a background daemon thread
  (`_run_tailor_queue`) that tailors every queued job **sequentially** — the local
  LLM serves one request at a time, so there's nothing to parallelize. Progress is
  exposed via `/api/tailor-queue/status` and polled by the UI every 2s.

### F. Track
- Marking a job "applied" stamps `applied_at` once (`COALESCE` preserves the first
  time). `/dashboard` reads applied jobs and aggregates by company and by week.

---

## 4. The scraper taxonomy — which sites share a pattern

This is the heart of the system. We have **~110 companies** in `config.py`, each
tagged with an `ats_type`. The `ats_type` decides which backend handles it. The key
insight: **most companies don't have a unique scraper — they share a handful of ATS
(Applicant Tracking System) backends.** Scrape the ATS once and you've scraped every
company on it.

| `ats_type`  | Backend file / function                        | How it works | Companies (examples) |
|-------------|-------------------------------------------------|--------------|----------------------|
| `greenhouse`| `greenhouse.fetch_greenhouse_jobs`              | Public JSON API, one call per board | ~45 (Stripe, Databricks, Cloudflare, GitLab, MongoDB, …) |
| `workday`   | `generic._scrape_workday_api`                   | Workday's `/wday/cxs/.../jobs` JSON POST API, paged | ~10 (Fannie Mae, Humana, Cigna, Target, Bank of America, …) |
| `oracle`    | `oracle._scrape_oracle_api`                     | Oracle HCM `recruitingCEJobRequisitions` REST API | JPMorgan, Kroger, Dell |
| `ashby`     | `generic._scrape_ashby`                         | Ashby `posting-api/job-board/{slug}` JSON | ~17 (Notion, Snowflake, Ramp, Plaid, Confluent, …) |
| `amazon`    | `generic._scrape_amazon`                        | Amazon Jobs `search.json` endpoint | Amazon |
| `generic`   | LLM autodiscover → Playwright anchor fallback   | Per-site JSON discovery or DOM scraping | Apple, Microsoft, Google, Tesla, Walmart, … |
| `jobspy`    | `jobspy_source.fetch_jobspy_jobs`               | Indeed + LinkedIn via the JobSpy library | covers everyone, attributed by board |

**Pattern families, concretely:**

- **"Public JSON API" family — greenhouse, ashby, amazon.** All the same shape: one
  HTTP GET to a documented/semi-documented endpoint, parse a JSON array, normalize.
  Cheapest and most reliable. No browser. Greenhouse is the gold standard — that's
  why we have 45 companies on it.

- **"Derive-the-API-from-the-portal-URL" family — workday, oracle.** These SPAs look
  un-scrapable but have a private JSON API whose URL can be *computed* from the public
  career URL. `generic._workday_api_url` turns
  `https://fanniemae.wd1.myworkdayjobs.com/FannieMaeCareers` into
  `.../wday/cxs/fanniemae/FannieMaeCareers/jobs`; `oracle._oracle_endpoint` does the
  same for Oracle CX. Both paginate (Workday 20/page, Oracle 25/page). No browser.

- **"JS SPA with no obvious API" family — generic.** Apple, Microsoft, Google, Tesla,
  etc. Three of these (Amazon/Apple/Microsoft) we hand-wrote JSON scrapers for because
  their endpoints were findable. The rest go through a two-stage fallback:
  1. **LLM autodiscover** (`autodiscover.scrape_company_autodiscover`): load the page
     in Playwright, capture every JSON response, ask Gemini *"which response holds the
     jobs array, and what are the field paths?"*, cache that "plan", and replay it on
     future scrapes with no further LLM calls.
  2. **Playwright anchor scraper** (`runner._scrape_playwright_title`): if no API is
     discoverable, just load the page and harvest `<a>` tags whose href looks like a
     job link (`/job/`, `/jobs/`, `/careers/`, …). Crude, but catches simple sites.

- **"Aggregator" family — jobspy.** Doesn't scrape a company at all — it queries
  Indeed and LinkedIn (which already index nearly every employer). One source, huge
  coverage. This is the safety net for the ~37 `generic` SPAs that still return little
  directly.

The UI "source" badge (`database._job_source`) is derived: JobSpy rows show their
board (linkedin/indeed); everything else shows its `ats_type`.

---

## 5. The scraper backends, function by function

### `scrapers/runner.py` — orchestration + filtering

- **`run_scrape(company_filter, titles, test_mode)`** — top-level entry. Guards
  against concurrent runs with `_scrape_lock` + `_scrape_running`. Builds the company
  list, sets up the semaphore + thread pool + aiohttp session, gathers all
  `_scrape_one` tasks, then runs JobSpy. Writes `_last_run` summary.
- **`_scrape_one(sem, session, executor, company_cfg, search_titles, db_conn)`** —
  per-company. Dispatches by `ats_type`:
  - `greenhouse` → `fetch_greenhouse_jobs` (async, direct).
  - `generic` → autodiscover (if Gemini key present) then Playwright fallback, both
    in the thread pool because Playwright sync API can't run on the async loop.
  - everything else (`amazon`/`workday`/`ashby`/`oracle`) → `generic.scrape_company`.
  Then filters + `infer_exp` + `insert_job`. Returns `(new, updated, error)`.
- **`_is_us_location(location, job_url)`** — keeps US/Americas roles. Returns True for
  an explicit US signal (state code, "United States", "Americas"); otherwise rejects
  if any foreign-location keyword (`_NON_US` list) appears. The explicit-signal check
  means a multi-region role that lists the US *and* foreign cities survives.
- **`_is_eligible(job_title)`** — rejects director+ titles (`_SENIOR_PAT`) and
  non-full-time roles (`_NON_FULLTIME_PAT`: contract, intern, temp, …).
- **`_scrape_playwright_title` / `_scrape_playwright_company_async` /
  `_run_playwright_company_sync`** — the anchor-tag fallback scraper. Loads the search
  URL, waits for job-link selectors, harvests matching anchors. The sync wrapper
  exists so it can run in a thread with its own loop (Windows Playwright constraint).
- **`_scrape_jobspy`** — runs `fetch_jobspy_jobs` in the executor, filters, inserts.

### `scrapers/greenhouse.py` — the simplest, most reliable scraper

- **`fetch_greenhouse_jobs(session, company_cfg, search_titles)`** — GETs
  `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`, iterates `jobs`,
  keeps title matches. Because `content=true`, Greenhouse rows arrive **with the full
  JD already** (used by `infer_exp` and later by the detail fetcher).
- **`_title_matches`** — substring match (case-insensitive). Note: greenhouse uses a
  loose substring match; `generic._title_matches` uses a stricter word-boundary regex.
- **`_normalize`** — maps the raw Greenhouse object to our standard job dict.
- **`_parse_date`** — trims the ISO timestamp to `YYYY-MM-DD`.

### `scrapers/generic.py` — the multi-backend file

Despite the name, this holds several distinct JSON-API scrapers plus the Playwright
fallback, all behind one **`scrape_company` dispatcher** (by `ats_type`).

- **`_scrape_amazon`** — Amazon Jobs `search.json`: GET per title, parse `jobs[]`,
  build the URL from `job_path`.
- **`_scrape_workday_api`** — the Workday workhorse. `_workday_api_url` derives the
  private endpoint; then POSTs `{searchText, limit:20, offset}` paging up to
  `WORKDAY_MAX=100` per title. `_location_from_workday_url` recovers a real location
  when the API returns the useless "3 Locations" placeholder by parsing the URL slug.
- **`_scrape_apple`** — Apple's `searchResults` JSON; builds `details/{positionId}` URL.
- **`_scrape_microsoft`** — Microsoft's `operationResult.result.jobs` JSON.
- **`_scrape_ashby`** — Ashby `posting-api/job-board/{slug}`; slug comes from the
  career URL path.
- **`_scrape_workday` (Playwright)** — a DOM-based Workday scraper using
  `data-automation-id` selectors. This is a legacy/fallback path; the JSON API
  (`_scrape_workday_api`) is what actually runs for `workday` companies.
- **`_scrape_generic_playwright`** — anchor-harvest fallback (same idea as runner's).
- **`_workday_relative_date`** — "Posted 3 days ago" → a `YYYY-MM-DD` estimate.
- Shared helpers: **`_title_matches`** (word-boundary regex), **`_parse_date`**
  (handles ISO / "March 15, 2024" / "03/15/2024"), **`_job`** (builds the standard dict).

### `scrapers/oracle.py` — Oracle HCM Cloud

- **`_oracle_endpoint(career_url)`** — derives `(api_url, host, site_number)` from the
  Oracle CX URL by regexing `/sites/{SITE}` and swapping the path to the REST resource.
- **`_scrape_oracle_api`** — paginates the `recruitingCEJobRequisitions` finder query
  (25/page up to `ORACLE_MAX=200`), reads `items[0].requisitionList`, builds job URLs
  from `Id`. Reuses `generic`'s `_title_matches`/`_parse_date`/`_job`.

### `scrapers/jobspy_source.py` — the aggregator

- **`fetch_jobspy_jobs(search_titles)`** — for each board × title, calls JobSpy's
  `scrape_jobs`, dedupes by URL, applies the same word-boundary title match, and
  normalizes into our dict (with `full_description` populated, `ats_type="jobspy"`,
  `source_url` = board name).
- **`_scrape_site_title`** — one board+title call with retry/backoff on transient
  errors (timeout/429/connection).
- **`_clean` / `_date_posted`** — pandas-NaN-safe cell normalization.
- Config: only `indeed` + `linkedin` run by default; Glassdoor/Google/ZipRecruiter
  are gated behind `JOBSPY_PROXIES` because they 403 from a plain IP and would just
  add a minute of guaranteed-empty calls.

### `scrapers/autodiscover.py` — the clever one

LLM-driven scraper for SPAs with no known API. The trick: **discover once, replay
forever.**

- **`scrape_company_autodiscover`** — entry. Loads each search URL in Playwright,
  captures JSON, and either discovers a plan (first time) or replays the cached one.
- **`_capture(page, url)`** — attaches a response listener that records every JSON/API
  response (skipping static assets), then waits for network idle so the SPA's XHR fires.
- **`_candidate_arrays` / `_briefing`** — walk the captured JSON, find every
  list-of-dicts (potential job arrays), and build a compact text "briefing" (URL +
  item keys + a sample row) to send to the LLM.
- **`_discover_plan`** — sends the briefing to Gemini with `_PROMPT`, which asks for a
  strict JSON "plan": which URL, the `items_path`, and the field paths for
  title/location/url/id/date. Cached via `_save_plan` into `autodiscover_plans.json`.
- **`_execute_plan`** — replays a plan against captured JSON: resolve `items_path`,
  then for each item resolve the field paths, build the job URL (direct field, or
  `id` + `url_template`), title-match, and emit job dicts. **No LLM call here** — this
  is what runs on every subsequent scrape.
- **`_resolve(data, path)`** — navigates a `a.b[0].c` path into nested JSON.
- **`_extract_json`** — strips markdown fences and salvages the JSON object from the
  LLM's reply.

**Why this matters:** it turns "every SPA needs a custom scraper" into "Gemini writes
the scraper once, for free, and we cache it." The plan cache means steady-state
scraping makes ~zero LLM calls.

---

## 6. The JD-detail layer — `scrapers/detail.py`

Scraping stores only the basics. The full description is fetched **on demand** (when
you open a job or tailor it), because fetching JDs for 1000s of jobs up front would be
slow and wasteful.

- **`fetch_job_detail(job)`** — dispatches by `ats_type`:
  - `greenhouse` → `_greenhouse_detail`: re-hit the board API for that one job id
    (parsed from `gh_jid=` or a numeric path segment), return `content`.
  - `workday` → `_workday_detail`: derive the detail endpoint, **prime cookies by
    visiting the board first** (Workday 403s otherwise), return `jobDescription`.
    *Known gotcha: still 403s from some IPs — degrades gracefully to None.*
  - `oracle` → `_oracle_detail`: GET the single requisition, concatenate
    short-description + responsibilities + qualifications.
  - `jobspy` → already has `full_description`; only falls back to a page fetch if not.
  - everything else → `_generic_detail`: fetch the raw page HTML, strip it
    (`exp_parser.strip_html`), and if there's enough text, hand it to **Gemini**
    (`_llm_clean_jd`) to extract the real JD out of nav/cookie/boilerplate noise.
- **`_llm_clean_jd`** — Gemini call with `_JD_CLEAN_PROMPT`. Returns None if the model
  says `NONE` or the result is too short; degrades to raw text if Gemini errors/no key.
- **`_clean`** — unescape HTML entities + `strip_html` + collapse whitespace.

---

## 7. Experience inference — `scrapers/exp_parser.py`

This is how the "3+ yrs req" badge and the max-exp filter work.

- **`infer_exp(title, jd_html)`** — returns the minimum years required (0 = entry /
  unknown). Tries the JD first, then the title.
- **`exp_from_jd(html)`** — strips HTML, runs six regexes ("3+ years", "3-5 years",
  "minimum 3 years", "at least 3 years", "3 or more years", "3 years of experience"),
  returns the **minimum** plausible number found (0–20).
- **`exp_from_title(title)`** — heuristic from seniority words: senior/lead/principal/
  staff/architect → 6; roman numerals III/IV/V → 6, II → 3; junior/entry/associate → 0.
- **`strip_html` / `_HTMLStripper`** — a stdlib `HTMLParser` subclass that drops
  `<script>/<style>/<svg>/...` contents and returns whitespace-collapsed text. Reused
  all over (detail cleanup, exp parsing).

---

## 8. The résumé tailoring pipeline

Two files: `resume.py` (orchestration + LaTeX) and `tailor_client.py` (the LLM call).

### `tailor_client.py` — provider-agnostic LLM
- **`chat(prompt, model)`** — single-prompt chat completion against an
  OpenAI-compatible API (Ollama by default). Sends `num_ctx: 8192` via `extra_body`
  because the résumé + JD prompt blows past Ollama's 4096 default, which would
  **silently truncate the front of the prompt** (this caused a real layout bug —
  see §11). Returns `_strip`-ed text.
- **`_strip(text)`** — removes Qwen3 `<think>…</think>` reasoning traces (cuts
  everything through the **last** `</think>`, because Qwen sometimes emits unbalanced
  tags) and unwraps ```` ```latex ```` fences.
- **`_client`** — `lru_cache`d OpenAI client; base URL/key/model all from `.env`.

### `resume.py` — base LaTeX + JD → tailored LaTeX → PDF
The base résumé (`resume/base_resume.tex`) is split into a **fixed preamble** (the
LLM never touches `\documentclass`, packages, or `\newcommand` definitions) and a
**body** the LLM rewrites.

- **`tailor_resume_for_job(job_id, jd, company_name)`** — top-level. Generates the
  tailored LaTeX, then compiles it to `OUTPUT_DIR/<YYYY-MM-DD>/<Company>.pdf`.
- **`generate_tailored_latex(jd, model)`** — reads the base résumé, splits it
  (`_split_document`), builds the prompt (`_build_prompt`), calls the LLM, extracts the
  body (`_extract_body`), and re-assembles `preamble + body + \end{document}`.
- **`_split_document(base_tex)`** — returns `(preamble_incl_\begin{document}, body)`.
- **`_build_prompt(base_body, jd, instructions)`** — the guardrails live here: output
  *only* the body LaTeX, no preamble, no new commands, **don't fabricate experience**,
  keep length. JD is truncated to 12k chars. Instructions come from
  `resume/instructions.md` if present.
- **`_extract_body(model_out)`** — defensive cleanup: strips any `\begin/\end{document}`
  the model wrongly emitted, and keeps only from the first to the last line that starts
  with `\` (drops "Here's a tailored version…" prose and stray reasoning remnants).
- **`compile_pdf(tex_source, out_dir, stem)`** — writes the `.tex`, runs the engine,
  then **deletes all scratch files** (`.tex/.aux/.log/.out/.fls/.fdb_latexmk/.synctex.gz`)
  so only the PDF remains. (On compile failure the engine raises first, leaving the
  `.log` for debugging.)
- **`_run_latex` → `_run_pdflatex` / `_run_tectonic`** — the isolated engine call,
  dispatched by `RESUME_LATEX_ENGINE` (default `pdflatex`). `_run_pdflatex` runs two
  passes (the 2nd settles section rules / tabular spacing) with
  `-interaction=nonstopmode -halt-on-error`, and on failure surfaces the tail of the
  LaTeX `.log`.
- **`_safe_filename(name)`** — strips Windows-forbidden filename chars from the company
  name for the PDF stem.
- **`ResumeError`** — carries an optional `.log` (the LaTeX compile log) surfaced to the
  UI in a collapsible `<details>`.

**The full tailor path:** UI → `POST /tailor-resume` → (fetch JD if missing) →
`tailor_resume_for_job` → `generate_tailored_latex` → `tailor_client.chat` (Ollama on
GPU) → `_extract_body` → `compile_pdf` → `_run_pdflatex` (MiKTeX) → PDF on disk →
`set_resume_path` → download link in UI.

---

## 9. The database — `database.py`

### Schema (single `jobs` table)
```sql
id                  SERIAL PRIMARY KEY
first_seen_at       TEXT NOT NULL      -- when we first saw it
scraped_at          TEXT NOT NULL      -- last scrape that touched it
company_name        TEXT NOT NULL
job_title           TEXT NOT NULL
location            TEXT
job_url             TEXT NOT NULL UNIQUE   -- the dedup key
source_url          TEXT               -- career page / board name
ats_type            TEXT               -- scraper backend (greenhouse/workday/jobspy/…)
requested_title     TEXT               -- which SEARCH_TITLE matched
date_posted         TEXT               -- YYYY-MM-DD
status              TEXT               -- NULL | applied | resume_modify | skipped
years_exp           INTEGER            -- inferred minimum experience
full_description     TEXT              -- the JD (lazy-filled)
detail_fetched_at   TEXT
resume_path         TEXT               -- generated PDF path
resume_generated_at TEXT
queued              INTEGER DEFAULT 0  -- in the tailor queue?
applied_at          TEXT
notes               TEXT
-- indexes: company_name, first_seen_at, date_posted, job_title, queued
```

### Why this schema
- **One denormalized table, no joins.** A job *is* the unit of work. There are no
  separate companies/sources/applications tables because nothing is shared or reused
  across rows in a way that a join would help. Companies live in `config.py` (code, not
  data); "applications" are just `jobs` rows with `status='applied'`. For a
  single-user app over ~thousands of rows, one wide table is simpler and faster than a
  normalized schema, and it keeps every query a single `SELECT ... WHERE`.
- **`job_url UNIQUE` is the natural key.** `insert_job` is `ON CONFLICT (job_url) DO
  NOTHING`, so re-scraping the same posting is a no-op and the scraper is fully
  idempotent. No "have I seen this?" bookkeeping needed.
- **Dates stored as `TEXT` (ISO strings).** A holdover from the SQLite origin (SQLite
  has no native date type). It keeps Python ↔ DB round-trips trivial (`isoformat()` in,
  string compares work because ISO sorts lexically). The cost: week-bucketing for the
  dashboard is done in Python (`get_applied_stats`) rather than SQL, to avoid
  dialect-specific date functions.
- **`status` overloads several states** (NULL/applied/resume_modify/skipped) in one
  column rather than separate booleans — simpler filtering, and a job is only ever in
  one state.
- **Indexes** cover exactly the columns the UI filters/sorts on.

### Key DB functions
- **`get_db` / `init_db`** — open a connection (caller owns + closes it); create schema.
- **`insert_job`** — idempotent insert; also back-fills `years_exp` if a previously-seen
  row had it NULL.
- **`query_jobs`** — the one big read. Builds parametrized WHERE clauses from all the
  filters (company/source/title/since/posted_since/max_exp/view), handles the
  active-vs-skipped view, and the source filter's dual meaning (ATS platform OR JobSpy
  board). Sort is posted-date or found-date. Returns `(rows, total)`.
- **`update_job_status`** — sets status; stamps `applied_at` once via `COALESCE`.
- **`set_job_detail` / `set_resume_path` / `set_job_queued`** — single-column updates.
- **`insert_manual_job`** — for jobs you applied to outside Jobsaver; synthesizes a
  `manual://…` URL if none given.
- **`get_applied_jobs` / `get_applied_stats`** — dashboard reads; week-bucketing in
  Python.
- **`_job_source`** — derives the UI source badge (board for JobSpy, else `ats_type`).
- **`get_companies` / `get_sources` / `get_stats`** — populate filters + the header.
- Helpers **`_dict_rows` / `_dict_row` / `_scalar`** wrap psycopg2's `RealDictCursor`.

---

## 10. The frontend — `templates/` + `static/app.js`

No framework. `index.html` is server-rendered with the first page of jobs; `app.js`
takes over for everything interactive.

- **Rendering**: `renderCard(job)` builds each job card's HTML; `renderQueueItem`
  builds the right-panel queue. `escHtml` guards against injection.
- **Fetching**: `fetchJobs(append)` builds the query string from `currentFilters` and
  hits `/api/jobs`; "Load more" bumps the offset.
- **Filters**: multiselects for company/source, debounced title search, date inputs,
  max-exp. `applyFilters` reads them all and refetches.
- **Triage**: `handleStatusChange` (applied / modify), `showUndoToast`+`commitSkip`
  (skip with a 5s undo window), `restoreJob`.
- **Tailor queue**: `addToQueue`/`removeFromQueue`, per-item `fetchJd`/`tailorResume`,
  and `tailorAll` + `pollTailorStatus` (polls `/api/tailor-queue/status` every 2s,
  reconnects after a page refresh if a run is in progress).
- **Scrape**: `triggerScrape` + `pollScrapeStatus` (polls `/api/scrape/status` every
  3s, also reconnects on load).

The pattern throughout: **POST an action, then re-fetch the affected slice.** No
client-side state store, no optimistic-update framework — just refetch.

---

## 11. War stories (bugs that shaped the code)

- **Ollama ran on CPU, not the RTX 4060.** Caused by an `OLLAMA_VULKAN=true` env var:
  the Vulkan backend probed the AMD iGPU, found the driver too old, and fell back to
  CPU (8m51s per résumé). Fix: removed the flag so Ollama uses CUDA → ~70–140s on GPU.
  Persisted `OLLAMA_VULKAN=0` to User env.
- **Project overlap in a generated PDF.** Qwen hallucinated `\vspace{-100pt}` (base was
  `-10pt`), yanking project 2 up into project 1. Root cause was the **4096 context
  truncation** silently dropping the front of the prompt. Fix: `num_ctx=8192` in
  `tailor_client` + `OLLAMA_CONTEXT_LENGTH=8192` in env. Clean re-tailor confirmed.
- **`pdflatex not found on PATH`.** MiKTeX was installed and on the persisted User PATH,
  but a long-running app process had a stale PATH. Fix: launch from a fresh terminal,
  or refresh PATH from the registry in the launching process.
- **Windows `ProactorEventLoop` breaks aiohttp POSTs** on uvicorn's shared loop — why
  scrapes run in a dedicated thread with their own `asyncio.run`, and why Playwright
  sync scrapers run in a thread pool.

---

## 12. What we could have done differently

These are honest trade-offs, not regrets — most were the right call for a one-user app.

1. **Real timestamp columns instead of `TEXT`.** Storing dates as ISO strings is a
   SQLite holdover. With `TIMESTAMPTZ` we could do week-bucketing, "posted in last N
   days", and sorting in SQL instead of Python, and avoid `COALESCE(date_posted,
   first_seen_at)` string gymnastics. Low effort now that we're on Postgres.

2. **A `companies` table.** Right now companies live in `config.py` (a 970-line Python
   literal). Moving them to a table would let you enable/disable and add companies from
   the UI, store per-company scrape health, and stop redeploying to edit the list. The
   cost is a join and an admin screen.

3. **A connection pool.** Every request does `get_db()` → connect → close. For a
   single user that's fine, but `psycopg2.pool` or a dependency-injected pooled
   connection would cut per-request connection overhead and is the standard FastAPI
   pattern.

4. **A real job/task queue for tailoring.** `_run_tailor_queue` is a module-global dict
   mutated by a daemon thread. It works and survives a page refresh, but it's lost on
   process restart and can't be inspected/retried. A tiny persisted queue table (or
   RQ/Celery) would make batch tailoring durable and retryable.

5. **Unify `_title_matches`.** Greenhouse uses a loose substring match while everyone
   else uses a word-boundary regex, so Greenhouse can let through "Data Engineering
   Manager" for "Data Engineer". One shared matcher would make filtering consistent.

6. **The two Workday scrapers.** There's both a JSON-API Workday scraper and a
   Playwright DOM Workday scraper; only the API one runs. The DOM one is effectively
   dead code kept as a fallback — worth either wiring up as a real fallback or removing.

7. **Detail-fetch resilience.** Workday detail still 403s from some IPs. A shared
   browser-context fetch (reusing Playwright cookies) or a small retry/proxy layer would
   raise the JD hit rate. Today it just degrades to "fetch the JD first" failures.

8. **Structured output from the tailor LLM.** We post-process free-form LaTeX with
   `_extract_body` and `_strip` to survive the model's prose/reasoning. Asking for a
   constrained format (or clamping numeric `\vspace`/`\hspace` values) would make the
   pipeline more robust than regex cleanup after the fact.

9. **Tests.** There's `test_resume_cleanup.py` but no coverage of the scrapers'
   normalize/parse functions or `query_jobs`' WHERE-building — the places most likely to
   regress silently. Those are pure functions and would be cheap to test.

---

## 13. File-by-file index

| File | Role |
|------|------|
| `main.py` | FastAPI app: pages + all `/api/*` routes, scrape/tailor triggers, lifespan. |
| `config.py` | `SEARCH_TITLES`, `TEST_COMPANIES`, and the ~110-entry `COMPANIES` list (the scrape targets). |
| `database.py` | Postgres connection, schema, all reads/writes, source-derivation. |
| `models.py` | Pydantic response/request models. |
| `scrapers/runner.py` | Orchestrator + US/eligibility filters + Playwright fallback. |
| `scrapers/greenhouse.py` | Greenhouse JSON API scraper. |
| `scrapers/generic.py` | Amazon/Workday-API/Apple/Microsoft/Ashby scrapers + dispatcher + Playwright fallback. |
| `scrapers/oracle.py` | Oracle HCM Cloud REST scraper. |
| `scrapers/jobspy_source.py` | Indeed/LinkedIn via the JobSpy library. |
| `scrapers/autodiscover.py` | LLM-discovers + plan-caches SPA APIs. |
| `scrapers/detail.py` | On-demand full-JD fetch (per ATS) + Gemini JD cleanup. |
| `scrapers/exp_parser.py` | HTML stripping + years-of-experience inference. |
| `scrapers/llm.py` | Gemini client (scraping side). |
| `resume.py` | Base LaTeX split, prompt build, LaTeX compile, file cleanup. |
| `tailor_client.py` | OpenAI-compatible chat client (Ollama, tailoring side). |
| `templates/` | `index.html`, `dashboard.html`, `job.html`. |
| `static/app.js`, `static/style.css` | The whole frontend. |
| `migrate_sqlite_to_pg.py` | One-time SQLite→Postgres data import. |
</content>
</invoke>
