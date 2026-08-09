# JobSense

> Self-hosted job intelligence: ATS discovery, JD extraction, evidence-based resume matching, and a guarded auto-apply workflow.

## Architecture

```text
career_watcher.py
      |
      +--> ATS discovery (Workday / Greenhouse / Lever / Ashby / Oracle / fallback)
      |
      v
job details + full JD
      |
      v
llm_evaluator.py
  +-- deterministic eligibility
  +-- structured required/preferred skill evidence
  +-- weighted candidate score
  +-- optional LLM evidence review
      |
      v
SQLite
  +-- legacy watcher tables
  +-- normalized jobs/evaluations
  +-- application state machine + audit metadata
      |
      +--> secure local dashboard
      |
      +--> dry-run auto-apply
      |
      +--> CLI-only live submission (explicit opt-in)
```

## Security model

The dashboard is **localhost-only** and requires a password. All state-changing
requests use Flask-WTF CSRF protection. The web UI can launch only a dry-run
application flow; real submission is intentionally CLI-only and requires
`JOBSENSE_LIVE_APPLY=true` in the private `.env`.

Candidate contact details, resume path, screening answers, and credentials are
loaded from environment variables and are not stored in Python source.

## Setup

### 1. Install

```bash
git clone https://github.com/yashpra22/JobSense.git
cd JobSense
python -m pip install -r requirements.txt
playwright install chromium
```

### 2. Configure private data

```bash
cp .env.example .env
```

Fill the candidate fields and API credentials in `.env`. **Never commit `.env`.**

For the dashboard, set:

```text
DASHBOARD_PASSWORD=<strong-local-password>
DASHBOARD_SECRET_KEY=<random-long-secret>
```

### 3. Add resume

Keep a sanitized skill/profile document in `resume.txt`. Keep the actual PDF
resume local and set `RESUME_PDF_PATH` in `.env`.

### 4. Run

Watcher:

```bash
python career_watcher.py
```

Dashboard:

```bash
python dashboard.py
```

Open `http://127.0.0.1:5000`.

### 5. Auto-apply safety

Default behavior is dry-run:

```bash
python auto_apply/run_auto_apply.py --dry-run --max-jobs 1 --min-score 80
```

Live submission is blocked unless you explicitly set:

```text
JOBSENSE_LIVE_APPLY=true
DRY_RUN=false
```

Then run the CLI manually. The web dashboard never enables live submission.

## Matching pipeline

The evaluator now uses four stages:

1. **Eligibility:** role level, explicit location, timezone restrictions, experience requirements, and mandatory degree requirements.
2. **Structured requirements:** required and preferred sections are separated before skill scoring.
3. **Weighted deterministic score:** role alignment, required/preferred skills, and location contribute separately.
4. **Evidence-based LLM review:** the LLM must return required-skill evidence and missing-required-skills instead of simply counting keywords.

Location filtering deliberately avoids treating arbitrary two-letter strings such
as `CA`, `OR`, or `GA` as geographic evidence.

## Data model

New normalized persistence is available under `storage/`:

- `jobs.job_id` — canonical SHA-256 identity derived from ATS/source ID (URL fallback only).
- `evaluations` — versioned evaluator/model results.
- `applications` — explicit application state machine and audit metadata.

Application states:

```text
pending -> approved -> filling -> submitted -> success
                         |             |
                         +-----------> uncertain
                         +-----------> failed
```

`dry_run` and `skipped` are terminal audit states.

## ATS architecture

`ats/base.py` defines a normalized `ATSAdapter` contract and `ats/registry.py`
provides adapter resolution. Existing discovery code can migrate to these
adapters incrementally instead of keeping all ATS logic inside one module.

## Quality

CI runs on Python 3.11 and 3.12 and performs:

- Python compilation checks
- evaluator regression tests
- canonical-ID/storage tests
- application state-machine tests
- pytest

Run locally:

```bash
pytest -q
python -m compileall -q .
```

## Repository structure

```text
JobSense/
├── career_watcher.py       # existing discovery orchestrator
├── jd_fetcher.py            # JD retrieval
├── llm_evaluator.py         # eligibility + weighted/evidence evaluation
├── dashboard.py             # authenticated localhost dashboard
├── auto_apply/              # guarded browser application workflow
├── ats/                     # normalized ATS adapter boundary
├── evaluation/              # evaluation service boundary
├── storage/                 # canonical IDs + normalized persistence
├── tests/                   # regression and state-machine tests
└── .github/workflows/ci.yml # CI
```

## Branching

`main` remains the stable branch. Hardening work is developed on focused
branches and merged through pull requests after CI passes. Configure GitHub
branch protection to require the CI check and review before merging.
