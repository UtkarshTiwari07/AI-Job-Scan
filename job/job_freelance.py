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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jobscan_config import load_config
import jobscan_llm

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

cfg = load_config("freelance")

SERPER_API_KEY   = os.getenv("SERPER_API_KEY")
SEEN_FP_FILE     = os.path.join(os.path.dirname(__file__), "seen_fp_freelance.json")
MAX_POSTING_AGE_DAYS = cfg.max_posting_age_days
MIN_PAY_PER_HOUR_USD = cfg.min_pay_per_hour_usd

QUERY_CLUSTERS = cfg.query_clusters
DIRECT_URLS    = cfg.direct_urls
SERPER_EXTRA   = cfg.serper_extra
SITE_ALLOWLIST = cfg.site_allowlist

CANDIDATE_PROFILE = cfg.profile

RECRUITER_PATTERN       = cfg.recruiter_re
TITLE_REJECT_PATTERNS   = cfg.title_reject_re
AI_RELEVANCE_KEYWORDS   = cfg.ai_relevance_re
EXPERIENCE_TITLE_REJECT = cfg.seniority_reject_re
EXPERIENCE_YEARS_REJECT = cfg.experience_years_reject
EDUCATION_REJECT_TOKENS = cfg.education_reject_tokens

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
                    data=json.dumps({"q":query,"num":cluster["num"],"tbs":"qdr:w",**SERPER_EXTRA}),timeout=15)
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
                        data=json.dumps({"q":query,"num":cluster["num"],"tbs":"qdr:w",**SERPER_EXTRA}),timeout=15)
                    resp.raise_for_status()
                    found=0
                    for r in resp.json().get("organic",[]):
                        l=r.get("link","").strip()
                        if l and l not in seen_urls: seen_urls.add(l);all_urls.append(l);found+=1
                    print(f"       ✓ {found} from {site}")
                except Exception as e: print(f"     ⚠️ Serper error ({site}): {e}")

    # ── Direct URL injection ─────────────────────────────────────
    all_direct = DIRECT_URLS
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

SCRAPE_INSTRUCTION = cfg.scrape_instruction

async def scrape_jobs(urls: List[str], raw_ndjson_path: str) -> List[dict]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    import logging; logging.getLogger("crawl4ai").setLevel(logging.ERROR)
    print(f"\n🕷️  PHASE 2 — Crawl4AI scraping {len(urls)} URLs...")
    strategy=LLMExtractionStrategy(
        llm_config=LLMConfig(provider=jobscan_llm.get_model(), api_token=jobscan_llm.resolve_token()),
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

EVAL_SYSTEM = cfg.eval_system

FORMAT_INSTRUCTIONS = cfg.format_instructions

def evaluate_and_draft(candidates: List[dict]) -> str:
    if not candidates:
        return json.dumps({"evaluated_jobs": []}, indent=2)

    model = jobscan_llm.get_model()
    print(f"\n🧠 PHASE 4 — {model} evaluating {len(candidates)} candidates...")

    system_prompt = EVAL_SYSTEM.format(
        name=CANDIDATE_PROFILE.get("name", ""),
        stack=CANDIDATE_PROFILE.get("stack", ""),
        metrics=CANDIDATE_PROFILE.get("metrics", ""),
        location=CANDIDATE_PROFILE.get("location", ""),
        min_rate=CANDIDATE_PROFILE.get("min_rate", ""),
        format_instructions=FORMAT_INSTRUCTIONS,
    )

    def call_llm(batch: List[dict], batch_num: int, total: int) -> List[dict]:
        print(f"  📦 Batch {batch_num}/{total} ({len(batch)} jobs)...")
        text = ""
        try:
            text, reasoning = jobscan_llm.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Jobs:\n{json.dumps(batch, indent=2)}"},
                ],
                max_tokens=14000,
            )
            if reasoning:
                lines = reasoning.strip().splitlines()
                print(f"  💭 Thinking (batch {batch_num}, {len(lines)} lines):")
                for line in lines[:20]:
                    print(f"  {line}")
                if len(lines) > 20:
                    print(f"  ... ({len(lines)-20} more)")
            text = text or ""
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            return json.loads(text).get("evaluated_jobs", [])
        except Exception as e:
            print(f"  ⚠️ Batch {batch_num} error: {e}")
            if text:
                print("  📄 Raw:", text[:400])
            return []

    batches = [candidates[i:i+10] for i in range(0, len(candidates), 10)]
    all_evaluated: List[dict] = []
    for idx, batch in enumerate(batches, 1):
        results = call_llm(batch, idx, len(batches))
        all_evaluated.extend(results)
        hits = sum(1 for j in results if j.get("is_match"))
        print(f"  ✅ Batch {idx}/{len(batches)} — {hits}/{len(results)} matched, total: {len(all_evaluated)}")

    return json.dumps({"evaluated_jobs": all_evaluated}, indent=2)

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
