from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone

import requests

from scraper.models import Job, MatchResult

log = logging.getLogger(__name__)

SOURCE_NAMES = {
    "justjoin": "JustJoin.it",
    "nofluffjobs": "NoFluffJobs",
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "glassdoor": "Glassdoor",
    "google": "Google Jobs",
}


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    minutes = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 0)} min ago"
    return f"{minutes // 60} h {minutes % 60} min ago"


def format_job(job: Job, match: MatchResult) -> str:
    e = html.escape
    where = ", ".join(job.cities[:4]) or "—"
    if job.remote:
        where += " · remote"
    elif job.workplace:
        where += f" · {job.workplace}"

    lines = [f"🆕 <b>{e(job.title)}</b>", f"🏢 {e(job.company or '—')}", f"📍 {e(where)}"]
    meta = []
    if job.levels:
        meta.append("🎚 " + e(", ".join(job.levels)))
    if job.salary:
        meta.append("💰 " + e(job.salary))
    if meta:
        lines.append("  ".join(meta))
    if job.info:
        lines.append("ℹ️ " + e(job.info))
    if match.matched_skills:
        lines.append("✅ Your skills: " + e(", ".join(match.matched_skills)))
    if match.missing_required:
        lines.append("⚠️ Must-haves you don't list: " + e(", ".join(match.missing_required)))
    if not match.requirements_checked:
        lines.append("❔ No description returned — requirements not checked")
    source = SOURCE_NAMES.get(job.source, job.source)
    footer = f'🔗 <a href="{e(job.url, quote=True)}">Open on {e(source)}</a>'
    if job.published_at:
        footer += f" · {_ago(job.published_at)}"
    lines.append(footer)
    return "\n".join(lines)


class Telegram:
    def __init__(self, token: str | None, chat_id: str | None, dry_run: bool = False):
        self.token, self.chat_id, self.dry_run = token, chat_id, dry_run
        if not dry_run and not (token and chat_id):
            raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (or use --dry-run)")

    def send(self, text: str) -> bool:
        if self.dry_run:
            print("-" * 60)
            print(text)
            return True
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        for _ in range(3):
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(int(wait) + 1)
                continue
            if not resp.ok:
                log.error("Telegram error %s: %s", resp.status_code, resp.text[:300])
                return False
            time.sleep(0.4)
            return True
        return False
