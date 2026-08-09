"""
auto_apply/config.py — Candidate Profile & Auto-Apply Configuration

Edit the CANDIDATE dict below with your personal details before running.
Set dry_run = False only when you are ready to actually submit applications.
"""

import os
from dotenv import load_dotenv

# Load .env from the project root (same folder as career_watcher.py)
_env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(_env_path)

# ─── CANDIDATE PERSONAL DETAILS ───────────────────────────────────────────────
# Fill in your correct personal details here before running.

CANDIDATE = {
    # ── Identity
    "full_name": "Yash Prakash",
    "first_name": "Yash",
    "last_name": "Prakash",
    "email": "Yashpra222@gmail.com",
    "phone": "+91-7007385969",

    # ── Location
    "city": "Noida",
    "state": "Uttar Pradesh",
    "country": "India",
    "postal_code": "201301",

    # ── Experience
    "years_experience": "1",         # Software Engineer @ Samsung Research India (Feb 2025 – Present)
    "current_company": "Samsung Research India",
    "current_title": "Software Engineer — AI Systems & Agentic AI",
    "highest_degree": "Bachelor of Technology",
    "field_of_study": "Computer Science & Engineering",
    "university": "DIT University, Dehradun",
    "graduation_year": "2024",

    # ── Online Profiles
    "linkedin": "https://linkedin.com/in/yash0prakash",
    "github": "",                    # GitHub link was not a full URL in resume — leave blank or fill manually
    "portfolio": "",

    # ── Resume PDF
    "resume_pdf_path": r"C:\Users\yashp\Downloads\Resume.pdf",

    # ── Application Preferences
    "willing_to_relocate": True,
    "visa_sponsorship_needed": False,
    "us_authorized": False,
    "authorized_to_work": True,

    # ── Screening question answers (sourced directly from resume)
    "why_this_company": (
        "I am a Software Engineer at Samsung Research India specializing in Agentic AI, "
        "LLMs, and RAG systems. I am passionate about building production-grade autonomous "
        "AI agents and believe this role aligns perfectly with my expertise in multi-agent "
        "architectures, LLM-driven automation, and scalable backend AI services."
    ),
    "greatest_strength": (
        "My deepest strength is building end-to-end production AI systems — from designing "
        "planner-executor agent architectures and RAG pipelines to integrating VLMs for "
        "UI understanding. I have delivered these at Samsung Research India and been "
        "recognized with a Quarterly Employee Award and shortlisted for Samsung Best "
        "Research Paper 2026 for FLEX-MAS."
    ),
    "summary": (
        "Software Engineer at Samsung Research India specializing in Agentic AI, LLMs, "
        "RAG, and Android Automation. Experienced in building production AI systems using "
        "multi-agent architectures, VLMs, backend services, and enterprise AI workflows."
    ),
    "salary_expectation": "",
}

# ─── AUTO-APPLY ENGINE SETTINGS ───────────────────────────────────────────────

# Minimum LLM match score (0–100) to consider a job for auto-apply
APPLY_SCORE_THRESHOLD = float(os.environ.get("APPLY_SCORE_THRESHOLD", "60.0"))

# Maximum number of job applications to attempt per run
MAX_JOBS_PER_RUN = int(os.environ.get("MAX_JOBS_PER_RUN", "5"))

# Production Mode: DRY_RUN=False actually submits applications live.
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() in ("true", "1")

# Browser visibility (headless=True runs in background silently, headless=False opens visible browser on screen)
HEADLESS = os.environ.get("HEADLESS", "false").lower() in ("true", "1")

# ─── DATABASE & PATH CONFIG ───────────────────────────────────────────────────

# Path to the JobSense SQLite database (relative to project root)
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seen_jobs.db")

# resume.txt (skills/profile YAML — used as LLM context for screening questions)
RESUME_TXT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resume.txt")

# ─── LLM PROVIDER & MODEL CONFIG ─────────────────────────────────────────────
# Options: "nvidia" or "ollama"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")

# NVIDIA API Settings
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL    = os.environ.get("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct")
NVIDIA_API_KEY  = os.environ.get("NVIDIA_API_KEY", "")

# Ollama Local Settings
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud")

# ─── SUPPORTED PORTAL TYPES ───────────────────────────────────────────────────
# Maps URL pattern -> portal name (used in task prompt construction)

PORTAL_PATTERNS = {
    "greenhouse.io":           "Greenhouse",
    "boards.greenhouse.io":    "Greenhouse",
    "job-boards.greenhouse.io":"Greenhouse",
    "lever.co":                "Lever",
    "jobs.ashbyhq.com":        "Ashby",
    "app.ashbyhq.com":         "Ashby",
    "myworkdayjobs.com":       "Workday",
    "wd1.myworkdayjobs.com":   "Workday",
    "wd5.myworkdayjobs.com":   "Workday",
    "oraclecloud.com":         "Oracle HCM",
}

def detect_portal(url: str) -> str:
    """Detect job portal type from URL."""
    url_lower = url.lower()
    for pattern, portal in PORTAL_PATTERNS.items():
        if pattern in url_lower:
            return portal
    return "Unknown"
