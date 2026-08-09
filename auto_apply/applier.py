"""
auto_apply/applier.py -- Browser-Use AI Agent for Job Application
"""

import asyncio
import os
import sys
import time
import traceback
from pathlib import Path

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
    detect_portal,
)


def _check_browser_use() -> None:
    if not _BROWSER_USE_AVAILABLE:
        print("\n" + "=" * 60)
        print("  ERROR: browser-use library is not installed.")
        print("  Please run: .venv\\Scripts\\pip.exe install browser-use")
        print("=" * 60 + "\n")
        sys.exit(1)


def _load_resume_context() -> str:
    try:
        with open(RESUME_TXT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _build_task_prompt(job: dict, dry_run: bool) -> str:
    c = CANDIDATE
    resume_ctx = _load_resume_context()
    portal = job.get("portal", "Unknown")
    job_url = job.get("url", "")
    title   = job.get("title", "")
    company = job.get("company", "")
    score   = job.get("match_score", 0)

    submit_instruction = (
        "[DRY RUN MODE] Do NOT click the Submit or Apply button. "
        "Fill all fields as if you were going to submit, but STOP just before the final submit button. "
        "Take a screenshot of the completed form and use the done action with result='dry_run_complete'."
        if dry_run else
        "After filling all fields completely, click the Submit / Apply button to submit the application. "
        "Confirm the success screen appears, then use the done action with result='submitted'."
    )

    portal_hints = {
        "Greenhouse": (
            "This is a Greenhouse application. The form typically has: "
            "First Name, Last Name, Email, Phone, Resume upload, LinkedIn URL, "
            "then demographic questions (Gender, Race, Veteran status, Disability status)."
        ),
        "Lever": (
            "This is a Lever application. Lever forms typically have: "
            "Full Name, Email, Phone, Current Company/University, LinkedIn URL, Resume upload."
        ),
        "Ashby": (
            "This is an Ashby application. Forms have: Name, Email, Phone, "
            "optional social links, Resume upload, and custom questions."
        ),
        "Workday": (
            "This is a Workday application. Workday multi-step flow: "
            "1) Sign In/Create Account with email and password. "
            "2) Personal Information (Name, Address, Phone, Source). "
            "3) Work Experience & Resume Upload. 4) Education. 5) Voluntary Disclosures. "
            "WORKDAY ACCOUNT CREATION HINT: When creating a Workday account: "
            "Step 1: Input Password (e.g. 'StrongPass123!') and Verify Password fields. "
            "Step 2: Do NOT click 'Create Account' in the same step as typing. Type passwords first, wait 1 step, then click 'Create Account'. "
            "WORKDAY DROPDOWN HINT: For Workday dropdowns (like 'How Did You Hear About Us?'): "
            "click the combobox input, type the search text (e.g. 'LinkedIn' or 'Career Site' or 'Search Engine'), "
            "then click directly on the popover list item that appears below the input box, "
            "or press the Enter key. If 'LinkedIn' is not in the list, select 'Career Site', 'Search Engine', or 'Other'."
        ),
    }
    portal_hint = portal_hints.get(portal, "Fill all visible application form fields carefully.")

    prompt = f"""
You are an expert job application assistant helping a software engineer apply for a job.

## JOB DETAILS
- Title: {title}
- Company: {company}
- Portal Type: {portal}
- Match Score: {score:.1f}%
- Application URL: {job_url}

## YOUR TASK
Navigate to the application URL and fill out the entire job application form using the candidate information below.

## PORTAL INSTRUCTIONS
{portal_hint}

## CANDIDATE INFORMATION
- Full Name: {c["full_name"]}
- First Name: {c["first_name"]}
- Last Name: {c["last_name"]}
- Email: {c["email"]}
- Phone: {c["phone"]}
- City: {c["city"]}
- State/Province: {c.get("state", "Uttar Pradesh")}
- Country: {c["country"]}
- Postal Code: {c.get("postal_code", "")}
- Current Company: {c.get("current_company", "Samsung Research India")}
- Current Title: {c.get("current_title", "Software Engineer - AI Systems & Agentic AI")}
- Years of Experience: {c["years_experience"]} year(s)
- Degree: {c["highest_degree"]}
- Field of Study: {c.get("field_of_study", "Computer Science & Engineering")}
- University: {c.get("university", "")}
- Graduation Year: {c.get("graduation_year", "")}
- LinkedIn: {c.get("linkedin", "")}
- GitHub: {c.get("github", "")}
- Portfolio: {c.get("portfolio", "")}
- Willing to Relocate: {"Yes" if c.get("willing_to_relocate") else "No"}
- Visa Sponsorship Needed: {"Yes" if c.get("visa_sponsorship_needed") else "No"}
- Authorized to Work in India: {"Yes" if c.get("authorized_to_work") else "No"}
- US Work Authorization: {"Yes" if c.get("us_authorized") else "No"}
- Professional Summary: {c.get("summary", "")}

## RESUME UPLOAD
Upload resume PDF path: {c["resume_pdf_path"]}

## SCREENING QUESTIONS
Why this company: {c.get("why_this_company", "")}
Greatest strength: {c.get("greatest_strength", "")}

## SKILLS CONTEXT
{resume_ctx[:1500]}

## SUBMISSION INSTRUCTION
{submit_instruction}
"""
    return prompt.strip()


async def apply_to_job(
    job: dict,
    dry_run: bool = True,
    provider: str = None,
    model: str = None,
    headless: bool = None
) -> dict:
    _check_browser_use()

    job_url = job.get("url", "").strip()
    if not job_url:
        return {"status": "skipped", "notes": "No application URL found for this job."}

    title   = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    mode    = "DRY RUN" if dry_run else "LIVE"

    active_provider = (provider or LLM_PROVIDER).lower()
    is_headless = HEADLESS if headless is None else headless
    vis_mode = "HEADLESS (Background)" if is_headless else "HEADED (Visible Screen)"

    print(f"\n{'-'*60}")
    print(f"  [{mode}] Applying to: {title} @ {company}")
    print(f"  URL        : {job_url}")
    print(f"  Provider   : {active_provider.upper()}")
    print(f"  Visibility : {vis_mode}")
    print(f"{'-'*60}")

    if active_provider == "ollama":
        target_model = model or OLLAMA_MODEL
        print(f"  Ollama Model: {target_model} @ {OLLAMA_BASE_URL}")
        llm = ChatOpenAI(
            model=target_model,
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",
            temperature=0.1,
            max_completion_tokens=4096,
            add_schema_to_system_prompt=True,
            timeout=300,
        )
    else:  # nvidia
        target_model = model or NVIDIA_MODEL
        if not NVIDIA_API_KEY:
            return {
                "status": "failed",
                "notes": "NVIDIA_API_KEY is not set. Check your .env file.",
            }
        print(f"  NVIDIA Model: {target_model} @ {NVIDIA_BASE_URL}")
        llm = ChatOpenAI(
            model=target_model,
            base_url=NVIDIA_BASE_URL,
            api_key=NVIDIA_API_KEY,
            temperature=0.1,
            max_completion_tokens=4096,
            add_schema_to_system_prompt=True,
            timeout=300,
        )

    step_times = []
    last_step_time = [time.time()]

    def _step_callback(state_summary, agent_output, step_number):
        now = time.time()
        elapsed = now - last_step_time[0]
        last_step_time[0] = now
        step_times.append(elapsed)

        print(f"\n{'='*60}")
        print(f"  [STEP {step_number}] Time taken: {elapsed:.2f}s ({elapsed/60:.2f} min)")
        next_g = ""
        if hasattr(agent_output, "current_state"):
            cs = agent_output.current_state
            if hasattr(cs, "evaluation_previous_goal") and cs.evaluation_previous_goal:
                print(f"  Eval     : {cs.evaluation_previous_goal}")
            if hasattr(cs, "memory") and cs.memory:
                print(f"  Memory   : {cs.memory}")
            if hasattr(cs, "next_goal") and cs.next_goal:
                next_g = str(cs.next_goal)
                print(f"  Next Goal: {next_g}")
        if hasattr(agent_output, "action") and agent_output.action:
            print(f"  Actions  : {agent_output.action}")
        print(f"{'='*60}\n")

        # Stream event to database so dashboard activity feed displays progress live
        try:
            import sqlite3
            from auto_apply.config import DB_PATH
            evt_conn = sqlite3.connect(DB_PATH)
            msg = f"[STEP {step_number}] ({elapsed:.1f}s) Goal: {next_g[:120]}" if next_g else f"[STEP {step_number}] ({elapsed:.1f}s) Processing form..."
            evt_conn.execute(
                "INSERT INTO events (ts, event_type, company, message) VALUES (?, ?, ?, ?)",
                (time.time(), "agent_step", company, msg)
            )
            evt_conn.commit()
            evt_conn.close()
        except Exception:
            pass

    task = _build_task_prompt(job, dry_run=dry_run)

    profile = BrowserProfile(
        headless=is_headless,
        keep_alive=True,
        viewport={"width": 1280, "height": 900},
    )

    browser_session = BrowserSession(browser_profile=profile)
    resume_pdf = CANDIDATE.get("resume_pdf_path", "")
    available_files = [resume_pdf] if resume_pdf and os.path.exists(resume_pdf) else []

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
            return {
                "status": "dry_run",
                "notes": f"Dry run complete. Agent result: {final_result[:300]}",
            }
        else:
            result_lower = final_result.lower()
            if any(kw in result_lower for kw in ["submitted", "success", "thank you", "application received", "confirmed"]):
                return {
                    "status": "success",
                    "notes": f"Application submitted successfully. Result: {final_result[:300]}",
                }
            else:
                return {
                    "status": "failed",
                    "notes": f"Submission uncertain. Agent result: {final_result[:300]}",
                }

    except Exception as exc:
        tb = traceback.format_exc()
        return {
            "status": "failed",
            "notes": f"Exception during agent run: {exc}\n{tb[:500]}",
        }
    finally:
        if not is_headless:
            print("\n  [Visible Mode] Pausing 20 seconds before closing browser window so you can inspect the application...")
            try:
                await asyncio.sleep(20)
            except Exception:
                pass
        try:
            await browser_session.stop()
        except Exception:
            pass
