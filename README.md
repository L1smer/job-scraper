# job-scraper — hourly job alerts to Telegram

Checks **JustJoin.it**, **NoFluffJobs**, **LinkedIn** and **Indeed** every hour. Filters offers by
title, seniority, location and **requirements**, and sends only new matches to your Telegram.
Runs free on GitHub Actions; no server is needed.

```
config.yaml ─► fetch newest listings ─► skip already seen (seen_jobs.json)
            ─► prefilter (title / seniority / city)  ─► download offer details
            ─► requirements filter (must-haves, your skills, "5+ years")  ─► Telegram
```

Example message:

```
🆕 Frontend Developer (React)
🏢 Acme sp. z o.o.
📍 Wrocław, Warszawa · hybrid
🎚 mid  💰 14 000–19 000 PLN/month b2b
✅ Your skills: react, typescript, next.js
⚠️ Must-haves you don't list: GraphQL
🔗 Open on JustJoin.it · 20 min ago
```

---

## 1. Create the Telegram bot (2 min)

1. In Telegram, open **@BotFather** → `/newbot` → pick a name and a username ending in `bot`.
2. Copy the token it gives you (`123456789:AAE…`). Keep it secret.
3. Send any message (e.g. `hi`) to your new bot.
4. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number in
   `"chat":{"id": … }`. That's your **chat ID**.

## 2. Test locally on Windows

```powershell
cd C:\IT\job-scraper
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then put your token + chat ID into .env

python -m scraper.main --dry-run -v    # prints matches, sends nothing, logs why offers were skipped
python -m scraper.main                 # real run -> messages in Telegram
python -m pytest -q                    # offline tests
```

The dry run also writes `seen_jobs.json`. Delete it if you want the next run to start fresh.

## 3. Run it every hour on GitHub

0. **One manual step:** move `job-alerts.yml` from the project root into `.github\workflows\`.
   That folder already exists.
   ```powershell
   move job-alerts.yml .github\workflows\job-alerts.yml
   ```
1. Create a new repo on GitHub, e.g. `job-scraper`. **Private** is fine.
2. Push this folder:
   ```powershell
   git init
   git add .
   git commit -m "Job alerts"
   git branch -M main
   git remote add origin https://github.com/L1smer/job-scraper.git
   git push -u origin main
   ```
3. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Go to **Settings → Actions → General → Workflow permissions** and select **Read and write permissions**.
   The bot needs this to commit `seen_jobs.json`.
5. Go to **Actions → job-alerts → Run workflow** to test it now. After that it runs at :07 every hour.

After each run the workflow commits `seen_jobs.json`, so offers are never sent twice.
The bot commits to the repo every hour, so run `git pull` before you push your own changes.

> **Private-repo minutes:** private repos get a monthly quota of free Actions minutes. One run takes
> about 1–2 minutes, so ~720 runs a month fits into the free plan, but check
> *Settings → Billing → Usage* after the first days. Public repos have no limit.
>
> GitHub cron can start a few minutes late, and it pauses schedules in repos with no activity for 60 days.
> If that happens, open **Actions** and click **Enable workflow**.

## 4. Tune the filters (`config.yaml`)

| Setting | What it does |
|---|---|
| `title_include` / `title_exclude` | Regex on the job title. This is the first and cheapest filter. |
| `seniority_allowed` | `junior`, `mid`… Only applied where the board gives a level (JustJoin.it, NoFluffJobs). |
| `my_skills` | Your stack. `a\|b` = alternative spellings. Shown as ✅ in the message. |
| `min_skill_matches` | The offer must mention at least N of your skills. |
| `reject_if_required` | Drop the offer if a **must-have** is e.g. Angular or PHP. |
| `max_missing_required` | Drop the offer if it has more than N must-haves you don't list in `my_skills`. |
| `ignore_missing` | Must-haves that never count as missing (English, Git, Jest…). |
| `reject_patterns` | Regex on the description. The default rejects "5+ years" but keeps "3-5 years". |
| `locations` | Cities you accept. With `allow_remote: true`, remote offers always pass. |
| `max_age_hours` | Only offers published within this window count as new. |

How precise the requirements check is depends on the board:

- **JustJoin.it / NoFluffJobs** list must-haves as structured data, so the must-have and
  missing-skill rules work exactly.
- **LinkedIn / Indeed** only provide description text. The scraper matches your skills and the
  `reject_patterns` against it. If LinkedIn returns no description, the job is still sent with
  "❔ requirements not checked".

To change what's searched, edit `sources.nofluffjobs.raw_search` (same syntax as the NoFluffJobs
search box) and `sources.jobspy.searches`.

## Troubleshooting

- **You get "⚠️ <source> failed 3 runs in a row".** JustJoin.it and NoFluffJobs use the internal
  JSON API behind their websites, not an official one, so they can change it without warning.
  Check the Actions log for the error. Keep the boards' own email alerts on as a backup.
- **LinkedIn returns 0 results or 429 errors.** GitHub runner IPs are shared, and LinkedIn
  rate-limits them. Lower `results_wanted` or use fewer searches. Indeed is much more tolerant.
- **justjoin: 0 listings every run.** Set `categories: []`, or try slugs from justjoin.it URLs
  (`javascript`, `mobile`).
- **Too many or too few alerts.** Run `python -m scraper.main --dry-run -v` locally. The log shows
  the reason each offer was skipped.

## Files

```
scraper/main.py              pipeline + CLI (--dry-run, --init, --sources, -v)
scraper/filters.py           prefilter + requirements filter
scraper/sources/justjoin.py  justjoin.it/api/candidate-api
scraper/sources/nofluffjobs.py  nofluffjobs.com/api/search/posting
scraper/sources/jobspy_source.py  LinkedIn / Indeed via python-jobspy
scraper/notify.py            Telegram formatting + sending
scraper/state.py             seen_jobs.json (entries expire after 45 days)
.github/workflows/job-alerts.yml  hourly schedule
```
