"""justjoin.it — uses the JSON API behind the website (not an official public API).

Listing: GET https://justjoin.it/api/candidate-api/offers?from=<cursor>&sortBy=publishedAt&orderBy=descending&categories=...
Detail:  GET https://justjoin.it/api/candidate-api/offers/<slug>
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from scraper.http import html_to_text
from scraper.models import Job
from scraper.sources.base import Source, parse_iso, skill_names

log = logging.getLogger(__name__)

OFFERS_URL = "https://justjoin.it/api/candidate-api/offers"
OFFER_PAGE = "https://justjoin.it/job-offer/{slug}"


def _level(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        v = value.get("value") or value.get("name") or value.get("label")
        return [v] if v else []
    if isinstance(value, list):
        return [x for x in (_level(v) for v in value) for x in x]
    return []


def _salary(employment_types) -> str | None:
    parts = []
    for et in employment_types or []:
        if not isinstance(et, dict):
            continue
        if et.get("currencySource") not in (None, "original"):
            continue
        lo, hi = et.get("from"), et.get("to")
        if lo is None and hi is None:
            continue
        rng = f"{lo:,}–{hi:,}".replace(",", " ") if lo and hi else str(lo or hi)
        currency = str(et.get("currency", "")).upper()
        unit = et.get("unit") or "month"
        contract = et.get("type") or ""
        parts.append(f"{rng} {currency}/{unit} {contract}".strip())
    return "; ".join(dict.fromkeys(parts)) or None


def parse_offer(offer: dict) -> Job:
    slug = offer.get("slug", "")
    cities = [
        loc.get("city") for loc in (offer.get("locations") or offer.get("multilocation") or [])
        if isinstance(loc, dict) and loc.get("city")
    ]
    if not cities and offer.get("city"):
        cities = [offer["city"]]
    workplace = offer.get("workplaceType")
    return Job(
        source="justjoin",
        key=f"justjoin:{slug}",
        title=offer.get("title", ""),
        company=offer.get("companyName", ""),
        url=OFFER_PAGE.format(slug=slug),
        cities=list(dict.fromkeys(cities)),
        workplace=workplace,
        remote=(workplace == "remote") if workplace else None,
        levels=_level(offer.get("experienceLevel")),
        required_skills=skill_names(offer.get("requiredSkills") or offer.get("skills")),
        nice_skills=skill_names(offer.get("niceToHaveSkills")),
        salary=_salary(offer.get("employmentTypes")),
        published_at=parse_iso(offer.get("publishedAt") or offer.get("lastPublishedAt")),
    )


class JustJoinSource(Source):
    name = "justjoin"

    def fetch(self) -> list[Job]:
        categories = self.config.get("categories") or []
        max_batches = int(self.config.get("max_batches", 6))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.max_age_hours)

        jobs: list[Job] = []
        cursor = 0
        for batch in range(max_batches):
            params = [("from", cursor), ("sortBy", "publishedAt"), ("orderBy", "descending")]
            params += [("categories", c) for c in categories]
            resp = self.session.get(OFFERS_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            offers = data.get("data") or []
            if not offers:
                break
            batch_jobs = [parse_offer(o) for o in offers if isinstance(o, dict)]
            jobs.extend(batch_jobs)

            # Sorted newest-first: once a whole batch is older than the cutoff, stop.
            if all(j.published_at and j.published_at < cutoff for j in batch_jobs):
                break
            nxt = (data.get("meta") or {}).get("next") or {}
            if nxt.get("cursor") is None:
                break
            cursor = nxt["cursor"]
            time.sleep(1)
        log.info("justjoin: %d listings", len(jobs))
        return jobs

    def load_details(self, job: Job) -> None:
        slug = job.key.split(":", 1)[1]
        resp = self.session.get(f"{OFFERS_URL}/{slug}", timeout=30)
        resp.raise_for_status()
        detail = resp.json()
        job.description = html_to_text(detail.get("body") or detail.get("description"))
        req = skill_names(detail.get("requiredSkills") or detail.get("skills"))
        nice = skill_names(detail.get("niceToHaveSkills"))
        if req:
            job.required_skills = req
        if nice:
            job.nice_skills = nice
        if not job.levels:
            job.levels = _level(detail.get("experienceLevel"))
        time.sleep(0.5)
