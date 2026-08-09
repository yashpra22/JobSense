"""
send_db_email.py — Standalone script to send an email with all jobs currently
in seen_jobs.db without re-running the full scraping cycle.
"""

import sys
import sqlite3
from career_watcher import send_email, EMAIL_FROM, EMAIL_TO, SMTP_USER, SMTP_PASS, DB_PATH

def send_full_db_email():
    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        rows = cur.execute("""
            SELECT jd.company, jd.title, jd.seniority, jd.location, 
                   COALESCE(NULLIF(jd.url, ''), jd.job_id) as url,
                   MAX(COALESCE(desc.match_score, 0.0)) as match_score,
                   MAX(COALESCE(desc.passes_filter, 0)) as passes_filter
            FROM jobs_detail jd
            JOIN job_descriptions desc ON (jd.job_id = desc.job_id OR jd.url = desc.job_id)
            WHERE desc.passes_filter = 1
            GROUP BY jd.job_id
            ORDER BY match_score DESC, jd.company, jd.title
        """).fetchall()
    except Exception as e:
        print(f"Error querying jobs_detail: {e}")
        return False
    finally:
        conn.close()

    if not rows:
        print("No jobs found in jobs_detail table.")
        return False

    jobs = [dict(r) for r in rows]
    print(f"Found {len(jobs)} jobs in database.")
    print(f"Sending email from {EMAIL_FROM} to {EMAIL_TO}...")

    if not SMTP_PASS:
        print("\n[WARNING] SMTP_PASS is not set in environment or .env file!")
        print("Please set SMTP_PASS (Gmail App Password) in your .env file or environment.")
        print("Example .env file:")
        print("    SMTP_PASS=xxxxxxxxxxxxxxxx\n")

    try:
        send_email(jobs)
        print(f"[SUCCESS] Successfully sent email containing {len(jobs)} jobs!")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")
        return False

if __name__ == "__main__":
    send_full_db_email()
