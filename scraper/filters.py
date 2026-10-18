"""Decides whether an offer is worth a notification.

Two stages so we only download offer details for plausible candidates:
  prefilter() — cheap fields from the listing: title, seniority, location
  evaluate()  — after details are loaded: requirements, skills, red-flag phrases
"""
from __future__ import annotations

import re
from functools import lru_cache

from scraper.models import Job, MatchResult, normalize

LEVEL_ALIASES = {
    "trainee": "trainee", "intern": "trainee", "internship": "trainee", "staz": "trainee",
    "junior": "junior", "entry level": "junior", "entry": "junior",
    "mid": "mid", "regular": "mid", "medium": "mid", "middle": "mid", "mid level": "mid",
    "senior": "senior", "expert": "senior", "lead": "senior", "c_level": "senior",
    "c level": "senior", "manager": "senior", "principal": "senior",
}


@lru_cache(maxsize=None)
def term_regex(term: str) -> re.Pattern:
    """'next.js|nextjs' -> matches either spelling as a whole word."""
    alts = [re.escape(normalize(a)) for a in term.split("|") if a.strip()]
    return re.compile(r"(?<![a-z0-9.])(?:" + "|".join(alts) + r")(?![a-z0-9])")


@lru_cache(maxsize=None)
def user_regex(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


def skill_label(term: str) -> str:
    return term.split("|")[0]


class JobFilter:
    def __init__(self, profile: dict):
        self.p = profile or {}
        self.my_skills: list[str] = self.p.get("my_skills") or []
        self.cities = {normalize(c) for c in (self.p.get("locations") or {}).get("cities") or []}
        self.allow_remote = (self.p.get("locations") or {}).get("allow_remote", True)
        self.allowed_levels = {LEVEL_ALIASES.get(normalize(l), normalize(l))
                               for l in self.p.get("seniority_allowed") or []}

    # ---------- stage 1 ----------
    def prefilter(self, job: Job) -> tuple[bool, str]:
        title = job.title or ""
        includes = self.p.get("title_include") or []
        if includes and not any(user_regex(r).search(title) for r in includes):
            return False, "title not in title_include"
        for r in self.p.get("title_exclude") or []:
            if user_regex(r).search(title):
                return False, f"title matches exclude {r!r}"

        if self.allowed_levels and job.levels:
            levels = {LEVEL_ALIASES.get(normalize(l), normalize(l)) for l in job.levels}
            if not levels & self.allowed_levels:
                return False, f"seniority {job.levels}"

        if self.cities or not self.allow_remote:
            if not self._location_ok(job):
                return False, f"location {job.cities} remote={job.remote}"
        return True, ""

    def _location_ok(self, job: Job) -> bool:
        if job.remote and self.allow_remote:
            return True
        if not job.cities:
            return job.remote is None  # unknown location -> don't throw it away
        for city in job.cities:
            c = normalize(city)
            if any(target in c for target in self.cities):
                return True
            if self.allow_remote and "remote" in c:
                return True
        return False

    # ---------- stage 2 ----------
    def evaluate(self, job: Job) -> MatchResult:
        text = normalize(" ".join([job.title, job.description, *job.required_skills, *job.nice_skills]))

        for r in self.p.get("reject_patterns") or []:
            m = user_regex(r).search(job.description or "")
            if m:
                return MatchResult(False, f"description matches {m.group(0)!r}")

        required = job.required_skills
        for bad in self.p.get("reject_if_required") or []:
            rx = term_regex(bad)
            hit = next((s for s in required if rx.search(normalize(s))), None)
            if hit:
                return MatchResult(False, f"requires {hit}")

        matched = [skill_label(s) for s in self.my_skills if term_regex(s).search(text)]
        known = [*self.my_skills, *(self.p.get("ignore_missing") or [])]
        missing = [s for s in required
                   if not any(term_regex(t).search(normalize(s)) for t in known)]

        has_requirements = bool(job.description.strip() or required)
        if not has_requirements:
            # LinkedIn sometimes returns no description: keep the job, flag it in the message.
            return MatchResult(True, "requirements unknown", matched, [], requirements_checked=False)

        if len(matched) < int(self.p.get("min_skill_matches", 1)):
            return MatchResult(False, "not enough of my skills", matched, missing)
        max_missing = self.p.get("max_missing_required")
        if max_missing is not None and len(missing) > int(max_missing):
            return MatchResult(False, f"missing must-haves: {missing}", matched, missing)
        return MatchResult(True, "", matched, missing)
