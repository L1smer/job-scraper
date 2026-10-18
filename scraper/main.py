"""Entry point:  python -m scraper.main [--dry-run] [--init] [--sources justjoin,nofluffjobs]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from scraper.filters import JobFilter
from scraper.http import make_session
from scraper.models import Job, MatchResult
from scraper.notify import Telegram, format_job
from scraper.sources import SOURCES
from scraper.state import State

log = logging.getLogger("scraper")
ROOT = Path(__file__).resolve().parent.parent
FAILURE_ALERT_AFTER = 3  # consecutive failed runs before you get a warning in Telegram


def load_dotenv(path: Path) -> None:
    """Tiny .env loader for local runs (GitHub Actions uses repository secrets)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def run(config: dict, state: State, telegram: Telegram, *, init: bool = False,
        only: set[str] | None = None, sources_override: dict | None = None) -> list[tuple[Job, MatchResult]]:
    max_age = float(config.get("max_age_hours", 3))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age)
    flt = JobFilter(config.get("profile", {}))
    session = make_session()
    source_classes = sources_override or SOURCES

    matches: list[tuple[Job, MatchResult]] = []
    fingerprints_this_run: set[str] = set()
    stats = {"listed": 0, "new": 0, "prefiltered_out": 0, "rejected": 0, "matched": 0}

    for name, cfg in (config.get("sources") or {}).items():
        if not (cfg or {}).get("enabled", True) or (only and name not in only):
            continue
        cls = source_classes.get(name)
        if not cls:
            log.warning("unknown source %r in config", name)
            continue
        source = cls(cfg, session, max_age)
        try:
            jobs = source.fetch()
            state.failures[name] = 0
        except Exception as exc:
            state.failures[name] = state.failures.get(name, 0) + 1
            log.error("source %s failed (%d in a row): %s", name, state.failures[name], exc)
            if state.failures[name] == FAILURE_ALERT_AFTER and not init:
                telegram.send(f"⚠️ Job scraper: <b>{name}</b> failed {FAILURE_ALERT_AFTER} runs in a row.\n"
                              f"The site may have changed its API. Error: <code>{str(exc)[:200]}</code>")
            continue

        stats["listed"] += len(jobs)
        for job in jobs:
            if state.has(job.key):
                continue
            state.add(job.key)
            if job.published_at and job.published_at < cutoff:
                continue  # old offer we simply hadn't seen before (e.g. first run)
            stats["new"] += 1
            if init:
                continue

            ok, reason = flt.prefilter(job)
            if not ok:
                stats["prefiltered_out"] += 1
                log.debug("skip %s — %s", job.title, reason)
                continue
            try:
                source.load_details(job)
            except Exception as exc:
                log.warning("details failed for %s: %s", job.url, exc)

            result = flt.evaluate(job)
            if not result.ok:
                stats["rejected"] += 1
                log.debug("reject %s @ %s — %s", job.title, job.company, result.reason)
                continue
            if job.fingerprint in fingerprints_this_run or state.has(job.fingerprint):
                continue  # same offer already sent from another board
            fingerprints_this_run.add(job.fingerprint)
            state.add(job.fingerprint)
            matches.append((job, result))
            log.info("MATCH %s @ %s (%s)", job.title, job.company, job.source)

    stats["matched"] = len(matches)
    log.info("run summary: %s", stats)

    matches.sort(key=lambda m: m[1].score, reverse=True)
    limit = int(config.get("max_notifications_per_run", 25))
    for job, result in matches[:limit]:
        telegram.send(format_job(job, result))
    if len(matches) > limit:
        telegram.send(f"…and {len(matches) - limit} more matches this hour (raise "
                      f"max_notifications_per_run in config.yaml to see them all).")
    return matches


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hourly job alerts to Telegram")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--state", default=str(ROOT / "seen_jobs.json"))
    ap.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    ap.add_argument("--init", action="store_true", help="mark current offers as seen, send nothing")
    ap.add_argument("--sources", help="comma-separated subset, e.g. justjoin,nofluffjobs")
    ap.add_argument("-v", "--verbose", action="store_true", help="log why each offer was skipped")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    for noisy in ("urllib3", "JobSpy", "jobspy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    load_dotenv(ROOT / ".env")
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    state = State(args.state)
    telegram = Telegram(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"),
                        dry_run=args.dry_run or args.init)
    only = set(args.sources.split(",")) if args.sources else None

    run(config, state, telegram, init=args.init, only=only)
    state.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
