from pydantic import BaseModel


class JobResponse(BaseModel):
    id: int
    scraped_at: str
    company_name: str
    job_title: str
    location: str | None
    job_url: str
    source_url: str | None
    ats_type: str | None
    requested_title: str | None
    date_posted: str | None
    source: str | None = None
    first_seen_at: str | None
    status: str | None = None
    years_exp: int | None = None
    full_description: str | None = None
    detail_fetched_at: str | None = None
    resume_path: str | None = None
    resume_generated_at: str | None = None
    queued: int | None = 0
    applied_at: str | None = None
    notes: str | None = None


class StatusUpdate(BaseModel):
    status: str | None = None


class JDResponse(BaseModel):
    ok: bool
    full_description: str | None = None
    detail_fetched_at: str | None = None
    years_exp: int | None = None
    error: str | None = None


class TailorResponse(BaseModel):
    ok: bool
    resume_url: str | None = None
    error: str | None = None
    compile_log: str | None = None


class ManualJob(BaseModel):
    company_name: str
    job_title: str
    job_url: str | None = None
    location: str | None = None
    notes: str | None = None


class AppliedStats(BaseModel):
    total: int
    by_company: list[dict]
    by_week: list[dict]


class StatsResponse(BaseModel):
    total_jobs: int
    company_count: int
    last_scraped: str | None
    skipped_count: int = 0


class ScrapeStatusResponse(BaseModel):
    running: bool
    last_run: dict | None


class JobsResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    offset: int
    limit: int
