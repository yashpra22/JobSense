"""Secure local JobSense dashboard with a premium animated AI-native UI."""

import os
import secrets
import sqlite3
import subprocess
import sys
from functools import wraps

from flask import Flask, jsonify, redirect, render_template, render_template_string, request, session, url_for
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.dirname(__file__)
DB = os.path.join(BASE_DIR, "seen_jobs.db")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY", "")
if not PASSWORD:
    raise RuntimeError("DASHBOARD_PASSWORD must be set in .env before starting the dashboard")
if not SECRET_KEY:
    raise RuntimeError("DASHBOARD_SECRET_KEY must be set in .env before starting the dashboard")

PASSWORD_HASH = generate_password_hash(PASSWORD)
app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    WTF_CSRF_ENABLED=True,
    WTF_CSRF_TIME_LIMIT=3600,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
)
csrf = CSRFProtect(app)
SCRAPER_PROCESS = None
APPLIER_PROCESS = None

LOGIN_HTML = """
<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>JobSense — Sign in</title>
<meta name='csrf-token' content='{{ csrf_token() }}'><style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#050816;color:#eef2ff;font:14px system-ui,sans-serif}.login{width:min(420px,calc(100% - 32px));padding:34px;border:1px solid #202a46;border-radius:24px;background:#0b1022;box-shadow:0 30px 90px #0008}.orb{width:42px;height:42px;border-radius:14px;background:linear-gradient(135deg,#8b5cf6,#22d3ee);box-shadow:0 0 30px #7c3aed66}.brand{display:flex;align-items:center;gap:12px}.login input,.login button{width:100%;padding:13px 14px;border-radius:12px;border:1px solid #263252;background:#080d1d;color:#fff;margin-top:10px}.login button{background:#6d3df5;border-color:#815cff;font-weight:700;cursor:pointer}.muted{color:#8f9ab5}.error{color:#fb7185}</style></head><body><main class='login'><div class='brand'><div class='orb'></div><div><h1>JobSense</h1><small>AI Career Intelligence</small></div></div><p class='muted'>Sign in to your local AI career workspace.</p><form method='post'><input type='hidden' name='csrf_token' value='{{ csrf_token() }}'><input type='password' name='password' placeholder='Dashboard password' required autofocus><button>Enter workspace</button></form>{% if error %}<p class='error'>{{ error }}</p>{% endif %}</main></body></html>
"""


def get_db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication_required"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def find_running_processes():
    pids = []
    try:
        import psutil
        current = os.getpid()
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if proc.info["pid"] == current:
                    continue
                cmd = " ".join(proc.info.get("cmdline") or [])
                if "career_watcher.py" in cmd or "run_auto_apply.py" in cmd:
                    pids.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except ImportError:
        pass
    return pids


@app.errorhandler(CSRFError)
def csrf_error(error):
    return jsonify({"error": "csrf_validation_failed", "message": error.description}), 400


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if check_password_hash(PASSWORD_HASH, request.form.get("password", "")):
            session.clear()
            session["authenticated"] = True
            session["login_nonce"] = secrets.token_hex(16)
            return redirect(url_for("index"))
        return render_template_string(LOGIN_HTML, error="Invalid password")
    return render_template_string(LOGIN_HTML, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("dashboard.html")


def _company_names(conn):
    try:
        rows = conn.execute("SELECT DISTINCT company FROM events WHERE company IS NOT NULL AND company != ''").fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


@app.route("/api/stats")
@login_required
def api_stats():
    conn = get_db()
    try:
        seen = conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        try:
            from auto_apply.db_updater import get_apply_stats, migrate_db
            migrate_db(conn)
            apply_stats = get_apply_stats(conn)
        except Exception:
            apply_stats = {"success": 0, "failed": 0, "dry_run": 0, "pending": 0, "uncertain": 0}
        return jsonify({
            "company_count": len(_company_names(conn)),
            "seen_count": seen,
            "scraper_running": bool(SCRAPER_PROCESS and SCRAPER_PROCESS.poll() is None) or bool(find_running_processes()),
            "apply_stats": apply_stats,
        })
    finally:
        conn.close()


@app.route("/api/jobs")
@login_required
def api_jobs():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT jd.job_id, COALESCE(NULLIF(jd.url,''), jd.job_id) AS url,
                   jd.title, jd.company, jd.location, jd.seniority, jd.first_seen,
                   COALESCE(desc.match_score,0) AS match_score,
                   COALESCE(desc.match_analysis,'') AS match_analysis
            FROM jobs_detail jd
            JOIN job_descriptions desc ON desc.job_id = jd.job_id
            WHERE desc.passes_filter = 1
            ORDER BY desc.match_score DESC, jd.first_seen DESC
            LIMIT 500
        """).fetchall()
        return jsonify({"jobs": [dict(r) for r in rows]})
    except sqlite3.Error as exc:
        return jsonify({"jobs": [], "error": str(exc)})
    finally:
        conn.close()


@app.route("/api/control/start", methods=["POST"])
@login_required
def api_control_start():
    global SCRAPER_PROCESS
    if find_running_processes():
        return jsonify({"status": "error", "message": "A JobSense process is already running"}), 409
    try:
        SCRAPER_PROCESS = subprocess.Popen([sys.executable, os.path.join(BASE_DIR, "career_watcher.py")], cwd=BASE_DIR)
        return jsonify({"status": "ok", "running": True})
    except OSError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/control/stop", methods=["POST"])
@login_required
def api_control_stop():
    global SCRAPER_PROCESS, APPLIER_PROCESS
    pids = find_running_processes()
    for proc in (SCRAPER_PROCESS, APPLIER_PROCESS):
        if proc is not None:
            pids.append(proc.pid)
    for pid in set(pids):
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    SCRAPER_PROCESS = None
    APPLIER_PROCESS = None
    return jsonify({"status": "ok", "running": False})


@app.route("/api/control/auto_apply", methods=["POST"])
@login_required
def api_control_auto_apply():
    """The web UI can launch only a dry-run; live submission stays CLI-only."""
    global APPLIER_PROCESS
    if APPLIER_PROCESS is not None and APPLIER_PROCESS.poll() is None:
        return jsonify({"status": "error", "message": "Auto-apply process is already running"}), 409
    script = os.path.join(BASE_DIR, "auto_apply", "run_auto_apply.py")
    try:
        APPLIER_PROCESS = subprocess.Popen(
            [sys.executable, script, "--dry-run", "--max-jobs", "1", "--min-score", "80", "--yes", "--no-headless"],
            cwd=BASE_DIR,
        )
        return jsonify({"status": "ok", "message": "Dry-run auto-apply launched; live submit is CLI-only"})
    except OSError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/control/clear_db", methods=["POST"])
@login_required
def api_control_clear_db():
    conn = get_db()
    try:
        for table in ("applications", "seen", "events", "jobs_detail", "job_descriptions"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
        return jsonify({"status": "ok"})
    except sqlite3.Error as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": str(exc)}), 500
    finally:
        conn.close()


if __name__ == "__main__":
    # Localhost-only binding prevents accidental LAN exposure.
    app.run(host="127.0.0.1", port=int(os.environ.get("DASHBOARD_PORT", "5000")), debug=False, threaded=True)
