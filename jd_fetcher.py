"""
jd_fetcher.py — Extract clean, beautifully formatted Job Description (JD) text and unified relative posting date (e.g. 'Posted 16 Days Ago').
"""

import re
import html
import time
import sqlite3
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, parse_qs
import requests
from bs4 import BeautifulSoup

# ---- Workday session cache keyed by tenant hostname ----
# Each Session carries CSRF cookies needed to bypass 403 on CXS API
_WD_SESSIONS: dict = {}
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

def _get_workday_session(host: str, tenant: str, site: str) -> requests.Session:
    """Return (or create) a requests.Session with CSRF cookies for a Workday tenant.
    Workday CXS API requires a CALYPSO_CSRF_TOKEN obtained from the tenant homepage.
    """
    if host in _WD_SESSIONS:
        return _WD_SESSIONS[host]
    session = requests.Session()
    session.headers.update({
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    # Visit the tenant listing page to collect session cookies (CALYPSO_CSRF_TOKEN)
    try:
        homepage = f"https://{host}/en-US/{site}"
        session.get(homepage, timeout=10, allow_redirects=True)
    except Exception:
        pass  # proceed even if homepage fails — cookies may still be set
    _WD_SESSIONS[host] = session
    return session

TODAY = date.today()

def normalize_posted_date(pdate, first_seen=None):
    """Normalize any date format into unified 'Posted X Days Ago' / 'Posted Today' / 'Posted Yesterday'."""
    if not pdate:
        if first_seen:
            days = max(0, int((time.time() - first_seen) // 86400))
            if days <= 0:
                return "Posted Today"
            if days == 1:
                return "Posted Yesterday"
            return f"Posted {days} Days Ago"
        return "Posted Today"

    pdate = pdate.strip()

    # Case 1: ISO Date YYYY-MM-DD
    m_iso = re.search(r"(\d{4})[/-](\d{2})[/-](\d{2})", pdate)
    if m_iso:
        try:
            d = date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            delta = (date.today() - d).days
            if delta <= 0:
                return "Posted Today"
            if delta == 1:
                return "Posted Yesterday"
            if delta > 30:
                return "Posted 30+ Days Ago"
            return f"Posted {delta} Days Ago"
        except Exception:
            pass

    # Case 2: Today / Yesterday
    if re.search(r"Today", pdate, re.I):
        return "Posted Today"
    if re.search(r"Yesterday", pdate, re.I):
        return "Posted Yesterday"

    # Case 3: Already 'Posted X Days Ago'
    m_days = re.search(r"(\d+)\+?\s*days?\s*ago", pdate, re.I)
    if m_days:
        num = int(m_days.group(1))
        if num > 30 or "+" in pdate:
            return "Posted 30+ Days Ago"
        if num <= 0:
            return "Posted Today"
        if num == 1:
            return "Posted Yesterday"
        return f"Posted {num} Days Ago"

    return pdate

POSTED_DATE_PATTERNS = [
    re.compile(r"\bPosted\s+([0-9]+\s+days?\s+ago)\b", re.I),
    re.compile(r"\bPosted\s+(Today|Yesterday|30\+\s+Days\s+Ago)\b", re.I),
    re.compile(r"\bPosted\s*(?:on)?\s*([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})\b", re.I),
    re.compile(r"\bPosted\s*(?:on)?\s*(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\b", re.I),
    re.compile(r"\b([0-9]+\s+days?\s+ago)\b", re.I),
]

def extract_posted_date_from_text(text):
    """Regex helper to extract posted date string from raw or clean text."""
    if not text:
        return ""
    for pat in POSTED_DATE_PATTERNS:
        m = pat.search(text)
        if m:
            return normalize_posted_date(m.group(0))
    return ""

def clean_html_to_text(html_content):
    """Convert HTML string to clean, beautifully formatted plain text without raw HTML tags or entities."""
    if not html_content:
        return ""
    raw_text = html.unescape(str(html_content))
    soup = BeautifulSoup(raw_text, "html.parser")

    for s in soup(["script", "style", "nav", "header", "footer", "form", "svg", "noscript", "button", "iframe"]):
        s.extract()

    for li in soup.find_all("li"):
        li.insert_before("• ")
        li.append("\n")

    for block in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "br", "tr"]):
        block.append("\n")

    text = soup.get_text(separator="\n")
    text = re.sub(r"<[^>]+>", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    clean_lines = [line for line in lines if line]
    return "\n".join(clean_lines)

def fetch_greenhouse_jd(url, company_name=""):
    """Fetch Greenhouse JD text and posted date using Greenhouse Board API."""
    job_id = None
    slug = None

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "gh_jid" in qs:
        job_id = qs["gh_jid"][0]
    else:
        m = re.search(r"greenhouse\.io/.*?(?:jobs|job_board)/(\d+)", url, re.I)
        if not m:
            m = re.search(r"[/-](\d{7,10})(?:$|\?|#)", url)
        if m:
            job_id = m.group(1)

    slug_m = re.search(r"greenhouse\.io/([a-z0-9_-]+)", url, re.I)
    if slug_m:
        slug = slug_m.group(1)
    elif company_name:
        slug = re.sub(r"[^a-z0-9_-]", "", company_name.lower().split()[0])

    if job_id:
        slugs_to_try = [slug] if slug else []
        if company_name:
            c_slug = re.sub(r"[^a-z0-9_-]", "", company_name.lower().split()[0])
            if c_slug not in slugs_to_try:
                slugs_to_try.append(c_slug)
        slugs_to_try.extend(["cloudflare", "mongodb", "sambanova", "databricks", "canonical"])

        for s in slugs_to_try:
            if not s:
                continue
            api = f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs/{job_id}"
            try:
                r = requests.get(api, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    content = data.get("content", "")
                    title = data.get("title", "")
                    loc = (data.get("location") or {}).get("name", "")
                    updated_at = data.get("updated_at") or ""
                    posted_date = normalize_posted_date(updated_at[:10]) if updated_at else extract_posted_date_from_text(content)

                    clean = clean_html_to_text(content)
                    if clean:
                        return f"Role: {title}\nLocation: {loc}\n\n{clean}", posted_date
            except Exception:
                pass
    return None, ""

def fetch_workday_jd(url):
    """Fetch Workday JD text and posted date using CXS API with session cookies.
    Falls back to generic HTML scraping when CXS fails (JS-rendered pages won't
    yield JD text via HTML, but at least we avoid saving stub data).
    """
    m = re.search(
        r"https?://([^.]+)\.[^/]*myworkdayjobs\.com"
        r"/(?:[a-z]{2}-[A-Z]{2}/)?"  # optional locale prefix (en-US, etc.)
        r"([^/?#]+)"                  # site/tenant path
        r"(/job/[^?#]+)",             # /job/... path
        url, re.I
    )
    if not m:
        return None, ""

    tenant, site, ext = m.group(1), m.group(2), m.group(3)
    host = urlparse(url).netloc
    api = f"https://{host}/wday/cxs/{tenant}/{site}{ext}"

    # ---- Attempt 1: CXS API with session cookies (bypasses 403) ----
    try:
        session = _get_workday_session(host, tenant, site)
        csrf = session.cookies.get("CALYPSO_CSRF_TOKEN", "")
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": url,
            "Origin": f"https://{host}",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if csrf:
            headers["X-Calypso-CSRF-Token"] = csrf
        r = session.get(api, headers=headers, timeout=14)
        if r.status_code == 200:
            info = (r.json().get("jobPostingInfo") or {})
            title = info.get("title", "")
            loc = info.get("location", "")
            html_desc = info.get("jobDescription", "")
            posted_on = info.get("postedOn", "") or ""
            if not posted_on and info.get("startDate"):
                posted_on = info.get("startDate")
            posted_date = normalize_posted_date(posted_on)
            clean = clean_html_to_text(html_desc)
            if clean:
                return f"Role: {title}\nLocation: {loc}\n\n{clean}", posted_date
    except Exception:
        pass

    return None, ""

def fetch_lever_jd(url):
    """Fetch Lever JD text and posted date using Lever API."""
    m = re.search(r"lever\.co/([a-z0-9_-]+)/([a-f0-9-]+)", url, re.I)
    if m:
        slug, posting_id = m.group(1), m.group(2)
        api = f"https://api.lever.co/v0/postings/{slug}/{posting_id}"
        try:
            r = requests.get(api, timeout=12)
            if r.status_code == 200:
                data = r.json()
                title = data.get("text", "")
                categories = data.get("categories", {})
                loc = categories.get("location", "")
                created_ms = data.get("createdAt")
                posted_date = ""
                if created_ms:
                    days_ago = int((time.time() - (created_ms / 1000.0)) // 86400)
                    if days_ago <= 0:
                        posted_date = "Posted Today"
                    elif days_ago == 1:
                        posted_date = "Posted Yesterday"
                    else:
                        posted_date = f"Posted {days_ago} Days Ago"

                desc = clean_html_to_text(data.get("descriptionPlain", "") or data.get("description", ""))
                lists = data.get("lists", [])
                extra_text = []
                for lst in lists:
                    text_title = lst.get("text", "")
                    content = clean_html_to_text(lst.get("content", ""))
                    extra_text.append(f"{text_title}\n{content}")
                full_jd = f"Role: {title}\nLocation: {loc}\n\n" + desc + "\n\n" + "\n\n".join(extra_text)
                if full_jd.strip():
                    return full_jd.strip(), posted_date
        except Exception:
            pass
    return None, ""

def fetch_ashby_jd(url):
    """Fetch Ashby JD text and posted date using Ashby HTML/script parsing."""
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "Chrome/126.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            # Parse JSON script tags containing descriptionHtml or jobPosting
            for s in soup.find_all("script"):
                txt = s.string or ""
                if "descriptionHtml" in txt or "jobPosting" in txt:
                    m = re.search(r'"descriptionHtml"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
                    if m:
                        try:
                            raw_html = m.group(1).encode("utf-8").decode("unicode_escape")
                        except Exception:
                            raw_html = m.group(1)
                        clean = clean_html_to_text(raw_html)
                        posted_date = extract_posted_date_from_text(raw_html) or extract_posted_date_from_text(r.text)
                        if clean:
                            return clean, posted_date
            
            # Fallback to generic clean HTML text
            clean = clean_html_to_text(r.text)
            posted_date = extract_posted_date_from_text(r.text)
            if clean:
                return clean, posted_date
    except Exception:
        pass
    return "", ""

def fetch_generic_html_jd(url):
    """Fetch URL and extract main body text + posted date."""
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "Chrome/126.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code == 200:
            raw = r.text
            posted_date = extract_posted_date_from_text(raw)
            clean = clean_html_to_text(raw)
            if not posted_date:
                posted_date = extract_posted_date_from_text(clean)
            return clean, posted_date
    except Exception:
        pass
    return "", ""

def fetch_jd(url, company_name=""):
    """Router to fetch JD text and posted_date based on URL platform."""
    if not url or not url.startswith("http"):
        return "", ""
    u = url.lower()

    if "myworkdayjobs.com" in u:
        jd, pdate = fetch_workday_jd(url)
        if jd:
            return jd, pdate
    if "gh_jid" in u or "greenhouse.io" in u or "mongodb.com" in u or "canonical.com" in u or "databricks.com" in u:
        jd, pdate = fetch_greenhouse_jd(url, company_name)
        if jd:
            return jd, pdate
    if "lever.co" in u:
        jd, pdate = fetch_lever_jd(url)
        if jd:
            return jd, pdate
    if "ashbyhq.com" in u:
        jd, pdate = fetch_ashby_jd(url)
        if jd:
            return jd, pdate

    return fetch_generic_html_jd(url)

def fetch_and_store_jds(db_path="seen_jobs.db", concurrency=10, force_refetch=True):
    """Find jobs in jobs_detail, fetch clean beautified JDs + posted dates, and save in job_descriptions."""
    from career_watcher import init_db
    conn = init_db()
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("ALTER TABLE job_descriptions ADD COLUMN posted_date TEXT")
        conn.commit()
    except Exception:
        pass

    query = """
        SELECT jd.job_id, COALESCE(NULLIF(jd.url, ''), jd.job_id) AS url, jd.title, jd.company, jd.first_seen
        FROM jobs_detail jd
    """
    pending = conn.execute(query).fetchall()
    conn.close()

    if not pending:
        print("No jobs found in database.")
        return 0

    print(f"Fetching & normalizing posted dates for {len(pending)} jobs ({concurrency} parallel requests)...")

    def _worker(row):
        job_id = row["job_id"]
        url = row["url"]
        title = row["title"]
        company = row["company"]
        first_seen = row["first_seen"]

        jd_text, posted_date = fetch_jd(url, company)
        posted_date = normalize_posted_date(posted_date, first_seen)
        jd_text = clean_html_to_text(jd_text) if jd_text else ""
        # Return None for jd_text when nothing useful fetched —
        # the DB record will only be updated when we have real content.
        if len(jd_text.strip()) < 80:
            jd_text = None  # signal: no real JD content fetched
        return job_id, jd_text, posted_date

    fetched_count = 0
    skipped_count = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = executor.map(_worker, pending)
        conn = sqlite3.connect(db_path, timeout=30.0)
        cur = conn.cursor()

        for job_id, jd_text, posted_date in results:
            if jd_text is None:
                # No real JD fetched — INSERT a placeholder if no record exists,
                # but NEVER overwrite an existing record that may have good JD text.
                cur.execute("""
                    INSERT OR IGNORE INTO job_descriptions
                    (job_id, jd_text, posted_date, fetched_at, passes_filter, filter_reason, match_score, match_analysis, evaluated_at)
                    VALUES (?, '', ?, ?, 1, '', 0.0, '', 0.0)
                """, (job_id, posted_date, time.time()))
                skipped_count += 1
            else:
                # Only write jd_text when it's better (longer) than what's stored.
                cur.execute("""
                    INSERT INTO job_descriptions
                    (job_id, jd_text, posted_date, fetched_at, passes_filter, filter_reason, match_score, match_analysis, evaluated_at)
                    VALUES (?, ?, ?, ?, 1, '', 0.0, '', 0.0)
                    ON CONFLICT(job_id) DO UPDATE SET
                        jd_text = CASE
                            WHEN LENGTH(excluded.jd_text) > LENGTH(job_descriptions.jd_text)
                            THEN excluded.jd_text
                            ELSE job_descriptions.jd_text
                        END,
                        posted_date = excluded.posted_date,
                        fetched_at = excluded.fetched_at
                """, (job_id, jd_text, posted_date, time.time()))
                fetched_count += 1

        conn.commit()
        conn.close()

    print(f"[SUCCESS] Saved {fetched_count} JD(s) with content; {skipped_count} had no fetchable JD (preserved existing data).")
    return fetched_count

if __name__ == "__main__":
    fetch_and_store_jds(force_refetch=True)
