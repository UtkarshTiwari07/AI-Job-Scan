"""
Autonomous Worldwide Remote Job Search Agent — v4
=================================================
Phase 1: Serper multi-cluster (per-site + broad free-text) + direct URL injection
Phase 2: Crawl4AI scrape
Phase 3: Pre-filter (qdr:w in Serper, 3-day Phase 3 enforcement)
Phase 4: DeepSeek V3 evaluation + proposal

Run:
  python job/job_remote.py           # full run
  python job/job_remote.py --dry-run
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
SEEN_FP_FILE     = os.path.join(os.path.dirname(__file__), "seen_fp_remote.json")
MAX_POSTING_AGE_DAYS = 3   # Serper uses qdr:w, Phase 3 enforces 3 days

TARGET_SITES = [
    "remoteok.com", "weworkremotely.com", "himalayas.app", "remotive.com",
    "wellfound.com", "arc.dev", "contra.com", "braintrust.us", "torre.ai", "linkedin.com",
]
PORTAL_SITES = [
    "site:boards.greenhouse.io", "site:jobs.lever.co", "site:jobs.ashbyhq.com",
    "site:huggingface.co/jobs", "site:cohere.com/careers", "site:mistral.ai/careers",
    "site:together.ai/careers", "site:modal.com/careers", "site:replicate.com/careers",
    "site:anyscale.com/careers",
]
LINKEDIN_DIRECT_URLS = [
    "https://www.linkedin.com/jobs/search/?keywords=LLM%20Engineer&f_WT=2&f_TPR=r259200&f_E=2",
    "https://www.linkedin.com/jobs/search/?keywords=AI%20Agent%20Engineer&f_WT=2&f_TPR=r259200",
    "https://www.linkedin.com/jobs/search/?keywords=voice%20AI%20engineer&f_WT=2&f_TPR=r259200",
    "https://www.linkedin.com/jobs/search/?keywords=RAG%20engineer%20remote&f_WT=2&f_TPR=r259200",
    "https://www.linkedin.com/jobs/search/?keywords=generative%20AI%20engineer%20worldwide&f_TPR=r259200&f_E=2",
]
WELLFOUND_DIRECT_URLS = [
    "https://wellfound.com/jobs?q=LLM+engineer&remote=true",
    "https://wellfound.com/jobs?q=AI+agent+engineer&remote=true",
    "https://wellfound.com/jobs?q=voice+AI&remote=true",
    "https://wellfound.com/jobs?q=RAG+engineer&remote=true",
]

QUERY_CLUSTERS = [
    {
        "name": "A1 — Voice AI / LLM Remote [per-site]",
        "terms": '("voice AI" OR "LLM engineer" OR "AI agent engineer") ("worldwide remote" OR "fully remote") -senior -lead',
        "num": 20, "sites": TARGET_SITES, "broad": False,
    },
    {
        "name": "A2 — RAG / LangChain / FastAPI Remote [per-site]",
        "terms": '("RAG engineer" OR "LangChain" OR "CrewAI" OR "FastAPI" OR "LLM ops") ("fully remote" OR "worldwide") -senior -lead',
        "num": 20, "sites": TARGET_SITES, "broad": False,
    },
    {
        "name": "A3 — GenAI Entry-Level Remote [per-site]",
        "terms": '"generative AI engineer" OR "AI engineer" ("fully remote" OR "worldwide") ("0-2 years" OR "entry level" OR "junior") -senior',
        "num": 20, "sites": TARGET_SITES, "broad": False,
    },
    {
        "name": "B1 — Voice AI Worldwide [broad]",
        "terms": '"voice AI engineer" OR "conversational AI engineer" OR "LiveKit engineer" ("fully remote" OR "worldwide") -senior -lead',
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
    {
        "name": "B2 — LLM / RAG Worldwide [broad]",
        "terms": '"LLM engineer" OR "RAG engineer" OR "agentic AI engineer" ("fully remote" OR "worldwide" OR "async") ("junior" OR "entry" OR "0-2 years") -senior',
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
    {
        "name": "B3 — Remote-First AI Startups [broad]",
        "terms": '("remote-first startup" OR "async company" OR "distributed team") "AI engineer" OR "LLM platform" ("junior" OR "entry") -"data scientist" -"DevOps"',
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
    {
        "name": "C1 — Greenhouse / Lever / Ashby Portals",
        "terms": '"LLM" OR "RAG" OR "voice AI" OR "LangChain" ("fully remote" OR "remote") ("junior" OR "0-2 years" OR "entry")',
        "num": 10, "sites": PORTAL_SITES, "broad": False, "sites_preformatted": True,
    },
    {
        "name": "D1 — Python AI Backend Remote [broad]",
        "terms": '("AI infrastructure engineer" OR "LLM platform engineer" OR "AI backend engineer") ("fully remote" OR "worldwide") ("junior" OR "entry" OR "0-3 years") -"data scientist" -"DevOps" -"SRE"',
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
]

CANDIDATE_PROFILE = {
    "name": "Utkarsh Tiwari",
    "stack": "AI Engineer (1 YOE). Python, PyTorch, LightGBM, RAG, LLMs (GPT-4, Gemini, LLaMA LoRA fine-tuning), CrewAI, LangChain, FastAPI, LiveKit, Deepgram STT, ElevenLabs TTS, Pinecone.",
    "metrics": "Built production voice AI for 2,000+ concurrent calls. Reduced LLM cold-start 10.4x (3.9s→378ms). Trained LightGBM on 716K+ records. Reduced AI-content detection from 100%→30%.",
}

RECRUITER_PATTERN = re.compile(r"\b(recruit|staffing|placement agency|hr solutions|manpower)\b", re.IGNORECASE)
TITLE_REJECT_PATTERNS = re.compile(
    r"(medical writer|medical editor|biostatistic|clinical research|"
    r"data analyst|business analyst|data modeler|data scientist|mlops|"
    r"data engineer(?!.*ai)|computer vision|cv engineer|"
    r"java\b|\.net\b|android\b|ios\b|devops(?!.*ai)|sysadmin|network engineer|"
    r"blockchain|solidity|frontend developer|support consultant|technical support(?!.*ai))",
    re.IGNORECASE,
)
EXPERIENCE_TITLE_REJECT = re.compile(r"\b(senior|lead|principal|manager|director|vp |head of|staff engineer)\b", re.IGNORECASE)
EXPERIENCE_YEARS_REJECT = ["4+ years", "5+ years", "6+ years", "7+ years", "8+ years", "10+"]
REMOTE_PASS_TOKENS   = ["remote", "work from home", "wfh", "anywhere", "worldwide", "globally"]
REMOTE_REJECT_TOKENS = ["on-site only", "onsite only", "must be in office", "must relocate"]
GEO_LOCK_TOKENS = [
    "united states only", "us only", "us-based", "us residents", "us citizens",
    "must be in the us", "must be located in", "authorized to work in the us",
    "right to work in the uk", "uk-based", "uk residents",
    "canada only", "australia only", "eu only", "europe only",
    "remote (us)", "remote (usa)", "remote (uk)", "remote (canada)",
    "remote, united states", "remote, usa", "us permanent resident", "green card",
]
EDUCATION_REJECT_TOKENS = [
    "master's degree required", "masters degree required", "m.s. required",
    "msc required", "phd required", "ph.d", "doctorate required",
]

# ══════════════════════════════════════════════════════════════════
# CROSS-RUN DEDUP
# ══════════════════════════════════════════════════════════════════

def load_seen_fingerprints() -> dict:
    if not os.path.exists(SEEN_FP_FILE): return {}
    try:
        with open(SEEN_FP_FILE) as f: data = json.load(f)
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat()
        return {fp: ts for fp, ts in data.items() if ts >= cutoff}
    except: return {}

def save_seen_fingerprints(fp_map: dict):
    try:
        with open(SEEN_FP_FILE, "w") as f: json.dump(fp_map, f)
    except Exception as e: print(f"  ⚠️ Cache save error: {e}")

# ══════════════════════════════════════════════════════════════════
# PHASE 1 — SERPER + DIRECT INJECTION
# ══════════════════════════════════════════════════════════════════

def search_for_jobs() -> List[str]:
    if not SERPER_API_KEY: print("❌ SERPER_API_KEY not set"); return []
    print("\n🔍 PHASE 1 — Remote Job Search (qdr:w → 3-day filter in Phase 3)")
    seen_urls: Set[str] = set(); all_urls: List[str] = []
    api_url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    for cluster in QUERY_CLUSTERS:
        print(f"\n  📌 {cluster['name']}")
        is_broad = cluster.get("broad", False)
        is_preformatted = cluster.get("sites_preformatted", False)

        if is_broad:
            query = cluster["terms"]
            print(f"     → [BROAD] {query[:110]}")
            try:
                resp = requests.post(api_url, headers=headers,
                    data=json.dumps({"q": query, "num": cluster["num"], "tbs": "qdr:w"}), timeout=15)
                resp.raise_for_status()
                found = 0
                for r in resp.json().get("organic", []):
                    link = r.get("link", "").strip()
                    if link and link not in seen_urls:
                        seen_urls.add(link); all_urls.append(link); found += 1
                print(f"       ✓ {found} new URLs")
            except Exception as e: print(f"     ⚠️ Serper error: {e}")
        else:
            for site in cluster["sites"]:
                site_part = site if is_preformatted else f"site:{site}"
                query = f"{site_part} {cluster['terms']}"
                print(f"     → {query[:120]}")
                try:
                    resp = requests.post(api_url, headers=headers,
                        data=json.dumps({"q": query, "num": cluster["num"], "tbs": "qdr:w"}), timeout=15)
                    resp.raise_for_status()
                    found = 0
                    for r in resp.json().get("organic", []):
                        link = r.get("link","").strip()
                        if link and link not in seen_urls:
                            seen_urls.add(link); all_urls.append(link); found += 1
                    print(f"       ✓ {found} from {site.rsplit('/',1)[-1]}")
                except Exception as e: print(f"     ⚠️ Serper error ({site}): {e}")

    inject = LINKEDIN_DIRECT_URLS + WELLFOUND_DIRECT_URLS
    print(f"\n  🔗 Injecting {len(inject)} direct URLs...")
    for url in inject:
        if url not in seen_urls: seen_urls.add(url); all_urls.append(url)

    print(f"\n  🎯 {len(all_urls)} unique URLs queued")
    return all_urls

# ══════════════════════════════════════════════════════════════════
# PHASE 2 — CRAWL4AI
# ══════════════════════════════════════════════════════════════════

class ScrapedJob(BaseModel):
    title: str = ""; company: str = ""; url: str = ""; site: str = ""
    posted_date: str = ""; location_text: str = ""; is_remote: bool = False
    job_type: str = ""; pay_text: str = ""; experience_text: str = ""
    description_snippet: str = ""

SCRAPE_INSTRUCTION = """Extract EVERY job posting on this page. For each return:
title, company, url (direct apply link), site (domain),
posted_date (ISO or relative like '3 hours ago' — ALWAYS fill),
location_text, is_remote (true/false), job_type,
pay_text (salary or empty), experience_text (years/level — ALWAYS fill),
description_snippet (first 400 chars — ALWAYS fill even if partial).
Return [] if not a job listing."""

async def scrape_jobs(urls: List[str], raw_ndjson_path: str) -> List[dict]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    import logging; logging.getLogger("crawl4ai").setLevel(logging.ERROR)
    print(f"\n🕷️  PHASE 2 — Crawl4AI scraping {len(urls)} URLs...")
    strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(provider="deepseek/deepseek-chat", api_token=DEEPSEEK_API_KEY),
        schema=ScrapedJob.model_json_schema(), extraction_type="schema", instruction=SCRAPE_INSTRUCTION)
    run_cfg = CrawlerRunConfig(extraction_strategy=strategy, cache_mode=CacheMode.BYPASS, magic=True)
    all_jobs: List[dict] = []; seen_fp: Set[str] = set()
    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
        for result in await crawler.arun_many(urls=urls, config=run_cfg):
            if not result.success or not result.extracted_content: continue
            try: items = json.loads(result.extracted_content)
            except: continue
            if not isinstance(items, list): items = [items]
            for job in items:
                if not isinstance(job, dict) or not job.get("title"): continue
                fp = hashlib.md5(f"{job.get('title','').lower()}|{job.get('company','').lower()}".encode()).hexdigest()
                if fp in seen_fp: continue
                seen_fp.add(fp)
                job["_fingerprint"] = fp; job["_scraped_at"] = datetime.datetime.utcnow().isoformat() + "Z"
                with open(raw_ndjson_path, "a") as f: f.write(json.dumps(job) + "\n")
                all_jobs.append(job)
    print(f"  ✅ {len(all_jobs)} unique jobs scraped → {raw_ndjson_path}")
    return all_jobs

# ══════════════════════════════════════════════════════════════════
# PHASE 3 — PRE-FILTER
# ══════════════════════════════════════════════════════════════════

def parse_age_days(posted_date: str) -> Optional[int]:
    if not posted_date: return None
    txt = posted_date.lower().strip()
    m = re.match(r"(\d+)\s*(hour|day|week|month|year)", txt)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return {" hour": 0, "hour": 0, "day": n, "week": n*7, "month": n*30, "year": n*365}.get(u, None)
    for slen, fmt in [(20, "%Y-%m-%dT%H:%M:%SZ"), (19, "%Y-%m-%dT%H:%M:%S"), (10, "%Y-%m-%d")]:
        try:
            dt = datetime.datetime.strptime(posted_date[:slen].replace("Z",""), fmt.replace("Z",""))
            return max((datetime.datetime.utcnow() - dt).days, 0)
        except ValueError: continue
    if any(w in txt for w in ["just","today","now","moment"]): return 0
    return None

def prefilter(jobs: List[dict], cross_run_seen: dict) -> tuple[List[dict], List[dict]]:
    print(f"\n🔬 PHASE 3 — Pre-filter ({len(jobs)} raw, {len(cross_run_seen)} cross-run known)...")
    candidates: List[dict] = []; rejected: List[dict] = []
    session_seen: Set[str] = set()
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    for job in jobs:
        title   = (job.get("title") or "").strip()
        title_l = title.lower()
        company = (job.get("company") or "").lower()
        exp     = (job.get("experience_text") or "").lower()
        desc    = (job.get("description_snippet") or "").lower()
        loc     = (job.get("location_text") or "").lower()
        combined= f"{title_l} {loc} {exp}"
        fp      = job.get("_fingerprint", "")

        def reject(r): job["rejection_reason"] = r; rejected.append(job)

        if fp and fp in cross_run_seen: reject(f"Already seen ({cross_run_seen[fp][:10]})"); continue
        if fp and fp in session_seen:   reject("Dup in run"); continue
        if fp: session_seen.add(fp)

        age = parse_age_days(job.get("posted_date", ""))
        if age is None: job["freshness_unknown"] = True
        elif age > MAX_POSTING_AGE_DAYS: reject(f"Stale: {age}d ago"); continue

        if TITLE_REJECT_PATTERNS.search(title): reject(f"Off-stack title: {title}"); continue
        if RECRUITER_PATTERN.search(company): reject(f"Recruiter: {company}"); continue
        if any(tok in f"{exp} {desc}" for tok in EDUCATION_REJECT_TOKENS): reject("Advanced degree required"); continue

        # Experience: seniority-words in TITLE only; year ranges in experience_text only
        if EXPERIENCE_TITLE_REJECT.search(title): reject(f"Senior/lead title: {title}"); continue
        if any(tok in exp for tok in EXPERIENCE_YEARS_REJECT): reject(f"Too many YOE: {exp}"); continue

        # Remote check
        has_remote = any(t in combined for t in REMOTE_PASS_TOKENS)
        has_onsite = any(t in combined for t in REMOTE_REJECT_TOKENS)
        if has_onsite: reject("On-site required"); continue
        if not job.get("is_remote") and not has_remote: reject("Not confirmed remote"); continue

        if any(t in f"{loc} {desc}" for t in GEO_LOCK_TOKENS): reject("Geo-locked US/UK/EU"); continue

        if fp: cross_run_seen[fp] = now_iso
        candidates.append(job)

    print(f"  ✅ {len(candidates)} candidates | ❌ {len(rejected)} rejected")
    return candidates, rejected

# ══════════════════════════════════════════════════════════════════
# PHASE 4 — DEEPSEEK V3
# ══════════════════════════════════════════════════════════════════

EVAL_SYSTEM = """You are {name}'s worldwide-remote job agent. Candidate is India-based.
Stack: {stack}
Metrics: {metrics}

RULES:
- Role MUST be 100% worldwide remote (reject if US/UK/EU residency required).
- Any type (full-time/contract/freelance) is fine.
- Reject: pure data science, MLOps-only, DevOps-only, Java/.NET, unrelated to AI/LLM.
- Empty description_snippet but clear AI title → is_match=true, score=60, note "description unavailable".
- 1 YOE but production-scale — don't reject purely due to YOE.

For is_match=true: drafted_proposal (3 paras: achievement → stack fit → metric + CTA).
For is_match=false: rejection_reason (1 sentence).
{format_instructions}"""

FORMAT_INSTRUCTIONS = 'Return ONLY valid JSON: {"evaluated_jobs":[{"is_match":true/false,"job_title":"string","company":"string","application_url":"string","match_score":0-100,"rejection_reason":"string or null","drafted_proposal":"string or null"}]}'

def evaluate_and_draft(candidates: List[dict]) -> str:
    if not candidates: return json.dumps({"evaluated_jobs": []}, indent=2)
    print(f"\n🧠 PHASE 4 — DeepSeek V3 evaluating {len(candidates)} candidates...")
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    system_prompt = EVAL_SYSTEM.format(name=CANDIDATE_PROFILE["name"], stack=CANDIDATE_PROFILE["stack"],
        metrics=CANDIDATE_PROFILE["metrics"], format_instructions=FORMAT_INSTRUCTIONS)

    def call_ds(batch, bn, total):
        print(f"  📦 Batch {bn}/{total}...")
        text = ""
        try:
            resp = client.chat.completions.create(model="deepseek-chat", max_tokens=14000,
                messages=[{"role":"system","content":system_prompt},
                          {"role":"user","content":f"Jobs:\n{json.dumps(batch, indent=2)}"}],
                extra_body={"thinking":{"type":"enabled"}})
            reasoning = getattr(resp.choices[0].message, "reasoning_content", None)
            if reasoning:
                lines = reasoning.strip().splitlines()
                print(f"  💭 Thinking ({bn}, {len(lines)} lines):")
                for l in lines[:20]: print(f"  {l}")
                if len(lines)>20: print(f"  ...({len(lines)-20} more)")
            text = resp.choices[0].message.content or ""
            if "```json" in text: text=text.split("```json")[1].split("```")[0].strip()
            elif "```" in text: text=text.split("```")[1].split("```")[0].strip()
            return json.loads(text).get("evaluated_jobs",[])
        except Exception as e:
            print(f"  ⚠️ Batch {bn} error: {e}")
            if text: print("  Raw:", text[:400])
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

MOCK_JOBS = [
    {"title":"AI Voice Engineer","company":"Remote-First AI Co","url":"https://jobs.ashbyhq.com/ai-voice-123","site":"jobs.ashbyhq.com","posted_date":"2 hours ago","location_text":"Worldwide Remote","is_remote":True,"job_type":"full-time","pay_text":"$80-120k/yr","experience_text":"1-2 years","description_snippet":"Build voice pipelines with LiveKit, Deepgram, ElevenLabs. FastAPI backend."},
    {"title":"Senior ML Engineer","company":"BigCorp","url":"https://jobs.lever.co/senior-ml","site":"jobs.lever.co","posted_date":"2 days ago","location_text":"Remote (US Only)","is_remote":True,"job_type":"full-time","pay_text":"$180k/yr","experience_text":"5+ years","description_snippet":"Senior ML, US timezone required."},
    {"title":"LLM Platform Engineer","company":"Cohere","url":"https://cohere.com/careers/llm","site":"cohere.com","posted_date":"5 hours ago","location_text":"Remote — Worldwide","is_remote":True,"job_type":"full-time","pay_text":"$90-130k/yr","experience_text":"1-2 years","description_snippet":"Production LLM APIs, RAG, fine-tuning pipelines."},
    {"title":"AI Agent Engineer","company":"Replicate","url":"https://replicate.com/careers/ai-agent","site":"replicate.com","posted_date":"1 day ago","location_text":"Fully Remote","is_remote":True,"job_type":"full-time","pay_text":"$100k/yr","experience_text":"0-3 years","description_snippet":"Agentic workflows using LangChain, CrewAI. Python backend. Entry-level welcome."},
]

async def main(dry_run: bool = False):
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    rdir = os.path.join(os.path.dirname(__file__), "reports_remote")
    os.makedirs(rdir, exist_ok=True)
    raw_ndjson   = os.path.join(rdir, f"raw_remote_{ts}.ndjson")
    rejected_out = os.path.join(rdir, f"rejected_remote_{ts}.json")
    report_out   = os.path.join(rdir, f"report_remote_{ts}.json")

    print(f"\n{'='*60}")
    print(f"🚀 WORLDWIDE REMOTE JOB SEARCH v4  {'[DRY RUN]' if dry_run else '[LIVE — 3-day window]'}")
    print(f"{'='*60}")

    cross_run_seen = load_seen_fingerprints()
    print(f"  📦 Cross-run cache: {len(cross_run_seen)} fingerprints")

    if dry_run:
        print("\n[DRY RUN] Using mock data")
        raw_jobs = MOCK_JOBS
        for j in raw_jobs:
            if "_fingerprint" not in j:
                j["_fingerprint"] = hashlib.md5(f"{j['title'].lower()}|{j['company'].lower()}".encode()).hexdigest()
                j["_scraped_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    else:
        urls = search_for_jobs()
        if not urls: print("No URLs. Exiting."); return
        raw_jobs = await scrape_jobs(urls, raw_ndjson)

    candidates, rejected = prefilter(raw_jobs, cross_run_seen)
    with open(rejected_out, "w") as f: json.dump(rejected, f, indent=2)
    print(f"  💾 Rejected → {rejected_out}")
    if not dry_run: save_seen_fingerprints(cross_run_seen)

    final_json = (json.dumps({"dry_run":True,"candidates_passed_prefilter":len(candidates),"candidates":candidates},indent=2)
                  if dry_run else evaluate_and_draft(candidates))
    with open(report_out, "w") as f: f.write(final_json)
    print(f"\n{'='*60}\nFINAL REPORT\n{'='*60}")
    print(final_json[:3000] + ("\n... (truncated)" if len(final_json)>3000 else ""))
    print(f"\n💾 Report → {report_out}")

if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
