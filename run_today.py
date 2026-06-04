"""Throwaway driver: scrape AI Engineer jobs, then tailor a resume for each one
found today. Reuses the same functions the FastAPI endpoints call. Not committed."""
import asyncio
import sys
from datetime import datetime, timezone

from database import get_db, get_job, set_job_detail, set_resume_path
from scrapers.runner import run_scrape
from scrapers.detail import fetch_job_detail
from resume import tailor_resume_for_job, ResumeError


async def main():
    if "--no-scrape" in sys.argv:
        print("=== SCRAPE SKIPPED (--no-scrape) ===", flush=True)
    else:
        print("=== SCRAPE START (AI Engineer) ===", flush=True)
        res = await run_scrape(titles=["AI Engineer"])
        print(f"=== SCRAPE DONE === {res}", flush=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM jobs WHERE requested_title ILIKE '%%AI Engineer%%' "
            "AND substr(first_seen_at, 1, 10) = %s AND date_posted IS NOT NULL "
            "ORDER BY company_name",
            (today,),
        )
        ids = [r[0] for r in cur.fetchall()]
    print(f"=== TAILOR {len(ids)} AI Engineer jobs found {today} ===", flush=True)

    ok = skipped = failed = 0
    for i, jid in enumerate(ids, 1):
        job = get_job(conn, jid)
        company = job["company_name"]
        jd = job.get("full_description")
        if not jd:
            try:
                jd = await fetch_job_detail(job)
            except Exception as e:  # noqa: BLE001 — log and move on
                print(f"[{i}/{len(ids)}] JD-ERR {company} (id {jid}): {e}", flush=True)
                jd = None
            if jd:
                set_job_detail(conn, jid, jd)
        if not jd:
            skipped += 1
            print(f"[{i}/{len(ids)}] SKIP {company} (id {jid}): no JD available", flush=True)
            continue
        try:
            stem = f"{company} - {jid}"  # job-id suffix avoids same-company overwrite
            pdf = await asyncio.to_thread(tailor_resume_for_job, jid, jd, stem)
            set_resume_path(conn, jid, str(pdf))
            ok += 1
            print(f"[{i}/{len(ids)}] OK   {company} (id {jid}) -> {pdf}", flush=True)
        except ResumeError as e:
            failed += 1
            print(f"[{i}/{len(ids)}] FAIL {company} (id {jid}): {e}", flush=True)

    conn.close()
    print(f"=== SUMMARY: ok={ok} skipped(no JD)={skipped} failed={failed} "
          f"of {len(ids)} ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
