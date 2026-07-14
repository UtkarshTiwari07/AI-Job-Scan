"""
Autonomous Job Search Agent — Production v2
==========================================
Phases:
  1. Serper.dev multi-cluster search across targeted platforms
  2. Crawl4AI universal scrape (DeepSeek reads any layout)
  3. Pure-Python pre-filter (zero LLM cost)
  4. DeepSeek R1 (Thinking Mode) evaluation + proposal drafting

Run:
  python job/job2.py          # full run
  python job/job2.py --dry-run  # test filters with mock data, no API calls
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
from pydantic import BaseModel

# ══════════════════════════════════════════════════════════════════
# CONFIG — edit this block, never touch the logic below
# ══════════════════════════════════════════════════════════════════

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERPER_API_KEY   = os.getenv("SERPER_API_KEY")

# Default platforms for clusters A–C
TARGET_SITES = [
    "arc.dev",
    "toptal.com",
    "freelancermap.com",
    "beeig.com",
    "freeup.io",
    "hubstaff.com",
    "peopleperhour.com",
    "remoteok.com",
    "weworkremotely.com",
    "wellfound.com",
    "naukri.com",
    "linkedin.com",
    "indeed.com",
]

# India-specific platforms for cluster D
INDIA_SITES = [
    "naukri.com",
    "linkedin.com",
    "indeed.co.in",
    "foundit.in",
    "internshala.com",
    "instahyre.com",
    "wellfound.com",
]

# Serper query clusters — drives Phase 1
# Each cluster may override sites via optional "sites" key
QUERY_CLUSTERS = [
    {
        "name": "A — Voice / Realtime AI",
        "terms": (
            '"voice ai" OR "conversational ai" OR "voice agent" OR '
            '"real-time audio" OR "STT" OR "TTS" OR "LiveKit" OR '
            '"WebRTC" OR "Twilio" remote freelance OR contract'
        ),
        "results_per_site": 5,
    },
    {
        "name": "B — LLM / RAG",
        "terms": (
            '"LLM" OR "RAG" OR "retrieval-augmented" OR "prompt engineer" OR '
            '"vector database" OR "Pinecone" remote freelance OR contract'
        ),
        "results_per_site": 5,
    },
    {
        "name": "C — General AI Contract (0-2 yrs)",
        "terms": (
            '"AI engineer" OR "machine learning engineer" OR "ml engineer" '
            'contract OR freelance remote entry junior "0-2" OR "1 year" OR "2 years"'
        ),
        "results_per_site": 5,
    },
    {
        "name": "D — India MNC Entry AI Engineer",
        "terms": (
            '("AI engineer" OR "machine learning engineer" OR "generative AI engineer" '
            'OR "NLP engineer" OR "deep learning engineer") '
            '("entry level" OR "junior" OR "fresher" OR "0-2 years" OR "0-1 year") '
            'India remote -senior -lead -principal -manager'
        ),
        "results_per_site": 8,
        "sites": INDIA_SITES,   # override — India-specific boards only
    },
]

# Candidate profile passed to DeepSeek (not hardcoded in prompt string)
CANDIDATE_PROFILE = {
    "name": "Utkarsh",
    "stack": (
        "AI, RAG, Python, LLMs (OpenAI, LLaMA, Gemini), Generative AI, NLP, "
        "PyTorch, CrewAI, TensorFlow, Transformers, AI Agents, Prompt Engineering, "
        "MLOps, MCP, LangChain, LangGraph, LiveKit, FastAPI, Deepgram, Pinecone, Voice AI"
    ),
    "metrics": (
        "reduced LLM latency by 45%, handles 2000+ concurrent voice agents, "
        "<30% AI-detectable content, built 6-agent AI architectures"
    ),
}

# Pre-filter rules
RECRUITER_PATTERN = re.compile(r"(recruit|staffing|agency|consult)", re.IGNORECASE)

EXPERIENCE_PASS_TOKENS    = ["entry", "junior", "fresher", "early-career", "0-2", "1 year", "2 years"]
EXPERIENCE_REJECT_TOKENS  = ["senior", "lead", "principal", "manager", "3+ years", "5+ years", "10+"]

REMOTE_PASS_TOKENS        = ["remote", "work from home", "wfh", "anywhere", "worldwide", "globally"]
REMOTE_REJECT_TOKENS      = ["on-site", "onsite", "must relocate", "relocation", "visa sponsorship"]

# Geo-lock: role is "remote" but restricted to a specific country — useless for India-based candidate
GEO_LOCK_TOKENS = [
    "united states only", "us only", "us-based", "us residents", "us citizens",
    "must be in the us", "must reside in", "must be located in", "must be based in",
    "authorized to work in the us", "authorization to work in the us",
    "right to work in the uk", "right to work in the united kingdom",
    "uk-based", "uk residents", "canada only", "australia only",
    "eu only", "europe only", "within the us", "within the uk",
    "remote (usa)", "remote (us)", "remote (uk)", "remote (canada)",
    "remote, united states", "remote, usa", "remote, uk", "remote, canada",
    "remote in the us", "remote in the uk", "work from home in the us",
    "us permanent resident", "green card",
]

# Education: reject roles requiring postgrad degree
EDUCATION_REJECT_TOKENS = [
    "master's degree required", "masters degree", "master degree required",
    "m.s. required", "msc required", "m.tech required", "mtech required",
    "phd", "ph.d", "doctorate", "doctoral degree", "postgraduate degree required",
]

ALLOWED_JOB_TYPES         = {"contract", "freelance", "part-time", "consulting", "unknown", ""}
BLOCKED_JOB_TYPES         = {"full-time", "permanent", "fulltime"}

MIN_HOURLY_USD            = 30  # minimum acceptable rate
MAX_POSTING_AGE_DAYS      = 14  # reject jobs older than 2 weeks

# ══════════════════════════════════════════════════════════════════
# PHASE 1 — SERPER MULTI-SITE SEARCH
# ══════════════════════════════════════════════════════════════════

def build_site_query(base_terms: str, sites: List[str]) -> List[str]:
    """Split sites into small groups so Serper can handle site: operators."""
    # Serper handles multiple site: operators best as separate calls per site cluster
    queries = []
    site_group = " OR ".join(f"site:{s}" for s in sites)
    queries.append(f"({site_group}) {base_terms}")
    return queries


def search_for_jobs() -> List[str]:
    if not SERPER_API_KEY:
        print("❌ SERPER_API_KEY not set"); return []

    print("\n🔍 PHASE 1 — Serper.dev Multi-Site Search")
    seen_urls: Set[str] = set()
    all_urls: List[str] = []
    api_url  = "https://google.serper.dev/search"
    headers  = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    for cluster in QUERY_CLUSTERS:
        print(f"\n  📌 Cluster: {cluster['name']}")
        sites   = cluster.get("sites", TARGET_SITES)  # cluster may override sites
        queries = build_site_query(cluster["terms"], sites)

        for query in queries:
            print(f"     → {query[:120]}...")
            payload = json.dumps({
                "q":   query,
                "num": cluster["results_per_site"] * len(sites),
                "tbs": "qdr:m",  # last month — freshness enforced in Phase 3
            })
            try:
                resp = requests.post(api_url, headers=headers, data=payload, timeout=15)
                resp.raise_for_status()
                for r in resp.json().get("organic", []):
                    link = r.get("link", "").strip()
                    if link and link not in seen_urls:
                        seen_urls.add(link)
                        all_urls.append(link)
            except Exception as e:
                print(f"     ⚠️ Serper error: {e}")

    print(f"\n  🎯 {len(all_urls)} unique URLs to scrape across all clusters")
    return all_urls


# ══════════════════════════════════════════════════════════════════
# PHASE 2 — CRAWL4AI UNIVERSAL SCRAPE
# ══════════════════════════════════════════════════════════════════

class ScrapedJob(BaseModel):
    title:              str = ""
    company:            str = ""
    url:                str = ""
    site:               str = ""
    posted_date:        str = ""
    location_text:      str = ""
    is_remote:          bool = False
    job_type:           str = ""   # contract / freelance / full-time / part-time / unknown
    pay_text:           str = ""
    experience_text:    str = ""
    description_snippet: str = ""


SCRAPE_INSTRUCTION = """
Extract EVERY job posting visible on this page.
For each job return:
  title, company, url (the direct apply/detail link), site (domain),
  posted_date (ISO or relative like '3 days ago'),
  location_text (exact text shown), is_remote (true/false),
  job_type (contract / freelance / full-time / part-time / unknown),
  pay_text (exact salary/rate string shown),
  experience_text (exact experience requirement shown),
  description_snippet (first 300 chars of job description).
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
    run_cfg = CrawlerRunConfig(
        extraction_strategy=strategy,
        cache_mode=CacheMode.BYPASS,
        magic=True,
    )
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
                items = parsed if isinstance(parsed, list) else [parsed]
            except (json.JSONDecodeError, TypeError):
                continue

            for job in items:
                if not isinstance(job, dict) or not job.get("title"):
                    continue
                # Dedup by title+company fingerprint
                fp = hashlib.md5(
                    f"{job.get('title','').lower().strip()}|{job.get('company','').lower().strip()}".encode()
                ).hexdigest()
                if fp in seen_fingerprints:
                    continue
                seen_fingerprints.add(fp)
                job["_fingerprint"] = fp
                job["_scraped_at"]  = datetime.datetime.utcnow().isoformat() + "Z"

                # Atomic append to raw NDJSON
                with open(raw_ndjson_path, "a") as f:
                    f.write(json.dumps(job) + "\n")

                all_jobs.append(job)

    print(f"  ✅ {len(all_jobs)} unique job objects scraped (raw saved to {raw_ndjson_path})")
    return all_jobs


# ══════════════════════════════════════════════════════════════════
# PHASE 3 — PURE-PYTHON PRE-FILTER (zero LLM cost)
# ══════════════════════════════════════════════════════════════════

def parse_age_days(posted_date: str) -> Optional[int]:
    """Convert relative date text to number of days ago. Returns None if unparseable."""
    if not posted_date:
        return None
    txt = posted_date.lower().strip()
    m = re.match(r"(\d+)\s*(hour|day|week|month|year)", txt)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit == "hour":  return 0
    if unit == "day":   return n
    if unit == "week":  return n * 7
    if unit == "month": return n * 30
    if unit == "year":  return n * 365
    return None


def parse_hourly_usd(pay_text: str) -> Optional[float]:
    """Best-effort: extract hourly USD rate or derive from fixed budget."""
    if not pay_text:
        return None
    # Match explicit hourly: "$35/hr", "$35 per hour", "35/h"
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:/\s*hr?|per\s+hour)", pay_text, re.I)
    if m:
        return float(m.group(1).replace(",", ""))
    # Match range and take lower: "$30-$60"
    m = re.search(r"\$\s*([\d,]+)\s*[-–]\s*\$?\s*[\d,]+", pay_text)
    if m:
        return float(m.group(1).replace(",", ""))
    # Fixed budget heuristic
    m = re.search(r"\$\s*([\d,]+)", pay_text)
    if m:
        budget = float(m.group(1).replace(",", ""))
        return round(budget / 40, 2)  # conservative implied hourly
    return None


def prefilter(jobs: List[dict]) -> tuple[List[dict], List[dict]]:
    """
    Returns (candidates, rejected) — rejection includes reason.
    Runs entirely in Python with no LLM calls.
    """
    print(f"\n🔬 PHASE 3 — Pre-filter ({len(jobs)} raw jobs)...")
    candidates: List[dict] = []
    rejected:   List[dict] = []

    seen_fp: Set[str] = set()

    for job in jobs:
        title        = (job.get("title") or "").lower()
        company      = (job.get("company") or "").lower()
        location     = (job.get("location_text") or "").lower()
        job_type     = (job.get("job_type") or "unknown").lower().strip()
        pay_text     = (job.get("pay_text") or "")
        experience   = (job.get("experience_text") or "").lower()
        description  = (job.get("description_snippet") or "").lower()
        combined     = f"{title} {description} {location} {experience}"
        fp           = job.get("_fingerprint", "")

        def reject(reason: str):
            job["rejection_reason"] = reason
            rejected.append(job)

        # 1. Duplicate check (cross-phase safety)
        if fp and fp in seen_fp:
            reject("Duplicate job (same title+company seen earlier)"); continue
        if fp:
            seen_fp.add(fp)

        # 2. Freshness check — reject stale listings
        age_days = parse_age_days(job.get("posted_date", ""))
        if age_days is not None and age_days > MAX_POSTING_AGE_DAYS:
            reject(f"Stale listing: posted {age_days}d ago (max {MAX_POSTING_AGE_DAYS}d)"); continue

        # 3. Recruiter / spam block
        if RECRUITER_PATTERN.search(company):
            reject(f"Recruiter/staffing company: {company}"); continue

        # 4. Education filter — reject roles requiring Master's / PhD
        edu_combined = f"{experience} {description}"
        if any(tok in edu_combined for tok in EDUCATION_REJECT_TOKENS):
            reject("Requires advanced degree (Master's/PhD)"); continue

        # 5. Experience level: reject senior roles
        if any(tok in combined for tok in EXPERIENCE_REJECT_TOKENS):
            reject("Requires senior/lead/5+ experience"); continue

        # 6. Remote check
        is_remote_field = job.get("is_remote", False)
        has_remote_text = any(tok in combined for tok in REMOTE_PASS_TOKENS)
        has_onsite_text = any(tok in combined for tok in REMOTE_REJECT_TOKENS)
        if has_onsite_text:
            reject("On-site / relocation required"); continue
        if not is_remote_field and not has_remote_text:
            reject("Not confirmed remote"); continue

        # 7. Geo-lock: role is remote BUT restricted to a specific country
        geo_combined = f"{location} {description}"
        if any(tok in geo_combined for tok in GEO_LOCK_TOKENS):
            reject("Geo-locked: remote restricted to specific country (not worldwide)"); continue

        # 8. Job type filter
        if job_type in BLOCKED_JOB_TYPES:
            # Allow if "remote" is present (some full-time remote contract roles slip through)
            if not has_remote_text:
                reject(f"Job type '{job_type}' not contract/freelance"); continue

        # 9. Pay filter
        hourly = parse_hourly_usd(pay_text)
        if hourly is not None and hourly < MIN_HOURLY_USD:
            reject(f"Pay too low: ${hourly}/hr (min ${MIN_HOURLY_USD})"); continue
        job["pay_hourly_usd"] = hourly
        job["pay_missing"]    = (pay_text == "")

        candidates.append(job)

    print(f"  ✅ {len(candidates)} candidates passed | ❌ {len(rejected)} rejected")
    return candidates, rejected


# ══════════════════════════════════════════════════════════════════
# PHASE 4 — DEEPSEEK V3 EVALUATION + PROPOSAL
# ══════════════════════════════════════════════════════════════════



EVAL_SYSTEM = """You are {name}'s autonomous job agent. The candidate is India-based.

Candidate stack: {stack}
Key metrics to weave into proposals: {metrics}

IMPORTANT LOCATION RULES:
- The candidate requires 100% worldwide remote — they are based in India.
- If a job says "remote" but implies or mentions US/UK/Canada/EU residency, timezone restrictions
  like "must overlap US hours" as a HARD requirement, or work authorization in a specific country,
  set is_match=false with rejection_reason "Geo-locked remote (not worldwide)".
- Roles that say "remote, India" or "remote (any country)" or "worldwide" are fine.

For each job in the list:
1. Set is_match=true only if the role genuinely matches the candidate's AI/Python stack AND is
   accessible from India (worldwide remote or explicitly India-friendly).
   Reject: Java, C++, .NET, UI/UX design, sales, devops-only, 3+ yrs niche experience, geo-locked.
2. If is_match=true:
   - Set match_score (0-100, how well it fits)
   - Write drafted_proposal: a tight 3-paragraph technical proposal.
     Para 1: directly address the job's core requirement with a specific past achievement.
     Para 2: stack fit + relevant tool/framework used.
     Para 3: one concrete metric that proves impact.
3. If is_match=false: set rejection_reason (one concise sentence).

{format_instructions}
"""


def evaluate_and_draft(candidates: List[dict]) -> str:
    if not candidates:
        return json.dumps({"evaluated_jobs": []}, indent=2)

    print(f"\n🧠 PHASE 4 — DeepSeek V3.2 (Thinking Mode) evaluating {len(candidates)} candidates...")

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
    )

    # Build format instructions manually (no LangChain parser needed)
    format_instructions = """
Return ONLY a JSON object (no markdown, no code fences) matching this schema exactly:
{
  "evaluated_jobs": [
    {
      "is_match": true/false,
      "job_title": "string",
      "company": "string",
      "application_url": "string",
      "match_score": 0-100,
      "rejection_reason": "string or null",
      "drafted_proposal": "string or null"
    }
  ]
}"""

    system_prompt = EVAL_SYSTEM.format(
        name=CANDIDATE_PROFILE["name"],
        stack=CANDIDATE_PROFILE["stack"],
        metrics=CANDIDATE_PROFILE["metrics"],
        format_instructions=format_instructions,
    )

    def call_deepseek(batch: List[dict], batch_num: int, total_batches: int) -> List[dict]:
        print(f"  📦 Batch {batch_num}/{total_batches} ({len(batch)} candidates)...")
        text = ""
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",           # deepseek-chat = DeepSeek-V3.2 per official docs
                max_tokens=12000,                # per-batch safe limit for 10 jobs
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Candidate jobs:\n{json.dumps(batch, indent=2)}"},
                ],
                extra_body={"thinking": {"type": "enabled"}},  # V3.2 thinking mode
            )

            # Log the thinking chain (first 30 lines per batch)
            reasoning = getattr(response.choices[0].message, "reasoning_content", None)
            if reasoning:
                lines = reasoning.strip().splitlines()
                print(f"\n  💭 Thinking Chain (batch {batch_num}):")
                print("  " + "-" * 52)
                for line in lines[:30]:
                    print(f"  {line}")
                if len(lines) > 30:
                    print(f"  ... ({len(lines) - 30} more lines truncated)")
                print("  " + "-" * 52 + "\n")

            text = response.choices[0].message.content or ""

            # Strip markdown fences if model wraps anyway
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            data = json.loads(text)
            return data.get("evaluated_jobs", [])

        except Exception as e:
            print(f"  ⚠️ Batch {batch_num} error: {e}")
            if text:
                print("  📄 Raw:\n", text[:500])
            return []

    # ── Batch in chunks of 20 ───────────────────────────────────
    BATCH_SIZE = 10  # 10 jobs × ~1000 tokens/proposal comfortably fits 12k output limit
    all_evaluated: List[dict] = []
    batches = [candidates[i:i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]
    total = len(batches)

    for idx, batch in enumerate(batches, 1):
        results = call_deepseek(batch, idx, total)
        all_evaluated.extend(results)
        print(f"  ✅ Batch {idx}/{total} done — {len(results)} evaluated, running total: {len(all_evaluated)}")

    return json.dumps({"evaluated_jobs": all_evaluated}, indent=2)


# ══════════════════════════════════════════════════════════════════
# DRY-RUN MOCK DATA
# ══════════════════════════════════════════════════════════════════

MOCK_JOBS = [
    {
        "title": "AI Voice Agent Developer", "company": "TechStartup Inc",
        "url": "https://arc.dev/jobs/ai-voice-agent-123",
        "site": "arc.dev", "posted_date": "2 days ago",
        "location_text": "Remote", "is_remote": True, "job_type": "contract",
        "pay_text": "$50/hr", "experience_text": "1-2 years", "description_snippet": "Build LiveKit voice agents using Python and OpenAI APIs.",
    },
    {
        "title": "Senior Lead ML Engineer", "company": "BigCorp",
        "url": "https://linkedin.com/jobs/456",
        "site": "linkedin.com", "posted_date": "1 day ago",
        "location_text": "New York, NY (Onsite)", "is_remote": False, "job_type": "full-time",
        "pay_text": "$180,000/yr", "experience_text": "5+ years", "description_snippet": "Senior lead role requiring 5+ years in enterprise.",
    },
    {
        "title": "AI Voice Agent Developer", "company": "TechStartup Inc",  # duplicate
        "url": "https://arc.dev/jobs/ai-voice-agent-123",
        "site": "arc.dev", "posted_date": "2 days ago",
        "location_text": "Remote", "is_remote": True, "job_type": "contract",
        "pay_text": "$50/hr", "experience_text": "1-2 years", "description_snippet": "Duplicate entry.",
        "_fingerprint": hashlib.md5(b"ai voice agent developer|techstartup inc").hexdigest(),
    },
    {
        "title": "LLM Engineer — RAG Systems", "company": "AI Startup",
        "url": "https://wellfound.com/jobs/789",
        "site": "wellfound.com", "posted_date": "3 days ago",
        "location_text": "Remote / India", "is_remote": True, "job_type": "freelance",
        "pay_text": "$4000 project budget", "experience_text": "Entry level welcome",
        "description_snippet": "Build a RAG pipeline using LangChain and Pinecone for document Q&A.",
    },
    {
        "title": "Python Dev", "company": "Apex Recruitment Agency",
        "url": "https://remoteok.com/jobs/999",
        "site": "remoteok.com", "posted_date": "5 days ago",
        "location_text": "Remote", "is_remote": True, "job_type": "contract",
        "pay_text": "$15/hr", "experience_text": "", "description_snippet": "Python scripting.",
    },
]


# ══════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════

async def main(dry_run: bool = False):
    timestamp    = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    reports_dir  = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(reports_dir, exist_ok=True)

    raw_ndjson   = os.path.join(reports_dir, f"raw_{timestamp}.ndjson")
    rejected_out = os.path.join(reports_dir, f"rejected_{timestamp}.json")
    report_out   = os.path.join(reports_dir, f"report_{timestamp}.json")

    print(f"\n{'='*60}")
    print(f"🚀 AUTONOMOUS JOB SEARCH AGENT  {'[DRY RUN]' if dry_run else '[LIVE]'}")
    print(f"{'='*60}")

    # ── Phase 1 ──────────────────────────────────────────────────
    if dry_run:
        print("\n[DRY RUN] Skipping Serper search — using mock data")
        raw_jobs = MOCK_JOBS
        # Assign fingerprints to mock items that don't have them
        for j in raw_jobs:
            if "_fingerprint" not in j:
                j["_fingerprint"] = hashlib.md5(
                    f"{j['title'].lower()}|{j['company'].lower()}".encode()
                ).hexdigest()
                j["_scraped_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    else:
        urls     = search_for_jobs()
        if not urls:
            print("No URLs found. Exiting."); return
        raw_jobs = await scrape_jobs(urls, raw_ndjson)

    # ── Phase 3 ──────────────────────────────────────────────────
    candidates, rejected = prefilter(raw_jobs)

    with open(rejected_out, "w") as f:
        json.dump(rejected, f, indent=2)
    print(f"  💾 Rejected log → {rejected_out}")

    # ── Phase 4 ──────────────────────────────────────────────────
    if dry_run:
        print("\n[DRY RUN] Skipping DeepSeek evaluation")
        final_json = json.dumps({
            "dry_run": True,
            "candidates_passed_prefilter": len(candidates),
            "candidates": candidates,
        }, indent=2)
    else:
        final_json = evaluate_and_draft(candidates)

    with open(report_out, "w") as f:
        f.write(final_json)

    print(f"\n{'='*60}")
    print("FINAL REPORT")
    print(f"{'='*60}")
    print(final_json[:3000] + ("\n... (truncated, see file)" if len(final_json) > 3000 else ""))
    print(f"\n💾 Report → {report_out}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry_run))
