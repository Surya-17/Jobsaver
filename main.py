import asyncio
import logging
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import (
    get_db, init_db, get_companies, get_sources, get_stats, query_jobs, update_job_status,
    get_job, set_job_detail, set_resume_path, set_job_queued, get_queued_jobs,
    get_applied_jobs, get_applied_stats, insert_manual_job,
)
from models import (
    JobsResponse, JobResponse, ScrapeStatusResponse, StatsResponse, StatusUpdate,
    JDResponse, TailorResponse, ManualJob,
)
from scrapers.detail import fetch_job_detail
from resume import tailor_resume_for_job, ResumeError
from scrapers.runner import get_last_run, is_running, run_scrape

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create the jobs table/indexes if this is a fresh Postgres DB
    yield


app = FastAPI(title="Fortune 500 Job Scraper", lifespan=lifespan)

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
        sources = get_sources(conn)
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
            "sources": sources,
            "stats": stats,
            "scrape_running": is_running(),
        },
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    conn = get_db()
    try:
        applied = get_applied_jobs(conn)
        stats = get_applied_stats(conn)
    finally:
        conn.close()
    now_week = datetime.now(timezone.utc).strftime("%Y-W%W")
    this_week = next((w["count"] for w in stats["by_week"] if w["week"] == now_week), 0)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"applied": applied, "stats": stats, "this_week": this_week},
    )


@app.get("/job/{job_id}", response_class=HTMLResponse)
async def job_detail_page(request: Request, job_id: int):
    conn = get_db()
    try:
        job = get_job(conn, job_id)
    finally:
        conn.close()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(
        request=request, name="job.html", context={"job": job},
    )


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/jobs", response_model=JobsResponse)
async def get_jobs(
    company: str | None = None,
    source: str | None = None,
    title: str | None = None,
    since: str | None = None,
    posted_since: str | None = None,
    limit: int = 50,
    offset: int = 0,
    view: str = "active",
    sort: str = "posted",
    max_exp: int | None = None,
):
    limit = min(limit, 200)
    companies = [c for c in company.split(",") if c] if company else None
    sources = [s for s in source.split(",") if s] if source else None
    conn = get_db()
    try:
        jobs, total = query_jobs(
            conn, companies=companies, sources=sources, title_keyword=title,
            since=since, posted_since=posted_since,
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


@app.post("/api/jobs/{job_id}/fetch-jd", response_model=JDResponse)
async def fetch_jd(job_id: int):
    conn = get_db()
    try:
        job = get_job(conn, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.get("full_description"):
            return {"ok": True, "full_description": job["full_description"],
                    "detail_fetched_at": job.get("detail_fetched_at")}
        text = await fetch_job_detail(job)
        if not text:
            return {"ok": False, "error": "Could not fetch a job description for this source."}
        set_job_detail(conn, job_id, text)
        row = get_job(conn, job_id)
        return {"ok": True, "full_description": text,
                "detail_fetched_at": row.get("detail_fetched_at")}
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/tailor-resume", response_model=TailorResponse)
async def tailor_resume(job_id: int):
    conn = get_db()
    try:
        job = get_job(conn, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        jd = job.get("full_description")
        if not jd:
            jd = await fetch_job_detail(job)
            if jd:
                set_job_detail(conn, job_id, jd)
        if not jd:
            return {"ok": False, "error": "No job description available — fetch the JD first."}
        try:
            pdf_path = await asyncio.to_thread(
                tailor_resume_for_job, job_id, jd, job["company_name"])
        except ResumeError as e:
            return {"ok": False, "error": str(e), "compile_log": e.log}
        set_resume_path(conn, job_id, str(pdf_path))
        return {"ok": True, "resume_url": f"/api/jobs/{job_id}/resume"}
    finally:
        conn.close()


@app.get("/api/jobs/{job_id}/resume")
async def download_resume(job_id: int):
    conn = get_db()
    try:
        job = get_job(conn, job_id)
    finally:
        conn.close()
    if not job or not job.get("resume_path") or not Path(job["resume_path"]).exists():
        raise HTTPException(status_code=404, detail="No resume generated yet")
    return FileResponse(job["resume_path"], media_type="application/pdf",
                        filename=f"resume_{job_id}.pdf")


# ── Batch tailoring (Tailor All on the queue) ──────────────────────────────────
# Runs sequentially in a daemon thread — the local LLM serves one request at a
# time, so there's nothing to parallelize. Progress is polled via /status.

_tailor_state: dict = {
    "running": False, "total": 0, "done": 0, "ok": 0, "failed": 0, "current": None,
}


def _run_tailor_queue() -> None:
    conn = get_db()
    try:
        jobs = get_queued_jobs(conn)
        _tailor_state.update(running=True, total=len(jobs), done=0, ok=0, failed=0,
                             current=None)
        for job in jobs:
            jid, company = job["id"], job["company_name"]
            _tailor_state["current"] = company
            jd = job.get("full_description")
            if not jd:
                try:
                    jd = asyncio.run(fetch_job_detail(job))  # async fetch, own loop
                except Exception:  # noqa: BLE001 — skip this job, keep the batch going
                    jd = None
                if jd:
                    set_job_detail(conn, jid, jd)
            if not jd:
                _tailor_state["failed"] += 1
            else:
                try:
                    # Unique stem (company + id) so same-company jobs don't overwrite.
                    pdf = tailor_resume_for_job(jid, jd, f"{company} - {jid}")
                    set_resume_path(conn, jid, str(pdf))
                    _tailor_state["ok"] += 1
                except ResumeError:
                    _tailor_state["failed"] += 1
            _tailor_state["done"] += 1
        _tailor_state["current"] = None
    finally:
        conn.close()
        _tailor_state["running"] = False


@app.post("/api/tailor-queue")
async def tailor_queue_all():
    if _tailor_state["running"]:
        return {"status": "already_running"}
    conn = get_db()
    try:
        count = len(get_queued_jobs(conn))
    finally:
        conn.close()
    if count == 0:
        return {"status": "empty"}
    threading.Thread(target=_run_tailor_queue, daemon=True).start()
    return {"status": "started", "total": count}


@app.get("/api/tailor-queue/status")
async def tailor_queue_status():
    return _tailor_state


@app.post("/api/jobs/manual", response_model=JobResponse)
async def add_manual_job(body: ManualJob):
    conn = get_db()
    try:
        data = body.model_dump()
        if not data.get("job_url"):
            slug = re.sub(r"[^a-z0-9]+", "-", f"{data['company_name']}-{data['job_title']}".lower()).strip("-")
            data["job_url"] = f"manual://{slug}-{uuid4().hex[:8]}"
        new_id = insert_manual_job(conn, data)
        return get_job(conn, new_id)
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/queue")
async def add_to_queue(job_id: int):
    conn = get_db()
    try:
        if not set_job_queued(conn, job_id, True):
            raise HTTPException(status_code=404, detail="Job not found")
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/jobs/{job_id}/queue")
async def remove_from_queue(job_id: int):
    conn = get_db()
    try:
        set_job_queued(conn, job_id, False)
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/queue")
async def list_queue():
    conn = get_db()
    try:
        return {"jobs": get_queued_jobs(conn)}
    finally:
        conn.close()


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
