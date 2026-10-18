"""LinkedIn / Indeed (and other boards JobSpy supports) via the python-jobspy library."""
from __future__ import annotations

import logging
import math
import time

from scraper.models import Job
from scraper.sources.base import Source

log = logging.getLogger(__name__)


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (TypeError, ValueError, ImportError):
        pass
    return value


def _salary(row: dict) -> str | None:
    lo, hi = _clean(row.get("min_amount")), _clean(row.get("max_amount"))
    if not lo and not hi:
        return None
    rng = f"{lo:,.0f}–{hi:,.0f}" if lo and hi else f"{lo or hi:,.0f}"
    return f"{rng.replace(',', ' ')} {_clean(row.get('currency')) or ''}/{_clean(row.get('interval')) or ''}".strip()


def row_to_job(row: dict) -> Job:
    site = str(_clean(row.get("site")) or "jobspy")
    location = _clean(row.get("location")) or ""
    level = _clean(row.get("job_level"))
    skills = _clean(row.get("skills"))
    return Job(
        source=site,
        key=f"{site}:{_clean(row.get('id')) or _clean(row.get('job_url'))}",
        title=str(_clean(row.get("title")) or ""),
        company=str(_clean(row.get("company")) or ""),
        url=str(_clean(row.get("job_url")) or ""),
        cities=[location] if location else [],
        remote=bool(_clean(row.get("is_remote"))) or None,
        description=str(_clean(row.get("description")) or ""),
        required_skills=[s.strip() for s in str(skills).split(",")] if skills else [],
        salary=_salary(row),
        info=f"Level on {site}: {level}" if level else None,
    )


class JobSpySource(Source):
    name = "jobspy"

    def fetch(self) -> list[Job]:
        from jobspy import scrape_jobs  # imported lazily: heavy dependency

        jobs: list[Job] = []
        hours_old = max(1, math.ceil(self.max_age_hours))
        for search in self.config.get("searches") or []:
            try:
                df = scrape_jobs(
                    site_name=search.get("sites") or self.config.get("sites", ["linkedin", "indeed"]),
                    search_term=search["search_term"],
                    location=search.get("location"),
                    is_remote=bool(search.get("is_remote", False)),
                    results_wanted=int(self.config.get("results_wanted", 20)),
                    hours_old=hours_old,
                    country_indeed=self.config.get("country_indeed", "poland"),
                    fetch_description=True,
                    description_format="markdown",
                    verbose=0,
                )
            except Exception as exc:  # one failed search shouldn't kill the others
                log.warning("jobspy search %r failed: %s", search.get("search_term"), exc)
                continue
            rows = df.to_dict("records") if df is not None else []
            jobs.extend(row_to_job(r) for r in rows)
            log.info("jobspy %r @ %s: %d results", search["search_term"], search.get("location"), len(rows))
            time.sleep(3)
        return jobs
