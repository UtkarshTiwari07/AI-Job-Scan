"""
Autonomous Freelance Job Search Agent — v5
==========================================
Phase 1: Serper multi-cluster (per-site + broad) + direct URL injection
         (15+ platform direct URLs + niche AI board clusters)
Phase 2: Crawl4AI scrape
Phase 3: Pre-filter (domain allowlist, AI-relevance gate, title reject,
         3-day freshness enforcement, pay ≥ $30/hr)
Phase 4: DeepSeek V3 evaluation + proposal

Run:
  python job/job_freelance.py           # full run
  python job/job_freelance.py --dry-run
"""

import asyncio, json, os, re, sys, hashlib, datetime, warnings, requests
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
SEEN_FP_FILE     = os.path.join(os.path.dirname(__file__), "seen_fp_freelance.json")
MAX_POSTING_AGE_DAYS = 3   # Serper qdr:w, then Phase 3 enforces 3 days
MIN_PAY_PER_HOUR_USD = 30  # Reject freelance gigs below this

# ── Core freelance platforms for Serper site: queries ────────────
TARGET_SITES = [
    "braintrust.us", "contra.com", "lemon.io", "gun.io", "malt.com",
    "toptal.com", "wellfound.com", "arc.dev", "upwork.com", "freelancer.com",
]

# ── Domain allowlist ─────────────────────────────────────────────
# ONLY jobs scraped from these domains pass Phase 3.
# Blocks glassdoor.co.in, jobstreet.com, jooble.org, random sites.
SITE_ALLOWLIST = {
    # Tier 1: AI-specialized freelance platforms
    "arc.dev", "braintrust.us", "contra.com", "gun.io", "lemon.io",
    "malt.com", "toptal.com", "wellfound.com", "turing.com",
    # Tier 2: Mainstream but useful for AI contracts
    "upwork.com", "freelancer.com",
    # Tier 3: Niche AI/ML platforms
    "pangea.ai", "loopp.com", "workwall.com", "botpool.ai",
    "jobbers.io", "kolabtree.com",
    # Tier 4: Direct company / ATS portals
    "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
    "smartrecruiters.com", "jobs.smartrecruiters.com",
    # Tier 5: Remote job boards with AI contracts
    "weworkremotely.com", "remoteok.com", "flexjobs.com",
    "ziprecruiter.com",
    "linkedin.com", "lnkd.in",
    # Tier 6: AI training / gig platforms
    "alignerr.com", "scale.com", "outlier.ai",
}

# ── AI-relevance keyword filter ──────────────────────────────────
# If NEITHER title NOR description contains any of these keywords,
# the job is hard-rejected before reaching DeepSeek.
# Blocks: IoT boards, CAD, logo design, wedding sites, PCB hardware.
AI_RELEVANCE_KEYWORDS = re.compile(
    r"(llm|rag|langchain|crewai|fastapi|openai|pinecone|"
    r"generative.?ai|gen.?ai|ai.?agent|voice.?ai|"
    r"chatbot|gpt|gemini|llama|lora|fine.?tun|"
    r"machine.?learn|deep.?learn|neural|pytorch|"
    r"nlp|natural.?language|transformer|embedding|"
    r"ai.?engineer|ml.?engineer|ai.?develop|"
    r"artificial.?intelligence|"
    r"hugging.?face|vector.?db|chromadb|weaviate|"
    r"livek|deepgram|elevenlabs|whisper|"
    r"python.?ai|ai.?consult|ai.?automat|"
    r"prompt.?engineer|ai.?model|ai.?train|"
    r"ai.?ops|ai.?platform|ai.?solution)", re.IGNORECASE
)

# ── Direct platform URL injection ────────────────────────────────
# Pre-built search URLs injected directly into Phase 1 (no Serper needed).
# Each URL is a pre-filtered search on a freelance platform for AI roles.
UPWORK_DIRECT_URLS = [
    "https://www.upwork.com/nx/search/jobs/?q=LLM+engineer&sort=recency",
    "https://www.upwork.com/nx/search/jobs/?q=AI+agent+developer&sort=recency",
    "https://www.upwork.com/nx/search/jobs/?q=voice+AI+engineer&sort=recency",
    "https://www.upwork.com/nx/search/jobs/?q=RAG+engineer+Python&sort=recency",
    "https://www.upwork.com/nx/search/jobs/?q=LangChain+FastAPI&sort=recency",
    "https://www.upwork.com/nx/search/jobs/?q=generative+AI+developer&sort=recency",
    "https://www.upwork.com/nx/search/jobs/?q=AI+chatbot+developer&sort=recency",
]
WELLFOUND_DIRECT_URLS = [
    "https://wellfound.com/jobs?q=LLM+engineer&remote=true",
    "https://wellfound.com/jobs?q=AI+agent&remote=true",
    "https://wellfound.com/jobs?q=voice+AI&remote=true",
    "https://wellfound.com/jobs?q=generative+AI&remote=true",
]
DIRECT_PLATFORM_URLS = [
    # ── Turing (AI talent marketplace) ──
    "https://www.turing.com/remote-developer-jobs/remote-python-developer-jobs",
    "https://www.turing.com/remote-developer-jobs/remote-ai-ml-developer-jobs",
    # ── Arc.dev (remote dev jobs) ──
    "https://arc.dev/remote-jobs?disciplines=Engineering&skills=AI&skills=Python&skills=Machine+Learning",
    "https://arc.dev/remote-jobs?disciplines=Engineering&skills=LLM&skills=NLP",
    # ── Contra (commission-free freelance) ──
    "https://contra.com/opportunity?query=AI+engineer",
    "https://contra.com/opportunity?query=LLM+developer",
    # ── Gun.io (vetted devs) ──
    "https://gun.io/find-work/?q=AI+engineer",
    "https://gun.io/find-work/?q=Python+AI",
    # ── Braintrust (Web3 freelance) ──
    "https://app.usebraintrust.com/jobs?search=AI+engineer",
    "https://app.usebraintrust.com/jobs?search=LLM",
    # ── Toptal (elite freelance) ──
    "https://www.toptal.com/freelance-jobs/developers/python",
    # ── We Work Remotely (remote job board) ──
    "https://weworkremotely.com/remote-jobs/search?term=AI+engineer",
    "https://weworkremotely.com/remote-jobs/search?term=machine+learning",
    "https://weworkremotely.com/remote-jobs/search?term=LLM",
    # ── RemoteOK (remote job board) ──
    "https://remoteok.com/remote-ai-jobs",
    "https://remoteok.com/remote-machine-learning-jobs",
    "https://remoteok.com/remote-python-jobs",
    # ── BotPool (AI freelance marketplace) ──
    "https://botpool.ai/jobs",
    # ── Pangea.ai (AI dev network) ──
    "https://pangea.ai/talents?skill=AI",
    # ── Lemon.io (startup devs) ──
    "https://lemon.io/for-developers",
    # ── FlexJobs (vetted remote) ──
    "https://www.flexjobs.com/remote-jobs/ai",
    "https://www.flexjobs.com/remote-jobs/machine-learning",
    # ── Greenhouse ATS boards (AI startups) ──
    "https://boards.greenhouse.io/cohere",
    "https://boards.greenhouse.io/huggingface",
    "https://boards.greenhouse.io/together",
    "https://boards.greenhouse.io/anyscale",
    "https://boards.greenhouse.io/wandb",
    "https://boards.greenhouse.io/clarifai",
    "https://boards.greenhouse.io/modal",
    # ── Lever ATS boards ──
    "https://jobs.lever.co/turing",
    "https://jobs.lever.co/scale",
    # ── Ashby ATS ──
    "https://jobs.ashbyhq.com/sarvam",
    # ── SmartRecruiters ──
    "https://jobs.smartrecruiters.com/?keyword=LLM+engineer&location=Remote",
    "https://jobs.smartrecruiters.com/?keyword=AI+engineer&location=Remote",
    # ── Alignerr / Scale (AI training gigs) ──
    "https://www.alignerr.com/",
    "https://scale.com/careers",
]

# ── Serper query clusters ────────────────────────────────────────
QUERY_CLUSTERS = [
    # ── GROUP A: Site-restricted (core freelance platforms) ──────
    {
        "name": "A1 — Voice AI / LLM Freelance [per-site]",
        "terms": '("voice AI" OR "LLM engineer" OR "AI agent developer") ("contract" OR "freelance" OR "hourly") -senior -lead',
        "num": 20, "sites": TARGET_SITES, "broad": False,
    },
    {
        "name": "A2 — RAG / LangChain / FastAPI Freelance [per-site]",
        "terms": '("RAG" OR "LangChain" OR "CrewAI" OR "FastAPI" OR "Pinecone") ("contract" OR "freelance" OR "project") "AI engineer" -senior',
        "num": 20, "sites": TARGET_SITES, "broad": False,
    },
    {
        "name": "A3 — GenAI Entry Freelance [per-site]",
        "terms": '"generative AI" OR "LLM developer" OR "AI chatbot" ("freelance" OR "contract") ("junior" OR "entry" OR "0-2 years")',
        "num": 20, "sites": TARGET_SITES, "broad": False,
    },
    # ── GROUP B: Broad free-text with garbage-site exclusions ────
    {
        "name": "B1 — Voice AI / Agents Gigs [broad]",
        "terms": (
            '"voice AI freelance" OR "LiveKit developer" OR "Deepgram developer" '
            'OR "ElevenLabs developer" contract OR hourly -senior -lead '
            '-site:glassdoor.co.in -site:id.jobstreet.com -site:jooble.org '
            '-site:jobgether.com -site:founditgulf.com'
        ),
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
    {
        "name": "B2 — LLM / RAG Project Work [broad]",
        "terms": (
            '"LLM engineer" OR "RAG developer" OR "AI agent freelance" OR "LangChain developer" '
            '("freelance" OR "contract" OR "project-based") -senior -lead '
            '-site:glassdoor.co.in -site:id.jobstreet.com -site:jooble.org'
        ),
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
    {
        "name": "B3 — AI Startup Contract [broad]",
        "terms": (
            '("fractional AI engineer" OR "AI consultant" OR "contract AI engineer") '
            '("junior" OR "entry" OR "0-2 years") -senior '
            '-site:glassdoor.co.in -site:id.jobstreet.com'
        ),
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
    {
        "name": "B4 — Python AI Backend Freelance [broad]",
        "terms": (
            '("FastAPI developer freelance" OR "Python AI developer" OR "AI API developer") '
            '("contract" OR "remote freelance") -"data scientist" -"DevOps" '
            '-site:glassdoor.co.in -site:id.jobstreet.com -site:jooble.org'
        ),
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
    # ── GROUP C: Niche AI platforms (per-site) ──────────────────
    {
        "name": "C1 — Braintrust / Contra / Gun.io / Arc [per-site]",
        "terms": '"AI engineer" OR "LLM" OR "voice AI" OR "RAG" OR "generative AI" contract OR freelance OR project',
        "num": 20, "sites": ["braintrust.us", "contra.com", "gun.io", "lemon.io", "arc.dev"],
        "broad": False,
    },
    {
        "name": "C2 — Remote AI Job Boards [per-site]",
        "terms": (
            '("AI engineer" OR "LLM" OR "machine learning" OR "generative AI" OR "Python AI") '
            '("remote" OR "freelance" OR "contract")'
        ),
        "num": 20,
        "sites": ["weworkremotely.com", "remoteok.com", "flexjobs.com"],
        "broad": False,
    },
    {
        "name": "C3 — AI Training / Gig Platforms [per-site]",
        "terms": '"AI trainer" OR "LLM trainer" OR "AI engineer" OR "Python" remote contract',
        "num": 15,
        "sites": ["turing.com", "alignerr.com", "scale.com", "outlier.ai", "pangea.ai", "botpool.ai"],
        "broad": False,
    },
    {
        "name": "C4 — Toptal / Malt Elite Freelance [per-site]",
        "terms": '"AI" OR "machine learning" OR "LLM" OR "Python" freelance OR contract',
        "num": 15,
        "sites": ["toptal.com", "malt.com", "jobbers.io", "kolabtree.com"],
        "broad": False,
    },
]

CANDIDATE_PROFILE = {
    "name": "Utkarsh Tiwari",
    "stack": "AI Engineer (1 YOE). Python, PyTorch, LightGBM, RAG, LLMs (GPT-4, Gemini, LLaMA LoRA fine-tuning), CrewAI, LangChain, FastAPI, LiveKit, Deepgram STT, ElevenLabs TTS, Pinecone.",
    "metrics": "Built production voice AI for 2,000+ concurrent calls. Reduced LLM cold-start 10.4x (3.9s→378ms). Trained LightGBM on 716K+ records. Reduced AI-content detection 100%→30%.",
    "min_rate": f"${MIN_PAY_PER_HOUR_USD}/hr minimum",
}

RECRUITER_PATTERN = re.compile(r"\b(recruit|staffing|placement agency|hr solutions|manpower)\b", re.IGNORECASE)

# Hard-reject non-AI-stack job titles.
# Expanded after live-run analysis: IoT/PCB/CAD/logo/wedding/WordPress/
# hardware/marketing/graphic design noise observed in freelancer.com data.
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
    r"computer vision|cv engineer|robotics|"
    r"\bjava\b|java developer|java engineer|\.net\b|angular|react native|mern|mean stack|"
    r"php developer|php engineer|ruby on rails|node.?js developer|wordpress|"
    r"android\b|ios developer|ios engineer|flutter|kotlin|swift|"
    r"devops(?!.*ai)|sysadmin|network engineer|embedded|firmware|"
    r"blockchain|solidity|web3(?!.*ai)|nft|"
    r"full.?stack(?!.*ai|.*ml|.*python)|frontend(?!.*ai)|"
    r"support consultant|technical support(?!.*ai)|it support|service desk|"
    r"salesforce|oracle|powerbi|odoo|teamcenter|"
    r"executive assistant|chief of staff|operations manager|scrum master|"
    r"cyber security|cybersecurity|penetration test|"
    r"pcb design|hardware design|circuit|cad design|autocad|solidworks|"
    r"3d model|3d print|3d anim|revit|logo design|illustration|"
    r"wedding|video edit|photo edit|retouching|power.?point|"
    r"seo |google ads|meta ads|email market)",
    re.IGNORECASE,
)

EXPERIENCE_TITLE_REJECT = re.compile(r"\b(senior|lead|principal|manager|director|vp |head of)\b", re.IGNORECASE)
EXPERIENCE_YEARS_REJECT = ["4+ years", "5+ years", "6+ years", "7+ years", "8+ years", "10+"]
EDUCATION_REJECT_TOKENS = ["master's degree required", "masters degree required", "phd required", "ph.d", "doctorate"]

def parse_pay_hourly(pay_str: str) -> Optional[float]:
    """Extract lowest hourly USD rate from pay string."""
    if not pay_str: return None
    pay_l = pay_str.lower()
    # Only hourly; skip project/fixed/monthly
    if not any(w in pay_l for w in ["/hr", "per hour", "hourly", "/h"]):
        return None
    m = re.search(r"\$\s?(\d+(?:,\d+)?(?:\.\d+)?)", pay_str)
    if m:
        try: return float(m.group(1).replace(",",""))
        except: return None
    return None

# ══════════════════════════════════════════════════════════════════
# CROSS-RUN DEDUP
# ══════════════════════════════════════════════════════════════════

def load_seen_fingerprints() -> dict:
    if not os.path.exists(SEEN_FP_FILE): return {}
    try:
        with open(SEEN_FP_FILE) as f: data=json.load(f)
        cutoff=(datetime.datetime.utcnow()-datetime.timedelta(days=7)).isoformat()
        return {fp:ts for fp,ts in data.items() if ts>=cutoff}
    except: return {}

def save_seen_fingerprints(fp_map: dict):
    try:
        with open(SEEN_FP_FILE,"w") as f: json.dump(fp_map,f)
    except Exception as e: print(f"  ⚠️ Cache save error: {e}")

# ══════════════════════════════════════════════════════════════════
# PHASE 1 — SERPER + DIRECT INJECTION
# ══════════════════════════════════════════════════════════════════

def search_for_jobs() -> List[str]:
    if not SERPER_API_KEY: print("❌ SERPER_API_KEY not set"); return []
    print("\n🔍 PHASE 1 — Freelance Job Search (qdr:w → 3-day filter in Phase 3)")
    seen_urls: Set[str]=set(); all_urls: List[str]=[]
    api_url="https://google.serper.dev/search"
    headers={"X-API-KEY":SERPER_API_KEY,"Content-Type":"application/json"}

    for cluster in QUERY_CLUSTERS:
        print(f"\n  📌 {cluster['name']}")
        is_broad=cluster.get("broad",False)
        if is_broad:
            query=cluster["terms"]
            print(f"     → [BROAD] {query[:110]}")
            try:
                resp=requests.post(api_url,headers=headers,
                    data=json.dumps({"q":query,"num":cluster["num"],"tbs":"qdr:w"}),timeout=15)
                resp.raise_for_status()
                found=sum(1 for r in resp.json().get("organic",[]) if (l:=r.get("link","").strip()) and l not in seen_urls and (seen_urls.add(l) or all_urls.append(l) or True))
                print(f"       ✓ {found} new URLs")
            except Exception as e: print(f"     ⚠️ Serper error: {e}")
        else:
            for site in cluster["sites"]:
                query=f"site:{site} {cluster['terms']}"
                print(f"     → {query[:120]}")
                try:
                    resp=requests.post(api_url,headers=headers,
                        data=json.dumps({"q":query,"num":cluster["num"],"tbs":"qdr:w"}),timeout=15)
                    resp.raise_for_status()
                    found=0
                    for r in resp.json().get("organic",[]):
                        l=r.get("link","").strip()
                        if l and l not in seen_urls: seen_urls.add(l);all_urls.append(l);found+=1
                    print(f"       ✓ {found} from {site}")
                except Exception as e: print(f"     ⚠️ Serper error ({site}): {e}")

    # ── Direct URL injection ─────────────────────────────────────
    all_direct = UPWORK_DIRECT_URLS + WELLFOUND_DIRECT_URLS + DIRECT_PLATFORM_URLS
    print(f"\n  🔗 Injecting {len(all_direct)} direct URLs (Upwork + Wellfound + 15 platforms)...")
    for url in all_direct:
        if url not in seen_urls: seen_urls.add(url); all_urls.append(url)

    print(f"\n  🎯 {len(all_urls)} unique URLs queued")
    return all_urls

# ══════════════════════════════════════════════════════════════════
# PHASE 2 — CRAWL4AI
# ══════════════════════════════════════════════════════════════════

class ScrapedJob(BaseModel):
    title: str=""; company: str=""; url: str=""; site: str=""
    posted_date: str=""; location_text: str=""; is_remote: bool=False
    job_type: str=""; pay_text: str=""; experience_text: str=""
    description_snippet: str=""

SCRAPE_INSTRUCTION="""Extract EVERY job/gig posting on this page. For each return:
title, company (client/company name or "Unknown"), url (direct apply link), site (domain),
posted_date (ISO or relative like '2 hours ago' — ALWAYS fill),
location_text, is_remote (true/false), job_type (contract/freelance/full-time/part-time),
pay_text (exact rate/salary shown — ALWAYS fill, e.g. '$50/hr', '$5000 project budget'),
experience_text (ALWAYS fill — e.g. '1-2 years', 'entry level', 'any level'),
description_snippet (first 400 chars — ALWAYS fill).
Return [] if not a job listing."""

async def scrape_jobs(urls: List[str], raw_ndjson_path: str) -> List[dict]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    import logging; logging.getLogger("crawl4ai").setLevel(logging.ERROR)
    print(f"\n🕷️  PHASE 2 — Crawl4AI scraping {len(urls)} URLs...")
    strategy=LLMExtractionStrategy(
        llm_config=LLMConfig(provider="deepseek/deepseek-chat",api_token=DEEPSEEK_API_KEY),
        schema=ScrapedJob.model_json_schema(),extraction_type="schema",instruction=SCRAPE_INSTRUCTION)
    run_cfg=CrawlerRunConfig(extraction_strategy=strategy,cache_mode=CacheMode.BYPASS,magic=True)
    all_jobs: List[dict]=[]; seen_fp: Set[str]=set()
    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
        for result in await crawler.arun_many(urls=urls,config=run_cfg):
            if not result.success or not result.extracted_content: continue
            try: items=json.loads(result.extracted_content)
            except: continue
            if not isinstance(items,list): items=[items]
            for job in items:
                if not isinstance(job,dict) or not job.get("title"): continue
                fp=hashlib.md5(f"{job.get('title','').lower()}|{job.get('company','').lower()}".encode()).hexdigest()
                if fp in seen_fp: continue
                seen_fp.add(fp)
                job["_fingerprint"]=fp; job["_scraped_at"]=datetime.datetime.utcnow().isoformat()+"Z"
                with open(raw_ndjson_path,"a") as f: f.write(json.dumps(job)+"\n")
                all_jobs.append(job)
    print(f"  ✅ {len(all_jobs)} unique jobs scraped → {raw_ndjson_path}")
    return all_jobs

# ══════════════════════════════════════════════════════════════════
# PHASE 3 — PRE-FILTER
# ══════════════════════════════════════════════════════════════════

def parse_age_days(posted_date: str) -> Optional[int]:
    if not posted_date: return None
    txt=posted_date.lower().strip()
    m=re.match(r"(\d+)\s*(hour|day|week|month|year)",txt)
    if m:
        n,u=int(m.group(1)),m.group(2)
        if u=="hour": return 0
        if u=="day": return n
        if u=="week": return n*7
        if u=="month": return n*30
        if u=="year": return n*365
    for slen,fmt in [(20,"%Y-%m-%dT%H:%M:%SZ"),(19,"%Y-%m-%dT%H:%M:%S"),(10,"%Y-%m-%d")]:
        try:
            dt=datetime.datetime.strptime(posted_date[:slen].replace("Z",""),fmt.replace("Z",""))
            return max((datetime.datetime.utcnow()-dt).days,0)
        except ValueError: continue
    if any(w in txt for w in ["just","today","now","moment"]): return 0
    return None

def _domain_match(site_field: str) -> bool:
    """Check if a site field matches any domain in SITE_ALLOWLIST."""
    if not site_field: return False
    site_l = site_field.lower().strip()
    for allowed in SITE_ALLOWLIST:
        if site_l == allowed or site_l.endswith("." + allowed):
            return True
    return False

def prefilter(jobs: List[dict], cross_run_seen: dict) -> tuple[List[dict], List[dict]]:
    print(f"\n🔬 PHASE 3 — Pre-filter ({len(jobs)} raw, {len(cross_run_seen)} cross-run known)...")
    candidates: List[dict]=[]; rejected: List[dict]=[]
    session_seen: Set[str]=set()
    now_iso=datetime.datetime.utcnow().isoformat()+"Z"

    for job in jobs:
        title  =(job.get("title") or "").strip()
        title_l=title.lower()
        company=(job.get("company") or "").lower()
        exp    =(job.get("experience_text") or "").lower()
        desc   =(job.get("description_snippet") or "").lower()
        fp     =job.get("_fingerprint","")
        site   =(job.get("site") or "").lower().strip()

        def reject(r): job["rejection_reason"]=r; rejected.append(job)

        # 0. Site allowlist — hard-block garbage sites
        if site and not _domain_match(site):
            reject(f"Not-allowlisted site: {site}"); continue

        # 1. Cross-run dedup
        if fp and fp in cross_run_seen: reject(f"Already seen ({cross_run_seen[fp][:10]})"); continue
        if fp and fp in session_seen:   reject("Dup in run"); continue
        if fp: session_seen.add(fp)

        # 2. Freshness
        age=parse_age_days(job.get("posted_date",""))
        if age is None: job["freshness_unknown"]=True
        elif age>MAX_POSTING_AGE_DAYS: reject(f"Stale: {age}d ago"); continue

        # 3. Title reject
        if TITLE_REJECT_PATTERNS.search(title): reject(f"Off-stack title: {title}"); continue

        # 4. AI-relevance gate — blocks IoT/CAD/logo/wedding/PCB jobs
        combined_ai_text = f"{title} {desc}"
        if not AI_RELEVANCE_KEYWORDS.search(combined_ai_text):
            reject(f"No AI relevance in title+desc: {title[:60]}"); continue

        # 5. Recruiter spam
        if RECRUITER_PATTERN.search(company): reject(f"Recruiter: {company}"); continue
        if any(tok in f"{exp} {desc}" for tok in EDUCATION_REJECT_TOKENS): reject("Advanced degree required"); continue

        # 6. Experience: seniority in TITLE only; year ranges in experience_text only
        if EXPERIENCE_TITLE_REJECT.search(title): reject(f"Senior/lead title: {title}"); continue
        if any(tok in exp for tok in EXPERIENCE_YEARS_REJECT): reject(f"Too many YOE: {exp}"); continue

        # 7. Pay filter — only enforce for explicitly hourly pay; skip if pay not specified
        pay_text=job.get("pay_text","")
        hourly_rate=parse_pay_hourly(pay_text)
        if hourly_rate is not None and hourly_rate < MIN_PAY_PER_HOUR_USD:
            reject(f"Pay too low: ${hourly_rate}/hr (min ${MIN_PAY_PER_HOUR_USD}/hr)"); continue
        job["pay_hourly_usd"]=hourly_rate
        job["pay_missing"]=(pay_text=="")

        if fp: cross_run_seen[fp]=now_iso
        candidates.append(job)

    print(f"  ✅ {len(candidates)} candidates | ❌ {len(rejected)} rejected")
    return candidates, rejected

# ══════════════════════════════════════════════════════════════════
# PHASE 4 — DEEPSEEK V3
# ══════════════════════════════════════════════════════════════════

EVAL_SYSTEM="""You are {name}'s autonomous freelance AI job agent.
Stack: {stack}
Metrics: {metrics}
Min rate: {min_rate}

RULES:
- Worldwide remote freelance/contract only. Reject onsite-only positions.
- Reject if pay is specified and clearly below $30/hr equivalent.
- Reject: pure data science, MLOps-only, Java/.NET, DevOps, unrelated to AI Voice/LLM/RAG/Python.
- Empty description_snippet but clear AI title → is_match=true, score=60, note "description unavailable".
- 1 YOE but production-scale — do not reject purely due to YOE.

For is_match=true: drafted_proposal (3 tight paragraphs: achievement → stack fit → metric+CTA).
For is_match=false: rejection_reason (1 sentence).
{format_instructions}"""

FORMAT_INSTRUCTIONS='Return ONLY valid JSON: {"evaluated_jobs":[{"is_match":true/false,"job_title":"string","company":"string","application_url":"string","match_score":0-100,"rejection_reason":"string or null","drafted_proposal":"string or null"}]}'

def evaluate_and_draft(candidates: List[dict]) -> str:
    if not candidates: return json.dumps({"evaluated_jobs":[]},indent=2)
    print(f"\n🧠 PHASE 4 — DeepSeek V3 evaluating {len(candidates)} candidates...")
    client=OpenAI(api_key=DEEPSEEK_API_KEY,base_url="https://api.deepseek.com")
    system_prompt=EVAL_SYSTEM.format(name=CANDIDATE_PROFILE["name"],stack=CANDIDATE_PROFILE["stack"],
        metrics=CANDIDATE_PROFILE["metrics"],min_rate=CANDIDATE_PROFILE["min_rate"],
        format_instructions=FORMAT_INSTRUCTIONS)

    def call_ds(batch,bn,total):
        print(f"  📦 Batch {bn}/{total}...")
        text=""
        try:
            resp=client.chat.completions.create(model="deepseek-chat",max_tokens=14000,
                messages=[{"role":"system","content":system_prompt},
                          {"role":"user","content":f"Gigs:\n{json.dumps(batch,indent=2)}"}],
                extra_body={"thinking":{"type":"enabled"}})
            reasoning=getattr(resp.choices[0].message,"reasoning_content",None)
            if reasoning:
                lines=reasoning.strip().splitlines()
                print(f"  💭 Thinking ({bn}, {len(lines)} lines):")
                for l in lines[:20]: print(f"  {l}")
                if len(lines)>20: print(f"  ...({len(lines)-20} more)")
            text=resp.choices[0].message.content or ""
            if "```json" in text: text=text.split("```json")[1].split("```")[0].strip()
            elif "```" in text: text=text.split("```")[1].split("```")[0].strip()
            return json.loads(text).get("evaluated_jobs",[])
        except Exception as e:
            print(f"  ⚠️ Batch {bn} error: {e}")
            if text: print("  Raw:",text[:400])
            return []

    batches=[candidates[i:i+10] for i in range(0,len(candidates),10)]
    all_eval: List[dict]=[]
    for idx,batch in enumerate(batches,1):
        results=call_ds(batch,idx,len(batches))
        all_eval.extend(results)
        hits=sum(1 for j in results if j.get("is_match"))
        print(f"  ✅ {idx}/{len(batches)} — {hits}/{len(results)} matched, total: {len(all_eval)}")
    return json.dumps({"evaluated_jobs":all_eval},indent=2)

# ══════════════════════════════════════════════════════════════════
# MOCK + MAIN
# ══════════════════════════════════════════════════════════════════

MOCK_JOBS=[
    {"title":"AI Voice Agent Developer","company":"TechStartup Inc","url":"https://arc.dev/jobs/ai-voice-123","site":"arc.dev","posted_date":"2 hours ago","location_text":"Remote","is_remote":True,"job_type":"contract","pay_text":"$50/hr","experience_text":"1-2 years","description_snippet":"Build LiveKit voice agents using Python and OpenAI APIs. Entry level welcome."},
    {"title":"LLM Engineer — RAG","company":"AI Startup","url":"https://wellfound.com/jobs/789","site":"wellfound.com","posted_date":"4 hours ago","location_text":"Remote / Worldwide","is_remote":True,"job_type":"freelance","pay_text":"$4000 project budget","experience_text":"Entry level","description_snippet":"Build RAG pipeline using LangChain and Pinecone for document Q&A."},
    {"title":"Data Scientist","company":"Analytics Co","url":"https://upwork.com/jobs/data-scientist-123","site":"upwork.com","posted_date":"1 hour ago","location_text":"Remote","is_remote":True,"job_type":"contract","pay_text":"$20/hr","experience_text":"2 years","description_snippet":"Statistical modeling, A/B testing, pandas."},
    {"title":"AI Agent Developer","company":"SaaS Co","url":"https://contra.com/jobs/ai-agent","site":"contra.com","posted_date":"2 days ago","location_text":"Remote","is_remote":True,"job_type":"freelance","pay_text":"$60/hr","experience_text":"1-3 years","description_snippet":"Build multi-agent pipelines with CrewAI and LangChain. FastAPI backend."},
]

async def main(dry_run: bool=False):
    ts=datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    rdir=os.path.join(os.path.dirname(__file__),"reports_freelance")
    os.makedirs(rdir,exist_ok=True)
    raw_ndjson   =os.path.join(rdir,f"raw_freelance_{ts}.ndjson")
    rejected_out =os.path.join(rdir,f"rejected_freelance_{ts}.json")
    report_out   =os.path.join(rdir,f"report_freelance_{ts}.json")

    print(f"\n{'='*60}")
    print(f"🚀 FREELANCE JOB SEARCH v4  {'[DRY RUN]' if dry_run else '[LIVE — 3-day window]'}")
    print(f"{'='*60}")

    cross_run_seen=load_seen_fingerprints()
    print(f"  📦 Cross-run cache: {len(cross_run_seen)} fingerprints")

    if dry_run:
        print("\n[DRY RUN] Using mock data")
        raw_jobs=MOCK_JOBS
        for j in raw_jobs:
            if "_fingerprint" not in j:
                j["_fingerprint"]=hashlib.md5(f"{j['title'].lower()}|{j['company'].lower()}".encode()).hexdigest()
                j["_scraped_at"]=datetime.datetime.utcnow().isoformat()+"Z"
    else:
        urls=search_for_jobs()
        if not urls: print("No URLs. Exiting."); return
        raw_jobs=await scrape_jobs(urls,raw_ndjson)

    candidates,rejected=prefilter(raw_jobs,cross_run_seen)
    with open(rejected_out,"w") as f: json.dump(rejected,f,indent=2)
    print(f"  💾 Rejected → {rejected_out}")
    if not dry_run: save_seen_fingerprints(cross_run_seen)

    final_json=(json.dumps({"dry_run":True,"candidates_passed_prefilter":len(candidates),"candidates":candidates},indent=2)
                if dry_run else evaluate_and_draft(candidates))
    with open(report_out,"w") as f: f.write(final_json)
    print(f"\n{'='*60}\nFINAL REPORT\n{'='*60}")
    print(final_json[:3000]+("\n... (truncated)" if len(final_json)>3000 else ""))
    print(f"\n💾 Report → {report_out}")

if __name__=="__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
