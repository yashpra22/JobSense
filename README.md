# JobSense 🎯

> **Automated job discovery powered by LLM resume matching** — watches 1,500+ company career pages, fetches full job descriptions, and ranks every listing against your resume using NVIDIA's Nemotron LLM.

---

## What It Does

JobSense is a self-hosted job intelligence system that runs 24/7 and surfaces only the roles that **actually match your background**. It's not a keyword alert — it reads the full job description, scores it against your resume, and tells you *why* it's a good or bad fit.

- 🔍 **Scrapes 1,500+ companies** across Workday, Greenhouse, Lever, Ashby, Oracle HCM, and custom career portals every 5 hours
- 📄 **Fetches full job descriptions** via platform-specific APIs (Workday CXS, Greenhouse Board API, Lever API, Ashby GraphQL)
- 🤖 **LLM resume matching** — NVIDIA Nemotron scores each JD 0–100% against your `resume.txt`
- 🧹 **Hard pre-filters** — eliminates senior/staff/lead roles, wrong domains, and 3+ YOE requirements before the LLM ever sees them
- 📊 **Live dashboard** at `localhost:5000` — filter, sort, and browse every match with score breakdowns
- 📬 **Email alerts** — real-time email when new jobs clear filters, plus a daily digest
- 🗃️ **SQLite-backed deduplication** — never re-alerted on a job you've already seen

---

## Architecture

```
career_watcher.py          — Scrapes 1,500+ career pages on a 5h cycle
       │
       ├── detect_ats(url) — Routes to the right API backend
       │     ├── Workday CXS API    (with session/CSRF cookie support)
       │     ├── Greenhouse Board API
       │     ├── Lever API
       │     ├── Ashby GraphQL
       │     ├── Oracle HCM REST
       │     └── Playwright headless Chromium (fallback)
       │
       ▼
jd_fetcher.py              — Fetches full JD text per job (parallel, 15 workers)
       │
       ▼
llm_evaluator.py           — Pre-filters by title/YOE, then scores with NVIDIA Nemotron
       │
       ├── seen_jobs.db    — SQLite: deduplication + job details + JD text + scores
       │
       ▼
dashboard.py               — Flask live dashboard (localhost:5000)
send_db_email.py           — Sends HTML email digest of top matches
```

---

## Setup

### 1. Clone & Install

```bash
git clone https://github.com/sahilobhrai/JobSense.git
cd JobSense
```

```bash
pip install playwright beautifulsoup4 playwright-stealth requests flask openai
playwright install chromium
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```bash
# NVIDIA Nemotron LLM (for resume matching & auto-apply)
NVIDIA_API_KEY="nvapi-xxxxxxxxxxxxxxxxxxxx"

# Optional Email alerts
EMAIL_FROM="you@gmail.com"
EMAIL_TO="you@gmail.com"
SMTP_USER="you@gmail.com"
SMTP_PASS="your-16-char-app-password"   # Gmail App Password
```

**Getting a Gmail App Password:**
1. Enable 2-Step Verification on your Google account
2. Go to https://myaccount.google.com/apppasswords
3. Generate an App Password named "JobSense"

**Getting an NVIDIA API Key:**
1. Sign up at https://build.nvidia.com
2. Generate an API key — free tier includes generous credits

### 3. Add Your Resume

Replace `resume.txt` with your actual resume (plain text). This is what the LLM uses to score each job — the more detailed it is, the better the matches.

### 4. Run the Watcher

```bash
python career_watcher.py
```

This starts the main scraping loop. Every 5 hours it visits all 1,500+ company career pages, finds new jobs, fetches their full descriptions, and evaluates them against your resume.

### 5. Run the Dashboard

In a separate terminal:

```bash
python dashboard.py
```

Open `http://localhost:5000` to see all discovered jobs with match scores, sortable and filterable.

---

## Key Files

| File | Purpose |
|---|---|
| `career_watcher.py` | Main scraper — 1,500+ companies, ATS detection, deduplication, email alerts |
| `jd_fetcher.py` | Fetches full job description text from Workday CXS, Greenhouse, Lever, Ashby APIs |
| `llm_evaluator.py` | Pre-filter (title/YOE rules) + NVIDIA Nemotron LLM scoring against resume |
| `dashboard.py` | Flask web dashboard with live job listings, scores, filters, and sorting |
| `send_db_email.py` | HTML email digest of top-matched jobs |
| `resume.txt` | Your resume — the source of truth for all LLM matching |
| `seen_jobs.db` | SQLite database (auto-created, not committed to git) |

---

## Configuration

Edit the top of `career_watcher.py` to tune behaviour:

```python
CHECK_INTERVAL_MINUTES = 300   # Scrape cycle (default: every 5 hours)
CONCURRENCY = 8                # Parallel pages fetched at once
DAILY_DIGEST_HOUR = 8          # Send email digest at 8am local time
VERIFY_URLS = True             # HTTP-verify each job URL before alerting
DB_PATH = "seen_jobs.db"       # SQLite state file
```

---

## Filtering Logic

JobSense uses a **3-layer filter pipeline** before scoring:

### Layer 1 — Title Pre-filter (in `llm_evaluator.py`)
Hard rules applied **before** the LLM (fast, deterministic):
- ❌ Excludes roles requiring 3+ years of experience (regex on JD text)
- ❌ Excludes senior/staff/lead/principal/architect titles
- ❌ Excludes irrelevant domains: DevSecOps, Data Engineering, Integration Engineering, SAP, Salesforce, etc.
- ✅ Passes entry-level, associate, junior, and general SWE roles

### Layer 2 — LLM Evaluation (NVIDIA Nemotron)
For jobs that pass Layer 1, the full JD text is sent to the LLM along with your resume:
- Returns a **match score (0–100%)**
- Returns a **match analysis** explaining the fit

### Layer 3 — Dashboard Filter
The live dashboard lets you additionally filter by:
- Score threshold
- Company
- Location
- Posting date
- Sort by: score, company, date discovered, or last scraped

---

## Supported ATS Platforms

| Platform | Method | Companies |
|---|---|---|
| **Workday** | CXS JSON API (with session cookies for 403 bypass) | ~800+ |
| **Greenhouse** | `boards-api.greenhouse.io` REST API | ~200+ |
| **Lever** | `api.lever.co` REST API | ~100+ |
| **Ashby** | GraphQL API | ~50+ |
| **Oracle HCM** | REST API | ~50+ |
| **Custom / Other** | Playwright headless Chromium + BeautifulSoup | Remaining |

---

## Dashboard Features

- **Live match scores** — 0–100% match against your resume, colour-coded
- **Sort options** — by score, company A–Z, newest first, or identified last (scraping order)
- **One-click apply** — direct link to the application page
- **Match analysis** — LLM's explanation of why a job is or isn't a good fit
- **No duplicates** — deduplication across all sources

---

## Running Long-Term

For unattended operation, use `tmux` or `screen`:

```bash
# Terminal 1 — watcher (scrapes every 5h)
tmux new -s watcher
python career_watcher.py

# Terminal 2 — dashboard (always-on web UI)
tmux new -s dashboard
python dashboard.py
```

On Linux, wrap in a `systemd` service for true background operation.

---

## Stack

| Component | Technology |
|---|---|
| Scraping | Python `asyncio` + Playwright (headless Chromium) + `playwright-stealth` |
| ATS APIs | `requests` (Workday CXS, Greenhouse, Lever, Ashby, Oracle HCM) |
| HTML Parsing | BeautifulSoup4 |
| LLM | NVIDIA Nemotron via `openai`-compatible API |
| Storage | SQLite (`seen_jobs.db`) |
| Dashboard | Flask + vanilla HTML/CSS/JS |
| Email | Python `smtplib` (SMTP, HTML body) |

---

## Notes

- **First run will be slow** — fetching 1,500+ career pages takes time. Subsequent cycles only process new/changed listings.
- **seen_jobs.db is excluded from git** — your job history and personal data stay local.
- **.env is excluded from git** — your API keys and email credentials are never committed.
- **LLM costs** — NVIDIA's free tier is generous for personal use; the evaluator only sends jobs that pass the pre-filter to save credits.