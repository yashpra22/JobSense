"""Secure local JobSense dashboard.

The dashboard is intentionally local-only. State-changing endpoints require an
authenticated session and Flask-WTF CSRF protection. Live application submission
is deliberately not exposed through the web UI; use the CLI after explicit opt-in.
"""

import os
import secrets
import sqlite3
import subprocess
import sys
from functools import wraps

from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for
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

# Store only a password hash in process memory; do not log the password.
PASSWORD_HASH = generate_password_hash(PASSWORD)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    WTF_CSRF_ENABLED=True,
    WTF_CSRF_TIME_LIMIT=3600,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,  # localhost HTTP; use HTTPS if deployed behind TLS
)
csrf = CSRFProtect(app)

SCRAPER_PROCESS = None
APPLIER_PROCESS = None


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
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
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


LOGIN_HTML = """
<!doctype html><html><head><title>JobSense Login</title>
<meta name='csrf-token' content='{{ csrf_token() }}'>
<style>body{font-family:system-ui;max-width:420px;margin:15vh auto;padding:24px;background:#0b1020;color:#eef}input,button{width:100%;padding:12px;margin:8px 0;box-sizing:border-box}button{cursor:pointer}</style>
</head><body><h1>JobSense</h1><p>Local dashboard authentication</p>
<form method='post'><input type='hidden' name='csrf_token' value='{{ csrf_token() }}'><input type='password' name='password' placeholder='Dashboard password' required><button>Sign in</button></form>
{% if error %}<p>{{ error }}</p>{% endif %}</body></html>
"""

DASHBOARD_HTML = """
<!doctype html><html><head><title>JobSense Dashboard</title>
<meta name='csrf-token' content='{{ csrf_token() }}'>
<style>
body{font-family:system-ui;background:#0b1020;color:#eef;margin:0}.wrap{max-width:1100px;margin:auto;padding:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.card{background:#151d33;border:1px solid #2b3858;border-radius:12px;padding:16px}.jobs{margin-top:16px}.job{padding:14px;border-bottom:1px solid #2b3858}.btn{padding:10px 14px;margin:4px;border:0;border-radius:8px;cursor:pointer}.danger{background:#8b1e2d;color:white}.safe{background:#22543d;color:white}a{color:#7dd3fc}
</style></head><body><div class='wrap'>
<h1>JobSense</h1><p>Authenticated local control center</p>
<div class='grid'><div class='card'><b>Companies</b><h2 id='companies'>-</h2></div><div class='card'><b>Jobs</b><h2 id='jobs-count'>-</h2></div><div class='card'><b>Scraper</b><h2 id='scraper'>-</h2></div><div class='card'><b>Applications</b><h2 id='apps'>-</h2></div></div>
<p><button class='btn safe' onclick='post("/api/control/start")'>Start scraper</button><button class='btn danger' onclick='post("/api/control/stop")'>Stop processes</button><button class='btn safe' onclick='post("/api/control/auto_apply")'>Dry-run auto-apply</button><button class='btn danger' onclick='clearDb()'>Clear DB</button><button class='btn' onclick='location.href="/logout"'>Logout</button></p>
<div class='card jobs'><h2>Top matching jobs</h2><div id='job-list'>Loading...</div></div>
</div><script>
const csrf=document.querySelector('meta[name="csrf-token"]').content;
async function post(url){const r=await fetch(url,{method:'POST',headers:{'X-CSRFToken':csrf}});const d=await r.json();if(!r.ok)alert(d.error||d.message||'Request failed');await refresh();}
async function clearDb(){if(confirm('Clear all local JobSense records?'))await post('/api/control/clear_db');}
async function refresh(){
 const s=await fetch('/api/stats').then(r=>r.json());document.getElementById('companies').textContent=s.company_count??'-';document.getElementById('jobs-count').textContent=s.seen_count??'-';document.getElementById('scraper').textContent=s.scraper_running?'RUNNING':'STOPPED';document.getElementById('apps').textContent=(s.apply_stats?.success||0)+' success / '+(s.apply_stats?.pending||0)+' pending';
 const j=await fetch('/api/jobs').then(r=>r.json());document.getElementById('job-list').innerHTML=(j.jobs||[]).slice(0,50).map(x=>`<div class='job'><b>${escapeHtml(x.title||'Untitled')}</b> — ${escapeHtml(x.company||'')} — ${Number(x.match_score||0).toFixed(1)}%<br><small>${escapeHtml(x.location||'')}</small> <a href='${escapeAttr(x.url||'')}' target='_blank' rel='noopener'>open</a></div>`).join('')||'No matching jobs';
}
function escapeHtml(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function escapeAttr(v){return escapeHtml(v);}
refresh();setInterval(refresh,5000);
</script></body></html>
"""


@app.errorhandler(CSRFError)
def csrf_error(error):
    return jsonify({"error": "csrf_validation_failed", "message": error.description}), 400


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if check_password_hash(PASSWORD_HASH, password):
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
    return render_template_string(DASHBOARD_HTML)


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


def _company_names(conn):
    try:
        rows = conn.execute("SELECT DISTINCT company FROM events WHERE company IS NOT NULL AND company != ''").fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


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
    processes = find_running_processes()
    if SCRAPER_PROCESS is not None:
        processes.append(SCRAPER_PROCESS.pid)
    if APPLIER_PROCESS is not None:
        processes.append(APPLIER_PROCESS.pid)
    for pid in set(processes):
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
    """Web UI may launch ONLY a dry-run. Live submission remains CLI-only."""
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
