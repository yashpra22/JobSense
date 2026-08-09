"""Canonical SQLite access helpers."""
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def canonical_job_id(source: str, external_id: str = "", url: str = "") -> str:
    """Return one stable ID for a discovered job.

    Prefer the ATS/source ID; URL is only the fallback. Hashing keeps the DB key
    bounded and avoids joining tables on mutable URLs.
    """
    identity = f"{source.strip().lower()}|{external_id.strip()}" if external_id.strip() else f"url|{url.strip()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@contextmanager
def connect(db_path: str | Path):
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_core_schema(conn: sqlite3.Connection) -> None:
    """Create normalized core tables without deleting the legacy schema."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            external_id TEXT,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            first_seen REAL NOT NULL,
            UNIQUE(source, external_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(job_id),
            evaluator_version TEXT NOT NULL,
            model TEXT,
            eligible INTEGER NOT NULL,
            score REAL NOT NULL,
            analysis TEXT DEFAULT '',
            evaluated_at REAL NOT NULL,
            UNIQUE(job_id, evaluator_version)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evaluations_job ON evaluations(job_id)")
