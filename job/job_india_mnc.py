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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jobscan_config import load_config
import jobscan_llm

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

cfg = load_config("india_mnc")

SERPER_API_KEY   = os.getenv("SERPER_API_KEY")
SEEN_FP_FILE     = os.path.join(os.path.dirname(__file__), "seen_fp_india_mnc.json")
MAX_POSTING_AGE_DAYS = cfg.max_posting_age_days

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
GEO_LOCK_TOKENS         = cfg.geo_lock_tokens
EDUCATION_REJECT_TOKENS = cfg.education_reject_tokens

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
                **SERPER_EXTRA,
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
                    **SERPER_EXTRA,
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
    all_direct = DIRECT_URLS
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

SCRAPE_INSTRUCTION = cfg.scrape_instruction.strip()


async def scrape_jobs(urls: List[str], raw_ndjson_path: str) -> List[dict]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    import logging
    logging.getLogger("crawl4ai").setLevel(logging.ERROR)

    print(f"\n🕷️  PHASE 2 — Crawl4AI scraping {len(urls)} URLs...")

    strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(provider=jobscan_llm.get_model(), api_token=jobscan_llm.resolve_token()),
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
