# Jobsaver

A self-hosted job-search workbench for Data/AI Engineer roles. Jobsaver scrapes
openings from ~95 company career sites **plus** the big aggregators (LinkedIn,
Indeed via JobSpy), lets you pull jobs into a working queue, auto-tailors a LaTeX
résumé per job with a **local** LLM, compiles it to PDF, and tracks everything
you've applied to.

It runs entirely on your machine: **FastAPI + SQLite + vanilla JS**, no build
step, no ORM, no cloud dependency for the sensitive parts (your résumé never
leaves your computer).

---

## What it does

- **Discovery** — pulls Data/AI Engineer jobs from three kinds of sources:
  1. **Company ATS scrapers** — Greenhouse, Workday, Oracle HCM, Ashby, Amazon, plus
     generic career pages.
  2. **JobSpy aggregators** — Indeed + LinkedIn (Glassdoor / Google / ZipRecruiter
     available behind a proxy).
  3. **LLM auto-discovery** — for JavaScript-heavy career sites with no clean API,
     a Gemini-driven scraper sniffs the page's network calls, picks out the job
     array, and caches a reusable extraction "plan".
- **Filtering** — searchable multi-select company picker, source filter
  (linkedin / workday / greenhouse / …), separate "found" vs "posted" date
  filters, max-years-experience filter, and US-location/eligibility filtering.
- **On-demand JD fetch** — grab the full job description for a specific job only
  when you want it (keeps scrapes fast and light).
- **Résumé tailoring** — feed your base LaTeX résumé + the job description to a
  **local Ollama model** (`qwen3:8b`), which rewrites it for the role; Tectonic
  then compiles it to a downloadable PDF.
- **Tailor queue** — a right-hand staging panel: pull interesting jobs in, fetch
  their JDs, tailor résumés, and mark them applied, all from one place.
- **Applied dashboard** — `/dashboard` tracks everything you've applied to, with
  by-company / by-week breakdowns, résumé download links, and a form to log jobs
  you applied to elsewhere.

---

## Architecture at a glance

```
Browser (vanilla JS) ──HTTP──> FastAPI (main.py)
                                  │
                                  ├── database.py  ── SQLite (jobs.db, single table)
                                  ├── scrapers/    ── runner orchestrates all sources
                                  │     greenhouse · generic (workday/amazon/ashby) · oracle
                                  │     jobspy_source · autodiscover (Gemini) · detail (JD fetch)
                                  ├── resume.py + tailor_client.py ── Ollama → LaTeX → Tectonic PDF
                                  └── templates/ + static/  ── index (jobs) · dashboard (applied)
```

Two **separate** LLMs, by design:

| Purpose | LLM | Why |
|---|---|---|
| Scraping / auto-discovery | **Gemini free tier** (`gemini-3.1-flash-lite`) | Cheap, plan-cached so it rarely calls the API |
| Résumé tailoring | **Local Ollama** (`qwen3:8b`) | Free, unlimited, and your résumé stays on your machine |

The tailoring client talks to an OpenAI-compatible endpoint, so you can swap
Ollama for Groq / OpenRouter / OpenAI by changing three `.env` values.

---

## Project layout

| Path | Role |
|---|---|
| `main.py` | FastAPI app — pages (`/`, `/dashboard`) and `/api/*` endpoints |
| `config.py` | `COMPANIES` list (~95 employers + ATS metadata) and `SEARCH_TITLES` |
| `database.py` | Schema, `MIGRATIONS`, all `jobs`-table queries/helpers; derives the `source` badge |
| `models.py` | Pydantic request/response models |
| `scrapers/runner.py` | Orchestrates all scrapers; US-location + eligibility filters |
| `scrapers/greenhouse.py` | Greenhouse board API |
| `scrapers/generic.py` | Workday / Amazon / Ashby APIs + Playwright fallback |
| `scrapers/oracle.py` | Oracle HCM Cloud REST API |
| `scrapers/jobspy_source.py` | Indeed / LinkedIn (and proxied boards) via JobSpy |
| `scrapers/autodiscover.py` | Gemini-driven SPA scraper; plans cached in `autodiscover_plans.json` |
| `scrapers/detail.py` | On-demand full-JD fetch, dispatched by `ats_type` |
| `scrapers/llm.py` | Gemini client used by the scrapers |
| `resume.py` | Tailor LaTeX + compile to PDF (`_run_tectonic` isolates the engine call) |
| `tailor_client.py` | OpenAI-compatible chat client (default: local Ollama) |
| `templates/` | `index.html` (job list + queue), `dashboard.html` (applied tracker) |
| `static/` | `app.js`, `style.css` |
| `resume/` | `base_resume.tex` (your résumé — gitignored), `instructions.md`, `base_resume.example.tex` |
| `output/resumes/{job_id}/resume.pdf` | Generated, tailored résumés (gitignored) |

---

## Requirements

- **Python 3.10+**
- **Tectonic** — single-binary LaTeX engine, on your `PATH`
  (e.g. `%LOCALAPPDATA%\Programs\Tectonic`). First compile downloads packages, so
  it needs internet once.
- **Ollama** running locally with `qwen3:8b` pulled — only needed for résumé tailoring.
- **Gemini API key** (free tier) — only needed for LLM auto-discovery of SPA sites.
- **Playwright browsers** — `python -m playwright install chromium` (for the
  Playwright fallback scrapers).

Recommended hardware for local tailoring: a GPU with ~8 GB VRAM comfortably runs
`qwen3:8b`. Larger models spill to RAM and run slower.

---

## Setup

```powershell
# 1. Install Python deps
pip install -r requirements.txt
python -m playwright install chromium

# 2. Configure environment
copy .env.example .env
#   then edit .env — set GEMINI_API_KEY, and TAILOR_* if not using default Ollama

# 3. Install the LLM + LaTeX tooling
#    - Tectonic on PATH (https://tectonic-typesetting.github.io)
#    - Ollama: https://ollama.com  →  ollama pull qwen3:8b

# 4. Add your base résumé
copy resume\base_resume.example.tex resume\base_resume.tex
#   then replace it with your real LaTeX résumé
```

### Environment variables (`.env`)

```ini
# Scraping / auto-discovery LLM (Gemini free tier)
GEMINI_API_KEY=your-key-here
# LLM_MODEL=gemini-3.1-flash-lite          # optional override

# Résumé-tailoring LLM — OpenAI-compatible endpoint (default: local Ollama)
TAILOR_BASE_URL=http://localhost:11434/v1
TAILOR_API_KEY=ollama
TAILOR_MODEL=qwen3:8b
```

---

## Running

```powershell
python main.py
```

Then open **http://localhost:8000**.

- `/` — job list, filters, and the tailor queue (right panel).
- `/dashboard` — applied-jobs tracker + manual job entry.

Make sure Ollama is running (`ollama list`) before tailoring, and that Tectonic
is on the `PATH` of the terminal you launch from.

### Scraping

Click **Scrape** in the UI (or `POST /scrape`) to run all enabled company
scrapers + JobSpy in the background. Use `POST /scrape?test=true` to run against a
small set of 10 companies, or `POST /scrape?company=Amazon` for one.

---

## Typical workflow

1. **Scrape** to populate the job list.
2. **Filter** to the roles you care about (company / source / date / experience).
3. **＋ Queue** the interesting ones into the right-hand panel.
4. In the queue: **Fetch JD** → **Tailor Résumé** (downloads a tailored PDF) →
   **Mark Applied**.
5. Review everything on **/dashboard**; log anything you applied to elsewhere via
   **＋ Add manual job**.

---

## API reference

| Method & path | Purpose |
|---|---|
| `GET /` | Job list page |
| `GET /dashboard` | Applied tracker page |
| `GET /api/jobs` | Query jobs (filters: `company`, `source`, `title`, `since`, `posted_since`, `max_exp`, `view`, `sort`, `limit`, `offset`) |
| `PATCH /api/jobs/{id}/status` | Set status (`applied` / `skipped` / …); stamps `applied_at` |
| `POST /api/jobs/{id}/fetch-jd` | Fetch + cache the full job description |
| `POST /api/jobs/{id}/tailor-resume` | Tailor + compile résumé → returns a download URL |
| `GET /api/jobs/{id}/resume` | Download the tailored PDF |
| `POST /api/jobs/manual` | Add a manually-applied job |
| `POST` / `DELETE /api/jobs/{id}/queue` | Add / remove from the tailor queue |
| `GET /api/queue` | List queued jobs |
| `GET /api/companies` · `GET /api/stats` | Distinct companies · summary stats |
| `POST /scrape` | Trigger a background scrape (`?company=`, `?test=true`) |
| `GET /api/scrape/status` | Scrape running state + last run |

---

## Data model

One SQLite table, `jobs` (see `database.py`). New columns are added idempotently
via the `MIGRATIONS` list (`ALTER TABLE` wrapped in try/except). Key fields:

- Identity & provenance: `company_name`, `job_title`, `location`, `job_url` (UNIQUE),
  `ats_type`, `source_url`, `requested_title`, `date_posted`, `first_seen_at`,
  `scraped_at`, `years_exp`.
- Workflow: `status` (lifecycle: `skipped` / `applied` / …), `queued` (orthogonal
  boolean), `applied_at`, `notes`.
- Tailoring: `full_description`, `detail_fetched_at`, `resume_path`,
  `resume_generated_at`.

The UI **source** badge is *derived* (`_job_source`): JobSpy rows show their board
(linkedin / indeed), everything else shows its `ats_type`.

---

## Notes & gotchas

- **Gemini quotas are per-model**; auto-discovery plans are cached in
  `autodiscover_plans.json`, so the API is hit rarely.
- **First résumé tailor is slow** (~90 s): cold VRAM load + Qwen3 "thinking" mode.
  Warm runs are faster. The client strips `<think>…</think>` traces and code fences.
- **Swap the LaTeX engine** in one place: `resume._run_tectonic`.
- **Workday detail JD-fetch may 403** from some IPs; it falls back gracefully.
- Some ~37 "generic" SPA companies still return little directly — JobSpy covers
  those employers anyway.
- **Gitignored / kept local:** `output/`, `*.db`, `.env`, `autodiscover_plans.json`,
  and your real `resume/base_resume.tex`.

---

## Status

Built and verified end-to-end: discovery (company ATS + JobSpy + LLM
auto-discovery), filtering, on-demand JD fetch, résumé tailoring
(Ollama → Tectonic → PDF), the tailor queue, the applied dashboard, and manual
job entry.
