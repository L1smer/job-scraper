"""seen_jobs.json — remembers which offers were already processed between runs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


class State:
    def __init__(self, path: str | Path, keep_days: int = 45):
        self.path = Path(path)
        self.keep_days = keep_days
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        self.seen: dict[str, str] = data.get("seen", {})
        self.failures: dict[str, int] = data.get("failures", {})
        self.last_run: datetime | None = (
            datetime.fromisoformat(data["last_run"]) if data.get("last_run") else None
        )
        self.is_new_file = not self.path.exists()

    def has(self, key: str) -> bool:
        return key in self.seen

    def add(self, key: str) -> None:
        self.seen.setdefault(key, datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def save(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.keep_days)
        self.seen = {
            k: v for k, v in self.seen.items()
            if datetime.fromisoformat(v) >= cutoff
        }
        self.path.write_text(
            json.dumps({"last_run": self.last_run.isoformat(timespec="seconds") if self.last_run else None,
                        "failures": self.failures,
                        "seen": dict(sorted(self.seen.items()))},
                       indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
