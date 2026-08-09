"""
auto_apply/run_auto_apply.py — CLI Entry Point for JobSense Auto-Apply Engine

Usage:
    python auto_apply/run_auto_apply.py
    python auto_apply/run_auto_apply.py --dry-run        # fill forms, don't submit (default)
    python auto_apply/run_auto_apply.py --no-dry-run     # actually submit applications
    python auto_apply/run_auto_apply.py --max-jobs 3     # limit to 3 jobs this run
    python auto_apply/run_auto_apply.py --min-score 85   # only 85%+ matches
    python auto_apply/run_auto_apply.py --portal Greenhouse  # only Greenhouse jobs

This script:
  1. Migrates seen_jobs.db (adds applied/applied_at/apply_result columns safely)
  2. Reads top-scoring unapplied jobs from the database
  3. Displays a table and asks for confirmation before each application
  4. Runs the browser-use AI agent to fill (and optionally submit) each form
  5. Records the result back to seen_jobs.db
  6. Prints a final summary
"""

import argparse
import asyncio
import sqlite3
import sys
import os

# Ensure the JobSense root is on sys.path so `auto_apply.*` imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_apply.config import (
    APPLY_SCORE_THRESHOLD,
    CANDIDATE,
    DB_PATH,
    DRY_RUN,
    MAX_JOBS_PER_RUN,
)
from auto_apply.db_reader import get_top_jobs, print_job_table
from auto_apply.db_updater import get_apply_stats, mark_applied, migrate_db
from auto_apply.applier import apply_to_job


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="JobSense Auto-Apply Engine — applies to top-matched jobs automatically.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python auto_apply/run_auto_apply.py                     # dry run, top 5 jobs >= 80%
  python auto_apply/run_auto_apply.py --no-dry-run        # actually submit applications
  python auto_apply/run_auto_apply.py --min-score 90      # only 90%+ matches
  python auto_apply/run_auto_apply.py --max-jobs 1        # apply to at most 1 job
  python auto_apply/run_auto_apply.py --portal Greenhouse # Greenhouse only
        """,
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=None,
        help="Fill forms but do NOT submit (default from config.py)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually submit applications (use with caution!)",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help=f"Maximum number of jobs to apply to this run (default: {MAX_JOBS_PER_RUN})",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help=f"Minimum match score %% to consider (default: {APPLY_SCORE_THRESHOLD})",
    )
    parser.add_argument(
        "--portal",
        type=str,
        default=None,
        choices=["Greenhouse", "Lever", "Ashby", "Workday", "Oracle HCM", "Other"],
        help="Only apply to jobs on a specific portal",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        choices=["nvidia", "ollama"],
        help="LLM provider to use (default: nvidia/ollama from config.py)",
    )
    parser.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=None,
        help="Run browser in background silently without opening GUI window",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Open visible Playwright Chromium browser on screen (default)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        dest="auto_confirm",
        action="store_true",
        default=False,
        help="Bypass all interactive y/n confirmation prompts (auto-confirm)",
    )
    return parser.parse_args()


def _confirm(prompt: str, auto_confirm: bool = False) -> bool:
    """Ask user yes/no, return True for yes. Auto-confirms if auto_confirm is True or stdin is non-interactive."""
    if auto_confirm:
        print(f"\n  {prompt} [y/n]: Auto-confirmed (--yes / Web UI mode)")
        return True
    while True:
        try:
            answer = input(f"\n  {prompt} [y/n]: ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            print("  Please enter y or n.")
        except (EOFError, KeyboardInterrupt):
            return True


async def _run(args: argparse.Namespace) -> None:
    """Main async runner."""
    # ── Resolve settings (CLI args override config.py defaults)
    dry_run   = args.dry_run if args.dry_run is not None else DRY_RUN
    max_jobs  = args.max_jobs  if args.max_jobs  is not None else MAX_JOBS_PER_RUN
    min_score = args.min_score if args.min_score is not None else APPLY_SCORE_THRESHOLD
    portal_filter = [args.portal] if args.portal else None

    # ── Banner
    mode_label = "DRY RUN (no submit)" if dry_run else "LIVE (will SUBMIT applications!)"
    print("\n" + "=" * 65)
    print("  [JobSense Auto-Apply Engine]")
    print(f"  Mode      : {mode_label}")
    print(f"  Min Score : {min_score:.0f}%")
    print(f"  Max Jobs  : {max_jobs}")
    print(f"  Portal    : {args.portal or 'All'}")
    print(f"  Candidate : {CANDIDATE['full_name']} <{CANDIDATE['email']}>")
    print(f"  Resume    : {CANDIDATE['resume_pdf_path']}")
    print("=" * 65)

    # ── Safety confirmation for live mode
    if not dry_run:
        print("\n  [WARNING] You are in LIVE mode. Applications will be SUBMITTED.")
        if not _confirm("Are you sure you want to submit real applications?", auto_confirm=args.auto_confirm):
            print("  Aborted. Run with --dry-run to fill forms without submitting.")
            return

    # ── Migrate database (safe, idempotent)
    print(f"\n  Connecting to database: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print(f"\n  ERROR: Database not found at: {DB_PATH}")
        print("  Make sure career_watcher.py has been run at least once to create the database.")
        return

    conn = sqlite3.connect(DB_PATH)
    migrate_db(conn)

    # ── Show current stats
    stats = get_apply_stats(conn)
    print(f"\n  Current DB Stats:")
    print(f"    * Applied successfully : {stats['success']}")
    print(f"    * Dry-run completed    : {stats['dry_run']}")
    print(f"    * Failed attempts      : {stats['failed']}")
    print(f"    * Pending (unapplied)  : {stats['pending']}")

    # ── Fetch top jobs
    print(f"\n  Fetching top jobs (score >= {min_score:.0f}%, max {max_jobs})...")
    jobs = get_top_jobs(
        threshold=min_score,
        max_count=max_jobs,
        db_path=DB_PATH,
        portal_filter=portal_filter,
    )

    if not jobs:
        print(f"\n  No unapplied jobs found with score >= {min_score:.0f}%.")
        print("  Tip: Lower --min-score or wait for career_watcher.py to find more jobs.")
        conn.close()
        return

    print(f"\n  Found {len(jobs)} job(s) to process:\n")
    print_job_table(jobs)

    # ── Process each job
    results = {"success": 0, "dry_run": 0, "failed": 0, "skipped": 0}

    for i, job in enumerate(jobs, 1):
        title   = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        score   = job.get("match_score", 0)
        url     = job.get("url", "")
        portal  = job.get("portal", "?")

        print(f"\n{'-'*65}")
        print(f"  [{i}/{len(jobs)}] {title} @ {company}")
        print(f"  Portal : {portal}")
        print(f"  Score  : {score:.1f}%")
        print(f"  URL    : {url}")

        # Ask for per-job confirmation
        action = "fill (dry run)" if dry_run else "APPLY to"
        if not _confirm(f"Do you want to {action} this job?", auto_confirm=args.auto_confirm):
            print("  Skipped.")
            results["skipped"] += 1
            continue

        # ── Run the agent
        outcome = await apply_to_job(
            job,
            dry_run=dry_run,
            provider=args.provider,
            model=getattr(args, 'model', None),
            headless=args.headless
        )
        status = outcome["status"]
        notes  = outcome["notes"]

        print(f"\n  Result: [{status.upper()}]")
        if notes:
            print(f"  Notes : {notes[:200]}")

        # ── Record result in DB
        mark_applied(conn, job["job_id"], status, notes)
        results[status] = results.get(status, 0) + 1

        # ── Pause between applications (rate limiting)
        if i < len(jobs):
            print("\n  Waiting 5 seconds before next job...")
            await asyncio.sleep(5)

    conn.close()

    # ── Final summary
    print("\n" + "=" * 65)
    print("  [SUCCESS] Auto-Apply Run Complete")
    print(f"  Successfully applied : {results.get('success', 0)}")
    print(f"  Dry runs completed   : {results.get('dry_run', 0)}")
    print(f"  Failed               : {results.get('failed', 0)}")
    print(f"  Skipped              : {results.get('skipped', 0)}")
    print("=" * 65 + "\n")


def main():
    args = _parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n\n  Interrupted by user. Exiting.")


if __name__ == "__main__":
    main()
