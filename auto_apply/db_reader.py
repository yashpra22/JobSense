"""
auto_apply/db_reader.py — Read High-Match Jobs from seen_jobs.db

Queries the SQLite database for jobs that:
  - Passed the pre-filter (passes_filter = 1)
  - Have been LLM-evaluated (match_score > 0)
  - Meet the score threshold (match_score >= threshold)
  - Have not already been applied to (applied = 0 or column not yet present)

Returns a list of job dicts ready for the auto-apply agent.
"""

import sqlite3
from typing import Optional

from auto_apply.config import DB_PATH


def get_top_jobs(
    threshold: float = 80.0,
    max_count: int = 10,
    db_path: str = DB_PATH,
    portal_filter: Optional[list] = None,
) -> list[dict]:
    """
    Fetch top-scoring, unapplied jobs from seen_jobs.db.

    Args:
        threshold:     Minimum match_score (0–100) to include.
        max_count:     Maximum number of jobs to return.
        db_path:       Path to seen_jobs.db.
        portal_filter: Optional list of portal names to filter to
                       e.g. ['Greenhouse', 'Lever'] — None means all.

    Returns:
        List of job dicts sorted by match_score DESC.
        Each dict has: job_id, title, company, url, location, match_score,
                       match_analysis, first_seen, portal.
    """
    if not db_path:
        raise FileNotFoundError("DB_PATH is not configured in auto_apply/config.py")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # access columns by name

    try:
        # Try to include applied filter (column may not exist yet — handle gracefully)
        try:
            rows = conn.execute(
                """
                SELECT
                    jd.job_id,
                    jd.title,
                    jd.company,
                    jd.url,
                    jd.location,
                    jd.first_seen,
                    desc.match_score,
                    desc.match_analysis,
                    COALESCE(desc.applied, 0) AS applied
                FROM jobs_detail     AS jd
                JOIN job_descriptions AS desc USING (job_id)
                WHERE desc.passes_filter  = 1
                  AND desc.match_score   >= ?
                  AND desc.evaluated_at  >  0
                  AND COALESCE(desc.applied, 0) = 0
                ORDER BY desc.match_score DESC
                LIMIT ?
                """,
                (threshold, max_count),
            ).fetchall()
        except sqlite3.OperationalError:
            # applied column not yet migrated — fallback query without it
            rows = conn.execute(
                """
                SELECT
                    jd.job_id,
                    jd.title,
                    jd.company,
                    jd.url,
                    jd.location,
                    jd.first_seen,
                    desc.match_score,
                    desc.match_analysis,
                    0 AS applied
                FROM jobs_detail     AS jd
                JOIN job_descriptions AS desc USING (job_id)
                WHERE desc.passes_filter  = 1
                  AND desc.match_score   >= ?
                  AND desc.evaluated_at  >  0
                ORDER BY desc.match_score DESC
                LIMIT ?
                """,
                (threshold, max_count),
            ).fetchall()

        jobs = []
        for row in rows:
            job = dict(row)
            # Detect portal from URL
            url = job.get("url", "")
            job["portal"] = _detect_portal(url)

            # Apply portal filter if specified
            if portal_filter and job["portal"] not in portal_filter:
                continue

            # Ensure URL is usable (not empty)
            if not url or url.strip() in ("", "N/A"):
                continue

            jobs.append(job)

        return jobs

    finally:
        conn.close()


def _detect_portal(url: str) -> str:
    """Detect job portal from URL string."""
    url_lower = (url or "").lower()
    if "greenhouse.io" in url_lower:
        return "Greenhouse"
    if "lever.co" in url_lower:
        return "Lever"
    if "ashbyhq.com" in url_lower:
        return "Ashby"
    if "myworkdayjobs.com" in url_lower:
        return "Workday"
    if "oraclecloud.com" in url_lower:
        return "Oracle HCM"
    return "Other"


def print_job_table(jobs: list[dict]) -> None:
    """Pretty-print a table of jobs to the terminal."""
    if not jobs:
        print("  No jobs found matching the criteria.")
        return

    print(f"\n{'#':<4} {'Score':>6}  {'Company':<22} {'Title':<40} {'Portal':<12} {'URL'}")
    print("-" * 110)
    for i, job in enumerate(jobs, 1):
        title   = (job.get("title",   "")   or "")[:38]
        company = (job.get("company", "")   or "")[:20]
        score   = job.get("match_score", 0) or 0
        portal  = job.get("portal", "?")    or "?"
        url     = (job.get("url", "")       or "")[:50]
        print(f"{i:<4} {score:>5.1f}%  {company:<22} {title:<40} {portal:<12} {url}")
    print()
