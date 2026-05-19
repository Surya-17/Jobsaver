import asyncio
import logging
import threading
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import get_db, get_companies, get_stats, query_jobs, update_job_status
from models import JobsResponse, JobResponse, ScrapeStatusResponse, StatsResponse, StatusUpdate
from scrapers.runner import get_last_run, is_running, run_scrape

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Fortune 500 Job Scraper")

BASE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


# ── Pages ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    conn = get_db()
    try:
        jobs, total = query_jobs(conn, limit=50)
        companies = get_companies(conn)
        stats = get_stats(conn)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "jobs": jobs,
            "total": total,
            "companies": companies,
            "stats": stats,
            "scrape_running": is_running(),
        },
    )


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/jobs", response_model=JobsResponse)
async def get_jobs(
    company: str | None = None,
    title: str | None = None,
    since: str | None = None,
    limit: int = 50,
    offset: int = 0,
    view: str = "active",
    sort: str = "posted",
    max_exp: int | None = None,
):
    limit = min(limit, 200)
    conn = get_db()
    try:
        jobs, total = query_jobs(
            conn, company=company, title_keyword=title, since=since,
            limit=limit, offset=offset, view=view, sort=sort, max_exp=max_exp,
        )
    finally:
        conn.close()
    return {"jobs": jobs, "total": total, "offset": offset, "limit": limit}


@app.patch("/api/jobs/{job_id}/status")
async def set_job_status(job_id: int, body: StatusUpdate):
    conn = get_db()
    try:
        ok = update_job_status(conn, job_id, body.status)
    finally:
        conn.close()
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


@app.get("/api/companies")
async def api_companies() -> list[str]:
    conn = get_db()
    try:
        return get_companies(conn)
    finally:
        conn.close()


@app.get("/api/stats", response_model=StatsResponse)
async def api_stats():
    conn = get_db()
    try:
        return get_stats(conn)
    finally:
        conn.close()


@app.post("/scrape")
async def trigger_scrape(
    company: str | None = None,
    test: bool = False,
):
    if is_running():
        return {"status": "already_running", "message": "A scrape is already in progress."}

    # Run in a dedicated thread with its own event loop — BackgroundTasks shares
    # uvicorn's loop which causes aiohttp POST requests to silently fail on Windows.
    def _run():
        asyncio.run(run_scrape(company_filter=company, test_mode=test))

    threading.Thread(target=_run, daemon=True).start()

    if test:
        msg = "Test scrape started (10 companies)."
    elif company:
        msg = f"Scraping {company} in background."
    else:
        msg = "Scraping all companies in background."
    return {"status": "started", "message": msg}


@app.get("/api/debug/workday")
async def debug_workday():
    """Test Workday API directly from server context."""
    import aiohttp
    from scrapers.generic import _scrape_workday_api
    from config import SEARCH_TITLES, COMPANIES
    cigna = next(c for c in COMPANIES if c["company_name"] == "Cigna")
    async with aiohttp.ClientSession() as session:
        jobs = await _scrape_workday_api(session, cigna, SEARCH_TITLES)
    return {"company": "Cigna", "jobs_found": len(jobs), "sample": [j["job_title"] for j in jobs[:3]]}


@app.get("/api/scrape/status", response_model=ScrapeStatusResponse)
async def scrape_status():
    return {"running": is_running(), "last_run": get_last_run()}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
