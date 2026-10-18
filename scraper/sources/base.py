from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from scraper.models import Job


def skill_names(items: Any) -> list[str]:
    """Boards return skills as strings or dicts ({name|value, level}); flatten to names."""
    names: list[str] = []
    for item in items or []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name") or item.get("value") or item.get("label") or ""
        else:
            continue
        name = str(name).strip()
        if name and name.lower() not in (n.lower() for n in names):
            names.append(name)
    return names


def parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_epoch_ms(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


class Source:
    name = "base"

    def __init__(self, config: dict, session: requests.Session, max_age_hours: float):
        self.config = config or {}
        self.session = session
        self.max_age_hours = max_age_hours

    def fetch(self) -> list[Job]:
        """Return the newest listings (cheap call, no details)."""
        raise NotImplementedError

    def load_details(self, job: Job) -> None:
        """Fill description / structured requirements. Called only for candidates."""
        return None
