"""JobSense evaluation service.

The evaluator is deliberately split into deterministic eligibility checks and
an evidence-based LLM assessment. Location decisions are made from structured
location fields rather than matching arbitrary two-letter state abbreviations
inside the entire job description.
"""

import json
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "nvidia/nvidia-nemotron-nano-9b-v2")

EXCLUDE_TITLE_PATTERNS = re.compile(
    r"\b(SENIOR|SR\.?|STAFF|PRINCIPAL|LEAD|DISTINGUISHED|MANAGER|DIRECTOR|HEAD|VP|"
    r"ARCHITECT|PRODUCT MANAGER|SALES|MARKETING|HR|RECRUITER|INTERN|INTERNSHIP|"
    r"CO-OP|COOP|TRAINEE|APPRENTICE|DATA ENGINEER|DEVSECOPS|INTEGRATION ENGINEER|"
    r"SECDEVOPS|SYSTEM ADMINISTRATOR|DATABASE ADMINISTRATOR)\b", re.I)

LEVEL_EXCLUDE_PATTERNS = re.compile(
    r"\b(?:engineer|developer|sde|swe|mle|mts|software\s+engineer|machine\s+learning\s+engineer)"
    r"\s*[-–—:]?\s*(?:[3-9]|[1-9][0-9]|III|IV|V|VI|VII|VIII|IX|X)\b", re.I)

NON_INDIA_LOCATION_PATTERNS = re.compile(
    r"\b(?:united states|usa|u\.s\.?|us-only|remote\s+(?:in|from)\s+us|"
    r"united kingdom|uk-only|europe|emea|germany|canada|australia|japan|"
    r"london|tokyo|toronto|vancouver|new york|san francisco|seattle|boston|"
    r"chicago|austin|atlanta)\b", re.I)

INDIA_LOCATION_PATTERNS = re.compile(
    r"\b(?:india|bangalore|bengaluru|hyderabad|pune|chennai|delhi|gurugram|"
    r"gurgaon|noida|mumbai|kolkata|remote\s*[- ]?india|india\s+remote|global\s+remote|worldwide)\b", re.I)

TIMEZONE_EXCLUDE_PATTERNS = re.compile(
    r"\b(?:est|pst|cst|mst|cet|europe/london|europe/berlin|us/eastern|us/pacific|"
    r"us/central|us/mountain)\b", re.I)

YOE_REQUIRED_PATTERNS = [
    re.compile(r"\b(?:minimum|at\s+least|required|requires?)\s+(?:of\s+)?([3-9]|[1-9][0-9])\s*\+?\s*(?:years?|yrs?)", re.I),
    re.compile(r"\b([3-9]|[1-9][0-9])\s*\+\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|industry|professional|relevant)", re.I),
    re.compile(r"\b([3-9]|[1-9][0-9])\s*(?:to|[-–—])\s*[0-9]+\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|industry|professional)", re.I),
]

DEGREE_REQUIRED = re.compile(
    r"\b(?:master'?s?|m\.s\.?|m\.tech|ph\.?d\.?|doctorate)\b.*\b(?:required|mandatory|must\s+have|minimum)\b",
    re.I | re.S)

LANGUAGES = ["python", "java", "c++", "c", "javascript", "typescript", "go", "kotlin", "sql", "bash"]
AI_ML_SKILLS = [
    "machine learning", "deep learning", "generative ai", "genai", "large language models", "llm",
    "ai agents", "prompt engineering", "rag", "retrieval augmented generation", "vector databases",
    "embeddings", "fine tuning", "langchain", "langgraph", "llamaindex", "crewai", "autogen",
    "pytorch", "tensorflow", "scikit-learn", "xgboost", "pandas", "numpy", "opencv", "hugging face",
    "vllm", "fastapi", "mcp", "function calling", "multi-agent systems"
]
BACKEND_CLOUD_SKILLS = [
    "rest", "graphql", "grpc", "microservices", "distributed systems", "docker", "kubernetes",
    "aws", "azure", "gcp", "ec2", "s3", "lambda", "postgresql", "mysql", "mongodb", "redis",
    "elasticsearch", "sqlite", "git", "ci/cd", "linux", "system design", "multithreading"
]

SKILL_GROUPS = {
    "languages": LANGUAGES,
    "ai_ml": AI_ML_SKILLS,
    "backend_cloud": BACKEND_CLOUD_SKILLS,
}


def _contains_any(text: str, terms) -> bool:
    lowered = (text or "").lower()
    return any(term.lower() in lowered for term in terms)


def _required_text(jd_text: str) -> str:
    """Approximate required-vs-preferred sections for deterministic scoring."""
    text = jd_text or ""
    preferred = re.split(r"\b(?:preferred|nice\s+to\s+have|bonus|plus)\b", text, maxsplit=1, flags=re.I)[0]
    required = re.split(r"\b(?:requirements?|qualifications?|what\s+you(?:'|’)ll\s+need|must\s+have)\b", text, maxsplit=1, flags=re.I)
    return required[-1] if len(required) > 1 else preferred


def _preferred_text(jd_text: str) -> str:
    match = re.search(r"\b(?:preferred|nice\s+to\s+have|bonus|plus)\b(.*)", jd_text or "", re.I | re.S)
    return match.group(1) if match else ""


def prefilter_job(title, location, jd_text):
    """Return deterministic eligibility without scanning arbitrary JD tokens as locations."""
    title = title or ""
    location = location or ""
    jd_text = jd_text or ""

    match = EXCLUDE_TITLE_PATTERNS.search(title)
    if match:
        return True, 0, f"Filtered Out: Title excluded ({match.group(0)})", None
    match = LEVEL_EXCLUDE_PATTERNS.search(title)
    if match:
        return True, 0, f"Filtered Out: Senior level designation ({match.group(0)})", None

    # Location is authoritative. JD text is only inspected for explicit remote/timezone restrictions.
    location_is_india = bool(INDIA_LOCATION_PATTERNS.search(location))
    if NON_INDIA_LOCATION_PATTERNS.search(location) and not location_is_india:
        found = NON_INDIA_LOCATION_PATTERNS.search(location).group(0)
        return True, 0, f"Filtered Out: Non-India location ({found})", None
    if TIMEZONE_EXCLUDE_PATTERNS.search(location) and not location_is_india:
        found = TIMEZONE_EXCLUDE_PATTERNS.search(location).group(0)
        return True, 0, f"Filtered Out: Non-India timezone ({found})", None

    # Explicit job-level remote restriction is considered, but normal mentions of US/CA/OR/etc. are ignored.
    remote_restriction = re.search(r"\b(?:remote|work\s+from)\b.{0,80}\b(?:us|usa|united states|europe|uk|canada)\b", jd_text, re.I | re.S)
    if remote_restriction and not location_is_india:
        return True, 0, "Filtered Out: Remote work restricted outside India", None

    required_text = _required_text(jd_text)
    for pattern in YOE_REQUIRED_PATTERNS:
        match = pattern.search(required_text)
        if match:
            value = int(match.group(1))
            if value >= 3:
                return True, 0, f"Filtered Out: Requires {value}+ years experience", value

    # Bachelor + Master/PhD alternatives are allowed; mandatory graduate degrees are not.
    if DEGREE_REQUIRED.search(required_text) and not re.search(r"\b(?:bachelor'?s?|b\.tech|b\.e\.?|b\.s\.?)\b.*\b(?:or|/|and)\b", required_text, re.I | re.S):
        return True, 0, "Filtered Out: Mandatory Master's/PhD requirement", None

    return False, 1, "", None


def calculate_candidate_skill_score(title, location, jd_text):
    """Weighted deterministic score with explicit required/preferred skill evidence."""
    title_l = (title or "").lower()
    loc_l = (location or "").lower()
    jd = jd_text or ""
    required = _required_text(jd).lower()
    preferred = _preferred_text(jd).lower()
    full = f"{title_l}\n{loc_l}\n{jd.lower()}"

    role = 0.0
    if _contains_any(title_l, ["ai engineer", "genai", "llm", "applied ai", "machine learning"]):
        role = 100.0
    elif _contains_any(title_l, ["software engineer", "sde", "backend", "full stack", "developer"]):
        role = 85.0
    elif _contains_any(title_l, ["sdet", "qa", "automation"]):
        role = 65.0
    else:
        role = 45.0

    evidence = []
    weighted_groups = []
    for group_name, skills in SKILL_GROUPS.items():
        required_hits = [s for s in skills if s.lower() in required]
        preferred_hits = [s for s in skills if s.lower() in preferred]
        all_hits = [s for s in skills if s.lower() in full]
        group_score = min(100.0, len(required_hits) * 25.0 + len(preferred_hits) * 10.0 + len(set(all_hits) - set(required_hits) - set(preferred_hits)) * 5.0)
        weighted_groups.append(group_score)
        if required_hits:
            evidence.append(f"{group_name}: required={', '.join(required_hits[:5])}")
        elif all_hits:
            evidence.append(f"{group_name}: matched={', '.join(all_hits[:5])}")

    skill_score = sum(weighted_groups) / len(weighted_groups) if weighted_groups else 0.0
    location_score = 100.0 if _contains_any(loc_l, ["india", "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "delhi", "gurugram", "noida", "mumbai", "remote"]) else 35.0

    final = round((0.35 * role) + (0.50 * skill_score) + (0.15 * location_score), 1)
    final = max(0.0, min(100.0, final))
    return final, f"Deterministic score {final:.1f}% | " + ("; ".join(evidence) if evidence else "No strong skill evidence")


def get_nvidia_client():
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        raise ValueError("NVIDIA_API_KEY environment variable is not set")
    return OpenAI(base_url=NVIDIA_BASE_URL, api_key=api_key)


def load_resume():
    resume_file = os.path.join(os.path.dirname(__file__), "resume.txt")
    if os.path.exists(resume_file):
        try:
            with open(resume_file, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            pass
    return "Role: Software Engineer / SDE / AI Engineer (0-3 YOE)\nSkills: Python, Java, AI/ML, RAG, LLMs, FastAPI, Docker, AWS, Azure, Databases, Distributed Systems"


PROMPT_TEMPLATE = """
You are an evidence-based technical recruiter.

Candidate resume:
{resume_text}

Job:
Company: {company}
Title: {title}
Location: {location}
Description:
{jd_snippet}

The deterministic eligibility layer has already checked role level, location,
experience and degree constraints. Do NOT invent disqualifications.
Evaluate actual resume-to-JD evidence. Separate required skills from preferred
skills. Penalize missing REQUIRED skills more heavily than missing preferred skills.
Do not award points just because a technology appears in the JD.

Return ONLY JSON:
{{
  "passes_filter": true,
  "match_score": 0,
  "match_analysis": "brief evidence-based explanation",
  "required_skill_evidence": ["skill -> candidate evidence"],
  "missing_required_skills": ["skill"],
  "preferred_skill_evidence": ["skill -> candidate evidence"]
}}
"""


def parse_json_from_llm(text):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def evaluate_single_job(client, job_dict, resume_text):
    job_id = job_dict["job_id"]
    company = job_dict.get("company", "Unknown")
    title = job_dict.get("title", "Untitled")
    location = job_dict.get("location", "")
    jd_text = job_dict.get("jd_text", "") or ""

    is_filtered, passes_int, reason, yoe_val = prefilter_job(title, location, jd_text)
    if is_filtered:
        return job_id, passes_int, reason, 0.0, f"Filtered by rule: {reason}"

    skill_score, skill_analysis = calculate_candidate_skill_score(title, location, jd_text)
    prompt = PROMPT_TEMPLATE.format(
        resume_text=resume_text[:4000], company=company, title=title,
        location=location, jd_snippet=jd_text[:5000])

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=800,
            )
            data = parse_json_from_llm(response.choices[0].message.content)
            if data:
                if not data.get("passes_filter", True):
                    return job_id, 0, str(data.get("match_analysis", "Filtered by LLM")), 0.0, "Filtered out by evidence-based LLM evaluation"
                llm_score = float(data.get("match_score", skill_score))
                final_score = round((0.60 * skill_score) + (0.40 * max(0.0, min(100.0, llm_score))), 1)
                analysis = str(data.get("match_analysis", ""))[:300]
                required = data.get("required_skill_evidence", [])
                missing = data.get("missing_required_skills", [])
                evidence = "; ".join(map(str, required[:3]))
                missing_text = ", ".join(map(str, missing[:5]))
                if evidence:
                    analysis += f" | Evidence: {evidence}"
                if missing_text:
                    analysis += f" | Missing required: {missing_text}"
                return job_id, 1, "Passes eligibility", final_score, analysis[:700]
            break
        except Exception as exc:
            if ("429" in str(exc) or "Too Many Requests" in str(exc)) and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
            else:
                break

    # Availability fallback: preserve deterministic score, but make the method visible in analysis.
    return job_id, 1, "Passes eligibility; LLM fallback", skill_score, skill_analysis + " | evaluation_method=deterministic_fallback"


def evaluate_pending_jobs(db_path="seen_jobs.db", max_jobs=400, concurrency=2, force_reevaluate=True):
    from career_watcher import init_db
    init_db()
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row

    where = "" if force_reevaluate else "WHERE COALESCE(desc.evaluated_at, 0) = 0"
    query = f"""
        SELECT desc.job_id, jd.company, jd.title, jd.location, desc.jd_text
        FROM job_descriptions desc
        JOIN jobs_detail jd ON desc.job_id = jd.job_id
        {where}
        ORDER BY COALESCE(desc.evaluated_at, 0) ASC
        LIMIT ?
    """
    pending = conn.execute(query, (max_jobs,)).fetchall()
    if not pending:
        conn.close()
        print("No jobs found to evaluate.")
        return 0

    resume_text = load_resume()
    client = get_nvidia_client()
    print(f"Evaluating {len(pending)} job(s) with eligibility + weighted evidence scoring + LLM...")

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(lambda row: evaluate_single_job(client, dict(row), resume_text), pending))

    conn.executemany(
        """UPDATE job_descriptions SET passes_filter=?, filter_reason=?, match_score=?, match_analysis=?, evaluated_at=? WHERE job_id=?""",
        [(p, r, s, a, time.time(), job_id) for job_id, p, r, s, a in results],
    )
    conn.commit()
    conn.close()
    print(f"[SUCCESS] Evaluated {len(results)} job(s).")
    return len(results)


if __name__ == "__main__":
    evaluate_pending_jobs(force_reevaluate=True)
