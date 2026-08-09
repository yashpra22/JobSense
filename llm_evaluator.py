"""
llm_evaluator.py — LLM-Powered Edge Case Filtering & Resume Match Scoring
Full-text YOE, Location/Timezone (EST/Europe/US), and Resume Skill Match Evaluator.
"""

import os
import re
import json
import time
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = "nvidia/nvidia-nemotron-nano-9b-v2"

# ── EXCLUSION PATTERNS BASED ON USER RESUME ────────────────────────────────
EXCLUDE_TITLE_PATTERNS = re.compile(
    r"\b(SENIOR|SR|SR\.|STAFF|PRINCIPAL|LEAD|DISTINGUISHED|MANAGER|DIRECTOR|HEAD|VP|ARCHITECT|PRODUCT MANAGER|"
    r"SALES|MARKETING|HR|RECRUITER|INTERN|INTERNSHIP|CO-OP|COOP|TRAINEE|APPRENTICE|"
    r"SAP CONSULTANT|TECHNICAL SUPPORT|CUSTOMER SUPPORT|"
    r"DATA ENGINEER|DEVSECOPS|AZURE INTEGRATION|INTEGRATION ENGINEER|SECDEVOPS|"
    r"SOLUTIONS ARCHITECT|SOLUTION ARCHITECT|SYSTEM ADMINISTRATOR|DATABASE ADMINISTRATOR)\b",
    re.IGNORECASE
)

LEVEL_EXCLUDE_PATTERNS = re.compile(
    r"\b(?:sde|swe|mle|pe|se|mts|software\s+engineer|machine\s+learning\s+engineer|backend\s+engineer|"
    r"data\s+engineer|qa\s+engineer|cloud\s+engineer|devops\s+engineer|systems\s+engineer|"
    r"platform\s+engineer|engineer|developer)\s*[-–—:]?\s*([3-9]|[1-9][0-9])\b|"
    r"\b(?:engineer|developer|sde|swe|mle|mts)\s+[-–—:]?\s*(III|IV|V|VI|VII|VIII|IX|X)\b",
    re.IGNORECASE
)

# NON-INDIA LOCATIONS & TIMEZONES (EST, Europe, EMEA, SOCEUR, US, UK, etc.)
US_NON_INDIA_PATTERNS = re.compile(
    r"\b(EST|PST|CST|MST|CET|GMT|EUROPE|EU|EMEA|SOCEUR|LATAM|APAC|AMER|NORTH AMERICA|US EAST|US WEST|SOUTH AMERICA|"
    r"ATLANTA|GA|GEORGIA|SAN FRANCISCO|CA|CALIFORNIA|AUSTIN|TX|TEXAS|NEW YORK|NY|"
    r"SEATTLE|WA|WASHINGTON|BOSTON|MA|MASSACHUSETTS|CHICAGO|IL|ILLINOIS|DENVER|CO|COLORADO|"
    r"RALEIGH|NC|VIRGINIA|VA|OREGON|OR|PENNSYLVANIA|PA|MICHIGAN|MI|NEW JERSEY|NJ|OHIO|OH|FLORIDA|FL|"
    r"UNITED STATES|USA|US|U\.S\.|REMOTE IN US|REMOTE - US|REMOTE US|US-ONLY|US ONLY|"
    r"LONDON|UK|UNITED KINGDOM|GERMANY|BERLIN|HAMBURG|JAPAN|TOKYO|CANADA|TORONTO|VANCOUVER|AUSTRALIA)\b",
    re.IGNORECASE
)

INDIA_LOCATION_PATTERNS = re.compile(
    r"\b(INDIA|BANGALORE|BENGALURU|HYDERABAD|PUNE|CHENNAI|DELHI|GURUGRAM|GURGAON|NOIDA|MUMBAI|KOLKATA|REMOTE - INDIA|INDIA REMOTE|GLOBAL REMOTE|WORLDWIDE)\b",
    re.IGNORECASE
)

# ── STRICT FULL-TEXT YEARS OF EXPERIENCE (YOE >= 3) REGEXES ────────────────
YOE_PATTERNS = [
    re.compile(r"\b([3-9]|[1-9][0-9])\s*\+\s*(?:years?|yrs?|years?\s+of)\b", re.I),
    re.compile(r"\b([3-9]|[1-9][0-9])\s*(?:to|[-–—‐‑])\s*[0-9]+\s*(?:years?|yrs?)\b", re.I),
    re.compile(r"\b(?:minimum|at\s*least)\s+(?:of\s+)?([3-9]|[1-9][0-9])\s*(?:\+\s*)?(?:years?|yrs?)\b", re.I),
    re.compile(r"\b([3-9]|[1-9][0-9])\s*(?:\+\s*|[-–—‐‑]\s*)?years?\s+(?:of\s+)?(?:production|hands-on|industry|experience|work|relevant|building|delivering|maintaining|working|in)\b", re.I),
    re.compile(r"\b([3-9]|[1-9][0-9])\s*(?:[-–—‐‑]|to)\s*[0-9]+\s+years?\s+of\s+experience\b", re.I),
    re.compile(r"\((?:[3-9]|[1-9][0-9])\s*\+?\s*(?:years?|yrs?)\)", re.I),
]

# Candidate Skill Keywords parsed from resume.txt
LANGUAGES = ["python", "java", "c++", "c", "javascript", "typescript", "go", "kotlin", "sql", "bash"]
AI_ML_SKILLS = [
    "machine learning", "deep learning", "generative ai", "genai", "large language models", "llm",
    "ai agents", "prompt engineering", "rag", "retrieval augmented generation", "vector databases",
    "embeddings", "fine tuning", "langchain", "langgraph", "llamaindex", "crewai", "autogen",
    "pytorch", "tensorflow", "scikit-learn", "xgboost", "pandas", "numpy", "opencv",
    "openai", "anthropic", "gemini", "hugging face", "vllm", "fastapi"
]
BACKEND_CLOUD_SKILLS = [
    "rest", "graphql", "grpc", "microservices", "distributed systems", "docker", "kubernetes",
    "aws", "azure", "gcp", "ec2", "s3", "lambda", "postgresql", "mysql", "mongodb", "redis",
    "elasticsearch", "sqlite", "git", "ci/cd", "linux", "system design", "multithreading"
]

DEGREE_DISQUALIFY_PATTERNS = [
    re.compile(r"\b(?:ph\.?d\.?|doctorate)\s*(?:or|/|&|\+)\s*(?:master'?s?|\bms\b|m\.s\.|m\.tech)\b", re.I),
    re.compile(r"\b(?:master'?s?|\bms\b|m\.s\.|m\.tech)\s*(?:or|/|&|\+)\s*(?:ph\.?d\.?|doctorate)\b", re.I),
    re.compile(r"\b(?:ph\.?d\.?|doctorate)\s+(?:or|and)\s+(?:master'?s?|\bms\b|m\.s\.|m\.tech)\s+degree\b", re.I),
    re.compile(r"\b(?:ph\.?d\.?|doctorate)\s+(?:degree\s+)?(?:is\s+)?(?:required|mandatory|must\s+have)\b", re.I),
    re.compile(r"\b(?:must\s+have|requires?|minimum\s+of)\s+(?:a\s+)?(?:ph\.?d\.?|doctorate)\b", re.I),
    re.compile(r"\b(?:master'?s?|m\.s\.|m\.tech)\s+(?:degree\s+)?(?:is\s+)?(?:required|mandatory)\b", re.I),
    re.compile(r"\b(?:must\s+have|requires?|minimum\s+of)\s+(?:a\s+)?(?:master'?s?|m\.s\.|m\.tech)\s+(?:degree|in)\b", re.I),
]

def prefilter_job(title, location, jd_text):
    """
    Deterministic full-text pre-filter based on user resume exclusions:
    - Excludes Senior/Staff/Principal/Manager/Intern/Data Engineer/DevSecOps roles.
    - Excludes US/Non-India locations and Timezones (EST, Europe, EMEA, SOCEUR, US).
    - Excludes roles requiring YOE >= 3 years across the ENTIRE job description or title.
    - Excludes roles requiring Mandatory Master's / PhD degrees.
    Returns (is_filtered, passes_filter_int, reason, matched_yoe_val)
    """
    full_text = f"{title}\n{location}\n{jd_text}"

    # 1. Check Exclusion Titles / Internships / Level 3, 4, 5+ Senior Roles
    m_title = EXCLUDE_TITLE_PATTERNS.search(title)
    if m_title:
        return True, 0, f"Filtered Out: Title excluded ({m_title.group(0)})", None
    m_level = LEVEL_EXCLUDE_PATTERNS.search(title)
    if m_level:
        return True, 0, f"Filtered Out: Senior Level Designation ({m_level.group(0)})", None

    # 2. Check US / Non-India Location & Timezones (EST, Europe, EMEA, SOCEUR, US)
    loc_text = f"{location}\n{jd_text}"
    m_loc = US_NON_INDIA_PATTERNS.search(loc_text)
    if m_loc and not INDIA_LOCATION_PATTERNS.search(location):
        found_loc = m_loc.group(0)
        return True, 0, f"Filtered Out: Location / Timezone outside India ({found_loc})", None

    # 3. Check Years of Experience (YOE >= 3) across FULL JD text & title
    for pat in YOE_PATTERNS:
        for m in pat.finditer(full_text):
            try:
                val = int(m.group(1))
            except Exception:
                val = 3
            if val >= 3:
                return True, 0, f"Filtered Out: Requires {val}+ years experience ({m.group(0)})", val

    # 4. Check Mandatory Master's / PhD Degree Requirements
    # Ignore if Bachelor's degree is explicitly allowed as an option
    bachelor_allowed = (
        re.search(r"\b(?:bachelor'?s?|\bbs\b|b\.s\.|b\.tech|b\.e\.)\s*,?\s*(?:master|\bms\b|ph\.?d)", full_text, re.I) or
        re.search(r"\b(?:bachelor'?s?|\bbs\b|b\.s\.|b\.tech|b\.e\.)\s*(?:or|/)\s*(?:master|\bms\b|ph\.?d)", full_text, re.I) or
        re.search(r"\b(?:master'?s?|\bms\b|ph\.?d)\s*preferred\b", full_text, re.I)
    )
    if not bachelor_allowed:
        for pat in DEGREE_DISQUALIFY_PATTERNS:
            m = pat.search(full_text)
            if m:
                return True, 0, f"Filtered Out: Requires Mandatory Master's/PhD ({m.group(0)})", None

    return False, 1, "", None

def calculate_candidate_skill_score(title, location, jd_text):
    """
    Calculate a granular resume match percentage (45.0% - 98.0%)
    based on resume.txt candidate skills, role alignment, and technology overlap.
    """
    text = f"{title}\n{location}\n{jd_text}".lower()
    score = 35.0  # base score

    matched_details = []

    # Title Role Alignment
    if any(k in title.lower() for k in ["ai engineer", "genai", "llm", "applied ai", "machine learning"]):
        score += 25.0
        matched_details.append("AI/ML Role Title (+25%)")
    elif any(k in title.lower() for k in ["software engineer", "sde", "backend", "full stack", "developer"]):
        score += 20.0
        matched_details.append("SDE/Backend Role Title (+20%)")
    elif any(k in title.lower() for k in ["platform", "systems", "infrastructure", "cloud", "devops"]):
        score += 18.0
        matched_details.append("Systems/Platform Title (+18%)")
    elif any(k in title.lower() for k in ["sdet", "qa", "automation"]):
        score += 12.0
        matched_details.append("SDET/QA Title (+12%)")

    # Programming Languages Match
    lang_count = sum(1 for l in LANGUAGES if re.search(r"\b" + re.escape(l) + r"\b", text))
    lang_points = min(18.0, lang_count * 3.5)
    score += lang_points
    if lang_count:
        matched_details.append(f"{lang_count} Language(s) (+{lang_points:.1f}%)")

    # AI/ML Skills Match
    ai_count = sum(1 for a in AI_ML_SKILLS if a in text)
    ai_points = min(22.0, ai_count * 4.5)
    score += ai_points
    if ai_count:
        matched_details.append(f"{ai_count} AI/ML Skill(s) (+{ai_points:.1f}%)")

    # Backend / Cloud / Infrastructure Match
    be_count = sum(1 for b in BACKEND_CLOUD_SKILLS if b in text)
    be_points = min(18.0, be_count * 3.0)
    score += be_points
    if be_count:
        matched_details.append(f"{be_count} Cloud/Backend Skill(s) (+{be_points:.1f}%)")

    # Target Location Match
    if any(i in location.lower() for i in ["india", "bangalore", "bengaluru", "hyderabad", "pune", "delhi", "gurugram", "noida", "mumbai", "remote"]):
        score += 5.0

    final_score = round(min(98.0, max(45.0, score)), 1)
    analysis_text = f"Match Score {final_score:.1f}% │ " + ", ".join(matched_details)
    return final_score, analysis_text

def get_nvidia_client():
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        raise ValueError("NVIDIA_API_KEY environment variable is not set. Please add it to your .env file.")
    return OpenAI(base_url=NVIDIA_BASE_URL, api_key=api_key)

def load_resume():
    """Load candidate resume text from resume.txt."""
    resume_file = os.path.join(os.path.dirname(__file__), "resume.txt")
    if os.path.exists(resume_file):
        try:
            with open(resume_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass

    return """
    Role: Software Engineer / SDE / AI Engineer (0-3 YOE)
    Target Locations: India (Bangalore, Hyderabad, Pune, Chennai, Delhi NCR, Noida, Gurgaon, Mumbai, Remote India, Global Remote)
    Skills: Python, Java, C++, C, JavaScript, TypeScript, Go, AI/ML (LangChain, GenAI, RAG, PyTorch, LLMs), FastAPI, React, Docker, AWS, Azure, Databases, Distributed Systems
    """

PROMPT_TEMPLATE = """
You are an expert AI technical recruiter screening jobs for a candidate matching this resume profile (0-3 YOE):

### Candidate Profile & Skills:
{resume_text}

### MANDATORY DISQUALIFICATION RULES (passes_filter = false):
1. YOE OVER 3 YEARS: Does the job require 3+, 4+, 5+, 6+, 8+, 10+, or 12+ years of experience anywhere in title or text (e.g. "(5+ Years)", "3+ years on SaaS", "5+ years in production")? If YES, DISQUALIFY (passes_filter = false).
2. NON-INDIA / US TIMEZONES / NON-INDIA REGIONS: Is the job located outside India or restricted to non-India locations/timezones (e.g. SOCEUR, EST, Europe, EMEA, US, UK, PST, CST, Germany)? If YES, DISQUALIFY (passes_filter = false).
3. EXCLUDED ROLES: Is the title Data Engineer, DevSecOps, Azure Integration, Integration Engineer, .NET, Senior, Staff, Principal, Lead, Manager, Director, Trainee, or Intern? If YES, DISQUALIFY (passes_filter = false).

### Job Details:
Company: {company}
Title: {title}
Location: {location}
Job Description:
{jd_snippet}

### Instructions:
If the job passes ALL 3 rules, compute candidate match score (45.0 to 98.0%) reflecting true alignment with candidate programming languages, AI/ML stack, and Backend skills.
Respond ONLY with a JSON object (no markdown code blocks):
{{
  "passes_filter": true,
  "filter_reason": "Passes all criteria (SDE / AI Engineer in India, 0-3 YOE)",
  "match_score": 88.0,
  "match_analysis": "Strong alignment with candidate skills in Python, AI/GenAI, and Backend architecture"
}}
"""

def parse_json_from_llm(text):
    """Extract valid JSON object from LLM output."""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None

def evaluate_single_job(client, job_dict, resume_text):
    """Evaluate job via full-text pre-filter first, then compute candidate skill score and LLM analysis."""
    job_id = job_dict["job_id"]
    company = job_dict.get("company", "Unknown")
    title = job_dict.get("title", "Untitled")
    location = job_dict.get("location", "")
    jd_text = job_dict.get("jd_text", "") or ""

    # Step 1: Run Full-Text Deterministic Pre-filter (Exclusions, Timezones EST/Europe, YOE >= 4)
    is_filtered, passes_int, reason, yoe_val = prefilter_job(title, location, jd_text)
    if is_filtered:
        return job_id, passes_int, reason, 0.0, f"Filtered by rule: {reason}"

    # Step 2: Compute Granular Candidate Skill Match Score
    skill_score, skill_analysis = calculate_candidate_skill_score(title, location, jd_text)

    # Step 3: Run LLM Evaluation for remaining valid jobs
    jd_snippet = jd_text[:3500]
    prompt = PROMPT_TEMPLATE.format(
        resume_text=resume_text[:2000],
        company=company,
        title=title,
        location=location,
        jd_snippet=jd_snippet
    )

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=512,
            )
            content = resp.choices[0].message.content
            data = parse_json_from_llm(content)

            if data:
                passes = 1 if data.get("passes_filter", True) else 0
                if not passes:
                    return job_id, 0, str(data.get("filter_reason", "Filtered by LLM")), 0.0, "Filtered out by LLM evaluation"
                
                llm_score = float(data.get("match_score", skill_score))
                final_score = round(min(98.0, max(45.0, (0.6 * skill_score) + (0.4 * llm_score))), 1)
                llm_analysis = str(data.get("match_analysis", ""))[:220]
                combined_analysis = f"{skill_analysis} │ {llm_analysis}"
                return job_id, 1, "Passes all criteria", final_score, combined_analysis
            break
        except Exception as e:
            if ("429" in str(e) or "Too Many Requests" in str(e)) and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
            else:
                break

    return job_id, 1, "Passes all criteria", skill_score, skill_analysis

def evaluate_pending_jobs(db_path="seen_jobs.db", max_jobs=400, concurrency=2, force_reevaluate=True):
    """Find jobs in database and evaluate them using full-text pre-filter + candidate skill score + NVIDIA LLM API."""
    from career_watcher import init_db
    init_db()

    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT desc.job_id, jd.company, jd.title, jd.location, desc.jd_text
        FROM job_descriptions desc
        JOIN jobs_detail jd ON (desc.job_id = jd.job_id OR desc.job_id = jd.url)
        LIMIT ?
    """

    pending = conn.execute(query, (max_jobs,)).fetchall()

    if not pending:
        conn.close()
        print("No jobs found to evaluate.")
        return 0

    resume_text = load_resume()
    client = get_nvidia_client()

    print(f"Evaluating {len(pending)} jobs with full-text pre-filter + candidate skill score ({concurrency} parallel requests)...")

    def _worker(row):
        return evaluate_single_job(client, dict(row), resume_text)

    evaluated_results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        evaluated_results = list(executor.map(_worker, pending))

    cur = conn.cursor()
    for job_id, passes, reason, score, analysis in evaluated_results:
        cur.execute("""
            UPDATE job_descriptions
            SET passes_filter = ?, filter_reason = ?, match_score = ?, match_analysis = ?, evaluated_at = ?
            WHERE job_id = ?
        """, (passes, reason, score, analysis, time.time(), job_id))

    conn.commit()
    conn.close()

    print(f"[SUCCESS] Evaluated {len(evaluated_results)} job(s) with full-text pre-filter & candidate skill score.")
    return len(evaluated_results)

if __name__ == "__main__":
    evaluate_pending_jobs(force_reevaluate=True)
