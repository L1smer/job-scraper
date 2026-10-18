"""Offline tests: sample API payloads -> parsing -> filters -> messages. Run: python -m pytest -q"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from scraper.filters import JobFilter
from scraper.main import run
from scraper.models import Job
from scraper.notify import Telegram, format_job
from scraper.sources.base import Source
from scraper.sources.justjoin import parse_offer
from scraper.sources.nofluffjobs import parse_posting
from scraper.state import State

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
NOW = datetime.now(timezone.utc)
iso = lambda dt: dt.isoformat().replace("+00:00", "Z")  # noqa: E731

JJIT_OFFER = {
    "slug": "acme-frontend-developer-wroclaw-javascript",
    "title": "Frontend Developer (React)",
    "companyName": "Acme sp. z o.o.",
    "locations": [{"city": "Wrocław"}, {"city": "Warszawa"}],
    "workplaceType": "hybrid",
    "experienceLevel": "mid",
    "requiredSkills": [{"name": "React", "level": 4}, {"name": "TypeScript", "level": 3},
                       {"name": "GraphQL", "level": 3}],
    "niceToHaveSkills": [{"name": "Next.js", "level": 2}],
    "employmentTypes": [{"from": 14000, "to": 19000, "currency": "pln", "unit": "month",
                         "type": "b2b", "currencySource": "original"}],
    "publishedAt": iso(NOW - timedelta(minutes=20)),
}

NFJ_POSTING = {
    "id": "XYZ123", "reference": "XYZ", "url": "react-native-developer-beta-remote",
    "title": "React Native Developer", "name": "Beta S.A.",
    "location": {"places": [{"city": "Remote"}, {"city": "Kraków"}], "fullyRemote": True},
    "posted": int((NOW - timedelta(minutes=40)).timestamp() * 1000),
    "seniority": ["Junior", "Mid"],
    "tiles": {"values": [{"value": "React Native", "type": "requirement"},
                         {"value": "TypeScript", "type": "requirement"}]},
    "salary": {"from": 12000, "to": 16000, "type": "b2b", "currency": "PLN"},
}


def test_parse_justjoin():
    job = parse_offer(JJIT_OFFER)
    assert job.key == "justjoin:acme-frontend-developer-wroclaw-javascript"
    assert job.cities == ["Wrocław", "Warszawa"]
    assert job.required_skills == ["React", "TypeScript", "GraphQL"]
    assert job.levels == ["mid"]
    assert "14 000–19 000 PLN/month b2b" == job.salary


def test_parse_nofluff():
    job = parse_posting(NFJ_POSTING)
    assert job.remote is True and job.cities == ["Kraków"]
    assert job.levels == ["Junior", "Mid"]
    assert job.required_skills == ["React Native", "TypeScript"]


def test_filters_requirements():
    flt = JobFilter(CONFIG["profile"])
    job = parse_offer(JJIT_OFFER)
    job.description = "We build dashboards in React and TypeScript. 2-3 years of experience."
    assert flt.prefilter(job)[0]
    res = flt.evaluate(job)
    assert res.ok, res.reason
    assert "react" in res.matched_skills and res.missing_required == ["GraphQL"]


def test_rejections():
    flt = JobFilter(CONFIG["profile"])
    base = dict(source="x", key="k", company="C", url="u", cities=["Wrocław"])
    assert not flt.prefilter(Job(title="Senior React Developer", **base))[0]
    assert not flt.prefilter(Job(title="Angular Developer", **base))[0]
    assert not flt.prefilter(Job(title="React Developer", levels=["senior"], **base))[0]
    assert not flt.prefilter(Job(title="React Developer", **{**base, "cities": ["Gdańsk"]}))[0]
    assert flt.prefilter(Job(title="React Developer", remote=True, **{**base, "cities": ["Gdańsk"]}))[0]

    years = Job(title="React Developer", description="You have 5+ years with React", **base)
    assert not flt.evaluate(years).ok
    ok_range = Job(title="React Developer", description="3-5 years with React", **base)
    assert flt.evaluate(ok_range).ok
    angular_must = Job(title="Frontend Developer", required_skills=["Angular", "TypeScript"],
                       description="Angular app", **base)
    assert not flt.evaluate(angular_must).ok
    too_many_missing = Job(title="Fullstack Developer", description="React plus stuff",
                           required_skills=["React", "Elixir", "Kafka", "Terraform"], **base)
    assert not flt.evaluate(too_many_missing).ok


def test_message_format():
    job = parse_offer(JJIT_OFFER)
    res = JobFilter(CONFIG["profile"]).evaluate(job)
    msg = format_job(job, res)
    assert "<b>Frontend Developer (React)</b>" in msg and "Open on JustJoin.it" in msg


class FakeSource(Source):
    jobs: list = []

    def fetch(self):
        return list(self.jobs)

    def load_details(self, job):
        job.description = job.description or "React, TypeScript, REST API. Nice: Next.js."


def test_end_to_end_dedup(tmp_path, capsys):
    fresh = parse_offer(JJIT_OFFER)
    old = parse_offer({**JJIT_OFFER, "slug": "old", "publishedAt": iso(NOW - timedelta(days=2))})
    senior = parse_offer({**JJIT_OFFER, "slug": "sen", "title": "Senior React Dev"})
    nfj = parse_posting(NFJ_POSTING)
    # Same Acme job also found on LinkedIn -> must be sent only once
    linkedin_dup = Job(source="linkedin", key="linkedin:1", title="Frontend Developer (React)",
                       company="Acme", url="https://linkedin.com/jobs/view/1", cities=["Wrocław"],
                       description="React TypeScript")
    FakeSource.jobs = [fresh, old, senior, nfj, linkedin_dup]

    cfg = {**CONFIG, "sources": {"fake": {"enabled": True}}}
    state = State(tmp_path / "seen.json")
    tg = Telegram(None, None, dry_run=True)
    matches = run(cfg, state, tg, sources_override={"fake": FakeSource})
    titles = sorted(j.title for j, _ in matches)
    assert titles == ["Frontend Developer (React)", "React Native Developer"]
    state.save()

    # Second run: nothing new
    state2 = State(tmp_path / "seen.json")
    assert run(cfg, state2, tg, sources_override={"fake": FakeSource}) == []


def test_jobspy_row_with_missing_values(monkeypatch):
    import pandas as pd
    import jobspy
    from scraper.sources.jobspy_source import JobSpySource

    df = pd.DataFrame([{
        "id": "li-42", "site": "linkedin", "job_url": "https://www.linkedin.com/jobs/view/42",
        "title": "Junior React Developer", "company": "Gamma", "location": "Wrocław, Dolnośląskie, Poland",
        "is_remote": False, "job_level": "entry level", "description": float("nan"),
        "min_amount": float("nan"), "max_amount": float("nan"), "currency": None, "interval": None,
        "skills": None,
    }])
    calls = []
    monkeypatch.setattr(jobspy, "scrape_jobs", lambda **kw: calls.append(kw) or df)
    src = JobSpySource({"searches": [{"search_term": "react", "location": "Wrocław"}]}, None, 3)
    monkeypatch.setattr("time.sleep", lambda s: None)
    [job] = src.fetch()
    assert calls[0]["hours_old"] == 3 and calls[0]["fetch_description"] is True
    assert job.key == "linkedin:li-42" and job.description == "" and job.salary is None
    flt = JobFilter(CONFIG["profile"])
    assert flt.prefilter(job)[0]
    res = flt.evaluate(job)
    assert res.ok and not res.requirements_checked
