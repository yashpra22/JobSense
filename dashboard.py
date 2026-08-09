"""
Job Watcher — Live Dashboard
Run this alongside career_watcher.py:

    pip install flask
    python dashboard.py

Then open: http://localhost:5000
"""

import sys
import os
from unittest.mock import MagicMock

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Mock heavy runtime imports so we can import just the config constants
for _mod in ("playwright", "playwright.async_api", "playwright_stealth",
             "requests", "bs4", "BeautifulSoup"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Pull config constants from career_watcher without executing main()
_cw_path = os.path.join(os.path.dirname(__file__), "career_watcher.py")
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("career_watcher", _cw_path)
_cw   = _ilu.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_cw)
    COMPANIES              = _cw.COMPANIES
    CHECK_INTERVAL_MINUTES = _cw.CHECK_INTERVAL_MINUTES
    CONCURRENCY            = _cw.CONCURRENCY
except Exception:
    COMPANIES              = []
    CHECK_INTERVAL_MINUTES = 30
    CONCURRENCY            = 8

from flask import Flask, jsonify, request
import sqlite3
import psutil
import subprocess

app  = Flask(__name__)
DB   = os.path.join(os.path.dirname(__file__), "seen_jobs.db")
COMPANY_NAMES = [c["name"] for c in COMPANIES]

SCRAPER_PROCESS = None



def find_running_scraper_pids():
    pids = []
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] == current_pid:
                continue
            name = (proc.info['name'] or '').lower()
            if 'python' in name:
                cmdline = proc.info['cmdline'] or []
                cmd_str = ' '.join(cmdline).lower()
                if ('career_watcher.py' in cmd_str or 'run_auto_apply.py' in cmd_str) and 'dashboard.py' not in cmd_str:
                    pids.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return pids


def is_scraper_running():
    global SCRAPER_PROCESS, APPLIER_PROCESS
    if SCRAPER_PROCESS is not None and SCRAPER_PROCESS.poll() is None:
        return True
    if APPLIER_PROCESS is not None and APPLIER_PROCESS.poll() is None:
        return True
    pids = find_running_scraper_pids()
    return len(pids) > 0




def get_db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# HTML DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Watcher · Sci-Fi Command Center</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;600;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<style>
/* ── SCI-FI DESIGN TOKENS & HUD SYSTEM ─────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:            #020810;
  --surface:       rgba(6, 12, 26, 0.55);
  --surface-hover: rgba(14, 26, 52, 0.75);
  --border:        rgba(0, 240, 255, 0.25);
  --border-glow:   rgba(0, 240, 255, 0.55);
  --green:         #00ff9d;
  --green-glow:    rgba(0, 255, 157, 0.6);
  --cyan:          #00f0ff;
  --cyan-glow:     rgba(0, 240, 255, 0.6);
  --purple:        #d946ef;
  --purple-glow:   rgba(217, 70, 239, 0.6);
  --amber:         #ffb703;
  --amber-glow:    rgba(255, 183, 3, 0.6);
  --red:           #ff2a6d;
  --red-glow:      rgba(255, 42, 109, 0.6);
  --muted:         #8ea5c8;
  --dim:           #475e7d;
  --text:          #f0f6fc;
  --header-bg:     rgba(2, 6, 18, 0.88);
  --panel-hdr-bg:  rgba(4, 10, 28, 0.90);
  --stats-bg:      rgba(4, 8, 20, 0.70);
  --footer-bg:     rgba(2, 4, 12, 0.95);
  --card-bg:       rgba(6, 14, 30, 0.60);
  --font-scifi:    'Orbitron', sans-serif;
  --font:          'Space Grotesk', sans-serif;
  --mono:          'JetBrains Mono', monospace;
}

[data-theme="light"] {
  --bg:            #e8f0fe;
  --surface:       rgba(255, 255, 255, 0.72);
  --surface-hover: rgba(240, 245, 255, 0.90);
  --border:        rgba(14, 165, 233, 0.40);
  --border-glow:   rgba(14, 165, 233, 0.65);
  --green:         #059669;
  --green-glow:    rgba(5, 150, 105, 0.45);
  --cyan:          #0284c7;
  --cyan-glow:     rgba(2, 132, 199, 0.45);
  --purple:        #9333ea;
  --purple-glow:   rgba(147, 51, 234, 0.45);
  --amber:         #d97706;
  --amber-glow:    rgba(217, 119, 6, 0.45);
  --red:           #e11d48;
  --red-glow:      rgba(225, 29, 72, 0.45);
  --muted:         #334155;
  --dim:           #475569;
  --text:          #0f172a;
  --header-bg:     rgba(255, 255, 255, 0.90);
  --panel-hdr-bg:  rgba(248, 250, 255, 0.95);
  --stats-bg:      rgba(241, 245, 249, 0.85);
  --footer-bg:     rgba(248, 250, 255, 0.98);
  --card-bg:       rgba(255, 255, 255, 0.88);
}

html, body {
  height: 100%; font-family: var(--font);
  background: var(--bg); color: var(--text);
  overflow: hidden; -webkit-font-smoothing: antialiased;
  transition: background 0.4s ease, color 0.4s ease;
}

/* Sci-Fi Holographic Grid & Laser Sweep */
body::before {
  content: ''; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background-image:
    linear-gradient(rgba(0, 240, 255, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 240, 255, 0.04) 1px, transparent 1px);
  background-size: 32px 32px;
}
body::after {
  content: ''; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background: linear-gradient(180deg, transparent 0%, rgba(0, 240, 255, 0.05) 50%, transparent 100%);
  background-size: 100% 12px;
  animation: scifi-scan 10s linear infinite;
}
@keyframes scifi-scan {
  from { transform: translateY(-100%); }
  to   { transform: translateY(100%); }
}

.bg-grid {
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background: radial-gradient(circle at 50% 0%, rgba(0, 240, 255, 0.15) 0%, rgba(217, 70, 239, 0.08) 45%, transparent 75%);
}

/* ── LAYOUT ──────────────────────────────────────────────────────────────── */
.layout {
  position: relative; z-index: 2;
  display: grid;
  grid-template-rows: auto auto 1fr auto;
  height: 100vh;
  background: transparent;
}

/* ── SCI-FI HEADER ───────────────────────────────────────────────────────── */
header {
  display: flex; align-items: center; gap: 16px;
  padding: 0 26px; height: 64px;
  background: var(--header-bg);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  border-bottom: 1px solid var(--border);
  box-shadow: 0 4px 30px rgba(0, 240, 255, 0.18);
  transition: background 0.4s ease;
}

.logo {
  font-family: var(--font-scifi); font-size: 16px; font-weight: 900; letter-spacing: 2px;
  background: linear-gradient(135deg, var(--green) 0%, var(--cyan) 50%, var(--purple) 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  display: flex; align-items: center; gap: 8px;
  filter: drop-shadow(0 0 12px rgba(0,240,255,0.4));
  text-transform: uppercase;
}

.live-pill {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 14px;
  background: rgba(0, 255, 157, 0.08);
  border: 1px solid rgba(0, 255, 157, 0.35);
  clip-path: polygon(0 0, calc(100% - 6px) 0, 100% 6px, 100% 100%, 6px 100%, 0 calc(100% - 6px));
  font-family: var(--font-scifi); font-size: 10px; font-weight: 700; letter-spacing: 1.5px; color: var(--green);
  box-shadow: 0 0 16px rgba(0, 255, 157, 0.25);
}
.live-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green); box-shadow: 0 0 12px var(--green);
  animation: scifi-pulse 1.4s ease-in-out infinite;
}
@keyframes scifi-pulse { 0%,100%{opacity:1; transform:scale(1);} 50%{opacity:.3; transform:scale(0.75);} }

.header-sep { flex: 1; }

.hdr-meta { display: flex; align-items: center; gap: 20px; }
.hdr-meta .label {
  font-family: var(--font-scifi); font-size: 10px; font-weight: 700; color: var(--dim); text-transform: uppercase; letter-spacing: 1px;
}
.hdr-meta .val {
  font-family: var(--mono); font-size: 12px; font-weight: 700; color: var(--cyan);
  background: rgba(0, 240, 255, 0.08); padding: 3px 10px; border-radius: 4px;
  border: 1px solid rgba(0, 240, 255, 0.25); text-shadow: 0 0 8px rgba(0,240,255,0.4);
}

/* ── SCI-FI STATS BAR ────────────────────────────────────────────────────── */
.stats {
  display: flex; border-bottom: 1px solid var(--border);
  background: var(--stats-bg); backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  transition: background 0.4s ease;
}
.stat {
  flex: 1; padding: 16px 24px;
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 6px;
  transition: background .25s ease; position: relative; overflow: hidden;
  /* NO clip-path - it breaks 3D transform-style preserve-3d */
}
.stat:hover { background: rgba(0, 240, 255, 0.07); }
.stat:last-child { border-right: none; }

.stat:nth-child(1) { border-top: 3px solid var(--cyan); }
.stat:nth-child(2) { border-top: 3px solid var(--green); }
.stat:nth-child(3) { border-top: 3px solid var(--red); }
.stat:nth-child(4) { border-top: 3px solid var(--amber); }

.stat-lbl {
  font-family: var(--font-scifi); font-size: 10px; font-weight: 700; letter-spacing: 1.5px;
  text-transform: uppercase; color: var(--muted);
}
.stat-val {
  font-family: var(--mono); font-size: 26px; font-weight: 800;
  color: var(--text); transition: all .3s;
}
.stat-val.g { color: var(--green); text-shadow: 0 0 18px var(--green-glow); }
.stat-val.b { color: var(--cyan);   text-shadow: 0 0 18px var(--cyan-glow); }
.stat-val.p { color: var(--purple); text-shadow: 0 0 18px var(--purple-glow); }
.stat-sub { font-size: 11px; color: var(--dim); font-family: var(--mono); }

/* ── THREE-COLUMN MAIN AREA ──────────────────────────────────────────────── */
.cols {
  display: grid; grid-template-columns: 340px 1fr 280px; gap: 16px; padding: 16px; overflow: hidden;
}

.panel {
  display: flex; flex-direction: column;
  border: 1px solid var(--border); border-radius: 12px;
  background: var(--surface); backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  overflow: hidden; transition: background 0.4s ease, border-color 0.4s ease;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.panel-hdr {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--panel-hdr-bg); flex-shrink: 0;
  transition: background 0.4s ease;
}
.panel-hdr .ph-icon { font-size: 16px; }
.panel-hdr .ph-title {
  font-family: var(--font-scifi); font-size: 11px; font-weight: 700; letter-spacing: 1.5px;
  text-transform: uppercase; color: var(--cyan); text-shadow: 0 0 10px rgba(0,240,255,0.3);
}
.ph-badge {
  margin-left: auto;
  font-family: var(--mono); font-size: 11px; font-weight: 800;
  padding: 3px 12px; border-radius: 4px;
  background: rgba(0, 240, 255, 0.12);
  border: 1px solid rgba(0, 240, 255, 0.35);
  color: var(--cyan); box-shadow: 0 0 12px rgba(0, 240, 255, 0.2);
}

.panel-body { flex: 1; overflow-y: auto; }
.panel-body::-webkit-scrollbar { width: 4px; }
.panel-body::-webkit-scrollbar-track { background: transparent; }
.panel-body::-webkit-scrollbar-thumb { background: rgba(0, 240, 255, 0.3); border-radius: 4px; }

/* ── 3D HOLOGRAPHIC TILT (stats & cards) ──────────────────────────────────── */
.stat {
  perspective: 1000px;
  transform-style: preserve-3d;
  transition: transform 0.12s ease-out;
}
.stat-val, .tag-score {
  will-change: transform;
  transform: translateZ(20px);
  display: inline-block;
}

@keyframes fade-up {
  from { opacity:0; transform: translateY(10px); }
  to   { opacity:1; transform: translateY(0); }
}

.job-card {
  padding: 16px 20px;
  animation: fade-up .35s ease-out;
  background: var(--card-bg);
  margin: 10px 14px;
  border-radius: 8px;
  border-left: 3px solid var(--cyan);
  border-top: 1px solid var(--border);
  border-right: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  position: relative;
  perspective: 1000px;
  transform-style: preserve-3d;
  transition: transform 0.12s ease-out, box-shadow 0.2s, border-color 0.2s, background 0.4s;
}
.job-card:hover {
  background: var(--surface-hover);
  border-left-color: var(--purple);
  border-top-color: var(--cyan);
  box-shadow: 0 12px 40px rgba(0,0,0,0.5), 0 0 24px rgba(0, 240, 255, 0.25);
}
.jc-title {
  font-family: var(--font-scifi); font-size: 13px; font-weight: 700; line-height: 1.4;
  margin-bottom: 9px; color: var(--text); letter-spacing: .5px;
  will-change: transform; transform: translateZ(16px); display: block;
}
.jc-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.tag {
  font-family: var(--font-scifi); font-size: 10px; font-weight: 700;
  padding: 4px 10px; border-radius: 4px; letter-spacing: .5px;
}
.tag-co  { background: rgba(0, 240, 255, 0.14); color: var(--cyan); border: 1px solid rgba(0, 240, 255, 0.35); }
.tag-lvl { background: rgba(0, 255, 157, 0.14); color: var(--green); border: 1px solid rgba(0, 255, 157, 0.35); }
.tag-loc { background: rgba(142, 165, 200, 0.12); color: var(--muted); border: 1px solid rgba(142, 165, 200, 0.25); }
.tag-score {
  background: linear-gradient(135deg, rgba(217, 70, 239, 0.3), rgba(0, 240, 255, 0.3));
  color: var(--purple); border: 1px solid rgba(217, 70, 239, 0.5);
  font-weight: 900; box-shadow: 0 0 14px rgba(217, 70, 239, 0.3);
}
.tag-ts  { background: transparent; color: var(--dim); font-family: var(--mono); font-size: 10px; border: none; }
.jc-link {
  display: inline-block; margin-top: 10px;
  font-size: 11px; color: var(--cyan); font-family: var(--mono);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  text-decoration: none; transition: color .2s; font-weight: 600;
}
.jc-link:hover { color: var(--green); text-decoration: underline; }

.jc-reason {
  margin-top: 10px; font-size: 12px; color: #e9d5ff;
  background: rgba(217, 70, 239, 0.12); border-left: 3px solid var(--purple);
  padding: 8px 12px; border-radius: 4px; line-height: 1.5;
}

/* ── SCI-FI TACTICAL BUTTONS ────────────────────────────────────────────── */
.hdr-controls { display: flex; align-items: center; gap: 10px; margin-left: 14px; position: relative; z-index: 1000; }
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 16px; border-radius: 6px;
  font-family: var(--font-scifi); font-size: 11px; font-weight: 700; letter-spacing: 1px;
  cursor: pointer; transition: all .25s ease; border: 1px solid transparent; outline: none; color: #fff;
  text-transform: uppercase; position: relative; z-index: 1000; pointer-events: auto !important;
  user-select: none;
}
.btn-start {
  background: linear-gradient(135deg, #00ff9d 0%, #059669 100%);
  box-shadow: 0 0 16px rgba(0, 255, 157, 0.4); text-shadow: 0 0 6px rgba(0,0,0,0.6);
}
.btn-start:hover:not(:disabled) {
  box-shadow: 0 0 26px rgba(0, 255, 157, 0.7); transform: translateY(-1px);
}
.btn-stop {
  background: linear-gradient(135deg, #ff2a6d 0%, #be123c 100%);
  box-shadow: 0 0 16px rgba(255, 42, 109, 0.4); text-shadow: 0 0 6px rgba(0,0,0,0.6);
}
.btn-stop:hover:not(:disabled) {
  box-shadow: 0 0 26px rgba(255, 42, 109, 0.7); transform: translateY(-1px);
}
.btn-clear {
  background: linear-gradient(135deg, #ffb703 0%, #b45309 100%);
  box-shadow: 0 0 16px rgba(255, 183, 3, 0.4); text-shadow: 0 0 6px rgba(0,0,0,0.6);
}
.btn-clear:hover:not(:disabled) {
  box-shadow: 0 0 26px rgba(255, 183, 3, 0.7); transform: translateY(-1px);
}
.btn:disabled { opacity: 0.3; cursor: not-allowed; box-shadow: none !important; transform: none !important; }

.status-pill {
  display: flex; align-items: center; gap: 7px;
  padding: 4px 14px;
  clip-path: polygon(0 0, calc(100% - 6px) 0, 100% 6px, 100% 100%, 6px 100%, 0 calc(100% - 6px));
  font-family: var(--font-scifi); font-size: 10px; font-weight: 700; letter-spacing: 1px;
}
.status-pill.running {
  background: rgba(0, 255, 157, 0.12); border: 1px solid rgba(0, 255, 157, 0.4); color: var(--green);
}
.status-pill.stopped {
  background: rgba(255, 42, 109, 0.12); border: 1px solid rgba(255, 42, 109, 0.4); color: var(--red);
}

.sort-select {
  background: rgba(0, 240, 255, 0.08); color: var(--text); border: 1px solid rgba(0, 240, 255, 0.25);
  border-radius: 4px; padding: 5px 12px; font-family: var(--font-scifi); font-size: 11px; font-weight: 700;
  outline: none; cursor: pointer; margin-left: auto; transition: all .2s;
}
.sort-select:hover { border-color: var(--cyan); box-shadow: 0 0 16px rgba(0,240,255,0.3); }
.sort-select option { background: #040a18; color: #f0f6fc; }

footer {
  display: flex; align-items: center; gap: 16px;
  padding: 6px 26px; height: 34px;
  background: var(--footer-bg); backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-top: 1px solid var(--border);
  font-family: var(--mono); font-size: 11px; color: var(--dim);
  transition: background 0.4s ease;
}
.btn-theme {
  background: linear-gradient(135deg, #0284c7 0%, #6366f1 100%);
  box-shadow: 0 0 14px rgba(2, 132, 199, 0.35); text-shadow: 0 0 6px rgba(0,0,0,0.4);
}
.btn-theme:hover:not(:disabled) {
  box-shadow: 0 0 24px rgba(2, 132, 199, 0.65); transform: translateY(-1px);
}
.fc-ok  { color: var(--green); font-weight: 700; text-shadow: 0 0 8px var(--green-glow); }
.fc-err { color: var(--red); font-weight: 700; text-shadow: 0 0 8px var(--red-glow); }
.fsep   { color: rgba(0, 240, 255, 0.2); }
.sort-select option { background: #040a18; color: #f0f6fc; }
[data-theme="light"] .sort-select option { background: #f1f5f9; color: #0f172a; }
</style>
</head>
<body>
<!-- 3D Globe canvas sits at z-index:1 behind layout (z-index:2) but above bg (z-index:0) -->
<canvas id="canvas-globe" style="position:fixed; inset:0; z-index:1; pointer-events:none; width:100%; height:100%;"></canvas>
<div class="bg-grid"></div>
<div class="layout">

<!-- HEADER -->
<header>
  <span class="logo">⚡ Job Watcher</span>
  <div class="live-pill"><div class="live-dot"></div>LIVE</div>
  <div id="scraper-status" class="status-pill stopped">
    <canvas id="canvas-reactor" style="width:22px; height:22px; margin-right:4px; vertical-align:middle;"></canvas>
    <div class="status-dot"></div><span id="status-text">Scraper: STOPPED</span>
  </div>
  <div class="hdr-controls">
    <button class="btn btn-start" id="btn-start" onclick="startScraper()">▶ Start Scraping</button>
    <button class="btn btn-stop" id="btn-stop" onclick="stopScraper()" disabled>⏹ Stop Scraping</button>
    <button class="btn btn-start" id="btn-apply-visible" style="background: linear-gradient(135deg, #6366f1, #a855f7); border: none; color: #fff; font-weight:600;" onclick="startAutoApply(false)">👁️ Visible Apply</button>
    <button class="btn btn-start" id="btn-apply-silent" style="background: linear-gradient(135deg, #3b82f6, #10b981); border: none; color: #fff; font-weight:600;" onclick="startAutoApply(true)">👻 Silent Apply</button>
    <button class="btn btn-clear" id="btn-clear" onclick="clearDb()">🗑 Clear DB</button>
    <button class="btn btn-theme" id="btn-theme-toggle" onclick="toggleTheme()">☀️ Light Mode</button>
  </div>
  <span class="header-sep"></span>
  <div class="hdr-meta">
    <span class="label">Companies</span>
    <span class="val" id="h-cos">—</span>
    <span class="label">Interval</span>
    <span class="val" id="h-int">—</span>
    <span class="label">Clock</span>
    <span class="val" id="clock">--:--:--</span>
  </div>
</header>


<!-- STATS BAR -->
<div class="stats">
  <div class="stat">
    <span class="stat-lbl">Total Jobs Found</span>
    <span class="stat-val g" id="sv-jobs">0</span>
    <span class="stat-sub" id="ss-jobs">in database</span>
  </div>
  <div class="stat">
    <span class="stat-lbl">Applied Success</span>
    <span class="stat-val g" id="sv-app-success">0</span>
    <span class="stat-sub">submitted live</span>
  </div>
  <div class="stat">
    <span class="stat-lbl">Failed Attempts</span>
    <span class="stat-val" style="color: #f87171;" id="sv-app-failed">0</span>
    <span class="stat-sub">application errors</span>
  </div>
  <div class="stat">
    <span class="stat-lbl">Pending Unapplied</span>
    <span class="stat-val" style="color: #fbbf24;" id="sv-app-pending">0</span>
    <span class="stat-sub">>=80% match score</span>
  </div>
</div>

<!-- COLUMNS -->
<div class="cols">

  <!-- ACTIVITY FEED -->
  <div class="panel minimized" id="feed-panel">
    <div class="panel-hdr">
      <span class="ph-icon">📡</span>
      <span class="ph-title">Activity Feed</span>
      <span class="ph-badge" id="feed-cnt">0</span>
      <span class="panel-hdr-toggle" id="feed-toggle-btn" onclick="toggleFeedPanel()">[Expand]</span>
    </div>
    <div class="panel-body" id="feed-body">
      <div class="empty" id="feed-empty">
        <div class="empty-ico">📡</div>
        <div class="empty-ttl">Scraper Stopped / Idle</div>
        <div class="empty-sub">Activity feed is minimized when scraper is stopped.<br>Start the scraper to stream live events.</div>
      </div>
    </div>
  </div>

  <!-- JOBS FOUND -->
  <div class="panel">
    <div class="panel-hdr">
      <span class="ph-icon">💼</span>
      <span class="ph-title">Jobs Discovered</span>
      <span class="ph-badge" id="jobs-cnt">0</span>
      <select id="jobs-sort" class="sort-select" onchange="sortAndRenderJobs()">
        <option value="last_identified">🔎 Identified Last (Scraping Sort)</option>
        <option value="match_desc">⚡ Highest Match %</option>
        <option value="newest">🕒 Newest Posted</option>
        <option value="company">🏢 Company (A-Z)</option>
        <option value="location">📍 Location</option>
      </select>
    </div>
    <div class="panel-body" id="jobs-body">
      <div class="empty" id="jobs-empty">
        <div class="empty-ico">🔍</div>
        <div class="empty-ttl">No jobs yet</div>
        <div class="empty-sub">Matching jobs will appear here<br>after the first successful cycle.</div>
      </div>
    </div>
  </div>

  <!-- COMPANY STATUS -->
  <div class="panel">
    <div class="panel-hdr">
      <span class="ph-icon">🏢</span>
      <span class="ph-title">Companies</span>
      <span class="ph-badge" id="co-cnt">0</span>
    </div>
    <div class="panel-body" id="co-body">
      <div class="empty" id="co-empty">
        <div class="empty-ico">🏢</div>
        <div class="empty-ttl">Not yet checked</div>
        <div class="empty-sub">Company results appear<br>after the first cycle.</div>
      </div>
    </div>
  </div>

</div><!-- /cols -->

<!-- FOOTER STATUS BAR -->
<footer>
  <span>DB: <span id="db-st" class="fc-ok">connected</span></span>
  <span class="fsep">│</span>
  <span>Poll: 1.5s</span>
  <span class="fsep">│</span>
  <span id="last-poll">Last poll: —</span>
  <span style="margin-left:auto" class="fsep">│</span>
  <a href="/api/jobs" target="_blank">JSON</a>
  <span class="fsep">│</span>
  <a href="/api/events" target="_blank">Events</a>
</footer>

</div><!-- /layout -->

<script>
// ── STATE ─────────────────────────────────────────────────────────────────
const S = {
  lastId:        0,
  seenJobIds:    new Set(),
  seenEventIds:  new Set(),
  lastFeedMessage: '',
  intervalMin:   30,
  cycleEndTs:    null,    // ts of last cycle_end event
  cycleStartTs:  null,    // ts of last cycle_start event
  feedCount:     0,
  checksThisCycle: 0,
};

// ── ICONS ─────────────────────────────────────────────────────────────────
const ICON = {
  cycle_start:      '🔄',
  cycle_end:        '✅',
  company_checking: '⟳',
  company_ok:       '📡',
  company_error:    '❌',
  company_skip:     '⏭',
  new_job:          '✨',
  email_sent:       '📧',
  email_error:      '⚠️',
  url_verify:       '🔗',
  watcher_start:    '🚀',
  agent_step:       '🤖',
};

// ── UTILS ─────────────────────────────────────────────────────────────────
const h = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const hA = s => h(s).replace(/"/g,'&quot;');

function ago(ts) {
  if (!ts) return '—';
  const d = Math.floor(Date.now()/1000 - ts);
  if (d <  5)    return 'just now';
  if (d <  60)   return d + 's ago';
  if (d < 3600)  return Math.floor(d/60) + 'm ago';
  return Math.floor(d/3600) + 'h ago';
}
function fmtTs(ts) {
  if (!ts) return '—';
  return new Date(ts*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
function fmtDur(sec) {
  sec = Math.round(sec);
  if (sec < 60) return sec + 's';
  return `${Math.floor(sec/60)}m ${sec%60}s`;
}

// ── CLOCK ─────────────────────────────────────────────────────────────────
function tickClock() {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString([], {hour12:false});
}
setInterval(tickClock, 1000); tickClock();


// ── FEED ─────────────────────────────────────────────────────────────────
function addFeedItem(ev) {
  if (ev.id && S.seenEventIds.has(ev.id)) return;
  if (ev.id) S.seenEventIds.add(ev.id);

  const msgKey = (ev.event_type || '') + ':' + (ev.message || '');
  if (msgKey === S.lastFeedMessage) return;
  S.lastFeedMessage = msgKey;

  document.getElementById('feed-empty')?.remove();
  const body = document.getElementById('feed-body');
  const d = document.createElement('div');
  d.className = `feed-item fi-${ev.event_type}`;

  const isSystem = ['cycle_start','cycle_end','watcher_start','url_verify','email_sent','email_error'].includes(ev.event_type);
  const coDisplay = isSystem ? 'System' : (ev.company || 'System');

  // 'checking' events are lower-opacity to act as background noise vs. results
  const isDimmed = ev.event_type === 'company_checking';
  if (isDimmed) d.style.opacity = '0.55';

  d.innerHTML = `
    <span class="fi-icon">${ICON[ev.event_type]||'•'}</span>
    <div class="fi-body">
      <div class="fi-co">${h(coDisplay)}</div>
      <div class="fi-msg">${h(ev.message||ev.event_type)}</div>
    </div>
    <span class="fi-ts">${fmtTs(ev.ts)}</span>`;

  body.insertBefore(d, body.firstChild);

  S.feedCount++;
  // Trim DOM
  const items = body.querySelectorAll('.feed-item');
  if (items.length > 250) items[items.length-1].remove();
  document.getElementById('feed-cnt').textContent = S.feedCount;

  // Track cycle state
  if (ev.event_type === 'cycle_start') {
    S.cycleStartTs = ev.ts;
    S.checksThisCycle = 0;
  }
  if (ev.event_type === 'company_ok' || ev.event_type === 'company_error' || ev.event_type === 'company_skip') {
    S.checksThisCycle++;
  }
  if (ev.event_type === 'cycle_end') {
    S.cycleEndTs = ev.ts;
    const m = ev.message && ev.message.match(/Done in (\d+(?:\.\d+)?)s/);
    if (m) {
      document.getElementById('sv-dur').textContent = fmtDur(parseFloat(m[1]));
      document.getElementById('ss-dur').textContent = 'at ' + fmtTs(ev.ts);
    }
  }
}

// ── JOBS ─────────────────────────────────────────────────────────────────
function addJobCardToBody(job, body) {
  const link = (job.url && job.url.startsWith('http')) ? job.url : job.job_id;
  const linkDisplay = String(link).substring(0, 90) + (link.length > 90 ? '\u2026' : '');
  const d = document.createElement('div');
  d.className = 'job-card';
  const cardId = 'jd-' + Math.random().toString(36).substring(2, 9);
  const reasoning = job.match_analysis || job.filter_reason || '';
  const jdText = job.jd_text || '';
  const displayDate = job.posted_date ? `📅 ${h(job.posted_date)}` : ago(job.first_seen);

  let applyTag = '<span class="tag" style="background: rgba(251,191,36,0.15); border: 1px solid rgba(251,191,36,0.3); color: #fbbf24; font-weight:700;">⏳ Ready to Apply</span>';
  const resUpper = String(job.apply_result || '').toUpperCase();
  if (resUpper.includes('SUCCESS')) {
    applyTag = '<span class="tag" style="background: rgba(74,222,128,0.15); border: 1px solid rgba(74,222,128,0.3); color: #4ade80; font-weight:700;">✅ Applied Successfully</span>';
  } else if (resUpper.includes('DRY_RUN') || resUpper.includes('DRY RUN')) {
    applyTag = '<span class="tag" style="background: rgba(168,85,247,0.15); border: 1px solid rgba(168,85,247,0.3); color: #c084fc; font-weight:700;">🟣 Dry-Run Form Filled</span>';
  } else if (resUpper.includes('FAILED')) {
    applyTag = '<span class="tag" style="background: rgba(248,113,113,0.15); border: 1px solid rgba(248,113,113,0.3); color: #f87171; font-weight:700;">❌ Application Failed</span>';
  }

  d.innerHTML = `
    <div class="jc-title">${h(job.title||'Untitled')}</div>
    <div class="jc-meta">
      <span class="tag tag-score" title="${hA(reasoning)}">⚡ ${Math.round(job.match_score||70)}% Match</span>
      ${applyTag}
      <span class="tag tag-co">${h(job.company)}</span>
      ${job.seniority ? `<span class="tag tag-lvl">${h(job.seniority)}</span>` : ''}
      ${job.location  ? `<span class="tag tag-loc">\uD83D\uDCCD ${h(String(job.location).substring(0,45))}</span>` : ''}
      <span class="tag tag-ts">${displayDate}</span>
    </div>
    ${reasoning ? `<div class="jc-reason">💡 <strong>LLM Analysis:</strong> ${h(reasoning)}</div>` : ''}
    <a class="jc-link" href="${hA(link)}" target="_blank">${h(linkDisplay)}</a>
    ${jdText ? `
      <div class="jc-jd-toggle" onclick="toggleJd('${cardId}', this)">📖 View Job Description</div>
      <div class="jc-jd-body" id="${cardId}">${h(jdText)}</div>
    ` : ''}`;

  body.appendChild(d);
}

function addJobCard(job) {
  if (S.seenJobIds.has(job.job_id)) return;
  S.seenJobIds.add(job.job_id);
  document.getElementById('jobs-empty')?.remove();
  const body = document.getElementById('jobs-body');
  addJobCardToBody(job, body);

  const n = S.seenJobIds.size;
  document.getElementById('jobs-cnt').textContent = n;
  document.getElementById('sv-jobs').textContent  = n;
  document.getElementById('ss-jobs').textContent  = `${n} job${n!==1?'s':''} in database`;
}

function toggleJd(id, btn) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.style.display === 'block') {
    el.style.display = 'none';
    btn.textContent = '📖 View Job Description';
  } else {
    el.style.display = 'block';
    btn.textContent = '🙈 Hide Job Description';
  }
}

// ── COMPANIES ─────────────────────────────────────────────────────────────
function renderCompanies(list) {
  if (!list || !list.length) return;
  document.getElementById('co-empty')?.remove();

  // Sort: has jobs first, then name
  list.sort((a,b) => (b.jobs_found||0)-(a.jobs_found||0) || a.name.localeCompare(b.name));

  const body = document.getElementById('co-body');
  body.innerHTML = '';
  list.forEach(co => {
    const dotCls =
      co.status==='ok'    ? (co.jobs_found>0 ? 'd-ok'   : 'd-wait') :
      co.status==='error' ? 'd-err'   :
      co.status==='skip'  ? 'd-skip'  :
      co.status==='check' ? 'd-check' : 'd-wait';
    const d = document.createElement('div');
    d.className = `co-item${co.jobs_found>0?' active':''}`;
    d.innerHTML = `
      <div class="co-dot ${dotCls}"></div>
      <span class="co-name" title="${hA(co.name)}">${h(co.name)}</span>
      ${co.jobs_found>0 ? `<span class="co-cnt">+${co.jobs_found}</span>` : ''}`;
    body.appendChild(d);
  });
  document.getElementById('co-cnt').textContent = list.length;
}

// ── CONTROLS ──────────────────────────────────────────────────────────────
function toggleFeedPanel() {
  const panel = document.getElementById('feed-panel');
  const btn = document.getElementById('feed-toggle-btn');
  if (!panel) return;
  if (panel.classList.contains('minimized')) {
    panel.classList.remove('minimized');
    if (btn) btn.textContent = '[Minimize]';
  } else {
    panel.classList.add('minimized');
    if (btn) btn.textContent = '[Expand]';
  }
}

function updateScraperStatus(isRunning) {
  S.scraperRunning = isRunning;
  const pill      = document.getElementById('scraper-status') || document.getElementById('sp-pill');
  const statusTxt = document.getElementById('status-text') || document.getElementById('sp-status-txt');
  const startBtn  = document.getElementById('btn-start');
  const stopBtn   = document.getElementById('btn-stop');
  const feedPanel = document.getElementById('feed-panel');

  if (isRunning) {
    if (pill) pill.className = 'status-pill running';
    if (statusTxt) statusTxt.textContent = 'Scraper: RUNNING';
    if (startBtn) startBtn.disabled = true;
    if (stopBtn)  stopBtn.disabled  = false;
    document.getElementById('feed-idle-banner')?.remove();
    if (feedPanel) {
      feedPanel.classList.remove('minimized');
      const btn = document.getElementById('feed-toggle-btn');
      if (btn) btn.textContent = '[Minimize]';
    }
  } else {
    if (pill) pill.className = 'status-pill stopped';
    if (statusTxt) statusTxt.textContent = 'Scraper: STOPPED';
    if (startBtn) startBtn.disabled = false;
    if (stopBtn)  stopBtn.disabled  = true;
    if (feedPanel) {
      feedPanel.classList.add('minimized');
      const btn = document.getElementById('feed-toggle-btn');
      if (btn) btn.textContent = '[Expand]';
    }
  }
}

// ── LIGHT / DARK THEME SWITCHER ───────────────────────────────────────────
function toggleTheme() {
  try { SciFiAudio.playClick(); } catch(e){}
  const html = document.documentElement;
  const cur = html.getAttribute('data-theme') || 'dark';
  const next = cur === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('jobsense-theme', next);
  updateThemeButtonText(next);
  console.log('[Dashboard] Theme toggled to:', next);
}

function updateThemeButtonText(theme) {
  const btn = document.getElementById('btn-theme-toggle');
  if (btn) {
    btn.textContent = theme === 'light' ? '🌙 Dark Mode' : '☀️ Light Mode';
  }
}

// ── SCI-FI TACTICAL AUDIO SYNTH ───────────────────────────────────────────
const SciFiAudio = {
  ctx: null,
  init() {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) this.ctx = new AudioCtx();
    }
  },
  playClick() {
    try {
      this.init();
      if (!this.ctx) return;
      if (this.ctx.state === 'suspended') {
        this.ctx.resume().catch(() => {});
      }
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(960, this.ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(480, this.ctx.currentTime + 0.07);
      gain.gain.setValueAtTime(0.12, this.ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 0.07);
      osc.connect(gain); gain.connect(this.ctx.destination);
      osc.start(); osc.stop(this.ctx.currentTime + 0.07);
    } catch(e){}
  }
};

async function startScraper() {
  try { SciFiAudio.playClick(); } catch(e) {}
  console.log('[Dashboard] startScraper clicked');
  const startBtn = document.getElementById('btn-start');
  const stopBtn  = document.getElementById('btn-stop');
  if (startBtn) startBtn.disabled = true;
  if (stopBtn)  stopBtn.disabled  = true;
  try {
    const r = await fetch('/api/control/start', { method: 'POST' });
    const data = await r.json();
    if (r.ok) {
      updateScraperStatus(true);
    } else {
      alert(data.message || 'Error starting scraper');
      updateScraperStatus(false);
    }
  } catch (e) {
    alert('Error starting scraper: ' + e);
    updateScraperStatus(false);
  }
}

async function stopScraper() {
  try { SciFiAudio.playClick(); } catch(e) {}
  console.log('[Dashboard] stopScraper clicked');
  const startBtn = document.getElementById('btn-start');
  const stopBtn  = document.getElementById('btn-stop');
  if (startBtn) startBtn.disabled = true;
  if (stopBtn)  stopBtn.disabled  = true;
  try {
    const r = await fetch('/api/control/stop', { method: 'POST' });
    const data = await r.json();
    if (r.ok) {
      updateScraperStatus(false);
    } else {
      alert(data.message || 'Error stopping scraper');
      updateScraperStatus(true);
    }
  } catch (e) {
    alert('Error stopping scraper: ' + e);
    updateScraperStatus(false);
  }
}

async function startAutoApply(isHeadless) {
  try { SciFiAudio.playClick(); } catch(e) {}
  console.log('[Dashboard] startAutoApply clicked, isHeadless:', isHeadless);
  const btnV = document.getElementById('btn-apply-visible');
  const btnS = document.getElementById('btn-apply-silent');
  if (btnV) btnV.disabled = true;
  if (btnS) btnS.disabled = true;
  const endpoint = isHeadless ? '/api/control/auto_apply?headless=1' : '/api/control/auto_apply?headless=0';
  const modeTxt = isHeadless ? 'Background Silent Mode 👻' : 'Visible Screen Mode 👁️';
  try {
    const r = await fetch(endpoint, { method: 'POST' });
    const data = await r.json();
    if (r.ok) {
      alert(`🤖 Auto-Apply Engine launched in ${modeTxt}! Processing top job...`);
    } else {
      alert(data.message || 'Error launching Auto-Apply');
    }
  } catch (e) {
    alert('Error launching Auto-Apply: ' + e);
  } finally {
    setTimeout(() => { 
      if (btnV) btnV.disabled = false; 
      if (btnS) btnS.disabled = false; 
    }, 3000);
  }
}


async function clearDb() {
  try { SciFiAudio.playClick(); } catch(e) {}
  console.log('[Dashboard] clearDb clicked');
  if (!confirm('Are you sure you want to clear all jobs and events from the database?')) return;
  const btn = document.getElementById('btn-clear');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/control/clear_db', { method: 'POST' });
    const data = await r.json();
    if (r.ok) {
      S.lastId = 0;
      S.seenJobIds.clear();
      S.feedCount = 0;
      S.checksThisCycle = 0;
      const feedBody = document.getElementById('feed-body');
      if (feedBody) {
        feedBody.innerHTML = `
          <div class="empty" id="feed-empty">
            <div class="empty-ico">📡</div>
            <div class="empty-ttl">Awaiting events…</div>
            <div class="empty-sub">Live activity will stream here<br>once career_watcher.py is running.</div>
          </div>`;
      }
      const jobsBody = document.getElementById('jobs-body');
      if (jobsBody) {
        jobsBody.innerHTML = `
          <div class="empty" id="jobs-empty">
            <div class="empty-ico">🔍</div>
            <div class="empty-ttl">No jobs yet</div>
            <div class="empty-sub">Matching jobs will appear here<br>after the first successful cycle.</div>
          </div>`;
      }
      document.getElementById('jobs-cnt').textContent = '0';
      document.getElementById('feed-cnt').textContent = '0';
      document.getElementById('sv-jobs').textContent = '0';
      document.getElementById('ss-jobs').textContent = '0 jobs in database';
      await pollStats();
      await pollCompanies();
    } else {
      alert(data.message || 'Error clearing DB');
    }
  } catch (e) {
    alert('Error clearing DB: ' + e);
  } finally {
    btn.disabled = false;
  }
}

// ── POLLS ─────────────────────────────────────────────────────────────────
async function pollEvents() {
  try {
    const r = await fetch(`/api/events?since=${S.lastId}`);
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    document.getElementById('db-st').textContent = 'connected';
    document.getElementById('db-st').className   = 'fc-ok';

    // On first load (lastId=0), anchor to max_id so subsequent polls only get new events
    if (S.lastId === 0 && data.max_id) {
      S.lastId = data.max_id;
    }

    // Clear feed if new cycle started
    if (data.cycle_start_id && data.cycle_start_id !== S.currentCycleStartId) {
      S.currentCycleStartId = data.cycle_start_id;
    }

    if (data.events && data.events.length) {
      // Add in reverse so newest appears at top after insertBefore
      [...data.events].reverse().forEach(addFeedItem);
      if (data.latest_id > S.lastId) S.lastId = data.latest_id;
    }
    document.getElementById('last-poll').textContent =
      'Last poll: ' + new Date().toLocaleTimeString([],{hour12:false});
  } catch(e) {
    document.getElementById('db-st').textContent = 'error';
    document.getElementById('db-st').className   = 'fc-err';
  }
}

async function pollStats() {
  try {
    const r = await fetch('/api/stats');
    if (!r.ok) return;
    const d = await r.json();
    S.intervalMin = d.interval_min || 30;
    document.getElementById('h-cos').textContent   = d.company_count || '—';
    document.getElementById('h-int').textContent   = `${d.interval_min}m`;

    if (d.apply_stats) {
      const elSucc = document.getElementById('sv-app-success'); if (elSucc) elSucc.textContent = d.apply_stats.success || 0;
      const elDry  = document.getElementById('sv-app-dry');     if (elDry)  elDry.textContent  = d.apply_stats.dry_run || 0;
      const elFail = document.getElementById('sv-app-failed');  if (elFail) elFail.textContent = d.apply_stats.failed || 0;
      const elPend = document.getElementById('sv-app-pending'); if (elPend) elPend.textContent = d.apply_stats.pending || 0;
    }

    // Update Scraper status pill & control button states
    updateScraperStatus(!!d.scraper_running);
  } catch(e) {}
}



async function pollJobs() {
  try {
    const r = await fetch('/api/jobs');
    if (!r.ok) return;
    const d = await r.json();
    if (d.jobs) {
      const fp = d.jobs.map(j => j.job_id + ':' + (j.match_score||0)).join('|');
      if (fp !== S.lastJobsFingerprint) {
        S.lastJobsFingerprint = fp;
        S.allJobs = d.jobs;
        sortAndRenderJobs(true);
      }
    }
  } catch(e) {}
}

function sortAndRenderJobs(force = false) {
  if (!S.allJobs || !S.allJobs.length) return;
  const sortMode = document.getElementById('jobs-sort')?.value || 'last_identified';
  const sortFp = sortMode + ':' + (S.lastJobsFingerprint || '');

  if (!force && sortFp === S.lastSortFingerprint) {
    return;
  }
  S.lastSortFingerprint = sortFp;

  const sorted = [...S.allJobs];
  if (sortMode === 'last_identified') {
    sorted.sort((a,b) => (b.first_seen || 0) - (a.first_seen || 0));
  } else if (sortMode === 'match_desc') {
    sorted.sort((a,b) => (b.match_score || 0) - (a.match_score || 0) || (b.first_seen || 0) - (a.first_seen || 0));
  } else if (sortMode === 'newest') {
    sorted.sort((a,b) => (b.first_seen || 0) - (a.first_seen || 0));
  } else if (sortMode === 'company') {
    sorted.sort((a,b) => (a.company || '').localeCompare(b.company || ''));
  } else if (sortMode === 'location') {
    sorted.sort((a,b) => (a.location || '').localeCompare(b.location || ''));
  }

  const body = document.getElementById('jobs-body');
  if (!body) return;
  document.getElementById('jobs-empty')?.remove();
  body.innerHTML = '';

  S.seenJobIds.clear();
  sorted.forEach(job => {
    S.seenJobIds.add(job.job_id);
    addJobCardToBody(job, body);
  });

  const n = S.seenJobIds.size;
  document.getElementById('jobs-cnt').textContent = n;
  document.getElementById('sv-jobs').textContent  = n;
  document.getElementById('ss-jobs').textContent  = `${n} job${n!==1?'s':''} in database`;
}

function init3DTilt() {
  document.addEventListener('mousemove', (e) => {
    const card = e.target.closest('.job-card, .stat');
    if (!card) return;
    const rect = card.getBoundingClientRect();
    const x = e.clientX - rect.left - rect.width / 2;
    const y = e.clientY - rect.top - rect.height / 2;
    const tiltX = (y / (rect.height / 2)) * -10;
    const tiltY = (x / (rect.width / 2)) * 10;
    card.style.transform = `perspective(1000px) rotateX(${tiltX}deg) rotateY(${tiltY}deg) scale3d(1.02, 1.02, 1.02)`;
  });

  document.addEventListener('mouseout', (e) => {
    const card = e.target.closest('.job-card, .stat');
    if (card) {
      card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)';
    }
  });
}

function init3DGlobe() {
  const canvas = document.getElementById('canvas-globe');
  if (!canvas || typeof THREE === 'undefined') return;

  const W = window.innerWidth, H = window.innerHeight;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, W / H, 0.1, 1000);
  camera.position.z = 180;

  const renderer = new THREE.WebGLRenderer({
    canvas,
    alpha: true,
    antialias: true,
    powerPreference: "high-performance"
  });
  renderer.setSize(W, H);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));

  // ── 1. Lightweight 900-particle constellation ────────────────────────────
  const N = 900;
  const geom = new THREE.BufferGeometry();
  const pos  = new Float32Array(N * 3);
  const col  = new Float32Array(N * 3);
  const R = 110;
  const palette = [[0, 0.94, 1], [0, 1, 0.61], [0.85, 0.28, 0.94], [1, 0.72, 0.01]];

  for (let i = 0; i < N; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi   = Math.acos(2 * Math.random() - 1);
    pos[i*3]   = R * Math.sin(phi) * Math.cos(theta);
    pos[i*3+1] = R * Math.sin(phi) * Math.sin(theta);
    pos[i*3+2] = R * Math.cos(phi);
    const c = palette[i % palette.length];
    col[i*3] = c[0]; col[i*3+1] = c[1]; col[i*3+2] = c[2];
  }
  geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geom.setAttribute('color',    new THREE.BufferAttribute(col, 3));
  const ptMat = new THREE.PointsMaterial({ size: 3.8, vertexColors: true, transparent: true, opacity: 0.95 });
  const particles = new THREE.Points(geom, ptMat);
  scene.add(particles);

  // ── 2. Outer Wireframe ───────────────────────────────────────────────────
  const icoGeo = new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(R, 1));
  const icoMat = new THREE.LineBasicMaterial({ color: 0x00f0ff, transparent: true, opacity: 0.16 });
  const icos = new THREE.LineSegments(icoGeo, icoMat);
  scene.add(icos);

  // ── 3. Pulsing Core ──────────────────────────────────────────────────────
  const coreGeo = new THREE.IcosahedronGeometry(36, 1);
  const coreMat = new THREE.MeshBasicMaterial({ color: 0x00ff9d, wireframe: true, transparent: true, opacity: 0.30 });
  const core = new THREE.Mesh(coreGeo, coreMat);
  scene.add(core);

  // ── 4. Orbital Rings ─────────────────────────────────────────────────────
  const r1 = new THREE.Mesh(
    new THREE.TorusGeometry(120, 0.8, 8, 48),
    new THREE.MeshBasicMaterial({ color: 0x00f0ff, transparent: true, opacity: 0.45 })
  );
  r1.rotation.x = Math.PI / 2.8;
  scene.add(r1);

  const r2 = new THREE.Mesh(
    new THREE.TorusGeometry(132, 0.5, 8, 48),
    new THREE.MeshBasicMaterial({ color: 0xd946ef, transparent: true, opacity: 0.35 })
  );
  r2.rotation.y = Math.PI / 4;
  scene.add(r2);

  // ── 5. Throttled Mouse Tracking ──────────────────────────────────────────
  let targetX = 0, targetY = 0;
  let currentX = 0, currentY = 0;
  let mouseTicking = false;

  document.addEventListener('mousemove', e => {
    if (!mouseTicking) {
      requestAnimationFrame(() => {
        targetX = (e.clientX / window.innerWidth - 0.5) * 1.4;
        targetY = (e.clientY / window.innerHeight - 0.5) * 1.4;
        mouseTicking = false;
      });
      mouseTicking = true;
    }
  }, { passive: true });

  // ── 6. 60FPS Render Loop ─────────────────────────────────────────────────
  let t = 0;
  function animate() {
    requestAnimationFrame(animate);
    t += 0.016;

    currentX += (targetX - currentX) * 0.05;
    currentY += (targetY - currentY) * 0.05;

    particles.rotation.y = t * 0.15 + currentX;
    particles.rotation.x = currentY * 0.6;
    icos.rotation.y = particles.rotation.y;
    icos.rotation.x = particles.rotation.x;

    r1.rotation.z += 0.005;
    r2.rotation.z -= 0.004;

    const s = 1 + Math.sin(t * 2.5) * 0.1;
    core.scale.set(s, s, s);
    core.rotation.y += 0.015;

    renderer.render(scene, camera);
  }
  animate();

  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });
}

async function pollCompanies() {
  try {
    const r = await fetch('/api/companies');
    if (!r.ok) return;
    const d = await r.json();
    if (d.companies) {
      const fp = d.companies.map(c => c.name + ':' + c.status + ':' + (c.jobs_found||0)).join('|');
      if (fp !== S.lastCompaniesFingerprint) {
        S.lastCompaniesFingerprint = fp;
        renderCompanies(d.companies);
      }
    }
  } catch(e) {}
}

// ── 3D INTERACTIVE UI ENGINE ──────────────────────────────────────────────
function init3DTilt() {
  document.addEventListener('mousemove', (e) => {
    const cards = document.querySelectorAll('.stat, .job-card');
    cards.forEach(card => {
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      if (x >= 0 && x <= rect.width && y >= 0 && y <= rect.height) {
        const cx = rect.width / 2;
        const cy = rect.height / 2;
        const rx = ((y - cy) / cy) * -12;
        const ry = ((x - cx) / cx) * 12;
        card.style.transform = `perspective(1000px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg) scale3d(1.02, 1.02, 1.02)`;
      } else {
        card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)';
      }
    });
  });
}

function initReactorRing() {
  const canvas = document.getElementById('canvas-reactor');
  if (!canvas || typeof THREE === 'undefined') return;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
  camera.position.z = 2.8;

  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setSize(22, 22);

  const torusGeo = new THREE.TorusGeometry(0.85, 0.18, 12, 24);
  const torusMat = new THREE.MeshBasicMaterial({ color: 0x4ade80, wireframe: true });
  const torus = new THREE.Mesh(torusGeo, torusMat);
  scene.add(torus);

  function animate() {
    requestAnimationFrame(animate);
    torus.rotation.x += 0.04;
    torus.rotation.y += 0.05;
    renderer.render(scene, camera);
  }
  animate();
}

// ── BOOTSTRAP ─────────────────────────────────────────────────────────────
(async () => {
  const savedTheme = localStorage.getItem('jobsense-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', savedTheme);
  updateThemeButtonText(savedTheme);

  await pollStats();
  await pollJobs();
  await pollCompanies();
  await pollEvents();

  setInterval(pollEvents,    4000);
  setInterval(pollStats,     6000);
  setInterval(pollJobs,      5000);
  setInterval(pollCompanies, 10000);

  setTimeout(() => {
    init3DTilt();
    init3DGlobe();
    initReactorRing();
  }, 300);
})();
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/")
def index():
    return HTML


@app.route("/api/stats")
def api_stats():
    try:
        conn = get_db()
        seen = conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        try:
            cyc_row  = conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='cycle_end'"
            ).fetchone()
            last_row = conn.execute(
                "SELECT ts FROM events WHERE event_type='cycle_end' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            cycle_count    = cyc_row[0]  if cyc_row  else 0
            last_cycle_ts  = last_row[0] if last_row else None
        except Exception:
            cycle_count, last_cycle_ts = 0, None

        # Calculate checks in current cycle (distinct companies since latest cycle_start)
        try:
            last_start = conn.execute(
                "SELECT ts FROM events WHERE event_type='cycle_start' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last_start and last_start[0]:
                checks_this_cycle = conn.execute(
                    "SELECT COUNT(DISTINCT company) FROM events WHERE event_type IN ('company_ok','company_error','company_skip') AND ts >= ?",
                    (last_start[0],)
                ).fetchone()[0]
            else:
                checks_this_cycle = 0
        except Exception:
            checks_this_cycle = 0

        # Get apply stats
        try:
            from auto_apply.db_updater import get_apply_stats
            apply_stats = get_apply_stats(conn)
        except Exception:
            apply_stats = {"success": 0, "failed": 0, "dry_run": 0, "pending": 0}

        conn.close()
        return jsonify({
            "company_count":   len(COMPANY_NAMES),
            "seen_count":      seen,
            "cycle_count":     cycle_count,
            "last_cycle_ts":   last_cycle_ts,
            "checks_this_cycle": checks_this_cycle,
            "interval_min":    CHECK_INTERVAL_MINUTES,
            "concurrency":     CONCURRENCY,
            "scraper_running": is_scraper_running(),
            "apply_stats":     apply_stats,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/control/start", methods=["POST"])
def api_control_start():
    global SCRAPER_PROCESS
    if is_scraper_running():
        return jsonify({"status": "error", "message": "Scraper is already running", "running": True}), 400
    try:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        SCRAPER_PROCESS = subprocess.Popen(
            [sys.executable, _cw_path],
            cwd=os.path.dirname(_cw_path),
            creationflags=creationflags
        )
        return jsonify({"status": "ok", "message": "Scraper started successfully", "running": True})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "running": False}), 500


@app.route("/api/control/stop", methods=["POST"])
def api_control_stop():
    global SCRAPER_PROCESS, APPLIER_PROCESS
    pids = find_running_scraper_pids()

    if SCRAPER_PROCESS is not None:
        try:
            if os.name == 'nt':
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(SCRAPER_PROCESS.pid)], capture_output=True)
            else:
                SCRAPER_PROCESS.terminate()
        except Exception:
            pass
        SCRAPER_PROCESS = None

    if APPLIER_PROCESS is not None:
        try:
            if os.name == 'nt':
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(APPLIER_PROCESS.pid)], capture_output=True)
            else:
                APPLIER_PROCESS.terminate()
        except Exception:
            pass
        APPLIER_PROCESS = None

    for pid in pids:
        try:
            if os.name == 'nt':
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            else:
                os.kill(pid, 9)
        except Exception:
            pass

    return jsonify({"status": "ok", "message": "Scraper & Auto-Apply processes stopped successfully", "running": False})


@app.route("/api/control/clear_db", methods=["POST"])
def api_control_clear_db():
    try:
        conn = get_db()
        conn.execute("DELETE FROM seen")
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM jobs_detail")
        conn.execute("DELETE FROM job_descriptions")
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "message": "Database and application records cleared successfully"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/control/reset_apply_stats", methods=["POST"])
def api_control_reset_apply_stats():
    try:
        conn = get_db()
        conn.execute("UPDATE job_descriptions SET applied = 0, applied_at = 0, apply_result = ''")
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "message": "Apply stats reset successfully"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


APPLIER_PROCESS = None

@app.route("/api/control/auto_apply", methods=["POST"])
def api_control_auto_apply():
    global APPLIER_PROCESS
    is_headless = request.args.get("headless", "0") == "1"
    is_dry_run = request.args.get("dry_run", "0") == "1"
    headless_flag = "--headless" if is_headless else "--no-headless"
    dry_flag = "--dry-run" if is_dry_run else "--no-dry-run"
    try:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        base_dir = os.path.dirname(__file__)
        script_path = os.path.join(base_dir, "auto_apply", "run_auto_apply.py")
        APPLIER_PROCESS = subprocess.Popen(
            [sys.executable, script_path, dry_flag, "--max-jobs", "1", "--min-score", "60", headless_flag, "--yes"],
            cwd=base_dir,
            creationflags=creationflags
        )
        mode_label = ("DRY RUN" if is_dry_run else "PRODUCTION LIVE SUBMIT") + (" (silent)" if is_headless else " (visible)")
        return jsonify({"status": "ok", "message": f"Auto-Apply engine launched in {mode_label} mode", "running": True})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "running": False}), 500




@app.route("/api/events")
def api_events():
    since = int(request.args.get("since", 0))
    try:
        conn = get_db()
        try:
            # Get latest event ID so initial load can anchor to it
            max_row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
            max_id = max_row[0] if max_row else 0

            start_row = conn.execute(
                "SELECT id FROM events WHERE event_type='cycle_start' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            cycle_start_id = start_row[0] if start_row else 0

            if since == 0:
                # Fresh page load: don't replay old events.
                # Only return cycle_end and new_job events from the last completed cycle
                # so user sees a clean summary, not a wall of old 'checking' messages.
                rows = conn.execute(
                    "SELECT id, ts, event_type, company, message FROM events "
                    "WHERE id >= ? AND event_type IN ('cycle_end','new_job','cycle_start','watcher_start') "
                    "ORDER BY id ASC LIMIT 200",
                    (cycle_start_id,)
                ).fetchall()
            else:
                # Subsequent polls: return all new events since last seen ID
                rows = conn.execute(
                    "SELECT id, ts, event_type, company, message FROM events "
                    "WHERE id > ? ORDER BY id ASC LIMIT 5000",
                    (since,)
                ).fetchall()

            latest = rows[-1]["id"] if rows else max_id
        except Exception:
            rows, latest, cycle_start_id, max_id = [], 0, 0, 0
        conn.close()
        return jsonify({
            "events": [dict(r) for r in rows],
            "latest_id": latest,
            "cycle_start_id": cycle_start_id,
            "max_id": max_id
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/jobs")
def api_jobs():
    try:
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT jd.job_id, COALESCE(NULLIF(jd.url, ''), jd.job_id) as url, jd.title, jd.company, 
                       jd.location, jd.seniority, jd.first_seen,
                       MAX(COALESCE(desc.match_score, 0.0)) as match_score,
                       MAX(COALESCE(desc.passes_filter, 0)) as passes_filter,
                       COALESCE(desc.filter_reason, '') as filter_reason,
                       COALESCE(desc.match_analysis, '') as match_analysis,
                       COALESCE(desc.posted_date, '') as posted_date,
                       COALESCE(desc.jd_text, '') as jd_text,
                       MAX(COALESCE(desc.applied, 0)) as applied,
                       MAX(COALESCE(desc.applied_at, 0.0)) as applied_at,
                       COALESCE(desc.apply_result, '') as apply_result
                FROM jobs_detail jd
                JOIN job_descriptions desc ON (jd.job_id = desc.job_id OR jd.url = desc.job_id)
                WHERE desc.passes_filter = 1
                GROUP BY jd.job_id
                ORDER BY match_score DESC, jd.first_seen DESC
                LIMIT 500
            """).fetchall()
        except Exception:
            rows = conn.execute(
                "SELECT job_id, '' as url, '' as title, '' as company, '' as location, "
                "'' as seniority, 0 as first_seen, 0.0 as match_score, 0 as passes_filter, "
                "'' as filter_reason, '' as match_analysis, '' as posted_date, '' as jd_text FROM seen LIMIT 200"
            ).fetchall()
        conn.close()
        return jsonify({"jobs": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/applications")
def api_applications():
    try:
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT desc.job_id,
                       COALESCE(NULLIF(jd.url, ''), desc.job_id) as url,
                       COALESCE(jd.title, desc.job_id) as title,
                       COALESCE(jd.company, 'Unknown Company') as company,
                       COALESCE(jd.location, 'Remote / India') as location,
                       desc.match_score,
                       COALESCE(desc.applied, 0) as applied,
                       COALESCE(desc.applied_at, 0.0) as applied_at,
                       COALESCE(desc.apply_result, '') as apply_result
                FROM job_descriptions desc
                LEFT JOIN jobs_detail jd ON (desc.job_id = jd.job_id OR desc.job_id = jd.url)
                WHERE desc.passes_filter = 1
                ORDER BY desc.applied_at DESC, desc.match_score DESC
                LIMIT 500
            """).fetchall()
        except Exception:
            rows = []

        try:
            from auto_apply.db_updater import get_apply_stats
            stats = get_apply_stats(conn)
        except Exception:
            stats = {"success": 0, "failed": 0, "dry_run": 0, "pending": 0}

        conn.close()
        return jsonify({"applications": [dict(r) for r in rows], "stats": stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/companies")
def api_companies():
    try:
        conn = get_db()
        try:
            # Latest status per company from the events table
            rows = conn.execute("""
                SELECT company, event_type, message, MAX(ts) AS last_ts
                FROM events
                WHERE event_type IN ('company_ok','company_error','company_skip')
                GROUP BY company
            """).fetchall()

            job_counts = {}
            try:
                for r in conn.execute(
                    "SELECT company, COUNT(*) as n FROM jobs_detail GROUP BY company"
                ).fetchall():
                    job_counts[r["company"]] = r["n"]
            except Exception:
                pass

            seen_names = set()
            companies  = []
            for r in rows:
                seen_names.add(r["company"])
                st = ("ok"    if r["event_type"] == "company_ok"    else
                      "error" if r["event_type"] == "company_error" else "skip")
                companies.append({
                    "name":       r["company"],
                    "status":     st,
                    "jobs_found": job_counts.get(r["company"], 0),
                    "last_ts":    r["last_ts"],
                    "message":    r["message"],
                })

            # Add companies not yet checked
            for name in COMPANY_NAMES:
                if name not in seen_names:
                    companies.append({
                        "name": name, "status": "wait",
                        "jobs_found": 0, "last_ts": None, "message": "",
                    })
        except Exception:
            companies = [
                {"name": n, "status": "wait", "jobs_found": 0, "last_ts": None, "message": ""}
                for n in COMPANY_NAMES
            ]
        conn.close()
        return jsonify({"companies": companies})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    print("=" * 55)
    print("  Job Watcher Dashboard")
    print(f"  DB      : {DB}")
    print(f"  Tracking: {len(COMPANY_NAMES)} companies")
    print(f"  Open    : http://localhost:5000")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
