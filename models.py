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
    first_seen_at: str | None
    status: str | None = None
    years_exp: int | None = None


class StatusUpdate(BaseModel):
    status: str | None = None


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
