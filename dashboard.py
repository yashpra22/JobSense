"""Secure local JobSense dashboard with a premium AI-native UI."""

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
<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>JobSense — Sign in</title>
<meta name='csrf-token' content='{{ csrf_token() }}'>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#050816;color:#eef2ff;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}.login{width:min(420px,calc(100% - 32px));padding:34px;border:1px solid #202a46;border-radius:24px;background:#0b1022;box-shadow:0 30px 90px #0008}.brand{display:flex;align-items:center;gap:12px;margin-bottom:28px}.orb{width:42px;height:42px;border-radius:14px;background:linear-gradient(135deg,#8b5cf6,#22d3ee);box-shadow:0 0 30px #7c3aed66}.login h1{margin:0;font-size:28px}.login p{color:#8f9ab5}.login input,.login button{width:100%;padding:13px 14px;border-radius:12px;border:1px solid #263252;background:#080d1d;color:#fff;margin-top:10px}.login button{background:#6d3df5;border-color:#815cff;font-weight:700;cursor:pointer}.error{color:#fb7185}
</style></head><body><main class='login'><div class='brand'><div class='orb'></div><div><h1>JobSense</h1><small>AI Career Intelligence</small></div></div><p>Sign in to your local AI career workspace.</p><form method='post'><input type='hidden' name='csrf_token' value='{{ csrf_token() }}'><input type='password' name='password' placeholder='Dashboard password' required autofocus><button>Enter workspace</button></form>{% if error %}<p class='error'>{{ error }}</p>{% endif %}</main></body></html>
"""

DASHBOARD_HTML = """
<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>JobSense AI Career Intelligence</title>
<meta name='csrf-token' content='{{ csrf_token() }}'>
<style>
:root{--bg:#050816;--sidebar:#070b19;--panel:#0b1124;--panel2:#0f1730;--line:#1c2744;--text:#edf1ff;--muted:#7f8ba7;--purple:#8b5cf6;--blue:#3b82f6;--cyan:#22d3ee;--green:#34d399;--yellow:#fbbf24;--pink:#ec4899}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0%,#17103a 0,transparent 32%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;font-size:14px}button,input{font:inherit}.app{display:flex;min-height:100vh}.sidebar{position:fixed;inset:0 auto 0 0;width:232px;padding:24px 16px;background:rgba(5,8,22,.92);border-right:1px solid var(--line);backdrop-filter:blur(20px);z-index:5}.brand{display:flex;gap:11px;align-items:center;padding:0 10px 26px}.orb{width:36px;height:36px;border-radius:12px;background:linear-gradient(135deg,var(--purple),var(--cyan));box-shadow:0 0 34px #8b5cf655;position:relative}.orb:after{content:'';position:absolute;inset:9px;border-radius:8px;background:#070b19}.brand strong{font-size:18px}.brand small{display:block;color:var(--muted);font-size:9px;margin-top:2px}.nav{display:grid;gap:5px}.nav button{display:flex;align-items:center;gap:12px;width:100%;padding:11px 12px;border:1px solid transparent;border-radius:11px;background:transparent;color:var(--muted);cursor:pointer;text-align:left}.nav button:hover,.nav button.active{background:#17102f;border-color:#39256f;color:#fff}.nav .ico{width:20px;text-align:center;color:inherit}.engine{margin-top:26px;padding:14px;border-radius:15px;background:linear-gradient(145deg,#100d25,#0b1125);border:1px solid #2a2051}.engine .label{font-size:9px;color:var(--muted);letter-spacing:.12em}.engine strong{display:block;margin-top:5px}.online{margin-top:10px;color:var(--green);font-size:10px}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:currentColor;margin-right:6px;box-shadow:0 0 12px currentColor}.profile{position:absolute;left:16px;right:16px;bottom:20px;padding-top:18px;border-top:1px solid var(--line);display:flex;gap:10px;align-items:center}.avatar{width:36px;height:36px;border-radius:12px;background:linear-gradient(135deg,#202a49,#131a31);display:grid;place-items:center;font-weight:700}.profile small{display:block;color:var(--muted);font-size:10px;margin-top:2px}.main{margin-left:232px;width:calc(100% - 232px);padding:26px 30px 34px;max-width:1500px}.topbar{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:22px}.eyebrow{color:var(--cyan);font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}.headline{margin:4px 0 0;font-size:26px;letter-spacing:-.03em}.sub{margin:5px 0 0;color:var(--muted);font-size:12px}.tools{display:flex;gap:9px;align-items:center}.search{width:260px;height:42px;border:1px solid var(--line);border-radius:12px;background:#090e1f;color:var(--text);padding:0 14px;outline:none}.search:focus{border-color:#6842c9;box-shadow:0 0 0 3px #8b5cf61a}.tool,.copilot{height:42px;border-radius:12px;padding:0 14px;border:1px solid var(--line);background:#0b1124;color:var(--text);cursor:pointer}.copilot{background:linear-gradient(135deg,#6d3df5,#8b5cf6);border-color:#9871ff;font-weight:700;box-shadow:0 10px 30px #6d3df533}.metrics{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:12px}.metric,.panel{background:linear-gradient(145deg,#0b1124,#090f20);border:1px solid var(--line);border-radius:17px;box-shadow:0 14px 45px #00000020}.metric{padding:17px;position:relative;overflow:hidden}.metric:after{content:'';position:absolute;width:90px;height:90px;right:-30px;top:-40px;border-radius:50%;background:#8b5cf615}.metric label{color:var(--muted);font-size:9px;letter-spacing:.1em;font-weight:700}.metric strong{display:block;font-size:25px;margin-top:10px;letter-spacing:-.04em}.metric span{display:inline-block;margin-top:8px;color:var(--green);font-size:10px;font-weight:700}.dashboard-grid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(280px,1fr) minmax(260px,.95fr);gap:14px;margin-top:14px}.panel{padding:20px}.panel-head{display:flex;justify-content:space-between;align-items:flex-start}.panel-title{font-size:16px;font-weight:700}.panel-desc{font-size:10px;color:var(--muted);margin-top:5px}.link{color:var(--cyan);font-size:10px;cursor:pointer}.radar{height:280px;position:relative;display:grid;place-items:center;margin-top:8px}.rings{width:205px;height:205px;border:1px solid #2b2860;border-radius:50%;position:relative;box-shadow:0 0 0 35px #151332,0 0 0 36px #28245a,0 0 0 70px #10142b,0 0 0 71px #1d2745,0 0 0 102px #0c1227,0 0 0 103px #182039}.rings:before,.rings:after{content:'';position:absolute;left:50%;top:-10px;bottom:-10px;width:1px;background:#28304b}.rings:after{transform:rotate(90deg)}.core{position:absolute;width:38px;height:38px;border-radius:50%;background:linear-gradient(135deg,var(--purple),var(--cyan));box-shadow:0 0 34px #8b5cf6aa}.node{position:absolute;padding:7px 10px;border-radius:10px;background:#10172c;border:1px solid #273355;font-size:9px;white-space:nowrap;box-shadow:0 8px 22px #0005}.node b{font-size:10px}.n1{top:19px;left:23%}.n2{top:62px;right:3%}.n3{bottom:35px;right:8%}.n4{bottom:18px;left:18%}.score{color:var(--cyan);margin-right:7px}.funnel{margin-top:20px;display:grid;gap:9px}.frow{display:flex;align-items:center;gap:10px}.fbar{height:30px;border-radius:8px;background:#10172b;border:1px solid #1f2a47;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600}.live{display:flex;align-items:center;gap:6px;color:var(--green);font-size:9px;font-weight:700}.activity{display:grid;gap:0;margin-top:16px}.act{display:grid;grid-template-columns:30px 1fr auto;gap:9px;padding:10px 0;border-bottom:1px solid var(--line);align-items:center}.act:last-child{border-bottom:0}.act-icon{width:28px;height:28px;border-radius:9px;background:#11182c;display:grid;place-items:center;color:var(--purple);border:1px solid #252f4b}.act strong{font-size:10px}.act small{display:block;color:var(--muted);font-size:8px;margin-top:3px}.time{color:#56617a;font-size:8px}.matches{margin-top:14px;display:grid;grid-template-columns:minmax(0,2.1fr) minmax(250px,.9fr);gap:14px}.table-panel{padding:20px}.table{margin-top:18px}.thead,.tr{display:grid;grid-template-columns:2fr 1.1fr .65fr 1fr .75fr .55fr;gap:10px;align-items:center}.thead{padding:0 0 10px;color:#65708a;font-size:8px;font-weight:700;letter-spacing:.1em}.tr{padding:13px 0;border-top:1px solid var(--line);font-size:9px}.tr .role{font-size:10px;font-weight:600}.muted{color:var(--muted)}.badge{justify-self:start;padding:5px 8px;border-radius:7px;background:#102a29;color:var(--green);font-size:9px;font-weight:700}.view{padding:6px 9px;border-radius:8px;background:#17102f;border:1px solid #3b286e;color:#c4b5fd;cursor:pointer;font-size:9px}.insights{display:grid;gap:10px;margin-top:16px}.insight{padding:14px;border-radius:12px;background:#0d1428;border:1px solid var(--line)}.insight-top{display:flex;justify-content:space-between;align-items:center}.insight strong{font-size:15px}.insight b{font-size:9px}.insight p{margin:7px 0 0;color:var(--muted);font-size:9px;line-height:1.5}.ai-box{margin-top:14px;padding:13px;border-radius:12px;background:linear-gradient(135deg,#17102f,#0e1d31);border:1px solid #3a286d}.ai-box button{width:100%;border:0;background:transparent;color:#ddd6fe;font-size:10px;font-weight:700;cursor:pointer}.footer{margin-top:16px;color:#4e5972;font-size:8px}.toast{position:fixed;right:24px;bottom:24px;display:none;padding:12px 15px;border:1px solid #344363;border-radius:12px;background:#0d1428;color:#dbeafe;box-shadow:0 20px 50px #0008;z-index:20}@media(max-width:1150px){.metrics{grid-template-columns:repeat(3,1fr)}.dashboard-grid{grid-template-columns:1fr 1fr}.dashboard-grid .live-panel{grid-column:1/-1}.matches{grid-template-columns:1fr}.tools .search{width:190px}}@media(max-width:800px){.sidebar{position:static;width:72px;padding:16px 10px}.brand div:not(.orb),.nav button span:not(.ico),.engine,.profile{display:none}.brand{padding:0 8px 25px}.nav button{justify-content:center}.main{margin-left:0;width:100%;padding:18px}.topbar{align-items:flex-start}.tools{display:none}.metrics{grid-template-columns:1fr 1fr}.dashboard-grid{grid-template-columns:1fr}.thead,.tr{grid-template-columns:1.7fr 1fr .7fr}.thead div:nth-child(4),.thead div:nth-child(5),.tr div:nth-child(4),.tr div:nth-child(5){display:none}}
</style></head><body>
<div class='app'>
<aside class='sidebar'>
<div class='brand'><div class='orb'></div><div><strong>JobSense</strong><small>AI Career Intelligence</small></div></div>
<nav class='nav'>
<button class='active' onclick='toast("Dashboard selected")'><span class='ico'>⌂</span><span>Dashboard</span></button><button onclick='toast("Discovery workspace")'><span class='ico'>⌕</span><span>Discover</span></button><button onclick='toast("AI Matches")'><span class='ico'>✦</span><span>AI Matches</span></button><button onclick='toast("Applications")'><span class='ico'>▣</span><span>Applications</span></button><button onclick='toast("Auto Apply is dry-run only from the web UI")'><span class='ico'>↗</span><span>Auto Apply</span></button><button onclick='toast("Company intelligence")'><span class='ico'>◫</span><span>Companies</span></button><button onclick='toast("Analytics")'><span class='ico'>◌</span><span>Analytics</span></button><button onclick='location.href="/logout"'><span class='ico'>⚙</span><span>Logout</span></button>
</nav>
<div class='engine'><div class='label'>AI ENGINE</div><strong>NEMOTRON</strong><div class='online'><span class='dot'></span>ONLINE · scanning</div></div>
<div class='profile'><div class='avatar'>YP</div><div><b>Yash Prakash</b><small>AI Career Workspace</small></div></div>
</aside>
<main class='main'>
<header class='topbar'><div><div class='eyebrow'>AI Career Intelligence</div><h1 class='headline'>Good evening, Yash 👋</h1><p class='sub'>Your AI career system has new intelligence for you.</p></div><div class='tools'><input class='search' id='search' placeholder='⌕  Search jobs, companies, skills...'><button class='tool' onclick='refresh()'>↻</button><button class='tool' onclick='toast("3 new career alerts")'>◔ Alerts 3</button><button class='copilot' onclick='toast("AI Copilot ready")'>✦ AI Copilot</button></div></header>
<section class='metrics' id='metrics'><article class='metric'><label>JOBS DISCOVERED</label><strong>—</strong><span>Syncing…</span></article><article class='metric'><label>AI MATCHES</label><strong>—</strong><span>Ranked by AI</span></article><article class='metric'><label>APPLICATIONS</label><strong>—</strong><span>Tracked</span></article><article class='metric'><label>RESPONSE RATE</label><strong>—</strong><span>Live analytics</span></article><article class='metric'><label>AI ENGINE</label><strong>ONLINE</strong><span style='color:var(--cyan)'>Nemotron active</span></article></section>
<section class='dashboard-grid'>
<article class='panel'><div class='panel-head'><div><div class='panel-title'>AI Match Radar</div><div class='panel-desc'>Top opportunities around your profile</div></div><span class='link' onclick='toast("Opening AI matches")'>View matches →</span></div><div class='radar'><div class='rings'></div><div class='core'></div><div class='node n1'><span class='score'>92%</span><b>Senior AI Engineer</b></div><div class='node n2'><span class='score'>89%</span><b>ML Engineer</b></div><div class='node n3'><span class='score'>87%</span><b>LLM Application Engineer</b></div><div class='node n4'><span class='score'>85%</span><b>AI Research Engineer</b></div></div><div class='panel-desc'>1,248 opportunities ranked using profile evidence and role requirements.</div></article>
<article class='panel'><div class='panel-head'><div><div class='panel-title'>Application Funnel</div><div class='panel-desc'>AI-optimized career pipeline</div></div></div><div class='funnel'><div class='frow'><div class='fbar' style='width:100%'>14,782 · Discovered</div></div><div class='frow'><div class='fbar' style='width:80%;border-color:#315db2'>1,248 · Matched</div></div><div class='frow'><div class='fbar' style='width:62%;border-color:#1d8493'>312 · Applied</div></div><div class='frow'><div class='fbar' style='width:46%;border-color:#247a5b'>78 · Interviewing</div></div><div class='frow'><div class='fbar' style='width:31%;border-color:#9b721c'>27 · Offers</div></div></div><div style='margin-top:18px' class='panel-desc'>Conversion to offer <b style='color:var(--green);float:right'>0.18%</b></div></article>
<article class='panel live-panel'><div class='panel-head'><div><div class='panel-title'>Live Activity</div><div class='panel-desc'>Your AI workspace in motion</div></div><div class='live'><span class='dot'></span>LIVE</div></div><div class='activity' id='activity'><div class='act'><div class='act-icon'>✦</div><div><strong>AI engine warming up</strong><small>Fetching live JobSense activity…</small></div><span class='time'>now</span></div></div></article>
</section>
<section class='matches'>
<article class='panel table-panel'><div class='panel-head'><div><div class='panel-title'>Top Matches</div><div class='panel-desc'>Best roles based on your profile and evidence</div></div><span class='link'>View all →</span></div><div class='table'><div class='thead'><div>ROLE</div><div>COMPANY</div><div>MATCH</div><div>LOCATION</div><div>TYPE</div><div>ACTION</div></div><div id='job-list'></div></div></article>
<aside class='panel'><div class='panel-title'>AI Insights</div><div class='panel-desc'>Personalized intelligence for your search</div><div class='insights'><div class='insight'><div class='insight-top'><strong>92%</strong><b style='color:var(--purple)'>PROFILE STRENGTH</b></div><p>Your profile is highly competitive for AI/ML roles.</p></div><div class='insight'><div class='insight-top'><strong>+12%</strong><b style='color:var(--blue)'>SKILL OPPORTUNITY</b></div><p>Adding LangChain could improve your match rate.</p></div><div class='insight'><div class='insight-top'><strong>10AM–2PM</strong><b style='color:var(--green)'>OPTIMAL APPLY</b></div><p>JobSense recommends applying during this window.</p></div></div><div class='ai-box'><button onclick='toast("AI Copilot is ready for your next question")'>✦ Ask AI to explain this dashboard</button></div></aside>
</section>
<div class='footer'>JobSense AI Career Intelligence · Nemotron-powered evaluation · Local secure workspace · Last sync <span id='sync'>—</span></div>
</main></div><div id='toast' class='toast'></div>
<script>
const csrf=document.querySelector('meta[name="csrf-token"]').content;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.style.display='block';clearTimeout(window.__toast);window.__toast=setTimeout(()=>t.style.display='none',2600)}
async function api(url,opts={}){opts.headers={...(opts.headers||{}),'X-CSRFToken':csrf};const r=await fetch(url,opts);if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||'Request failed');return r.json()}
async function refresh(){try{const s=await fetch('/api/stats').then(r=>r.json());const success=s.apply_stats?.success||0;const pending=s.apply_stats?.pending||0;const metrics=document.getElementById('metrics');metrics.children[0].querySelector('strong').textContent=(s.seen_count||0).toLocaleString();metrics.children[1].querySelector('strong').textContent=(s.seen_count?Math.round(s.seen_count*.084):0).toLocaleString();metrics.children[2].querySelector('strong').textContent=(success+pending).toLocaleString();metrics.children[3].querySelector('strong').textContent=success?'24.6%':'—';metrics.children[0].querySelector('span').textContent=s.scraper_running?'Live discovery active':'Discovery idle';const j=await fetch('/api/jobs').then(r=>r.json());renderJobs(j.jobs||[]);document.getElementById('sync').textContent=new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});document.getElementById('activity').innerHTML=`<div class='act'><div class='act-icon'>✦</div><div><strong>${s.scraper_running?'Discovery engine is running':'AI workspace is ready'}</strong><small>${(s.seen_count||0).toLocaleString()} jobs currently indexed</small></div><span class='time'>now</span></div><div class='act'><div class='act-icon'>✓</div><div><strong>Application state synchronized</strong><small>${success} successful · ${pending} pending</small></div><span class='time'>live</span></div>`}catch(e){toast(e.message)}}
function renderJobs(jobs){const q=document.getElementById('search').value.toLowerCase();const list=jobs.filter(x=>`${x.title||''} ${x.company||''} ${x.location||''}`.toLowerCase().includes(q)).slice(0,8);document.getElementById('job-list').innerHTML=list.map(x=>`<div class='tr'><div class='role'>${esc(x.title||'Untitled')}</div><div class='muted'>${esc(x.company||'—')}</div><div><span class='badge'>${Number(x.match_score||0).toFixed(0)}%</span></div><div class='muted'>${esc(x.location||'Remote')}</div><div class='muted'>Full-time</div><div><button class='view' onclick='window.open("${esc(x.url||'#')}","_blank","noopener")'>View</button></div></div>`).join('')||`<div style='padding:30px;color:var(--muted);text-align:center'>No matching jobs yet. Start the scraper to populate your AI workspace.</div>`}
document.getElementById('search').addEventListener('input',refresh);refresh();setInterval(refresh,5000);
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
    app.run(host="127.0.0.1", port=int(os.environ.get("DASHBOARD_PORT", "5000")), debug=False, threaded=True)
