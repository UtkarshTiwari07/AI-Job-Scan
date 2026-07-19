"""
Autonomous India MNC Job Search Agent — v4
==========================================
Phases:
  1. Serper.dev multi-cluster search — per-site AND broad free-text
     + direct LinkedIn Jobs URL injection
     (qdr:w = last week in Serper, Phase 3 enforces 3-day freshness)
  2. Crawl4AI universal scrape
  3. Pure-Python pre-filter (zero LLM cost)
  4. DeepSeek V3 evaluation + cover letter drafting

Run:
  python job/job_india_mnc.py           # full run
  python job/job_india_mnc.py --dry-run # test filters, no API calls
"""

import asyncio
import json
import os
import re
import sys
import hashlib
import datetime
import warnings
import requests
from typing import List, Optional, Set
from pydantic import BaseModel

warnings.filterwarnings("ignore", message="urllib3 .* doesn't match a supported version!")

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from openai import OpenAI

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERPER_API_KEY   = os.getenv("SERPER_API_KEY")
SEEN_FP_FILE     = os.path.join(os.path.dirname(__file__), "seen_fp_india_mnc.json")

# ── Job boards (per-site queries) ────────────────────────────────
# NOTE: cutshort.io REMOVED — it returns entire listing pages with unrelated jobs
#       (cricket coaches, telecallers, maths tutors) because Crawl4AI scrapes all
#       visible jobs on the page, not just the queried one.
TARGET_SITES = [
    "naukri.com",
    "linkedin.com",
    "foundit.in",
    "wellfound.com",
    "instahyre.com",
    "iimjobs.com",
    "hirist.tech",
]

# ── Domain allowlist ─────────────────────────────────────────────
# ONLY jobs scraped from these domains pass Phase 3.
# Any Crawl4AI result from talent.com / apna.co / fresheroffcampus /
# qureos / simplyhired / glassdoor / instagram etc. is hard-rejected.
SITE_ALLOWLIST = {
    "naukri.com", "linkedin.com", "lnkd.in",
    "foundit.in", "wellfound.com", "instahyre.com",
    "iimjobs.com", "hirist.tech",
    # India AI startups
    "sarvam.ai", "krutrim.ai", "observe.ai", "yellow.ai",
    "haptik.ai", "uniphore.com", "sprinklr.com", "razorpay.com",
    "groww.in", "meesho.io", "smallcase.com", "zepto.team",
    "hasura.io", "freshworks.com",
    # ATS platforms
    "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
    "smartrecruiters.com", "jobs.smartrecruiters.com",
    # MNC career portals
    "careers.microsoft.com", "careers.google.com", "amazon.jobs",
    "careers.adobe.com", "atlassian.com", "salesforce.com",
    "careers.oracle.com", "jobs.sap.com", "jobs.cisco.com",
    "ibm.com", "nvidia.wd5.myworkdayjobs.com", "databricks.com",
    "stripe.com", "mongodb.com", "elastic.co", "hubspot.com",
    "workday.com",
    # New domains from direct ATS/MNC URLs
    "cohere.com", "huggingface.co", "modal.com", "together.ai",
    "anyscale.com", "wandb.ai", "clarifai.com", "turing.com", "scale.ai",
    # NOTE: in.indeed.com EXCLUDED — its listing pages return JioStar sports
    #       interns, Deloitte M&A interns, AWS interns etc alongside AI jobs.
}

# ── Direct company career pages ────────────────────────────────
# Group 1: India AI startups (for Serper C1 cluster)
DIRECT_COMPANY_PAGES = [
    "site:sarvam.ai",
    "site:krutrim.ai",
    "site:observe.ai",
    "site:yellow.ai",
    "site:haptik.ai",
    "site:uniphore.com",
    "site:sprinklr.com",
    "site:razorpay.com",
    "site:groww.in",
    "site:meesho.io",
    "site:smallcase.com",
    "site:zepto.team",
]

# Group 2: Big MNC career portals (for Serper C2 cluster)
MNC_CAREER_PAGES = [
    "site:careers.microsoft.com",
    "site:adobe.com/careers",
    "site:atlassian.com/company/careers",
    "site:salesforce.com/company/careers",
    "site:oracle.com/careers",
    "site:sap.com/careers",
    "site:cisco.com/c/en/us/about/careers",
    "site:ibm.com/employment",
    "site:amazon.jobs",
    "site:nvidia.com/en-us/about-nvidia/careers",
    "site:databricks.com/company/careers",
    "site:stripe.com/jobs",
    "site:hubspot.com/careers",
    "site:workday.com/en-us/company/careers",
    "site:mongodb.com/careers",
    "site:elastic.co/careers",
    "site:hasura.io/careers",
    "site:freshworks.com/company/careers",
]

# ── DIRECT ATS URL injection ────────────────────────────────────
# These bypass Serper entirely. Each URL is a pre-filtered search on a company’s
# own ATS (Greenhouse, Lever, Workday, SmartRecruiters, etc.) for India AI roles.
# Crawl4AI will scrape the listing page and extract all matching jobs.
DIRECT_COMPANY_URLS = [
    # ── Greenhouse ATS (most YC-backed + funded startups) ──
    "https://boards.greenhouse.io/embed/job_board?for=cohere&b=https%3A%2F%2Fcohere.com%2Fcareers",
    "https://boards.greenhouse.io/huggingface",
    "https://boards.greenhouse.io/modal",
    "https://boards.greenhouse.io/together",
    "https://boards.greenhouse.io/anyscale",
    "https://boards.greenhouse.io/wandb",
    "https://boards.greenhouse.io/clarifai",
    # ── Lever ATS ──
    "https://jobs.lever.co/turing",
    "https://jobs.lever.co/scale",
    # ── Ashby ATS ──
    "https://jobs.ashbyhq.com/sarvam",
    # ── Microsoft India ──
    "https://careers.microsoft.com/v2/global/en/search.html?lc=India&l=en_us&d=Software%20Engineering&exp=Experienced%20professionals&et=Full-Time",
    # ── Google India ──
    "https://careers.google.com/jobs/results/?company=Google&jex=ENTRY_LEVEL&location=India&q=machine+learning",
    "https://careers.google.com/jobs/results/?company=Google&jex=ENTRY_LEVEL&location=India&q=AI+engineer",
    # ── Databricks India ──
    "https://www.databricks.com/company/careers/open-positions?department=Engineering&location=India",
    # ── Salesforce India ──
    "https://careers.salesforce.com/en/jobs/?search=AI+engineer&location=India&country=India",
    # ── Stripe India ──
    "https://stripe.com/jobs/search?location_filter=india&name=engineer",
    # ── Atlassian India ──
    "https://www.atlassian.com/company/careers/all-jobs?location=India&team=Engineering",
    # ── Oracle India ──
    "https://careers.oracle.com/jobs/#en/sites/jobsearch/jobs?keyword=AI+ML&location=India",
    # ── Freshworks India ──
    "https://www.freshworks.com/company/careers/job-openings/?location=india",
    # ── Adobe India ──
    "https://careers.adobe.com/us/en/search-results?keywords=AI%20engineer&country=India",
    # ── IBM India ──
    "https://www.ibm.com/careers/search?q=AI+engineer&country=India",
    # ── Nvidia India ──
    "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite?locationCountry=469a2d0c4ba44f4da32ece4bfd00e5b4&q=deep+learning",
    # ── SAP India ──
    "https://jobs.sap.com/search/?q=AI+engineer&locname=India&country=IN",
    # ── Cisco India ──
    "https://jobs.cisco.com/jobs/SearchJobs/AI?listFilterMode=1&21178=%5B186%5D&21178_format=6020",
    # ── SmartRecruiters hosted portals ──
    "https://jobs.smartrecruiters.com/?keyword=LLM+engineer&location=India",
    "https://jobs.smartrecruiters.com/?keyword=generative+AI&location=India",
]

# ── LinkedIn India AI Jobs direct search URLs (injected into Phase 1) ──
# f_TPR=r259200 = last 3 days, f_E=1,2 = Internship+Entry, f_E=2 = Entry level
LINKEDIN_DIRECT_URLS = [
    "https://www.linkedin.com/jobs/search/?keywords=LLM%20Engineer&location=India&f_TPR=r259200&f_E=2",
    "https://www.linkedin.com/jobs/search/?keywords=AI%20Agent%20Engineer&location=India&f_TPR=r259200&f_E=2",
    "https://www.linkedin.com/jobs/search/?keywords=Generative%20AI%20Engineer&location=India&f_TPR=r259200&f_E=1%2C2",
    "https://www.linkedin.com/jobs/search/?keywords=RAG%20Engineer&location=India&f_TPR=r259200",
    "https://www.linkedin.com/jobs/search/?keywords=voice%20AI%20engineer&location=India&f_TPR=r259200",
    "https://www.linkedin.com/jobs/search/?keywords=LangChain%20FastAPI%20engineer&location=India&f_TPR=r259200&f_E=2",
    "https://www.linkedin.com/jobs/search/?keywords=AI%20engineer%20startup%20India&f_TPR=r259200&f_E=1%2C2",
]

# ── Wellfound India direct URLs ───────────────────────────────────
WELLFOUND_DIRECT_URLS = [
    "https://wellfound.com/jobs?q=AI+engineer&l=India&remote=false",
    "https://wellfound.com/jobs?q=LLM+engineer&l=India",
    "https://wellfound.com/jobs?q=generative+AI&l=India",
]

# ── Naukri direct URLs ────────────────────────────────────────────
NAUKRI_DIRECT_URLS = [
    "https://www.naukri.com/llm-engineer-jobs-in-india",
    "https://www.naukri.com/ai-engineer-jobs-in-india?experience=0",
    "https://www.naukri.com/generative-ai-engineer-jobs",
]

# ── 8 Serper query clusters ───────────────────────────────────────
QUERY_CLUSTERS = [
    # ── GROUP A: Site-restricted (board-targeted) ────────────────
    {
        "name": "A1 — LLM/RAG/Agent Engineer India [per-site]",
        "terms": (
            '("LLM engineer" OR "RAG engineer" OR "AI agent engineer" OR "generative AI engineer") '
            '("0-2 years" OR "0-1 year" OR "entry level" OR "junior" OR "fresher") India'
        ),
        "num": 20,
        "sites": TARGET_SITES,
        "broad": False,
    },
    {
        "name": "A2 — Voice AI / LangChain / FastAPI India [per-site]",
        "terms": (
            '("voice AI" OR "LangChain" OR "CrewAI" OR "FastAPI" OR "Pinecone" OR "LiveKit") '
            '"AI engineer" OR "software engineer" India'
        ),
        "num": 20,
        "sites": TARGET_SITES,
        "broad": False,
    },
    {
        "name": "A3 — Funded AI Startup India [per-site]",
        "terms": (
            '("Series B" OR "Series C" OR "unicorn" OR "funded" OR "startup") '
            '"AI engineer" OR "ML engineer" ("fresher" OR "entry level" OR "0-2 years") India'
        ),
        "num": 20,
        "sites": TARGET_SITES,
        "broad": False,
    },
    # ── GROUP B: Broad free-text, BUT Google-negative-filtered to block apna/talent/qureos
    #    Note: broad=True means no site: prefix, but we add hard -site: exclusions in the query
    {
        "name": "B1 — GenAI Engineer India [broad, AI boards only]",
        "terms": (
            '"generative AI engineer" OR "LLM engineer" OR "AI agent developer" '
            'India ("0-2 years" OR "freshers" OR "entry level" OR "junior") '
            '-"data scientist" -"data analyst" -"business analyst" -"medical" '
            '-site:apna.co -site:talent.com -site:qureos.com -site:fresheroffcampus.com '
            '-site:simplyhired.co.in -site:ambitionbox.com -site:shine.com -site:monsterindia.com '
            '-site:glassdoor.co.in -site:jaabz.com -site:instagram.com'
        ),
        "num": 20,
        "sites": ["_broad_"],
        "broad": True,
    },
    {
        "name": "B2 — Named AI Companies India [broad]",
        "terms": (
            '(Sarvam OR Krutrim OR "Observe.AI" OR "Yellow.ai" OR Haptik OR Uniphore OR '
            '"Sprinklr" OR "Murf AI" OR "Gnani AI" OR "Slang Labs" OR "PolyAI" OR "Reverie") '
            '"AI engineer" OR "backend engineer" ("junior" OR "fresher" OR "0-2") India '
            '-site:apna.co -site:talent.com -site:qureos.com -site:glassdoor.co.in'
        ),
        "num": 20,
        "sites": ["_broad_"],
        "broad": True,
    },
    {
        "name": "B3 — YC / Sequoia India AI [broad]",
        "terms": (
            '("Y Combinator" OR "YC" OR "Sequoia" OR "Lightspeed" OR "Accel" OR "Nexus") '
            '"AI engineer" OR "founding engineer" India '
            '("0-2 years" OR "entry level" OR "fresher") -senior -lead '
            '-site:apna.co -site:talent.com -site:glassdoor.co.in -site:qureos.com'
        ),
        "num": 20,
        "sites": ["_broad_"],
        "broad": True,
    },
    # ── GROUP C: Direct company pages (India AI startups) ─────────────
    {
        "name": "C1 — India AI Startup Career Pages",
        "terms": (
            '"AI engineer" OR "software engineer" OR "backend engineer" OR "founding engineer" '
            'India ("junior" OR "0-2 years" OR "entry" OR "fresher" OR "new grad")'
        ),
        "num": 10,
        "sites": DIRECT_COMPANY_PAGES,
        "broad": False,
        "sites_preformatted": True,
    },
    # ── GROUP C2: MNC career portals ────────────────────────────────
    {
        "name": "C2 — Big MNC Career Portals India",
        "terms": (
            '("AI engineer" OR "ML engineer" OR "machine learning" OR "LLM" OR '
            '"generative AI" OR "AI software engineer") India'
        ),
        "num": 10,
        "sites": MNC_CAREER_PAGES,
        "broad": False,
        "sites_preformatted": True,
    },
    # ── GROUP D: Python AI backend catch-all ─────────────────────
    {
        "name": "D1 — Python AI Backend India [broad]",
        "terms": (
            '("python AI engineer" OR "backend AI" OR "AI infrastructure" OR "LLM platform") '
            'India ("0-2 years" OR "junior" OR "fresher") '
            '-"data scientist" -"data engineer" -"DevOps" -"SRE" '
            '-site:apna.co -site:talent.com -site:qureos.com -site:glassdoor.co.in '
            '-site:fresheroffcampus.com -site:simplyhired.co.in'
        ),
        "num": 20,
        "sites": ["_broad_"],
        "broad": True,
    },
]

CANDIDATE_PROFILE = {
    "name": "Utkarsh Tiwari",
    "stack": (
        "AI Engineer (1 YOE). Python, PyTorch, LightGBM, RAG, "
        "LLMs (GPT-4, Gemini, LLaMA LoRA fine-tuning), CrewAI, LangChain, "
        "FastAPI, LiveKit, Deepgram STT, ElevenLabs TTS, Pinecone."
    ),
    "metrics": (
        "Built production AI voice infrastructure handling 2,000+ concurrent calls. "
        "Reduced LLM cold-start latency by 10.4x (3.9s to 378ms) with Groq and Pinecone. "
        "Trained LightGBM models on 716K+ records. "
        "Reduced AI-content detection from 100% to 30% in multi-agent architectures."
    ),
}

# Pre-filter rules
RECRUITER_PATTERN = re.compile(r"\b(recruit|staffing|placement agency|hr solutions|manpower)\b", re.IGNORECASE)

# Hard-reject non-AI-stack job titles
# Expanded massively after live-run analysis: QA/tester/PHP/Rails/Angular/MERN/Node/
# delivery/HR/sales/marketing/medical/banking noise observed in real data.
TITLE_REJECT_PATTERNS = re.compile(
    r"(\bqa\b|quality ana|quality assur|manual test|software test|functional test|automation test|"
    r"medical writer|medical editor|biostatistic|statistical programmer|clinical research|"
    r"pharmacovigilance|regulatory affair|"
    r"relationship officer|sales|telecall|tele sales|tele caller|"
    r"business development|branch manager|delivery boy|customer service|client serv|"
    r"hr \b|human resource|talent acqui|talent manag|"
    r"marketing|social media|graphic design|content writer|digital content|"
    r"data analyst|business analyst|data modeler|power bi|tableau|"
    r"data engineer(?!.*ai)|data scientist|mlops|"
    r"computer vision|cv engineer|nlp researcher|deep learning researcher|robotics|"
    r"\bjava\b|java developer|java engineer|\.net\b|angular|react native|mern|mean stack|"
    r"php developer|php engineer|ruby on rails|node.?js developer|wordpress|"
    r"android\b|ios developer|ios engineer|flutter|kotlin|swift|"
    r"devops(?!.*ai)|sysadmin|network engineer|embedded|firmware|"
    r"blockchain|solidity|web3(?!.*ai)|nft|"
    r"full.?stack(?!.*ai|.*ml|.*python)|frontend(?!.*ai)|"
    r"support consultant|technical support(?!.*ai)|it support|service desk|"
    r"salesforce|oracle|powerbi|odoo|teamcenter|"
    r"executive assistant|chief of staff|operations manager|scrum master|"
    r"cyber security|cybersecurity|penetration test)",
    re.IGNORECASE,
)

# Keywords that MUST appear in title OR description for a job to be considered AI-relevant.
# If description is empty and title has none of these, the job is hard-rejected before LLM.
AI_RELEVANCE_KEYWORDS = re.compile(
    r"(\bllm\b|\brag\b|langchain|crewai|fastapi|openai|gemini|claude|gpt|pinecone|"
    r"\bai\b|\bml\b|machine learning|generative|agentic|voice ai|livek|deepgram|"
    r"eleven.?labs|transformer|fine.?tun|vector|embedding|chatbot|nlp|hugging face|"
    r"pytorch|tensorflow|sklearn|scikit|inferenc|llama|mistral|prompt)",
    re.IGNORECASE,
)

# Reject tokens checked ONLY in title (not description body)
EXPERIENCE_TITLE_REJECT = re.compile(
    r"\b(senior|lead|principal|manager|director|vp |head of)\b", re.IGNORECASE
)
EXPERIENCE_YEARS_REJECT = ["3+ years", "4+ years", "5+ years", "6+ years", "7+ years", "8+ years", "10+ years", "10+"]

# Geo-lock: US/UK/EU-only positions sneaking onto Indian boards
GEO_LOCK_TOKENS = [
    "united states only", "us only", "us citizens", "us permanent resident", "green card",
    "uk residents", "uk-based", "canada only", "australia only", "europe only", "eu only",
    "must be located in the us", "authorized to work in the us",
]

EDUCATION_REJECT_TOKENS = [
    "master's degree required", "masters degree required", "m.s. required",
    "msc required", "m.tech required", "phd required", "ph.d", "doctorate required",
    "doctoral degree", "postgraduate degree required",
]

MAX_POSTING_AGE_DAYS = 3   # 3-day window (qdr:w in Serper + Phase 3 enforcement)


# ══════════════════════════════════════════════════════════════════
# CROSS-RUN DEDUP
# ══════════════════════════════════════════════════════════════════

def load_seen_fingerprints() -> dict:
    if not os.path.exists(SEEN_FP_FILE):
        return {}
    try:
        with open(SEEN_FP_FILE) as f:
            data = json.load(f)
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat()
        return {fp: ts for fp, ts in data.items() if ts >= cutoff}
    except Exception:
        return {}

def save_seen_fingerprints(fp_map: dict):
    try:
        with open(SEEN_FP_FILE, "w") as f:
            json.dump(fp_map, f)
    except Exception as e:
        print(f"  ⚠️ Could not save fingerprint cache: {e}")


# ══════════════════════════════════════════════════════════════════
# PHASE 1 — SERPER MULTI-CLUSTER + DIRECT URL INJECTION
# ══════════════════════════════════════════════════════════════════

def search_for_jobs() -> List[str]:
    if not SERPER_API_KEY:
        print("❌ SERPER_API_KEY not set"); return []

    print("\n🔍 PHASE 1 — Serper.dev Multi-Cluster Search (qdr:w, 3-day Phase 3 filter)")
    seen_urls: Set[str] = set()
    all_urls: List[str] = []
    api_url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    for cluster in QUERY_CLUSTERS:
        print(f"\n  📌 {cluster['name']}")
        is_broad = cluster.get("broad", False)
        is_preformatted = cluster.get("sites_preformatted", False)
        sites = cluster["sites"]

        if is_broad:
            # Single broad query — no site: restriction
            query = cluster["terms"]
            print(f"     → [BROAD] {query[:120]}")
            payload = json.dumps({
                "q":   query,
                "num": cluster["num"],
                "tbs": "qdr:w",   # last week — Phase 3 enforces 3 days
                "gl":  "in",      # India region
                "hl":  "en",
            })
            try:
                resp = requests.post(api_url, headers=headers, data=payload, timeout=15)
                resp.raise_for_status()
                found = 0
                for r in resp.json().get("organic", []):
                    link = r.get("link", "").strip()
                    if link and link not in seen_urls:
                        seen_urls.add(link)
                        all_urls.append(link)
                        found += 1
                print(f"       ✓ {found} new URLs")
            except Exception as e:
                print(f"     ⚠️ Serper error: {e}")
        else:
            # Per-site queries
            for site in sites:
                site_part = site if is_preformatted else f"site:{site}"
                query = f"{site_part} {cluster['terms']}"
                print(f"     → {query[:130]}")
                payload = json.dumps({
                    "q":   query,
                    "num": cluster["num"],
                    "tbs": "qdr:w",
                    "gl":  "in",
                    "hl":  "en",
                })
                try:
                    resp = requests.post(api_url, headers=headers, data=payload, timeout=15)
                    resp.raise_for_status()
                    found = 0
                    for r in resp.json().get("organic", []):
                        link = r.get("link", "").strip()
                        if link and link not in seen_urls:
                            seen_urls.add(link)
                            all_urls.append(link)
                            found += 1
                    print(f"       ✓ {found} from {site.split('/')[-1] if '/' in site else site}")
                except Exception as e:
                    print(f"     ⚠️ Serper error ({site}): {e}")

    # ── Direct URL injection ───────────────────────────────────
    all_direct = LINKEDIN_DIRECT_URLS + WELLFOUND_DIRECT_URLS + NAUKRI_DIRECT_URLS + DIRECT_COMPANY_URLS
    print(f"\n  🔗 Injecting {len(all_direct)} direct URLs (LinkedIn + Wellfound + Naukri + MNC portals)...")
    for url in all_direct:
        if url not in seen_urls:
            seen_urls.add(url)
            all_urls.append(url)

    print(f"\n  🎯 {len(all_urls)} unique URLs queued for scraping")
    return all_urls


# ══════════════════════════════════════════════════════════════════
# PHASE 2 — CRAWL4AI UNIVERSAL SCRAPE
# ══════════════════════════════════════════════════════════════════

class ScrapedJob(BaseModel):
    title:               str = ""
    company:             str = ""
    url:                 str = ""
    site:                str = ""
    posted_date:         str = ""
    location_text:       str = ""
    is_remote:           bool = False
    job_type:            str = ""
    pay_text:            str = ""
    experience_text:     str = ""
    description_snippet: str = ""

SCRAPE_INSTRUCTION = """
Extract EVERY job posting visible on this page.
For each job return:
  title, company, url (the direct apply/detail link), site (domain),
  posted_date (ISO 8601 date OR relative string like '3 hours ago' — ALWAYS fill this),
  location_text (exact text shown), is_remote (true/false),
  job_type (full-time / contract / part-time / unknown),
  pay_text (exact salary shown, or empty),
  experience_text (exact years/level shown, e.g. "0-2 years", "fresher", "3-5 yrs" — ALWAYS fill this),
  description_snippet (first 400 chars of job description — ALWAYS fill even if partial).
If the page is NOT a job listing, return an empty list [].
""".strip()


async def scrape_jobs(urls: List[str], raw_ndjson_path: str) -> List[dict]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    import logging
    logging.getLogger("crawl4ai").setLevel(logging.ERROR)

    print(f"\n🕷️  PHASE 2 — Crawl4AI scraping {len(urls)} URLs...")

    strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(provider="deepseek/deepseek-chat", api_token=DEEPSEEK_API_KEY),
        schema=ScrapedJob.model_json_schema(),
        extraction_type="schema",
        instruction=SCRAPE_INSTRUCTION,
    )
    run_cfg    = CrawlerRunConfig(extraction_strategy=strategy, cache_mode=CacheMode.BYPASS, magic=True)
    browser_cfg = BrowserConfig(headless=True)

    all_jobs: List[dict] = []
    seen_fingerprints: Set[str] = set()

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        results = await crawler.arun_many(urls=urls, config=run_cfg)
        for result in results:
            if not result.success or not result.extracted_content:
                continue
            try:
                parsed = json.loads(result.extracted_content)
                items  = parsed if isinstance(parsed, list) else [parsed]
            except (json.JSONDecodeError, TypeError):
                continue
            for job in items:
                if not isinstance(job, dict) or not job.get("title"):
                    continue
                fp = hashlib.md5(
                    f"{job.get('title','').lower().strip()}|{job.get('company','').lower().strip()}".encode()
                ).hexdigest()
                if fp in seen_fingerprints:
                    continue
                seen_fingerprints.add(fp)
                job["_fingerprint"] = fp
                job["_scraped_at"]  = datetime.datetime.utcnow().isoformat() + "Z"
                with open(raw_ndjson_path, "a") as f:
                    f.write(json.dumps(job) + "\n")
                all_jobs.append(job)

    print(f"  ✅ {len(all_jobs)} unique jobs scraped (raw → {raw_ndjson_path})")
    return all_jobs


# ══════════════════════════════════════════════════════════════════
# PHASE 3 — PURE-PYTHON PRE-FILTER
# ══════════════════════════════════════════════════════════════════

def parse_age_days(posted_date: str) -> Optional[int]:
    if not posted_date:
        return None
    txt = posted_date.lower().strip()
    m = re.match(r"(\d+)\s*(hour|day|week|month|year)", txt)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "hour":  return 0
        if unit == "day":   return n
        if unit == "week":  return n * 7
        if unit == "month": return n * 30
        if unit == "year":  return n * 365
    iso_formats = [(20, "%Y-%m-%dT%H:%M:%SZ"), (19, "%Y-%m-%dT%H:%M:%S"), (10, "%Y-%m-%d")]
    for slen, fmt in iso_formats:
        try:
            dt = datetime.datetime.strptime(posted_date[:slen].replace("Z",""), fmt.replace("Z",""))
            return max((datetime.datetime.utcnow() - dt).days, 0)
        except ValueError:
            continue
    if any(w in txt for w in ["just", "today", "now", "moment"]):
        return 0
    return None


def prefilter(jobs: List[dict], cross_run_seen: dict) -> tuple[List[dict], List[dict]]:
    print(f"\n🔬 PHASE 3 — Pre-filter ({len(jobs)} raw, {len(cross_run_seen)} cross-run known)...")
    candidates: List[dict] = []
    rejected:   List[dict] = []
    session_seen: Set[str] = set()
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    for job in jobs:
        title        = (job.get("title") or "").strip()
        title_l      = title.lower()
        company      = (job.get("company") or "").lower()
        experience   = (job.get("experience_text") or "").lower()
        description  = (job.get("description_snippet") or "").lower()
        location     = (job.get("location_text") or "").lower()
        site         = (job.get("site") or "").lower().strip()
        fp           = job.get("_fingerprint", "")

        def reject(reason: str):
            job["rejection_reason"] = reason
            rejected.append(job)

        # 0. SITE DOMAIN ALLOWLIST — drop jobs from garbage sites immediately
        #    This kills apna.co (cricket coaches), talent.com (BPO/sales),
        #    fresheroffcampus, qureos, simplyhired, glassdoor, instagram etc.
        if site and not any(site.endswith(allowed) or site == allowed
                           for allowed in SITE_ALLOWLIST):
            reject(f"Not-allowlisted site: {site}"); continue

        # 1. Cross-run dedup
        if fp and fp in cross_run_seen:
            reject(f"Already seen ({cross_run_seen[fp][:10]})"); continue

        # 2. Session dedup
        if fp and fp in session_seen:
            reject("Duplicate within run"); continue
        if fp:
            session_seen.add(fp)

        # 3. Freshness — 3-day window
        age = parse_age_days(job.get("posted_date", ""))
        if age is None:
            job["freshness_unknown"] = True
        elif age > MAX_POSTING_AGE_DAYS:
            reject(f"Stale: {age}d ago (max {MAX_POSTING_AGE_DAYS}d)"); continue

        # 4. Title hard-reject
        if TITLE_REJECT_PATTERNS.search(title):
            reject(f"Off-stack title: {title}"); continue

        # 5. AI-relevance gate — prevents non-AI jobs from reaching Phase 4 LLM.
        #    Checks BOTH title AND description. If neither contains an AI keyword, reject.
        #    This catches: JioStar sport interns, corporate finance interns, AWS infra,
        #    MERN devs with description, etc. that pass the TITLE_REJECT regex.
        combined_ai_text = f"{title} {description}"
        if not AI_RELEVANCE_KEYWORDS.search(combined_ai_text):
            reject(f"No AI relevance in title+desc: {title[:60]}"); continue

        # 6. Recruiter / staffing spam
        if RECRUITER_PATTERN.search(company):
            reject(f"Recruiter/staffing: {company}"); continue

        # 7. Education filter
        edu_text = f"{experience} {description}"
        if any(tok in edu_text for tok in EDUCATION_REJECT_TOKENS):
            reject("Requires advanced degree"); continue

        # 8. Experience — seniority in TITLE only; years in experience_text only
        if EXPERIENCE_TITLE_REJECT.search(title):
            reject(f"Senior/lead title: {title}"); continue
        if any(tok in experience for tok in EXPERIENCE_YEARS_REJECT):
            reject(f"Too many YOE: {experience}"); continue

        # 9. Geo-lock
        geo_text = f"{location} {description}"
        if any(tok in geo_text for tok in GEO_LOCK_TOKENS):
            reject("Geo-locked to US/UK/EU"); continue

        # Mark as seen
        if fp:
            cross_run_seen[fp] = now_iso

        candidates.append(job)

    print(f"  ✅ {len(candidates)} candidates | ❌ {len(rejected)} rejected")
    return candidates, rejected


# ══════════════════════════════════════════════════════════════════
# PHASE 4 — DEEPSEEK V3 EVALUATION + COVER LETTER
# ══════════════════════════════════════════════════════════════════

EVAL_SYSTEM = """You are {name}'s autonomous India AI job matching agent.

Candidate stack: {stack}
Key metrics: {metrics}

RULES:
- Target: well-funded India startups (Series A+), top MNCs, or high-growth AI companies.
- The role must be geographically accessible in India (remote India, hybrid India, onsite India).
- Reject: relocation to US/Europe, unpaid internships, pure data science (no LLM/agent work), DevOps-only, sales, roles entirely unrelated to AI/LLM/Python.
- IMPORTANT: If description_snippet is empty but title and company clearly indicate an AI engineering role, set is_match=true with a best-effort match_score of 60 and note "description unavailable".
- Candidate has 1 YOE but production-scale achievements — do NOT reject purely due to YOE if stack fits.

For each job:
1. is_match=true only if role matches AI Voice/LLM/RAG/FastAPI stack AND is India-accessible.
2. If is_match=true:
   - match_score (0-100): reward Voice AI, LLM optimization, RAG, LiveKit, production backend scale.
   - drafted_proposal: tight 3-paragraph technical cover letter.
     Para 1: address the company's specific AI goal with a production achievement.
     Para 2: stack fit + tools (LiveKit, CrewAI, FastAPI, Pinecone, LangChain).
     Para 3: one concrete metric + offer to interview.
3. If is_match=false: rejection_reason (one sentence).

{format_instructions}
"""

FORMAT_INSTRUCTIONS = """
Return ONLY valid JSON (no markdown fences):
{"evaluated_jobs":[{"is_match":true/false,"job_title":"string","company":"string","application_url":"string","match_score":0-100,"rejection_reason":"string or null","drafted_proposal":"string or null"}]}
"""


def evaluate_and_draft(candidates: List[dict]) -> str:
    if not candidates:
        return json.dumps({"evaluated_jobs": []}, indent=2)

    print(f"\n🧠 PHASE 4 — DeepSeek V3 evaluating {len(candidates)} candidates...")
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    system_prompt = EVAL_SYSTEM.format(
        name=CANDIDATE_PROFILE["name"],
        stack=CANDIDATE_PROFILE["stack"],
        metrics=CANDIDATE_PROFILE["metrics"],
        format_instructions=FORMAT_INSTRUCTIONS,
    )

    def call_deepseek(batch: List[dict], batch_num: int, total: int) -> List[dict]:
        print(f"  📦 Batch {batch_num}/{total} ({len(batch)} jobs)...")
        text = ""
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                max_tokens=14000,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Jobs:\n{json.dumps(batch, indent=2)}"},
                ],
                extra_body={"thinking": {"type": "enabled"}},
            )
            reasoning = getattr(resp.choices[0].message, "reasoning_content", None)
            if reasoning:
                lines = reasoning.strip().splitlines()
                print(f"\n  💭 Thinking Chain (batch {batch_num}, {len(lines)} lines):")
                print("  " + "-"*52)
                for line in lines[:25]: print(f"  {line}")
                if len(lines) > 25: print(f"  ... ({len(lines)-25} more)")
                print("  " + "-"*52 + "\n")

            text = resp.choices[0].message.content or ""
            if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:   text = text.split("```")[1].split("```")[0].strip()
            return json.loads(text).get("evaluated_jobs", [])
        except Exception as e:
            print(f"  ⚠️ Batch {batch_num} error: {e}")
            if text: print("  📄 Raw:\n", text[:500])
            return []

    BATCH_SIZE = 10
    batches = [candidates[i:i+BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]
    all_evaluated: List[dict] = []
    for idx, batch in enumerate(batches, 1):
        results = call_deepseek(batch, idx, len(batches))
        all_evaluated.extend(results)
        hits = sum(1 for j in results if j.get("is_match"))
        print(f"  ✅ Batch {idx}/{len(batches)} — {hits}/{len(results)} matched, total: {len(all_evaluated)}")

    return json.dumps({"evaluated_jobs": all_evaluated}, indent=2)


# ══════════════════════════════════════════════════════════════════
# DRY-RUN MOCK DATA
# ══════════════════════════════════════════════════════════════════

MOCK_JOBS = [
    {
        "title": "LLM Engineer", "company": "Sarvam AI",
        "url": "https://sarvam.ai/careers/llm-engineer",
        "site": "sarvam.ai", "posted_date": "3 hours ago",
        "location_text": "Bangalore, India (Hybrid)", "is_remote": False, "job_type": "full-time",
        "pay_text": "₹30-50 LPA", "experience_text": "0-2 years",
        "description_snippet": "Build production LLM pipelines using LangChain & FastAPI for Indic language AI.",
    },
    {
        "title": "Senior Data Scientist", "company": "Analytics Firm",
        "url": "https://naukri.com/jobs/123", "site": "naukri.com",
        "posted_date": "5 days ago", "location_text": "Mumbai",
        "is_remote": False, "job_type": "full-time",
        "pay_text": "₹15-20 LPA", "experience_text": "4+ years",
        "description_snippet": "Statistical modeling and A/B testing.",
    },
    {
        "title": "AI Engineer — Voice & Agents", "company": "Razorpay",
        "url": "https://razorpay.com/careers/ai-engineer-voice",
        "site": "razorpay.com", "posted_date": "1 hour ago",
        "location_text": "Bangalore / Remote India", "is_remote": True, "job_type": "full-time",
        "pay_text": "₹40-60 LPA", "experience_text": "1-2 years",
        "description_snippet": "We mention senior engineers in our team. Build voice AI agents using LiveKit and FastAPI for fintech automation. This is a junior role.",
    },
    {
        "title": "Gen AI Developer", "company": "ProAI Solutions",
        "url": "https://foundit.in/job/gen-ai-12345",
        "site": "foundit.in", "posted_date": "2 days ago",
        "location_text": "Bengaluru", "is_remote": False, "job_type": "full-time",
        "pay_text": "", "experience_text": "Fresher",
        "description_snippet": "Design and optimise GenAI features: RAG workflows, LangChain stacks, Pinecone vector stores. Python, FastAPI REST APIs.",
    },
    {
        "title": "Medical Writer", "company": "Syneos Health",
        "url": "https://foundit.in/job/medical-789",
        "site": "foundit.in", "posted_date": "1 hour ago",
        "location_text": "Remote", "is_remote": True, "job_type": "full-time",
        "pay_text": "", "experience_text": "3 years",
        "description_snippet": "Write clinical study reports and regulatory documents.",
    },
]


# ══════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════

async def main(dry_run: bool = False):
    timestamp    = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    reports_dir  = os.path.join(os.path.dirname(__file__), "reports_india_mnc")
    os.makedirs(reports_dir, exist_ok=True)

    raw_ndjson   = os.path.join(reports_dir, f"raw_india_mnc_{timestamp}.ndjson")
    rejected_out = os.path.join(reports_dir, f"rejected_india_mnc_{timestamp}.json")
    report_out   = os.path.join(reports_dir, f"report_india_mnc_{timestamp}.json")

    print(f"\n{'='*60}")
    print(f"🚀 INDIA MNC JOB SEARCH v4  {'[DRY RUN]' if dry_run else '[LIVE — 3-day window]'}")
    print(f"{'='*60}")

    cross_run_seen = load_seen_fingerprints()
    print(f"  📦 Cross-run cache: {len(cross_run_seen)} fingerprints")

    if dry_run:
        print("\n[DRY RUN] Using mock data")
        raw_jobs = MOCK_JOBS
        for j in raw_jobs:
            if "_fingerprint" not in j:
                j["_fingerprint"] = hashlib.md5(f"{j['title'].lower()}|{j['company'].lower()}".encode()).hexdigest()
                j["_scraped_at"]  = datetime.datetime.utcnow().isoformat() + "Z"
    else:
        urls = search_for_jobs()
        if not urls:
            print("No URLs found. Exiting."); return
        raw_jobs = await scrape_jobs(urls, raw_ndjson)

    candidates, rejected = prefilter(raw_jobs, cross_run_seen)

    with open(rejected_out, "w") as f:
        json.dump(rejected, f, indent=2)
    print(f"  💾 Rejected → {rejected_out}")

    if not dry_run:
        save_seen_fingerprints(cross_run_seen)

    if dry_run:
        print("\n[DRY RUN] Skipping DeepSeek evaluation")
        final_json = json.dumps({"dry_run": True, "candidates_passed_prefilter": len(candidates), "candidates": candidates}, indent=2)
    else:
        final_json = evaluate_and_draft(candidates)

    with open(report_out, "w") as f:
        f.write(final_json)

    print(f"\n{'='*60}\nFINAL REPORT\n{'='*60}")
    print(final_json[:3000] + ("\n... (truncated)" if len(final_json) > 3000 else ""))
    print(f"\n💾 Report → {report_out}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry_run))
