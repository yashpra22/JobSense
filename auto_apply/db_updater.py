"""Database migrations and application state management."""

import sqlite3
import time
import uuid

APPLICATION_STATES = {
    "pending",
    "approved",
    "filling",
    "submitted",
    "success",
    "failed",
    "uncertain",
    "dry_run",
    "skipped",
}


def migrate_db(conn: sqlite3.Connection) -> None:
    """Create/upgrade the auto-apply schema without destroying legacy data."""
    columns = [
        ("applied", "INTEGER DEFAULT 0"),
        ("applied_at", "REAL DEFAULT 0.0"),
        ("apply_result", "TEXT DEFAULT ''"),
    ]
    for name, definition in columns:
        try:
            conn.execute(f"ALTER TABLE job_descriptions ADD COLUMN {name} {definition}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS applications (
            job_id TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'pending',
            approval_token TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            submitted_at REAL,
            verification_url TEXT,
            application_id TEXT,
            notes TEXT DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            CHECK (state IN ('pending','approved','filling','submitted','success','failed','uncertain','dry_run','skipped'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_applications_state ON applications(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_applications_updated ON applications(updated_at)")
    conn.commit()


def _ensure_application(conn: sqlite3.Connection, job_id: str) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO applications(job_id, state, created_at, updated_at)
        VALUES(?, 'pending', ?, ?)
        ON CONFLICT(job_id) DO NOTHING
        """,
        (job_id, now, now),
    )


def set_application_state(
    conn: sqlite3.Connection,
    job_id: str,
    state: str,
    notes: str = "",
    verification_url: str = "",
    application_id: str = "",
) -> None:
    """Persist an application state transition with an audit-friendly timestamp."""
    if state not in APPLICATION_STATES:
        raise ValueError(f"Invalid application state: {state}")
    _ensure_application(conn, job_id)
    now = time.time()
    submitted_at = now if state in {"submitted", "success", "uncertain"} else None
    conn.execute(
        """
        UPDATE applications
        SET state=?, updated_at=?, submitted_at=COALESCE(?, submitted_at),
            verification_url=COALESCE(NULLIF(?, ''), verification_url),
            application_id=COALESCE(NULLIF(?, ''), application_id),
            notes=?
        WHERE job_id=?
        """,
        (state, now, submitted_at, verification_url, application_id, notes[:2000], job_id),
    )
    conn.commit()


def create_approval_token(conn: sqlite3.Connection, job_id: str) -> str:
    """Create a one-time approval token for a specific job."""
    _ensure_application(conn, job_id)
    token = uuid.uuid4().hex
    now = time.time()
    conn.execute(
        "UPDATE applications SET state='approved', approval_token=?, updated_at=? WHERE job_id=?",
        (token, now, job_id),
    )
    conn.commit()
    return token


def consume_approval_token(conn: sqlite3.Connection, job_id: str, token: str) -> bool:
    """Consume a job-specific approval token exactly once."""
    row = conn.execute(
        "SELECT approval_token, state FROM applications WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if not row or row[0] != token or row[1] != "approved":
        return False
    conn.execute(
        "UPDATE applications SET approval_token=NULL, state='filling', updated_at=? WHERE job_id=?",
        (time.time(), job_id),
    )
    conn.commit()
    return True


def mark_applied(conn: sqlite3.Connection, job_id: str, result: str, notes: str = "") -> None:
    """Bridge legacy apply results into the explicit application state machine."""
    mapping = {
        "success": "success",
        "failed": "failed",
        "skipped": "skipped",
        "dry_run": "dry_run",
        "uncertain": "uncertain",
    }
    state = mapping.get(result, "failed")
    ts = time.time()
    _ensure_application(conn, job_id)
    conn.execute(
        "UPDATE applications SET state=?, updated_at=?, submitted_at=CASE WHEN ? IN ('success','uncertain') THEN ? ELSE submitted_at END, notes=? WHERE job_id=?",
        (state, ts, state, ts, notes[:2000], job_id),
    )
    conn.execute(
        """
        UPDATE job_descriptions
        SET applied=?, applied_at=?, apply_result=?
        WHERE job_id=?
        """,
        (1 if state in {"success", "dry_run"} else 0, ts, f"[{state.upper()}] {notes[:300]}", job_id),
    )
    conn.commit()


def get_apply_stats(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute(
            "SELECT state, COUNT(*) FROM applications GROUP BY state"
        ).fetchall()
        counts = {row[0]: row[1] for row in rows}
        return {
            "success": counts.get("success", 0),
            "failed": counts.get("failed", 0),
            "dry_run": counts.get("dry_run", 0),
            "pending": counts.get("pending", 0) + counts.get("approved", 0),
            "uncertain": counts.get("uncertain", 0),
        }
    except sqlite3.OperationalError:
        return {"success": 0, "failed": 0, "dry_run": 0, "pending": 0, "uncertain": 0}
