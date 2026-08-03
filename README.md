# intern — Free Dev Opportunity Tracker

Runs once a day (via GitHub Actions, free tier), scans a handful of real
sources for free/scholarship developer programs, certifications, and
hackathons suitable for a junior developer working remotely from anywhere,
uses an LLM to turn the good ones into clean structured data, tracks them
in a Notion pipeline, and pings you on Telegram — both when something new
shows up and again on the day its results are expected.

Verified end-to-end against live data while building this: a single run
pulled 380 raw candidates (Reddit 61, Devpost 18, GitHub 301), 104 passed
the keyword filter, and 40 were new. See `data/opportunities.ics` and
`data/seen.json` for real output from that run.

## How it works

```
sources/          -> fetch raw candidates (Reddit, Devpost, GitHub, Hacker News)
processing/        -> keyword-score + filter (cheap first pass), then
                       Gemini extracts clean structured fields for whatever
                       survives the filter (falls back to the old
                       keyword/dateparser heuristic if Gemini isn't
                       configured or a call fails)
state.py           -> dedupe against data/seen.json so nothing repeats daily
integrations/      -> write data/opportunities.ics + optional Google
                       Calendar push, push new items to Notion, Discord/
                       Telegram daily summary, result-day + countdown reminders
main.py            -> run_pipeline() (fetch->filter->gatekeep->Notion) plus
                       main() (the plain-text batch summary) — run daily by GH Actions
bot/               -> optional 24/7 alternative: same run_pipeline(), but with
                       interactive Telegram buttons instead of plain text
```

### Data sources

| Source | What it covers | How |
|---|---|---|
| **Reddit** (`sources/reddit_source.py`) | Community-shared postings in r/cscareerquestions, r/csMajors, r/developersIndia, etc. | Public `search.rss` feed, no credentials — Reddit closed self-service API app registration in 2026 (see comment at top of the file). Paced with a backoff + a hard 240s time budget per run so an unusually strict rate-limit day can't eat the whole CI job — it just returns whatever it collected and lets the other sources still run. |
| **Devpost** (`sources/devpost_source.py`) | Online hackathons, open for submissions | Devpost's own search JSON endpoint (undocumented but public — fails soft if it ever changes) |
| **GitHub** (`sources/github_source.py`) | Curated "free certifications" / "internships" / "open source programs" list repos, **plus** the SimplifyJobs/PittCSC curated internship list (jointly maintained — same repo) targeted directly | GitHub Search API, by **topic** for the general lists (not a hardcoded repo name, so it self-updates) — the curated internship list is found by searching *within* the `SimplifyJobs` org for a repo matching `Summer<year>-Internships`/`New-Grad-Positions`, since it gets renamed every recruiting season. Handles both markdown-link and raw-HTML-table README formats. |
| **Hacker News** (`sources/hackernews_source.py`) | Real internship/entry-level openings from the monthly "Ask HN: Who is hiring?" thread | Official Algolia HN Search API (`hn.algolia.com`) — free, no auth. Found by searching for stories from the `whoishiring` account (not fuzzy title matching, so it can't accidentally grab that day's "Who wants to be hired?" sibling thread). |

**Investigated but not integrated** (no free/stable public API found, or only third-party/paid scrapers exist — see the project's no-scraping principle): MLH's own event list (only OAuth login API, official), HackerEarth, Google Summer of Code (project-level, not org-level), Outreachy, LFX Mentorship, Microsoft Learn/Google Cloud Skills Boost/AWS Educate challenge pages, Wellfound. Devpost already covers a lot of MLH-run hackathons since many are hosted there.

### Filtering

Plain keyword scoring (`config.py` → `POSITIVE_KEYWORDS` / `NEGATIVE_KEYWORDS`),
not a full NLP model — deliberately, so the whole thing installs and runs
in seconds on a free CI runner, and so the (quota-limited) LLM step below
only ever runs against a small, pre-filtered set. Positive signals: free/
scholarship, remote/global, student/junior/entry-level, certification/
hackathon/internship, recognizable CV-value orgs (Microsoft, AWS, Google
Cloud, GitHub...). Negative signals: tuition/fees, in-person-only,
US-citizens-only, senior-only. Tune `MIN_SCORE` and the keyword weights any
time — nothing else needs to change.

### Smart parsing + QA/prestige gatekeeper (Google Gemini)

`processing/llm_parser.py` sends each *new* (already keyword-filtered)
opportunity to Gemini with a strict JSON response schema. Gemini first
classifies it as one of:
- **TYPE A** — a program (certification/scholarship/bootcamp/hackathon/
  fellowship): must be run by a reputable organizer, genuinely free/fully
  funded, remote/global, with a legitimate link.
- **TYPE B** — a genuine internship/entry-level job actually **offered by
  an employer** (e.g. an HN "who is hiring" post, or a curated internship
  repo entry): must name a real company, be junior/entry-level (being paid
  is normal here — TYPE A's "free" rule doesn't apply to employment), and
  not obviously closed to a Turkey-based applicant.
- Neither — general discussion, questions, news, or **an individual asking
  for a job/referral for themselves** (distinct from an employer's own
  posting) — always rejected.

Only if valid does it extract `program_name`, `application_link`,
`eligibility`, `deadline`/`deadline_time`/`deadline_timezone` (converted to
Europe/Istanbul — see below), `announcement_date`, `summary`, and a Turkish
`turkish_cv_summary`. Free API key: https://aistudio.google.com/apikey
(the default model is the `-lite` variant — the full `flash` model's free
tier tops out at 20 requests/**day**, project-wide, nowhere near enough for
daily volume).

Retries transient errors (429/503/timeouts) a few times with backoff before
giving up. If it still can't get an answer — bad key, persistent outage,
whatever — the opportunity is **rejected**, not assumed valid. An earlier
version defaulted an unreachable LLM to "keep it," which is exactly how an
unrelated job-referral Reddit post once slipped past the gatekeeper and got
broadcast as a "new opportunity"; nothing ships unless Gemini actually
verified it.

### Notion pipeline

`integrations/notion_sync.py` pushes every new opportunity into a Notion
database (Name, Application Link, Eligibility, Deadline, Start Date,
Announcement Date, Summary, Turkish CV Summary, Source, Score, Status) so
you have a running, filterable/sortable board. `Status` starts at `New` and
moves to `Applied`/`Accepted`/`Rejected` — either by hand in Notion, or via
the interactive Telegram bot's buttons (see below).

One manual step is unavoidable (Notion has no CLI or API to create a
workspace/integration from scratch): create an internal integration and
share a page with it. `setup.sh` walks you through exactly that and
auto-creates the database's schema for you — see the comment at the top of
`integrations/notion_sync.py` for the precise steps if you're doing it
without the script.

### Notion dedup (never a duplicate "new opportunity" alert)

Before pushing anything new, `find_existing_notion_page()` also checks
Notion itself for a matching Application Link or Name — on top of the
cheap local `data/seen.json` check. Notion is the source of truth: even if
local state were ever lost, reset, or out of sync, the same opportunity
still can't trigger a second "new opportunity" broadcast.

### Result-day reminders & 10-day deadline countdown

Every run also queries Notion for anything whose **Announcement Date** is
today (a "results might be out" nudge), and separately for anything that's
still `Status = "New"` (i.e. you haven't clicked "✅ Başvurdum" yet) whose
**Deadline** (or **Start Date**, if there's no fixed deadline) is 1-10 days
away — repeating daily until the window passes *or* you apply, whichever
comes first, so a time-sensitive deadline can't get buried under newer
notifications without turning into spam after you've already acted on it.

### Timezone conversion (deadlines in TR time)

`processing/timezone_converter.py` combines Gemini's extracted deadline
date/time/timezone (e.g. "11:59 PM PST") into a single Europe/Istanbul-local
datetime via the stdlib `zoneinfo` — including correctly shifting the
*date* when the conversion crosses midnight (an 11:59 PM PST deadline is
already the next morning in Turkey). Notion's `Deadline` property and every
Telegram message show the converted time labeled "(TR Saatiyle)".

### Interactive bot (optional): buttons that update Notion for you

`bot/` is a 24/7 alternative to the plain GitHub Actions cron: it polls
Telegram continuously so it can react to button taps and slash commands,
on top of running two independent background jobs (`bot/scheduler.py`),
both anchored to Europe/Istanbul via `JobQueue.run_daily` (itself an
APScheduler `AsyncIOScheduler` + `CronTrigger` under the hood):

- **Opportunity Hunter** — scrape → gatekeeper → Notion → broadcast. Runs
  **twice a day** (09:00 & 21:00) so a limited-quota program doesn't sit
  undiscovered for 12+ hours. Also triggerable on demand with `/tara`
  (shares the exact same code, `hunt_and_broadcast()`).
- **Morning Briefing** — result-day + 10-day countdown reminders only, from
  what's already in Notion. Runs **once a day, 09:00 only** — deliberately
  not registered for the evening slot too, so reminders can't double up.

Buttons:
- **New opportunity** messages get an inline **✅ Başvurdum** button —
  tapping it sets that page's Notion `Status` to `Applied` and edits the
  message to confirm.
- **Result-day** reminders get **🎉 Kabul Edildim** / **❌ Reddedildim**
  buttons that set `Status` to `Accepted`/`Rejected` the same way.

Commands (also registered with Telegram's native menu button via
`set_my_commands()` — see `bot/app.py`):

| Command | What it does |
|---|---|
| `/yardim` or `/start` | Lists every command |
| `/tara` | Force-runs the Opportunity Hunter immediately |
| `/bekleyenler` | Everything still `Status="New"` with a future deadline |
| `/basvurularim` | Everything currently `Status="Applied"` |
| `/rapor` | A stats dashboard (counts per Status) |

A Notion page id is embedded in each button's `callback_data` (safe to do —
see the comment at the top of `bot/keyboards.py`), and every handler
checks the clicking chat against `TELEGRAM_CHAT_ID` before touching Notion.

This needs a long-lived process, which GitHub Actions can't provide — see
**Deploying the 24/7 bot** below. Run only `main.py` (via GH Actions) *or*
`bot/` (on an always-on host), never both, or you'll get every
notification twice.

### Dates

Two paths, in order of preference: Gemini's extraction (above), or
`processing/date_parser.py`, which looks for date-like phrases near
trigger words ("deadline", "apply by", "starts on", ...) using
`dateparser`. Both are best-effort on free text — every calendar
event/Notion page keeps the original link so you can double check before
relying on a parsed date.

### Calendar output

**Primary: the `.ics` feed** (`data/opportunities.ics`), because a live
Google Calendar push needs OAuth, and user-OAuth refresh tokens are a bad
fit for an unattended cron job (they can expire/get revoked with no one
watching). Subscribe once:

1. Push this repo to GitHub (public or private — either works for this).
2. Google Calendar → Settings → Add calendar → **From URL** → paste:
   `https://raw.githubusercontent.com/<you>/<repo>/main/data/opportunities.ics`
3. Done — it refreshes automatically every time the daily Action runs and commits.

**Optional: live push** via `integrations/google_calendar.py` using a
Google service account (works headlessly, no consent screen). Setup steps
are documented at the top of that file. Skip it entirely if the `.ics`
feed / Notion is enough for you.

## Setup

### Automated (recommended)

```bash
cd intern
./setup.sh
```

Prompts you once for whatever credentials aren't already in `.env`
(Gemini, Telegram, Notion), creates the Notion database, initializes git,
creates a **private** GitHub repo, sets every GitHub Actions secret via
`gh secret set`, pushes, and triggers the first run. Requires the
[`gh` CLI](https://cli.github.com), already authenticated (`gh auth login`).
Safe to re-run — it reuses whatever's already configured.

### Manual (if you'd rather not run a script that touches your GitHub account)

```bash
cd intern
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env   # fill in at least: a notification channel, Gemini, Notion
python main.py
```

1. **Telegram**: message [@BotFather](https://t.me/BotFather) → `/newbot` →
   copy the token, then message your new bot once and open
   `https://api.telegram.org/bot<token>/getUpdates` to find your chat id.
   (Discord is a supported alternative: channel Settings → Integrations →
   Webhooks → New Webhook → copy URL into `DISCORD_WEBHOOK_URL`.)
2. **Gemini**: free key at https://aistudio.google.com/apikey → `GEMINI_API_KEY`.
3. **Notion**: create an integration at https://www.notion.so/my-integrations
   → `NOTION_TOKEN`. Share a page with it, copy that page's ID →
   `NOTION_PARENT_PAGE_ID`. Run `python scripts/notion_create_database.py`
   once → paste the printed ID into `NOTION_DATABASE_ID`.
4. Push this folder to a new GitHub repo, then repo → Settings → Secrets
   and variables → Actions → add every secret from your `.env`
   (`GITHUB_TOKEN` is provided automatically — don't set it yourself).
5. `.github/workflows/daily.yml` runs every day at 07:00 UTC (10:00 Türkiye
   time) and on-demand from the Actions tab (**Run workflow** button). It
   commits `data/seen.json` and `data/opportunities.ics` back to the repo
   after each run.

## Deploying the 24/7 bot

Only needed if you want the interactive buttons/`/basvurularim` — skip this
entirely if the GitHub Actions cron + plain Telegram messages are enough.

`bot/app.py` runs forever (`Application.run_polling()`), so it needs an
always-on host, not a scheduled job. Two free options:

**Render (recommended — free Background Worker)**
1. Push this repo to GitHub (private is fine — Render can pull from a
   private repo once you connect your GitHub account).
2. [dashboard.render.com](https://dashboard.render.com) → New → **Background
   Worker** → connect the repo.
3. Build command: `pip install -r requirements.txt`. Start command:
   `python -m bot.app`.
4. Add every secret from your `.env` as an environment variable in Render's
   dashboard (Settings → Environment).
5. Deploy. Render restarts the process automatically if it ever crashes.
6. Delete/disable `.github/workflows/daily.yml` (repo → Actions → the
   workflow → "..." → Disable) so the pipeline isn't also running from
   there.

**PythonAnywhere (free tier)**
1. Upload/clone this repo into your PythonAnywhere account (Files or a
   `git clone` from a Bash console).
2. Bash console: `pip install --user -r requirements.txt`.
3. Dashboard → **Tasks** → "Always-on tasks" (free tier includes one) →
   command: `python3 /home/<you>/intern/bot/app.py` (adjust the path).
4. Set the same environment variables PythonAnywhere lets you configure
   for the task, or keep them in `.env` in that same folder (the bot loads
   it automatically).
5. Same as above — disable the GitHub Actions workflow once this is live.

## Tuning

Everything worth adjusting lives in `config.py`: search terms, subreddits,
GitHub topics, keyword weights, `MIN_SCORE`, date-trigger phrases. No other
file should need touching for day-to-day tuning.

## Known limitations (by design, not bugs)

- Devpost's endpoint is undocumented — if it changes shape, that source
  returns nothing rather than crashing the run (check the Action log).
- GitHub's unauthenticated rate limit is 60 requests/hour; in CI the
  `GITHUB_TOKEN` secret raises that a lot, but running the script locally
  without a token will hit it fast if you re-run repeatedly. Export your
  own token locally: `export GITHUB_TOKEN=$(gh auth token)` (if you have
  the `gh` CLI) or a personal access token.
- Reddit's unauthenticated RSS search rate-limits more aggressively than
  its documented API ever did; the 240s time budget in
  `sources/reddit_source.py` means an unlucky day may return fewer Reddit
  results than usual, not zero — and never blocks the other sources.
- Date parsing (Gemini or the heuristic fallback) is best-effort; always
  check the linked source before treating a parsed deadline/announcement
  date as authoritative.
