"""JobSense Auto-Apply CLI with explicit live-submit safety gates."""

import argparse
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_apply.config import (
    APPLY_SCORE_THRESHOLD,
    CANDIDATE,
    DB_PATH,
    DRY_RUN,
    HEADLESS,
    LIVE_APPLY_ENABLED,
    MAX_JOBS_PER_RUN,
    assert_live_apply_enabled,
    validate_candidate_profile,
)
from auto_apply.db_reader import get_top_jobs, print_job_table
from auto_apply.db_updater import (
    create_approval_token,
    consume_approval_token,
    get_apply_stats,
    mark_applied,
    migrate_db,
    set_application_state,
)
from auto_apply.applier import apply_to_job


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JobSense Auto-Apply Engine")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    parser.add_argument("--max-jobs", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--portal", choices=["Greenhouse", "Lever", "Ashby", "Workday", "Oracle HCM", "Other"], default=None)
    parser.add_argument("--provider", choices=["nvidia", "ollama"], default=None)
    parser.add_argument("--headless", dest="headless", action="store_true", default=None)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--yes", "-y", dest="auto_confirm", action="store_true", default=False)
    return parser.parse_args()


def _confirm(prompt: str, auto_confirm: bool = False) -> bool:
    if auto_confirm:
        print(f"  {prompt} [y/n]: Auto-confirmed (--yes)")
        return True
    while True:
        try:
            answer = input(f"\n  {prompt} [y/n]: ").strip().lower()
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
        except (EOFError, KeyboardInterrupt):
            return False
        print("  Please enter y or n.")


async def _run(args: argparse.Namespace) -> None:
    dry_run = args.dry_run if args.dry_run is not None else DRY_RUN
    max_jobs = args.max_jobs if args.max_jobs is not None else MAX_JOBS_PER_RUN
    min_score = args.min_score if args.min_score is not None else APPLY_SCORE_THRESHOLD
    headless = args.headless if args.headless is not None else HEADLESS

    # Live submission is opt-in at two independent layers: CLI/config mode and environment.
    if not dry_run:
        assert_live_apply_enabled()
    validate_candidate_profile(require_resume=True)

    mode_label = "DRY RUN (no submit)" if dry_run else "LIVE SUBMIT"
    print("\n" + "=" * 65)
    print("  [JobSense Auto-Apply Engine]")
    print(f"  Mode      : {mode_label}")
    print(f"  Min Score : {min_score:.0f}%")
    print(f"  Max Jobs  : {max_jobs}")
    print(f"  Portal    : {args.portal or 'All'}")
    print(f"  Candidate : {CANDIDATE['full_name']} <{CANDIDATE['email']}>")
    print("=" * 65)

    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    migrate_db(conn)
    stats = get_apply_stats(conn)
    print(f"\n  Current DB Stats: {stats}")

    jobs = get_top_jobs(
        threshold=min_score,
        max_count=max_jobs,
        db_path=DB_PATH,
        portal_filter=[args.portal] if args.portal else None,
    )
    if not jobs:
        print(f"\n  No unapplied jobs found with score >= {min_score:.0f}%.")
        conn.close()
        return

    print_job_table(jobs)
    results = {"success": 0, "dry_run": 0, "failed": 0, "skipped": 0, "uncertain": 0}

    for index, job in enumerate(jobs, 1):
        job_id = job["job_id"]
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        score = job.get("match_score", 0)

        print(f"\n{'-' * 65}")
        print(f"  [{index}/{len(jobs)}] {title} @ {company} ({score:.1f}%)")

        action = "fill (dry run)" if dry_run else "APPLY to"
        if not _confirm(f"Do you want to {action} this job?", auto_confirm=args.auto_confirm):
            set_application_state(conn, job_id, "skipped", "User did not approve this application")
            results["skipped"] += 1
            continue

        # Every application gets an auditable approval transition. Live mode consumes a one-time token.
        token = create_approval_token(conn, job_id)
        if not consume_approval_token(conn, job_id, token):
            mark_applied(conn, job_id, "failed", "Approval token validation failed")
            results["failed"] += 1
            continue

        try:
            outcome = await apply_to_job(
                job,
                dry_run=dry_run,
                provider=args.provider,
                model=None,
                headless=headless,
            )
            status = outcome.get("status", "failed")
            notes = outcome.get("notes", "")
            mark_applied(conn, job_id, status, notes)
            results[status] = results.get(status, 0) + 1
        except Exception as exc:
            mark_applied(conn, job_id, "failed", f"Unhandled auto-apply exception: {exc}")
            results["failed"] += 1

        if index < len(jobs):
            await asyncio.sleep(5)

    conn.close()
    print("\n" + "=" * 65)
    print("  [COMPLETE] Auto-Apply Run")
    for key, value in results.items():
        print(f"  {key:12}: {value}")
    print("=" * 65)


def main():
    args = _parse_args()
    try:
        asyncio.run(_run(args))
    except RuntimeError as exc:
        print(f"\n[SAFETY BLOCK] {exc}")
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")


if __name__ == "__main__":
    main()
