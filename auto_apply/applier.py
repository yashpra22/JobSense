"""Browser-use powered application assistant with conservative submission verification."""

import asyncio
import os
import sys
import time
import traceback

try:
    from browser_use import Agent, BrowserProfile, BrowserSession
    from browser_use.llm.openai.chat import ChatOpenAI
    _BROWSER_USE_AVAILABLE = True
except ImportError:
    _BROWSER_USE_AVAILABLE = False

from auto_apply.config import (
    CANDIDATE,
    HEADLESS,
    LLM_PROVIDER,
    NVIDIA_API_KEY,
    NVIDIA_BASE_URL,
    NVIDIA_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    RESUME_TXT_PATH,
    assert_live_apply_enabled,
)


def _check_browser_use() -> None:
    if not _BROWSER_USE_AVAILABLE:
        raise RuntimeError("browser-use library is not installed")


def _load_resume_context() -> str:
    try:
        with open(RESUME_TXT_PATH, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _build_task_prompt(job: dict, dry_run: bool) -> str:
    c = CANDIDATE
    resume_ctx = _load_resume_context()
    portal = job.get("portal", "Unknown")
    submit_instruction = (
        "DRY RUN: fill all fields but NEVER click Submit/Apply. Stop before final submission and report dry_run_complete."
        if dry_run else
        "LIVE MODE: fill all fields, click the final Submit/Apply control once, and only report submitted after observing a concrete confirmation state."
    )
    return f"""
You are an expert job application assistant.

JOB
- Title: {job.get('title', '')}
- Company: {job.get('company', '')}
- Portal: {portal}
- Match score: {job.get('match_score', 0):.1f}
- URL: {job.get('url', '')}

CANDIDATE
- Full name: {c.get('full_name', '')}
- First name: {c.get('first_name', '')}
- Last name: {c.get('last_name', '')}
- Email: {c.get('email', '')}
- Phone: {c.get('phone', '')}
- City: {c.get('city', '')}
- State: {c.get('state', '')}
- Country: {c.get('country', '')}
- Postal code: {c.get('postal_code', '')}
- Current company: {c.get('current_company', '')}
- Current title: {c.get('current_title', '')}
- Experience: {c.get('years_experience', '')} year(s)
- Degree: {c.get('highest_degree', '')}
- Field: {c.get('field_of_study', '')}
- University: {c.get('university', '')}
- Graduation year: {c.get('graduation_year', '')}
- LinkedIn: {c.get('linkedin', '')}
- GitHub: {c.get('github', '')}
- Portfolio: {c.get('portfolio', '')}
- Willing to relocate: {c.get('willing_to_relocate', False)}
- Visa sponsorship needed: {c.get('visa_sponsorship_needed', False)}
- Authorized to work: {c.get('authorized_to_work', False)}
- Professional summary: {c.get('summary', '')}

RESUME PDF: {c.get('resume_pdf_path', '')}
RESUME CONTEXT:
{resume_ctx[:2500]}

RULES
- Never invent candidate facts.
- Never answer sensitive demographic questions unless an exact candidate value is configured.
- If a required question cannot be answered safely, stop and report blocked_missing_data.
- Do not bypass CAPTCHA, anti-bot, or access controls.
- {submit_instruction}
""".strip()


def _looks_like_concrete_submission_confirmation(text: str) -> bool:
    """Conservative confirmation: generic 'success' is intentionally insufficient."""
    lowered = (text or "").lower()
    strong_signals = [
        "application received",
        "application has been submitted",
        "application submitted successfully",
        "thank you for applying",
        "thank you for your application",
        "we have received your application",
        "confirmation number",
        "application id",
    ]
    return any(signal in lowered for signal in strong_signals)


async def apply_to_job(job: dict, dry_run: bool = True, provider: str = None, model: str = None, headless: bool = None) -> dict:
    _check_browser_use()
    job_url = job.get("url", "").strip()
    if not job_url:
        return {"status": "skipped", "notes": "No application URL found."}
    if not dry_run:
        # Defense in depth: the caller also enforces this gate.
        assert_live_apply_enabled()

    active_provider = (provider or LLM_PROVIDER).lower()
    is_headless = HEADLESS if headless is None else headless
    if active_provider == "ollama":
        llm = ChatOpenAI(
            model=model or OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",
            temperature=0.1,
            max_completion_tokens=4096,
            add_schema_to_system_prompt=True,
            timeout=300,
        )
    else:
        if not NVIDIA_API_KEY:
            return {"status": "failed", "notes": "NVIDIA_API_KEY is not set."}
        llm = ChatOpenAI(
            model=model or NVIDIA_MODEL,
            base_url=NVIDIA_BASE_URL,
            api_key=NVIDIA_API_KEY,
            temperature=0.1,
            max_completion_tokens=4096,
            add_schema_to_system_prompt=True,
            timeout=300,
        )

    task = _build_task_prompt(job, dry_run=dry_run)
    profile = BrowserProfile(headless=is_headless, keep_alive=True, viewport={"width": 1280, "height": 900})
    browser_session = BrowserSession(browser_profile=profile)
    resume_pdf = CANDIDATE.get("resume_pdf_path", "")
    available_files = [resume_pdf] if resume_pdf and os.path.exists(resume_pdf) else []
    step_times = []
    last_step_time = time.time()

    def _step_callback(state_summary, agent_output, step_number):
        nonlocal last_step_time
        now = time.time()
        step_times.append(now - last_step_time)
        last_step_time = now
        try:
            import sqlite3
            from auto_apply.config import DB_PATH
            conn = sqlite3.connect(DB_PATH)
            next_goal = ""
            current = getattr(agent_output, "current_state", None)
            if current is not None:
                next_goal = str(getattr(current, "next_goal", ""))
            conn.execute(
                "INSERT INTO events (ts, event_type, company, message) VALUES (?, ?, ?, ?)",
                (time.time(), "agent_step", job.get("company", "Unknown"), f"[STEP {step_number}] Goal: {next_goal[:300]}"),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    try:
        agent = Agent(
            task=task,
            llm=llm,
            browser=browser_session,
            available_file_paths=available_files,
            max_failures=3,
            max_actions_per_step=8,
            use_vision=False,
            use_thinking=False,
            directly_open_url=True,
            llm_timeout=300,
            step_timeout=300,
            register_new_step_callback=_step_callback,
        )
        history = await agent.run()
        final_result = history.final_result() or ""

        if dry_run:
            return {"status": "dry_run", "notes": f"Dry run complete: {final_result[:500]}"}

        if _looks_like_concrete_submission_confirmation(final_result):
            return {"status": "success", "notes": f"Verified confirmation signal: {final_result[:500]}"}

        # Do not claim success merely because the agent returned 'success' or completed actions.
        return {
            "status": "uncertain",
            "notes": "Submission may have occurred, but no concrete confirmation signal was observed. Manual verification required. Agent result: " + final_result[:500],
        }
    except Exception as exc:
        return {"status": "failed", "notes": f"Exception during agent run: {exc}\n{traceback.format_exc()[:1000]}"}
    finally:
        if not is_headless:
            try:
                await asyncio.sleep(5)
            except Exception:
                pass
        try:
            await browser_session.stop()
        except Exception:
            pass
