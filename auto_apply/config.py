"""JobSense auto-apply configuration.

Sensitive candidate data is loaded from environment variables and must not be
committed to source control.
"""
import os
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


CANDIDATE = {
    "full_name": _env("CANDIDATE_FULL_NAME"),
    "first_name": _env("CANDIDATE_FIRST_NAME"),
    "last_name": _env("CANDIDATE_LAST_NAME"),
    "email": _env("CANDIDATE_EMAIL"),
    "phone": _env("CANDIDATE_PHONE"),
    "city": _env("CANDIDATE_CITY", "Noida"),
    "state": _env("CANDIDATE_STATE", "Uttar Pradesh"),
    "country": _env("CANDIDATE_COUNTRY", "India"),
    "postal_code": _env("CANDIDATE_POSTAL_CODE"),
    "years_experience": _env("CANDIDATE_YEARS_EXPERIENCE", "0"),
    "current_company": _env("CANDIDATE_CURRENT_COMPANY"),
    "current_title": _env("CANDIDATE_CURRENT_TITLE"),
    "highest_degree": _env("CANDIDATE_HIGHEST_DEGREE", "Bachelor of Technology"),
    "field_of_study": _env("CANDIDATE_FIELD_OF_STUDY", "Computer Science & Engineering"),
    "university": _env("CANDIDATE_UNIVERSITY"),
    "graduation_year": _env("CANDIDATE_GRADUATION_YEAR"),
    "linkedin": _env("CANDIDATE_LINKEDIN"),
    "github": _env("CANDIDATE_GITHUB"),
    "portfolio": _env("CANDIDATE_PORTFOLIO"),
    "resume_pdf_path": _env("RESUME_PDF_PATH"),
    "willing_to_relocate": _env_bool("CANDIDATE_WILLING_TO_RELOCATE", True),
    "visa_sponsorship_needed": _env_bool("CANDIDATE_VISA_SPONSORSHIP_NEEDED", False),
    "us_authorized": _env_bool("CANDIDATE_US_AUTHORIZED", False),
    "authorized_to_work": _env_bool("CANDIDATE_AUTHORIZED_TO_WORK", True),
    "why_this_company": _env("CANDIDATE_WHY_THIS_COMPANY"),
    "greatest_strength": _env("CANDIDATE_GREATEST_STRENGTH"),
    "summary": _env("CANDIDATE_SUMMARY"),
    "salary_expectation": _env("CANDIDATE_SALARY_EXPECTATION"),
}

APPLY_SCORE_THRESHOLD = float(_env("APPLY_SCORE_THRESHOLD", "60.0"))
MAX_JOBS_PER_RUN = int(_env("MAX_JOBS_PER_RUN", "5"))
DRY_RUN = _env_bool("DRY_RUN", True)
HEADLESS = _env_bool("HEADLESS", False)
LIVE_APPLY_ENABLED = _env_bool("JOBSENSE_LIVE_APPLY", False)

DB_PATH = os.path.join(PROJECT_ROOT, "seen_jobs.db")
RESUME_TXT_PATH = os.path.join(PROJECT_ROOT, "resume.txt")

LLM_PROVIDER = _env("LLM_PROVIDER", "ollama")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = _env("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct")
NVIDIA_API_KEY = _env("NVIDIA_API_KEY")
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "gpt-oss:120b-cloud")

PORTAL_PATTERNS = {
    "greenhouse.io": "Greenhouse",
    "boards.greenhouse.io": "Greenhouse",
    "job-boards.greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "jobs.ashbyhq.com": "Ashby",
    "app.ashbyhq.com": "Ashby",
    "myworkdayjobs.com": "Workday",
    "wd1.myworkdayjobs.com": "Workday",
    "wd5.myworkdayjobs.com": "Workday",
    "oraclecloud.com": "Oracle HCM",
}


def detect_portal(url: str) -> str:
    url_lower = (url or "").lower()
    for pattern, portal in PORTAL_PATTERNS.items():
        if pattern in url_lower:
            return portal
    return "Unknown"


def validate_candidate_profile(require_resume: bool = False) -> None:
    required = ["full_name", "first_name", "last_name", "email", "phone"]
    missing = [key for key in required if not CANDIDATE.get(key)]
    if require_resume and not CANDIDATE.get("resume_pdf_path"):
        missing.append("resume_pdf_path")
    if missing:
        raise RuntimeError("Missing candidate configuration: " + ", ".join(missing) + ". Put these values in .env.")


def assert_live_apply_enabled() -> None:
    if not LIVE_APPLY_ENABLED:
        raise RuntimeError("Live auto-apply is disabled. Set JOBSENSE_LIVE_APPLY=true in private .env after review.")
