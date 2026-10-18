from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime


def normalize(text: str | None) -> str:
    """Lowercase, strip Polish/other diacritics, collapse whitespace."""
    if not text:
        return ""
    text = text.lower().replace("ł", "l")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Job:
    source: str                      # "justjoin", "nofluffjobs", "linkedin", "indeed", ...
    key: str                         # unique id, stored in seen_jobs.json
    title: str
    company: str
    url: str
    cities: list[str] = field(default_factory=list)
    remote: bool | None = None
    workplace: str | None = None     # remote / hybrid / office (if the board says)
    levels: list[str] = field(default_factory=list)          # seniority, if the board gives it
    required_skills: list[str] = field(default_factory=list)  # structured must-haves
    nice_skills: list[str] = field(default_factory=list)      # structured nice-to-haves
    description: str = ""
    salary: str | None = None
    published_at: datetime | None = None
    info: str | None = None          # extra line for the message (e.g. LinkedIn job level)

    @property
    def fingerprint(self) -> str:
        """Same job posted on several boards -> same fingerprint."""
        title = re.sub(r"[^a-z0-9 ]", " ", normalize(self.title))
        company = " ".join(re.sub(r"[^a-z0-9 ]", " ", normalize(self.company)).split())
        company = re.sub(r"\b(sp z o o|sp k|s a|sa|ltd|inc|gmbh|llc|group)\b", " ", company)
        return "fp:" + " ".join(title.split()) + "|" + " ".join(company.split())


@dataclass
class MatchResult:
    ok: bool
    reason: str = ""
    matched_skills: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    requirements_checked: bool = True

    @property
    def score(self) -> int:
        return len(self.matched_skills) * 2 - len(self.missing_required)
