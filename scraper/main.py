"""Entry point:  python -m scraper.main [--dry-run] [--init] [--test HOURS] [--sources justjoin,nofluffjobs] [-v]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter, defaultdict
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


def window_hours(config: dict, state: State, now: datetime, override: float | None) -> float:
    """How far back to look. Normally max_age_hours, but if GitHub skipped scheduled runs
    (it often does) we look back to the last successful run so nothing published in the gap is lost."""
    if override:
        return override
    hours = float(config.get("max_age_hours", 3))
    if state.last_run:
        since_last = (now - state.last_run).total_seconds() / 3600 + 0.25
        hours = max(hours, min(since_last, float(config.get("max_catchup_hours", 24))))
    return hours


def run(config: dict, state: State, telegram: Telegram, *, init: bool = False,
        only: set[str] | None = None, sources_override: dict | None = None,
        max_age_override: float | None = None, test: bool = False,
        per_source_limit: int | None = None) -> list[tuple[Job, MatchResult]]:
    now = datetime.now(timezone.utc)
    max_age = window_hours(config, state, now, max_age_override)
    cutoff = now - timedelta(hours=max_age)
    log.info("looking at offers published in the last %.1f h%s", max_age, " (TEST MODE)" if test else "")
    flt = JobFilter(config.get("profile", {}))
    session = make_session()
    source_classes = sources_override or SOURCES

    matches: list[tuple[Job, MatchResult]] = []
    fingerprints_this_run: set[str] = set()
    failed: list[str] = []

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
            failed.append(name)
            if state.failures[name] == FAILURE_ALERT_AFTER and not init and not test:
                telegram.send(f"⚠️ Job scraper: <b>{name}</b> failed {FAILURE_ALERT_AFTER} runs in a row.\n"
                              f"The site may have changed its API. Error: <code>{str(exc)[:200]}</code>")
            continue

        # per-platform counters (jobspy yields linkedin + indeed)
        stats: dict[str, Counter] = defaultdict(Counter)
        for job in jobs:
            st = stats[job.source]
            st["listed"] += 1
            if not test:
                if state.has(job.key):
                    st["already seen"] += 1
                    continue
                state.add(job.key)
            if job.published_at and job.published_at < cutoff:
                st["too old"] += 1
                continue
            st["new"] += 1
            if init:
                continue

            ok, reason = flt.prefilter(job)
            if not ok:
                bucket = reason.split()[0]  # title / seniority / location
                st[f"skip: {bucket}"] += 1
                # title rejections are the bulk and boring; the rest are worth seeing
                level = logging.DEBUG if bucket == "title" else logging.INFO
                log.log(level, "  ✗ %-10s %s @ %s — %s", job.source, job.title, job.company, reason)
                continue
            try:
                source.load_details(job)
            except Exception as exc:
                log.warning("details failed for %s: %s", job.url, exc)

            result = flt.evaluate(job)
            if not result.ok:
                st["skip: requirements"] += 1
                log.info("  ✗ %-10s %s @ %s — %s", job.source, job.title, job.company, result.reason)
                continue
            if job.fingerprint in fingerprints_this_run or (not test and state.has(job.fingerprint)):
                st["duplicate"] += 1
                continue
            fingerprints_this_run.add(job.fingerprint)
            if not test:
                state.add(job.fingerprint)
            st["MATCH"] += 1
            matches.append((job, result))
            log.info("  ✓ %-10s %s @ %s", job.source, job.title, job.company)

        if not stats:
            log.info("[%s] 0 listings returned", name)
        for platform, st in stats.items():
            log.info("[%s] %s", platform, ", ".join(f"{k}={v}" for k, v in st.items()))

    log.info("total matches: %d", len(matches))
    matches.sort(key=lambda m: m[1].score, reverse=True)
    if per_source_limit:
        per: dict[str, int] = defaultdict(int)
        limited = []
        for job, res in matches:
            if per[job.source] < per_source_limit:
                per[job.source] += 1
                limited.append((job, res))
        matches = limited

    prefix = "🧪 <b>TEST</b> — " if test else ""
    if test:
        found = Counter(j.source for j, _ in matches)
        telegram.send(f"🧪 <b>Test run</b>: offers from the last {max_age:g} h, ignoring seen jobs.\n"
                      + ("Matches per platform: " + ", ".join(f"{k}: {v}" for k, v in found.items())
                         if found else "No matches on any platform.")
                      + (f"\n⚠️ Failed: {', '.join(failed)} (see the Actions log)" if failed else ""))
    limit = int(config.get("max_notifications_per_run", 25))
    for job, result in matches[:limit]:
        telegram.send(prefix + format_job(job, result))
    if len(matches) > limit:
        telegram.send(f"…and {len(matches) - limit} more matches (raise "
                      f"max_notifications_per_run in config.yaml to see them all).")
    if not test and not init:
        state.last_run = now
    return matches


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hourly job alerts to Telegram")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--state", default=str(ROOT / "seen_jobs.json"))
    ap.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    ap.add_argument("--init", action="store_true", help="mark current offers as seen, send nothing")
    ap.add_argument("--sources", help="comma-separated subset, e.g. justjoin,nofluffjobs")
    ap.add_argument("--test", type=float, metavar="HOURS",
                    help="test mode: offers from the last HOURS, ignore seen jobs, save nothing, "
                         "max 3 per platform, messages marked TEST")
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

    if args.test:
        run(config, state, telegram, only=only, test=True,
            max_age_override=args.test, per_source_limit=3)
        return 0  # test runs never touch seen_jobs.json
    run(config, state, telegram, init=args.init, only=only)
    state.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
