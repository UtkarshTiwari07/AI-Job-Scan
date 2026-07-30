"""
Autonomous Worldwide Remote Job Search Agent — v11
===================================================
Phase 0: Company source — direct ATS fetch (full JD) + Serper-careers queries for
         a persistent, never-repeating rotation across a 180+ company registry
Phase 1: Serper multi-cluster (per-site + broad free-text) + direct URL injection
Phase 2: Crawl4AI scrape
Phase V: Deterministic pre-filter — dedup, freshness, title/AI-domain/education/
         seniority/years-of-experience, confident geo region-lock rejects. Cheap,
         conservative: only rejects what it's SURE about (job/requirements.py).
Phase X: LLM extracts job facts as STRUCTURED data (years required, location
         policy, role family) — it does NOT judge fit here.
Phase D: Pure-Python deterministic decision over Phase X's extraction
         (job/requirements.py's decide_match) — same input, same verdict, every
         run, unlike letting the LLM both read the JD and rule on it in one step.
Phase L: LLM drafts a proposal — ONLY for confirmed matches from Phase D.

Run:
  python job/job_remote.py                 # full run — prompts for company count
  python job/job_remote.py --companies 20   # full run, skip the prompt
  python job/job_remote.py --dry-run
"""

import asyncio, json, os, re, sys, hashlib, datetime, warnings, requests
from typing import List, Optional, Set
from pydantic import BaseModel
warnings.filterwarnings("ignore", message="urllib3 .* doesn't match a supported version!")

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
from openai import OpenAI

sys.path.insert(0, os.path.dirname(__file__))
try:
    import companies
except ImportError as e:
    companies = None
    print(f"  ⚠️  job/companies.py unavailable ({e}) — the 200-company source is "
          f"skipped this run. `pip install -r requirements.txt` to enable it.")

import candidate_profile as prof
import requirements as req

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERPER_API_KEY   = os.getenv("SERPER_API_KEY")
SEEN_FP_FILE     = os.path.join(os.path.dirname(__file__), "seen_fp_remote.json")
MAX_POSTING_AGE_DAYS = 3   # Serper uses qdr:w, Phase V enforces 3 days

# The candidate profile — generalized in v11. Previously a hardcoded dict
# (CANDIDATE_PROFILE) duplicated, and already drifted, across all three job_*.py
# scripts. Run `python job/init_profile.py` once to personalize; a missing/partial
# config/profile.yaml degrades to the original tool's own defaults.
PROFILE = prof.load_profile()
HOME_PATTERN = req.build_home_pattern(PROFILE)

# Curated AI/ML search queries stay fixed by design (per explicit decision) — a
# résumé changes WHO is searching, never WHAT domain is searched.
TARGET_SITES = [
    "remoteok.com", "weworkremotely.com", "himalayas.app", "remotive.com",
    "wellfound.com", "arc.dev", "contra.com", "braintrust.us", "torre.ai", "linkedin.com",
    "workingnomads.com", "jobspresso.co", "remoterocketship.com",
    "cryptojobslist.com", "web3.career",
]
PORTAL_SITES = [
    "site:boards.greenhouse.io", "site:jobs.lever.co", "site:jobs.ashbyhq.com",
    "site:huggingface.co/jobs", "site:cohere.com/careers", "site:mistral.ai/careers",
    "site:together.ai/careers", "site:modal.com/careers", "site:replicate.com/careers",
    "site:anyscale.com/careers", "site:jobs.workable.com", "site:apply.workable.com",
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
        "terms": '("remote-first startup" OR "async company" OR "distributed team") "AI engineer" OR "LLM platform" ("junior" OR "entry") -"DevOps"',
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
    {
        "name": "C1 — Greenhouse / Lever / Ashby Portals",
        "terms": '"LLM" OR "RAG" OR "voice AI" OR "LangChain" ("fully remote" OR "remote") ("junior" OR "0-2 years" OR "entry")',
        "num": 10, "sites": PORTAL_SITES, "broad": False, "sites_preformatted": True,
    },
    {
        "name": "D1 — Python AI Backend Remote [broad]",
        "terms": '("AI infrastructure engineer" OR "LLM platform engineer" OR "AI backend engineer") ("fully remote" OR "worldwide") ("junior" OR "entry" OR "0-3 years") -"DevOps" -"SRE"',
        "num": 20, "sites": ["_broad_"], "broad": True,
    },
    {
        "name": "A4 — Data Scientist / Forward Deployed Engineer Remote [per-site]",
        "terms": '("data scientist" OR "applied scientist" OR "forward deployed engineer") ("fully remote" OR "worldwide") ("junior" OR "entry" OR "0-2 years") -senior -lead',
        "num": 20, "sites": TARGET_SITES, "broad": False,
    },
]

# Non-AI-stack denylist — cheap, complements (not replaces) req.is_ai_relevant()'s
# positive gate below. This alone was job_remote.py's ONLY content filter in v10,
# which is why non-enumerated non-AI titles ("Solutions Architect," "Accountant")
# reached a paid LLM call; the positive gate in Phase V now closes that hole.
RECRUITER_PATTERN = re.compile(r"\b(recruit|staffing|placement agency|hr solutions|manpower)\b", re.IGNORECASE)
TITLE_REJECT_PATTERNS = re.compile(
    r"(medical writer|medical editor|biostatistic|clinical research|"
    r"data analyst|business analyst|data modeler|mlops|"
    r"data engineer(?!.*ai)|computer vision|cv engineer|"
    r"java\b|\.net\b|android\b|ios\b|devops(?!.*ai)|sysadmin|network engineer|"
    r"blockchain|solidity|frontend developer|support consultant|technical support(?!.*ai))",
    re.IGNORECASE,
)

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
    print("\n🔍 PHASE 1 — Remote Job Search (qdr:w → 3-day filter in Phase V)")
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
    description: str = ""

SCRAPE_INSTRUCTION = """Extract EVERY job posting on this page. For each return:
title, company, url (direct apply link), site (domain),
posted_date (ISO or relative like '3 hours ago' — ALWAYS fill),
location_text, is_remote (true/false), job_type,
pay_text (salary or empty), experience_text (years/level — ALWAYS fill),
description (the FULL job description text — every responsibility, requirement,
and qualification on the page, not a summary or excerpt — ALWAYS fill even if partial).
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
# PHASE V — DETERMINISTIC PRE-FILTER (cheap, conservative rejects only)
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

def prefilter(jobs: List[dict], cross_run_seen: dict) -> tuple:
    print(f"\n🔬 PHASE V — Deterministic pre-filter ({len(jobs)} raw, {len(cross_run_seen)} cross-run known)...")
    candidates: List[dict] = []; rejected: List[dict] = []
    session_seen: Set[str] = set()
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    for job in jobs:
        title   = (job.get("title") or "").strip()
        company = (job.get("company") or "").lower()
        exp     = job.get("experience_text") or ""
        desc    = job.get("description") or ""
        loc     = job.get("location_text") or ""
        fp      = job.get("_fingerprint", "")

        def reject(r): job["rejection_reason"] = r; rejected.append(job)

        if fp and fp in cross_run_seen: reject(f"Already seen ({cross_run_seen[fp][:10]})"); continue
        if fp and fp in session_seen:   reject("Dup in run"); continue
        if fp: session_seen.add(fp)

        # The 3-day freshness window matches Serper's qdr:w "posted in the last
        # week" search results. It does NOT apply to ATS-direct jobs (companies.py):
        # a Greenhouse/Lever/Ashby board lists every CURRENTLY OPEN role regardless
        # of its original post date — a live posting from 3 weeks ago is still a
        # real, applicable job, not a stale one.
        if job.get("_source") != "ats_direct":
            age = parse_age_days(job.get("posted_date", ""))
            if age is None: job["freshness_unknown"] = True
            elif age > MAX_POSTING_AGE_DAYS: reject(f"Stale: {age}d ago"); continue

        if TITLE_REJECT_PATTERNS.search(title): reject(f"Off-stack title: {title}"); continue
        if RECRUITER_PATTERN.search(company): reject(f"Recruiter: {company}"); continue

        # Positive AI/ML-domain gate — v10 had none in this script (its only content
        # filter was the denylist above), so any non-enumerated non-AI title reached
        # a paid LLM call. This closes that hole.
        if not req.is_ai_relevant(title, desc):
            reject(f"No AI/ML relevance in title+desc: {title[:60]}"); continue

        if not req.education_ok(PROFILE, f"{exp} {desc}"):
            reject("Requires more education than profile's education_ceiling"); continue

        # Seniority + years — profile-driven, not hardcoded. classify_seniority()
        # catches titles v10's narrower regex missed (Staff Engineer, Sr. ML
        # Engineer, Engineer III, Member of Technical Staff, ...).
        seniority = req.classify_seniority(title)
        if seniority: reject(seniority); continue

        # THE core accuracy fix: reads experience_text AND the full description
        # (v10 read only experience_text, which no ATS adapter ever populates —
        # this gate was a 100%-dead no-op on every company-page-sourced job).
        yoe_ok, yoe_detail = req.experience_ok(exp, desc, PROFILE["years_experience"], PROFILE["yoe_slack"])
        if not yoe_ok: reject(yoe_detail); continue

        # Confident region-lock rejection only (word-boundary, not v10's
        # decorated-phrase-only substring list that missed bare "Europe"/
        # "Toronto"/"Canada"). A genuinely ambiguous location — the common case —
        # is NOT decided here; it survives to Phase X, which reads the full JD.
        geo_reject = req.pre_kill_location(loc, desc, HOME_PATTERN)
        if geo_reject: reject(geo_reject); continue

        if fp: cross_run_seen[fp] = now_iso
        candidates.append(job)

    print(f"  ✅ {len(candidates)} candidates | ❌ {len(rejected)} rejected")
    return candidates, rejected

# ══════════════════════════════════════════════════════════════════
# PHASE X — LLM STRUCTURED EXTRACTION (facts only, no judgment)
# ══════════════════════════════════════════════════════════════════

EXTRACTION_SYSTEM = """You are a job-posting information extractor. For each job in
the list, extract ONLY facts stated or clearly implied in its text — do NOT judge
whether the candidate is a good fit; that decision happens in a separate step.

The candidate is based in: {location}
Cities that also count as local for onsite/hybrid roles: {home_cities}

For each job return a JSON object with EXACTLY these keys:
- job_title, company: echoed back verbatim (string)
- min_years: integer minimum years of experience the posting requires, or null
  if none is stated
- location_policy: one of "worldwide_remote" (open to a candidate anywhere),
  "country_locked" (remote, but restricted to specific countries/regions), or
  "onsite" (requires physical presence at a specific office/city)
- eligible_countries: array of country names if location_policy is
  "country_locked", else an empty array
- home_eligible: true if a candidate based in {location} could actually take
  this role. Always true for "worldwide_remote". For "onsite"/hybrid roles,
  true ONLY if the office is in {location} or one of the home cities above.
  For "country_locked", true only if {location}'s country is in
  eligible_countries.
- role_family: pick the SINGLE closest match from this list: {target_role_families}.
  Use "other" if none genuinely fit.

Return ONLY valid JSON: {{"extractions":[{{...}}]}}"""

def _slim_for_extraction(job: dict) -> dict:
    # Cap description length — requirement/location language is virtually always
    # near the top of a JD, and this keeps per-batch token cost bounded.
    return {
        "job_title": job.get("title", ""), "company": job.get("company", ""),
        "location_text": job.get("location_text", ""), "is_remote": job.get("is_remote"),
        "experience_text": job.get("experience_text", ""),
        "description": (job.get("description") or "")[:4000],
    }

def extract_requirements(candidates: List[dict]) -> List[dict]:
    if not candidates: return []
    print(f"\n🧠 PHASE X — DeepSeek extracting requirements for {len(candidates)} candidates...")
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    system_prompt = EXTRACTION_SYSTEM.format(
        location=PROFILE["location"],
        home_cities=", ".join(PROFILE.get("home_cities") or []) or "(none listed)",
        target_role_families=", ".join(PROFILE["target_role_families"]))

    def call_ds(batch, bn, total):
        print(f"  📦 Extraction batch {bn}/{total}...")
        text = ""
        try:
            payload = [_slim_for_extraction(c) for c in batch]
            resp = client.chat.completions.create(model="deepseek-chat", max_tokens=8000,
                messages=[{"role":"system","content":system_prompt},
                          {"role":"user","content":f"Jobs:\n{json.dumps(payload, indent=2)}"}])
            text = resp.choices[0].message.content or ""
            if "```json" in text: text=text.split("```json")[1].split("```")[0].strip()
            elif "```" in text: text=text.split("```")[1].split("```")[0].strip()
            return json.loads(text).get("extractions", [])
        except Exception as e:
            print(f"  ⚠️ Extraction batch {bn} error: {e}")
            if text: print("  Raw:", text[:400])
            return []

    batches = [candidates[i:i+10] for i in range(0, len(candidates), 10)]
    all_ext: List[dict] = []
    for idx, batch in enumerate(batches, 1):
        results = call_ds(batch, idx, len(batches))
        all_ext.extend(results)
        print(f"  ✅ {idx}/{len(batches)} — {len(results)} extracted, total: {len(all_ext)}")
    return all_ext

def _match_key(title, company):
    return (str(title or "").strip().lower(), str(company or "").strip().lower())

def pair_extractions(candidates: List[dict], extractions: List[dict]) -> list:
    """Match extraction rows back to candidates by (title, company) — batches
    aren't guaranteed to preserve order or completeness through an LLM call."""
    index = {_match_key(e.get("job_title"), e.get("company")): e for e in extractions}
    return [(c, index.get(_match_key(c.get("title"), c.get("company")))) for c in candidates]

# ══════════════════════════════════════════════════════════════════
# PHASE D — DETERMINISTIC DECISION (pure Python, no LLM call)
# ══════════════════════════════════════════════════════════════════

def decide_all(pairs: list) -> tuple:
    print(f"\n📐 PHASE D — deciding {len(pairs)} candidates against the profile...")
    matches, non_matches = [], []
    for job, extraction in pairs:
        if extraction is None:
            job["rejection_reason"] = "extraction failed or missing (LLM did not return this job)"
            non_matches.append(job)
            continue
        ok, reason = req.decide_match(PROFILE, extraction)
        job["_extraction"] = extraction
        if ok:
            matches.append(job)
        else:
            job["rejection_reason"] = reason
            non_matches.append(job)
    print(f"  ✅ {len(matches)} match | ❌ {len(non_matches)} rejected")
    return matches, non_matches

# ══════════════════════════════════════════════════════════════════
# PHASE L — LLM PROPOSAL DRAFTING (matches only — tokens never spent on rejects)
# ══════════════════════════════════════════════════════════════════

DRAFT_SYSTEM = """You are {name}'s job-application assistant. The candidate ALREADY
qualifies for every job below — experience, location, and role fit are already
confirmed by a separate step. Your ONLY task is to draft a tight, specific
proposal for each.

Stack: {stack}
Metrics: {metrics}

For each job return: job_title, company, application_url, match_score (0-100 —
rate how STRONG a fit this is given the stack/metrics, not eligibility, which is
already confirmed), drafted_proposal (3 short paragraphs: an achievement that
addresses this company's specific need → stack fit → one concrete metric + a
call to interview).

Return ONLY valid JSON: {{"drafted":[{{"job_title":"string","company":"string",
"application_url":"string","match_score":0-100,"drafted_proposal":"string"}}]}}"""

def draft_proposals(matches: List[dict]) -> List[dict]:
    if not matches: return []
    print(f"\n✍️  PHASE L — Drafting proposals for {len(matches)} confirmed matches...")
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    system_prompt = DRAFT_SYSTEM.format(name=PROFILE["name"], stack=PROFILE["stack"], metrics=PROFILE["metrics"])

    def call_ds(batch, bn, total):
        print(f"  📦 Drafting batch {bn}/{total}...")
        text = ""
        try:
            payload = [{"job_title": j.get("title"), "company": j.get("company"),
                       "application_url": j.get("url"), "description": (j.get("description") or "")[:3000]}
                      for j in batch]
            resp = client.chat.completions.create(model="deepseek-chat", max_tokens=8000,
                messages=[{"role":"system","content":system_prompt},
                          {"role":"user","content":f"Jobs:\n{json.dumps(payload, indent=2)}"}])
            text = resp.choices[0].message.content or ""
            if "```json" in text: text=text.split("```json")[1].split("```")[0].strip()
            elif "```" in text: text=text.split("```")[1].split("```")[0].strip()
            return json.loads(text).get("drafted", [])
        except Exception as e:
            print(f"  ⚠️ Drafting batch {bn} error: {e}")
            if text: print("  Raw:", text[:400])
            return []

    batches = [matches[i:i+10] for i in range(0, len(matches), 10)]
    all_drafted: List[dict] = []
    for idx, batch in enumerate(batches, 1):
        results = call_ds(batch, idx, len(batches))
        all_drafted.extend(results)
        print(f"  ✅ {idx}/{len(batches)} — {len(results)} drafted")
    return all_drafted

def build_evaluated_jobs(matches: List[dict], non_matches: List[dict], drafted: List[dict]) -> list:
    """Same report schema as v10's single-call evaluate_and_draft(), so existing
    tooling/readers of report_remote_*.json don't break — plus `extraction` on
    matches for anyone who wants the raw structured facts."""
    drafted_index = {_match_key(d.get("job_title"), d.get("company")): d for d in drafted}
    evaluated = []
    for job in matches:
        d = drafted_index.get(_match_key(job.get("title"), job.get("company")), {})
        evaluated.append({
            "is_match": True, "job_title": job.get("title"), "company": job.get("company"),
            "application_url": job.get("url"), "match_score": d.get("match_score", 70),
            "rejection_reason": None,
            "drafted_proposal": d.get("drafted_proposal") or "(drafting failed — review the JD and apply manually)",
            "extraction": job.get("_extraction"),
        })
    for job in non_matches:
        evaluated.append({
            "is_match": False, "job_title": job.get("title"), "company": job.get("company"),
            "application_url": job.get("url"), "match_score": 0,
            "rejection_reason": job.get("rejection_reason"), "drafted_proposal": None,
        })
    return evaluated

# ══════════════════════════════════════════════════════════════════
# MOCK + MAIN
# ══════════════════════════════════════════════════════════════════

MOCK_JOBS = [
    {"title":"AI Voice Engineer","company":"Remote-First AI Co","url":"https://jobs.ashbyhq.com/ai-voice-123","site":"jobs.ashbyhq.com","posted_date":"2 hours ago","location_text":"Worldwide Remote","is_remote":True,"job_type":"full-time","pay_text":"$80-120k/yr","experience_text":"1-2 years","description":"Build voice pipelines with LiveKit, Deepgram, ElevenLabs. FastAPI backend."},
    {"title":"Senior ML Engineer","company":"BigCorp","url":"https://jobs.lever.co/senior-ml","site":"jobs.lever.co","posted_date":"2 days ago","location_text":"Remote (US Only)","is_remote":True,"job_type":"full-time","pay_text":"$180k/yr","experience_text":"5+ years","description":"Senior ML, US timezone required."},
    {"title":"LLM Platform Engineer","company":"Cohere","url":"https://cohere.com/careers/llm","site":"cohere.com","posted_date":"5 hours ago","location_text":"Remote — Worldwide","is_remote":True,"job_type":"full-time","pay_text":"$90-130k/yr","experience_text":"1-2 years","description":"Production LLM APIs, RAG, fine-tuning pipelines."},
    {"title":"AI Agent Engineer","company":"Replicate","url":"https://replicate.com/careers/ai-agent","site":"replicate.com","posted_date":"1 day ago","location_text":"Fully Remote","is_remote":True,"job_type":"full-time","pay_text":"$100k/yr","experience_text":"0-3 years","description":"Agentic workflows using LangChain, CrewAI. Python backend. Entry-level welcome."},
    {"title":"Data Scientist","company":"Groww","url":"https://groww.in/careers/data-scientist","site":"groww.in","posted_date":"3 hours ago","location_text":"Bengaluru, India (Onsite)","is_remote":False,"job_type":"full-time","pay_text":"₹18-25 LPA","experience_text":"1-2 years","description":"Build ML models for credit risk and fraud detection using PyTorch and LightGBM."},
    {"title":"Forward Deployed Engineer","company":"Palantir-style AI Co","url":"https://jobs.ashbyhq.com/fde-456","site":"jobs.ashbyhq.com","posted_date":"6 hours ago","location_text":"Remote — Worldwide","is_remote":True,"job_type":"full-time","pay_text":"$95-140k/yr","experience_text":"1-3 years","description":"Embed with customers to deploy LLM-powered workflows using Python and FastAPI."},
    {"title":"ML Engineer","company":"EuroAI GmbH","url":"https://jobs.lever.co/euro-ml","site":"jobs.lever.co","posted_date":"1 day ago","location_text":"Remote (EU Only)","is_remote":True,"job_type":"full-time","pay_text":"€70k/yr","experience_text":"1-2 years","description":"Region-locked despite otherwise fitting the stack — should be rejected on location alone."},
    {"title":"Staff Software Engineer, AI Platform","company":"BigAI Corp","url":"https://jobs.ashbyhq.com/staff-789","site":"jobs.ashbyhq.com","posted_date":"4 hours ago","location_text":"Remote — Worldwide","is_remote":True,"job_type":"full-time","pay_text":"$220k/yr","experience_text":"","description":"8+ years of experience building large-scale ML infrastructure. Staff-level IC role."},
]

def _companies_arg() -> Optional[int]:
    """--companies N on the command line skips the interactive prompt (for cron/CI)."""
    if "--companies" in sys.argv:
        i = sys.argv.index("--companies")
        if i + 1 < len(sys.argv):
            try: return int(sys.argv[i + 1])
            except ValueError: pass
    return None

async def main(dry_run: bool = False):
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    rdir = os.path.join(os.path.dirname(__file__), "reports_remote")
    os.makedirs(rdir, exist_ok=True)
    raw_ndjson   = os.path.join(rdir, f"raw_remote_{ts}.ndjson")
    rejected_out = os.path.join(rdir, f"rejected_remote_{ts}.json")
    report_out   = os.path.join(rdir, f"report_remote_{ts}.json")

    print(f"\n{'='*60}")
    print(f"🚀 WORLDWIDE REMOTE JOB SEARCH v11  {'[DRY RUN]' if dry_run else '[LIVE — 3-day window]'}")
    print(f"👤 Profile: {PROFILE['name']} | {PROFILE['years_experience']} YOE (+{PROFILE['yoe_slack']} slack) "
          f"| {PROFILE['location']} | roles: {', '.join(PROFILE['target_role_families'])}")
    print(f"{'='*60}")

    cross_run_seen = load_seen_fingerprints()
    print(f"  📦 Cross-run cache: {len(cross_run_seen)} fingerprints")

    company_manifest = []
    pool_total = pool_remaining = None

    if dry_run:
        print("\n[DRY RUN] Using mock data")
        raw_jobs = MOCK_JOBS
        for j in raw_jobs:
            if "_fingerprint" not in j:
                j["_fingerprint"] = hashlib.md5(f"{j['title'].lower()}|{j['company'].lower()}".encode()).hexdigest()
                j["_scraped_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    else:
        # PHASE 0 — company source: ATS-direct (full JD already, no crawl needed) +
        # Serper-careers (feeds the existing Crawl4AI path below). Selection is a
        # persistent, never-repeating rotation across the whole registry — see
        # companies.select_companies()'s docstring. Every company scanned (incl.
        # zero-yield ones) is recorded in company_manifest and written into the
        # report — v10 had NO record anywhere of which companies actually ran.
        ats_jobs, company_urls = [], []
        if companies:
            n_companies = _companies_arg()
            if n_companies is None:
                n_companies = companies.prompt_company_count()
            pool_total, pool_remaining = companies.pool_status()
            if n_companies:
                ats_batch, serper_batch = companies.select_companies(n_companies)
                print(f"\n🏢 PHASE 0 — Company source: {len(ats_batch)} ATS-direct + "
                      f"{len(serper_batch)} via Serper-careers ({n_companies} requested, "
                      f"cycle progress before this run: {pool_total - pool_remaining}/{pool_total})")
                if ats_batch:
                    ats_jobs, ats_manifest = companies.fetch_ats_jobs(ats_batch)
                    company_manifest.extend(ats_manifest)
                if serper_batch:
                    company_urls, serper_manifest = companies.serper_careers_urls(serper_batch, SERPER_API_KEY)
                    company_manifest.extend(serper_manifest)
                # Commit the rotation cursor only NOW, after the fetch attempts
                # actually ran — v10 committed inside select_companies(), before
                # any fetch happened, so a crash or a missing SERPER_API_KEY still
                # permanently marked those companies done with zero jobs produced.
                companies.mark_companies_done(company_manifest)
                pool_total, pool_remaining = companies.pool_status()
            else:
                print("\n🏢 PHASE 0 — 0 companies requested this run; skipping the company source.")
        else:
            print("\n🏢 PHASE 0 — job/companies.py unavailable; skipping the company source.")

        urls = search_for_jobs() + company_urls
        if not urls and not ats_jobs: print("No URLs. Exiting."); return
        scraped_jobs = await scrape_jobs(urls, raw_ndjson) if urls else []
        # ATS jobs bypass Crawl4AI (they already carry a full JD) but v10 never
        # wrote them to raw_ndjson at all — a PASSING ats job left no raw record
        # anywhere. Append them here so the raw dump reflects every source.
        if ats_jobs:
            with open(raw_ndjson, "a") as f:
                for j in ats_jobs: f.write(json.dumps(j, default=str) + "\n")
        raw_jobs = scraped_jobs + ats_jobs

    candidates, rejected = prefilter(raw_jobs, cross_run_seen)
    with open(rejected_out, "w") as f: json.dump(rejected, f, indent=2, default=str)
    print(f"  💾 Rejected → {rejected_out}")
    if not dry_run: save_seen_fingerprints(cross_run_seen)

    if dry_run:
        result = {"dry_run": True, "candidates_passed_prefilter": len(candidates), "candidates": candidates}
    else:
        extractions = extract_requirements(candidates)
        pairs = pair_extractions(candidates, extractions)
        matches, non_matches = decide_all(pairs)
        drafted = draft_proposals(matches)
        evaluated_jobs = build_evaluated_jobs(matches, non_matches, drafted)
        result = {
            "evaluated_jobs": evaluated_jobs,
            "run_manifest": {
                "companies_scanned": company_manifest,
                "cycle_progress": (f"{pool_total - pool_remaining}/{pool_total}"
                                   if pool_total is not None else None),
                "funnel": {"raw_jobs": len(raw_jobs), "prefilter_passed": len(candidates),
                          "extracted": len(extractions), "matched": len(matches),
                          "reported": len(matches)},
            },
        }
    final_json = json.dumps(result, indent=2, default=str)
    with open(report_out, "w") as f: f.write(final_json)
    print(f"\n{'='*60}\nFINAL REPORT\n{'='*60}")
    print(final_json[:3000] + ("\n... (truncated)" if len(final_json)>3000 else ""))
    print(f"\n💾 Report → {report_out}")

if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
