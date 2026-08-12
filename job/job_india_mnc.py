"""
Autonomous India MNC Job Search Agent — v12
============================================
Phase 0: Company source — ATS-direct fetch (full JD) + Serper-careers, from the
         593-company India registry (93 ats: + 500 serper:, job/companies.py,
         mode="india"). Serper-careers now searches each company's OWN careers
         page (with -site: negatives for linkedin/naukri/glassdoor/ambitionbox/
         indeed/wellfound — those are covered by Phase 1's board search
         instead) and takes an ATS-URL shortcut when a hit is itself a
         Greenhouse/Lever/Ashby/Workable per-job link (full JD, no crawl).
Phase 1: Serper.dev multi-cluster search — per-site AND broad free-text
         + direct LinkedIn Jobs URL injection
         (qdr:w = last week in Serper, Phase 3 enforces 3-day freshness)
Phase 2: Crawl4AI markdown-only scrape (v12 — NO LLMExtractionStrategy; v10/v11
         fired >=1 DeepSeek call per URL BEFORE any relevance check, wasting
         >90% of LLM spend on pages that were off-stack/senior/foreign-onsite
         anyway). Every crawled page is hard-filtered against
         job/requirements.py's deterministic gates on its raw markdown, for $0,
         BEFORE it ever becomes a candidate — only survivors proceed.
Phase 3: Deterministic pre-filter (job/requirements.py's shared gates — years of
         experience read from the FULL description, not just experience_text;
         profile-driven education ceiling; expanded seniority regex; geo_ok's
         India-or-worldwide-remote policy, which also catches ATS/career jobs
         that are onsite in a country that isn't India)
Phase 4: DeepSeek V3 evaluation + cover letter drafting — the ONLY LLM call in
         the whole pipeline now; also does the structured extraction (title/
         company) for markdown-sourced candidates that arrive with those fields
         blank, in the same pass.

Run:
  python job/job_india_mnc.py           # full run — prompts for company count
  python job/job_india_mnc.py --companies 20
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

warnings.filterwarnings("ignore", message="urllib3 .* doesn't match a supported version!")

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from openai import OpenAI

sys.path.insert(0, os.path.dirname(__file__))
try:
    import companies
except ImportError as e:
    companies = None
    print(f"  ⚠️  job/companies.py unavailable ({e}) — the India ATS company "
          f"source is skipped this run. `pip install -r requirements.txt` to enable it.")

import candidate_profile as prof
import requirements as req

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
# NOTE: foundit.in REMOVED (v11) — user-reported it only returned stale (month-old)
#       jobs. Its removal is intentionally not backfilled with another aggregator;
#       v11 leans harder on ATS-direct/career-page sourcing instead (Phase 0 below).
TARGET_SITES = [
    "naukri.com",
    "linkedin.com",
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
    "wellfound.com", "instahyre.com",
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
            '-"data analyst" -"business analyst" -"medical" '
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
            '-"data engineer" -"DevOps" -"SRE" '
            '-site:apna.co -site:talent.com -site:qureos.com -site:glassdoor.co.in '
            '-site:fresheroffcampus.com -site:simplyhired.co.in'
        ),
        "num": 20,
        "sites": ["_broad_"],
        "broad": True,
    },
    {
        "name": "D2 — Data Scientist / Forward Deployed Engineer India [broad]",
        "terms": (
            '("data scientist" OR "applied scientist" OR "forward deployed engineer") '
            'India ("0-2 years" OR "junior" OR "fresher" OR "entry level") -senior -lead '
            '-site:apna.co -site:talent.com -site:qureos.com -site:glassdoor.co.in'
        ),
        "num": 20,
        "sites": ["_broad_"],
        "broad": True,
    },
]

# Candidate profile — generalized in v11 (see job/candidate_profile.py). Previously a
# hardcoded dict duplicated (and already drifted — this one alone mentioned
# Groq) across all three job_*.py scripts. Run `python job/init_profile.py`
# once to personalize; a missing/partial config/profile.yaml degrades to the
# original tool's own defaults.
PROFILE = prof.load_profile()
HOME_PATTERN = req.build_home_pattern(PROFILE)

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
    r"data engineer(?!.*ai)|mlops|"
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

# Years/seniority/education/AI-relevance/geo gates now come from job/requirements.py
# (shared across all three scripts — v10 had three independent, already-drifted
# copies; only job_remote.py's seniority regex had "staff engineer", only this
# file's years-list had "3+ years", etc). MAX_POSTING_AGE_DAYS unchanged.
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
# PHASE 2 — CRAWL4AI MARKDOWN SCRAPE + DETERMINISTIC HARD-FILTER (v12)
# ══════════════════════════════════════════════════════════════════
# v10/v11 used LLMExtractionStrategy here — >=1 DeepSeek call PER URL to
# structure the page into a job dict, BEFORE any relevance check. For ~200+
# URLs/run that's >90% of LLM spend wasted on off-stack/senior/foreign-onsite
# pages. companies.scrape_markdown() reads Crawl4AI's raw markdown (zero
# DeepSeek); companies.page_passes_hardfilter() then applies the SAME
# deterministic gates job/requirements.py uses on structured jobs, directly
# against that markdown. A page that fails never becomes a candidate — it's
# recorded with its reason and never reaches Phase 3/4. Survivors carry an
# empty title/company (unknown until the LLM reads the page in Phase 4) and a
# URL-based fingerprint (title|company would collide across every survivor —
# they're all "" at this point).

async def scrape_and_hardfilter(url_sources: dict, raw_ndjson_path: str) -> tuple:
    """`url_sources` is {url: "careers"|"board"}. Returns (jobs, hardfilter_rejected).

    v15 — fail-proof + junk-free:
      1. Every URL passes companies.is_crawlable_job_url() BEFORE a browser
         touches it — kills YouTube/Instagram/Facebook/Reddit/blog/about/
         privacy/PDF junk that flooded (and hung) a real run's crawl queue.
      2. Each scraped page is STREAM-WRITTEN to raw_ndjson the instant it
         resolves (companies.scrape_markdown's on_result callback) — so if the
         process is killed mid-crawl, everything scraped so far is already on
         disk. A --resume run reads those logs and continues, never re-crawling
         or (crucially) re-spending Serper.
      3. URLs already present in raw_ndjson (a prior interrupted run) are
         skipped — only the remainder is crawled."""
    raw_urls = list(url_sources.keys())
    urls = [u for u in raw_urls if companies.is_crawlable_job_url(u)]
    junk = len(raw_urls) - len(urls)

    already = _scraped_urls_on_disk(raw_ndjson_path)
    todo = [u for u in urls if u not in already]
    print(f"\n🕷️  PHASE 2 — Crawl4AI markdown scrape (no LLM)")
    print(f"    {len(raw_urls)} discovered → {junk} junk dropped pre-crawl → "
          f"{len(already)} already scraped (resume) → {len(todo)} to crawl now")

    # Stream each scraped page to disk AS it completes — the fail-proof core.
    def _persist(url, md):
        rec = {
            "title": "", "company": "", "url": url, "site": _domain(url),
            "posted_date": "", "location_text": "", "is_remote": None, "job_type": "",
            "pay_text": "", "experience_text": "", "description": md,
            "_fingerprint": hashlib.md5(url.encode()).hexdigest(),
            "_scraped_at": datetime.datetime.utcnow().isoformat() + "Z",
            "source": url_sources.get(url, "board"), "_kind": "scraped",
        }
        with open(raw_ndjson_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    if todo:
        await companies.scrape_markdown(todo, on_result=_persist)

    # Hard-filter reads back EVERY scraped page from raw_ndjson (this run's +
    # any from a resumed prior run) — deterministic and free, so re-running it
    # over the full log on resume costs nothing.
    jobs: List[dict] = []
    hardfilter_rejected: List[dict] = []
    for rec in _load_scraped_records(raw_ndjson_path):
        md = rec.get("description") or ""
        source = rec.get("source", "board")
        ok, reason = companies.page_passes_hardfilter(md, PROFILE, HOME_PATTERN)
        if ok:
            jobs.append(rec)
        else:
            hardfilter_rejected.append({"url": rec.get("url"), "source": source,
                                        "rejection_reason": f"hardfilter: {reason}"})

    print(f"  🔬 hard-filter: {len(jobs)} survived for $0, "
          f"{len(hardfilter_rejected)} killed before any LLM call")
    return jobs, hardfilter_rejected


def _scraped_urls_on_disk(raw_ndjson_path: str) -> set:
    """URLs already crawled in a prior (possibly interrupted) run — read from
    the streamed raw_ndjson so --resume never re-crawls them."""
    out = set()
    if not os.path.exists(raw_ndjson_path):
        return out
    try:
        with open(raw_ndjson_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("_kind") == "scraped" and rec.get("url"):
                    out.add(rec["url"])
    except Exception:
        pass
    return out


def _load_scraped_records(raw_ndjson_path: str) -> List[dict]:
    """All streamed scraped-page records from raw_ndjson (this run + resumed)."""
    out = []
    if not os.path.exists(raw_ndjson_path):
        return out
    try:
        with open(raw_ndjson_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("_kind") == "scraped":
                    out.append(rec)
    except Exception:
        pass
    return out


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url or "").netloc
    except Exception:
        return ""


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
        company      = (job.get("company") or "").lower()
        experience   = job.get("experience_text") or ""
        description  = job.get("description") or ""
        location     = job.get("location_text") or ""
        site         = (job.get("site") or "").lower().strip()
        fp           = job.get("_fingerprint", "")

        def reject(reason: str):
            job["rejection_reason"] = reason
            rejected.append(job)

        # 0. SITE DOMAIN ALLOWLIST — drop jobs from garbage sites immediately
        #    This kills apna.co (cricket coaches), talent.com (BPO/sales),
        #    fresheroffcampus, qureos, simplyhired, glassdoor, instagram etc.
        #    Skipped for ATS-direct jobs (companies.py) — those never had a
        #    scraped `site` guessed from a search result in the first place, and
        #    are already trusted (they came from a verified company ATS token).
        #    v12: ALSO skipped for source="careers" jobs — companies.
        #    serper_careers_urls() already host-filters those (ATS host or the
        #    company's own domain) before they're ever returned, so a second,
        #    fixed-list allowlist check here would just reject legitimate
        #    career-page hits from the ~500 companies that were never going to
        #    be on this hardcoded list — defeating the entire point of sourcing
        #    from them. "board" jobs (QUERY_CLUSTERS/DIRECT_URLS) still need it
        #    as a safety net against garbage aggregators.
        #    FIXED (v11): bare `site.endswith(allowed)` with no dot separator let
        #    "evilnaukri.com" or "fake-linkedin.com" pass — endswith("naukri.com")
        #    is true for either. Must require a dot (or exact match) before the
        #    allowed suffix, matching job_freelance.py's already-correct pattern.
        if (site and job.get("_source") != "ats_direct" and job.get("source") != "careers"
                and not any(site == allowed or site.endswith("." + allowed) for allowed in SITE_ALLOWLIST)):
            reject(f"Not-allowlisted site: {site}"); continue

        # 1. Cross-run dedup
        if fp and fp in cross_run_seen:
            reject(f"Already seen ({cross_run_seen[fp][:10]})"); continue

        # 2. Session dedup
        if fp and fp in session_seen:
            reject("Duplicate within run"); continue
        if fp:
            session_seen.add(fp)

        # 3. Freshness — 3-day window. Skipped for ATS-direct jobs (companies.py):
        # a Greenhouse/Lever/Ashby board lists every CURRENTLY OPEN role regardless
        # of its original post date — a live posting from 3 weeks ago is still a
        # real, applicable job, not a stale one (same fix as job_remote.py).
        if job.get("_source") != "ats_direct":
            age = parse_age_days(job.get("posted_date", ""))
            if age is None:
                job["freshness_unknown"] = True
            elif age > MAX_POSTING_AGE_DAYS:
                reject(f"Stale: {age}d ago (max {MAX_POSTING_AGE_DAYS}d)"); continue

        # 4. Title hard-reject
        if TITLE_REJECT_PATTERNS.search(title):
            reject(f"Off-stack title: {title}"); continue

        # 5. AI-relevance gate (shared, job/requirements.py) — prevents non-AI
        #    jobs from reaching Phase 4 LLM. Catches JioStar sport interns,
        #    corporate finance interns, AWS infra, etc. that pass TITLE_REJECT.
        if not req.is_ai_relevant(title, description):
            reject(f"No AI relevance in title+desc: {title[:60]}"); continue

        # 6. Recruiter / staffing spam
        if RECRUITER_PATTERN.search(company):
            reject(f"Recruiter/staffing: {company}"); continue

        # 7. Education filter — profile-driven ceiling (job/requirements.py),
        # not a fixed "bachelor's only" assumption.
        if not req.education_ok(PROFILE, f"{experience} {description}"):
            reject("Requires more education than profile's education_ceiling"); continue

        # 8. Experience — seniority regex expanded (catches "Staff Engineer,"
        # "Sr. ML Engineer," "Engineer III," "Member of Technical Staff" that the
        # old narrower per-file regex missed); years now read from the FULL
        # description too, not just experience_text (which no ATS adapter ever
        # populates — this was the core accuracy bug fixed in v11).
        seniority = req.classify_seniority(title)
        if seniority:
            reject(seniority); continue
        yoe_ok, yoe_detail = req.experience_ok(experience, description,
                                               PROFILE["years_experience"], PROFILE["yoe_slack"])
        if not yoe_ok:
            reject(yoe_detail); continue

        # 9. Geo — job/requirements.py's geo_ok() (v12): accept India-accessible
        # (remote/hybrid/onsite India) OR worldwide/anywhere-remote; reject
        # confident foreign-lock OR foreign onsite/hybrid (a real place, not
        # home, with no remote signal anywhere — this is the mechanism that
        # actually catches an ATS job from a global board whose location_text
        # names a specific non-India city, e.g. "Austin, TX" + is_remote=False;
        # the old pre_kill_location left that ambiguous and let it through).
        # Markdown-sourced candidates (location_text="") can never hit that
        # foreign-onsite branch — they were already hard-filtered on their raw
        # page text in Phase 2, and stay ambiguous here by design (LLM decides).
        geo = req.geo_ok(location, description, PROFILE, HOME_PATTERN,
                         is_remote=job.get("is_remote"), job_type=job.get("job_type", ""))
        if geo is False:
            reject("geo: confident foreign-lock or foreign onsite (no remote/India signal)"); continue

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
Target roles: {target_roles}

RULES:
- Target: well-funded India startups (Series A+), top MNCs, or high-growth AI companies.
- GEO (read the full description, not just location_text — some candidates have no
  separate location field at all): accept iff the role is India-accessible (remote
  India, hybrid India, onsite India) OR genuinely worldwide/anywhere-remote. REJECT a
  role that is onsite/hybrid in a specific country that isn't India, or remote but
  scoped to a specific country/region other than India (e.g. "Remote, United States",
  "US-based", "EU residents only") with no worldwide language anywhere.
- Experience: candidate has {years_experience} YOE (+{yoe_slack} slack). REJECT any role
  that requires more than {years_experience_cap} years, or is titled
  Senior/Lead/Principal/Staff/Manager/Director — even if the stack fits well.
- Accept AI/ML Engineer, LLM/RAG Engineer, Applied/Data Scientist (AI/ML-focused —
  modeling, LLMs, production ML pipelines — NOT pure BI/reporting/analytics), and
  Forward Deployed Engineer roles as in-scope matches.
- Reject: relocation to US/Europe, unpaid internships, DevOps-only, sales, roles
  entirely unrelated to AI/ML/LLM/Python/data science.
- Some candidates have title="" and company="" (their job_title/company weren't known
  before this call) — for those, read the description field itself (it's the FULL
  page text, e.g. a company careers page or job board listing) and extract the real
  job title and company name into job_title/company. Always set application_url to
  that candidate's own `url` field, never a generic company homepage. If a candidate's
  description is empty or too thin to tell what the role even is, reject it
  (rejection_reason: "insufficient information") — do NOT guess a match from title
  and company alone.

For each job:
1. is_match=true only if role matches the target roles AND is India-accessible AND fits the experience bracket.
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


def evaluate_and_draft(candidates: List[dict], eval_cache_path: str = None) -> str:
    if not candidates:
        return json.dumps({"evaluated_jobs": []}, indent=2)

    # v15 — DeepSeek resume. Each evaluated batch is appended to eval_cache_path
    # (ndjson) as it returns, keyed by the candidate's _fingerprint. On a
    # --resume run, candidates already in the cache are NOT re-sent to DeepSeek —
    # so an interrupted eval never re-spends on jobs it already paid to score.
    done_fps, cached_results = set(), []
    if eval_cache_path and os.path.exists(eval_cache_path):
        try:
            with open(eval_cache_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    fp = rec.get("_fingerprint")
                    if fp:
                        done_fps.add(fp)
                    cached_results.append({k: v for k, v in rec.items() if k != "_fingerprint"})
        except Exception:
            done_fps, cached_results = set(), []
    remaining = [c for c in candidates if c.get("_fingerprint") not in done_fps]
    if done_fps:
        print(f"  ♻️  resume: {len(done_fps)} candidates already evaluated (cached), "
              f"{len(remaining)} left to score")

    if not remaining:
        return json.dumps({"evaluated_jobs": cached_results}, indent=2)

    print(f"\n🧠 PHASE 4 — DeepSeek V3 evaluating {len(remaining)} candidates...")
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    system_prompt = EVAL_SYSTEM.format(
        name=PROFILE["name"],
        stack=PROFILE["stack"],
        metrics=PROFILE["metrics"],
        target_roles=", ".join(PROFILE["target_role_families"]),
        years_experience=PROFILE["years_experience"],
        yoe_slack=PROFILE["yoe_slack"],
        years_experience_cap=PROFILE["years_experience"] + PROFILE["yoe_slack"],
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
    batches = [remaining[i:i+BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    all_evaluated: List[dict] = list(cached_results)
    for idx, batch in enumerate(batches, 1):
        results = call_deepseek(batch, idx, len(batches))
        all_evaluated.extend(results)
        # Persist this batch immediately so a mid-eval kill doesn't lose (or
        # force a re-pay for) the batches already scored. Tag each result with
        # its candidate fingerprint (best-effort match by application_url).
        if eval_cache_path:
            url_to_fp = {c.get("url"): c.get("_fingerprint") for c in batch}
            try:
                with open(eval_cache_path, "a") as f:
                    for r in results:
                        rec = dict(r)
                        rec["_fingerprint"] = url_to_fp.get(r.get("application_url"))
                        f.write(json.dumps(rec, default=str) + "\n")
            except Exception as e:
                print(f"  ⚠️ Could not persist eval batch {idx}: {e}")
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
        "description": "Build production LLM pipelines using LangChain & FastAPI for Indic language AI.",
    },
    {
        "title": "Senior Data Scientist", "company": "Analytics Firm",
        "url": "https://naukri.com/jobs/123", "site": "naukri.com",
        "posted_date": "5 days ago", "location_text": "Mumbai",
        "is_remote": False, "job_type": "full-time",
        "pay_text": "₹15-20 LPA", "experience_text": "4+ years",
        "description": "Statistical modeling and A/B testing.",
    },
    {
        "title": "AI Engineer — Voice & Agents", "company": "Razorpay",
        "url": "https://razorpay.com/careers/ai-engineer-voice",
        "site": "razorpay.com", "posted_date": "1 hour ago",
        "location_text": "Bangalore / Remote India", "is_remote": True, "job_type": "full-time",
        "pay_text": "₹40-60 LPA", "experience_text": "1-2 years",
        "description": "We mention senior engineers in our team. Build voice AI agents using LiveKit and FastAPI for fintech automation. This is a junior role.",
    },
    {
        "title": "Gen AI Developer", "company": "ProAI Solutions",
        "url": "https://instahyre.com/job/gen-ai-12345",
        "site": "instahyre.com", "posted_date": "2 days ago",
        "location_text": "Bengaluru", "is_remote": False, "job_type": "full-time",
        "pay_text": "", "experience_text": "Fresher",
        "description": "Design and optimise GenAI features: RAG workflows, LangChain stacks, Pinecone vector stores. Python, FastAPI REST APIs.",
    },
    {
        "title": "Medical Writer", "company": "Syneos Health",
        "url": "https://naukri.com/job/medical-789",
        "site": "naukri.com", "posted_date": "1 hour ago",
        "location_text": "Remote", "is_remote": True, "job_type": "full-time",
        "pay_text": "", "experience_text": "3 years",
        "description": "Write clinical study reports and regulatory documents.",
    },
    {
        "title": "Data Scientist", "company": "Groww",
        "url": "https://groww.in/careers/data-scientist",
        "site": "groww.in", "posted_date": "4 hours ago",
        "location_text": "Bengaluru, India (Onsite)", "is_remote": False, "job_type": "full-time",
        "pay_text": "₹20-28 LPA", "experience_text": "1-2 years",
        "description": "Build ML models for credit risk and fraud detection using PyTorch and LightGBM.",
    },
    {
        "title": "Forward Deployed Engineer", "company": "Uniphore",
        "url": "https://uniphore.com/careers/fde-101",
        "site": "uniphore.com", "posted_date": "6 hours ago",
        "location_text": "Bengaluru, India (Hybrid)", "is_remote": False, "job_type": "full-time",
        "pay_text": "₹22-30 LPA", "experience_text": "0-2 years",
        "description": "Embed with enterprise customers to deploy LLM-powered conversational AI workflows.",
    },
]


# ══════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════

def _companies_arg() -> Optional[int]:
    """--companies N on the command line skips the interactive prompt (for cron/CI)."""
    if "--companies" in sys.argv:
        i = sys.argv.index("--companies")
        if i + 1 < len(sys.argv):
            try: return int(sys.argv[i + 1])
            except ValueError: pass
    return None


# ── v15 checkpoint/resume ─────────────────────────────────────────────
# The expensive/irrecoverable step is DISCOVERY (Serper credits) and CRAWL
# (time / hang-prone). A checkpoint written right after discovery records the
# URL list + artifact paths so a --resume run SKIPS discovery entirely (spends
# ZERO new Serper) and continues from the streamed logs. See scrape_and_
# hardfilter for the crawl-side resume (already-scraped URLs skipped) and
# evaluate_and_draft for the DeepSeek-side resume (already-scored candidates
# skipped).
_CHECKPOINT_PATH = os.path.join(os.path.dirname(__file__), "reports_india_mnc",
                                ".checkpoint_india_mnc.json")


def _save_checkpoint(state: dict):
    try:
        os.makedirs(os.path.dirname(_CHECKPOINT_PATH), exist_ok=True)
        with open(_CHECKPOINT_PATH, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"  ⚠️ Could not write checkpoint: {e}")


def _load_checkpoint() -> Optional[dict]:
    if not os.path.exists(_CHECKPOINT_PATH):
        return None
    try:
        with open(_CHECKPOINT_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def _clear_checkpoint():
    try:
        if os.path.exists(_CHECKPOINT_PATH):
            os.remove(_CHECKPOINT_PATH)
    except Exception:
        pass


def _load_structured_from_raw(raw_ndjson_path: str) -> List[dict]:
    """Reload the ATS-direct / careers-direct (full-JD) jobs a prior run
    persisted to raw_ndjson (tagged _kind='structured'), so --resume gets them
    back without re-fetching from the ATS APIs."""
    out = []
    if not os.path.exists(raw_ndjson_path):
        return out
    try:
        with open(raw_ndjson_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("_kind") == "structured":
                    out.append(rec)
    except Exception:
        pass
    return out


async def main(dry_run: bool = False, resume: bool = False):
    reports_dir  = os.path.join(os.path.dirname(__file__), "reports_india_mnc")
    os.makedirs(reports_dir, exist_ok=True)

    print(f"\n{'='*60}")
    mode_tag = "[DRY RUN]" if dry_run else ("[RESUME]" if resume else "[LIVE — 3-day window]")
    print(f"🚀 INDIA MNC JOB SEARCH v15  {mode_tag}")
    print(f"👤 Profile: {PROFILE['name']} | {PROFILE['years_experience']} YOE (+{PROFILE['yoe_slack']} slack) "
          f"| roles: {', '.join(PROFILE['target_role_families'])}")
    print(f"{'='*60}")

    cross_run_seen = load_seen_fingerprints()
    print(f"  📦 Cross-run cache: {len(cross_run_seen)} fingerprints")

    company_manifest = []
    pool_total = pool_remaining = None
    hardfilter_rejected: List[dict] = []
    eval_cache = None

    if dry_run:
        timestamp    = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        raw_ndjson   = os.path.join(reports_dir, f"raw_india_mnc_{timestamp}.ndjson")
        rejected_out = os.path.join(reports_dir, f"rejected_india_mnc_{timestamp}.json")
        report_out   = os.path.join(reports_dir, f"report_india_mnc_{timestamp}.json")
        print("\n[DRY RUN] Using mock data")
        raw_jobs = MOCK_JOBS
        for j in raw_jobs:
            if "_fingerprint" not in j:
                j["_fingerprint"] = hashlib.md5(f"{j['title'].lower()}|{j['company'].lower()}".encode()).hexdigest()
                j["_scraped_at"]  = datetime.datetime.utcnow().isoformat() + "Z"
    elif resume:
        ck = _load_checkpoint()
        if not ck:
            print("\n♻️  --resume: no checkpoint found. Nothing to resume — run without --resume "
                  "to start a fresh scan.")
            return
        raw_ndjson   = ck["raw_ndjson"]
        rejected_out = ck["rejected_out"]
        report_out   = ck["report_out"]
        url_sources  = ck.get("url_sources", {})
        company_manifest = ck.get("company_manifest", [])
        pool_total   = ck.get("pool_total")
        pool_remaining = ck.get("pool_remaining")
        eval_cache   = ck.get("eval_cache")
        structured_jobs = _load_structured_from_raw(raw_ndjson)
        already = _scraped_urls_on_disk(raw_ndjson)
        print(f"\n♻️  RESUMING run {ck.get('timestamp')} — NO new Serper spend.")
        print(f"    {len(url_sources)} discovered URLs · {len(structured_jobs)} structured jobs "
              f"· {len(already)} pages already scraped on disk")
        crawled_jobs, hardfilter_rejected = (
            await scrape_and_hardfilter(url_sources, raw_ndjson) if url_sources else ([], []))
        raw_jobs = crawled_jobs + structured_jobs
    else:
        timestamp    = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        raw_ndjson   = os.path.join(reports_dir, f"raw_india_mnc_{timestamp}.ndjson")
        rejected_out = os.path.join(reports_dir, f"rejected_india_mnc_{timestamp}.json")
        report_out   = os.path.join(reports_dir, f"report_india_mnc_{timestamp}.json")
        eval_cache   = report_out + ".evalcache.ndjson"
        # PHASE 0 — India ATS-direct + Serper-careers company source
        # (job/companies.py, mode="india": its own 593-company registry — 93
        # ats: + 500 serper: — and its own rotation cursor). direct_jobs =
        # ATS-URL-shortcut hits (already full JDs, source="careers") — merged
        # with batch ATS jobs (source="ats") since both bypass Crawl4AI.
        structured_jobs, career_crawl_urls = [], []
        if companies:
            n_companies = _companies_arg()
            if n_companies is None:
                n_companies = companies.prompt_company_count(mode="india")
            pool_total, pool_remaining = companies.pool_status(mode="india")
            if n_companies:
                ats_batch, serper_batch = companies.select_companies(n_companies, mode="india")
                print(f"\n🏢 PHASE 0 — India company source: {len(ats_batch)} ATS-direct + "
                      f"{len(serper_batch)} via Serper-careers ({n_companies} requested, "
                      f"cycle progress before this run: {pool_total - pool_remaining}/{pool_total})")
                if ats_batch:
                    ats_jobs, ats_manifest = companies.fetch_ats_jobs(ats_batch)
                    structured_jobs.extend(ats_jobs)
                    company_manifest.extend(ats_manifest)
                if serper_batch:
                    career_crawl_urls, direct_jobs, serper_manifest = companies.serper_careers_urls(
                        serper_batch, SERPER_API_KEY)
                    structured_jobs.extend(direct_jobs)
                    company_manifest.extend(serper_manifest)
                companies.mark_companies_done(company_manifest, mode="india")
                pool_total, pool_remaining = companies.pool_status(mode="india")
            else:
                print("\n🏢 PHASE 0 — 0 companies requested this run; skipping the company source.")
        else:
            print("\n🏢 PHASE 0 — job/companies.py unavailable; skipping the company source.")

        # PHASE 1 board search + career-page URLs → the crawl queue.
        url_sources = {u: "board" for u in search_for_jobs()}
        url_sources.update({u: "careers" for u in career_crawl_urls})

        # Persist structured jobs to raw_ndjson NOW (tagged _kind='structured'),
        # BEFORE crawling — so they survive an interrupted crawl and --resume
        # gets them back without re-hitting the ATS APIs.
        for j in structured_jobs:
            j["_kind"] = "structured"
        if structured_jobs:
            with open(raw_ndjson, "a") as f:
                for j in structured_jobs:
                    f.write(json.dumps(j, default=str) + "\n")

        # Checkpoint the discovery result (URL list + paths) so a later --resume
        # skips Serper entirely and continues from here.
        _save_checkpoint({
            "timestamp": timestamp, "raw_ndjson": raw_ndjson,
            "rejected_out": rejected_out, "report_out": report_out,
            "url_sources": url_sources, "company_manifest": company_manifest,
            "pool_total": pool_total, "pool_remaining": pool_remaining,
            "eval_cache": eval_cache, "stage": "discovered",
        })

        if not url_sources and not structured_jobs:
            print("No URLs found. Exiting."); return
        crawled_jobs, hardfilter_rejected = (
            await scrape_and_hardfilter(url_sources, raw_ndjson) if url_sources else ([], []))
        raw_jobs = crawled_jobs + structured_jobs

    candidates, rejected = prefilter(raw_jobs, cross_run_seen)
    if not dry_run and hardfilter_rejected:
        rejected = hardfilter_rejected + rejected

    with open(rejected_out, "w") as f:
        json.dump(rejected, f, indent=2, default=str)
    print(f"  💾 Rejected → {rejected_out}")

    if not dry_run:
        save_seen_fingerprints(cross_run_seen)

    if dry_run:
        print("\n[DRY RUN] Skipping DeepSeek evaluation")
        result = {"dry_run": True, "candidates_passed_prefilter": len(candidates), "candidates": candidates}
        final_json = json.dumps(result, indent=2, default=str)
    else:
        final_json = evaluate_and_draft(candidates, eval_cache_path=eval_cache)
        # Splice a run_manifest into the evaluated_jobs report — v10 had no record
        # anywhere of which India companies actually ran; a zero-yield company was
        # invisible.
        try:
            parsed = json.loads(final_json)
            # v14: remote-FIRST ordering. india_mnc accepts India-accessible AND
            # worldwide-remote roles; the user wants the do-it-from-anywhere and
            # remote-India ones at the TOP, above hybrid/onsite India. Nothing is
            # dropped — the report is just sorted by (remote-priority tier asc,
            # match_score desc). Priority is computed deterministically from each
            # CANDIDATE's real geo fields (job/requirements.remote_priority), then
            # joined onto the LLM's report rows by application_url — the LLM never
            # decides the ordering.
            prio_by_url = {}
            for c in candidates:
                if c.get("url"):
                    prio_by_url[c["url"]] = req.remote_priority(
                        c.get("location_text", ""), c.get("description", ""),
                        is_remote=c.get("is_remote"), job_type=c.get("job_type", ""),
                        profile=PROFILE, home_pattern=HOME_PATTERN)
            def _row_sort_key(row):
                tier = prio_by_url.get(row.get("application_url"), req.REMOTE_PRIORITY_OTHER)
                try:
                    score = float(row.get("match_score") or 0)
                except (TypeError, ValueError):
                    score = 0.0
                return (tier, -score)
            evaluated = parsed.get("evaluated_jobs")
            if isinstance(evaluated, list):
                parsed["evaluated_jobs"] = sorted(evaluated, key=_row_sort_key)
            # v12-E: source mix (ats/careers/board) at each funnel stage — shows
            # whether results are actually coming from company career pages/ATS
            # boards now, not just LinkedIn/Naukri board search.
            def _source_mix(jobs):
                counts = {}
                for j in jobs:
                    counts[j.get("source") or "board"] = counts.get(j.get("source") or "board", 0) + 1
                return counts
            parsed["run_manifest"] = {
                "companies_scanned": company_manifest,
                "cycle_progress": (f"{pool_total - pool_remaining}/{pool_total}"
                                   if pool_total is not None else None),
                "funnel": {"raw_jobs": len(raw_jobs), "hardfilter_killed": len(hardfilter_rejected),
                          "prefilter_passed": len(candidates),
                          "reported": len(parsed.get("evaluated_jobs", []))},
                "source_mix": {"raw_jobs": _source_mix(raw_jobs), "candidates": _source_mix(candidates)},
            }
            final_json = json.dumps(parsed, indent=2, default=str)
        except (json.JSONDecodeError, TypeError):
            pass

    with open(report_out, "w") as f:
        f.write(final_json)

    # Run finished cleanly — drop the checkpoint + eval cache so the NEXT run
    # starts fresh (a new company batch) instead of resuming this completed one.
    if not dry_run:
        _clear_checkpoint()
        try:
            if eval_cache and os.path.exists(eval_cache): os.remove(eval_cache)
        except Exception: pass

    print(f"\n{'='*60}\nFINAL REPORT\n{'='*60}")
    print(final_json[:3000] + ("\n... (truncated)" if len(final_json) > 3000 else ""))
    print(f"\n💾 Report → {report_out}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    resume  = "--resume" in sys.argv
    asyncio.run(main(dry_run=dry_run, resume=resume))
