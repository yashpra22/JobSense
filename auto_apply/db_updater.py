"""
auto_apply/db_updater.py — Database Migration & Application Status Updater

Adds auto-apply tracking columns to seen_jobs.db (non-breaking) and
provides helpers to record the outcome of each application attempt.
"""

import sqlite3
import time
from datetime import datetime


def migrate_db(conn: sqlite3.Connection) -> None:
    """
    Safely add auto-apply tracking columns to job_descriptions table.
    Uses ALTER TABLE wrapped in try/except so it is idempotent — safe to
    call multiple times even if columns already exist.
    """
    new_columns = [
        ("applied",      "INTEGER DEFAULT 0"),
        ("applied_at",   "REAL    DEFAULT 0.0"),
        ("apply_result", "TEXT    DEFAULT ''"),
    ]
    for col_name, col_def in new_columns:
        try:
            conn.execute(
                f"ALTER TABLE job_descriptions ADD COLUMN {col_name} {col_def}"
            )
            print(f"  [migrate_db] Added column `{col_name}` to job_descriptions.")
        except Exception:
            # Column already exists — this is expected on subsequent runs
            pass
    conn.commit()


def mark_applied(
    conn: sqlite3.Connection,
    job_id: str,
    result: str,
    notes: str = "",
) -> None:
    """
    Record the outcome of an auto-apply attempt for a job.

    Args:
        conn:    Active SQLite connection
        job_id:  The job's canonical ID (primary key in job_descriptions)
        result:  One of: 'success', 'failed', 'skipped', 'dry_run'
        notes:   Free-form notes about what happened (e.g. error message)
    """
    ts = datetime.now().timestamp()
    summary = f"[{result.upper()}] {notes[:300]}" if notes else f"[{result.upper()}]"
    try:
        conn.execute(
            """
            UPDATE job_descriptions
               SET applied      = ?,
                   applied_at   = ?,
                   apply_result = ?
             WHERE job_id = ?
            """,
            (1 if result in ("success", "dry_run") else 0, ts, summary, job_id),
        )
        conn.commit()
        print(f"  [db_updater] Marked job {job_id[:40]}… as {result.upper()}")
    except Exception as exc:
        print(f"  [db_updater] WARNING: Could not update job status: {exc}")


def get_apply_stats(conn: sqlite3.Connection) -> dict:
    """Return a summary dict of current auto-apply statistics."""
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE applied = 1 AND apply_result LIKE '[SUCCESS]%') AS success_count,
                COUNT(*) FILTER (WHERE apply_result LIKE '[FAILED]%')                  AS failed_count,
                COUNT(*) FILTER (WHERE apply_result LIKE '[DRY_RUN]%')                 AS dry_run_count,
                COUNT(*) FILTER (WHERE applied = 0 AND passes_filter = 1
                                   AND match_score > 0)                                AS pending_count
            FROM job_descriptions
            """
        ).fetchone()
        return {
            "success":  row[0] or 0,
            "failed":   row[1] or 0,
            "dry_run":  row[2] or 0,
            "pending":  row[3] or 0,
        }
    except Exception:
        return {"success": 0, "failed": 0, "dry_run": 0, "pending": 0}
