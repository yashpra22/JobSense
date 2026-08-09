import sqlite3

from auto_apply.db_updater import consume_approval_token, create_approval_token, migrate_db, set_application_state


def test_application_state_machine_and_one_time_approval_token():
    conn = sqlite3.connect(":memory:")
    migrate_db(conn)
    set_application_state(conn, "job-1", "pending", "queued")
    token = create_approval_token(conn, "job-1")
    assert consume_approval_token(conn, "job-1", token) is True
    assert consume_approval_token(conn, "job-1", token) is False
    set_application_state(conn, "job-1", "submitted", "submitted")
    state = conn.execute("SELECT state FROM applications WHERE job_id='job-1'").fetchone()[0]
    assert state == "submitted"
    conn.close()
