import sqlite3

from storage.db import canonical_job_id, ensure_core_schema


def test_canonical_job_id_is_stable():
    a = canonical_job_id("greenhouse", "123", "https://example.com/a")
    b = canonical_job_id("greenhouse", "123", "https://example.com/changed")
    assert a == b
    assert a != canonical_job_id("lever", "123", "https://example.com/a")


def test_core_schema_creates_normalized_tables():
    conn = sqlite3.connect(":memory:")
    ensure_core_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "evaluations"}.issubset(tables)
    conn.close()
