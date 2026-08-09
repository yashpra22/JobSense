"""
Career-page job watcher — 100 US companies, OS / platform / embedded / Linux focus.

v3 changes:
  - Added playwright-stealth to defeat basic bot detection on Cloudflare/etc sites.
  - Detects ATS platform (Workday, Greenhouse, SmartRecruiters, Lever, iCIMS,
    Eightfold, Ashby) and applies the right scraping strategy automatically.
  - For Workday sites, calls the JSON API directly when possible (fast + reliable).
  - For Greenhouse boards (boards-api), calls the public API.

Setup:
    pip install playwright beautifulsoup4 playwright-stealth requests
    playwright install chromium
    Edit EMAIL CONFIG below. Gmail App Password: https://myaccount.google.com/apppasswords

Run:
    python career_watcher.py
Stop:
    Ctrl+C (state persists in seen_jobs.db)
"""

import asyncio
import json
import os
import re
import smtplib
import sqlite3
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime
from urllib.parse import urljoin, urlparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# Optional: playwright-stealth. Falls back gracefully if not installed or API differs.
try:
    from playwright_stealth import Stealth
    _STEALTH = Stealth()

    async def _apply_stealth(context):
        await _STEALTH.apply_stealth_async(context)
except ImportError:
    try:
        # Older API
        from playwright_stealth import stealth_async as _legacy_stealth

        async def _apply_stealth(page_or_context):
            await _legacy_stealth(page_or_context)
    except ImportError:
        async def _apply_stealth(_):
            pass  # Stealth not installed; continue without it.

# ================ EMAIL CONFIG ================
# Set these via environment variables so credentials never live in source.
# For Gmail, generate an App Password: https://myaccount.google.com/apppasswords
#
#   export EMAIL_FROM="you@gmail.com"
#   export EMAIL_TO="you@gmail.com"
#   export SMTP_USER="you@gmail.com"
#   export SMTP_PASS="your-16-char-app-password"

import os

# Auto-load environment variables from local .env file if present
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ[_k.strip()] = _v.strip().strip("'\"")
    except Exception:
        pass

EMAIL_FROM = os.environ.get("EMAIL_FROM", "yash.prakash09876@gmail.com")
EMAIL_TO   = os.environ.get("EMAIL_TO", "sahilobhrai19@gmail.com, yashpra222@gmail.com")
SMTP_HOST  = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER  = os.environ.get("SMTP_USER", "yash.prakash09876@gmail.com")
SMTP_PASS  = os.environ.get("SMTP_PASS", "")   # Loaded securely from .env file or environment

# ================ RUNTIME CONFIG ================

CHECK_INTERVAL_MINUTES = 300
CONCURRENCY = 8                # how many pages to fetch in parallel
ATTACH_TXT_FILE = True         # attach .txt file with URLs to each email
VERIFY_URLS = True             # check each job URL is reachable (200/redirect) before alerting
URL_VERIFY_CONCURRENCY = 16    # parallel verification requests
DAILY_DIGEST_HOUR = 8          # send a roundup email at this hour (24h, local time) even if quiet
DB_PATH = "seen_jobs.db"
PAGE_TIMEOUT_MS = 20_000        # 20s — bot-walled sites hang; don't waste 60s each

# ================ USER PROFILE CONFIG ================
USER_EXPERIENCE_YOE = 3        # Target candidate experience level in years
EXCLUDE_HIGH_SENIORITY = True  # Exclude >3 YOE roles (Senior, Staff, Principal, Lead, Director)
EXCLUDE_US_LOCATIONS = True    # Strict filter: Only allow India roles (Bangalore, Hyd, etc.) or pure global Remote roles (excludes Remote US, Remote UK, Remote Canada, EMEA, Japan, etc.)


# ================ COMPANIES ================
# 100 US companies grouped by category. Most are systems/OS/embedded heavy.

COMPANIES = [
    {"name": "NVIDIA", "url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"},
    {"name": "AMD", "url": "https://careers.amd.com/careers-home/jobs"},
    {"name": "SambaNova", "url": "https://boards.greenhouse.io/sambanovasystems"},
    {"name": "Tenstorrent", "url": "https://job-boards.greenhouse.io/tenstorrent"},
    {"name": "Cloudflare", "url": "https://boards.greenhouse.io/cloudflare"},
    {"name": "Render Cloud", "url": "https://jobs.ashbyhq.com/render"},
    {"name": "Warp", "url": "https://jobs.ashbyhq.com/warp"},
    {"name": "SimplifyJobs New-Grad", "url": "https://github.com/SimplifyJobs/New-Grad-Positions"},
    {"name": "Intel", "url": "https://intel.wd1.myworkdayjobs.com/External"},
    {"name": "Qualcomm", "url": "https://qualcomm.wd12.myworkdayjobs.com/en-US/External"},
    {"name": "Broadcom", "url": "https://broadcom.wd1.myworkdayjobs.com/External_Career"},
    {"name": "Marvell", "url": "https://marvell.wd1.myworkdayjobs.com/MarvellCareers"},
    {"name": "Micron", "url": "https://micron.wd1.myworkdayjobs.com/External"},
    {"name": "Texas Instruments", "url": "https://edbz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs"},
    {"name": "Analog Devices", "url": "https://analogdevices.wd1.myworkdayjobs.com/External"},
    {"name": "Cadence", "url": "https://cadence.wd1.myworkdayjobs.com/External_Careers"},
    {"name": "Applied Materials", "url": "https://amat.wd1.myworkdayjobs.com/External"},
    {"name": "KLA", "url": "https://kla.wd1.myworkdayjobs.com/Search"},
    {"name": "Salesforce", "url": "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site"},
    {"name": "Adobe", "url": "https://adobe.wd5.myworkdayjobs.com/external_experienced"},
    {"name": "Fastly", "url": "https://www.fastly.com/about/careers/current-openings"},
    {"name": "Akamai", "url": "https://akamaicareers.dejobs.org/"},
    {"name": "Databricks", "url": "https://www.databricks.com/company/careers/open-positions"},
    {"name": "MongoDB", "url": "https://boards.greenhouse.io/mongodb"},
    {"name": "Elastic", "url": "https://www.elastic.co/about/careers"},
    {"name": "Red Hat", "url": "https://redhat.wd5.myworkdayjobs.com/jobs"},
    {"name": "Canonical", "url": "https://canonical.com/careers/all"},
    {"name": "SUSE", "url": "https://jobs.suse.com/"},
    {"name": "Cisco", "url": "https://jobs.cisco.com/jobs/SearchJobs/"},
    {"name": "Arista Networks", "url": "https://www.arista.com/en/company/careers/jobs"},
    {"name": "Verizon", "url": "https://mycareer.verizon.com/jobs/"},
    {"name": "Comcast", "url": "https://jobs.comcast.com/careers"},
    {"name": "SpaceX", "url": "https://www.spacex.com/careers/jobs"},
    {"name": "Waymo", "url": "https://boards.greenhouse.io/waymo"},
    {"name": "Rivian", "url": "https://careers.rivian.com/jobs"},
    {"name": "Lucid Motors", "url": "https://lucidmotors.com/careers"},
    {"name": "Aurora Innovation", "url": "https://aurora.tech/jobs"},
    {"name": "Nuro", "url": "https://www.nuro.ai/careers"},
    {"name": "Boston Dynamics", "url": "https://bostondynamics.com/careers/"},
    {"name": "Saildrone", "url": "https://www.saildrone.com/careers"},
    {"name": "Lockheed Martin", "url": "https://www.lockheedmartinjobs.com/search-jobs"},
    {"name": "Northrop Grumman", "url": "https://www.northropgrumman.com/jobs/"},
    {"name": "Boeing", "url": "https://jobs.boeing.com/search-jobs"},
    {"name": "General Dynamics", "url": "https://careers-gd.icims.com/jobs/search"},
    {"name": "Palantir", "url": "https://www.palantir.com/careers/"},
    {"name": "Anthropic", "url": "https://www.anthropic.com/jobs"},
    {"name": "OpenAI", "url": "https://openai.com/careers/search/"},
    {"name": "Valve", "url": "https://www.valvesoftware.com/en/jobs"},
    {"name": "Epic Games", "url": "https://www.epicgames.com/site/en-US/careers"},
    {"name": "Unity", "url": "https://careers.unity.com/"},
    {"name": "Electronic Arts", "url": "https://ea.gr8people.com/jobs"},
    {"name": "Activision", "url": "https://careers.activisionblizzard.com/jobs"},
    {"name": "Take-Two", "url": "https://www.take2games.com/careers"},
    {"name": "Riot Games", "url": "https://www.riotgames.com/en/work-with-us/jobs"},
    {"name": "Dell", "url": "https://jobs.dell.com/en/search-jobs"},
    {"name": "HPE", "url": "https://hpe.wd5.myworkdayjobs.com/Jobsathpe"},
    {"name": "NetApp", "url": "https://careers.netapp.com/search-jobs"},
    {"name": "Pure Storage", "url": "https://www.purestorage.com/company/careers/job-openings.html"},
    {"name": "Stripe", "url": "https://stripe.com/jobs/search"},
    {"name": "Datadog", "url": "https://boards.greenhouse.io/datadog"},
    {"name": "Splunk", "url": "https://www.splunk.com/en_us/careers/jobs.html"},
    {"name": "Twilio", "url": "https://boards.greenhouse.io/twilio"},
    {"name": "Cloudera", "url": "https://www.cloudera.com/about/careers.html"},
    {"name": "PagerDuty", "url": "https://boards.greenhouse.io/pagerduty"},
    {"name": "Uber", "url": "https://www.uber.com/us/en/careers/list/"},
    {"name": "Lyft", "url": "https://boards.greenhouse.io/lyft"},
    {"name": "DoorDash", "url": "https://careersatdoordash.com/jobs"},
    {"name": "Airbnb", "url": "https://careers.airbnb.com/positions/"},
    {"name": "Snap", "url": "https://snapchat.wd1.myworkdayjobs.com/snap"},
    {"name": "Spotify", "url": "https://www.lifeatspotify.com/jobs"},
    {"name": "Reddit", "url": "https://www.redditinc.com/careers"},
    {"name": "Square / Block", "url": "https://block.xyz/careers"},
    {"name": "Coinbase", "url": "https://www.coinbase.com/careers/positions"},
    {"name": "Scale AI", "url": "https://boards.greenhouse.io/scaleai"},
    {"name": "xAI", "url": "https://job-boards.greenhouse.io/xai"},
    {"name": "Hugging Face", "url": "https://apply.workable.com/huggingface/"},
    {"name": "Modal Labs", "url": "https://jobs.ashbyhq.com/modal"},
    {"name": "LangChain", "url": "https://jobs.ashbyhq.com/langchain"},
    {"name": "Glean", "url": "https://job-boards.greenhouse.io/gleanwork"},
    {"name": "Harvey", "url": "https://jobs.ashbyhq.com/harvey"},
    {"name": "Brex", "url": "https://job-boards.greenhouse.io/brex"},
    {"name": "Ramp", "url": "https://jobs.ashbyhq.com/ramp"},
    {"name": "Chime", "url": "https://job-boards.greenhouse.io/chime"},
    {"name": "Mercury", "url": "https://job-boards.greenhouse.io/mercury"},
    {"name": "Affirm", "url": "https://job-boards.greenhouse.io/affirm"},
    {"name": "Robinhood", "url": "https://job-boards.greenhouse.io/robinhood"},
    {"name": "Carta", "url": "https://job-boards.greenhouse.io/carta"},
    {"name": "Gusto", "url": "https://job-boards.greenhouse.io/gusto"},
    {"name": "Bill.com", "url": "https://job-boards.greenhouse.io/billcom"},
    {"name": "Marqeta", "url": "https://job-boards.greenhouse.io/marqeta"},
    {"name": "GitHub", "url": "https://github.careers/careers-home/jobs"},
    {"name": "GitLab", "url": "https://about.gitlab.com/jobs/all-jobs/"},
    {"name": "Vercel", "url": "https://vercel.com/careers"},
    {"name": "Netlify", "url": "https://www.netlify.com/careers/"},
    {"name": "Linear", "url": "https://linear.app/careers"},
    {"name": "Airtable", "url": "https://job-boards.greenhouse.io/airtable"},
    {"name": "Postman", "url": "https://www.postman.com/company/careers/open-positions/"},
    {"name": "Grafana Labs", "url": "https://job-boards.greenhouse.io/grafanalabs"},
    {"name": "New Relic", "url": "https://job-boards.greenhouse.io/newrelic"},
    {"name": "Sumo Logic", "url": "https://job-boards.greenhouse.io/sumologic"},
    {"name": "CockroachDB", "url": "https://job-boards.greenhouse.io/cockroachlabs"},
    {"name": "PlanetScale", "url": "https://job-boards.greenhouse.io/planetscale"},
    {"name": "Supabase", "url": "https://jobs.ashbyhq.com/supabase"},
    {"name": "Render", "url": "https://jobs.ashbyhq.com/render"},
    {"name": "Figure AI", "url": "https://job-boards.greenhouse.io/figureai"},
    {"name": "Agility Robotics", "url": "https://www.agilityrobotics.com/careers"},
    {"name": "Wisk Aero", "url": "https://wisk.aero/careers/"},
    {"name": "Joby Aviation", "url": "https://www.jobyaviation.com/careers/"},
    {"name": "Archer Aviation", "url": "https://www.archer.com/careers"},
    {"name": "Astranis", "url": "https://job-boards.greenhouse.io/astranis"},
    {"name": "Planet Labs", "url": "https://job-boards.greenhouse.io/planetlabs"},
    {"name": "Rocket Lab", "url": "https://www.rocketlabusa.com/careers/positions/"},
    {"name": "Relativity Space", "url": "https://job-boards.greenhouse.io/relativity"},
    {"name": "Shield AI", "url": "https://shield.ai/careers/"},
    {"name": "CrowdStrike", "url": "https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers"},
    {"name": "Palo Alto Networks", "url": "https://jobs.paloaltonetworks.com/en/jobs/"},
    {"name": "Okta", "url": "https://job-boards.greenhouse.io/okta"},
    {"name": "Doppler", "url": "https://jobs.ashbyhq.com/doppler"},
    {"name": "Dropbox", "url": "https://job-boards.greenhouse.io/dropbox"},
    {"name": "Asana", "url": "https://job-boards.greenhouse.io/asana"},
    {"name": "Atlassian", "url": "https://www.atlassian.com/company/careers/all-jobs"},
    {"name": "Zoom", "url": "https://zoom.wd5.myworkdayjobs.com/Zoom"},
    {"name": "Box", "url": "https://www.box.com/careers"},
    {"name": "Webflow", "url": "https://job-boards.greenhouse.io/webflow"},
    {"name": "Figma", "url": "https://job-boards.greenhouse.io/figma"},
    {"name": "Canva", "url": "https://www.lifeatcanva.com/en/jobs/"},
    {"name": "Calendly", "url": "https://job-boards.greenhouse.io/calendly"},
    {"name": "Instacart", "url": "https://job-boards.greenhouse.io/instacart"},
    {"name": "Compass", "url": "https://www.compass.com/careers/"},
    {"name": "Zillow", "url": "https://www.zillow.com/careers/"},
    {"name": "Yelp", "url": "https://www.yelp.careers/us/en/search-results"},
    {"name": "Shopify", "url": "https://www.shopify.com/careers/search"},
    {"name": "Faire", "url": "https://www.faire.com/careers"},
    {"name": "Klaviyo", "url": "https://job-boards.greenhouse.io/klaviyo"},
    {"name": "Toast", "url": "https://job-boards.greenhouse.io/toast"},
    {"name": "Solana Labs", "url": "https://jobs.solana.com/"},
    {"name": "Verily", "url": "https://www.verily.com/careers/"},
    {"name": "10x Genomics", "url": "https://www.10xgenomics.com/careers"},
    {"name": "Astera Labs", "url": "https://job-boards.greenhouse.io/asteralabs"},
    {"name": "Recogni", "url": "https://recogni.com/careers/"},
    {"name": "d-Matrix", "url": "https://www.d-matrix.ai/careers/"},
    {"name": "Lightmatter", "url": "https://job-boards.greenhouse.io/lightmatter"},
    {"name": "Ayar Labs", "url": "https://ayarlabs.com/careers/"},
    {"name": "Esperanto Technologies", "url": "https://www.esperanto.ai/careers/"},
    {"name": "Mythic AI", "url": "https://mythic.ai/careers/"},
    {"name": "Rain AI", "url": "https://rain.ai/careers"},
    {"name": "Etched", "url": "https://www.etched.com/careers"},
    {"name": "FuriosaAI", "url": "https://www.furiosa.ai/careers/"},
    {"name": "Rebellions", "url": "https://www.rebellions.ai/careers"},
    {"name": "Mojo (Modular)", "url": "https://www.modular.com/company/careers"},
    {"name": "Discord", "url": "https://discord.com/careers"},
    {"name": "Brave Software", "url": "https://brave.com/careers/"},
    {"name": "Khan Academy", "url": "https://job-boards.greenhouse.io/khanacademy"},
    {"name": "Coursera", "url": "https://job-boards.greenhouse.io/coursera"},
    {"name": "Chegg", "url": "https://jobs.chegg.com/"},
    {"name": "Nutanix", "url": "https://www.nutanix.com/company/careers"},
    {"name": "Cohesity", "url": "https://www.cohesity.com/company/careers/"},
    {"name": "Rubrik", "url": "https://www.rubrik.com/company/careers"},
    {"name": "Veeva Systems", "url": "https://www.veeva.com/careers/"},
    {"name": "Workday", "url": "https://workday.wd5.myworkdayjobs.com/Workday"},
    {"name": "Pure Storage HQ", "url": "https://job-boards.greenhouse.io/purestorage"},
    {"name": "Roku", "url": "https://job-boards.greenhouse.io/roku"},
    {"name": "TiVo", "url": "https://www.tivo.com/careers"},
    {"name": "Ambarella", "url": "https://www.ambarella.com/about-us/careers/"},
    {"name": "MaxLinear", "url": "https://www.maxlinear.com/company/careers"},
    {"name": "Amplitude", "url": "https://job-boards.greenhouse.io/amplitude"},
    {"name": "Mixpanel", "url": "https://job-boards.greenhouse.io/mixpanel"},
    {"name": "Sigma Computing", "url": "https://job-boards.greenhouse.io/sigmacomputing"},
    {"name": "Fivetran", "url": "https://job-boards.greenhouse.io/fivetran"},
    {"name": "Hex", "url": "https://jobs.ashbyhq.com/hex"},
    {"name": "Hightouch", "url": "https://jobs.ashbyhq.com/hightouch"},
    {"name": "Temporal", "url": "https://job-boards.greenhouse.io/temporaltechnologies"},
    {"name": "Vanta", "url": "https://jobs.ashbyhq.com/vanta"},
    {"name": "Honeycomb", "url": "https://job-boards.greenhouse.io/honeycomb"},
    {"name": "Cribl", "url": "https://job-boards.greenhouse.io/cribl"},
    {"name": "CircleCI", "url": "https://job-boards.greenhouse.io/circleci"},
    {"name": "ClickHouse", "url": "https://job-boards.greenhouse.io/clickhouse"},
    {"name": "SingleStore", "url": "https://job-boards.greenhouse.io/singlestore"},
    {"name": "Yugabyte", "url": "https://job-boards.greenhouse.io/yugabyte"},
    {"name": "Fireworks AI", "url": "https://jobs.ashbyhq.com/fireworks"},
    {"name": "Baseten", "url": "https://jobs.ashbyhq.com/baseten"},
    {"name": "CoreWeave", "url": "https://job-boards.greenhouse.io/coreweave"},
    {"name": "Sierra", "url": "https://jobs.ashbyhq.com/sierra"},
    {"name": "Decagon", "url": "https://jobs.ashbyhq.com/decagon"},
    {"name": "Cresta", "url": "https://job-boards.greenhouse.io/cresta"},
    {"name": "Cognition AI", "url": "https://jobs.ashbyhq.com/cognition"},
    {"name": "Luma AI", "url": "https://jobs.ashbyhq.com/lumaai"},
    {"name": "Pika", "url": "https://jobs.ashbyhq.com/pika"},
    {"name": "Suno", "url": "https://jobs.ashbyhq.com/suno"},
    {"name": "ElevenLabs", "url": "https://jobs.ashbyhq.com/elevenlabs"},
    {"name": "Stripe Issuing", "url": "https://stripe.com/jobs/search"},
    {"name": "Rippling", "url": "https://www.rippling.com/careers/open-roles"},
    {"name": "Unit", "url": "https://jobs.ashbyhq.com/unit"},
    {"name": "Column", "url": "https://jobs.ashbyhq.com/column"},
    {"name": "Lithic", "url": "https://job-boards.greenhouse.io/lithic"},
    {"name": "Sardine", "url": "https://jobs.ashbyhq.com/sardine"},
    {"name": "Collaborative Robotics", "url": "https://jobs.ashbyhq.com/cobot"},
    {"name": "Path Robotics", "url": "https://job-boards.greenhouse.io/pathrobotics"},
    {"name": "Waabi", "url": "https://jobs.lever.co/waabi"},
    {"name": "Varda Space", "url": "https://job-boards.greenhouse.io/vardaspace"},
    {"name": "Muon Space", "url": "https://job-boards.greenhouse.io/muonspace"},
    {"name": "Sourcegraph", "url": "https://job-boards.greenhouse.io/sourcegraph91"},
    {"name": "Sourcegraph Cody", "url": "https://job-boards.greenhouse.io/sourcegraph91"},
    {"name": "Mintlify", "url": "https://jobs.ashbyhq.com/mintlify"},
    {"name": "BrowserStack", "url": "https://www.browserstack.com/careers"},
    {"name": "Groww", "url": "https://groww.in/careers"},
    {"name": "BigBasket", "url": "https://careers.bigbasket.com/"},
    {"name": "MakeMyTrip", "url": "https://www.makemytrip.com/careers/"},
    {"name": "Flipkart", "url": "https://www.flipkartcareers.com/#!/joblist"},
    {"name": "BharatPe", "url": "https://bharatpe.com/career"},
    {"name": "Zoho", "url": "https://www.zoho.com/careers/"},
    {"name": "Ola Electric", "url": "https://www.olaelectric.com/careers"},
    {"name": "AngelOne", "url": "https://www.angelone.in/careers"},
    {"name": "Freshworks", "url": "https://jobs.lever.co/freshworks"},
    {"name": "Meesho", "url": "https://www.meesho.io/jobs"},
    {"name": "Nykaa", "url": "https://www.nykaa.com/careers"},
    {"name": "Atlan", "url": "https://atlan.com/careers/"},
    {"name": "Clevertap", "url": "https://clevertap.com/careers/"},
    {"name": "CRED", "url": "https://careers.cred.club/"},
    {"name": "Unacademy", "url": "https://unacademy.com/careers"},
    {"name": "Cashfree", "url": "https://www.cashfree.com/careers"},
    {"name": "Jupiter", "url": "https://jupiter.money/careers"},
    {"name": "Blinkit", "url": "https://blinkit.com/careers"},
    {"name": "InMobi", "url": "https://www.inmobi.com/company/careers"},
    {"name": "Delhivery", "url": "https://www.delhivery.com/careers"},
    {"name": "PhonePe", "url": "https://boards.greenhouse.io/phonepe"},
    {"name": "Porter", "url": "https://porter.in/careers"},
    {"name": "Setu", "url": "https://setu.co/careers"},
    {"name": "1mg", "url": "https://www.1mg.com/jobs"},
    {"name": "upGrad", "url": "https://www.upgrad.com/careers/"},
    {"name": "Swiggy", "url": "https://careers.swiggy.com/"},
    {"name": "ClearTax", "url": "https://cleartax.in/s/careers"},
    {"name": "Chargebee", "url": "https://careers.chargebee.com/jobs/"},
    {"name": "Turso", "url": "https://turso.tech/careers"},
    {"name": "Neon", "url": "https://jobs.ashbyhq.com/neon"},
    {"name": "Deno", "url": "https://jobs.ashbyhq.com/deno"},
    {"name": "Railway", "url": "https://railway.app/careers"},
    {"name": "Liveblocks", "url": "https://liveblocks.io/careers"},
    {"name": "Notion", "url": "https://www.notion.so/careers"},
    {"name": "Wingify", "url": "https://wingify.com/careers/"},
    {"name": "Together AI", "url": "https://www.together.ai/careers"},
    {"name": "Replit", "url": "https://replit.com/site/careers"},
    {"name": "Licious", "url": "https://www.licious.in/careers"},
    {"name": "Mistral AI", "url": "https://jobs.ashbyhq.com/mistral"},
    {"name": "Stability AI", "url": "https://stability.ai/careers"},
    {"name": "Inflection AI", "url": "https://inflection.ai/careers"},
    {"name": "CoinDCX", "url": "https://careers.coindcx.com/"},
    {"name": "Cursor", "url": "https://www.cursor.com/careers"},
    {"name": "Planetscale", "url": "https://job-boards.greenhouse.io/planetscale"},
    {"name": "Slice", "url": "https://sliceit.com/careers"},
    {"name": "Codeium", "url": "https://codeium.com/careers"},
    {"name": "Inngest", "url": "https://www.inngest.com/careers"},
    {"name": "PostHog", "url": "https://posthog.com/careers"},
    {"name": "Clerk", "url": "https://jobs.ashbyhq.com/clerk"},
    {"name": "Novu", "url": "https://jobs.ashbyhq.com/novu"},
    {"name": "Tailscale", "url": "https://tailscale.com/careers"},
    {"name": "WorkOS", "url": "https://jobs.ashbyhq.com/workos"},
    {"name": "Resend", "url": "https://jobs.ashbyhq.com/resend"},
    {"name": "Cal.com", "url": "https://jobs.ashbyhq.com/calcom"},
    {"name": "Databricks (GH)", "url": "https://job-boards.greenhouse.io/databricks"},
    {"name": "Qdrant", "url": "https://jobs.ashbyhq.com/qdrant"},
    {"name": "Upstash", "url": "https://jobs.ashbyhq.com/upstash"},
    {"name": "Runway ML", "url": "https://runwayml.com/careers"},
    {"name": "Replicate", "url": "https://jobs.ashbyhq.com/replicate"},
    {"name": "Fly.io", "url": "https://fly.io/jobs"},
    {"name": "Typeform", "url": "https://job-boards.greenhouse.io/typeform"},
    {"name": "HubSpot", "url": "https://www.hubspot.com/jobs"},
    {"name": "Moonshot AI", "url": "https://moonshot.ai/careers"},
    {"name": "Arc", "url": "https://jobs.ashbyhq.com/arc"},
    {"name": "Twitch", "url": "https://job-boards.greenhouse.io/twitch"},
    {"name": "Intercom", "url": "https://www.intercom.com/careers"},
    {"name": "Roblox", "url": "https://job-boards.greenhouse.io/roblox"},
    {"name": "Jam.dev", "url": "https://jobs.ashbyhq.com/jam"},
    {"name": "Trigger.dev", "url": "https://trigger.dev/careers"},
    {"name": "Milvus / Zilliz", "url": "https://zilliz.com/careers"},
    {"name": "Cycle", "url": "https://jobs.ashbyhq.com/cycle"},
    {"name": "Rows", "url": "https://jobs.ashbyhq.com/rows"},
    {"name": "Duolingo", "url": "https://job-boards.greenhouse.io/duolingo"},
    {"name": "Chroma", "url": "https://trychroma.com/jobs"},
    {"name": "Wundergraph", "url": "https://wundergraph.com/careers"},
    {"name": "Zendesk", "url": "https://jobs.zendesk.com/"},
    {"name": "Loops", "url": "https://loops.so/careers"},
    {"name": "Ema", "url": "https://jobs.ashbyhq.com/ema"},
    {"name": "Applied Intuition", "url": "https://jobs.ashbyhq.com/appliedintuition"},
    {"name": "Supa Health", "url": "https://jobs.ashbyhq.com/supa"},
    {"name": "Coactive AI", "url": "https://jobs.ashbyhq.com/coactive"},
    {"name": "Anyscale", "url": "https://jobs.ashbyhq.com/anyscale"},
    {"name": "Graphcore", "url": "https://job-boards.greenhouse.io/graphcore"},
    {"name": "Cartesia", "url": "https://jobs.ashbyhq.com/cartesia"},
    {"name": "Samsara", "url": "https://jobs.ashbyhq.com/samsara"},
    {"name": "Linear (Ashby)", "url": "https://jobs.ashbyhq.com/linear"},
    {"name": "Spinny", "url": "https://www.spinny.com/careers/"},
    {"name": "LogRocket", "url": "https://jobs.lever.co/logrocket"},
    {"name": "Pocket FM", "url": "https://jobs.ashbyhq.com/pocketfm"},
    {"name": "Car24", "url": "https://www.cars24.com/careers/"},
    {"name": "Postman (GH)", "url": "https://job-boards.greenhouse.io/postman"},
    {"name": "Kuku FM", "url": "https://kukufm.com/careers"},
    {"name": "InMobi (GH)", "url": "https://job-boards.greenhouse.io/inmobi"},
    {"name": "Classplus", "url": "https://classplusapp.com/careers"},
    {"name": "Plum", "url": "https://jobs.ashbyhq.com/plum"},
    {"name": "Zepto", "url": "https://www.zeptonow.com/careers"},
    {"name": "DeHaat", "url": "https://agrevolution.in/careers"},
    {"name": "GitLab (GH)", "url": "https://job-boards.greenhouse.io/gitlab"},
    {"name": "Lattice", "url": "https://job-boards.greenhouse.io/lattice"},
    {"name": "Wiz", "url": "https://jobs.ashbyhq.com/wiz"},
    {"name": "Abnormal Security", "url": "https://jobs.ashbyhq.com/abnormalsecurity"},
    {"name": "Buildkite", "url": "https://job-boards.greenhouse.io/buildkite"},
    {"name": "Cyera", "url": "https://jobs.ashbyhq.com/cyera"},
    {"name": "Pitch", "url": "https://jobs.ashbyhq.com/pitch"},
    {"name": "Framers", "url": "https://jobs.ashbyhq.com/framer"},
    {"name": "Rive", "url": "https://jobs.ashbyhq.com/rive"},
    {"name": "Monzo", "url": "https://job-boards.greenhouse.io/monzo"},
    {"name": "Descript", "url": "https://jobs.ashbyhq.com/descript"},
    {"name": "HeyGen", "url": "https://jobs.ashbyhq.com/heygen"},
    {"name": "Synthesia", "url": "https://jobs.ashbyhq.com/synthesia"},
    {"name": "LaunchDarkly", "url": "https://job-boards.greenhouse.io/launchdarkly"},
    {"name": "Wrike", "url": "https://job-boards.greenhouse.io/wrike"},
    {"name": "Netlify (GH)", "url": "https://job-boards.greenhouse.io/netlify"},
    {"name": "Netskope", "url": "https://job-boards.greenhouse.io/netskope"},
    {"name": "N26", "url": "https://job-boards.greenhouse.io/n26"},

    # ================ WORKDAY COMPANIES (auto-merged) ================
    {"name": "2020Companies Workday (external_careers)", "url": "https://2020companies.wd1.myworkdayjobs.com/external_careers"},
    {"name": "23Andme Workday (23)", "url": "https://23andme.wd5.myworkdayjobs.com/23"},
    {"name": "4Flow Workday (4flow)", "url": "https://4flow.wd3.myworkdayjobs.com/4flow"},
    {"name": "7Eleven Workday (7eleven)", "url": "https://7eleven.wd3.myworkdayjobs.com/7eleven"},
    {"name": "7Eleven Workday (7elevenv2)", "url": "https://7eleven.wd3.myworkdayjobs.com/7elevenv2"},
    {"name": "8X8Inc Workday (8x8_external_careers)", "url": "https://8x8inc.wd5.myworkdayjobs.com/8x8_external_careers"},
    {"name": "9Dot Workday (ofl_careers)", "url": "https://9dot.wd503.myworkdayjobs.com/ofl_careers"},
    {"name": "9Dot Workday (ofy_careers)", "url": "https://9dot.wd503.myworkdayjobs.com/ofy_careers"},
    {"name": "9Dot Workday (pie-la_careers)", "url": "https://9dot.wd503.myworkdayjobs.com/pie-la_careers"},
    {"name": "9Dot Workday (pie_careers)", "url": "https://9dot.wd503.myworkdayjobs.com/pie_careers"},
    {"name": "9Dot Workday (pmg_careers)", "url": "https://9dot.wd503.myworkdayjobs.com/pmg_careers"},
    {"name": "9Dot Workday (pse)", "url": "https://9dot.wd503.myworkdayjobs.com/pse"},
    {"name": "9Dot Workday (skyrocket)", "url": "https://9dot.wd503.myworkdayjobs.com/skyrocket"},
    {"name": "A1Group Workday (a1_jobs)", "url": "https://a1group.wd3.myworkdayjobs.com/a1_jobs"},
    {"name": "A1Group Workday (a1_jobs_at)", "url": "https://a1group.wd3.myworkdayjobs.com/a1_jobs_at"},
    {"name": "A1Group Workday (a1_jobs_bg)", "url": "https://a1group.wd3.myworkdayjobs.com/a1_jobs_bg"},
    {"name": "A1Group Workday (a1_jobs_cr)", "url": "https://a1group.wd3.myworkdayjobs.com/a1_jobs_cr"},
    {"name": "A1Group Workday (a1_jobs_mk)", "url": "https://a1group.wd3.myworkdayjobs.com/a1_jobs_mk"},
    {"name": "A1Group Workday (a1_jobs_sl)", "url": "https://a1group.wd3.myworkdayjobs.com/a1_jobs_sl"},
    {"name": "A1Group Workday (a1_jobs_sr)", "url": "https://a1group.wd3.myworkdayjobs.com/a1_jobs_sr"},
    {"name": "A1Group Workday (karriere_at)", "url": "https://a1group.wd3.myworkdayjobs.com/karriere_at"},
    {"name": "A1Group Workday (towers_jobs)", "url": "https://a1group.wd3.myworkdayjobs.com/towers_jobs"},
    {"name": "Aaaie Workday (csaacareers)", "url": "https://aaaie.wd1.myworkdayjobs.com/csaacareers"},
    {"name": "Aaaie Workday (csaacareers2)", "url": "https://aaaie.wd1.myworkdayjobs.com/csaacareers2"},
    {"name": "Aaaie Workday (csaacareersexecutives)", "url": "https://aaaie.wd1.myworkdayjobs.com/csaacareersexecutives"},
    {"name": "Aaamidatlantic Workday (aaamidatlantic)", "url": "https://aaamidatlantic.wd5.myworkdayjobs.com/aaamidatlantic"},
    {"name": "Aaamidatlantic Workday (external_career_ase)", "url": "https://aaamidatlantic.wd5.myworkdayjobs.com/external_career_ase"},
    {"name": "Aafp Workday (aafp_careers)", "url": "https://aafp.wd5.myworkdayjobs.com/aafp_careers"},
    {"name": "Aah Workday (external)", "url": "https://aah.wd5.myworkdayjobs.com/external"},
    {"name": "Aah Workday (scotland-external)", "url": "https://aah.wd5.myworkdayjobs.com/scotland-external"},
    {"name": "Aalto Workday (aalto)", "url": "https://aalto.wd3.myworkdayjobs.com/aalto"},
    {"name": "Aalto Workday (privatejobposting)", "url": "https://aalto.wd3.myworkdayjobs.com/privatejobposting"},
    {"name": "Aalto Workday (tuntiopettajapooli)", "url": "https://aalto.wd3.myworkdayjobs.com/tuntiopettajapooli"},
    {"name": "Aamc Workday (aamc)", "url": "https://aamc.wd5.myworkdayjobs.com/aamc"},
    {"name": "Aareon Workday (aareon)", "url": "https://aareon.wd103.myworkdayjobs.com/aareon"},
    {"name": "Aba Workday (aba)", "url": "https://aba.wd1.myworkdayjobs.com/aba"},
    {"name": "Abb Workday (external_career_page)", "url": "https://abb.wd3.myworkdayjobs.com/external_career_page"},
    {"name": "Abbluecross Workday (careers)", "url": "https://abbluecross.wd3.myworkdayjobs.com/careers"},
    {"name": "Abbott Workday (abbottcareers)", "url": "https://abbott.wd5.myworkdayjobs.com/abbottcareers"},
    {"name": "Abbott Workday (abbottcareers2)", "url": "https://abbott.wd5.myworkdayjobs.com/abbottcareers2"},
    {"name": "Abbott Workday (agency)", "url": "https://abbott.wd5.myworkdayjobs.com/agency"},
    {"name": "Abbott Workday (equest)", "url": "https://abbott.wd5.myworkdayjobs.com/equest"},
    {"name": "Abcfinancial Workday (abcfinancialservices)", "url": "https://abcfinancial.wd5.myworkdayjobs.com/abcfinancialservices"},
    {"name": "Abcsupply Workday (abcsupplycareers)", "url": "https://abcsupply.wd1.myworkdayjobs.com/abcsupplycareers"},
    {"name": "Abcsupply Workday (acmcareers)", "url": "https://abcsupply.wd1.myworkdayjobs.com/acmcareers"},
    {"name": "Abcsupply Workday (canadaabcsupplycareers)", "url": "https://abcsupply.wd1.myworkdayjobs.com/canadaabcsupplycareers"},
    {"name": "Abcsupply Workday (mulehidecareers)", "url": "https://abcsupply.wd1.myworkdayjobs.com/mulehidecareers"},
    {"name": "Abglobal Workday (abcampuscareers)", "url": "https://abglobal.wd1.myworkdayjobs.com/abcampuscareers"},
    {"name": "Abglobal Workday (alliancebernsteincareers)", "url": "https://abglobal.wd1.myworkdayjobs.com/alliancebernsteincareers"},
    {"name": "Abinbev Workday (eur)", "url": "https://abinbev.wd1.myworkdayjobs.com/eur"},
    {"name": "Abrdn Workday (abrdn)", "url": "https://abrdn.wd3.myworkdayjobs.com/abrdn"},
    {"name": "Abrdn Workday (finimize)", "url": "https://abrdn.wd3.myworkdayjobs.com/finimize"},
    {"name": "Absa Workday (absacareersite)", "url": "https://absa.wd3.myworkdayjobs.com/absacareersite"},
    {"name": "Absa Workday (nbc_careers)", "url": "https://absa.wd3.myworkdayjobs.com/nbc_careers"},
    {"name": "Acaciumgroup Workday (favorite_external_careers)", "url": "https://acaciumgroup.wd3.myworkdayjobs.com/favorite_external_careers"},
    {"name": "Academy Workday (careers)", "url": "https://academy.wd1.myworkdayjobs.com/careers"},
    {"name": "Academyofartuniversity Workday (academy_of_art)", "url": "https://academyofartuniversity.wd5.myworkdayjobs.com/academy_of_art"},
    {"name": "Academyofartuniversity Workday (academy_of_art_federal_work_study)", "url": "https://academyofartuniversity.wd5.myworkdayjobs.com/academy_of_art_federal_work_study"},
    {"name": "Accelentertainment Workday (accelentertainment_careers)", "url": "https://accelentertainment.wd12.myworkdayjobs.com/accelentertainment_careers"},
    {"name": "Accelentertainment Workday (bulldoggamingcareers)", "url": "https://accelentertainment.wd12.myworkdayjobs.com/bulldoggamingcareers"},
    {"name": "Accelentertainment Workday (centurygamingmontanacareers)", "url": "https://accelentertainment.wd12.myworkdayjobs.com/centurygamingmontanacareers"},
    {"name": "Accelentertainment Workday (centurygamingnevadacareers)", "url": "https://accelentertainment.wd12.myworkdayjobs.com/centurygamingnevadacareers"},
    {"name": "Accelentertainment Workday (grandvisiongamingcareers)", "url": "https://accelentertainment.wd12.myworkdayjobs.com/grandvisiongamingcareers"},
    {"name": "Accelentertainment Workday (toucangamingcareers)", "url": "https://accelentertainment.wd12.myworkdayjobs.com/toucangamingcareers"},
    {"name": "Accelentertainment Workday (yellowstonecasinocareers)", "url": "https://accelentertainment.wd12.myworkdayjobs.com/yellowstonecasinocareers"},
    {"name": "Accelleron Workday (accelleron)", "url": "https://accelleron.wd3.myworkdayjobs.com/accelleron"},
    {"name": "Accelleron Workday (omt)", "url": "https://accelleron.wd3.myworkdayjobs.com/omt"},
    {"name": "Accellglobal Workday (careers)", "url": "https://accellglobal.wd103.myworkdayjobs.com/careers"},
    {"name": "Accelya Workday (careers)", "url": "https://accelya.wd103.myworkdayjobs.com/careers"},
    {"name": "Accenture Workday (accenturecareers)", "url": "https://accenture.wd103.myworkdayjobs.com/accenturecareers"},
    {"name": "Accenture Workday (avanadecareers)", "url": "https://accenture.wd103.myworkdayjobs.com/avanadecareers"},
    {"name": "Accesscu Workday (access_careers)", "url": "https://accesscu.wd3.myworkdayjobs.com/access_careers"},
    {"name": "Accesscu Workday (brio_careers)", "url": "https://accesscu.wd3.myworkdayjobs.com/brio_careers"},
    {"name": "Acciona Workday (acciona_employment_channel)", "url": "https://acciona.wd3.myworkdayjobs.com/acciona_employment_channel"},
    {"name": "Accuray Workday (external)", "url": "https://accuray.wd5.myworkdayjobs.com/external"},
    {"name": "Ace Workday (careers)", "url": "https://ace.wd5.myworkdayjobs.com/careers"},
    {"name": "Acehardware Workday (ahhs_external)", "url": "https://acehardware.wd1.myworkdayjobs.com/ahhs_external"},
    {"name": "Acehardware Workday (aih_external)", "url": "https://acehardware.wd1.myworkdayjobs.com/aih_external"},
    {"name": "Acehardware Workday (arg_external)", "url": "https://acehardware.wd1.myworkdayjobs.com/arg_external"},
    {"name": "Acehardware Workday (ejdexternal)", "url": "https://acehardware.wd1.myworkdayjobs.com/ejdexternal"},
    {"name": "Acehardware Workday (external)", "url": "https://acehardware.wd1.myworkdayjobs.com/external"},
    {"name": "Acehardware Workday (gla_external)", "url": "https://acehardware.wd1.myworkdayjobs.com/gla_external"},
    {"name": "Acehardware Workday (westlake_external)", "url": "https://acehardware.wd1.myworkdayjobs.com/westlake_external"},
    {"name": "Acelero Workday (acelerolearningcareers)", "url": "https://acelero.wd1.myworkdayjobs.com/acelerolearningcareers"},
    {"name": "Acelero Workday (shineearlylearningcareers)", "url": "https://acelero.wd1.myworkdayjobs.com/shineearlylearningcareers"},
    {"name": "Acendahealth Workday (acendahealth)", "url": "https://acendahealth.wd501.myworkdayjobs.com/acendahealth"},
    {"name": "Acendahealth Workday (broadbean_external)", "url": "https://acendahealth.wd501.myworkdayjobs.com/broadbean_external"},
    {"name": "Acg Workday (careers)", "url": "https://acg.wd1.myworkdayjobs.com/careers"},
    {"name": "Acrisure Workday (acrisure)", "url": "https://acrisure.wd1.myworkdayjobs.com/acrisure"},
    {"name": "Acronis Workday (acronis_careers)", "url": "https://acronis.wd502.myworkdayjobs.com/acronis_careers"},
    {"name": "Acrt Workday (acrt_careers)", "url": "https://acrt.wd1.myworkdayjobs.com/acrt_careers"},
    {"name": "Acrt Workday (careerbuilder)", "url": "https://acrt.wd1.myworkdayjobs.com/careerbuilder"},
    {"name": "Acrt Workday (enviroscienceinc)", "url": "https://acrt.wd1.myworkdayjobs.com/enviroscienceinc"},
    {"name": "Acs Workday (acscan)", "url": "https://acs.wd5.myworkdayjobs.com/acscan"},
    {"name": "Acs Workday (acscareers)", "url": "https://acs.wd5.myworkdayjobs.com/acscareers"},
    {"name": "Acu Workday (acucareers)", "url": "https://acu.wd108.myworkdayjobs.com/acucareers"},
    {"name": "Acuityinternational Workday (external)", "url": "https://acuityinternational.wd5.myworkdayjobs.com/external"},
    {"name": "Acushnetgolf Workday (acu)", "url": "https://acushnetgolf.wd12.myworkdayjobs.com/acu"},
    {"name": "Acxiomllc Workday (acxiom_apac_agency)", "url": "https://acxiomllc.wd5.myworkdayjobs.com/acxiom_apac_agency"},
    {"name": "Acxiomllc Workday (acxiomchn)", "url": "https://acxiomllc.wd5.myworkdayjobs.com/acxiomchn"},
    {"name": "Acxiomllc Workday (acxiommex)", "url": "https://acxiomllc.wd5.myworkdayjobs.com/acxiommex"},
    {"name": "Acxiomllc Workday (acxiompol)", "url": "https://acxiomllc.wd5.myworkdayjobs.com/acxiompol"},
    {"name": "Acxiomllc Workday (acxiomuk)", "url": "https://acxiomllc.wd5.myworkdayjobs.com/acxiomuk"},
    {"name": "Acxiomllc Workday (acxiomusa)", "url": "https://acxiomllc.wd5.myworkdayjobs.com/acxiomusa"},
    {"name": "Adams Workday (asu)", "url": "https://adams.wd1.myworkdayjobs.com/asu"},
    {"name": "Adec Workday (a-dec)", "url": "https://adec.wd5.myworkdayjobs.com/a-dec"},
    {"name": "Adient Workday (broadbean_external)", "url": "https://adient.wd3.myworkdayjobs.com/broadbean_external"},
    {"name": "Adient Workday (equest)", "url": "https://adient.wd3.myworkdayjobs.com/equest"},
    {"name": "Adient Workday (external)", "url": "https://adient.wd3.myworkdayjobs.com/external"},
    {"name": "Admiralbeverage Workday (external)", "url": "https://admiralbeverage.wd5.myworkdayjobs.com/external"},
    {"name": "Admiralbeverage Workday (teton_external_career)", "url": "https://admiralbeverage.wd5.myworkdayjobs.com/teton_external_career"},
    {"name": "Adobe Workday (external_university)", "url": "https://adobe.wd5.myworkdayjobs.com/external_university"},
    {"name": "Adtran Workday (adtran)", "url": "https://adtran.wd3.myworkdayjobs.com/adtran"},
    {"name": "Adtran Workday (ans)", "url": "https://adtran.wd3.myworkdayjobs.com/ans"},
    {"name": "Adtran Workday (osa)", "url": "https://adtran.wd3.myworkdayjobs.com/osa"},
    {"name": "Advanceauto Workday (advanceexternalcareers)", "url": "https://advanceauto.wd5.myworkdayjobs.com/advanceexternalcareers"},
    {"name": "Advantech Workday (external)", "url": "https://advantech.wd3.myworkdayjobs.com/external"},
    {"name": "Adventhealth Workday (ah_external_career_site)", "url": "https://adventhealth.wd12.myworkdayjobs.com/ah_external_career_site"},
    {"name": "Adventisthealthcare Workday (adventisthealthcarecareers)", "url": "https://adventisthealthcare.wd1.myworkdayjobs.com/adventisthealthcarecareers"},
    {"name": "Advisorgroup Workday (advisor_career_site)", "url": "https://advisorgroup.wd1.myworkdayjobs.com/advisor_career_site"},
    {"name": "Advisorgroup Workday (career_profile)", "url": "https://advisorgroup.wd1.myworkdayjobs.com/career_profile"},
    {"name": "Aeci Workday (aeci)", "url": "https://aeci.wd1.myworkdayjobs.com/aeci"},
    {"name": "Aegislondon Workday (careers)", "url": "https://aegislondon.wd3.myworkdayjobs.com/careers"},
    {"name": "Aenetworks Workday (ae-careers)", "url": "https://aenetworks.wd1.myworkdayjobs.com/ae-careers"},
    {"name": "Aep Workday (aepcareersite)", "url": "https://aep.wd1.myworkdayjobs.com/aepcareersite"},
    {"name": "Aer Workday (aer)", "url": "https://aer.wd3.myworkdayjobs.com/aer"},
    {"name": "Aero Workday (external)", "url": "https://aero.wd5.myworkdayjobs.com/external"},
    {"name": "Aero Workday (urr)", "url": "https://aero.wd5.myworkdayjobs.com/urr"},
    {"name": "Aersale Workday (aer1001)", "url": "https://aersale.wd5.myworkdayjobs.com/aer1001"},
    {"name": "Aes Workday (aes_andes)", "url": "https://aes.wd1.myworkdayjobs.com/aes_andes"},
    {"name": "Aes Workday (aes_argentina)", "url": "https://aes.wd1.myworkdayjobs.com/aes_argentina"},
    {"name": "Aes Workday (aes_asa)", "url": "https://aes.wd1.myworkdayjobs.com/aes_asa"},
    {"name": "Aes Workday (aes_brazil)", "url": "https://aes.wd1.myworkdayjobs.com/aes_brazil"},
    {"name": "Aes Workday (aes_chile)", "url": "https://aes.wd1.myworkdayjobs.com/aes_chile"},
    {"name": "Aes Workday (aes_clean_energy)", "url": "https://aes.wd1.myworkdayjobs.com/aes_clean_energy"},
    {"name": "Aes Workday (aes_colombia)", "url": "https://aes.wd1.myworkdayjobs.com/aes_colombia"},
    {"name": "Aes Workday (aes_dominicanrepublic)", "url": "https://aes.wd1.myworkdayjobs.com/aes_dominicanrepublic"},
    {"name": "Aes Workday (aes_elsalvador)", "url": "https://aes.wd1.myworkdayjobs.com/aes_elsalvador"},
    {"name": "Aes Workday (aes_eurasia)", "url": "https://aes.wd1.myworkdayjobs.com/aes_eurasia"},
    {"name": "Aes Workday (aes_mcac)", "url": "https://aes.wd1.myworkdayjobs.com/aes_mcac"},
    {"name": "Aes Workday (aes_mexico)", "url": "https://aes.wd1.myworkdayjobs.com/aes_mexico"},
    {"name": "Aes Workday (aes_panama)", "url": "https://aes.wd1.myworkdayjobs.com/aes_panama"},
    {"name": "Aes Workday (aes_puertorico)", "url": "https://aes.wd1.myworkdayjobs.com/aes_puertorico"},
    {"name": "Aes Workday (aes_us)", "url": "https://aes.wd1.myworkdayjobs.com/aes_us"},
    {"name": "Aes Workday (external_union_spa)", "url": "https://aes.wd1.myworkdayjobs.com/external_union_spa"},
    {"name": "Aes Workday (fluence)", "url": "https://aes.wd1.myworkdayjobs.com/fluence"},
    {"name": "Aesop Workday (aesopcareers)", "url": "https://aesop.wd3.myworkdayjobs.com/aesopcareers"},
    {"name": "Aesop Workday (broadbean_external)", "url": "https://aesop.wd3.myworkdayjobs.com/broadbean_external"},
    {"name": "Aett Workday (stax)", "url": "https://aett.wd3.myworkdayjobs.com/stax"},
    {"name": "Aett Workday (versent)", "url": "https://aett.wd3.myworkdayjobs.com/versent"},
    {"name": "Ag Workday (airbus)", "url": "https://ag.wd3.myworkdayjobs.com/airbus"},
    {"name": "Ag Workday (airbus_specific)", "url": "https://ag.wd3.myworkdayjobs.com/airbus_specific"},
    {"name": "Agcbio Workday (agcbio_careers)", "url": "https://agcbio.wd5.myworkdayjobs.com/agcbio_careers"},
    {"name": "Agecare Workday (agecare_careers_external)", "url": "https://agecare.wd10.myworkdayjobs.com/agecare_careers_external"},
    {"name": "Agecare Workday (albus_careers_external)", "url": "https://agecare.wd10.myworkdayjobs.com/albus_careers_external"},
    {"name": "Agf Workday (agf_careers)", "url": "https://agf.wd3.myworkdayjobs.com/agf_careers"},
    {"name": "Agf Workday (glassdoor_careers)", "url": "https://agf.wd3.myworkdayjobs.com/glassdoor_careers"},
    {"name": "Agf Workday (indeed_careers)", "url": "https://agf.wd3.myworkdayjobs.com/indeed_careers"},
    {"name": "Aggreko Workday (aggreko_careers_1)", "url": "https://aggreko.wd3.myworkdayjobs.com/aggreko_careers_1"},
    {"name": "Agilent Workday (agilent_careers)", "url": "https://agilent.wd5.myworkdayjobs.com/agilent_careers"},
    {"name": "Agilent Workday (agilent_student_careers)", "url": "https://agilent.wd5.myworkdayjobs.com/agilent_student_careers"},
    {"name": "Agilitas Workday (lmc)", "url": "https://agilitas.wd503.myworkdayjobs.com/lmc"},
    {"name": "Agilitas Workday (lmc-ga)", "url": "https://agilitas.wd503.myworkdayjobs.com/lmc-ga"},
    {"name": "Agiliti Workday (careers)", "url": "https://agiliti.wd5.myworkdayjobs.com/careers"},
    {"name": "Agiliti Workday (corporate)", "url": "https://agiliti.wd5.myworkdayjobs.com/corporate"},
    {"name": "Agiliti Workday (management)", "url": "https://agiliti.wd5.myworkdayjobs.com/management"},
    {"name": "Agiliti Workday (operations)", "url": "https://agiliti.wd5.myworkdayjobs.com/operations"},
    {"name": "Agiliti Workday (sales)", "url": "https://agiliti.wd5.myworkdayjobs.com/sales"},
    {"name": "Agiliti Workday (technicians)", "url": "https://agiliti.wd5.myworkdayjobs.com/technicians"},
    {"name": "Agilonhealth Workday (external)", "url": "https://agilonhealth.wd1.myworkdayjobs.com/external"},
    {"name": "Agl Workday (agl_recruitment)", "url": "https://agl.wd3.myworkdayjobs.com/agl_recruitment"},
    {"name": "Agl Workday (all_jobs)", "url": "https://agl.wd3.myworkdayjobs.com/all_jobs"},
    {"name": "Agloan Workday (americanagcredit)", "url": "https://agloan.wd5.myworkdayjobs.com/americanagcredit"},
    {"name": "Agoc Workday (external)", "url": "https://agoc.wd5.myworkdayjobs.com/external"},
    {"name": "Agp Workday (agp_careers)", "url": "https://agp.wd12.myworkdayjobs.com/agp_careers"},
    {"name": "Agrana Workday (careers)", "url": "https://agrana.wd3.myworkdayjobs.com/careers"},
    {"name": "Agreenspace Workday (global_express_career_site)", "url": "https://agreenspace.wd3.myworkdayjobs.com/global_express_career_site"},
    {"name": "Agropur Workday (agropur_careers)", "url": "https://agropur.wd3.myworkdayjobs.com/agropur_careers"},
    {"name": "Ahri Workday (ahri1)", "url": "https://ahri.wd3.myworkdayjobs.com/ahri1"},
    {"name": "Aia Workday (aia-digital)", "url": "https://aia.wd3.myworkdayjobs.com/aia-digital"},
    {"name": "Aia Workday (amplifyhealthexternal)", "url": "https://aia.wd3.myworkdayjobs.com/amplifyhealthexternal"},
    {"name": "Aia Workday (external)", "url": "https://aia.wd3.myworkdayjobs.com/external"},
    {"name": "Aig Workday (aig)", "url": "https://aig.wd1.myworkdayjobs.com/aig"},
    {"name": "Aig Workday (early_careers)", "url": "https://aig.wd1.myworkdayjobs.com/early_careers"},
    {"name": "Aig Workday (japan)", "url": "https://aig.wd1.myworkdayjobs.com/japan"},
    {"name": "Aig Workday (privatejobpostings)", "url": "https://aig.wd1.myworkdayjobs.com/privatejobpostings"},
    {"name": "Aimco Workday (aimcocareers)", "url": "https://aimco.wd10.myworkdayjobs.com/aimcocareers"},
    {"name": "Aimcodevco Workday (aimco)", "url": "https://aimcodevco.wd5.myworkdayjobs.com/aimco"},
    {"name": "Aims Workday (jobs)", "url": "https://aims.wd1.myworkdayjobs.com/jobs"},
    {"name": "Airasia Workday (careers)", "url": "https://airasia.wd3.myworkdayjobs.com/careers"},
    {"name": "Airliquidehr Workday (airgasexternalcareer)", "url": "https://airliquidehr.wd3.myworkdayjobs.com/airgasexternalcareer"},
    {"name": "Airliquidehr Workday (airliquideexternalcareer)", "url": "https://airliquidehr.wd3.myworkdayjobs.com/airliquideexternalcareer"},
    {"name": "Airproducts Workday (ap0001)", "url": "https://airproducts.wd5.myworkdayjobs.com/ap0001"},
    {"name": "Airproducts Workday (ap0003)", "url": "https://airproducts.wd5.myworkdayjobs.com/ap0003"},
    {"name": "Airtron Workday (airtron_careers)", "url": "https://airtron.wd108.myworkdayjobs.com/airtron_careers"},
    {"name": "Airzonecontrol Workday (external_career_site)", "url": "https://airzonecontrol.wd103.myworkdayjobs.com/external_career_site"},
    {"name": "Akamerica Workday (aka)", "url": "https://akamerica.wd501.myworkdayjobs.com/aka"},
    {"name": "Aksteel Workday (careers)", "url": "https://aksteel.wd1.myworkdayjobs.com/careers"},
    {"name": "Akumincorp Workday (adgcareers)", "url": "https://akumincorp.wd5.myworkdayjobs.com/adgcareers"},
    {"name": "Akumincorp Workday (akumincareers)", "url": "https://akumincorp.wd5.myworkdayjobs.com/akumincareers"},
    {"name": "Akumincorp Workday (elitecareers)", "url": "https://akumincorp.wd5.myworkdayjobs.com/elitecareers"},
    {"name": "Alamedacourts Workday (alamedacourts)", "url": "https://alamedacourts.wd5.myworkdayjobs.com/alamedacourts"},
    {"name": "Alantra Workday (alantra)", "url": "https://alantra.wd3.myworkdayjobs.com/alantra"},
    {"name": "Alaskacommunications Workday (alaska_communications)", "url": "https://alaskacommunications.wd5.myworkdayjobs.com/alaska_communications"},
    {"name": "Alation Workday (externalsite)", "url": "https://alation.wd503.myworkdayjobs.com/externalsite"},
    {"name": "Albanymed Workday (albany_med)", "url": "https://albanymed.wd5.myworkdayjobs.com/albany_med"},
    {"name": "Albanymed Workday (albany_med_system_career_site)", "url": "https://albanymed.wd5.myworkdayjobs.com/albany_med_system_career_site"},
    {"name": "Albemarle Workday (albcollegerecruiting)", "url": "https://albemarle.wd5.myworkdayjobs.com/albcollegerecruiting"},
    {"name": "Albemarle Workday (external)", "url": "https://albemarle.wd5.myworkdayjobs.com/external"},
    {"name": "Albemarle Workday (ketjenexternal)", "url": "https://albemarle.wd5.myworkdayjobs.com/ketjenexternal"},
    {"name": "Albertamotorassociation Workday (ama)", "url": "https://albertamotorassociation.wd3.myworkdayjobs.com/ama"},
    {"name": "Albertamotorassociation Workday (bridgewaterbank)", "url": "https://albertamotorassociation.wd3.myworkdayjobs.com/bridgewaterbank"},
    {"name": "Alcoa Workday (careers)", "url": "https://alcoa.wd5.myworkdayjobs.com/careers"},
    {"name": "Alcon Workday (careers_alcon)", "url": "https://alcon.wd5.myworkdayjobs.com/careers_alcon"},
    {"name": "Alcority Workday (nms)", "url": "https://alcority.wd1.myworkdayjobs.com/nms"},
    {"name": "Alcority Workday (salamander)", "url": "https://alcority.wd1.myworkdayjobs.com/salamander"},
    {"name": "Alcority Workday (traditionshealth)", "url": "https://alcority.wd1.myworkdayjobs.com/traditionshealth"},
    {"name": "Alcority Workday (westlawn)", "url": "https://alcority.wd1.myworkdayjobs.com/westlawn"},
    {"name": "Alcority Workday (williamsracing)", "url": "https://alcority.wd1.myworkdayjobs.com/williamsracing"},
    {"name": "Alegeus Workday (alegeus_external_careers)", "url": "https://alegeus.wd1.myworkdayjobs.com/alegeus_external_careers"},
    {"name": "Alfa Workday (alfa)", "url": "https://alfa.wd3.myworkdayjobs.com/alfa"},
    {"name": "Alfalaval Workday (alfa_laval_jobs)", "url": "https://alfalaval.wd3.myworkdayjobs.com/alfa_laval_jobs"},
    {"name": "Alfalaval Workday (framo_jobs)", "url": "https://alfalaval.wd3.myworkdayjobs.com/framo_jobs"},
    {"name": "Algonquincollege Workday (careeropportunities)", "url": "https://algonquincollege.wd3.myworkdayjobs.com/careeropportunities"},
    {"name": "Aliaserviziambientali Workday (multiutility_external_career_site)", "url": "https://aliaserviziambientali.wd103.myworkdayjobs.com/multiutility_external_career_site"},
    {"name": "Aliaxis Workday (aliaxis)", "url": "https://aliaxis.wd3.myworkdayjobs.com/aliaxis"},
    {"name": "Aliaxis Workday (aliaxis_apac)", "url": "https://aliaxis.wd3.myworkdayjobs.com/aliaxis_apac"},
    {"name": "Aliaxis Workday (aliaxis_emea)", "url": "https://aliaxis.wd3.myworkdayjobs.com/aliaxis_emea"},
    {"name": "Aliaxis Workday (aliaxis_indiaashirvad1)", "url": "https://aliaxis.wd3.myworkdayjobs.com/aliaxis_indiaashirvad1"},
    {"name": "Aliaxis Workday (aliaxis_latam_career_site)", "url": "https://aliaxis.wd3.myworkdayjobs.com/aliaxis_latam_career_site"},
    {"name": "Aliaxis Workday (canplas)", "url": "https://aliaxis.wd3.myworkdayjobs.com/canplas"},
    {"name": "Aliaxis Workday (hamiltonkent)", "url": "https://aliaxis.wd3.myworkdayjobs.com/hamiltonkent"},
    {"name": "Aliaxis Workday (ipex)", "url": "https://aliaxis.wd3.myworkdayjobs.com/ipex"},
    {"name": "Aliaxis Workday (silverlineplastics)", "url": "https://aliaxis.wd3.myworkdayjobs.com/silverlineplastics"},
    {"name": "Alight Workday (careers)", "url": "https://alight.wd5.myworkdayjobs.com/careers"},
    {"name": "Aligneddc Workday (aligneddc)", "url": "https://aligneddc.wd12.myworkdayjobs.com/aligneddc"},
    {"name": "Aligneddc Workday (odata)", "url": "https://aligneddc.wd12.myworkdayjobs.com/odata"},
    {"name": "Aligneddc Workday (odatasp)", "url": "https://aligneddc.wd12.myworkdayjobs.com/odatasp"},
    {"name": "Alignmenthealthcare Workday (ahc_external)", "url": "https://alignmenthealthcare.wd12.myworkdayjobs.com/ahc_external"},
    {"name": "Alirahealth Workday (alirahealth)", "url": "https://alirahealth.wd3.myworkdayjobs.com/alirahealth"},
    {"name": "Alkami Workday (alkami)", "url": "https://alkami.wd12.myworkdayjobs.com/alkami"},
    {"name": "Alkegen Workday (alkegen)", "url": "https://alkegen.wd5.myworkdayjobs.com/alkegen"},
    {"name": "Allcatclaims Workday (allcat_external)", "url": "https://allcatclaims.wd1.myworkdayjobs.com/allcat_external"},
    {"name": "Allegion Workday (careers)", "url": "https://allegion.wd5.myworkdayjobs.com/careers"},
    {"name": "Allegion Workday (careers_api)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_api"},
    {"name": "Allegion Workday (careers_bricard)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_bricard"},
    {"name": "Allegion Workday (careers_cisa)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_cisa"},
    {"name": "Allegion Workday (careers_gps)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_gps"},
    {"name": "Allegion Workday (careers_gps-germany)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_gps-germany"},
    {"name": "Allegion Workday (careers_gps_dutch)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_gps_dutch"},
    {"name": "Allegion Workday (careers_gps_poland)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_gps_poland"},
    {"name": "Allegion Workday (careers_normbau)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_normbau"},
    {"name": "Allegion Workday (careers_normbau_france)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_normbau_france"},
    {"name": "Allegion Workday (careers_normbau_germany)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_normbau_germany"},
    {"name": "Allegion Workday (careers_simonsvoss)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_simonsvoss"},
    {"name": "Allegion Workday (careers_simonsvoss_english)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_simonsvoss_english"},
    {"name": "Allegion Workday (careers_simonsvoss_french)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_simonsvoss_french"},
    {"name": "Allegion Workday (careers_simonsvoss_italian)", "url": "https://allegion.wd5.myworkdayjobs.com/careers_simonsvoss_italian"},
    {"name": "Allegion Workday (de_at_karriere_interflex)", "url": "https://allegion.wd5.myworkdayjobs.com/de_at_karriere_interflex"},
    {"name": "Allegion Workday (de_ch_karriere_interflex)", "url": "https://allegion.wd5.myworkdayjobs.com/de_ch_karriere_interflex"},
    {"name": "Allegion Workday (earlytalent_careers)", "url": "https://allegion.wd5.myworkdayjobs.com/earlytalent_careers"},
    {"name": "Allegion Workday (en_career_interflex)", "url": "https://allegion.wd5.myworkdayjobs.com/en_career_interflex"},
    {"name": "Allegion Workday (engineering_careers)", "url": "https://allegion.wd5.myworkdayjobs.com/engineering_careers"},
    {"name": "Allegion Workday (interflex_german)", "url": "https://allegion.wd5.myworkdayjobs.com/interflex_german"},
    {"name": "Allegion Workday (manufacturing_careers)", "url": "https://allegion.wd5.myworkdayjobs.com/manufacturing_careers"},
    {"name": "Allegion Workday (plano)", "url": "https://allegion.wd5.myworkdayjobs.com/plano"},
    {"name": "Allegion Workday (swe)", "url": "https://allegion.wd5.myworkdayjobs.com/swe"},
    {"name": "Allegromicro Workday (allegrocareers)", "url": "https://allegromicro.wd5.myworkdayjobs.com/allegrocareers"},
    {"name": "Alleima Workday (alleima-jobs)", "url": "https://alleima.wd3.myworkdayjobs.com/alleima-jobs"},
    {"name": "Alleima Workday (kanthal-jobs)", "url": "https://alleima.wd3.myworkdayjobs.com/kanthal-jobs"},
    {"name": "Allens Workday (allens)", "url": "https://allens.wd3.myworkdayjobs.com/allens"},
    {"name": "Allens Workday (confidential)", "url": "https://allens.wd3.myworkdayjobs.com/confidential"},
    {"name": "Alliance Workday (nissanjobs)", "url": "https://alliance.wd3.myworkdayjobs.com/nissanjobs"},
    {"name": "Alliance Workday (nissanprivateexternal)", "url": "https://alliance.wd3.myworkdayjobs.com/nissanprivateexternal"},
    {"name": "Alliance Workday (traineeprivateaccess)", "url": "https://alliance.wd3.myworkdayjobs.com/traineeprivateaccess"},
    {"name": "Alliancedata Workday (breadfinancial_india)", "url": "https://alliancedata.wd5.myworkdayjobs.com/breadfinancial_india"},
    {"name": "Alliancedata Workday (breadfinancial_private)", "url": "https://alliancedata.wd5.myworkdayjobs.com/breadfinancial_private"},
    {"name": "Alliancedata Workday (breadfinancial_us)", "url": "https://alliancedata.wd5.myworkdayjobs.com/breadfinancial_us"},
    {"name": "Alliancedata Workday (comenitybanks)", "url": "https://alliancedata.wd5.myworkdayjobs.com/comenitybanks"},
    {"name": "Allianceground Workday (agi_careers)", "url": "https://allianceground.wd1.myworkdayjobs.com/agi_careers"},
    {"name": "Alliancewd Workday (alpine-racing-careers)", "url": "https://alliancewd.wd3.myworkdayjobs.com/alpine-racing-careers"},
    {"name": "Alliancewd Workday (renault-group-careers)", "url": "https://alliancewd.wd3.myworkdayjobs.com/renault-group-careers"},
    {"name": "Alliantenergy Workday (alliant)", "url": "https://alliantenergy.wd1.myworkdayjobs.com/alliant"},
    {"name": "Alliantenergy Workday (travero)", "url": "https://alliantenergy.wd1.myworkdayjobs.com/travero"},
    {"name": "Allina Workday (external)", "url": "https://allina.wd5.myworkdayjobs.com/external"},
    {"name": "Allisontransmission Workday (ati-external)", "url": "https://allisontransmission.wd1.myworkdayjobs.com/ati-external"},
    {"name": "Allstate Workday (agent)", "url": "https://allstate.wd5.myworkdayjobs.com/agent"},
    {"name": "Allstate Workday (allstate_careers)", "url": "https://allstate.wd5.myworkdayjobs.com/allstate_careers"},
    {"name": "Allstate Workday (sourcing_event)", "url": "https://allstate.wd5.myworkdayjobs.com/sourcing_event"},
    {"name": "Alphawave Workday (alphawave_external)", "url": "https://alphawave.wd10.myworkdayjobs.com/alphawave_external"},
    {"name": "Alphia Workday (alphiacareers)", "url": "https://alphia.wd12.myworkdayjobs.com/alphiacareers"},
    {"name": "Alpinephysicians Workday (external)", "url": "https://alpinephysicians.wd1.myworkdayjobs.com/external"},
    {"name": "Alsacstjude Workday (careersalsacstjude)", "url": "https://alsacstjude.wd1.myworkdayjobs.com/careersalsacstjude"},
    {"name": "Alsglobal Workday (external)", "url": "https://alsglobal.wd103.myworkdayjobs.com/external"},
    {"name": "Alston Workday (discoveryattorneyprofessionalcareer)", "url": "https://alston.wd1.myworkdayjobs.com/discoveryattorneyprofessionalcareer"},
    {"name": "Alston Workday (externalcareer)", "url": "https://alston.wd1.myworkdayjobs.com/externalcareer"},
    {"name": "Altamed Workday (careers)", "url": "https://altamed.wd1.myworkdayjobs.com/careers"},
    {"name": "Altapm Workday (altacareers)", "url": "https://altapm.wd1.myworkdayjobs.com/altacareers"},
    {"name": "Altasciences Workday (careers)", "url": "https://altasciences.wd1.myworkdayjobs.com/careers"},
    {"name": "Altera Workday (altera)", "url": "https://altera.wd1.myworkdayjobs.com/altera"},
    {"name": "Alterra Workday (alpineaerotech)", "url": "https://alterra.wd1.myworkdayjobs.com/alpineaerotech"},
    {"name": "Alterra Workday (alterramountaincompany)", "url": "https://alterra.wd1.myworkdayjobs.com/alterramountaincompany"},
    {"name": "Alterra Workday (arapahoebasin)", "url": "https://alterra.wd1.myworkdayjobs.com/arapahoebasin"},
    {"name": "Alterra Workday (bigbearmountainresort)", "url": "https://alterra.wd1.myworkdayjobs.com/bigbearmountainresort"},
    {"name": "Alterra Workday (bluemountainresort)", "url": "https://alterra.wd1.myworkdayjobs.com/bluemountainresort"},
    {"name": "Alterra Workday (cmhheli-skiing)", "url": "https://alterra.wd1.myworkdayjobs.com/cmhheli-skiing"},
    {"name": "Alterra Workday (crystalmountain)", "url": "https://alterra.wd1.myworkdayjobs.com/crystalmountain"},
    {"name": "Alterra Workday (deervalleyresort)", "url": "https://alterra.wd1.myworkdayjobs.com/deervalleyresort"},
    {"name": "Alterra Workday (junemountain)", "url": "https://alterra.wd1.myworkdayjobs.com/junemountain"},
    {"name": "Alterra Workday (mammothmountain)", "url": "https://alterra.wd1.myworkdayjobs.com/mammothmountain"},
    {"name": "Alterra Workday (mikewiegelehelicopterskiing)", "url": "https://alterra.wd1.myworkdayjobs.com/mikewiegelehelicopterskiing"},
    {"name": "Alterra Workday (palisadestahoe)", "url": "https://alterra.wd1.myworkdayjobs.com/palisadestahoe"},
    {"name": "Alterra Workday (schweitzer)", "url": "https://alterra.wd1.myworkdayjobs.com/schweitzer"},
    {"name": "Alterra Workday (skibutlers)", "url": "https://alterra.wd1.myworkdayjobs.com/skibutlers"},
    {"name": "Alterra Workday (snowshoemountain)", "url": "https://alterra.wd1.myworkdayjobs.com/snowshoemountain"},
    {"name": "Alterra Workday (steamboatskiresort)", "url": "https://alterra.wd1.myworkdayjobs.com/steamboatskiresort"},
    {"name": "Alterra Workday (strattonmountain)", "url": "https://alterra.wd1.myworkdayjobs.com/strattonmountain"},
    {"name": "Alterra Workday (sugarbushresort)", "url": "https://alterra.wd1.myworkdayjobs.com/sugarbushresort"},
    {"name": "Alterra Workday (winterparkresort)", "url": "https://alterra.wd1.myworkdayjobs.com/winterparkresort"},
    {"name": "Alteryx Workday (alteryxcareers)", "url": "https://alteryx.wd108.myworkdayjobs.com/alteryxcareers"},
    {"name": "Alto Workday (alto_external_career_site)", "url": "https://alto.wd1.myworkdayjobs.com/alto_external_career_site"},
    {"name": "Alto Workday (fuzehealthcareersite)", "url": "https://alto.wd1.myworkdayjobs.com/fuzehealthcareersite"},
    {"name": "Alto Workday (private_posting_external_career_site)", "url": "https://alto.wd1.myworkdayjobs.com/private_posting_external_career_site"},
    {"name": "Altusgroup Workday (altusgroup)", "url": "https://altusgroup.wd3.myworkdayjobs.com/altusgroup"},
    {"name": "Alwayscompassionate Workday (achomecare)", "url": "https://alwayscompassionate.wd1.myworkdayjobs.com/achomecare"},
    {"name": "Alyeskapipe Workday (alyeska_pipeline_service_company)", "url": "https://alyeskapipe.wd12.myworkdayjobs.com/alyeska_pipeline_service_company"},
    {"name": "Amainc Workday (ama_careers)", "url": "https://amainc.wd12.myworkdayjobs.com/ama_careers"},
    {"name": "Amarillo Workday (amarillo_external_career_site)", "url": "https://amarillo.wd5.myworkdayjobs.com/amarillo_external_career_site"},
    {"name": "Ambarella Workday (ambarella)", "url": "https://ambarella.wd108.myworkdayjobs.com/ambarella"},
    {"name": "Ambgroup Workday (amb_ff)", "url": "https://ambgroup.wd1.myworkdayjobs.com/amb_ff"},
    {"name": "Ambgroup Workday (amb_group)", "url": "https://ambgroup.wd1.myworkdayjobs.com/amb_group"},
    {"name": "Ambgroup Workday (amb_west)", "url": "https://ambgroup.wd1.myworkdayjobs.com/amb_west"},
    {"name": "Ambgroup Workday (ambse)", "url": "https://ambgroup.wd1.myworkdayjobs.com/ambse"},
    {"name": "Ambgroup Workday (appl_only_site)", "url": "https://ambgroup.wd1.myworkdayjobs.com/appl_only_site"},
    {"name": "Ambgroup Workday (mbscareers)", "url": "https://ambgroup.wd1.myworkdayjobs.com/mbscareers"},
    {"name": "Ambgroup Workday (ranch_msgr)", "url": "https://ambgroup.wd1.myworkdayjobs.com/ranch_msgr"},
    {"name": "Ambgroup Workday (ranch_westcreek)", "url": "https://ambgroup.wd1.myworkdayjobs.com/ranch_westcreek"},
    {"name": "Amcn Workday (amcnetworks)", "url": "https://amcn.wd5.myworkdayjobs.com/amcnetworks"},
    {"name": "Amcor Workday (amcor_external_career_site)", "url": "https://amcor.wd5.myworkdayjobs.com/amcor_external_career_site"},
    {"name": "Ameren Workday (collegiate)", "url": "https://ameren.wd1.myworkdayjobs.com/collegiate"},
    {"name": "Ameren Workday (external)", "url": "https://ameren.wd1.myworkdayjobs.com/external"},
    {"name": "Ameresco Workday (ameresco)", "url": "https://ameresco.wd5.myworkdayjobs.com/ameresco"},
    {"name": "Americancentury Workday (americancenturyinvestments)", "url": "https://americancentury.wd5.myworkdayjobs.com/americancenturyinvestments"},
    {"name": "Americancentury Workday (avantiscareers)", "url": "https://americancentury.wd5.myworkdayjobs.com/avantiscareers"},
    {"name": "Americanfidelity Workday (external)", "url": "https://americanfidelity.wd5.myworkdayjobs.com/external"},
    {"name": "Americanglobal Workday (american_global_careers)", "url": "https://americanglobal.wd108.myworkdayjobs.com/american_global_careers"},
    {"name": "Americanredcross Workday (american_red_cross_careers)", "url": "https://americanredcross.wd1.myworkdayjobs.com/american_red_cross_careers"},
    {"name": "Americanregent Workday (american_regent_careers)", "url": "https://americanregent.wd1.myworkdayjobs.com/american_regent_careers"},
    {"name": "Amerilife Workday (crumpexternal)", "url": "https://amerilife.wd5.myworkdayjobs.com/crumpexternal"},
    {"name": "Amerilife Workday (external)", "url": "https://amerilife.wd5.myworkdayjobs.com/external"},
    {"name": "Amerilife Workday (saybrusexternal)", "url": "https://amerilife.wd5.myworkdayjobs.com/saybrusexternal"},
    {"name": "Ameriprise Workday (ameriprise)", "url": "https://ameriprise.wd5.myworkdayjobs.com/ameriprise"},
    {"name": "Amerisbank Workday (ameris_bank_careers)", "url": "https://amerisbank.wd108.myworkdayjobs.com/ameris_bank_careers"},
    {"name": "Amerivet Workday (amerivet)", "url": "https://amerivet.wd5.myworkdayjobs.com/amerivet"},
    {"name": "Amesconstruction Workday (ames)", "url": "https://amesconstruction.wd12.myworkdayjobs.com/ames"},
    {"name": "Amfam Workday (agent-staff)", "url": "https://amfam.wd1.myworkdayjobs.com/agent-staff"},
    {"name": "Amfam Workday (american_family_insurance_claims_services)", "url": "https://amfam.wd1.myworkdayjobs.com/american_family_insurance_claims_services"},
    {"name": "Amfam Workday (americanfamilyconnectpropertycasualtyinsurancecompany)", "url": "https://amfam.wd1.myworkdayjobs.com/americanfamilyconnectpropertycasualtyinsurancecompany"},
    {"name": "Amfam Workday (amfamgroupinterncareers)", "url": "https://amfam.wd1.myworkdayjobs.com/amfamgroupinterncareers"},
    {"name": "Amfam Workday (careers)", "url": "https://amfam.wd1.myworkdayjobs.com/careers"},
    {"name": "Amfam Workday (careers-private)", "url": "https://amfam.wd1.myworkdayjobs.com/careers-private"},
    {"name": "Amfam Workday (homesitecareers)", "url": "https://amfam.wd1.myworkdayjobs.com/homesitecareers"},
    {"name": "Amfam Workday (homesitecareers_private)", "url": "https://amfam.wd1.myworkdayjobs.com/homesitecareers_private"},
    {"name": "Amfam Workday (msagroup_careers)", "url": "https://amfam.wd1.myworkdayjobs.com/msagroup_careers"},
    {"name": "Amfam Workday (thegeneral)", "url": "https://amfam.wd1.myworkdayjobs.com/thegeneral"},
    {"name": "Amfam Workday (thegeneralprivate)", "url": "https://amfam.wd1.myworkdayjobs.com/thegeneralprivate"},
    {"name": "Amgen Workday (agency)", "url": "https://amgen.wd1.myworkdayjobs.com/agency"},
    {"name": "Amgen Workday (careers)", "url": "https://amgen.wd1.myworkdayjobs.com/careers"},
    {"name": "Amherst Workday (amherst_jobs)", "url": "https://amherst.wd5.myworkdayjobs.com/amherst_jobs"},
    {"name": "Amherst Workday (fsl_employment_opportunities)", "url": "https://amherst.wd5.myworkdayjobs.com/fsl_employment_opportunities"},
    {"name": "Amherstgroup Workday (amherst_holdings_careers)", "url": "https://amherstgroup.wd1.myworkdayjobs.com/amherst_holdings_careers"},
    {"name": "Amli Workday (amli_careers)", "url": "https://amli.wd5.myworkdayjobs.com/amli_careers"},
    {"name": "Amlrightsource Workday (amlrightsource)", "url": "https://amlrightsource.wd1.myworkdayjobs.com/amlrightsource"},
    {"name": "Amn Workday (amn_careers)", "url": "https://amn.wd1.myworkdayjobs.com/amn_careers"},
    {"name": "Amplify Workday (amplify_careers)", "url": "https://amplify.wd1.myworkdayjobs.com/amplify_careers"},
    {"name": "Amplity Workday (amplityhealth)", "url": "https://amplity.wd1.myworkdayjobs.com/amplityhealth"},
    {"name": "Amynta Workday (global)", "url": "https://amynta.wd5.myworkdayjobs.com/global"},
    {"name": "Anaheimducks Workday (adhctw)", "url": "https://anaheimducks.wd5.myworkdayjobs.com/adhctw"},
    {"name": "Anaheimducks Workday (allco)", "url": "https://anaheimducks.wd5.myworkdayjobs.com/allco"},
    {"name": "Anaheimducks Workday (hsv)", "url": "https://anaheimducks.wd5.myworkdayjobs.com/hsv"},
    {"name": "Anaheimducks Workday (linkedin)", "url": "https://anaheimducks.wd5.myworkdayjobs.com/linkedin"},
    {"name": "Anaheimducks Workday (ocvibe)", "url": "https://anaheimducks.wd5.myworkdayjobs.com/ocvibe"},
    {"name": "Anaheimducks Workday (rinks)", "url": "https://anaheimducks.wd5.myworkdayjobs.com/rinks"},
    {"name": "Anaheimducks Workday (sdg)", "url": "https://anaheimducks.wd5.myworkdayjobs.com/sdg"},
    {"name": "Anchorglass Workday (careers)", "url": "https://anchorglass.wd1.myworkdayjobs.com/careers"},
    {"name": "Andela Workday (external)", "url": "https://andela.wd1.myworkdayjobs.com/external"},
    {"name": "Andersen Workday (andersen_external_career_site)", "url": "https://andersen.wd12.myworkdayjobs.com/andersen_external_career_site"},
    {"name": "Andersonauto Workday (external)", "url": "https://andersonauto.wd5.myworkdayjobs.com/external"},
    {"name": "Andersonsinc Workday (theandersonscareers)", "url": "https://andersonsinc.wd1.myworkdayjobs.com/theandersonscareers"},
    {"name": "Andersonuniversity Workday (andersonuniversity)", "url": "https://andersonuniversity.wd1.myworkdayjobs.com/andersonuniversity"},
    {"name": "Andrewsdistributing Workday (andrewsdistributing)", "url": "https://andrewsdistributing.wd5.myworkdayjobs.com/andrewsdistributing"},
    {"name": "Angc Workday (angc)", "url": "https://angc.wd5.myworkdayjobs.com/angc"},
    {"name": "Angc Workday (auburn_culinary)", "url": "https://angc.wd5.myworkdayjobs.com/auburn_culinary"},
    {"name": "Angc Workday (berckmans_i)", "url": "https://angc.wd5.myworkdayjobs.com/berckmans_i"},
    {"name": "Angc Workday (clubops_i)", "url": "https://angc.wd5.myworkdayjobs.com/clubops_i"},
    {"name": "Angc Workday (concessions_i)", "url": "https://angc.wd5.myworkdayjobs.com/concessions_i"},
    {"name": "Angc Workday (concessions_ii)", "url": "https://angc.wd5.myworkdayjobs.com/concessions_ii"},
    {"name": "Angc Workday (culinary_institute_lenore)", "url": "https://angc.wd5.myworkdayjobs.com/culinary_institute_lenore"},
    {"name": "Angc Workday (dallas_college)", "url": "https://angc.wd5.myworkdayjobs.com/dallas_college"},
    {"name": "Angc Workday (dcc)", "url": "https://angc.wd5.myworkdayjobs.com/dcc"},
    {"name": "Angc Workday (jfci)", "url": "https://angc.wd5.myworkdayjobs.com/jfci"},
    {"name": "Angc Workday (jwu)", "url": "https://angc.wd5.myworkdayjobs.com/jwu"},
    {"name": "Angc Workday (keiser)", "url": "https://angc.wd5.myworkdayjobs.com/keiser"},
    {"name": "Angc Workday (lci)", "url": "https://angc.wd5.myworkdayjobs.com/lci"},
    {"name": "Angc Workday (nc_merchandise)", "url": "https://angc.wd5.myworkdayjobs.com/nc_merchandise"},
    {"name": "Angc Workday (paine_consessions)", "url": "https://angc.wd5.myworkdayjobs.com/paine_consessions"},
    {"name": "Angc Workday (public)", "url": "https://angc.wd5.myworkdayjobs.com/public"},
    {"name": "Anglefinance Workday (angle_auto_career_opportunities)", "url": "https://anglefinance.wd105.myworkdayjobs.com/angle_auto_career_opportunities"},
    {"name": "Anglefinance Workday (angle_finance_careers)", "url": "https://anglefinance.wd105.myworkdayjobs.com/angle_finance_careers"},
    {"name": "Anglicare Workday (anglicare_careers)", "url": "https://anglicare.wd105.myworkdayjobs.com/anglicare_careers"},
    {"name": "Anglicare Workday (food_and_financial_assistance_volunteers)", "url": "https://anglicare.wd105.myworkdayjobs.com/food_and_financial_assistance_volunteers"},
    {"name": "Anglicare Workday (op_shops_volunteers)", "url": "https://anglicare.wd105.myworkdayjobs.com/op_shops_volunteers"},
    {"name": "Anglicare Workday (residential_care_volunteers)", "url": "https://anglicare.wd105.myworkdayjobs.com/residential_care_volunteers"},
    {"name": "Anglicare Workday (seniors_living_volunteer)", "url": "https://anglicare.wd105.myworkdayjobs.com/seniors_living_volunteer"},
    {"name": "Anglicare Workday (volunteers_careers)", "url": "https://anglicare.wd105.myworkdayjobs.com/volunteers_careers"},
    {"name": "Ankura Workday (ankura)", "url": "https://ankura.wd5.myworkdayjobs.com/ankura"},
    {"name": "Anokacounty Workday (anoka_county_career_opportunities)", "url": "https://anokacounty.wd1.myworkdayjobs.com/anoka_county_career_opportunities"},
    {"name": "Anselm Workday (anselm)", "url": "https://anselm.wd1.myworkdayjobs.com/anselm"},
    {"name": "Ansira Workday (ansira_careers)", "url": "https://ansira.wd1.myworkdayjobs.com/ansira_careers"},
    {"name": "Antares Workday (antares)", "url": "https://antares.wd5.myworkdayjobs.com/antares"},
    {"name": "Anticimexinc Workday (anticimex_carolinas_external_careers)", "url": "https://anticimexinc.wd1.myworkdayjobs.com/anticimex_carolinas_external_careers"},
    {"name": "Anticimexinc Workday (jp_mchale_pest_management_external_careers)", "url": "https://anticimexinc.wd1.myworkdayjobs.com/jp_mchale_pest_management_external_careers"},
    {"name": "Anticimexinc Workday (northwest_exterminating_external_careers)", "url": "https://anticimexinc.wd1.myworkdayjobs.com/northwest_exterminating_external_careers"},
    {"name": "Anticimexinc Workday (pestban_external_careers)", "url": "https://anticimexinc.wd1.myworkdayjobs.com/pestban_external_careers"},
    {"name": "Anticimexinc Workday (turner_pest_control_external_careers)", "url": "https://anticimexinc.wd1.myworkdayjobs.com/turner_pest_control_external_careers"},
    {"name": "Anticimexinc Workday (waynes_pest_control_service_external_careers)", "url": "https://anticimexinc.wd1.myworkdayjobs.com/waynes_pest_control_service_external_careers"},
    {"name": "Aoins Workday (autoowners)", "url": "https://aoins.wd5.myworkdayjobs.com/autoowners"},
    {"name": "Aoins Workday (concord)", "url": "https://aoins.wd5.myworkdayjobs.com/concord"},
    {"name": "Aoncology Workday (aoncology_careers)", "url": "https://aoncology.wd12.myworkdayjobs.com/aoncology_careers"},
    {"name": "Apa Workday (apa-careers)", "url": "https://apa.wd105.myworkdayjobs.com/apa-careers"},
    {"name": "Apacheip Workday (apache_careers)", "url": "https://apacheip.wd1.myworkdayjobs.com/apache_careers"},
    {"name": "Apexcapitalcorp Workday (apex_external)", "url": "https://apexcapitalcorp.wd5.myworkdayjobs.com/apex_external"},
    {"name": "Apexcapitalcorp Workday (jobsattcs)", "url": "https://apexcapitalcorp.wd5.myworkdayjobs.com/jobsattcs"},
    {"name": "Apog Workday (apogee)", "url": "https://apog.wd1.myworkdayjobs.com/apogee"},
    {"name": "Appliedis Workday (ais_careers)", "url": "https://appliedis.wd5.myworkdayjobs.com/ais_careers"},
    {"name": "Applyboard Workday (applyboardnh)", "url": "https://applyboard.wd3.myworkdayjobs.com/applyboardnh"},
    {"name": "Aptiagroup Workday (broadbean_external)", "url": "https://aptiagroup.wd3.myworkdayjobs.com/broadbean_external"},
    {"name": "Aptiagroup Workday (opportunities)", "url": "https://aptiagroup.wd3.myworkdayjobs.com/opportunities"},
    {"name": "Aptiv Workday (aptiv_careers)", "url": "https://aptiv.wd5.myworkdayjobs.com/aptiv_careers"},
    {"name": "Aptive Workday (aptive)", "url": "https://aptive.wd1.myworkdayjobs.com/aptive"},
    {"name": "Aptos Workday (aptos)", "url": "https://aptos.wd108.myworkdayjobs.com/aptos"},
    {"name": "Aptos Workday (revionics)", "url": "https://aptos.wd108.myworkdayjobs.com/revionics"},
    {"name": "Aqa Workday (aqa)", "url": "https://aqa.wd3.myworkdayjobs.com/aqa"},
    {"name": "Aquaamerica Workday (essential_careers)", "url": "https://aquaamerica.wd5.myworkdayjobs.com/essential_careers"},
    {"name": "Aquafinance Workday (aqua_finance)", "url": "https://aquafinance.wd12.myworkdayjobs.com/aqua_finance"},
    {"name": "Aquilacapital Workday (aquilacapital)", "url": "https://aquilacapital.wd3.myworkdayjobs.com/aquilacapital"},
    {"name": "Aquilacapital Workday (aquilagroup)", "url": "https://aquilacapital.wd3.myworkdayjobs.com/aquilagroup"},
    {"name": "Archgroup Workday (careers)", "url": "https://archgroup.wd1.myworkdayjobs.com/careers"},
    {"name": "Archildrens Workday (external_career_site)", "url": "https://archildrens.wd1.myworkdayjobs.com/external_career_site"},
    {"name": "Arcis Workday (extna)", "url": "https://arcis.wd12.myworkdayjobs.com/extna"},
    {"name": "Arctera Workday (arctera)", "url": "https://arctera.wd501.myworkdayjobs.com/arctera"},
    {"name": "Arcticwolf Workday (external)", "url": "https://arcticwolf.wd1.myworkdayjobs.com/external"},
    {"name": "Ardian Workday (ardiancareers)", "url": "https://ardian.wd103.myworkdayjobs.com/ardiancareers"},
    {"name": "Aresmgmt Workday (external)", "url": "https://aresmgmt.wd1.myworkdayjobs.com/external"},
    {"name": "Aresmgmt Workday (external-ada)", "url": "https://aresmgmt.wd1.myworkdayjobs.com/external-ada"},
    {"name": "Areteir Workday (arete-careers)", "url": "https://areteir.wd1.myworkdayjobs.com/arete-careers"},
    {"name": "Argenx Workday (external_careers)", "url": "https://argenx.wd3.myworkdayjobs.com/external_careers"},
    {"name": "Argonne Workday (argonne_careers)", "url": "https://argonne.wd1.myworkdayjobs.com/argonne_careers"},
    {"name": "Argonne Workday (edu_pub)", "url": "https://argonne.wd1.myworkdayjobs.com/edu_pub"},
    {"name": "Arianegroup Workday (externalall)", "url": "https://arianegroup.wd3.myworkdayjobs.com/externalall"},
    {"name": "Arianegroup Workday (groupmobility-parentcompanies)", "url": "https://arianegroup.wd3.myworkdayjobs.com/groupmobility-parentcompanies"},
    {"name": "Arianegroup Workday (groupmobility-subsidiaries)", "url": "https://arianegroup.wd3.myworkdayjobs.com/groupmobility-subsidiaries"},
    {"name": "Arienscompany Workday (external)", "url": "https://arienscompany.wd5.myworkdayjobs.com/external"},
    {"name": "Aristocrat Workday (aristocratexternalcareerssite)", "url": "https://aristocrat.wd3.myworkdayjobs.com/aristocratexternalcareerssite"},
    {"name": "Aritzia Workday (calling_new_graduates)", "url": "https://aritzia.wd3.myworkdayjobs.com/calling_new_graduates"},
    {"name": "Aritzia Workday (external)", "url": "https://aritzia.wd3.myworkdayjobs.com/external"},
    {"name": "Aritzia Workday (internships)", "url": "https://aritzia.wd3.myworkdayjobs.com/internships"},
    {"name": "Arkbluecross Workday (abcbs_external_careers)", "url": "https://arkbluecross.wd1.myworkdayjobs.com/abcbs_external_careers"},
    {"name": "Arlo Workday (external_careers)", "url": "https://arlo.wd12.myworkdayjobs.com/external_careers"},
    {"name": "Armacell Workday (career-armacell)", "url": "https://armacell.wd3.myworkdayjobs.com/career-armacell"},
    {"name": "Armaninollp Workday (armanino)", "url": "https://armaninollp.wd1.myworkdayjobs.com/armanino"},
    {"name": "Armaninollp Workday (armanino_india)", "url": "https://armaninollp.wd1.myworkdayjobs.com/armanino_india"},
    {"name": "Arraytechinc Workday (array_careers)", "url": "https://arraytechinc.wd5.myworkdayjobs.com/array_careers"},
    {"name": "Arriva Workday (careers)", "url": "https://arriva.wd3.myworkdayjobs.com/careers"},
    {"name": "Arrowstreetcapital Workday (arrowstreet)", "url": "https://arrowstreetcapital.wd5.myworkdayjobs.com/arrowstreet"},
    {"name": "Arvada Workday (city_of_arvada_external_career_site)", "url": "https://arvada.wd5.myworkdayjobs.com/city_of_arvada_external_career_site"},
    {"name": "Ascendperformancematerials Workday (ascend)", "url": "https://ascendperformancematerials.wd1.myworkdayjobs.com/ascend"},
    {"name": "Ascensushr Workday (ascensuscareers)", "url": "https://ascensushr.wd1.myworkdayjobs.com/ascensuscareers"},
    {"name": "Ascentgl Workday (asg)", "url": "https://ascentgl.wd1.myworkdayjobs.com/asg"},
    {"name": "Ascentgl Workday (usj)", "url": "https://ascentgl.wd1.myworkdayjobs.com/usj"},
    {"name": "Ascentsolutions Workday (external)", "url": "https://ascentsolutions.wd1.myworkdayjobs.com/external"},
    {"name": "Asco Workday (asco)", "url": "https://asco.wd5.myworkdayjobs.com/asco"},
    {"name": "Asd Workday (asd20)", "url": "https://asd.wd5.myworkdayjobs.com/asd20"},
    {"name": "Asda Workday (asdajobs)", "url": "https://asda.wd103.myworkdayjobs.com/asdajobs"},
    {"name": "Ashealthnet Workday (ashn)", "url": "https://ashealthnet.wd1.myworkdayjobs.com/ashn"},
    {"name": "Ashealthnet Workday (beaumonthomehealthandhospice)", "url": "https://ashealthnet.wd1.myworkdayjobs.com/beaumonthomehealthandhospice"},
    {"name": "Ashealthnet Workday (genesishomecare)", "url": "https://ashealthnet.wd1.myworkdayjobs.com/genesishomecare"},
    {"name": "Ashealthnet Workday (ketteringhomecare)", "url": "https://ashealthnet.wd1.myworkdayjobs.com/ketteringhomecare"},
    {"name": "Ashealthnet Workday (memorialhomehealthservices)", "url": "https://ashealthnet.wd1.myworkdayjobs.com/memorialhomehealthservices"},
    {"name": "Ashealthnet Workday (mercymedicalcenterhomehealthandhospice)", "url": "https://ashealthnet.wd1.myworkdayjobs.com/mercymedicalcenterhomehealthandhospice"},
    {"name": "Ashealthnet Workday (summahealthathomeandhospice)", "url": "https://ashealthnet.wd1.myworkdayjobs.com/summahealthathomeandhospice"},
    {"name": "Ashealthnet Workday (theohiostateuniversitywexnermedicalcenterhomecare)", "url": "https://ashealthnet.wd1.myworkdayjobs.com/theohiostateuniversitywexnermedicalcenterhomecare"},
    {"name": "Ashland Workday (ashlandcareers1)", "url": "https://ashland.wd12.myworkdayjobs.com/ashlandcareers1"},
    {"name": "Askbio Workday (askbio)", "url": "https://askbio.wd12.myworkdayjobs.com/askbio"},
    {"name": "Asmglobal Workday (careers)", "url": "https://asmglobal.wd1.myworkdayjobs.com/careers"},
    {"name": "Asml Workday (asmlext1)", "url": "https://asml.wd3.myworkdayjobs.com/asmlext1"},
    {"name": "Asos Workday (externalcareers)", "url": "https://asos.wd3.myworkdayjobs.com/externalcareers"},
    {"name": "Aspca Workday (aspcawebsite)", "url": "https://aspca.wd1.myworkdayjobs.com/aspcawebsite"},
    {"name": "Aspca Workday (contingentworkerssourcedbyaspca)", "url": "https://aspca.wd1.myworkdayjobs.com/contingentworkerssourcedbyaspca"},
    {"name": "Aspendental Workday (azpetvet)", "url": "https://aspendental.wd1.myworkdayjobs.com/azpetvet"},
    {"name": "Aspendental Workday (careers_aspen_dental)", "url": "https://aspendental.wd1.myworkdayjobs.com/careers_aspen_dental"},
    {"name": "Aspendental Workday (careers_clearchoice)", "url": "https://aspendental.wd1.myworkdayjobs.com/careers_clearchoice"},
    {"name": "Aspendental Workday (careers_the_aspen_group)", "url": "https://aspendental.wd1.myworkdayjobs.com/careers_the_aspen_group"},
    {"name": "Aspendental Workday (mychaptercom-careers)", "url": "https://aspendental.wd1.myworkdayjobs.com/mychaptercom-careers"},
    {"name": "Aspendental Workday (wellnowurgentcarecareers)", "url": "https://aspendental.wd1.myworkdayjobs.com/wellnowurgentcarecareers"},
    {"name": "Aspentech Workday (aspentech)", "url": "https://aspentech.wd5.myworkdayjobs.com/aspentech"},
    {"name": "Aspenvalleyhealth Workday (avh)", "url": "https://aspenvalleyhealth.wd501.myworkdayjobs.com/avh"},
    {"name": "Assetmark Workday (assetmark_careers)", "url": "https://assetmark.wd5.myworkdayjobs.com/assetmark_careers"},
    {"name": "Associatedbank Workday (external_careers)", "url": "https://associatedbank.wd1.myworkdayjobs.com/external_careers"},
    {"name": "Assuranceamerica Workday (assuranceamerica)", "url": "https://assuranceamerica.wd12.myworkdayjobs.com/assuranceamerica"},
    {"name": "Assurant Workday (assurant_careers)", "url": "https://assurant.wd1.myworkdayjobs.com/assurant_careers"},
    {"name": "Assurant Workday (ismash_external_career_site)", "url": "https://assurant.wd1.myworkdayjobs.com/ismash_external_career_site"},
    {"name": "Astound Workday (astound_careers)", "url": "https://astound.wd108.myworkdayjobs.com/astound_careers"},
    {"name": "Astrazeneca Workday (alexion)", "url": "https://astrazeneca.wd3.myworkdayjobs.com/alexion"},
    {"name": "Astrazeneca Workday (broadbean_external)", "url": "https://astrazeneca.wd3.myworkdayjobs.com/broadbean_external"},
    {"name": "Astrazeneca Workday (careers)", "url": "https://astrazeneca.wd3.myworkdayjobs.com/careers"},
    {"name": "Astrazeneca Workday (emerging-talent)", "url": "https://astrazeneca.wd3.myworkdayjobs.com/emerging-talent"},
    {"name": "Astreya Workday (life-at-astreya-opportunities)", "url": "https://astreya.wd5.myworkdayjobs.com/life-at-astreya-opportunities"},
    {"name": "Astro Workday (astro_careers)", "url": "https://astro.wd3.myworkdayjobs.com/astro_careers"},
    {"name": "Asu Workday (asustaffcareers)", "url": "https://asu.wd1.myworkdayjobs.com/asustaffcareers"},
    {"name": "Asuep Workday (asuep)", "url": "https://asuep.wd5.myworkdayjobs.com/asuep"},
    {"name": "Asuep Workday (asufoundation)", "url": "https://asuep.wd5.myworkdayjobs.com/asufoundation"},
    {"name": "Asuep Workday (skysonginnovations)", "url": "https://asuep.wd5.myworkdayjobs.com/skysonginnovations"},
    {"name": "Asurion Workday (asurioncareers_us)", "url": "https://asurion.wd5.myworkdayjobs.com/asurioncareers_us"},
    {"name": "Asurion Workday (atk_ext_japan)", "url": "https://asurion.wd5.myworkdayjobs.com/atk_ext_japan"},
    {"name": "Asurion Workday (usextprivate)", "url": "https://asurion.wd5.myworkdayjobs.com/usextprivate"},
    {"name": "Asx Workday (asx_careers)", "url": "https://asx.wd105.myworkdayjobs.com/asx_careers"},
    {"name": "Atcllc Workday (atcllc)", "url": "https://atcllc.wd5.myworkdayjobs.com/atcllc"},
    {"name": "Atd Workday (american_tire_distributors)", "url": "https://atd.wd1.myworkdayjobs.com/american_tire_distributors"},
    {"name": "Atd Workday (torqata_data_and_analytics)", "url": "https://atd.wd1.myworkdayjobs.com/torqata_data_and_analytics"},
    {"name": "Athenago Workday (athena)", "url": "https://athenago.wd108.myworkdayjobs.com/athena"},
    {"name": "Athenahealth Workday (external)", "url": "https://athenahealth.wd1.myworkdayjobs.com/external"},
    {"name": "Athene Workday (apollo_careers)", "url": "https://athene.wd5.myworkdayjobs.com/apollo_careers"},
    {"name": "Athene Workday (apollononpubliccareersite)", "url": "https://athene.wd5.myworkdayjobs.com/apollononpubliccareersite"},
    {"name": "Athene Workday (athene_careers)", "url": "https://athene.wd5.myworkdayjobs.com/athene_careers"},
    {"name": "Athensservices Workday (athens_services)", "url": "https://athensservices.wd1.myworkdayjobs.com/athens_services"},
    {"name": "Athensservices Workday (drivers)", "url": "https://athensservices.wd1.myworkdayjobs.com/drivers"},
    {"name": "Athora Workday (athora-careers)", "url": "https://athora.wd3.myworkdayjobs.com/athora-careers"},
    {"name": "Atlantabravesmlb Workday (atlantabraves)", "url": "https://atlantabravesmlb.wd5.myworkdayjobs.com/atlantabraves"},
    {"name": "Atlanticmedia Workday (careers)", "url": "https://atlanticmedia.wd1.myworkdayjobs.com/careers"},
    {"name": "Atriumhospitality Workday (atriumhospitality)", "url": "https://atriumhospitality.wd5.myworkdayjobs.com/atriumhospitality"},
    {"name": "Atsg Workday (xtiumcareers)", "url": "https://atsg.wd108.myworkdayjobs.com/xtiumcareers"},
    {"name": "Att Workday (attcollege)", "url": "https://att.wd1.myworkdayjobs.com/attcollege"},
    {"name": "Att Workday (attcollegespecialinvite)", "url": "https://att.wd1.myworkdayjobs.com/attcollegespecialinvite"},
    {"name": "Att Workday (attgeneral)", "url": "https://att.wd1.myworkdayjobs.com/attgeneral"},
    {"name": "Att Workday (attspecialinvite)", "url": "https://att.wd1.myworkdayjobs.com/attspecialinvite"},
    {"name": "Att Workday (cricket)", "url": "https://att.wd1.myworkdayjobs.com/cricket"},
    {"name": "Auchanportugal Workday (auchan-retail)", "url": "https://auchanportugal.wd3.myworkdayjobs.com/auchan-retail"},
    {"name": "Audubon Workday (audubon)", "url": "https://audubon.wd503.myworkdayjobs.com/audubon"},
    {"name": "Aurecongroup Workday (aurecon)", "url": "https://aurecongroup.wd3.myworkdayjobs.com/aurecon"},
    {"name": "Aurecongroup Workday (broadbean_external)", "url": "https://aurecongroup.wd3.myworkdayjobs.com/broadbean_external"},
    {"name": "Auroragov Workday (careers)", "url": "https://auroragov.wd1.myworkdayjobs.com/careers"},
    {"name": "Aussiebroadband Workday (wholesale-external)", "url": "https://aussiebroadband.wd3.myworkdayjobs.com/wholesale-external"},
    {"name": "Austalusa Workday (austal)", "url": "https://austalusa.wd1.myworkdayjobs.com/austal"},
    {"name": "Austincc Workday (external)", "url": "https://austincc.wd1.myworkdayjobs.com/external"},
    {"name": "Austintexas Workday (coa_careers)", "url": "https://austintexas.wd5.myworkdayjobs.com/coa_careers"},
    {"name": "Australiancricket Workday (cricket_australia)", "url": "https://australiancricket.wd105.myworkdayjobs.com/cricket_australia"},
    {"name": "Australiancricket Workday (ctas)", "url": "https://australiancricket.wd105.myworkdayjobs.com/ctas"},
    {"name": "Australiancricket Workday (queensland_cricket)", "url": "https://australiancricket.wd105.myworkdayjobs.com/queensland_cricket"},
    {"name": "Australiancricket Workday (wa_cricket)", "url": "https://australiancricket.wd105.myworkdayjobs.com/wa_cricket"},
    {"name": "Autismplus Workday (autismplus_careers)", "url": "https://autismplus.wd3.myworkdayjobs.com/autismplus_careers"},
    {"name": "Autodesk Workday (ext)", "url": "https://autodesk.wd1.myworkdayjobs.com/ext"},
    {"name": "Autodesk Workday (uni)", "url": "https://autodesk.wd1.myworkdayjobs.com/uni"},
    {"name": "Autodoc Workday (autodoc_group)", "url": "https://autodoc.wd3.myworkdayjobs.com/autodoc_group"},
    {"name": "Automationanywhere Workday (automationanywherejobs)", "url": "https://automationanywhere.wd5.myworkdayjobs.com/automationanywherejobs"},
    {"name": "Autostore Workday (autostore)", "url": "https://autostore.wd3.myworkdayjobs.com/autostore"},
    {"name": "Availity Workday (availity_careers_india)", "url": "https://availity.wd1.myworkdayjobs.com/availity_careers_india"},
    {"name": "Availity Workday (availity_careers_us)", "url": "https://availity.wd1.myworkdayjobs.com/availity_careers_us"},
    {"name": "Avant Workday (external_careers)", "url": "https://avant.wd503.myworkdayjobs.com/external_careers"},
    {"name": "Avav Workday (avav)", "url": "https://avav.wd1.myworkdayjobs.com/avav"},
    {"name": "Avera Workday (avera-careers)", "url": "https://avera.wd5.myworkdayjobs.com/avera-careers"},
    {"name": "Avera Workday (bhs-careers)", "url": "https://avera.wd5.myworkdayjobs.com/bhs-careers"},
    {"name": "Averis Workday (averis)", "url": "https://averis.wd3.myworkdayjobs.com/averis"},
    {"name": "Averis Workday (rge)", "url": "https://averis.wd3.myworkdayjobs.com/rge"},
    {"name": "Averis Workday (tpl)", "url": "https://averis.wd3.myworkdayjobs.com/tpl"},
    {"name": "Avesis Workday (avesis)", "url": "https://avesis.wd5.myworkdayjobs.com/avesis"},
    {"name": "Aveva Workday (aveva_careers)", "url": "https://aveva.wd3.myworkdayjobs.com/aveva_careers"},
    {"name": "Aveva Workday (etap_careers)", "url": "https://aveva.wd3.myworkdayjobs.com/etap_careers"},
    {"name": "Aveva Workday (rib_careers)", "url": "https://aveva.wd3.myworkdayjobs.com/rib_careers"},
    {"name": "Aviagen Workday (aviagen-careers)", "url": "https://aviagen.wd1.myworkdayjobs.com/aviagen-careers"},
    {"name": "Avid Workday (avid)", "url": "https://avid.wd5.myworkdayjobs.com/avid"},
    {"name": "Avisbudget Workday (abg_careers)", "url": "https://avisbudget.wd1.myworkdayjobs.com/abg_careers"},
    {"name": "Avisbudget Workday (zipcar_careers)", "url": "https://avisbudget.wd1.myworkdayjobs.com/zipcar_careers"},
    {"name": "Aviva Workday (aviva_investors_external)", "url": "https://aviva.wd1.myworkdayjobs.com/aviva_investors_external"},
    {"name": "Aviva Workday (external)", "url": "https://aviva.wd1.myworkdayjobs.com/external"},
    {"name": "Avnet Workday (external)", "url": "https://avnet.wd1.myworkdayjobs.com/external"},
    {"name": "Avon Workday (naturacareers)", "url": "https://avon.wd5.myworkdayjobs.com/naturacareers"},
    {"name": "Awc Workday (awc_career_site)", "url": "https://awc.wd3.myworkdayjobs.com/awc_career_site"},
    {"name": "Awc Workday (wine_rack_career_site)", "url": "https://awc.wd3.myworkdayjobs.com/wine_rack_career_site"},
    {"name": "Awe Workday (art_and_wellness)", "url": "https://awe.wd1.myworkdayjobs.com/art_and_wellness"},
    {"name": "Awepeople Workday (apprentice_careers)", "url": "https://awepeople.wd3.myworkdayjobs.com/apprentice_careers"},
    {"name": "Awepeople Workday (external_careers)", "url": "https://awepeople.wd3.myworkdayjobs.com/external_careers"},
    {"name": "Awepeople Workday (grad_careers)", "url": "https://awepeople.wd3.myworkdayjobs.com/grad_careers"},
    {"name": "Awg Workday (a1)", "url": "https://awg.wd3.myworkdayjobs.com/a1"},
    {"name": "Awg Workday (broadbean_external)", "url": "https://awg.wd3.myworkdayjobs.com/broadbean_external"},
    {"name": "Awg Workday (spa)", "url": "https://awg.wd3.myworkdayjobs.com/spa"},
    {"name": "Axalta Workday (axalta)", "url": "https://axalta.wd1.myworkdayjobs.com/axalta"},
    {"name": "Axcelis Workday (axcelis)", "url": "https://axcelis.wd1.myworkdayjobs.com/axcelis"},
    {"name": "Axiomspace Workday (external_career_site)", "url": "https://axiomspace.wd5.myworkdayjobs.com/external_career_site"},
    {"name": "Axis Workday (external_career_site)", "url": "https://axis.wd3.myworkdayjobs.com/external_career_site"},
    {"name": "Axiscapital Workday (axiscareers)", "url": "https://axiscapital.wd1.myworkdayjobs.com/axiscareers"},
    {"name": "Axiscapital Workday (confidentialaxiscareers)", "url": "https://axiscapital.wd1.myworkdayjobs.com/confidentialaxiscareers"},
    {"name": "Axos Workday (axos)", "url": "https://axos.wd5.myworkdayjobs.com/axos"},
    {"name": "Ayvens Workday (ayvenscareers)", "url": "https://ayvens.wd3.myworkdayjobs.com/ayvenscareers"},
    {"name": "Azelis Workday (azelis_careers)", "url": "https://azelis.wd3.myworkdayjobs.com/azelis_careers"},
    {"name": "Azenta Workday (azentajobs)", "url": "https://azenta.wd1.myworkdayjobs.com/azentajobs"},
    {"name": "Aztecgroup Workday (external)", "url": "https://aztecgroup.wd103.myworkdayjobs.com/external"},
    {"name": "Babelgroup Workday (rec_external_career_site)", "url": "https://babelgroup.wd103.myworkdayjobs.com/rec_external_career_site"},
    {"name": "Babson Workday (staff)", "url": "https://babson.wd1.myworkdayjobs.com/staff"},
    {"name": "Bacardi Workday (jobs_bacardi)", "url": "https://bacardi.wd3.myworkdayjobs.com/jobs_bacardi"},
    {"name": "Badgermeter Workday (badger_meter_europe)", "url": "https://badgermeter.wd5.myworkdayjobs.com/badger_meter_europe"},
    {"name": "Badgermeter Workday (us_careersite)", "url": "https://badgermeter.wd5.myworkdayjobs.com/us_careersite"},
    {"name": "Bah Workday (bah_jobs)", "url": "https://bah.wd1.myworkdayjobs.com/bah_jobs"},
    {"name": "Baicommunications Workday (external)", "url": "https://baicommunications.wd3.myworkdayjobs.com/external"},
    {"name": "Bailliegifford Workday (bailliegiffordcareers)", "url": "https://bailliegifford.wd3.myworkdayjobs.com/bailliegiffordcareers"},
    {"name": "Bailliegifford Workday (bailliegiffordclientmanager)", "url": "https://bailliegifford.wd3.myworkdayjobs.com/bailliegiffordclientmanager"},
    {"name": "Bailliegifford Workday (bailliegiffordearlycareers)", "url": "https://bailliegifford.wd3.myworkdayjobs.com/bailliegiffordearlycareers"},
    {"name": "Baincapital Workday (external_private)", "url": "https://baincapital.wd1.myworkdayjobs.com/external_private"},
    {"name": "Baincapital Workday (external_public)", "url": "https://baincapital.wd1.myworkdayjobs.com/external_public"},
    {"name": "Bakerhughes Workday (bakerhughes)", "url": "https://bakerhughes.wd5.myworkdayjobs.com/bakerhughes"},
    {"name": "Bakertilly Workday (btcareers)", "url": "https://bakertilly.wd5.myworkdayjobs.com/btcareers"},
    {"name": "Baldwin Workday (baldwin)", "url": "https://baldwin.wd1.myworkdayjobs.com/baldwin"},
    {"name": "Baldwin Workday (lease-track)", "url": "https://baldwin.wd1.myworkdayjobs.com/lease-track"},
    {"name": "Baldwin Workday (msi)", "url": "https://baldwin.wd1.myworkdayjobs.com/msi"},
    {"name": "Baldwin Workday (rogers-gray)", "url": "https://baldwin.wd1.myworkdayjobs.com/rogers-gray"},
    {"name": "Baldwin Workday (westwood-insurance-agency)", "url": "https://baldwin.wd1.myworkdayjobs.com/westwood-insurance-agency"},
    {"name": "Ballardspahr Workday (ballard_spahr_llp)", "url": "https://ballardspahr.wd5.myworkdayjobs.com/ballard_spahr_llp"},
    {"name": "Ballesterhermanos Workday (bhi-e)", "url": "https://ballesterhermanos.wd12.myworkdayjobs.com/bhi-e"},
    {"name": "Baltimorecity Workday (epfl_external)", "url": "https://baltimorecity.wd1.myworkdayjobs.com/epfl_external"},
    {"name": "Baltimorecity Workday (external)", "url": "https://baltimorecity.wd1.myworkdayjobs.com/external"},
    {"name": "Bamfhealth Workday (external)", "url": "https://bamfhealth.wd115.myworkdayjobs.com/external"},
    {"name": "Bamfunds Workday (external)", "url": "https://bamfunds.wd1.myworkdayjobs.com/external"},
    {"name": "Bankatfirst Workday (ffb)", "url": "https://bankatfirst.wd1.myworkdayjobs.com/ffb"},
    {"name": "Bankeasy Workday (bank-easy-job-openings)", "url": "https://bankeasy.wd5.myworkdayjobs.com/bank-easy-job-openings"},
    {"name": "Bannerhealth Workday (careers)", "url": "https://bannerhealth.wd108.myworkdayjobs.com/careers"},
    {"name": "Bannerhealth Workday (sonoraquestcareers)", "url": "https://bannerhealth.wd108.myworkdayjobs.com/sonoraquestcareers"},
    {"name": "Barclays Workday (external_career_site_barclays)", "url": "https://barclays.wd3.myworkdayjobs.com/external_career_site_barclays"},
    {"name": "Barings Workday (barings)", "url": "https://barings.wd1.myworkdayjobs.com/barings"},
    {"name": "Barings Workday (early_talent)", "url": "https://barings.wd1.myworkdayjobs.com/early_talent"},
    {"name": "Barnard Workday (faculty)", "url": "https://barnard.wd1.myworkdayjobs.com/faculty"},
    {"name": "Barnard Workday (staff)", "url": "https://barnard.wd1.myworkdayjobs.com/staff"},
    {"name": "Barr Workday (barrcareers)", "url": "https://barr.wd1.myworkdayjobs.com/barrcareers"},
    {"name": "Barryu Workday (barryu)", "url": "https://barryu.wd5.myworkdayjobs.com/barryu"},
    {"name": "Barryu Workday (barryustudentjobs)", "url": "https://barryu.wd5.myworkdayjobs.com/barryustudentjobs"},
    {"name": "Barrywehmiller Workday (bwcareers)", "url": "https://barrywehmiller.wd1.myworkdayjobs.com/bwcareers"},
    {"name": "Barrywehmiller Workday (bwconfidential)", "url": "https://barrywehmiller.wd1.myworkdayjobs.com/bwconfidential"},
    {"name": "Basecamp Workday (ecmc)", "url": "https://basecamp.wd1.myworkdayjobs.com/ecmc"},
    {"name": "Basicfit Workday (basicfit_career_site_nl)", "url": "https://basicfit.wd103.myworkdayjobs.com/basicfit_career_site_nl"},
    {"name": "Basspro Workday (careers)", "url": "https://basspro.wd1.myworkdayjobs.com/careers"},
    {"name": "Battlemotors Workday (battlemotors)", "url": "https://battlemotors.wd12.myworkdayjobs.com/battlemotors"},
    {"name": "Bavariannordic Workday (bavariannordic)", "url": "https://bavariannordic.wd103.myworkdayjobs.com/bavariannordic"},
    {"name": "Baxter Workday (baxter)", "url": "https://baxter.wd1.myworkdayjobs.com/baxter"},
    {"name": "Baxter Workday (vantive)", "url": "https://baxter.wd1.myworkdayjobs.com/vantive"},
    {"name": "Baystatehealth Workday (external_careers)", "url": "https://baystatehealth.wd12.myworkdayjobs.com/external_careers"},
    {"name": "Bayware Workday (jobsatbayware)", "url": "https://bayware.wd3.myworkdayjobs.com/jobsatbayware"},
    {"name": "Bb Workday (blackberry)", "url": "https://bb.wd3.myworkdayjobs.com/blackberry"},
    {"name": "Bb Workday (cylance)", "url": "https://bb.wd3.myworkdayjobs.com/cylance"},
    {"name": "Bb Workday (qnx)", "url": "https://bb.wd3.myworkdayjobs.com/qnx"},
    {"name": "Bb Workday (secusmart)", "url": "https://bb.wd3.myworkdayjobs.com/secusmart"},
    {"name": "Bb Workday (student)", "url": "https://bb.wd3.myworkdayjobs.com/student"},
    {"name": "Bbb Workday (bbb_careers)", "url": "https://bbb.wd501.myworkdayjobs.com/bbb_careers"},
    {"name": "Bbh Workday (bbh)", "url": "https://bbh.wd5.myworkdayjobs.com/bbh"},
    {"name": "Bbinsurance Workday (arrowheadcareers)", "url": "https://bbinsurance.wd1.myworkdayjobs.com/arrowheadcareers"},
    {"name": "Bbinsurance Workday (bsgcareers)", "url": "https://bbinsurance.wd1.myworkdayjobs.com/bsgcareers"},
    {"name": "Bbinsurance Workday (careers)", "url": "https://bbinsurance.wd1.myworkdayjobs.com/careers"},
    {"name": "Bbinsurance Workday (careers_europe)", "url": "https://bbinsurance.wd1.myworkdayjobs.com/careers_europe"},
    {"name": "Bbinsurance Workday (proctorcareers)", "url": "https://bbinsurance.wd1.myworkdayjobs.com/proctorcareers"},
    {"name": "Bbva Workday (bbva)", "url": "https://bbva.wd3.myworkdayjobs.com/bbva"},
    {"name": "Bcaa Workday (bcaacareers)", "url": "https://bcaa.wd3.myworkdayjobs.com/bcaacareers"},
    {"name": "Bcassessment Workday (bca)", "url": "https://bcassessment.wd10.myworkdayjobs.com/bca"},
    {"name": "Bcbsa Workday (careers)", "url": "https://bcbsa.wd1.myworkdayjobs.com/careers"},
    {"name": "Bcbsaz Workday (bcbsazcareers)", "url": "https://bcbsaz.wd1.myworkdayjobs.com/bcbsazcareers"},
    {"name": "Bcbskc Workday (bcbs_external_career_site)", "url": "https://bcbskc.wd1.myworkdayjobs.com/bcbs_external_career_site"},
    {"name": "Bcbskc Workday (spira_care_external_career_site)", "url": "https://bcbskc.wd1.myworkdayjobs.com/spira_care_external_career_site"},
    {"name": "Bcbsks Workday (external)", "url": "https://bcbsks.wd1.myworkdayjobs.com/external"},
    {"name": "Bcbsla Workday (external)", "url": "https://bcbsla.wd1.myworkdayjobs.com/external"},
    {"name": "Bcbsmn Workday (bluecrossmn)", "url": "https://bcbsmn.wd5.myworkdayjobs.com/bluecrossmn"},
    {"name": "Bcbsmn Workday (coupehealth)", "url": "https://bcbsmn.wd5.myworkdayjobs.com/coupehealth"},
    {"name": "Bcbsms Workday (bcbsms)", "url": "https://bcbsms.wd5.myworkdayjobs.com/bcbsms"},
    {"name": "Bcbsnc Workday (bcbsnc)", "url": "https://bcbsnc.wd5.myworkdayjobs.com/bcbsnc"},
    {"name": "Bcbst Workday (external)", "url": "https://bcbst.wd1.myworkdayjobs.com/external"},
    {"name": "Bcbst Workday (externalbluehorizon)", "url": "https://bcbst.wd1.myworkdayjobs.com/externalbluehorizon"},
    {"name": "Bcbst Workday (sharedhealthexternal)", "url": "https://bcbst.wd1.myworkdayjobs.com/sharedhealthexternal"},
    {"name": "Bcbswy Workday (careers)", "url": "https://bcbswy.wd1.myworkdayjobs.com/careers"},
    {"name": "Bci Workday (bci_careers)", "url": "https://bci.wd10.myworkdayjobs.com/bci_careers"},
    {"name": "Bcidaho Workday (bci)", "url": "https://bcidaho.wd5.myworkdayjobs.com/bci"},
    {"name": "Bcone Workday (bristlecone)", "url": "https://bcone.wd1.myworkdayjobs.com/bristlecone"},
    {"name": "Bdc Workday (bdc_careers)", "url": "https://bdc.wd10.myworkdayjobs.com/bdc_careers"},
    {"name": "Bdgrowers Workday (external)", "url": "https://bdgrowers.wd1.myworkdayjobs.com/external"},
    {"name": "Bdo Workday (bdo)", "url": "https://bdo.wd3.myworkdayjobs.com/bdo"},
    {"name": "Bdoau Workday (bdocareers)", "url": "https://bdoau.wd105.myworkdayjobs.com/bdocareers"},
    {"name": "Bdouk Workday (bdo_careers)", "url": "https://bdouk.wd3.myworkdayjobs.com/bdo_careers"},
    {"name": "Bdrthermea Workday (external)", "url": "https://bdrthermea.wd103.myworkdayjobs.com/external"},
    {"name": "Bdx Workday (embectacareers)", "url": "https://bdx.wd1.myworkdayjobs.com/embectacareers"},
    {"name": "Bdx Workday (external_career_site_australia)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_australia"},
    {"name": "Bdx Workday (external_career_site_austria)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_austria"},
    {"name": "Bdx Workday (external_career_site_brazil)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_brazil"},
    {"name": "Bdx Workday (external_career_site_canada)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_canada"},
    {"name": "Bdx Workday (external_career_site_china)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_china"},
    {"name": "Bdx Workday (external_career_site_france)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_france"},
    {"name": "Bdx Workday (external_career_site_germany)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_germany"},
    {"name": "Bdx Workday (external_career_site_hong_kong)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_hong_kong"},
    {"name": "Bdx Workday (external_career_site_hungary)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_hungary"},
    {"name": "Bdx Workday (external_career_site_india)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_india"},
    {"name": "Bdx Workday (external_career_site_ireland)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_ireland"},
    {"name": "Bdx Workday (external_career_site_japan)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_japan"},
    {"name": "Bdx Workday (external_career_site_korea_republic_of)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_korea_republic_of"},
    {"name": "Bdx Workday (external_career_site_malaysia)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_malaysia"},
    {"name": "Bdx Workday (external_career_site_singapore)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_singapore"},
    {"name": "Bdx Workday (external_career_site_spain)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_spain"},
    {"name": "Bdx Workday (external_career_site_switzerland)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_switzerland"},
    {"name": "Bdx Workday (external_career_site_thailand)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_thailand"},
    {"name": "Bdx Workday (external_career_site_uk)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_uk"},
    {"name": "Bdx Workday (external_career_site_usa)", "url": "https://bdx.wd1.myworkdayjobs.com/external_career_site_usa"},
    {"name": "Bdx Workday (us_early_talent_site)", "url": "https://bdx.wd1.myworkdayjobs.com/us_early_talent_site"},
    {"name": "Beachbody Workday (careers)", "url": "https://beachbody.wd503.myworkdayjobs.com/careers"},
    {"name": "Beautyhealth Workday (beautyhealthcareer)", "url": "https://beautyhealth.wd12.myworkdayjobs.com/beautyhealthcareer"},
    {"name": "Beca Workday (beca)", "url": "https://beca.wd105.myworkdayjobs.com/beca"},
    {"name": "Becu Workday (external)", "url": "https://becu.wd1.myworkdayjobs.com/external"},
    {"name": "Beemok Workday (bhc_careers)", "url": "https://beemok.wd5.myworkdayjobs.com/bhc_careers"},
    {"name": "Beemok Workday (meeting_street_schools_)", "url": "https://beemok.wd5.myworkdayjobs.com/meeting_street_schools_"},
    {"name": "Beemok Workday (sorelle_careers)", "url": "https://beemok.wd5.myworkdayjobs.com/sorelle_careers"},
    {"name": "Beemok Workday (thecharlestonplace_careers)", "url": "https://beemok.wd5.myworkdayjobs.com/thecharlestonplace_careers"},
    {"name": "Begacheese Workday (bega_careers)", "url": "https://begacheese.wd3.myworkdayjobs.com/bega_careers"},
    {"name": "Behavioralframework Workday (bf_careers)", "url": "https://behavioralframework.wd12.myworkdayjobs.com/bf_careers"},
    {"name": "Beigene Workday (beigene)", "url": "https://beigene.wd5.myworkdayjobs.com/beigene"},
    {"name": "Belk Workday (careers-corporate)", "url": "https://belk.wd1.myworkdayjobs.com/careers-corporate"},
    {"name": "Belk Workday (jobs-stores)", "url": "https://belk.wd1.myworkdayjobs.com/jobs-stores"},
    {"name": "Belkin Workday (belkin_careers)", "url": "https://belkin.wd5.myworkdayjobs.com/belkin_careers"},
    {"name": "Bellpartnersinc Workday (careers)", "url": "https://bellpartnersinc.wd5.myworkdayjobs.com/careers"},
    {"name": "Belron Workday (autoglass_and_laddaw_careers)", "url": "https://belron.wd3.myworkdayjobs.com/autoglass_and_laddaw_careers"},
    {"name": "Belron Workday (belron_canada_careers)", "url": "https://belron.wd3.myworkdayjobs.com/belron_canada_careers"},
    {"name": "Belron Workday (belron_new_zealand_careers)", "url": "https://belron.wd3.myworkdayjobs.com/belron_new_zealand_careers"},
    {"name": "Belron Workday (carglass_careers)", "url": "https://belron.wd3.myworkdayjobs.com/carglass_careers"},
    {"name": "Belron Workday (obrien_au)", "url": "https://belron.wd3.myworkdayjobs.com/obrien_au"},
    {"name": "Belron Workday (safelite_careers)", "url": "https://belron.wd3.myworkdayjobs.com/safelite_careers"},
    {"name": "Belron Workday (spain_carglass_careers)", "url": "https://belron.wd3.myworkdayjobs.com/spain_carglass_careers"},
    {"name": "Benchmark Workday (pgh_careers)", "url": "https://benchmark.wd1.myworkdayjobs.com/pgh_careers"},
    {"name": "Benchmark Workday (riseuptown)", "url": "https://benchmark.wd1.myworkdayjobs.com/riseuptown"},
    {"name": "Benchmark Workday (skamania)", "url": "https://benchmark.wd1.myworkdayjobs.com/skamania"},
    {"name": "Benchmarkeducation Workday (ext)", "url": "https://benchmarkeducation.wd501.myworkdayjobs.com/ext"},
    {"name": "Beneva Workday (benevasite_carriere)", "url": "https://beneva.wd10.myworkdayjobs.com/benevasite_carriere"},
    {"name": "Beneva Workday (uni_sitecarriere)", "url": "https://beneva.wd10.myworkdayjobs.com/uni_sitecarriere"},
    {"name": "Bentley Workday (faculty)", "url": "https://bentley.wd503.myworkdayjobs.com/faculty"},
    {"name": "Bentley Workday (staff)", "url": "https://bentley.wd503.myworkdayjobs.com/staff"},
    {"name": "Berklee Workday (berkleecareers)", "url": "https://berklee.wd1.myworkdayjobs.com/berkleecareers"},
    {"name": "Bernco Workday (berncocareers)", "url": "https://bernco.wd1.myworkdayjobs.com/berncocareers"},
    {"name": "Bernergroup Workday (careers_berner_group)", "url": "https://bernergroup.wd3.myworkdayjobs.com/careers_berner_group"},
    {"name": "Bestbuycanada Workday (bestbuyca_career)", "url": "https://bestbuycanada.wd3.myworkdayjobs.com/bestbuyca_career"},
    {"name": "Bestfriends Workday (bestfriendscareers)", "url": "https://bestfriends.wd1.myworkdayjobs.com/bestfriendscareers"},
    {"name": "Bestwestern Workday (careers)", "url": "https://bestwestern.wd1.myworkdayjobs.com/careers"},
    {"name": "Betmgminc Workday (betmgm)", "url": "https://betmgminc.wd5.myworkdayjobs.com/betmgm"},
    {"name": "Beyondmeat Workday (external_careers)", "url": "https://beyondmeat.wd1.myworkdayjobs.com/external_careers"},
    {"name": "Bf Workday (international)", "url": "https://bf.wd5.myworkdayjobs.com/international"},
    {"name": "Bf Workday (usa_canada)", "url": "https://bf.wd5.myworkdayjobs.com/usa_canada"},
    {"name": "Bgfoods Workday (bg_foods_careers)", "url": "https://bgfoods.wd1.myworkdayjobs.com/bg_foods_careers"},
    {"name": "Bgfoods Workday (equest)", "url": "https://bgfoods.wd1.myworkdayjobs.com/equest"},
    {"name": "Bhs Workday (careers)", "url": "https://bhs.wd1.myworkdayjobs.com/careers"},
    {"name": "Biamp Workday (biamp)", "url": "https://biamp.wd12.myworkdayjobs.com/biamp"},
    {"name": "Bigcommerce Workday (commerce)", "url": "https://bigcommerce.wd12.myworkdayjobs.com/commerce"},
    {"name": "Biibhr Workday (external)", "url": "https://biibhr.wd3.myworkdayjobs.com/external"},
    {"name": "Bilh Workday (external)", "url": "https://bilh.wd1.myworkdayjobs.com/external"},
    {"name": "Billgosling Workday (global)", "url": "https://billgosling.wd5.myworkdayjobs.com/global"},
    {"name": "Biltmore Workday (biltmorecareers)", "url": "https://biltmore.wd108.myworkdayjobs.com/biltmorecareers"},
    {"name": "Binus Workday (lifeatbinus)", "url": "https://binus.wd3.myworkdayjobs.com/lifeatbinus"},
    {"name": "Biomarpeople Workday (biomar)", "url": "https://biomarpeople.wd3.myworkdayjobs.com/biomar"},
    {"name": "Biotechne Workday (biotechne)", "url": "https://biotechne.wd5.myworkdayjobs.com/biotechne"},
    {"name": "Birch Workday (fusioncareers)", "url": "https://birch.wd1.myworkdayjobs.com/fusioncareers"},
    {"name": "Bird Workday (birdconstructioncareers)", "url": "https://bird.wd3.myworkdayjobs.com/birdconstructioncareers"},
    {"name": "Bird Workday (canemcareers)", "url": "https://bird.wd3.myworkdayjobs.com/canemcareers"},
    {"name": "Bird Workday (nasoncareers)", "url": "https://bird.wd3.myworkdayjobs.com/nasoncareers"},
    {"name": "Bison Workday (bisondrivercareers)", "url": "https://bison.wd3.myworkdayjobs.com/bisondrivercareers"},
    {"name": "Bison Workday (bisonnon-drivingcareers)", "url": "https://bison.wd3.myworkdayjobs.com/bisonnon-drivingcareers"},
    {"name": "Bitsight Workday (bitsight)", "url": "https://bitsight.wd1.myworkdayjobs.com/bitsight"},
    {"name": "Bitsight Workday (broadbean_external)", "url": "https://bitsight.wd1.myworkdayjobs.com/broadbean_external"},
    {"name": "Bizagi Workday (bizagi_career_site)", "url": "https://bizagi.wd3.myworkdayjobs.com/bizagi_career_site"},
    {"name": "Bjswholesaleclub Workday (bjscareers)", "url": "https://bjswholesaleclub.wd1.myworkdayjobs.com/bjscareers"},
    {"name": "Blackbaud Workday (externalcareers)", "url": "https://blackbaud.wd1.myworkdayjobs.com/externalcareers"},
    {"name": "Blackbaud Workday (justgiving)", "url": "https://blackbaud.wd1.myworkdayjobs.com/justgiving"},
    {"name": "Blackknight Workday (bkc)", "url": "https://blackknight.wd1.myworkdayjobs.com/bkc"},
    {"name": "Blackknight Workday (ics)", "url": "https://blackknight.wd1.myworkdayjobs.com/ics"},
    {"name": "Blackrock Workday (blackrock_professional)", "url": "https://blackrock.wd1.myworkdayjobs.com/blackrock_professional"},
    {"name": "Blackstone Workday (blackstone_campus_careers)", "url": "https://blackstone.wd1.myworkdayjobs.com/blackstone_campus_careers"},
    {"name": "Blackstone Workday (blackstone_careers)", "url": "https://blackstone.wd1.myworkdayjobs.com/blackstone_careers"},
    {"name": "Blackstone Workday (blackstone_internalcareers)", "url": "https://blackstone.wd1.myworkdayjobs.com/blackstone_internalcareers"},
    {"name": "Blackstone Workday (blackstone_technology)", "url": "https://blackstone.wd1.myworkdayjobs.com/blackstone_technology"},
    {"name": "Blackstone Workday (bx_external_site)", "url": "https://blackstone.wd1.myworkdayjobs.com/bx_external_site"},
    {"name": "Blackstone Workday (hec_paris)", "url": "https://blackstone.wd1.myworkdayjobs.com/hec_paris"},
    {"name": "Blattner Workday (blattnercompany)", "url": "https://blattner.wd5.myworkdayjobs.com/blattnercompany"},
    {"name": "Blattner Workday (blattnerenergy)", "url": "https://blattner.wd5.myworkdayjobs.com/blattnerenergy"},
    {"name": "Blattner Workday (dhb)", "url": "https://blattner.wd5.myworkdayjobs.com/dhb"},
    {"name": "Blgllp Workday (blg-external)", "url": "https://blgllp.wd10.myworkdayjobs.com/blg-external"},
    {"name": "Blinkcharging Workday (blinkcharging)", "url": "https://blinkcharging.wd5.myworkdayjobs.com/blinkcharging"},
    {"name": "Bloomberg Workday (bloombergindustrygroup_external_career_site)", "url": "https://bloomberg.wd1.myworkdayjobs.com/bloombergindustrygroup_external_career_site"},
    {"name": "Bloomenergy Workday (bloomenergycareers)", "url": "https://bloomenergy.wd1.myworkdayjobs.com/bloomenergycareers"},
    {"name": "Bluemeridian Workday (bmp-external)", "url": "https://bluemeridian.wd12.myworkdayjobs.com/bmp-external"},
    {"name": "Blueorigin Workday (blueorigin)", "url": "https://blueorigin.wd5.myworkdayjobs.com/blueorigin"},
    {"name": "Blueowl Workday (blueowl)", "url": "https://blueowl.wd1.myworkdayjobs.com/blueowl"},
    {"name": "Bluescopenac Workday (bnacareers)", "url": "https://bluescopenac.wd5.myworkdayjobs.com/bnacareers"},
    {"name": "Bmc Workday (bmc)", "url": "https://bmc.wd1.myworkdayjobs.com/bmc"},
    {"name": "Bmc Workday (clearwayhealth)", "url": "https://bmc.wd1.myworkdayjobs.com/clearwayhealth"},
    {"name": "Bmo Workday (campus)", "url": "https://bmo.wd3.myworkdayjobs.com/campus"},
    {"name": "Bmo Workday (external)", "url": "https://bmo.wd3.myworkdayjobs.com/external"},
    {"name": "Bmo Workday (external-air-miles)", "url": "https://bmo.wd3.myworkdayjobs.com/external-air-miles"},
    {"name": "Bmo Workday (privileged)", "url": "https://bmo.wd3.myworkdayjobs.com/privileged"},
    {"name": "Bne Workday (bne)", "url": "https://bne.wd1.myworkdayjobs.com/bne"},
    {"name": "Bne Workday (bneland)", "url": "https://bne.wd1.myworkdayjobs.com/bneland"},
    {"name": "Bne Workday (rosehill)", "url": "https://bne.wd1.myworkdayjobs.com/rosehill"},
    {"name": "Bnl Workday (externa)", "url": "https://bnl.wd1.myworkdayjobs.com/externa"},
    {"name": "Boarshead Workday (bhc)", "url": "https://boarshead.wd1.myworkdayjobs.com/bhc"},
    {"name": "Bobsdf Workday (bobs_careers)", "url": "https://bobsdf.wd1.myworkdayjobs.com/bobs_careers"},
    {"name": "Boeing Workday (aaeoy)", "url": "https://boeing.wd1.myworkdayjobs.com/aaeoy"},
    {"name": "Boeing Workday (alpfa)", "url": "https://boeing.wd1.myworkdayjobs.com/alpfa"},
    {"name": "Boeing Workday (clear)", "url": "https://boeing.wd1.myworkdayjobs.com/clear"},
    {"name": "Boeing Workday (external_careers)", "url": "https://boeing.wd1.myworkdayjobs.com/external_careers"},
    {"name": "Boeing Workday (external_recall)", "url": "https://boeing.wd1.myworkdayjobs.com/external_recall"},
    {"name": "Boeing Workday (external_subsidiary)", "url": "https://boeing.wd1.myworkdayjobs.com/external_subsidiary"},
    {"name": "Boeing Workday (external_ukcontingentworker)", "url": "https://boeing.wd1.myworkdayjobs.com/external_ukcontingentworker"},
    {"name": "Boeing Workday (incl)", "url": "https://boeing.wd1.myworkdayjobs.com/incl"},
    {"name": "Boeing Workday (intern)", "url": "https://boeing.wd1.myworkdayjobs.com/intern"},
    {"name": "Boeing Workday (mfg)", "url": "https://boeing.wd1.myworkdayjobs.com/mfg"},
    {"name": "Boeing Workday (naba)", "url": "https://boeing.wd1.myworkdayjobs.com/naba"},
    {"name": "Boeing Workday (nsbe)", "url": "https://boeing.wd1.myworkdayjobs.com/nsbe"},
    {"name": "Boeing Workday (pride)", "url": "https://boeing.wd1.myworkdayjobs.com/pride"},
    {"name": "Boeing Workday (sase)", "url": "https://boeing.wd1.myworkdayjobs.com/sase"},
    {"name": "Boeing Workday (shpe)", "url": "https://boeing.wd1.myworkdayjobs.com/shpe"},
    {"name": "Boeing Workday (tap2)", "url": "https://boeing.wd1.myworkdayjobs.com/tap2"},
    {"name": "Boeing Workday (usbln)", "url": "https://boeing.wd1.myworkdayjobs.com/usbln"},
    {"name": "Boeing Workday (wmb)", "url": "https://boeing.wd1.myworkdayjobs.com/wmb"},
    {"name": "Boeing Workday (woct)", "url": "https://boeing.wd1.myworkdayjobs.com/woct"},
    {"name": "Boliden Workday (bolidenjobs)", "url": "https://boliden.wd3.myworkdayjobs.com/bolidenjobs"},
    {"name": "Bonterra Workday (bonterratech)", "url": "https://bonterra.wd1.myworkdayjobs.com/bonterratech"},
    {"name": "Bonterra Workday (ngpvan)", "url": "https://bonterra.wd1.myworkdayjobs.com/ngpvan"},
    {"name": "Booster Workday (booster_careers)", "url": "https://booster.wd5.myworkdayjobs.com/booster_careers"},
    {"name": "Boracorpcdmo Workday (upshersmithcareers)", "url": "https://boracorpcdmo.wd108.myworkdayjobs.com/upshersmithcareers"},
    {"name": "Borgwarner Workday (borgwarner_careers)", "url": "https://borgwarner.wd5.myworkdayjobs.com/borgwarner_careers"},
    {"name": "Borrdrilling Workday (borr_career_site)", "url": "https://borrdrilling.wd103.myworkdayjobs.com/borr_career_site"},
    {"name": "Boseallaboutme Workday (bose_careers)", "url": "https://boseallaboutme.wd503.myworkdayjobs.com/bose_careers"},
    {"name": "Bostondynamics Workday (boston_dynamics)", "url": "https://bostondynamics.wd1.myworkdayjobs.com/boston_dynamics"},
    {"name": "Bouldercolorado Workday (external)", "url": "https://bouldercolorado.wd1.myworkdayjobs.com/external"},
    {"name": "Boydcorp Workday (boyd_careers)", "url": "https://boydcorp.wd12.myworkdayjobs.com/boyd_careers"},
    {"name": "Boydgroup Workday (boydcareers)", "url": "https://boydgroup.wd1.myworkdayjobs.com/boydcareers"},
    {"name": "Boystown Workday (boystowncareers)", "url": "https://boystown.wd1.myworkdayjobs.com/boystowncareers"},
    {"name": "Bozemanhealth Workday (bozemanhealthcareers)", "url": "https://bozemanhealth.wd1.myworkdayjobs.com/bozemanhealthcareers"},
    {"name": "Bpbcpa Workday (bbrec)", "url": "https://bpbcpa.wd5.myworkdayjobs.com/bbrec"},
    {"name": "Bpbcpa Workday (bpb)", "url": "https://bpbcpa.wd5.myworkdayjobs.com/bpb"},
    {"name": "Bpinternational Workday (bpcareers)", "url": "https://bpinternational.wd3.myworkdayjobs.com/bpcareers"},
    {"name": "Bpinternational Workday (bpprivateexternalcareerssite)", "url": "https://bpinternational.wd3.myworkdayjobs.com/bpprivateexternalcareerssite"},
    {"name": "Bracco Workday (braccocareers)", "url": "https://bracco.wd103.myworkdayjobs.com/braccocareers"},
    {"name": "Brambles Workday (brambles_careers)", "url": "https://brambles.wd5.myworkdayjobs.com/brambles_careers"},
    {"name": "Brandeis Workday (jobs)", "url": "https://brandeis.wd5.myworkdayjobs.com/jobs"},
    {"name": "Braunintertec Workday (brauninterteccareers)", "url": "https://braunintertec.wd5.myworkdayjobs.com/brauninterteccareers"},
    {"name": "Braunintertec Workday (edwards-pitmancareers)", "url": "https://braunintertec.wd5.myworkdayjobs.com/edwards-pitmancareers"},
    {"name": "Breakthrought1D Workday (breakthrought1d)", "url": "https://breakthrought1d.wd115.myworkdayjobs.com/breakthrought1d"},
    {"name": "Breakthru Workday (bbg-us)", "url": "https://breakthru.wd5.myworkdayjobs.com/bbg-us"},
    {"name": "Breakthru Workday (cdi-careers)", "url": "https://breakthru.wd5.myworkdayjobs.com/cdi-careers"},
    {"name": "Breakthru Workday (kindred-careers)", "url": "https://breakthru.wd5.myworkdayjobs.com/kindred-careers"},
    {"name": "Brenntag Workday (brenntag_jobs)", "url": "https://brenntag.wd3.myworkdayjobs.com/brenntag_jobs"},
    {"name": "Brevanhoward Workday (bh_externalcareers)", "url": "https://brevanhoward.wd3.myworkdayjobs.com/bh_externalcareers"},
    {"name": "Bridgeigp Workday (bcre)", "url": "https://bridgeigp.wd1.myworkdayjobs.com/bcre"},
    {"name": "Bridgeigp Workday (bigc)", "url": "https://bridgeigp.wd1.myworkdayjobs.com/bigc"},
    {"name": "Bridgeigp Workday (bpm)", "url": "https://bridgeigp.wd1.myworkdayjobs.com/bpm"},
    {"name": "Bridgestone Workday (external)", "url": "https://bridgestone.wd5.myworkdayjobs.com/external"},
    {"name": "Bridgestone Workday (latamexternalcareers)", "url": "https://bridgestone.wd5.myworkdayjobs.com/latamexternalcareers"},
    {"name": "Bridgestone Workday (wf_external_careers)", "url": "https://bridgestone.wd5.myworkdayjobs.com/wf_external_careers"},
    {"name": "Brighthorizons Workday (external-northamerica)", "url": "https://brighthorizons.wd5.myworkdayjobs.com/external-northamerica"},
    {"name": "Brighthorizons Workday (external-unitedkingdom)", "url": "https://brighthorizons.wd5.myworkdayjobs.com/external-unitedkingdom"},
    {"name": "Brightli Workday (brightlitalent)", "url": "https://brightli.wd5.myworkdayjobs.com/brightlitalent"},
    {"name": "Brilliancanada Workday (covenir)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/covenir"},
    {"name": "Brilliancanada Workday (dassian)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/dassian"},
    {"name": "Brilliancanada Workday (datapro)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/datapro"},
    {"name": "Brilliancanada Workday (drams)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/drams"},
    {"name": "Brilliancanada Workday (fivexfive)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/fivexfive"},
    {"name": "Brilliancanada Workday (helmoperations)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/helmoperations"},
    {"name": "Brilliancanada Workday (insuresoft)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/insuresoft"},
    {"name": "Brilliancanada Workday (omegro)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/omegro"},
    {"name": "Brilliancanada Workday (portfolioplus)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/portfolioplus"},
    {"name": "Brilliancanada Workday (quarzo)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/quarzo"},
    {"name": "Brilliancanada Workday (silvervine)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/silvervine"},
    {"name": "Brilliancanada Workday (ssp)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/ssp"},
    {"name": "Brilliancanada Workday (valuepro)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/valuepro"},
    {"name": "Brilliancanada Workday (vencora)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/vencora"},
    {"name": "Brilliancanada Workday (volaris)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/volaris"},
    {"name": "Brilliancanada Workday (wellingtonit)", "url": "https://brilliancanada.wd3.myworkdayjobs.com/wellingtonit"},
    {"name": "Brinks Workday (brinkscareers_row)", "url": "https://brinks.wd5.myworkdayjobs.com/brinkscareers_row"},
    {"name": "Brinks Workday (brinkscareerscanada)", "url": "https://brinks.wd5.myworkdayjobs.com/brinkscareerscanada"},
    {"name": "Brinks Workday (brinkscareersus)", "url": "https://brinks.wd5.myworkdayjobs.com/brinkscareersus"},
    {"name": "Brinks Workday (carreiras_brinks)", "url": "https://brinks.wd5.myworkdayjobs.com/carreiras_brinks"},
    {"name": "Brinks Workday (paiexternalcareers)", "url": "https://brinks.wd5.myworkdayjobs.com/paiexternalcareers"},
    {"name": "Brinks Workday (sitio_dominicana)", "url": "https://brinks.wd5.myworkdayjobs.com/sitio_dominicana"},
    {"name": "Brinks Workday (sitio_panama)", "url": "https://brinks.wd5.myworkdayjobs.com/sitio_panama"},
    {"name": "Brinks Workday (trabaja_con_nosotros)", "url": "https://brinks.wd5.myworkdayjobs.com/trabaja_con_nosotros"},
    {"name": "Bristolmyerssquibb Workday (bms)", "url": "https://bristolmyerssquibb.wd5.myworkdayjobs.com/bms"},
    {"name": "Bristow Workday (airnorth)", "url": "https://bristow.wd1.myworkdayjobs.com/airnorth"},
    {"name": "Bristow Workday (careers)", "url": "https://bristow.wd1.myworkdayjobs.com/careers"},
    {"name": "Broadinstitute Workday (broad_institute)", "url": "https://broadinstitute.wd1.myworkdayjobs.com/broad_institute"},
    {"name": "Broadlawns Workday (broadlawns_careers)", "url": "https://broadlawns.wd501.myworkdayjobs.com/broadlawns_careers"},
    {"name": "Broadridge Workday (careers)", "url": "https://broadridge.wd5.myworkdayjobs.com/careers"},
    {"name": "Broadviewfcu Workday (broadviewfcucareers)", "url": "https://broadviewfcu.wd1.myworkdayjobs.com/broadviewfcucareers"},
    {"name": "Brocku Workday (brocku_careers)", "url": "https://brocku.wd3.myworkdayjobs.com/brocku_careers"},
    {"name": "Brompton Workday (brompton)", "url": "https://brompton.wd3.myworkdayjobs.com/brompton"},
    {"name": "Bronsonhg Workday (medical-assistant)", "url": "https://bronsonhg.wd1.myworkdayjobs.com/medical-assistant"},
    {"name": "Bronsonhg Workday (newhires)", "url": "https://bronsonhg.wd1.myworkdayjobs.com/newhires"},
    {"name": "Bronsonhg Workday (nursing)", "url": "https://bronsonhg.wd1.myworkdayjobs.com/nursing"},
    {"name": "Bronsonhg Workday (physicianprovider)", "url": "https://bronsonhg.wd1.myworkdayjobs.com/physicianprovider"},
    {"name": "Brookfield Workday (blumontannuity)", "url": "https://brookfield.wd5.myworkdayjobs.com/blumontannuity"},
    {"name": "Brookfield Workday (bpandc)", "url": "https://brookfield.wd5.myworkdayjobs.com/bpandc"},
    {"name": "Brookfield Workday (brookfield)", "url": "https://brookfield.wd5.myworkdayjobs.com/brookfield"},
    {"name": "Brookfield Workday (brookfieldproperties)", "url": "https://brookfield.wd5.myworkdayjobs.com/brookfieldproperties"},
    {"name": "Brookfield Workday (ggp)", "url": "https://brookfield.wd5.myworkdayjobs.com/ggp"},
    {"name": "Brooksauto Workday (brooks_conversion_site)", "url": "https://brooksauto.wd1.myworkdayjobs.com/brooks_conversion_site"},
    {"name": "Brooksauto Workday (brooks_external_site)", "url": "https://brooksauto.wd1.myworkdayjobs.com/brooks_external_site"},
    {"name": "Brookshires Workday (bgc)", "url": "https://brookshires.wd108.myworkdayjobs.com/bgc"},
    {"name": "Brown Workday (staff-careers-brown)", "url": "https://brown.wd5.myworkdayjobs.com/staff-careers-brown"},
    {"name": "Brownadvisory Workday (brown)", "url": "https://brownadvisory.wd1.myworkdayjobs.com/brown"},
    {"name": "Brownadvisory Workday (brown-interns)", "url": "https://brownadvisory.wd1.myworkdayjobs.com/brown-interns"},
    {"name": "Brownhealth Workday (external_careers)", "url": "https://brownhealth.wd12.myworkdayjobs.com/external_careers"},
    {"name": "Browserstack Workday (external)", "url": "https://browserstack.wd3.myworkdayjobs.com/external"},
    {"name": "Brucepower Workday (brucepower)", "url": "https://brucepower.wd3.myworkdayjobs.com/brucepower"},
    {"name": "Brucepower Workday (brucepowerindigenousemploymentprogramwebsite)", "url": "https://brucepower.wd3.myworkdayjobs.com/brucepowerindigenousemploymentprogramwebsite"},
    {"name": "Brunellocucinelli Workday (cucinelli)", "url": "https://brunellocucinelli.wd3.myworkdayjobs.com/cucinelli"},
    {"name": "Brunswick Workday (searchemea)", "url": "https://brunswick.wd1.myworkdayjobs.com/searchemea"},
    {"name": "Bso Workday (bso)", "url": "https://bso.wd1.myworkdayjobs.com/bso"},
    {"name": "Bsu Workday (external)", "url": "https://bsu.wd12.myworkdayjobs.com/external"},
    {"name": "Btisolutions Workday (external)", "url": "https://btisolutions.wd12.myworkdayjobs.com/external"},
    {"name": "Bucknell Workday (external)", "url": "https://bucknell.wd1.myworkdayjobs.com/external"},
    {"name": "Bucks Workday (bccc)", "url": "https://bucks.wd1.myworkdayjobs.com/bccc"},
    {"name": "Bullhorn Workday (bullhorncareers)", "url": "https://bullhorn.wd1.myworkdayjobs.com/bullhorncareers"},
    {"name": "Bullish Workday (bullish)", "url": "https://bullish.wd3.myworkdayjobs.com/bullish"},
    {"name": "Bullish Workday (coindesk)", "url": "https://bullish.wd3.myworkdayjobs.com/coindesk"},
    {"name": "Bumble Workday (bumble_careers)", "url": "https://bumble.wd3.myworkdayjobs.com/bumble_careers"},
    {"name": "Bumble Workday (fruitz)", "url": "https://bumble.wd3.myworkdayjobs.com/fruitz"},
    {"name": "Buncombecounty Workday (buncombe_county_careers)", "url": "https://buncombecounty.wd1.myworkdayjobs.com/buncombe_county_careers"},
    {"name": "Bunnings Workday (careers)", "url": "https://bunnings.wd3.myworkdayjobs.com/careers"},
    {"name": "Bupa Workday (bguk_event_landing)", "url": "https://bupa.wd3.myworkdayjobs.com/bguk_event_landing"},
    {"name": "Bupa Workday (ext_career)", "url": "https://bupa.wd3.myworkdayjobs.com/ext_career"},
    {"name": "Burkert Workday (burkert-career)", "url": "https://burkert.wd502.myworkdayjobs.com/burkert-career"},
    {"name": "Burnesspaull Workday (our-vacancies)", "url": "https://burnesspaull.wd3.myworkdayjobs.com/our-vacancies"},
    {"name": "Bwcterminals Workday (bwc)", "url": "https://bwcterminals.wd12.myworkdayjobs.com/bwc"},
    {"name": "Bydeluxe Workday (deluxe_external)", "url": "https://bydeluxe.wd5.myworkdayjobs.com/deluxe_external"},
    {"name": "Byu Workday (byu-careers)", "url": "https://byu.wd1.myworkdayjobs.com/byu-careers"},
    {"name": "Byu Workday (faculty-careers)", "url": "https://byu.wd1.myworkdayjobs.com/faculty-careers"},
    {"name": "Bzam Workday (bzamcareers)", "url": "https://bzam.wd1.myworkdayjobs.com/bzamcareers"},
    {"name": "Caa Workday (campus)", "url": "https://caa.wd1.myworkdayjobs.com/campus"},
    {"name": "Caa Workday (careers)", "url": "https://caa.wd1.myworkdayjobs.com/careers"},
    {"name": "Cabinetworksgroup Workday (cabinetworksgroupcareers)", "url": "https://cabinetworksgroup.wd1.myworkdayjobs.com/cabinetworksgroupcareers"},
    {"name": "Cableone Workday (cable_one_external_careers)", "url": "https://cableone.wd1.myworkdayjobs.com/cable_one_external_careers"},
    {"name": "Cabotcorp Workday (careers)", "url": "https://cabotcorp.wd12.myworkdayjobs.com/careers"},
    {"name": "Cabrini Workday (cabrini)", "url": "https://cabrini.wd3.myworkdayjobs.com/cabrini"},
    {"name": "Caci Workday (external)", "url": "https://caci.wd1.myworkdayjobs.com/external"},
    {"name": "Caci Workday (referral)", "url": "https://caci.wd1.myworkdayjobs.com/referral"},
    {"name": "Cadence Workday (addl_jobs)", "url": "https://cadence.wd1.myworkdayjobs.com/addl_jobs"},
    {"name": "Cadence Workday (openeye_careers)", "url": "https://cadence.wd1.myworkdayjobs.com/openeye_careers"},
    {"name": "Cadence Workday (univ_careers)", "url": "https://cadence.wd1.myworkdayjobs.com/univ_careers"},
    {"name": "Cadence Workday (university_talent)", "url": "https://cadence.wd1.myworkdayjobs.com/university_talent"},
    {"name": "Cae Workday (career)", "url": "https://cae.wd3.myworkdayjobs.com/career"},
    {"name": "Cai Workday (computer_aid)", "url": "https://cai.wd5.myworkdayjobs.com/computer_aid"},
    {"name": "Calabrio Workday (calabriohq)", "url": "https://calabrio.wd5.myworkdayjobs.com/calabriohq"},
    {"name": "Calibercollision Workday (caliber)", "url": "https://calibercollision.wd1.myworkdayjobs.com/caliber"},
    {"name": "Calibercollision Workday (protech)", "url": "https://calibercollision.wd1.myworkdayjobs.com/protech"},
    {"name": "Calistacorp Workday (calista)", "url": "https://calistacorp.wd1.myworkdayjobs.com/calista"},
    {"name": "Calistacorp Workday (calistabrice)", "url": "https://calistacorp.wd1.myworkdayjobs.com/calistabrice"},
    {"name": "Calistacorp Workday (calistainternship)", "url": "https://calistacorp.wd1.myworkdayjobs.com/calistainternship"},
    {"name": "Calistacorp Workday (talentbank)", "url": "https://calistacorp.wd1.myworkdayjobs.com/talentbank"},
    {"name": "Calistacorp Workday (yulista)", "url": "https://calistacorp.wd1.myworkdayjobs.com/yulista"},
    {"name": "Calix Workday (external)", "url": "https://calix.wd1.myworkdayjobs.com/external"},
    {"name": "Calix Workday (externalinternational)", "url": "https://calix.wd1.myworkdayjobs.com/externalinternational"},
    {"name": "Callaghaninnovation Workday (external)", "url": "https://callaghaninnovation.wd105.myworkdayjobs.com/external"},
    {"name": "Calpolycorporation Workday (calpolypartner_external_careers)", "url": "https://calpolycorporation.wd12.myworkdayjobs.com/calpolypartner_external_careers"},
    {"name": "Calvin Workday (calvinuniversitycareers)", "url": "https://calvin.wd5.myworkdayjobs.com/calvinuniversitycareers"},
    {"name": "Calwatergroup Workday (cwsg)", "url": "https://calwatergroup.wd5.myworkdayjobs.com/cwsg"},
    {"name": "Calyx Workday (perceptive)", "url": "https://calyx.wd1.myworkdayjobs.com/perceptive"},
    {"name": "Camacollc Workday (camaco)", "url": "https://camacollc.wd108.myworkdayjobs.com/camaco"},
    {"name": "Cambiahealth Workday (external)", "url": "https://cambiahealth.wd504.myworkdayjobs.com/external"},
    {"name": "Cambiumlearning Workday (camb)", "url": "https://cambiumlearning.wd1.myworkdayjobs.com/camb"},
    {"name": "Cambiumlearning Workday (opportunities)", "url": "https://cambiumlearning.wd1.myworkdayjobs.com/opportunities"},
    {"name": "Cambria Workday (cambria_careers)", "url": "https://cambria.wd1.myworkdayjobs.com/cambria_careers"},
    {"name": "Cambridgeassociates Workday (cambridge_associates)", "url": "https://cambridgeassociates.wd5.myworkdayjobs.com/cambridge_associates"},
    {"name": "Cambro Workday (cambro_careers)", "url": "https://cambro.wd5.myworkdayjobs.com/cambro_careers"},
    {"name": "Camdennational Workday (cnb-careers)", "url": "https://camdennational.wd12.myworkdayjobs.com/cnb-careers"},
    {"name": "Campaignmonitor Workday (marigold)", "url": "https://campaignmonitor.wd5.myworkdayjobs.com/marigold"},
    {"name": "Campbellsoup Workday (externalcareers_globalsite)", "url": "https://campbellsoup.wd5.myworkdayjobs.com/externalcareers_globalsite"},
    {"name": "Campbellsville Workday (cu_workstudy)", "url": "https://campbellsville.wd1.myworkdayjobs.com/cu_workstudy"},
    {"name": "Campingworld Workday (jobs)", "url": "https://campingworld.wd5.myworkdayjobs.com/jobs"},
    {"name": "Canadagoose Workday (canadagoosecareers)", "url": "https://canadagoose.wd3.myworkdayjobs.com/canadagoosecareers"},
    {"name": "Canadiansolar Workday (canadiansolar)", "url": "https://canadiansolar.wd5.myworkdayjobs.com/canadiansolar"},
    {"name": "Canadiansolar Workday (estorage)", "url": "https://canadiansolar.wd5.myworkdayjobs.com/estorage"},
    {"name": "Canadiantirecorporation Workday (enterprise_external_careers_site)", "url": "https://canadiantirecorporation.wd3.myworkdayjobs.com/enterprise_external_careers_site"},
    {"name": "Canalbarge Workday (cbc)", "url": "https://canalbarge.wd5.myworkdayjobs.com/cbc"},
    {"name": "Canarywharf Workday (canarywharf)", "url": "https://canarywharf.wd103.myworkdayjobs.com/canarywharf"},
    {"name": "Cancerresearchuk Workday (broadbean_external)", "url": "https://cancerresearchuk.wd3.myworkdayjobs.com/broadbean_external"},
    {"name": "Cancerresearchuk Workday (external_careers)", "url": "https://cancerresearchuk.wd3.myworkdayjobs.com/external_careers"},
    {"name": "Candescent Workday (hiring)", "url": "https://candescent.wd501.myworkdayjobs.com/hiring"},
    {"name": "Canohealth Workday (ach-careers)", "url": "https://canohealth.wd5.myworkdayjobs.com/ach-careers"},
    {"name": "Canopygrowth Workday (canopy_growth_external_career_site)", "url": "https://canopygrowth.wd3.myworkdayjobs.com/canopy_growth_external_career_site"},
    {"name": "Capefearvalley Workday (cfv)", "url": "https://capefearvalley.wd1.myworkdayjobs.com/cfv"},
    {"name": "Capgroup Workday (capitalgroupcareers)", "url": "https://capgroup.wd1.myworkdayjobs.com/capitalgroupcareers"},
    {"name": "Capita Workday (capitaglobal)", "url": "https://capita.wd3.myworkdayjobs.com/capitaglobal"},
    {"name": "Capitaland Workday (broadbean_external)", "url": "https://capitaland.wd3.myworkdayjobs.com/broadbean_external"},
    {"name": "Capitaland Workday (capitalanddevelopment)", "url": "https://capitaland.wd3.myworkdayjobs.com/capitalanddevelopment"},
    {"name": "Capitaland Workday (capitalandgroup)", "url": "https://capitaland.wd3.myworkdayjobs.com/capitalandgroup"},
    {"name": "Capitaland Workday (capitalandinvestmentcareers)", "url": "https://capitaland.wd3.myworkdayjobs.com/capitalandinvestmentcareers"},
    {"name": "Capitalauto Workday (capitalautogroupcareers)", "url": "https://capitalauto.wd3.myworkdayjobs.com/capitalautogroupcareers"},
    {"name": "Capitalauto Workday (capitalfordlincolncareers)", "url": "https://capitalauto.wd3.myworkdayjobs.com/capitalfordlincolncareers"},
    {"name": "Capitalauto Workday (capitalgmcareersite)", "url": "https://capitalauto.wd3.myworkdayjobs.com/capitalgmcareersite"},
    {"name": "Capitalauto Workday (universalcollisioncentre)", "url": "https://capitalauto.wd3.myworkdayjobs.com/universalcollisioncentre"},
    {"name": "Capitalhealth Workday (capitalhealthcareers)", "url": "https://capitalhealth.wd1.myworkdayjobs.com/capitalhealthcareers"},
    {"name": "Capitalone Workday (capital_one)", "url": "https://capitalone.wd12.myworkdayjobs.com/capital_one"},
    {"name": "Capitalone Workday (discover_capitalone)", "url": "https://capitalone.wd12.myworkdayjobs.com/discover_capitalone"},
    {"name": "Capitalpower Workday (external)", "url": "https://capitalpower.wd10.myworkdayjobs.com/external"},
    {"name": "Capri Workday (jimmychoocareers)", "url": "https://capri.wd1.myworkdayjobs.com/jimmychoocareers"},
    {"name": "Capri Workday (michael_kors)", "url": "https://capri.wd1.myworkdayjobs.com/michael_kors"},
    {"name": "Capri Workday (versace)", "url": "https://capri.wd1.myworkdayjobs.com/versace"},
    {"name": "Carbery Workday (synergy_external_careers)", "url": "https://carbery.wd3.myworkdayjobs.com/synergy_external_careers"},
    {"name": "Carbery Workday (synergy_usa_external_careers)", "url": "https://carbery.wd3.myworkdayjobs.com/synergy_usa_external_careers"},
    {"name": "Carbonhealth Workday (careers)", "url": "https://carbonhealth.wd1.myworkdayjobs.com/careers"},
    {"name": "Cardinalhealth Workday (contractor)", "url": "https://cardinalhealth.wd1.myworkdayjobs.com/contractor"},
    {"name": "Cardinalhealth Workday (ext)", "url": "https://cardinalhealth.wd1.myworkdayjobs.com/ext"},
    {"name": "Cardinalhealth Workday (navista)", "url": "https://cardinalhealth.wd1.myworkdayjobs.com/navista"},
    {"name": "Cardlytics Workday (cardlyticsexternalcareersite)", "url": "https://cardlytics.wd5.myworkdayjobs.com/cardlyticsexternalcareersite"},
    {"name": "Cardworks Workday (cardworks_external)", "url": "https://cardworks.wd12.myworkdayjobs.com/cardworks_external"},
    {"name": "Cardworks Workday (merrick_bank_external)", "url": "https://cardworks.wd12.myworkdayjobs.com/merrick_bank_external"},
    {"name": "Careabout Workday (adjuvant_careers)", "url": "https://careabout.wd5.myworkdayjobs.com/adjuvant_careers"},
    {"name": "Careabout Workday (consensus_careers)", "url": "https://careabout.wd5.myworkdayjobs.com/consensus_careers"},
    {"name": "Careabout Workday (mspb_careers)", "url": "https://careabout.wd5.myworkdayjobs.com/mspb_careers"},
    {"name": "Careoregon Workday (hcp)", "url": "https://careoregon.wd12.myworkdayjobs.com/hcp"},
    {"name": "Careoregon Workday (hso)", "url": "https://careoregon.wd12.myworkdayjobs.com/hso"},
    {"name": "Caresource Workday (caresource)", "url": "https://caresource.wd1.myworkdayjobs.com/caresource"},
    {"name": "Caresource Workday (caresourcemilitaryveterans)", "url": "https://caresource.wd1.myworkdayjobs.com/caresourcemilitaryveterans"},
    {"name": "Carilionclinic Workday (external_careers)", "url": "https://carilionclinic.wd12.myworkdayjobs.com/external_careers"},
    {"name": "Carleton Workday (carletoncareers)", "url": "https://carleton.wd1.myworkdayjobs.com/carletoncareers"},
    {"name": "Carlislellc Workday (carlisle-corporatecareers)", "url": "https://carlislellc.wd5.myworkdayjobs.com/carlisle-corporatecareers"},
    {"name": "Carlislellc Workday (crg)", "url": "https://carlislellc.wd5.myworkdayjobs.com/crg"},
    {"name": "Carlislellc Workday (wendys-careers)", "url": "https://carlislellc.wd5.myworkdayjobs.com/wendys-careers"},
    {"name": "Carmax Workday (external)", "url": "https://carmax.wd1.myworkdayjobs.com/external"},
    {"name": "Carrier Workday (jobs)", "url": "https://carrier.wd5.myworkdayjobs.com/jobs"},
    {"name": "Carrier Workday (kgs_jobs)", "url": "https://carrier.wd5.myworkdayjobs.com/kgs_jobs"},
    {"name": "Cars Workday (cars)", "url": "https://cars.wd12.myworkdayjobs.com/cars"},
    {"name": "Cart Workday (cart)", "url": "https://cart.wd1.myworkdayjobs.com/cart"},
    {"name": "Cartech Workday (ctcexternal)", "url": "https://cartech.wd5.myworkdayjobs.com/ctcexternal"},
    {"name": "Carters Workday (carterscareers)", "url": "https://carters.wd1.myworkdayjobs.com/carterscareers"},
    {"name": "Cat Workday (caterpillarcareers)", "url": "https://cat.wd5.myworkdayjobs.com/caterpillarcareers"},
    {"name": "Cat Workday (solarturbines)", "url": "https://cat.wd5.myworkdayjobs.com/solarturbines"},
    {"name": "Catalent Workday (external)", "url": "https://catalent.wd1.myworkdayjobs.com/external"},
    {"name": "Catalent Workday (external_bloomington)", "url": "https://catalent.wd1.myworkdayjobs.com/external_bloomington"},
    {"name": "Catalight Workday (catalight)", "url": "https://catalight.wd1.myworkdayjobs.com/catalight"},
    {"name": "Catalyte Workday (catalyte)", "url": "https://catalyte.wd503.myworkdayjobs.com/catalyte"},
    {"name": "Cba Workday (bankwest_careers)", "url": "https://cba.wd3.myworkdayjobs.com/bankwest_careers"},
    {"name": "Cba Workday (commbank_careers)", "url": "https://cba.wd3.myworkdayjobs.com/commbank_careers"},
    {"name": "Cba Workday (private_ad)", "url": "https://cba.wd3.myworkdayjobs.com/private_ad"},
    {"name": "Cba Workday (x15_careers)", "url": "https://cba.wd3.myworkdayjobs.com/x15_careers"},
    {"name": "Cbcrc Workday (cbc_radio-canada_jobs)", "url": "https://cbcrc.wd3.myworkdayjobs.com/cbc_radio-canada_jobs"},
    {"name": "Cbecompanies Workday (cbecompanies)", "url": "https://cbecompanies.wd1.myworkdayjobs.com/cbecompanies"},
    {"name": "Cbgoc Workday (cbci)", "url": "https://cbgoc.wd3.myworkdayjobs.com/cbci"},
    {"name": "Cbh Workday (cherrybekaert)", "url": "https://cbh.wd12.myworkdayjobs.com/cherrybekaert"},
    {"name": "Cbh Workday (cherrybekaertearlycareers)", "url": "https://cbh.wd12.myworkdayjobs.com/cherrybekaertearlycareers"},
    {"name": "Cboe Workday (external_career_cboe)", "url": "https://cboe.wd1.myworkdayjobs.com/external_career_cboe"},
    {"name": "Cbrands Workday (cbi_external_careers)", "url": "https://cbrands.wd5.myworkdayjobs.com/cbi_external_careers"},
    {"name": "Cc Workday (chanelcareers)", "url": "https://cc.wd3.myworkdayjobs.com/chanelcareers"},
    {"name": "Cca Workday (cca)", "url": "https://cca.wd5.myworkdayjobs.com/cca"},
    {"name": "Cca Workday (cca_ypce)", "url": "https://cca.wd5.myworkdayjobs.com/cca_ypce"},
    {"name": "Ccc Workday (ccc_external)", "url": "https://ccc.wd5.myworkdayjobs.com/ccc_external"},
    {"name": "Cccis Workday (broadbean_external)", "url": "https://cccis.wd1.myworkdayjobs.com/broadbean_external"},
    {"name": "Ccf Workday (clevelandcliniccareers)", "url": "https://ccf.wd1.myworkdayjobs.com/clevelandcliniccareers"},
    {"name": "Ccf Workday (clevelandcliniccareersuk)", "url": "https://ccf.wd1.myworkdayjobs.com/clevelandcliniccareersuk"},
    {"name": "Ccitc Workday (marathon_county_careers)", "url": "https://ccitc.wd1.myworkdayjobs.com/marathon_county_careers"},
    {"name": "Ccrcca Workday (careers)", "url": "https://ccrcca.wd1.myworkdayjobs.com/careers"},
    {"name": "Ccsbts Workday (ccsbts)", "url": "https://ccsbts.wd12.myworkdayjobs.com/ccsbts"},
    {"name": "Cdfh Workday (external)", "url": "https://cdfh.wd3.myworkdayjobs.com/external"},
    {"name": "Cdk Workday (cdk)", "url": "https://cdk.wd1.myworkdayjobs.com/cdk"},
    {"name": "Cdpq Workday (cdpq)", "url": "https://cdpq.wd10.myworkdayjobs.com/cdpq"},
    {"name": "Cdpq Workday (cdpq-recrutement-universitaire)", "url": "https://cdpq.wd10.myworkdayjobs.com/cdpq-recrutement-universitaire"},
    {"name": "Cdpqfiliales Workday (cdpq-infra)", "url": "https://cdpqfiliales.wd10.myworkdayjobs.com/cdpq-infra"},
    {"name": "Cecentertainment Workday (adventure_world)", "url": "https://cecentertainment.wd5.myworkdayjobs.com/adventure_world"},
    {"name": "Cecentertainment Workday (cec_careers)", "url": "https://cecentertainment.wd5.myworkdayjobs.com/cec_careers"},
    {"name": "Cecentertainment Workday (peter_piper_pizza_careers)", "url": "https://cecentertainment.wd5.myworkdayjobs.com/peter_piper_pizza_careers"},
    {"name": "Cefcu Workday (cefcu)", "url": "https://cefcu.wd5.myworkdayjobs.com/cefcu"},
    {"name": "Ceh Workday (ceh_careers)", "url": "https://ceh.wd3.myworkdayjobs.com/ceh_careers"},
    {"name": "Cemstone Workday (cemstonecareers)", "url": "https://cemstone.wd1.myworkdayjobs.com/cemstonecareers"},
    {"name": "Cemstone Workday (tccmaterialscareers)", "url": "https://cemstone.wd1.myworkdayjobs.com/tccmaterialscareers"},
    {"name": "Cengage Workday (cengageemeacareers)", "url": "https://cengage.wd5.myworkdayjobs.com/cengageemeacareers"},
    {"name": "Cengage Workday (cengageindiacareers)", "url": "https://cengage.wd5.myworkdayjobs.com/cengageindiacareers"},
    {"name": "Cengage Workday (cengagenorthamericacareers)", "url": "https://cengage.wd5.myworkdayjobs.com/cengagenorthamericacareers"},
    {"name": "Cenhud Workday (cenhud)", "url": "https://cenhud.wd5.myworkdayjobs.com/cenhud"},
    {"name": "Cenovus Workday (careers)", "url": "https://cenovus.wd3.myworkdayjobs.com/careers"},
    {"name": "Centene Workday (centene_external)", "url": "https://centene.wd5.myworkdayjobs.com/centene_external"},
    {"name": "Centerstone Workday (centerstonecareers)", "url": "https://centerstone.wd5.myworkdayjobs.com/centerstonecareers"},
    {"name": "Centier Workday (centierbankcareers)", "url": "https://centier.wd501.myworkdayjobs.com/centierbankcareers"},
    {"name": "Centific Workday (centific_global)", "url": "https://centific.wd1.myworkdayjobs.com/centific_global"},
    {"name": "Central1Resources Workday (central1)", "url": "https://central1resources.wd10.myworkdayjobs.com/central1"},
    {"name": "Centralparknyc Workday (central_park_conservancy)", "url": "https://centralparknyc.wd12.myworkdayjobs.com/central_park_conservancy"},
    {"name": "Centrica Workday (centrica)", "url": "https://centrica.wd3.myworkdayjobs.com/centrica"},
    {"name": "Centricsoftware Workday (centric)", "url": "https://centricsoftware.wd501.myworkdayjobs.com/centric"},
    {"name": "Centrify Workday (external)", "url": "https://centrify.wd1.myworkdayjobs.com/external"},
    {"name": "Cerberus Workday (cerberuscareers)", "url": "https://cerberus.wd1.myworkdayjobs.com/cerberuscareers"},
    {"name": "Cerence Workday (cerence)", "url": "https://cerence.wd5.myworkdayjobs.com/cerence"},
    {"name": "Ceritypartners Workday (ceritypartnerscareers)", "url": "https://ceritypartners.wd12.myworkdayjobs.com/ceritypartnerscareers"},
    {"name": "Cerved Workday (cerved)", "url": "https://cerved.wd3.myworkdayjobs.com/cerved"},
    {"name": "Ceu Workday (blb)", "url": "https://ceu.wd3.myworkdayjobs.com/blb"},
    {"name": "Ceu Workday (ceu)", "url": "https://ceu.wd3.myworkdayjobs.com/ceu"},
    {"name": "Ceu Workday (uf3)", "url": "https://ceu.wd3.myworkdayjobs.com/uf3"},
    {"name": "Cfbhfg Workday (externalcareers)", "url": "https://cfbhfg.wd12.myworkdayjobs.com/externalcareers"},
    {"name": "Cfdsny Workday (cfds)", "url": "https://cfdsny.wd12.myworkdayjobs.com/cfds"},
    {"name": "Cfindustries Workday (careers)", "url": "https://cfindustries.wd1.myworkdayjobs.com/careers"},
    {"name": "Cfr Workday (buc-careers)", "url": "https://cfr.wd5.myworkdayjobs.com/buc-careers"},
    {"name": "Cgb Workday (cgb_careers)", "url": "https://cgb.wd5.myworkdayjobs.com/cgb_careers"},
    {"name": "Cgg Workday (deregtcareers)", "url": "https://cgg.wd103.myworkdayjobs.com/deregtcareers"},
    {"name": "Cgg Workday (sercelcareer)", "url": "https://cgg.wd103.myworkdayjobs.com/sercelcareer"},
    {"name": "Cgg Workday (viridiencareers)", "url": "https://cgg.wd103.myworkdayjobs.com/viridiencareers"},
    {"name": "Cgm Workday (cgm)", "url": "https://cgm.wd3.myworkdayjobs.com/cgm"},
    {"name": "Chamberlain Workday (chamberlain_group)", "url": "https://chamberlain.wd1.myworkdayjobs.com/chamberlain_group"},
    {"name": "Chamberlain Workday (systems_llc)", "url": "https://chamberlain.wd1.myworkdayjobs.com/systems_llc"},
    {"name": "Championx Workday (championx_external)", "url": "https://championx.wd1.myworkdayjobs.com/championx_external"},
    {"name": "Chaptershealth Workday (capitalcaringjobs)", "url": "https://chaptershealth.wd5.myworkdayjobs.com/capitalcaringjobs"},
    {"name": "Chaptershealth Workday (jobs)", "url": "https://chaptershealth.wd5.myworkdayjobs.com/jobs"},
    {"name": "Chaptershealth Workday (securjobs)", "url": "https://chaptershealth.wd5.myworkdayjobs.com/securjobs"},
    {"name": "Charleskeith Workday (external)", "url": "https://charleskeith.wd3.myworkdayjobs.com/external"},
    {"name": "Charlottenc Workday (citgov)", "url": "https://charlottenc.wd12.myworkdayjobs.com/citgov"},
    {"name": "Charlottenc Workday (citgovintern)", "url": "https://charlottenc.wd12.myworkdayjobs.com/citgovintern"},
    {"name": "Chartermfg Workday (charter_careers)", "url": "https://chartermfg.wd5.myworkdayjobs.com/charter_careers"},
    {"name": "Chas Workday (desertsage)", "url": "https://chas.wd1.myworkdayjobs.com/desertsage"},
    {"name": "Chatham Workday (chathamuniversity)", "url": "https://chatham.wd12.myworkdayjobs.com/chathamuniversity"},
    {"name": "Chatham Workday (chathamuniversitystudent)", "url": "https://chatham.wd12.myworkdayjobs.com/chathamuniversitystudent"},
    {"name": "Chaucergroup Workday (chaucer_group_careers)", "url": "https://chaucergroup.wd3.myworkdayjobs.com/chaucer_group_careers"},
    {"name": "Checkout Workday (checkoutcareers)", "url": "https://checkout.wd3.myworkdayjobs.com/checkoutcareers"},
    {"name": "Chenmed Workday (chenmed)", "url": "https://chenmed.wd1.myworkdayjobs.com/chenmed"},
    {"name": "Cheo Workday (external_site)", "url": "https://cheo.wd10.myworkdayjobs.com/external_site"},
    {"name": "Chess Workday (chess)", "url": "https://chess.wd1.myworkdayjobs.com/chess"},
    {"name": "Chess Workday (cnmjobs)", "url": "https://chess.wd1.myworkdayjobs.com/cnmjobs"},
    {"name": "Chess Workday (lccjobs)", "url": "https://chess.wd1.myworkdayjobs.com/lccjobs"},
    {"name": "Chess Workday (nnmcjobs)", "url": "https://chess.wd1.myworkdayjobs.com/nnmcjobs"},
    {"name": "Chess Workday (sfcc)", "url": "https://chess.wd1.myworkdayjobs.com/sfcc"},
    {"name": "Chess Workday (sjc)", "url": "https://chess.wd1.myworkdayjobs.com/sjc"},
    {"name": "Chesterfield Workday (ccps1)", "url": "https://chesterfield.wd5.myworkdayjobs.com/ccps1"},
    {"name": "Chevron Workday (jobs)", "url": "https://chevron.wd5.myworkdayjobs.com/jobs"},
    {"name": "Chevron Workday (university)", "url": "https://chevron.wd5.myworkdayjobs.com/university"},
    {"name": "Chfs Workday (chfs)", "url": "https://chfs.wd12.myworkdayjobs.com/chfs"},
    {"name": "Chg Workday (can_external_career_site)", "url": "https://chg.wd5.myworkdayjobs.com/can_external_career_site"},
    {"name": "Chg Workday (us_external_career_site)", "url": "https://chg.wd5.myworkdayjobs.com/us_external_career_site"},
    {"name": "Chghealthcare Workday (external)", "url": "https://chghealthcare.wd1.myworkdayjobs.com/external"},
    {"name": "Childrensinstitute Workday (cii)", "url": "https://childrensinstitute.wd5.myworkdayjobs.com/cii"},
    {"name": "Childrensplace Workday (tcp01)", "url": "https://childrensplace.wd1.myworkdayjobs.com/tcp01"},
    {"name": "Childrensplace Workday (tcp02)", "url": "https://childrensplace.wd1.myworkdayjobs.com/tcp02"},
    {"name": "Childrensplace Workday (tcp03)", "url": "https://childrensplace.wd1.myworkdayjobs.com/tcp03"},
    {"name": "Childrensplace Workday (tcp04)", "url": "https://childrensplace.wd1.myworkdayjobs.com/tcp04"},
    {"name": "Childrensplace Workday (tcp05)", "url": "https://childrensplace.wd1.myworkdayjobs.com/tcp05"},
    {"name": "Childrensplace Workday (tcp06)", "url": "https://childrensplace.wd1.myworkdayjobs.com/tcp06"},
    {"name": "Choicehotels Workday (external)", "url": "https://choicehotels.wd5.myworkdayjobs.com/external"},
    {"name": "Choicehotels Workday (hotelexternal)", "url": "https://choicehotels.wd5.myworkdayjobs.com/hotelexternal"},
    {"name": "Choicehotels Workday (skytouch)", "url": "https://choicehotels.wd5.myworkdayjobs.com/skytouch"},
    {"name": "Chordenergy Workday (external)", "url": "https://chordenergy.wd1.myworkdayjobs.com/external"},
    {"name": "Christianacare Workday (cchs)", "url": "https://christianacare.wd5.myworkdayjobs.com/cchs"},
    {"name": "Christies Workday (christies_careers)", "url": "https://christies.wd3.myworkdayjobs.com/christies_careers"},
    {"name": "Christies Workday (christies_careers_private)", "url": "https://christies.wd3.myworkdayjobs.com/christies_careers_private"},
    {"name": "Christmanco Workday (careers)", "url": "https://christmanco.wd108.myworkdayjobs.com/careers"},
    {"name": "Chrobinson Workday (chrobinson)", "url": "https://chrobinson.wd5.myworkdayjobs.com/chrobinson"},
    {"name": "Chtgroup Workday (cht-jobs)", "url": "https://chtgroup.wd3.myworkdayjobs.com/cht-jobs"},
    {"name": "Chubbfiresecurity Workday (chubbfs)", "url": "https://chubbfiresecurity.wd3.myworkdayjobs.com/chubbfs"},
    {"name": "Churchdwight Workday (animalnutritioncareers)", "url": "https://churchdwight.wd1.myworkdayjobs.com/animalnutritioncareers"},
    {"name": "Churchdwight Workday (chdcanadacareers)", "url": "https://churchdwight.wd1.myworkdayjobs.com/chdcanadacareers"},
    {"name": "Churchdwight Workday (chdcareers)", "url": "https://churchdwight.wd1.myworkdayjobs.com/chdcareers"},
    {"name": "Churchdwight Workday (chdukcareers)", "url": "https://churchdwight.wd1.myworkdayjobs.com/chdukcareers"},
    {"name": "Churchdwight Workday (waterpikcareers)", "url": "https://churchdwight.wd1.myworkdayjobs.com/waterpikcareers"},
    {"name": "Churchs Workday (corporate)", "url": "https://churchs.wd1.myworkdayjobs.com/corporate"},
    {"name": "Churchs Workday (field)", "url": "https://churchs.wd1.myworkdayjobs.com/field"},
    {"name": "Ci Workday (ci_financial_external_career-site)", "url": "https://ci.wd3.myworkdayjobs.com/ci_financial_external_career-site"},
    {"name": "Ci Workday (corient_external_career-site)", "url": "https://ci.wd3.myworkdayjobs.com/corient_external_career-site"},
    {"name": "Cibc Workday (campus)", "url": "https://cibc.wd3.myworkdayjobs.com/campus"},
    {"name": "Ciena Workday (careers)", "url": "https://ciena.wd5.myworkdayjobs.com/careers"},
    {"name": "Cigna Workday (cignacareers)", "url": "https://cigna.wd5.myworkdayjobs.com/cignacareers"},
    {"name": "Cincinnatichildrens Workday (careersatcincinnatichildrens)", "url": "https://cincinnatichildrens.wd5.myworkdayjobs.com/careersatcincinnatichildrens"},
    {"name": "Cinemark Workday (cinemark)", "url": "https://cinemark.wd1.myworkdayjobs.com/cinemark"},
    {"name": "Cineplex Workday (cineplex)", "url": "https://cineplex.wd3.myworkdayjobs.com/cineplex"},
    {"name": "Cip Workday (cip_mt)", "url": "https://cip.wd103.myworkdayjobs.com/cip_mt"},
    {"name": "Cip Workday (cip_tt)", "url": "https://cip.wd103.myworkdayjobs.com/cip_tt"},
    {"name": "Cip Workday (cipfs_careers)", "url": "https://cip.wd103.myworkdayjobs.com/cipfs_careers"},
    {"name": "Circle Workday (circle)", "url": "https://circle.wd1.myworkdayjobs.com/circle"},
    {"name": "Circlehealth Workday (chgcareers)", "url": "https://circlehealth.wd103.myworkdayjobs.com/chgcareers"},
    {"name": "Circlek Workday (circlekstorejobs)", "url": "https://circlek.wd3.myworkdayjobs.com/circlekstorejobs"},
    {"name": "Circles Workday (circles)", "url": "https://circles.wd103.myworkdayjobs.com/circles"},
    {"name": "Cirichlandwa Workday (cor)", "url": "https://cirichlandwa.wd12.myworkdayjobs.com/cor"},
    {"name": "Cisco Workday (cisco_careers)", "url": "https://cisco.wd5.myworkdayjobs.com/cisco_careers"},
    {"name": "Cisecurity Workday (cis_external_career_site)", "url": "https://cisecurity.wd1.myworkdayjobs.com/cis_external_career_site"},
    {"name": "Citi Workday (citi_early_careers_events_site)", "url": "https://citi.wd5.myworkdayjobs.com/citi_early_careers_events_site"},
    {"name": "Citicclsa Workday (external)", "url": "https://citicclsa.wd3.myworkdayjobs.com/external"},
    {"name": "Citjpl Workday (jobs)", "url": "https://citjpl.wd5.myworkdayjobs.com/jobs"},
    {"name": "City Workday (cfm)", "url": "https://city.wd1.myworkdayjobs.com/cfm"},
    {"name": "City Workday (cityus)", "url": "https://city.wd1.myworkdayjobs.com/cityus"},
    {"name": "Cityblockhealth Workday (cityblockexternalcareersite)", "url": "https://cityblockhealth.wd1.myworkdayjobs.com/cityblockexternalcareersite"},
    {"name": "Cityofgainesville Workday (careers)", "url": "https://cityofgainesville.wd5.myworkdayjobs.com/careers"},
    {"name": "Cityofgalveston Workday (cityofgalvestontexascareers)", "url": "https://cityofgalveston.wd12.myworkdayjobs.com/cityofgalvestontexascareers"},
    {"name": "Cityoforlando Workday (cityoforlandocareers)", "url": "https://cityoforlando.wd5.myworkdayjobs.com/cityoforlandocareers"},
    {"name": "Cityofvancouver Workday (cov)", "url": "https://cityofvancouver.wd5.myworkdayjobs.com/cov"},
    {"name": "Cityofventura Workday (cityofventura)", "url": "https://cityofventura.wd5.myworkdayjobs.com/cityofventura"},
    {"name": "Cityyear Workday (cityyear)", "url": "https://cityyear.wd5.myworkdayjobs.com/cityyear"},
    {"name": "Claires Workday (claires)", "url": "https://claires.wd12.myworkdayjobs.com/claires"},
    {"name": "Clarioclinical Workday (clarioclinical_careers)", "url": "https://clarioclinical.wd1.myworkdayjobs.com/clarioclinical_careers"},
    {"name": "Clarionhg Workday (clarion_external_careers)", "url": "https://clarionhg.wd3.myworkdayjobs.com/clarion_external_careers"},
    {"name": "Clarios Workday (clarioscareers)", "url": "https://clarios.wd5.myworkdayjobs.com/clarioscareers"},
    {"name": "Clarivate Workday (clarivate_careers)", "url": "https://clarivate.wd3.myworkdayjobs.com/clarivate_careers"},
    {"name": "Clarivate Workday (jobs)", "url": "https://clarivate.wd3.myworkdayjobs.com/jobs"},
    {"name": "Clark Workday (altura)", "url": "https://clark.wd503.myworkdayjobs.com/altura"},
    {"name": "Clark Workday (clarkexternal)", "url": "https://clark.wd503.myworkdayjobs.com/clarkexternal"},
    {"name": "Clark Workday (shirleyexternal)", "url": "https://clark.wd503.myworkdayjobs.com/shirleyexternal"},
    {"name": "Clarkcountywashington Workday (clarkcountyjobs)", "url": "https://clarkcountywashington.wd1.myworkdayjobs.com/clarkcountyjobs"},
    {"name": "Claylacy Workday (clay_lacy_aviation_careers)", "url": "https://claylacy.wd501.myworkdayjobs.com/clay_lacy_aviation_careers"},
    {"name": "Claytonhomes Workday (21stmortgage)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/21stmortgage"},
    {"name": "Claytonhomes Workday (arbor)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/arbor"},
    {"name": "Claytonhomes Workday (brohn)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/brohn"},
    {"name": "Claytonhomes Workday (chafin)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/chafin"},
    {"name": "Claytonhomes Workday (claytoncareers)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/claytoncareers"},
    {"name": "Claytonhomes Workday (goodall)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/goodall"},
    {"name": "Claytonhomes Workday (highland)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/highland"},
    {"name": "Claytonhomes Workday (homefirst)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/homefirst"},
    {"name": "Claytonhomes Workday (mungo)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/mungo"},
    {"name": "Claytonhomes Workday (oakwood)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/oakwood"},
    {"name": "Claytonhomes Workday (silverton)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/silverton"},
    {"name": "Claytonhomes Workday (vanderbilt)", "url": "https://claytonhomes.wd1.myworkdayjobs.com/vanderbilt"},
    {"name": "Claytonutz Workday (claytonutz1)", "url": "https://claytonutz.wd3.myworkdayjobs.com/claytonutz1"},
    {"name": "Clearchanneloutdoor Workday (cco)", "url": "https://clearchanneloutdoor.wd5.myworkdayjobs.com/cco"},
    {"name": "Clearesult Workday (clearesult_external_careers)", "url": "https://clearesult.wd1.myworkdayjobs.com/clearesult_external_careers"},
    {"name": "Clearskyhealth Workday (csh)", "url": "https://clearskyhealth.wd1.myworkdayjobs.com/csh"},
    {"name": "Clearwateranalytics Workday (clearwater_analytics_careers)", "url": "https://clearwateranalytics.wd1.myworkdayjobs.com/clearwater_analytics_careers"},
    {"name": "Cleco Workday (clecojobs)", "url": "https://cleco.wd5.myworkdayjobs.com/clecojobs"},
    {"name": "Clevelandmetroschools Workday (jobs)", "url": "https://clevelandmetroschools.wd1.myworkdayjobs.com/jobs"},
    {"name": "Clgrupoindustrial Workday (cl_grupo_industrial_sitio_web)", "url": "https://clgrupoindustrial.wd103.myworkdayjobs.com/cl_grupo_industrial_sitio_web"},
    {"name": "Clio Workday (cliocareersite)", "url": "https://clio.wd3.myworkdayjobs.com/cliocareersite"},
    {"name": "Clio Workday (university_boards)", "url": "https://clio.wd3.myworkdayjobs.com/university_boards"},
    {"name": "Cloudera Workday (external_career)", "url": "https://cloudera.wd5.myworkdayjobs.com/external_career"},
    {"name": "Clr Workday (clr_careers)", "url": "https://clr.wd5.myworkdayjobs.com/clr_careers"},
    {"name": "Clr Workday (college_recruiting)", "url": "https://clr.wd5.myworkdayjobs.com/college_recruiting"},
    {"name": "Clunegc Workday (clunegc)", "url": "https://clunegc.wd12.myworkdayjobs.com/clunegc"},
    {"name": "Clydeco Workday (clydecocareers)", "url": "https://clydeco.wd103.myworkdayjobs.com/clydecocareers"},
    {"name": "Cmcmarkets Workday (cmc_markets_careers)", "url": "https://cmcmarkets.wd3.myworkdayjobs.com/cmc_markets_careers"},
    {"name": "Cmegroup Workday (cme_careers)", "url": "https://cmegroup.wd1.myworkdayjobs.com/cme_careers"},
    {"name": "Cmh Workday (cmh)", "url": "https://cmh.wd1.myworkdayjobs.com/cmh"},
    {"name": "Cmno Workday (cms_career_site)", "url": "https://cmno.wd3.myworkdayjobs.com/cms_career_site"},
    {"name": "Cmno Workday (cms_holborn_career_site)", "url": "https://cmno.wd3.myworkdayjobs.com/cms_holborn_career_site"},
    {"name": "Cmu Workday (cmu)", "url": "https://cmu.wd5.myworkdayjobs.com/cmu"},
    {"name": "Cmu Workday (sei)", "url": "https://cmu.wd5.myworkdayjobs.com/sei"},
    {"name": "Cna Workday (cna_careers)", "url": "https://cna.wd1.myworkdayjobs.com/cna_careers"},
    {"name": "Cna Workday (cnahardy)", "url": "https://cna.wd1.myworkdayjobs.com/cnahardy"},
    {"name": "Cncbinternational Workday (cncbiexternalcareersite)", "url": "https://cncbinternational.wd3.myworkdayjobs.com/cncbiexternalcareersite"},
    {"name": "Cngholdingsinc Workday (cng)", "url": "https://cngholdingsinc.wd5.myworkdayjobs.com/cng"},
    {"name": "Cni Workday (cni)", "url": "https://cni.wd503.myworkdayjobs.com/cni"},
    {"name": "Cnoinc Workday (careers)", "url": "https://cnoinc.wd5.myworkdayjobs.com/careers"},
    {"name": "Cnx Workday (external_canada)", "url": "https://cnx.wd1.myworkdayjobs.com/external_canada"},
    {"name": "Cnx Workday (external_global)", "url": "https://cnx.wd1.myworkdayjobs.com/external_global"},
    {"name": "Cnx Workday (external_us)", "url": "https://cnx.wd1.myworkdayjobs.com/external_us"},
    {"name": "Coaction Workday (coaction)", "url": "https://coaction.wd1.myworkdayjobs.com/coaction"},
    {"name": "Coal Workday (warrior_met_coal)", "url": "https://coal.wd1.myworkdayjobs.com/warrior_met_coal"},
    {"name": "Cochlear Workday (cochlear_careers)", "url": "https://cochlear.wd3.myworkdayjobs.com/cochlear_careers"},
    {"name": "Coffeeandbagelbrands Workday (coffeeandbagelbrands)", "url": "https://coffeeandbagelbrands.wd1.myworkdayjobs.com/coffeeandbagelbrands"},
    {"name": "Cogeco Workday (cogeco_careers)", "url": "https://cogeco.wd3.myworkdayjobs.com/cogeco_careers"},
    {"name": "Cognex Workday (external_career_site)", "url": "https://cognex.wd1.myworkdayjobs.com/external_career_site"},
    {"name": "Cognosante Workday (cognosantecareers)", "url": "https://cognosante.wd1.myworkdayjobs.com/cognosantecareers"},
    {"name": "Cognosante Workday (jlodgecareers)", "url": "https://cognosante.wd1.myworkdayjobs.com/jlodgecareers"},
    {"name": "Cohesity Workday (cohesity_careers)", "url": "https://cohesity.wd5.myworkdayjobs.com/cohesity_careers"},
    {"name": "Coinflip Workday (coinflip_external)", "url": "https://coinflip.wd5.myworkdayjobs.com/coinflip_external"},
    {"name": "Coke Workday (coca-cola-careers)", "url": "https://coke.wd1.myworkdayjobs.com/coca-cola-careers"},
    {"name": "Colby Workday (colbycareers)", "url": "https://colby.wd5.myworkdayjobs.com/colbycareers"},
    {"name": "Colby Workday (colbysummerjobs)", "url": "https://colby.wd5.myworkdayjobs.com/colbysummerjobs"},
    {"name": "Colemanwm Workday (coleman_careers)", "url": "https://colemanwm.wd1.myworkdayjobs.com/coleman_careers"},
    {"name": "Collaborative Workday (allopenings)", "url": "https://collaborative.wd1.myworkdayjobs.com/allopenings"},
    {"name": "Collaborative Workday (collaborative_solutions)", "url": "https://collaborative.wd1.myworkdayjobs.com/collaborative_solutions"},
    {"name": "Collaborative Workday (collegegrads)", "url": "https://collaborative.wd1.myworkdayjobs.com/collegegrads"},
    {"name": "Colliers Workday (colliers-external-career-site)", "url": "https://colliers.wd3.myworkdayjobs.com/colliers-external-career-site"},
]

# ================ JOB-TYPE FILTER (systems / OS / embedded / platform) ================

ROLE_ANCHORS = [
    r"\bengineer\b", r"\bengineering\b",
    r"\bdeveloper\b",
    r"\bswe\b", r"\bsde\b",
    r"\bprogrammer\b",
    r"\barchitect\b",
    r"\btech(nical)? lead\b",
]
ROLE_PATTERN = re.compile("|".join(ROLE_ANCHORS), re.IGNORECASE)

DOMAIN_KEYWORDS = [
    # OS / kernel
    # r"\boperating system\b", r"\bos\s*(software|engineer|engineering)",
    # r"\bkernel\b", r"\blinux\b", r"\bunix\b", r"\bbsd\b",
    # r"\bandroid\b(?!\s+app)",
    # r"\bsystem(s)? software\b", r"\bsystems? engineer", r"\bsystems? programming\b",
    # r"\bdriver(s)?\b", r"\bdevice driver",
    # Embedded / firmware / low-level
    # r"\bembedded\b", r"\bfirmware\b", r"\bbootloader\b", r"\bbare[- ]?metal\b",
    # r"\brtos\b", r"\bfreertos\b", r"\bzephyr\b",
    # r"\bmicrocontroller\b", r"\bmcu\b", r"\bsoc\b", r"\bfpga\b",
    # --- Board / BSP / bring-up (NXP i.MX, Renesas R-Car, etc.) ---
    # r"\bbsp\b", r"\bboard support\b", r"\bboard bring[- ]?up\b",
    # r"\bbring[- ]?up\b", r"\bsilicon\b", r"\bsilicon bring[- ]?up\b",
    # r"\bsoc software\b", r"\byocto\b", r"\bbitbake\b", r"\bu-?boot\b",
    # r"\bdevice tree\b", r"\bdevicetree\b", r"\bboard software\b",
    # r"\bplatform bring[- ]?up\b",
    # # --- Boot / security (BL2/FIP secure boot experience) ---
    # r"\bsecure boot\b", r"\bboot\s*(engineer|software|firmware|loader)\b",
    # r"\btrusted firmware\b", r"\btf-?a\b",
    # # Platform / infra
    # r"\bplatform engineer", r"\bplatform software\b", r"\bplatform team\b",
    # r"\bplatform sw\b", r"\binfrastructure engineer",
    # r"\bcompiler\b", r"\btoolchain\b", r"\bllvm\b", r"\bgcc\b",
    # # --- Virtualization / hypervisors ---
    # r"\bvirtualization\b", r"\bhypervisor\b", r"\bcontainer runtime\b",
    # r"\bqemu\b", r"\bkvm\b", r"\bvirtual machine\b", r"\bemulation\b",
    # # Graphics / display / compositor
    # r"\bgraphics\b", r"\bgpu\b", r"\bwayland\b", r"\bweston\b",
    # r"\bcompositor\b", r"\bdisplay\b", r"\bopengl\b", r"\bvulkan\b",
    # r"\brendering\b", r"\bdrm\b", r"\bkms\b", r"\bdrm/kms\b",
    # # Performance / reliability
    # r"\bperformance engineer", r"\bperformance optimization\b",
    # r"\blow[- ]level\b", r"\bsystems? performance\b",
    # r"\bsre\b", r"\bsite reliability\b",
    # # --- Backend (Kafka, Spring Boot, gRPC, distributed systems) ---
    # r"\bbackend engineer", r"\bback[- ]end engineer", r"\bback[- ]end software\b",
    # r"\bdistributed systems\b", r"\bkafka\b", r"\bspring boot\b", r"\bgrpc\b",
    # r"\bdata pipeline\b", r"\bstreaming\b(?!\s+media)",
    # # Languages typical at this layer
    # r"\brust\b", r"\bc\+\+\b", r"(?<![a-z])\bc\b(?![a-z+#])",
    # r"\bgolang\b", r"\bgo developer\b", r"\bgo engineer\b",
    

    # ---------------- AI / LLM ----------------
    r"\bartificial intelligence\b",
    r"\bai engineer\b",
    r"\bmachine learning\b",
    r"\bml engineer\b",
    r"\bapplied ai\b",
    r"\bgenerative ai\b",
    r"\bgenai\b",
    r"\bllm\b",
    r"\blarge language model(s)?\b",
    r"\bfoundation model(s)?\b",
    # RAG — require context so bare "rag" in non-ML titles doesn't match
    r"\bretrieval[- ]augmented generation\b",
    r"\brag\b(?=[\w\s,-]*(?:pipeline|retrieval|generation|llm|search|chunk))",
    r"\bprompt engineering\b",
    r"\bprompt engineer\b",
    # agents — require "ai" prefix or agentic variants to avoid "travel agent", "customer agent"
    r"\bai\s+agent(s)?\b",
    r"\bmulti[- ]agent\b",
    r"\bagentic\b",
    r"\btool calling\b",
    r"\bfunction calling\b",
    r"\bmcp\b",
    r"\bmodel context protocol\b",
    # embeddings — require ML context so "audio embedding" / "mechanical embedding" don't pass
    r"\bvector embedding(s)?\b",
    r"\btext embedding(s)?\b",
    r"\bembedding(s)?\b(?=[\w\s,-]*(?:llm|model|ml|semantic|vector|neural|retrieval|search))",
    r"\bvector database\b",
    r"\bvector search\b",
    r"\bsemantic search\b",
    r"\blangchain\b",
    r"\blanggraph\b",
    r"\bllamaindex\b",
    r"\bvllm\b",
    r"\bollama\b",
    r"\bhugging ?face\b",
    # transformers — require ML context; "electrical transformers" should not match
    r"\btransformer[- ]?(?:model|architecture|based|network|layer)\b",
    r"\bllm[- ]?(?:transformer|inference|fine.?tun)\b",
    r"\binference\b",
    r"\bmodel serving\b",

    # ---------------- Backend ----------------
    r"\bbackend\b",
    r"\bbackend engineer\b",
    r"\bsoftware engineer\b",
    r"\bsoftware developer\b",
    r"\bsde\b",
    r"\bsde[- ]?ii\b",
    r"\bsde[- ]?2\b",
    r"\bjava\b",
    r"\bkotlin\b",
    r"\bpython\b",
    r"\bgo(lang)?\b",
    r"\bc\+\+\b",
    r"\bnode\.?js\b",
    r"\bspring\b",
    r"\bspring boot\b",
    r"\bfastapi\b",
    r"\bflask\b",
    r"\bgrpc\b",
    r"\brest api\b",
    r"\brestful\b",
    r"\bgraphql\b",
    r"\bmicroservices\b",
    r"\bdistributed systems\b",
    r"\bconcurrency\b",
    r"\bmultithreading\b",
    r"\basync\b",
    r"\bmessage queue\b",
    r"\bkafka\b",
    r"\brabbitmq\b",
    r"\bredis\b",
    r"\bpostgres(ql)?\b",
    r"\bmysql\b",
    r"\bmongodb\b",

    # ---------------- Cloud ----------------
    r"\baws\b",
    r"\bazure\b",
    r"\bgcp\b",
    r"\bcloud\b",
    r"\bcloud platform\b",
    r"\bec2\b",
    r"\bs3\b",
    r"\blambda\b",
    r"\becs\b",
    r"\beks\b",
    r"\bcloud run\b",
    r"\bcloud functions\b",
    r"\bapi gateway\b",

    # ---------------- Containers ----------------
    r"\bdocker\b",
    r"\bkubernetes\b",
    r"\bk8s\b",
    # container — avoid matching logistics/shipping context; require DevOps neighbours or plural
    r"\bcontainerization\b",
    r"\bcontainer(?:s|ized|ization)?\b(?=[\w\s,-]*(?:docker|k8s|kubernetes|runtime|orchestrat|pod|image))",
    # helm — require Kubernetes context to avoid "helm safety", "helm navigation"
    r"\bhelm\s+chart\b",
    r"\bhelm\b(?=[\w\s,-]*(?:chart|kubernetes|k8s|deploy|release))",

    # ---------------- Dev Tools ----------------
    r"\bgit\b",
    r"\bgithub\b",
    r"\bgithub actions\b",
    r"\bcicd\b",
    r"\bci/cd\b",
    r"\bjenkins\b",
    r"\bterraform\b",

    # ---------------- AI Infrastructure ----------------
    r"\bmlops\b",
    r"\bmodel deployment\b",
    r"\bserving infrastructure\b",
    r"\bfeature store\b",
    r"\bvector store\b",
    r"\bknowledge graph\b",
    r"\bpinecone\b",
    r"\bmilvus\b",
    r"\bchromadb\b",
    r"\bweaviate\b",

    # ---------------- Search ----------------
    r"\belasticsearch\b",
    r"\bopensearch\b",
    r"\bsolr\b",

    # ---------------- Automation ----------------
    r"\bautomation\b",
    r"\bworkflow\b",
    r"\borchestration\b",
    r"\borchestrator\b",
    r"\bpipeline(s)?\b",

]

DOMAIN_PATTERN = re.compile("|".join(DOMAIN_KEYWORDS), re.IGNORECASE)

EXCLUDE_KEYWORDS = [
    r"\brecruiter\b", r"\bsales\b", r"\bmarketing\b",
    r"\bsupport engineer\b",
    r"\bbusiness development\b",
    # Use precise phrases instead of bare \bhr\b (which could match mid-title abbreviations)
    r"\bhuman resources?\b", r"\bhr manager\b", r"\bhr director\b", r"\bpeople ops\b",
    # Use precise phrases for finance to avoid blocking fintech SWE roles
    r"\bfinance manager\b", r"\bfinancial analyst\b", r"\bfinance director\b",
    r"\baccountant\b", r"\blegal\b",
    r"\bcustomer success\b", r"\baccount manager\b",
    r"\bproduct manager\b", r"\bprogram manager\b", r"\bproject manager\b",
    r"\bdesigner\b", r"\bux\b", r"\bui designer\b",
    r"\bdata analyst\b", r"\bbusiness analyst\b",
    r"\bfrontend\b", r"\bfront[- ]end\b", r"\bweb developer\b",
]
EXCLUDE_PATTERN = re.compile("|".join(EXCLUDE_KEYWORDS), re.IGNORECASE)

# ================ STRICT LOCATION FILTER (INDIA & PURE GLOBAL REMOTE ONLY) ================

INDIA_LOCATION_INDICATORS = [
    # Nation / Country
    r"\bindia\b", r"\bindian\b", r"\bbharat\b",
    # Cities & Tech Hubs in India
    r"\bbengaluru\b", r"\bbangalore\b", r"\bblr\b",
    r"\bhyderabad\b", r"\bhyd\b",
    r"\bpune\b",
    r"\bchennai\b", r"\bmadras\b",
    r"\bnoida\b", r"\bgurgaon\b", r"\bgurugram\b", r"\bggn\b", r"\bdelhi\b", r"\bnew delhi\b", r"\bncr\b",
    r"\bmumbai\b", r"\bnavi mumbai\b", r"\bthane\b", r"\bbombay\b",
    r"\bkolkata\b", r"\bcalcutta\b",
    r"\bahmedabad\b",
    r"\bkochi\b", r"\bcochin\b", r"\btrivandrum\b", r"\bthiruvananthapuram\b",
    r"\bjaipur\b", r"\bindore\b", r"\bchandigarh\b", r"\blucknow\b",
    r"\bcoimbatore\b", r"\bvadodara\b", r"\bsurat\b", r"\bbhubaneswar\b",
    r"\bvisakhapatnam\b", r"\bvizag\b", r"\bnagpur\b", r"\bghaziabad\b", r"\bfaridabad\b",
    r"\bmysore\b", r"\bmysuru\b",
    # States in India
    r"\bkarnataka\b", r"\btelangana\b", r"\bmaharashtra\b", r"\btamil nadu\b",
    r"\bharyana\b", r"\buttar pradesh\b", r"\bkerala\b", r"\bgujarat\b",
    r"\bwest bengal\b", r"\brajasthan\b", r"\bpunjab\b",
]

REMOTE_INDICATORS = [
    r"\bremote\b",
    r"\bwork from home\b",
    r"\bwfh\b",
    r"\banywhere\b",
    r"\bdistributed\b",
    r"\bglobal\b",
    r"\bworldwide\b",
]

NON_INDIA_LOCATION_INDICATORS = [
    # US — bare abbreviations and dotted forms (U.S., US, U.S.A.)
    # Note: \bu\.s\.? catches "U.S." and "U.S" with or without trailing dot.
    r"\bunited states\b", r"\busa\b",
    r"\bu\.s\.a?\.?(?:\b|$)",   # U.S.A., U.S., U.S  (\b fails after dot — added $)
    r"\bu\.s\.?(?:\b|(?=[^a-z]))",  # standalone U.S. / U.S with any non-alpha after
    # Remote + US combos — all the variants that slipped through
    r"\bremote\s*[-/,]?\s*u\.?s\.?",  # Remote U.S., Remote US, Remote/US, Remote-US
    r"\bu\.?s\.?\s*[-/,]?\s*remote\b",  # US Remote, US/Remote
    r"\bremote\s*\(?\s*u\.?s\.?\)?",   # Remote (US), Remote (U.S.)
    r"\banywhere\s+in\s+the\s+u\.?s\.?",  # Anywhere in the US
    r"\bhybrid\s*[-–]?\s*u\.?s\.?",       # Hybrid - US, Hybrid US
    r"\bus remote\b", r"\bremote us\b", r"\bremote - us\b", r"\bremote, us\b",
    r"\b- us\b", r"\b, us\b", r"\b\(us\)\b",
    r"\bcalifornia\b", r"\btexas\b", r"\bwashington\b", r"\bnew york\b", r"\bmassachusetts\b", r"\bcolorado\b", r"\billinois\b", r"\bgeorgia\b", r"\bflorida\b", r"\bvirginia\b", r"\bnorth carolina\b", r"\bnew jersey\b", r"\bohio\b", r"\bmichigan\b", r"\bpennsylvania\b", r"\boregon\b", r"\barizona\b", r"\butah\b", r"\bmaryland\b", r"\bminnesota\b", r"\bwisconsin\b", r"\bconnecticut\b", r"\bnevada\b", r"\btennessee\b", r"\bmissouri\b", r"\bindiana\b",
    r"\bsan francisco\b", r"\bseattle\b", r"\baustin\b", r"\bboston\b", r"\bdenver\b", r"\bsanta clara\b", r"\bsan jose\b", r"\bsunnyvale\b", r"\bpalo alto\b", r"\bchicago\b", r"\batlanta\b", r"\bnew york city\b", r"\bnyc\b", r"\blos angeles\b", r"\bsan diego\b", r"\bportland\b", r"\bphoenix\b", r"\bsalt lake city\b", r"\braleigh\b", r"\bdurham\b", r"\bpittsburgh\b", r"\bredmond\b", r"\bbellevue\b", r"\bcupertino\b", r"\bmountain view\b", r"\bmenlo park\b", r"\bsan mateo\b", r"\bfremont\b", r"\boakland\b", r"\birvine\b", r"\bboulder\b", r"\breston\b", r"\bmclean\b", r"\barlington\b", r"\bdallas\b", r"\bhouston\b", r"\bplano\b", r"\bcharlotte\b", r"\bnashville\b", r"\btampa\b", r"\bmiami\b", r"\borlando\b", r"\bphiladelphia\b", r"\bbaltimore\b", r"\bminneapolis\b", r"\bdetroit\b", r"\bindianapolis\b", r"\bcolumbus\b", r"\bst\.? louis\b",
    r"\b[-,/|()]\s*(ca|tx|wa|ny|ma|co|il|ga|fl|va|nc|nj|oh|mi|pa|or|az|ut|md|nv|ct|tn|mo|mn|wi)\b",
    r"\b(ca|tx|wa|ny|ma|co|il|ga|fl|va|nc|nj|oh|mi|pa|or|az|ut|md|nv|ct|tn|mo|mn|wi)\s*[-,/|()]\b",
    r"\bnoram\b", r"\bnorth america\b", r"\bus & canada\b", r"\bus/canada\b",

    # Canada & Americas (non-India)
    r"\bcanada\b", r"\btoronto\b", r"\bvancouver\b", r"\bmontreal\b", r"\bottawa\b", r"\bcalgary\b", r"\bontario\b", r"\bquebec\b", r"\balberta\b", r"\bbritish columbia\b",
    r"\bmexico\b", r"\bmexico city\b", r"\bguadalajara\b", r"\bmonterrey\b", r"\bbrazil\b", r"\bsao paulo\b", r"\brio de janeiro\b", r"\blatam\b", r"\blatin america\b", r"\bamericas?\b", r"\bamer\b", r"\bargentina\b", r"\bbuenos aires\b", r"\bchile\b", r"\bsantiago\b", r"\bcolombia\b", r"\bbogota\b", r"\bperu\b", r"\blima\b", r"\bcosta rica\b",

    # UK & Ireland
    r"\bunited kingdom\b", r"\buk\b", r"\bu\.k\.\b", r"\bengland\b", r"\bscotland\b", r"\bwales\b", r"\blondon\b", r"\bmanchester\b", r"\bcambridge\b", r"\boxford\b", r"\bedinburgh\b", r"\bglasgow\b", r"\bbirmingham\b", r"\bleeds\b", r"\bbristol\b", r"\breading\b", r"\bbelfast\b",
    r"\bireland\b", r"\bdublin\b", r"\bcork\b",

    # Europe & EMEA & Nordics & DACH
    r"\bemea\b", r"\beurope\b", r"\beu\b", r"\beuropean union\b", r"\bnordics\b", r"\bbenelux\b", r"\bdach\b", r"\bbaltics\b",
    r"\bgermany\b", r"\bberlin\b", r"\bmunich\b", r"\bmünchen\b", r"\bfrankfurt\b", r"\bhamburg\b", r"\bstuttgart\b", r"\bdüsseldorf\b", r"\bcologne\b", r"\bköln\b",
    r"\bfrance\b", r"\bparis\b", r"\blyon\b", r"\bmarseille\b", r"\btoulouse\b",
    r"\bnetherlands\b", r"\bamsterdam\b", r"\brotterdam\b", r"\butrecht\b", r"\beindhoven\b",
    r"\bswitzerland\b", r"\bzurich\b", r"\bzürich\b", r"\bgeneva\b", r"\bbasel\b", r"\blausanne\b",
    r"\bspain\b", r"\bmadrid\b", r"\bbarcelona\b", r"\bvalencia\b", r"\bmalaga\b",
    r"\bitaly\b", r"\brome\b", r"\bmilan\b", r"\bmilano\b", r"\bturin\b",
    r"\bpoland\b", r"\bwarsaw\b", r"\bkrakow\b", r"\bwroclaw\b", r"\bpoznan\b", r"\bgdansk\b",
    r"\bportugal\b", r"\blisbon\b", r"\bporto\b",
    r"\bromania\b", r"\bbucharest\b", r"\bcluj\b", r"\btimisoara\b",
    r"\bczech\b", r"\bprague\b", r"\bbrno\b",
    r"\bsweden\b", r"\bstockholm\b", r"\bgothenburg\b",
    r"\bnorway\b", r"\boslo\b",
    r"\bfinland\b", r"\bhelsinki\b", r"\bespoo\b",
    r"\bdenmark\b", r"\bcopenhagen\b",
    r"\baustria\b", r"\bvienna\b", r"\bwien\b",
    r"\bbelgium\b", r"\bbrussels\b", r"\bantwerp\b",
    r"\bestonia\b", r"\btallinn\b",
    r"\bhungary\b", r"\bbudapest\b",
    r"\bgreece\b", r"\bathens\b",
    r"\bukraine\b", r"\bkyiv\b", r"\bkiev\b",
    r"\bserbia\b", r"\bbelgrade\b",
    r"\bcroatia\b", r"\bzagreb\b",
    r"\bslovakia\b", r"\bbratislava\b",
    r"\bbulgaria\b", r"\bsofia\b",
    r"\blithuania\b", r"\bvilnius\b",
    r"\blatvia\b", r"\briga\b",
    r"\bcyprus\b", r"\blimassol\b",
    r"\bluxembourg\b",
    r"\bturkey\b", r"\btürkiye\b", r"\bistanbul\b",

    # APAC & Asia (non-India) & Oceania
    r"\bapac\b", r"\basia\b",
    r"\bjapan\b", r"\btokyo\b", r"\bosaka\b", r"\byokohama\b", r"\bnagoya\b",
    r"\bchina\b", r"\bbeijing\b", r"\bshanghai\b", r"\bshenzhen\b", r"\bhangzhou\b", r"\bhong kong\b",
    r"\btaiwan\b", r"\btaipei\b", r"\bhsinchu\b",
    r"\bsouth korea\b", r"\bkorea\b", r"\bseoul\b",
    r"\bsingapore\b",
    r"\baustralia\b", r"\bsydney\b", r"\bmelbourne\b", r"\bbrisbane\b", r"\bperth\b", r"\badelaide\b",
    r"\bnew zealand\b", r"\bauckland\b", r"\bwellington\b",
    r"\bvietnam\b", r"\bho chi minh\b", r"\bhanoi\b",
    r"\bthailand\b", r"\bbangkok\b",
    r"\bphilippines\b", r"\bmanila\b",
    r"\bindonesia\b", r"\bjakarta\b",
    r"\bmalaysia\b", r"\bkuala lumpur\b",

    # Middle East & Africa
    r"\buae\b", r"\bunited arab emirates\b", r"\bdubai\b", r"\babu dhabi\b",
    r"\bqatar\b", r"\bdoha\b",
    r"\bsaudi arabia\b", r"\bsaudi\b", r"\briyadh\b", r"\bjeddah\b",
    r"\bisrael\b", r"\btel aviv\b", r"\bhaifa\b",
    r"\bsouth africa\b", r"\bcape town\b", r"\bjohannesburg\b",
    r"\begypt\b", r"\bcairo\b",
    r"\bkenya\b", r"\bnairobi\b",
    r"\bnigeria\b", r"\blagos\b",
    r"\bafrica\b",
]

INDIA_LOCATION_PATTERN = re.compile("|".join(INDIA_LOCATION_INDICATORS), re.IGNORECASE)
REMOTE_INDICATOR_PATTERN = re.compile("|".join(REMOTE_INDICATORS), re.IGNORECASE)
NON_INDIA_LOCATION_PATTERN = re.compile("|".join(NON_INDIA_LOCATION_INDICATORS), re.IGNORECASE)

# Backward-compatibility pattern aliases
NON_US_PATTERN = INDIA_LOCATION_PATTERN
US_PATTERN = NON_INDIA_LOCATION_PATTERN
INDIA_PATTERN = INDIA_LOCATION_PATTERN
NON_INDIA_PATTERN = NON_INDIA_LOCATION_PATTERN



HIGH_SENIORITY_PATTERN = re.compile(
    r"\bsenior\b|\bsr\.?\b|\bsnr\b|\bstaff\b|\bprincipal\b|\bdistinguished\b|\bfellow\b|\blead\b(?!\s+generation)|\bdirector\b|\bvp\b|\bhead of\b|\bmanager\b",
    re.IGNORECASE,
)


def is_target_job(title):
    if not title:
        return False
    # Drop overqualified roles (>3 YOE: Senior, Staff, Principal, Lead, Director)
    if EXCLUDE_HIGH_SENIORITY and HIGH_SENIORITY_PATTERN.search(title):
        return False
    if EXCLUDE_PATTERN.search(title):
        return False
    if not ROLE_PATTERN.search(title):
        return False
    # Entry-level pass-through: generic new-grad/junior/intern SWE titles
    # are kept even without a domain keyword in the title, because the
    # systems detail is usually in the description, not the title itself.
    if ENTRY_PATTERN.search(title):
        return True
    # General software-engineering pass-through: backend/microservices/SWE
    # titles also qualify, not only the explicitly low-level ones.
    if GENERAL_SWE_PATTERN.search(title):
        return True
    if not DOMAIN_PATTERN.search(title):
        return False
    return True


# Entry-level signals — if a title is clearly junior AND has a role anchor,
# let it through regardless of domain keywords.
ENTRY_PATTERN = re.compile(
    r"\bnew[- ]grad(uate)?\b|\bentry[- ]level\b|\bjunior\b|\bjr\.?\b|"
    r"\bassociate (software |systems )?engineer\b|\bgraduate (software )?engineer\b|"
    r"\bintern(ship)?\b|\bco[- ]?op\b|\bengineer\s+(i|1)\b|\bearly career\b|"
    r"\bsoftware engineer\s*[-,]?\s*(new grad|university|campus|2025|2026)\b|"
    r"\buniversity grad",
    re.IGNORECASE,
)

# General software-engineering titles that qualify on their own
# (backend, microservices, fullstack, staff, devops, sre, security, data, research)
# even when no low-level keyword appears in the title.
GENERAL_SWE_PATTERN = re.compile(
    r"\bsoftware (engineer|developer)\b|\bsoftware development engineer\b|"
    r"\bswe\b|\bsde\b|\bfull[- ]?stack\b|\bback[- ]?end\b|"
    r"\bservices? engineer\b|\bapplication(s)? (engineer|developer)\b|"
    r"\bapi (engineer|developer)\b|\bmicroservices?\b|\bjava (engineer|developer)\b|"
    r"\b\.net\s+(engineer|developer)\b|c#\s+(engineer|developer)\b|"
    r"\bpython (engineer|developer)\b|"
    r"\bspring boot\b|\bkafka\b|\bgrpc\b|\bdistributed systems\b|"
    # ---- Common engineering families not covered by domain keywords ----
    r"\bstaff (software\s+)?engineer\b|\bprincipal (software\s+)?engineer\b|"
    r"\btechnical lead\b|\btech lead\b|"
    r"\bplatform engineer\b|"
    r"\bdevops\s+engineer\b|\bdev[- ]ops\s+engineer\b|"
    r"\bsite reliability\b|\bsre\b|"
    r"\bsecurity engineer\b|\bcybersecurity engineer\b|\bapplication security\b|"
    r"\bdata engineer\b|\bdata pipeline engineer\b|"
    r"\bai (research\s+)?engineer\b|\bml (research\s+)?engineer\b|"
    r"\bresearch engineer\b|"
    r"\bsolutions architect\b|\bcloud architect\b|\bsoftware architect\b",
    re.IGNORECASE,
)


US_LOCATION_PATTERN = NON_INDIA_LOCATION_PATTERN


def is_target_location(location_text, title=""):
    """Strict location filter.
    Returns True ONLY if:
    1. If the TITLE explicitly mentions a non-India location (e.g. London, San Francisco, Tokyo, UK, US, Canada, EMEA, etc.)
       AND does NOT mention India, it is REJECTED immediately.
    2. The location (or title) explicitly mentions India or an Indian city/state (e.g. Bangalore, Hyderabad, Pune, Gurgaon, etc.).
    3. OR the location (or title) explicitly mentions Remote (or WFH / Global / Worldwide / Anywhere) AND
       DOES NOT contain any non-India location/country/region indicators.
    """
    title_clean = (title or "").strip()
    loc_clean = (location_text or "").strip()
    combined = f"{title_clean} {loc_clean}".strip()

    if not combined:
        return False

    title_has_non_india = bool(NON_INDIA_LOCATION_PATTERN.search(title_clean))
    title_has_india = bool(INDIA_LOCATION_PATTERN.search(title_clean))

    # Reject if job title explicitly specifies a foreign city/country and does NOT include India
    if title_has_non_india and not title_has_india:
        return False

    has_india = bool(INDIA_LOCATION_PATTERN.search(combined))
    has_non_india = bool(NON_INDIA_LOCATION_PATTERN.search(combined))
    has_remote = bool(REMOTE_INDICATOR_PATTERN.search(combined))

    if has_india:
        return True

    if has_remote and not has_non_india:
        return True

    return False



def is_us_location(location_text, title=""):
    """Alias for backwards compatibility with main loop calls."""
    return is_target_location(location_text, title)



# ================ SENIORITY / EXPERIENCE LEVEL FROM JOB TITLE ================
# Returns a label like "Mid (~2-5y) [3 YOE Target]", "Senior (~5-8y)", etc.
# Configured for candidate profile with ~3 years of experience.

_SENIORITY_PATTERNS = [
    # Internships / new grad first (most specific)
    ("Intern", r"\bintern(ship)?\b|\bco[- ]?op\b"),
    ("Entry / New Grad", r"\bnew[- ]grad(uate)?\b|\bentry[- ]level\b|\bjunior\b|\bjr\.?\b|\bassociate\b|\bgraduate engineer\b"),
    # Senior-most labels (specific terms beat generic "engineer")
    ("Fellow", r"\bfellow\b"),
    ("Distinguished (~15+y)", r"\bdistinguished\b"),
    ("Principal (~12+y)", r"\bprincipal\b"),
    ("Senior Staff (~10-15y)", r"\bsr\.?\s*staff\b|\bsenior\s+staff\b"),
    ("Staff (~8-12y)", r"\bstaff\b"),
    ("Senior (~5-8y)", r"\bsenior\b|\bsr\.?\b|\bsnr\b"),
    ("Lead (~7-10y)", r"\blead\b(?!\s+generation)"),
    # Levels like "Engineer II" or "Engineer 3" — prime fit for 3 YOE
    ("Mid (~2-5y) [3 YOE Target]", r"\bengineer\s+(ii|2|iii|3)\b|\blevel\s*(2|3)\b|\bl[345]\b"),
    ("Entry (~0-2y)", r"\bengineer\s+(i|1)\b|\blevel\s*1\b|\bl[12]\b"),
]
_SENIORITY_COMPILED = [(label, re.compile(pat, re.IGNORECASE)) for label, pat in _SENIORITY_PATTERNS]


def detect_seniority(title):
    """Return a seniority label from a job title, or '' if unclear."""
    if not title:
        return ""
    # First match wins; ordering is intentional (specific before generic).
    for label, pattern in _SENIORITY_COMPILED:
        if pattern.search(title):
            return label
    # Bare "Software Engineer" / "Developer" with no other qualifier → prime mid-level fit (~3 YOE)
    if re.search(r"\b(software\s+)?(engineer|developer|programmer|sre|devops)\b", title, re.IGNORECASE):
        return "Mid (~2-5y) [3 YOE Target]"
    return ""


# ================ JOB-LINK DETECTION HEURISTICS ================

JOB_URL_PATTERNS = [
    r"/jobs?/", r"/careers?/", r"/positions?/", r"/openings?/",
    r"/roles?/", r"/vacancy/", r"/vacancies/", r"/apply/",
    r"/job-detail", r"/job/",
    r"greenhouse\.io/.+/jobs/\d+",
    r"lever\.co/.+/[a-f0-9-]{20,}",
    r"ashbyhq\.com/.+/.+",
    r"workday\.com/.+/job/", r"myworkdayjobs\.com/.+/job/",
    r"smartrecruiters\.com/.+/\d+",
    r"icims\.com/jobs/\d+",
    r"dejobs\.org/.+/job/",
]

NON_JOB_TEXT = {
    "careers", "jobs", "open positions", "all jobs", "view all",
    "see all", "apply", "login", "sign in", "search", "filter",
    "home", "about", "contact", "privacy", "terms", "back",
    "next", "previous", "more", "load more",
}


def canonical_job_id(url):
    """Return a stable canonical ID for a job URL, used as the primary key in
    the `seen` and `jobs_detail` tables.

    Goals:
      - Same physical job posting always maps to the same ID across cycles.
      - Strips volatile tracking/session query params (e.g. ?gh_jid=, ?tracking_id=)
        that some portals append inconsistently between scraping runs.
      - Preserves the numeric job ID embedded in the URL path or a stable param.

    Strategy by portal:
      Greenhouse  boards.greenhouse.io/co/jobs/12345?gh_jid=12345  -> id 12345
      Greenhouse  job-boards.greenhouse.io/co/jobs/12345           -> id 12345
      Ashby       jobs.ashbyhq.com/co/<uuid>                        -> uuid
      Lever       jobs.lever.co/co/<uuid>                           -> uuid
      Workday     host/en-US/site/job/Loc/Title_JRXXXXX             -> JRXXXXX slug
      Generic     strip ?tracking=... ?source=... but keep gh_jid if path has no ID
    """
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    import re

    url = url.strip()
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    host = parsed.netloc.lower()

    # ---- Greenhouse (boards.greenhouse.io or job-boards.greenhouse.io) ----
    # URL: .../jobs/12345  or  .../jobs/12345?gh_jid=12345
    # The numeric ID in the path is stable; strip all query params.
    if "greenhouse.io" in host:
        m = re.search(r"/jobs?/([0-9]+)", path)
        if m:
            return f"gh:{m.group(1)}"
        # boards.greenhouse.io sometimes: /co/jobs?gh_jid=12345
        qs = parse_qs(parsed.query)
        if "gh_jid" in qs:
            return f"gh:{qs['gh_jid'][0]}"

    # ---- Ashby: jobs.ashbyhq.com/co/<uuid> ----
    if "ashbyhq.com" in host:
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            # last segment is the UUID / slug
            return f"ashby:{parts[-1]}"

    # ---- Lever: jobs.lever.co/co/<uuid> ----
    if "lever.co" in host:
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return f"lever:{parts[-1]}"

    # ---- Workday: host/en-US/Site/job/Location/Title_JRXXXXX ----
    if "myworkdayjobs.com" in host or "workday.com" in host:
        # externalPath format: /job/Location/Title_JR12345
        m = re.search(r"/job/[^/]+/([^/?#]+)", path)
        if m:
            return f"wd:{m.group(1)}"

    # ---- Sites that embed gh_jid as a query param in their own domain ----
    # e.g. careers.withwaymo.com/jobs?gh_jid=123, careers.datadoghq.com/detail/123/?gh_jid=123
    qs = parse_qs(parsed.query)
    if "gh_jid" in qs:
        return f"gh:{qs['gh_jid'][0]}"

    # ---- Fallback: strip known volatile tracking params, keep path + stable params ----
    VOLATILE_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
        "source", "tracking_id", "ref", "referrer", "redirect",
        # NOT stripping gh_jid here — already handled above
    }
    filtered = {k: v for k, v in qs.items() if k.lower() not in VOLATILE_PARAMS}
    clean_query = urlencode(filtered, doseq=True)
    return urlunparse((parsed.scheme, host, path, "", clean_query, ""))


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (job_id TEXT PRIMARY KEY)")

    # ---- Dashboard tables (non-breaking: IF NOT EXISTS) ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         REAL    NOT NULL,
            event_type TEXT    NOT NULL,
            company    TEXT    DEFAULT '',
            message    TEXT    DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs_detail (
            job_id     TEXT PRIMARY KEY,
            url        TEXT    DEFAULT '',
            title      TEXT    DEFAULT '',
            company    TEXT    DEFAULT '',
            location   TEXT    DEFAULT '',
            seniority  TEXT    DEFAULT '',
            first_seen REAL    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_descriptions (
            job_id         TEXT PRIMARY KEY,
            jd_text        TEXT    DEFAULT '',
            fetched_at     REAL    NOT NULL,
            passes_filter  INTEGER DEFAULT 1,
            filter_reason  TEXT    DEFAULT '',
            match_score    REAL    DEFAULT 0.0,
            match_analysis TEXT    DEFAULT '',
            evaluated_at   REAL    DEFAULT 0.0
        )
    """)
    # ---- Non-breaking migration: add `url` column if upgrading from old schema ----
    try:
        conn.execute("ALTER TABLE jobs_detail ADD COLUMN url TEXT DEFAULT ''")
    except Exception:
        pass  # Column already exists

    # ---- Migrate old http:// seen-table keys to canonical form ----
    # On first run after this upgrade, convert any existing URL-format keys so
    # already-seen jobs are never re-alerted under the new canonical scheme.
    old_keys = conn.execute(
        "SELECT job_id FROM seen WHERE job_id LIKE 'http%'"
    ).fetchall()
    if old_keys:
        cur = conn.cursor()
        migrated = 0
        for (old_url,) in old_keys:
            cjid = canonical_job_id(old_url)
            if cjid != old_url:
                try:
                    cur.execute("INSERT OR IGNORE INTO seen (job_id) VALUES (?)", (cjid,))
                    cur.execute("DELETE FROM seen WHERE job_id = ?", (old_url,))
                    migrated += 1
                except Exception:
                    pass
        if migrated:
            print(f"  [init_db] Migrated {migrated} seen-table key(s) to canonical format.")

    conn.commit()
    return conn


def log_event(conn, event_type, company="", message=""):
    """Write a timestamped event for the live dashboard. Never raises — dashboard
    logging must not crash the watcher."""
    try:
        conn.execute(
            "INSERT INTO events (ts, event_type, company, message) VALUES (?, ?, ?, ?)",
            (datetime.now().timestamp(), event_type, company, str(message)[:500]),
        )
        # Cap at 3 000 rows so the DB never grows unbounded.
        conn.execute(
            "DELETE FROM events WHERE id NOT IN "
            "(SELECT id FROM events ORDER BY id DESC LIMIT 3000)"
        )
    except Exception:
        pass


def looks_like_job_link(href, text, base_url):
    if not href or not text:
        return False
    text_clean = text.strip()
    if len(text_clean) < 3 or len(text_clean) > 200:
        return False
    if text_clean.lower() in NON_JOB_TEXT:
        return False
    full_url = urljoin(base_url, href)
    if not any(re.search(p, full_url, re.IGNORECASE) for p in JOB_URL_PATTERNS):
        return False
    if not re.match(r"^[A-Za-z0-9]", text_clean):
        return False
    return True


def get_nearby_location(a_tag):
    """Look for location text in the link's own immediate context.

    Strategy:
      1. Check next siblings of the <a> (location is often the next line/element).
      2. Check the immediate parent's own text (excluding nested links/headings).
      3. Check the immediate parent's next sibling.
    Stops at the first short text fragment containing a US or non-US indicator.
    Deliberately does NOT climb high in the tree — that grabs neighbors' data.
    """
    def looks_like_location(text):
        if not text or len(text) > 200:
            return False
        return bool(INDIA_LOCATION_PATTERN.search(text) or NON_INDIA_LOCATION_PATTERN.search(text) or REMOTE_INDICATOR_PATTERN.search(text))


    # 1. Next siblings of the link
    for sib in a_tag.next_siblings:
        if hasattr(sib, "get_text"):
            t = sib.get_text(" ", strip=True)
        else:
            t = str(sib).strip()
        if t and looks_like_location(t):
            return t

    # 2. Parent's own text minus the link's text
    parent = a_tag.parent
    if parent is not None:
        full = parent.get_text(" ", strip=True)
        link_text = a_tag.get_text(" ", strip=True)
        remainder = full.replace(link_text, "", 1).strip()
        if looks_like_location(remainder):
            return remainder

        # 3. Parent's next sibling (e.g. <li><a>title</a></li><li>location</li>)
        for sib in parent.next_siblings:
            if hasattr(sib, "get_text"):
                t = sib.get_text(" ", strip=True)
            else:
                t = str(sib).strip()
            if t and looks_like_location(t):
                return t

    return ""


def extract_jobs(html, base_url, selector=None):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    seen_urls = set()

    if selector:
        elements = soup.select(selector)
        for el in elements:
            a = el if el.name == "a" else el.find("a")
            if not a or not a.get("href"):
                continue
            href = a["href"]
            title = el.get_text(strip=True) if el.name != "a" else a.get_text(strip=True)
            full_url = urljoin(base_url, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            jobs.append({"title": title, "url": full_url, "location": get_nearby_location(a)})
    else:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if not looks_like_job_link(href, text, base_url):
                continue
            full_url = urljoin(base_url, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            jobs.append({"title": text, "url": full_url, "location": get_nearby_location(a)})

    return jobs


async def fetch_page(browser, url):
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    )
    await _apply_stealth(context)
    page = await context.new_page()
    try:
        await page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        # Give the page a moment to render JS — networkidle never fires on heavy SPAs.
        await page.wait_for_timeout(4000)
        return await page.content()
    finally:
        await context.close()


# ================ ATS-SPECIFIC FETCHERS ================
# These hit the JSON APIs that career-page widgets call internally. Much
# faster and more reliable than scraping rendered HTML.

WORKDAY_RE = re.compile(r"https?://([^.]+)\.[^/]*myworkdayjobs\.com/(?:[a-z-]+/)?([^/?#]+)", re.I)


def detect_ats(url):
    """Return 'workday' | 'greenhouse' | 'lever' | 'ashby' | 'oracle' | None"""
    u = url.lower()
    if "myworkdayjobs.com" in u or "workday.com" in u:
        return "workday"
    if "greenhouse.io" in u or "boards.greenhouse.io" in u:
        return "greenhouse"
    if "lever.co" in u:
        return "lever"
    if "ashbyhq.com" in u:
        return "ashby"
    if "oraclecloud.com" in u and "candidateexperience" in u:
        return "oracle"
    return None


def fetch_workday_api(url, max_retries=3):
    """Workday's job board renders client-side from POSTing to /wday/cxs/{tenant}/{site}/jobs.
    Returns list of {title, url, location} dicts, or raises.

    Improvements over the original:
      - Full browser-like headers (Origin, Referer, Sec-Fetch-*) to pass stricter tenants.
      - Retry with exponential backoff on ConnectionError / Timeout (transient resets).
      - Raises ValueError with a clear message if the site returns 0 jobs (wrong slug).
    """
    m = WORKDAY_RE.search(url)
    if not m:
        raise ValueError("Couldn't parse Workday tenant/site from URL")
    tenant, site = m.group(1), m.group(2)

    # The host pattern is e.g. nvidia.wd5.myworkdayjobs.com — need full host.
    host = urlparse(url).netloc
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"

    # Detect the locale prefix from the original URL if present (e.g. /en-US/),
    # otherwise default to /en-US/. Workday public job URLs require this segment.
    locale_match = re.search(r"https?://[^/]+/([a-z]{2}-[A-Z]{2})/", url)
    locale = locale_match.group(1) if locale_match else "en-US"

    # Full browser-like headers — required by stricter Workday tenants that
    # check Origin/Referer and Sec-Fetch-* to distinguish XHR from raw scripts.
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Origin": f"https://{host}",
        "Referer": url,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    def _post_with_retry(payload):
        """POST with exponential-backoff retry on transient connection failures."""
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                r = requests.post(api, json=payload, headers=headers, timeout=25)
                r.raise_for_status()
                return r.json()
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                last_err = e
                if attempt < max_retries:
                    time.sleep(2 ** attempt)  # 2s, 4s, 8s
            except requests.exceptions.HTTPError as e:
                raise  # 4xx/5xx — don't retry, surface immediately
        raise last_err  # all retries exhausted

    all_jobs = []
    offset = 0
    total = None
    while True:
        payload = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""}
        data = _post_with_retry(payload)
        if total is None:
            total = data.get("total", 0)
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for p in postings:
            ext = p.get("externalPath", "")
            if ext:
                # Workday public URL format: https://{host}/{locale}/{site}{externalPath}
                # externalPath is like "/job/Santa-Clara/Senior-Engineer_JR12345"
                job_url = f"https://{host}/{locale}/{site}{ext}"
            else:
                job_url = url
            all_jobs.append({
                "title": p.get("title", "").strip(),
                "url": job_url,
                "location": p.get("locationsText", "").strip(),
            })
        offset += 20
        if offset >= total or offset > 2000:  # safety cap
            break
    return all_jobs


def fetch_greenhouse_api(url):
    """Pull jobs from boards-api.greenhouse.io. Handles both boards.greenhouse.io
    and embedded greenhouse iframes."""
    # Try to find the company slug
    m = re.search(r"greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", url, re.I)
    if not m:
        raise ValueError("Couldn't parse Greenhouse company slug")
    slug = m.group(1)
    api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    r = requests.get(api, timeout=20)
    r.raise_for_status()
    return [
        {
            "title": j.get("title", "").strip(),
            "url": j.get("absolute_url", ""),
            "location": (j.get("location") or {}).get("name", ""),
        }
        for j in r.json().get("jobs", [])
    ]


def fetch_lever_api(url):
    m = re.search(r"lever\.co/([a-z0-9_-]+)", url, re.I)
    if not m:
        raise ValueError("Couldn't parse Lever company slug")
    slug = m.group(1)
    api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(api, timeout=20)
    r.raise_for_status()
    return [
        {
            "title": j.get("text", "").strip(),
            "url": j.get("hostedUrl", ""),
            "location": (j.get("categories") or {}).get("location", ""),
        }
        for j in r.json()
    ]


def fetch_ashby_api(url):
    m = re.search(r"ashbyhq\.com/([a-z0-9_-]+)", url, re.I)
    if not m:
        raise ValueError("Couldn't parse Ashby company slug")
    slug = m.group(1)
    api = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    r = requests.get(api, timeout=20)
    r.raise_for_status()
    return [
        {
            "title": j.get("title", "").strip(),
            "url": j.get("jobUrl", ""),
            "location": j.get("location", ""),
        }
        for j in r.json().get("jobs", [])
    ]


def fetch_oracle_api(url):
    """Oracle Cloud HCM exposes REST API at /hcmRestApi/resources/latest/recruitingCEJobRequisitions.
    Used by Oracle itself, Texas Instruments, and many other large companies."""
    # Extract the tenant host (e.g. edbz.fa.us2.oraclecloud.com)
    parsed = urlparse(url)
    host = parsed.netloc
    if not host:
        raise ValueError("Couldn't parse Oracle host")

    headers = {
        "Accept": "application/json",
        "REST-Framework-Version": "7",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    all_jobs = []
    offset = 0
    limit = 200
    while True:
        api = (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList.secondaryLocations,flexFieldsFacet.values"
            f"&finder=findReqs;siteNumber=CX_1,facetsList=LOCATIONS%3BWORK_LOCATIONS%3BTITLES,"
            f"limit={limit},offset={offset}"
        )
        r = requests.get(api, headers=headers, timeout=25)
        r.raise_for_status()
        data = r.json()
        items = (data.get("items") or [{}])[0].get("requisitionList", [])
        if not items:
            break
        for j in items:
            job_id = j.get("Id", "")
            locs = j.get("PrimaryLocation", "") or ""
            if j.get("secondaryLocations"):
                extras = [s.get("Name", "") for s in j["secondaryLocations"]]
                if extras:
                    locs = locs + " | " + ", ".join(extras)
            # Build the public job URL — pattern varies, but this works for most tenants:
            job_url = f"https://{host}/hcmUI/CandidateExperience/en/sites/CX/job/{job_id}"
            all_jobs.append({
                "title": j.get("Title", "").strip(),
                "url": job_url,
                "location": locs,
            })
        offset += limit
        if offset > 5000:  # safety cap
            break
        if len(items) < limit:
            break
    return all_jobs


def fetch_simplify_newgrad(url):
    """SimplifyJobs/New-Grad-Positions auto-updates a listings.json every ~30 min
    with new-grad SWE/Quant/PM roles (US/Canada/Remote). This is a clean
    structured feed — far better than scraping the README. We try the known
    raw.githubusercontent paths for the listings file."""
    candidates = [
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/listings.json",
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/.github/scripts/listings.json",
    ]
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    data = None
    for c in candidates:
        try:
            r = requests.get(c, headers=headers, timeout=20)
            if r.status_code == 200 and r.text.strip().startswith("["):
                data = r.json()
                break
        except Exception:
            continue
    if data is None:
        raise ValueError("Could not fetch SimplifyJobs listings.json")

    jobs = []
    for item in data:
        # Schema: title, company_name, locations[], url, active, date_updated, visible
        if not item.get("active", True):
            continue
        if item.get("visible") is False:
            continue
        title = item.get("title", "").strip()
        company = item.get("company_name", "").strip()
        locs = item.get("locations", []) or []
        loc_str = ", ".join(locs) if isinstance(locs, list) else str(locs)
        link = item.get("url", "")
        if not title or not link:
            continue
        jobs.append({
            "title": f"{title}  —  ({company})",
            "url": link,
            "location": loc_str,
        })
    return jobs


def clean_network_error(e):
    err_str = str(e)
    if "ERR_NAME_NOT_RESOLVED" in err_str or "NameResolutionError" in err_str or "getaddrinfo failed" in err_str:
        return "DNS resolution failed (check internet connection or domain spelling)"
    if "ConnectionResetError" in err_str or "10054" in err_str or "Connection aborted" in err_str:
        return "Connection reset by server"
    if "TimeoutError" in err_str or "timed out" in err_str.lower():
        return "Timeout (server did not respond)"
    return err_str[:120]


def fetch_via_ats(url):
    """Try ATS API. Returns (jobs, method_name) on success, or (None, None) if not applicable."""
    # SimplifyJobs new-grad aggregator — special-cased.
    if "github.com/SimplifyJobs/New-Grad-Positions" in url or "simplifyjobs" in url.lower():
        try:
            return fetch_simplify_newgrad(url), "simplify-newgrad"
        except requests.exceptions.RequestException:
            raise
        except Exception:
            return None, None

    ats = detect_ats(url)
    if ats == "workday":
        return fetch_workday_api(url), "workday-api"
    if ats == "greenhouse":
        return fetch_greenhouse_api(url), "greenhouse-api"
    if ats == "lever":
        return fetch_lever_api(url), "lever-api"
    if ats == "ashby":
        return fetch_ashby_api(url), "ashby-api"
    if ats == "oracle":
        return fetch_oracle_api(url), "oracle-api"
    return None, None


def _format_text_attachment(new_jobs):
    """Plain text, grouped by company, columns aligned."""
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in new_jobs:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            company, j = item
        elif isinstance(item, dict):
            company = item.get("company", "Unknown")
            j = item
        else:
            continue
        grouped[company].append(j)

    lines = [
        "=" * 78,
        f"  {len(new_jobs)} software / systems job(s)",
        f"  Generated: {datetime.now().strftime('%A, %b %d %Y at %H:%M')}",
        "=" * 78,
        "",
    ]
    for company in sorted(grouped.keys()):
        jobs = grouped[company]
        lines.append(f"### {company}  ({len(jobs)} role{'s' if len(jobs) != 1 else ''})")
        lines.append("-" * 78)
        for j in jobs:
            title = j.get("title", "Untitled")
            lines.append(f"  • {title}")
            level = j.get("seniority") or detect_seniority(title)
            if level:
                lines.append(f"      Level:    {level}")
            if j.get("location"):
                loc = str(j["location"])[:100]
                lines.append(f"      Location: {loc}")
            if j.get("url"):
                lines.append(f"      Link:     {j['url']}")
            lines.append("")
        lines.append("")
    return "\n".join(lines)


def _format_html_attachment(new_jobs):
    """HTML version — formatted as a clean, responsive data table matching DB view."""
    normalized = []
    for item in new_jobs:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            co, j = item
            title = j.get("title", "Untitled")
            loc = j.get("location", "")
            url = j.get("url", "")
            seniority = j.get("seniority") or detect_seniority(title)
            score = float(j.get("match_score", 70.0))
        elif isinstance(item, dict):
            co = item.get("company", "Unknown")
            title = item.get("title", "Untitled")
            loc = item.get("location", "")
            url = item.get("url", "")
            seniority = item.get("seniority") or detect_seniority(title)
            score = float(item.get("match_score", 70.0))
        else:
            continue
        normalized.append({
            "company": co,
            "title": title,
            "location": loc,
            "url": url,
            "seniority": seniority,
            "match_score": score
        })

    # Sort descending by match score, then company name
    normalized.sort(key=lambda x: (-x["match_score"], x["company"].lower(), x["title"].lower()))

    parts = ["""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Job Watcher Digest</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }
  .container { max-width: 1080px; margin: 0 auto; background: #ffffff; border-radius: 12px; padding: 24px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
  h1 { font-size: 22px; font-weight: 700; color: #0f172a; margin-top: 0; margin-bottom: 6px; }
  .meta { color: #64748b; font-size: 13px; margin-bottom: 20px; }
  table { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }
  th { background-color: #f1f5f9; color: #475569; font-weight: 600; text-align: left; padding: 10px 12px; border-bottom: 2px solid #e2e8f0; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }
  td { padding: 12px; border-bottom: 1px solid #e2e8f0; vertical-align: middle; }
  tr:nth-child(even) { background-color: #fafafa; }
  tr:hover { background-color: #f1f5f9; }
  .score-col { width: 12%; font-weight: 700; color: #9333ea; }
  .score-badge { display: inline-block; padding: 3px 8px; border-radius: 12px; font-size: 11px; background: #f3e8ff; color: #7e22ce; border: 1px solid #d8b4fe; }
  .co-col { font-weight: 600; color: #2563eb; width: 18%; }
  .title-col { font-weight: 600; color: #0f172a; width: 34%; }
  .lvl-col { width: 12%; }
  .loc-col { color: #64748b; font-size: 12px; width: 14%; }
  .action-col { width: 10%; text-align: center; }
  .badge { display: inline-block; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; background: #e0f2fe; color: #0369a1; }
  .btn-link { display: inline-block; padding: 6px 12px; background-color: #2563eb; color: #ffffff !important; text-decoration: none; border-radius: 6px; font-size: 11px; font-weight: 600; text-align: center; }
  .btn-link:hover { background-color: #1d4ed8; }
</style></head><body>
<div class="container">"""]

    parts.append(f"  <h1>⚡ Job Watcher — {len(normalized)} Job(s) Found</h1>")
    parts.append(f'  <div class="meta">Generated: {datetime.now().strftime("%A, %b %d %Y at %H:%M")}</div>')
    parts.append("""  <table>
    <thead>
      <tr>
        <th>Match %</th>
        <th>Company</th>
        <th>Job Title</th>
        <th>Level</th>
        <th>Location</th>
        <th style="text-align:center;">Action</th>
      </tr>
    </thead>
    <tbody>""")

    for j in normalized:
        co = j["company"].replace("<", "&lt;").replace(">", "&gt;")
        title = j["title"].replace("<", "&lt;").replace(">", "&gt;")
        loc = (j["location"] or "—").replace("<", "&lt;").replace(">", "&gt;")
        lvl = (j["seniority"] or "").replace("<", "&lt;").replace(">", "&gt;")
        url = j["url"]
        score_val = round(j["match_score"])

        score_badge = f'<span class="score-badge">⚡ {score_val}%</span>'
        lvl_badge = f'<span class="badge">{lvl}</span>' if lvl else '—'
        link_html = f'<a href="{url}" target="_blank" class="btn-link">View Job ↗</a>' if url else '—'

        parts.append(f"""      <tr>
        <td class="score-col">{score_badge}</td>
        <td class="co-col">{co}</td>
        <td class="title-col">{title}</td>
        <td class="lvl-col">{lvl_badge}</td>
        <td class="loc-col">{loc[:80]}</td>
        <td class="action-col">{link_html}</td>
      </tr>""")

    parts.append("""    </tbody>
  </table>
</div>
</body></html>""")
    return "\n".join(parts)


def send_email(new_jobs):
    enable_flag = os.environ.get("ENABLE_EMAIL_ALERTS", "false").lower() in ("true", "1", "yes")
    if not enable_flag:
        print("[INFO] Email alerts disabled (ENABLE_EMAIL_ALERTS=false). Skipping email sending.")
        return

    try:
        timestamp = datetime.now().strftime("%b %d %H:%M")

        # Build a clean HTML body that renders inline in the email (clickable!).
        html_body = _format_html_attachment(new_jobs)

        # Also include a plain-text fallback for email clients that don't render HTML.
        text_body = _format_text_attachment(new_jobs)

        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"{len(new_jobs)} new systems job(s) — {timestamp}"
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        # The body alternative (text + HTML) — Gmail will show the HTML.
        body_alt = MIMEMultipart("alternative")
        body_alt.attach(MIMEText(text_body, "plain", "utf-8"))
        body_alt.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(body_alt)

        if ATTACH_TXT_FILE:
            stamp = datetime.now().strftime("%Y%m%d_%H%M")

            # Formatted plain-text file
            txt_attach = MIMEApplication(text_body.encode("utf-8"), _subtype="txt")
            txt_attach.add_header("Content-Disposition", "attachment",
                                  filename=f"jobs_{stamp}.txt")
            msg.attach(txt_attach)

            # HTML file with guaranteed-clickable links (open in browser)
            html_attach = MIMEApplication(html_body.encode("utf-8"), _subtype="html")
            html_attach.add_header("Content-Disposition", "attachment",
                                   filename=f"jobs_{stamp}.html")
            msg.attach(html_attach)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        print(f"[SUCCESS] Sent email for {len(new_jobs)} jobs.")
    except Exception as e:
        print(f"[INFO] Email sending skipped due to SMTP configuration: {e}")


async def process_company(browser, company, sem):
    async with sem:
        name = company["name"]
        url = company["url"]
        selector = company.get("selector")

        # Try ATS-specific API first (fast, reliable, structured data).
        try:
            ats_jobs, method = await asyncio.to_thread(fetch_via_ats, url)
            if ats_jobs is not None:
                return name, ats_jobs, None, method
        except requests.exceptions.RequestException as e:
            return name, [], f"Network error: {clean_network_error(e)}", "ats-api"
        except Exception as e:
            pass

        # Known bot-walled / JS-fortress sites: scraping them always returns 0
        # AND each one hangs for the full timeout, wrecking cycle time. Skip them
        # entirely — use native job alerts on these companies' own sites instead.
        BOT_WALLED = {
            "Google", "Apple", "Meta", "Microsoft", "Amazon", "Netflix", "IBM",
            "Oracle", "LinkedIn", "Pinterest", "ByteDance / TikTok US", "Niantic",
            "Twitch", "AT&T", "T-Mobile", "L3Harris", "Tesla", "Snowflake",
            "Cohere", "InfluxData", "MemryX", "Rivos",
        }
        if name in BOT_WALLED:
            return name, [], None, "skipped (bot-walled)"

        # Fall back to browser scraping with heuristics.
        try:
            html = await fetch_page(browser, url)
            jobs = extract_jobs(html, url, selector)
            return name, jobs, None, "scrape"
        except Exception as e:
            return name, [], clean_network_error(e), "scrape"


async def verify_url(url, session_lock):
    """Quickly check if a URL is reachable.
    Returns True if 200/3xx (or rate-limited/forbidden — we don't drop those).
    Returns False only on clear 404/410/etc. or unreachable host."""
    def _check():
        try:
            # Use a real-browser User-Agent so we don't get blocked.
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
            }
            # HEAD first (cheap). Some sites block HEAD; fall back to a small GET.
            r = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
            if r.status_code == 405 or r.status_code == 403:
                # Method not allowed or forbidden → try GET with range header
                headers["Range"] = "bytes=0-1023"
                r = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
            if 200 <= r.status_code < 300:
                return True
            if 300 <= r.status_code < 400:
                # Redirect — usually followed automatically by requests, but be defensive.
                return True
            if r.status_code in (403, 429, 503):
                # Rate-limited or temporarily blocked — don't drop the job.
                return True
            return False  # 404, 410, 500, etc.
        except requests.RequestException:
            # Network error → don't drop; might be transient.
            return True

    return await asyncio.to_thread(_check)


async def verify_urls(jobs):
    """Verify a list of (company_name, job_dict) tuples in parallel.
    Returns (verified_jobs, dropped_count)."""
    if not jobs:
        return jobs, 0

    sem = asyncio.Semaphore(URL_VERIFY_CONCURRENCY)

    async def _verify_one(item):
        async with sem:
            return await verify_url(item[1]["url"], sem)

    results = await asyncio.gather(*[_verify_one(item) for item in jobs])
    verified = [job for job, ok in zip(jobs, results) if ok]
    dropped = len(jobs) - len(verified)
    return verified, dropped


async def _process_and_log(browser, company, sem, conn):
    """Wrap process_company so that a dashboard event is emitted the moment
    this company finishes — not after all 361 have completed."""
    name = company["name"]

    # Fire a 'checking' event as soon as a concurrency slot opens for this company
    log_event(conn, "company_checking", name, f"Scraping {company.get('url', '')}...")
    try:
        conn.commit()
    except Exception:
        pass

    name, jobs, err, method = await process_company(browser, company, sem)

    if err:
        log_event(conn, "company_error", name, err[:200])
    elif method == "skipped (bot-walled)":
        log_event(conn, "company_skip", name, "bot-walled — using native alerts")
    else:
        matched = 0
        us_ok   = 0
        cur = conn.cursor()
        for j in jobs:
            if not is_target_job(j.get("title", "")):
                continue
            matched += 1
            if not is_us_location(j.get("location", ""), j.get("title", "")):
                continue
            us_ok += 1

            # Store job card details in jobs_detail as each company finishes
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO jobs_detail "
                    "(job_id, url, title, company, location, seniority, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        canonical_job_id(j["url"]),
                        j["url"],
                        j.get("title", ""),
                        name,
                        j.get("location", ""),
                        detect_seniority(j.get("title", "")),
                        datetime.now().timestamp(),
                    ),
                )
            except Exception:
                pass

        log_event(
            conn, "company_ok", name,
            f"{len(jobs)} fetched \u2502 {matched} title-match \u2502 {us_ok} loc-pass [{method or 'scrape'}]",
        )

    try:
        conn.commit()          # flush immediately so the dashboard poll sees it
    except Exception:
        pass

    return name, jobs, err, method


async def check_once(conn, browser):
    cycle_start = datetime.now()
    log_event(conn, "cycle_start",
              message=f"Checking {len(COMPANIES)} companies ({CONCURRENCY} parallel)")
    conn.commit()

    sem = asyncio.Semaphore(CONCURRENCY)
    # Use wrapper so each company logs AS SOON AS it finishes, not in a batch at the end
    tasks = [_process_and_log(browser, c, sem, conn) for c in COMPANIES]
    results = await asyncio.gather(*tasks)

    cur = conn.cursor()
    new_jobs = []
    candidates = []
    cycle_seen_ids = set()  # within-cycle dedup: prevents same job appearing twice in one email

    for name, jobs, err, method in results:
        # company_ok / company_error / company_skip were already logged inside
        # _process_and_log() the moment each company finished — skip those here.
        if err:
            print(f"  ! {name}: {err}")
            continue
        if method == "skipped (bot-walled)":
            continue

        matched = 0
        us_pass = 0
        cand_count = 0
        for j in jobs:
            if not is_target_job(j["title"]):
                continue
            matched += 1
            if not is_us_location(j.get("location", ""), j.get("title", "")):
                continue
            us_pass += 1
            cjid = canonical_job_id(j["url"])   # canonical stable key
            if cjid in cycle_seen_ids:
                continue  # already queued this job earlier in this cycle — skip duplicate
            cur.execute("SELECT 1 FROM seen WHERE job_id = ?", (cjid,))
            if cur.fetchone():
                continue
            cycle_seen_ids.add(cjid)
            candidates.append((name, j))
            cand_count += 1

        tag = f" [{method}]" if method and method != "scrape" else ""
        print(f"  {name}: {len(jobs)} links | {matched} systems | {us_pass} passed | {cand_count} new{tag}")

    # Verify candidate URLs are reachable before alerting on them.
    if VERIFY_URLS and candidates:
        print(f"  Verifying {len(candidates)} candidate URL(s)...")
        log_event(conn, "url_verify", message=f"Verifying {len(candidates)} URLs…")
        verified, dropped = await verify_urls(candidates)
        if dropped:
            print(f"  Dropped {dropped} broken URL(s) — kept {len(verified)}.")
        new_jobs = verified
    else:
        new_jobs = candidates

    # Now commit the verified jobs to the database so we don't re-alert next cycle.
    for company_name, j in new_jobs:
        cjid = canonical_job_id(j["url"])    # stable primary key
        cur.execute("INSERT OR IGNORE INTO seen (job_id) VALUES (?)", (cjid,))
        # Store rich metadata for the dashboard.
        log_event(conn, "new_job", company_name, j["title"])
        try:
            cur.execute(
                "INSERT OR IGNORE INTO jobs_detail "
                "(job_id, url, title, company, location, seniority, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    cjid,
                    j["url"],
                    j.get("title", ""),
                    company_name,
                    j.get("location", ""),
                    detect_seniority(j.get("title", "")),
                    datetime.now().timestamp(),
                ),
            )
        except Exception:
            pass

    elapsed = (datetime.now() - cycle_start).total_seconds()
    log_event(conn, "cycle_end",
              message=f"Done in {elapsed:.0f}s │ {len(new_jobs)} new job(s) found")

    # ---- Purge stale jobs_detail rows older than 30 days ----
    # Keeps the DB compact; the seen table is never purged (prevents re-alerting).
    cutoff = (datetime.now().timestamp()) - (30 * 24 * 3600)
    try:
        cur.execute("DELETE FROM jobs_detail WHERE first_seen < ?", (cutoff,))
        purged = cur.rowcount
        if purged:
            print(f"  Purged {purged} stale jobs_detail row(s) older than 30 days.")
    except Exception:
        pass

    conn.commit()

    # ---- Trigger JD Extraction & NVIDIA LLM Evaluation ----
    try:
        from jd_fetcher import fetch_and_store_jds
        from llm_evaluator import evaluate_pending_jobs
        print("  Extracting Job Descriptions...")
        fetch_and_store_jds()
        print("  Evaluating jobs with NVIDIA Nemotron LLM...")
        evaluate_pending_jobs()
    except Exception as e:
        print(f"  JD/LLM processing note: {e}")

    return new_jobs


async def main():
    conn = init_db()

    print(f"Watching {len(COMPANIES)} companies, every {CHECK_INTERVAL_MINUTES} min.")
    print(f"Concurrency: {CONCURRENCY}. Daily digest at {DAILY_DIGEST_HOUR:02d}:00. Ctrl+C to stop.")
    print(f"Dashboard:   http://localhost:5000  (run dashboard.py separately)\n")

    log_event(conn, "watcher_start",
              message=f"Watching {len(COMPANIES)} companies │ every {CHECK_INTERVAL_MINUTES} min │ concurrency {CONCURRENCY}")
    conn.commit()

    digest_buffer = []          # accumulates all new jobs seen since last digest
    last_digest_date = datetime.now().date()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            while True:
                start = datetime.now()
                print(f"[{start.strftime('%H:%M:%S')}] Cycle starting...")
                new_jobs = await check_once(conn, browser)
                elapsed = (datetime.now() - start).total_seconds()
                print(f"  Cycle done in {elapsed:.0f}s.")

                if new_jobs:
                    print(f"  {len(new_jobs)} new job(s). Emailing...")
                    try:
                        send_email(new_jobs)
                        log_event(conn, "email_sent",
                                  message=f"Alert sent │ {len(new_jobs)} new job(s)")
                        conn.commit()
                        print("  Email sent.\n")
                    except Exception as e:
                        log_event(conn, "email_error", message=str(e)[:200])
                        conn.commit()
                        print(f"  Email failed: {e}\n")
                    digest_buffer.extend(new_jobs)
                else:
                    print("  No new jobs.\n")

                # ---- Daily digest: once per day at DAILY_DIGEST_HOUR ----
                now = datetime.now()
                if (now.hour == DAILY_DIGEST_HOUR
                        and now.date() != last_digest_date):
                    last_digest_date = now.date()
                    if digest_buffer:
                        print(f"  Sending daily digest ({len(digest_buffer)} jobs in last 24h)...")
                        try:
                            send_email(digest_buffer)
                            print("  Digest sent.\n")
                        except Exception as e:
                            print(f"  Digest failed: {e}\n")
                    else:
                        print("  Daily digest: nothing new in the last 24h.\n")
                    digest_buffer = []

                await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)
        finally:
            await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
