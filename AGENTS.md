# AGENTS.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

# Project map (Jobsaver)

A FastAPI + Postgres + vanilla-JS web app that scrapes Data/AI Engineer jobs, lets you
tailor a LaTeX resume per job (local LLM → PDF), and tracks applications. Single
Postgres DB (`jobs` table, via Docker), no ORM, no build step.

## Run
- `docker compose up -d` → starts Postgres (`postgres:16`, db/user/pass all `jobsaver`,
  port 5432). Connection string in `.env` `DATABASE_URL`. `database.init_db()` creates the
  schema on app startup.
- `python main.py` → uvicorn on http://localhost:8000 (run from a terminal where PATH
  includes Tectonic). `/` = job list + filters + tailor queue; `/job/{id}` = JD detail page;
  `/dashboard` = applied tracker.
- Scrape: `POST /scrape` (button in UI) runs all enabled company scrapers + JobSpy.
- One-time SQLite→Postgres data import: `python migrate_sqlite_to_pg.py` (idempotent).

## Layout
- `main.py` — FastAPI routes (pages + `/api/*`). `config.py` — `COMPANIES` list + `SEARCH_TITLES`.
- `database.py` — psycopg2 connection (`get_db`), `init_db` (schema), `_dict_rows/_dict_row/_scalar`
  helpers, all `jobs`-table queries/helpers. `models.py` — Pydantic responses.
- `scrapers/` — `runner.py` (orchestrates), `greenhouse.py`, `generic.py` (workday/amazon/ashby
  APIs + Playwright fallback), `oracle.py`, `jobspy_source.py` (Indeed/LinkedIn via JobSpy),
  `autodiscover.py` (LLM-driven generic SPA scraper, plan-cached in `autodiscover_plans.json`),
  `detail.py` (on-demand full JD fetch; Gemini cleans noisy generic/LinkedIn pages into a real JD), `llm.py` (Gemini client).
- `resume.py` (tailor LaTeX + compile) · `tailor_client.py` (OpenAI-compatible LLM, Ollama).
- `templates/` (index.html, dashboard.html) · `static/` (app.js, style.css).

## Key facts / gotchas
- **Scraping LLM** = Gemini (`.env` GEMINI_API_KEY, model `gemini-3.1-flash-lite`). Used by
  `autodiscover.py` (plan-cached, rarely calls the API) and `detail.py` (one call per JD fetch on
  the generic/LinkedIn path to extract a clean JD from noisy HTML). Gemini free quotas are per-model.
- **Tailoring LLM** = local Ollama `qwen3:8b` via OpenAI-compatible API (`.env` TAILOR_BASE_URL/
  TAILOR_API_KEY/TAILOR_MODEL). Separate from scraping. Needs `ollama serve` running. First
  tailor ~90s (cold VRAM load + Qwen3 thinking mode); warm runs faster.
- **LaTeX** = Tectonic at `%LOCALAPPDATA%\Programs\Tectonic` (on user PATH). Engine call isolated
  in `resume._run_tectonic` (swap there to change engines). Base resume: `resume/base_resume.tex`
  (user-provided; example at `resume/base_resume.example.tex`). Instructions: `resume/instructions.md`.
- **Job sources**: `ats_type` is the scraper backend; the UI "source" badge is derived
  (`database._job_source`): JobSpy rows show their board (linkedin/indeed), others show ats_type.
- **Location/eligibility filters** live in `scrapers/runner.py` (`_is_us_location`, `_is_eligible`).
- **`output/`, `*.db`, `.env`, `autodiscover_plans.json`** are gitignored. (`jobs.db` is the
  legacy SQLite file, kept only as the migration source.)
- Generated resumes: `output/resumes/{job_id}/resume.pdf`; path stored on the row (`resume_path`).
- **Tailor All**: `POST /api/tailor-queue` runs a background thread that tailors every queued job
  sequentially (local LLM serves one at a time); poll `GET /api/tailor-queue/status`.

## State (built; verified end-to-end)
Discovery (company ATS + JobSpy + LLM autodiscover), filters (company multiselect/search, source,
found/posted dates, max-exp), on-demand JD fetch, resume tailoring (Ollama→Tectonic→PDF), tailor
queue (right panel), applied dashboard, manual job entry. Workday detail JD-fetch may 403 from some
IPs (graceful fallback). ~37 "generic" SPA companies still return little directly — JobSpy covers
those employers anyway.
