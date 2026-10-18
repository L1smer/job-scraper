"""NoFluffJobs — uses the JSON API behind the website (not an official public API).

Search: POST https://nofluffjobs.com/api/search/posting?salaryCurrency=PLN&salaryPeriod=month&limit=N
        body {"rawSearch": "category=frontend,fullstack seniority=junior,mid"}
        (rawSearch uses the same syntax as the search box on nofluffjobs.com)
Detail: GET  https://nofluffjobs.com/api/posting/<slug>
"""
from __future__ import annotations

import logging
import time

from scraper.http import html_to_text
from scraper.models import Job
from scraper.sources.base import Source, parse_epoch_ms, skill_names

log = logging.getLogger(__name__)

SEARCH_URL = "https://nofluffjobs.com/api/search/posting"
DETAIL_URL = "https://nofluffjobs.com/api/posting/{slug}"
OFFER_PAGE = "https://nofluffjobs.com/job/{slug}"


def _places(location: dict) -> tuple[list[str], bool]:
    cities, remote = [], bool(location.get("remote") or location.get("fullyRemote"))
    for place in location.get("places") or []:
        if not isinstance(place, dict) or place.get("provinceOnly"):
            continue
        city = place.get("city")
        if not city:
            continue
        if city.lower() == "remote":
            remote = True
        else:
            cities.append(city)
    return list(dict.fromkeys(cities)), remote


def _listing_salary(salary) -> str | None:
    if not isinstance(salary, dict):
        return None
    lo, hi = salary.get("from"), salary.get("to")
    if not lo and not hi:
        return None
    rng = f"{lo:,.0f}–{hi:,.0f}".replace(",", " ") if lo and hi else f"{lo or hi:,.0f}"
    return f"{rng} {salary.get('currency', '')}/month {salary.get('type', '')}".strip()


def _detail_salary(original) -> str | None:
    if not isinstance(original, dict):
        return None
    parts = []
    for contract, d in (original.get("types") or {}).items():
        rng = (d or {}).get("range") or []
        if len(rng) >= 2:
            parts.append(f"{rng[0]:,.0f}–{rng[1]:,.0f}".replace(",", " ")
                         + f" {original.get('currency', '')}/{str(d.get('period', 'month')).lower()} {contract}")
    return "; ".join(parts) or None


def parse_posting(p: dict) -> Job:
    slug = p.get("url", "")
    cities, remote = _places(p.get("location") or {})
    remote = remote or bool(p.get("fullyRemote"))
    tiles = [t for t in ((p.get("tiles") or {}).get("values") or []) if isinstance(t, dict)]
    required = skill_names([t for t in tiles if t.get("type") == "requirement"])
    if p.get("technology"):
        required = skill_names([p["technology"], *required])
    ref = p.get("reference") or p.get("id") or slug
    return Job(
        source="nofluffjobs",
        key=f"nofluffjobs:{ref}",
        title=p.get("title", ""),
        company=p.get("name", ""),
        url=OFFER_PAGE.format(slug=slug),
        cities=cities,
        remote=remote,
        workplace="remote" if remote else None,
        levels=[str(s) for s in (p.get("seniority") or [])],
        required_skills=required,
        salary=_listing_salary(p.get("salary")),
        published_at=parse_epoch_ms(p.get("posted")),
    )


class NoFluffJobsSource(Source):
    name = "nofluffjobs"

    def fetch(self) -> list[Job]:
        resp = self.session.post(
            SEARCH_URL,
            params={
                "salaryCurrency": "PLN",
                "salaryPeriod": "month",
                "region": "pl",
                "limit": int(self.config.get("limit", 200)),
            },
            json={"rawSearch": self.config.get("raw_search", "")},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        postings = resp.json().get("postings") or []

        # One offer is returned once per city -> merge them.
        merged: dict[str, Job] = {}
        for p in postings:
            if not isinstance(p, dict):
                continue
            job = parse_posting(p)
            job._slug = p.get("url", "")  # type: ignore[attr-defined]
            if job.key in merged:
                existing = merged[job.key]
                existing.cities = list(dict.fromkeys(existing.cities + job.cities))
                existing.remote = existing.remote or job.remote
            else:
                merged[job.key] = job
        log.info("nofluffjobs: %d postings -> %d offers", len(postings), len(merged))
        return list(merged.values())

    def load_details(self, job: Job) -> None:
        slug = getattr(job, "_slug", None) or job.url.rsplit("/", 1)[-1]
        resp = self.session.get(DETAIL_URL.format(slug=slug), timeout=30)
        resp.raise_for_status()
        d = resp.json()
        reqs = d.get("requirements") or {}
        musts = skill_names(reqs.get("musts"))
        nices = skill_names(reqs.get("nices"))
        if musts:
            job.required_skills = musts
        if nices:
            job.nice_skills = nices
        job.description = html_to_text(reqs.get("description") or d.get("description"))
        seniority = (d.get("basics") or {}).get("seniority")
        if seniority:
            job.levels = [str(s) for s in seniority]
        job.salary = _detail_salary((d.get("essentials") or {}).get("originalSalary")) or job.salary
        time.sleep(1.5)
