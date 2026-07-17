"""
pipeline.py — the v2 orchestrator for remote + india_mnc modes.

Flow:  registry.fetch_all  →  filters.prefilter  →  LLM evaluate + draft  →
split into report_*.json (real matches only) + audit_*.json (everything
rejected, with reason) + raw_*.ndjson, and print a run summary.

The report/audit split is decided in Python (not trusted to the LLM): a job is
reported only if is_match AND score>=report_min_score AND its required experience
is within the candidate's bracket AND location_ok. That is what makes "every
reported job is a genuine >=50% match within my experience bracket" true.
"""

import os
import re
import sys
import json
import datetime
from collections import Counter
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(HERE), ".env"))

from jobscan_config import load_config
import registry
import filters
import discovery
import sources
import jobscan_llm
import jobspy_source

EVAL_BATCH = 4
SEARCH_TEXT = "AI engineer OR machine learning engineer OR LLM engineer"
LINKEDIN_MAX_NEW_COMPANIES = 40  # cap company_resolve probes per run
SERPER_JOB_FETCH_CAP = 250       # bound worst-case per-job network calls per run
BOARD_ROOT_SELECT_CAP = 3        # cap on jobs kept from a D1 board-root hit
NCR_BATCH_SIZE = 10              # v7: NCR companies targeted per run (rotates daily)

# v6 — the PRIMARY discovery net: job-level (not board-level) Serper queries, built
# at runtime from the candidate's own target roles. A live probe this session
# showed these return per-job posting URLs 70-90% of the time — the pool is
# role-matched before anything is fetched, instead of hoping the right job is
# buried in a 300-job board.
ATS_JOB_SITES = ["boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co",
                 "jobs.ashbyhq.com", "apply.workable.com"]
JUNIOR_QUERY_VARIANTS = [
    '"junior" OR "entry level" OR "0-2 years"',
    '"entry level" OR "fresher" OR "early career"',
    '"0-1 year" OR "new grad" OR "graduate"',
]
DEFAULT_TARGET_ROLES = ["AI engineer", "machine learning engineer", "LLM engineer", "RAG engineer"]


def _build_serper_job_queries(cfg, mode):
    """Runtime job-level Serper queries: profile.target_roles x junior-phrasing
    variants x ATS site restriction x mode geo. Extended by any queries the user
    adds under config/<mode>.yaml's `serper_job_queries`."""
    roles = cfg.target_roles or DEFAULT_TARGET_ROLES
    role_clause = "(" + " OR ".join(f'"{r}"' for r in roles[:5]) + ")"
    geo = "India" if mode == "india_mnc" else "remote"
    queries = [f"{role_clause} ({jv}) {geo} site:{site}"
               for site in ATS_JOB_SITES for jv in JUNIOR_QUERY_VARIANTS]
    return queries + list(cfg.serper_job_queries or [])


def _company_name_from_token(token: str) -> str:
    """Best-effort display name for a company only known by its ATS token (D1
    per-job / board-root hits don't carry a real company name)."""
    return re.sub(r"[-_]+", " ", token or "").strip().title() or token


def _guess_company_from_hit(hit: dict):
    """Best-effort (name, token) for a Serper hit whose URL matched no known ATS
    pattern — used only as a display label for the crawl4ai-enrichment stub; the
    reported link is always the hit's own URL, never a guess."""
    domain = urlparse(hit["url"]).netloc.replace("www.", "") or "unknown"
    title = hit.get("title") or ""
    for sep in (" at ", " - ", " | "):
        if sep in title:
            candidate = title.rsplit(sep, 1)[-1].strip()
            if 2 <= len(candidate) <= 60:
                return candidate, domain
    return domain, domain


def _ncr_search_batch(companies: list, batch_size: int = NCR_BATCH_SIZE) -> list:
    """v7 — most Delhi NCR tech companies (Housing.com-class) have no clean ATS API
    (live probe this session: 3/40 did) — the only way to reach them is a
    company-targeted board search. Rotates through the full list a bounded batch
    at a time (deterministic by day-of-year, no extra state file needed) so a run
    issues ~10 targeted searches, not one per company every time."""
    n = len(companies)
    if not n:
        return []
    start = (datetime.date.today().toordinal() * batch_size) % n
    if start + batch_size <= n:
        return companies[start:start + batch_size]
    return companies[start:] + companies[:(start + batch_size) - n]


def _build_ncr_searches(cfg) -> list:
    """Company-targeted JobSpy searches for the NCR companies with no clean ATS —
    the user's exact ask ('ai engineer job company name = housing.com'). Indeed
    supports the company name directly in its own search box; Google Jobs'
    `google_search_term` is a natural-language query built the same way — both are
    tried per company since either can miss depending on how each board indexed it."""
    batch = _ncr_search_batch(cfg.ncr_target_companies)
    role = (cfg.target_roles or DEFAULT_TARGET_ROLES)[0]
    searches = []
    for company in batch:
        searches.append({"sites": ["indeed"], "search_term": f"{role} {company}",
                         "location": "Delhi, India", "country_indeed": "India",
                         "results_wanted": 5, "hours_old": 720})
        searches.append({"sites": ["google"], "search_term": role,
                         "google_search_term": f"{role} jobs at {company} Delhi NCR",
                         "results_wanted": 5})
    return searches


# ─────────────────────── cross-run dedup cache ──────────────────────

def _cache_path(mode):
    return os.path.join(HERE, f"seen_fp_{mode}.json")


def load_seen(mode):
    p = _cache_path(mode)
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            data = json.load(f)
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat()
        return {fp: ts for fp, ts in data.items() if ts >= cutoff}
    except Exception:
        return {}


def save_seen(mode, fp_map):
    try:
        with open(_cache_path(mode), "w") as f:
            json.dump(fp_map, f)
    except Exception as e:
        print(f"  ⚠️ cache save error: {e}")


# ─────────────────────────── Phase 4 ────────────────────────────────

def evaluate_and_draft(candidates, cfg):
    """Returns (report_rows, audit_rows). report_rows are genuine matches only."""
    if not candidates:
        return [], []

    model = jobscan_llm.get_model()
    print(f"\n🧠 PHASE 4 — {model} evaluating {len(candidates)} candidates...")
    p = cfg.profile
    system_prompt = cfg.eval_system.format(
        name=p.get("name", ""), stack=p.get("stack", ""), metrics=p.get("metrics", ""),
        location=p.get("location", ""), experience_years=cfg.experience_years,
        min_rate=p.get("min_rate", ""), format_instructions=cfg.format_instructions,
    )
    bracket_max = filters.candidate_bracket(cfg)
    jd_chars = cfg.jd_eval_chars

    report, audit = [], []
    batches = [candidates[i:i + EVAL_BATCH] for i in range(0, len(candidates), EVAL_BATCH)]
    for bi, batch in enumerate(batches, 1):
        by_url = {c["url"]: c for c in batch}
        by_tc = {(c["title"].lower(), c["company"].lower()): c for c in batch}
        payload = [{
            "job_title": c["title"], "company": c["company"], "application_url": c["url"],
            "location": c["location_text"], "jd_text": (c["jd_text"] or "")[:jd_chars],
        } for c in batch]

        rows, text = [], ""
        try:
            text, _ = jobscan_llm.chat_completion(
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": f"Jobs:\n{json.dumps(payload, indent=2)}"}],
                max_tokens=14000)
            text = (text or "").strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            rows = json.loads(text).get("evaluated_jobs", [])
        except Exception as e:
            print(f"  ⚠️ batch {bi}/{len(batches)} error: {e}")
            if text:
                print("  raw:", text[:300])
            for c in batch:  # fail closed: unresolved → audit, never reported
                audit.append({**_slim(c), "rejection_reason": f"eval failed: {e}"})
            continue

        matched_urls = set()
        for row in rows:
            cand = (by_url.get(row.get("application_url"))
                    or by_tc.get((str(row.get("job_title", "")).lower(),
                                  str(row.get("company", "")).lower())))
            if cand is None:
                continue
            matched_urls.add(cand["url"])
            llm_min = row.get("min_years") or 0
            eff_min = max(int(cand.get("_yoe_min") or 0), int(llm_min) if str(llm_min).isdigit() else 0)
            score = row.get("match_score") or 0
            reasons = []
            if not row.get("is_match"):
                reasons.append(row.get("rejection_reason") or "LLM: not a match")
            if score < cfg.report_min_score:
                reasons.append(f"score {score} < {cfg.report_min_score}")
            if eff_min > bracket_max:
                reasons.append(f"needs {eff_min}+ yrs (bracket {bracket_max})")
            if row.get("location_ok") is False:
                reasons.append("location not accessible")
            entry = {**_slim(cand), "match_score": score, "min_years": eff_min,
                     "seniority": row.get("seniority", ""),
                     "drafted_proposal": row.get("drafted_proposal")}
            if reasons:
                entry["rejection_reason"] = "; ".join(reasons)
                entry.pop("drafted_proposal", None)
                audit.append(entry)
            else:
                # Carry the fingerprint through so pipeline.run can mark ONLY this
                # reported job seen for next run — popped again before the report
                # is written to disk (see _pop_fingerprints), so it never appears
                # in report_*.json.
                entry["_fingerprint"] = cand.get("_fingerprint")
                report.append(entry)
        for c in batch:  # any candidate the model silently dropped → audit
            if c["url"] not in matched_urls:
                audit.append({**_slim(c), "rejection_reason": "not returned by evaluator"})
        print(f"  ✅ batch {bi}/{len(batches)} — {sum(1 for r in report)} reported so far")

    report.sort(key=lambda r: r.get("match_score", 0), reverse=True)
    return report, audit


def _slim(c):
    return {"job_title": c["title"], "company": c["company"], "application_url": c["url"],
            "location": c["location_text"], "source": c.get("source", ""),
            "tier": c.get("tier"), "posted_date": c.get("posted_date", "")}


def _pop_reported_fingerprints(report: list) -> set:
    """v7 dedup fix: extract the fingerprint of every REPORTED job (popping the key
    so it never appears in report_*.json) — this is the only set that should ever
    be written to the cross-run seen-cache. Marking every candidate as seen (the
    v6-and-earlier behaviour) meant re-running the tool while tuning it silently
    drained the whole candidate pool; a job the user never actually saw stays
    eligible next run."""
    fps = set()
    for r in report:
        fp = r.pop("_fingerprint", None)
        if fp:
            fps.add(fp)
    return fps


# ─────────────────────────── run ────────────────────────────────────

def _enrichable(cfg):
    """Predicate for Phase E: only spend a crawl on a job whose TITLE already marks
    it AI/ML and isn't off-stack, so crawl budget is never wasted on a job that
    Phase V would title-reject anyway."""
    def ok(job):
        title = job.get("title") or ""
        if cfg.title_reject_re and cfg.title_reject_re.search(title):
            return False
        return bool(cfg.title_include_re and cfg.title_include_re.search(title))
    return ok


def _run_serper_job_discovery(mode, cfg, serper_key, known_tokens):
    """Phase D1 (v6, PRIMARY net) — job-level Serper discovery. Returns (jobs, stats).
    Three hit shapes, each handled to avoid ever ingesting a whole board blind:
      * per-job hit  → sources.fetch_job_by_ref: ONE job, full JD, no board download.
      * board-root hit → fetch that one board, keep only sources.select_relevant
        (cap BOARD_ROOT_SELECT_CAP) — never the whole board.
      * unresolved URL → a thin stub; Phase E's crawl4ai enrichment (already wired
        in run()) fetches its real detail-page text, or it's honestly dropped as
        unverifiable — this is the "why aren't you scraping" answer: we do, on the
        job's own detail page, never on a search/listing page.
    """
    stats = {"hits": 0, "per_job": 0, "board_root": 0, "unresolved_stub": 0, "fetch_calls": 0}
    if not serper_key:
        return [], stats
    queries = _build_serper_job_queries(cfg, mode)
    hits = discovery.serper_job_discover(queries, serper_key)
    stats["hits"] = len(hits)

    jobs = []
    seen_job_urls, board_done = set(), set()
    for h in hits:
        if h.get("job_id"):
            if h["url"] in seen_job_urls or stats["fetch_calls"] >= SERPER_JOB_FETCH_CAP:
                continue
            seen_job_urls.add(h["url"])
            stats["fetch_calls"] += 1
            job = sources.fetch_job_by_ref(h["ats"], h["token"], h["job_id"], h["url"])
            if job:
                name = _company_name_from_token(h["token"])
                registry.tag_jobs([job], name, h["token"], tier=1, tags=["serper-job"])
                jobs.append(job)
                stats["per_job"] += 1
        elif h.get("ats"):
            board_key = (h["ats"], h["token"])
            if board_key in board_done or h["token"] in known_tokens:
                continue
            board_done.add(board_key)
            try:
                board_jobs = sources.ADAPTERS[h["ats"]]({"token": h["token"]})
            except Exception:
                board_jobs = []
            selected = sources.select_relevant(board_jobs, cfg, cap=BOARD_ROOT_SELECT_CAP)
            if selected:
                name = _company_name_from_token(h["token"])
                registry.tag_jobs(selected, name, h["token"], tier=1, tags=["serper-board"])
                jobs.extend(selected)
                stats["board_root"] += 1
        else:
            if cfg.title_reject_re and cfg.title_reject_re.search(h.get("title") or ""):
                continue  # obviously off-stack even from the search snippet — skip
            name, token = _guess_company_from_hit(h)
            stub = sources._job(title=h.get("title") or "", url=h["url"], jd_text="", source="serper_unresolved")
            registry.tag_jobs([stub], name, token, tier=3, tags=["serper-unresolved"])
            jobs.append(stub)
            stats["unresolved_stub"] += 1
    return jobs, stats


def discover_and_fetch(mode, cfg, serper_key):
    """Phase D (v7) — job-first, not company-first: D0 (JobSpy — Indeed/Naukri/
    LinkedIn/Google, the actual market volume) and D1 (job-level Serper search)
    are the primary nets; the watchlist registry, LinkedIn-name resolution,
    RemoteOK and HN are secondary and all selection-gated (never whole-board
    ingestion). Merges every source into one pool, tagged by source
    (funnel['per_source']) so an empty report is diagnosable, not a mystery."""
    all_jobs = []
    funnel = {"per_source": {}, "dead_boards": [], "no_ai_roles_boards": [],
              "linkedin_companies_found": 0, "linkedin_companies_resolved": [],
              "serper_d1": {}, "jobspy_error": None, "jobspy_searches_run": 0}

    def add(jobs, label):
        all_jobs.extend(jobs)
        funnel["per_source"][label] = funnel["per_source"].get(label, 0) + len(jobs)

    known_tokens = {c.get("token") or c.get("tenant") for c in cfg.companies}

    # D0 — primary VOLUME source: real job boards (Indeed/Naukri/LinkedIn/Google)
    # via python-jobspy. This reaches the actual market — including companies with
    # no clean ATS API at all (most of Delhi NCR, live-verified: 37/40 probed) —
    # which no amount of registry curation or ATS-only discovery ever could.
    searches = list(cfg.jobspy_searches or [])
    if mode == "india_mnc" and cfg.ncr_target_companies:
        searches += _build_ncr_searches(cfg)
    if searches:
        by_site, jobspy_err = jobspy_source.fetch_jobspy(searches)
        funnel["jobspy_searches_run"] = len(searches)
        if jobspy_err:
            funnel["jobspy_error"] = jobspy_err
            print(f"  ⚠️  {jobspy_err}")
        for site_label, site_jobs in by_site.items():
            registry.tag_multi_company_jobs(site_jobs, tier=2, tags=["jobspy", site_label])
            add(site_jobs, site_label)

    # D1 — job-level Serper discovery (see _run_serper_job_discovery).
    if serper_key:
        d1_jobs, d1_stats = _run_serper_job_discovery(mode, cfg, serper_key, known_tokens)
        add(d1_jobs, "serper_job_discovery")
        funnel["serper_d1"] = d1_stats

    # D4 — watchlist registry: registry.fetch_all now applies select_relevant per
    # board (cfg.watchlist_cap_per_board), so a giant board can contribute at most
    # a handful of AI/ML roles, never its whole size.
    if cfg.sources.get("ats", True) and cfg.companies:
        reg_jobs, reg_summary = registry.fetch_all(cfg.companies, cfg, search_text=SEARCH_TEXT,
                                                    serper_key=serper_key)
        add(reg_jobs, "ats_registry")
        funnel["dead_boards"] = reg_summary.get("dead", [])
        funnel["no_ai_roles_boards"] = reg_summary.get("no_ai_roles", [])
        funnel["ats_raw_fetched"] = reg_summary.get("raw_fetched", 0)
    elif not cfg.companies:
        print(f"  ⚠️  No companies in config/{cfg.companies_file} — run: python job/probe_registry.py")

    # D2 — LinkedIn: company names only; resolve_and_fetch_new now selects only
    # title-matching jobs per resolved board (cap 3), never the whole board.
    if cfg.sources.get("linkedin", True) and cfg.linkedin_queries:
        print("  🔎 LinkedIn discovery (company names only — never a reported URL)...")
        by_company = discovery.linkedin_discover_companies(cfg.linkedin_queries, max_pages_per_query=2)
        funnel["linkedin_companies_found"] = len(by_company)
        li_jobs, li_summary = discovery.resolve_and_fetch_new(
            set(by_company.keys()), known_tokens, cfg, cap=LINKEDIN_MAX_NEW_COMPANIES,
            serper_key=serper_key, select_cap=3)
        add(li_jobs, "linkedin_resolved")
        funnel["linkedin_companies_resolved"] = li_summary["resolved"]
        known_tokens |= {r.split("->", 1)[1].split(":", 1)[1] for r in li_summary["resolved"]}

    # D3 — RemoteOK + HN: already job-targeted feeds with full JDs, unchanged.
    if cfg.sources.get("remoteok", False):
        rok = registry.tag_multi_company_jobs(sources.fetch_remoteok(), tier=2, tags=["remoteok"])
        add(rok, "remoteok")

    if cfg.sources.get("hn", False):
        hn = registry.tag_multi_company_jobs(sources.fetch_hn_whoishiring(), tier=2, tags=["hn"])
        add(hn, "hn_whoishiring")

    return all_jobs, funnel


def _cap_eval_slots(ranked: list, cfg) -> tuple:
    """Phase R structural anti-flooding: at most `cfg.eval_slots_per_company` of the
    top eval slots go to any one company, so the LLM eval budget can never be
    dominated by a few giant boards — this is what let a handful of companies eat
    a live run's entire top-60 while the LLM correctly rejected all of them.

    Round-robin fill: each company gets its best-ranked job first, one round at a
    time, up to the per-company cap; only once every company has hit the cap do
    later rounds widen it — and even then, EVERY company with remaining supply gets
    its next slot before any company gets a second "over-cap" slot. This matters:
    a naive "fill the cap, then backfill leftovers in rank order" approach dumps
    every overflow slot on whichever company is ranked highest — usually the very
    flooder the cap exists to stop (its leftover jobs sit first in the list simply
    because it has so many of them). Round-robin only lets a company exceed the cap
    when the market genuinely lacks enough OTHER companies to fill the budget.
    Returns (to_eval, over_cap) — over_cap keeps the original rank order for audit.
    """
    per_company = max(1, int(getattr(cfg, "eval_slots_per_company", 2)))
    budget = int(cfg.eval_max_candidates)

    by_company, order = {}, []
    for j in ranked:
        token = j.get("company_token") or j.get("company", "")
        if token not in by_company:
            by_company[token] = []
            order.append(token)
        by_company[token].append(j)

    to_eval = []
    counts = {t: 0 for t in order}
    round_cap = per_company
    while len(to_eval) < budget:
        progressed = False
        for token in order:
            if len(to_eval) >= budget:
                break
            bucket = by_company[token]
            if counts[token] < round_cap and counts[token] < len(bucket):
                to_eval.append(bucket[counts[token]])
                counts[token] += 1
                progressed = True
        if not progressed:
            break
        round_cap += 1

    to_eval_ids = {id(j) for j in to_eval}
    over_cap = [j for j in ranked if id(j) not in to_eval_ids]
    return to_eval, over_cap


def run(mode, dry_run=False, fresh=False):
    cfg = load_config(mode)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    rdir = os.path.join(HERE, f"reports_{mode}")
    os.makedirs(rdir, exist_ok=True)
    raw_path = os.path.join(rdir, f"raw_{mode}_{ts}.ndjson")
    report_path = os.path.join(rdir, f"report_{mode}_{ts}.json")
    audit_path = os.path.join(rdir, f"audit_{mode}_{ts}.json")

    print(f"\n{'='*64}\n🚀 {mode.upper()} scan v3  {'[DRY RUN]' if dry_run else '[LIVE]'}"
          f"{'  [--fresh: ignoring seen-cache]' if fresh else ''}\n{'='*64}")

    # --fresh ignores the on-disk seen-cache for THIS run (useful while tuning) but
    # still gets overwritten below with whatever's actually reported this run.
    cross_run_seen = {} if fresh else load_seen(mode)
    funnel = {}
    if dry_run:
        print("[DRY RUN] using mock ATS data (no network, no LLM)")
        jobs = _mock_jobs(mode)
    else:
        serper_key = os.getenv("SERPER_API_KEY", "")
        print(f"\n📡 PHASE D — discovery ("
              f"{'JobSpy (Indeed/Naukri/LinkedIn/Google)' if cfg.jobspy_searches or cfg.ncr_target_companies else 'JobSpy OFF (no jobspy_searches configured)'}"
              f" + {'job-first Serper search' if serper_key else 'NO SERPER_API_KEY — Serper net disabled'}"
              f" + {len(cfg.companies)} watchlist companies (selection-gated)"
              f"{' + LinkedIn' if cfg.sources.get('linkedin') and cfg.linkedin_queries else ''}"
              f"{' + RemoteOK' if cfg.sources.get('remoteok') else ''}"
              f"{' + HN' if cfg.sources.get('hn') else ''})...")
        jobs, funnel = discover_and_fetch(mode, cfg, serper_key)
        print(f"  ✓ {len(jobs)} jobs total — {funnel['per_source']}")

        # PHASE E — enrich thin-JD, link-only jobs with their FULL page text via
        # crawl4ai (verify from real raw text, never a fabricated snippet). No-op
        # when crawl4ai isn't installed or the sandbox blocks the headless browser.
        n_enriched = sources.enrich_jobs(
            jobs, min_jd_chars=cfg.min_jd_chars, cap=cfg.enrich_max_crawls,
            should_enrich=_enrichable(cfg))
        funnel["crawl4ai_enriched"] = n_enriched
        if n_enriched:
            print(f"\n🔎 PHASE E — crawl4ai backfilled {n_enriched} thin-JD job(s) with full page text")

        with open(raw_path, "w") as f:
            for j in jobs:
                f.write(json.dumps(j, default=str) + "\n")

    print(f"\n🔬 PHASE V — deterministic pre-filter ({len(jobs)} jobs)...")
    candidates, rejected = filters.prefilter(jobs, cfg, cross_run_seen, mode)
    print(f"  ✓ {len(candidates)} candidates | ✗ {len(rejected)} filtered")

    if dry_run:
        report, audit = [], rejected
        result = {"dry_run": True, "candidates_passed_prefilter": len(candidates),
                  "candidates": [_slim(c) for c in candidates]}
        with open(report_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
    else:
        print(f"\n📶 PHASE R — ranking {len(candidates)} candidates "
              f"(evaluating top {cfg.eval_max_candidates}, "
              f"max {cfg.eval_slots_per_company}/company)...")
        ranked = filters.rank(candidates, cfg)
        to_eval, over_cap = _cap_eval_slots(ranked, cfg)
        rank_pos = {id(j): i + 1 for i, j in enumerate(ranked)}
        over_cap_audit = [{**_slim(c), "rejection_reason":
                           f"not evaluated (rank #{rank_pos[id(c)]}; over cap {cfg.eval_max_candidates} "
                           f"or per-company eval limit {cfg.eval_slots_per_company})"}
                          for c in over_cap]

        report, eval_audit = evaluate_and_draft(to_eval, cfg)
        audit = rejected + over_cap_audit + eval_audit
        # Mark ONLY reported jobs seen — never a merely-evaluated or merely-fetched
        # one (see filters.prefilter's docstring and _pop_reported_fingerprints).
        newly_seen = _pop_reported_fingerprints(report)
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        for fp in newly_seen:
            cross_run_seen[fp] = now_iso
        save_seen(mode, cross_run_seen)
        with open(report_path, "w") as f:
            json.dump({"mode": mode, "generated_at": ts, "count": len(report),
                       "min_score": cfg.report_min_score, "matches": report}, f, indent=2, default=str)

    with open(audit_path, "w") as f:
        json.dump([{"job_title": a.get("job_title", a.get("title")),
                    "company": a.get("company"), "reason": a.get("rejection_reason")}
                   for a in audit], f, indent=2, default=str)

    n_eval_companies = len({c.get("company_token") or c.get("company", "") for c in to_eval}) if not dry_run else 0
    _summary(mode, dry_run, funnel, candidates, rejected, report, report_path, audit_path,
             n_evaluated=len(to_eval) if not dry_run else 0, n_eval_companies=n_eval_companies)


def _summary(mode, dry_run, funnel, candidates, rejected, report, report_path, audit_path,
            n_evaluated=0, n_eval_companies=0):
    print(f"\n{'='*64}\n📊 RUN SUMMARY — {mode}\n{'='*64}")
    if funnel:
        print(f"  per-source jobs     : {funnel.get('per_source', {})}")
        if funnel.get("jobspy_error"):
            print(f"  ⚠️  JobSpy (primary volume net): {funnel['jobspy_error']}")
        elif funnel.get("jobspy_searches_run"):
            jobspy_total = sum(v for k, v in funnel.get("per_source", {}).items() if k.startswith("jobspy_"))
            print(f"  JobSpy: {funnel['jobspy_searches_run']} searches run → {jobspy_total} real job-board postings "
                  f"(Indeed/Naukri/LinkedIn/Google — full descriptions, not thin cards)")
        d1 = funnel.get("serper_d1") or {}
        if d1.get("hits"):
            print(f"  Serper D1 (job-first): {d1['hits']} URL hits → "
                  f"{d1.get('per_job', 0)} per-job fetches, {d1.get('board_root', 0)} board-root selections, "
                  f"{d1.get('unresolved_stub', 0)} unresolved (→ crawl4ai or dropped honestly)")
        ats_raw = funnel.get("ats_raw_fetched")
        ats_kept = funnel.get("per_source", {}).get("ats_registry", 0)
        if ats_raw:
            print(f"  ATS watchlist selection: {ats_raw} raw jobs fetched → {ats_kept} AI/ML-selected "
                  f"({ats_kept*100//max(ats_raw,1)}% kept per board, capped)")
        dead = funnel.get("dead_boards", [])
        if dead:
            print(f"  registry boards w/ 0 jobs : {len(dead)}  (e.g. {', '.join(dead[:5])}{'…' if len(dead) > 5 else ''})")
        no_ai = funnel.get("no_ai_roles_boards", [])
        if no_ai:
            print(f"  registry boards w/ 0 AI/ML roles now : {len(no_ai)}  (e.g. {', '.join(no_ai[:3])}{'…' if len(no_ai) > 3 else ''})")
        if funnel.get("linkedin_companies_found"):
            print(f"  LinkedIn: {funnel['linkedin_companies_found']} companies found, "
                  f"{len(funnel.get('linkedin_companies_resolved', []))} resolved to a live board")
        if funnel.get("crawl4ai_enriched"):
            print(f"  crawl4ai enriched  : {funnel['crawl4ai_enriched']} thin-JD job(s) from their page text")
    print(f"  passed pre-filter   : {len(candidates)}"
          f"{f' ({len(candidates)*100//max(len(candidates)+len(rejected),1)}% of fetched pool)' if (candidates or rejected) else ''}")
    top_reasons = Counter(r.get("rejection_reason", "?").split(":")[0].split("(")[0].strip()
                          for r in rejected)
    print(f"  filtered out        : {len(rejected)}  top reasons: "
          f"{', '.join(f'{k}={v}' for k, v in top_reasons.most_common(5))}")
    if not dry_run:
        print(f"  evaluated           : {n_evaluated} candidates across {n_eval_companies} distinct companies "
              f"(max per company enforced — no single board can flood the eval budget)")
        print(f"  ✅ REPORTED (>=min)  : {len(report)}")
        if not report:
            print("     (No matches cleared the bar this run — that's an honest result,\n"
                  "      not an error. Check the per-source funnel above: an empty report\n"
                  "      with healthy fetch counts means no real match this run; a funnel\n"
                  "      full of zeros means a source broke — widen config/companies_*.yaml\n"
                  "      or config/<mode>.yaml's linkedin_queries for more volume.)")
    print(f"\n  report → {report_path}")
    print(f"  audit  → {audit_path}")


# ─────────────────────────── mock data ──────────────────────────────

def _mk(title, company, jd, location, **kw):
    import hashlib
    fp = hashlib.md5(f"{title.lower()}|{company.lower()}".encode()).hexdigest()
    return {"title": title, "company": company, "company_token": company.lower(),
            "url": f"https://example.com/{fp[:8]}", "location_text": location,
            "is_remote": kw.get("is_remote"), "workplace_type": kw.get("workplace_type", ""),
            "employment_type": "", "department": "", "team": "",
            "posted_date": "2026-07-10", "pay_text": "", "jd_text": jd, "jd_len": len(jd),
            "source": "mock", "tier": 1, "tags": [], "source_tier": 1,
            "_fingerprint": fp, "_fetched_at": "2026-07-14T00:00:00Z"}


_GOOD_JD = ("We are hiring an AI Engineer to build LLM-powered products. You will work "
            "with Python, FastAPI, RAG pipelines (LangChain, Pinecone), and fine-tune "
            "models. 1-2 years of experience with machine learning in production. "
            "Ship agentic features end to end. " * 3)

# AI-relevant but explicitly senior — only high YOE stated (tests the YOE gate).
_HIGH_YOE_JD = ("Build and lead LLM and RAG systems in Python with FastAPI and PyTorch. "
                "This role requires 8+ years of experience in machine learning. "
                "You will own agentic infrastructure at scale. " * 3)


def _mock_jobs(mode):
    if mode == "india_mnc":
        return [
            _mk("ML Engineer", "MockIndiaAI", _GOOD_JD, "Bengaluru, India"),
            _mk("AI Engineer", "MockUSco", _GOOD_JD, "San Francisco, CA", is_remote=False),
            _mk("Principal AI Scientist", "MockIndiaAI", _GOOD_JD + " 8+ years required.", "Hyderabad, India"),
            _mk("Data Analyst", "MockIndiaAI", "SQL dashboards and Excel reporting for finance teams. " * 8, "Pune, India"),
            _mk("AI Engineer", "ThinCo", "", "Bengaluru, India"),
        ]
    return [
        _mk("AI Engineer", "MockAI", _GOOD_JD, "Remote - Worldwide", is_remote=True, workplace_type="remote"),
        _mk("Senior Machine Learning Engineer", "MockAI2", _GOOD_JD + " 5+ years required.", "Remote", is_remote=True),
        _mk("AI Engineer", "ThinCo", "", "Remote", is_remote=True),
        _mk("Data Analyst", "AnalyticsCo", "SQL dashboards, Tableau and Excel reporting. " * 8, "Remote", is_remote=True),
        _mk("LLM Engineer", "USOnlyCo", _GOOD_JD + " Must be located in the United States. US citizen required.",
            "Remote (US Only)", is_remote=True),
        _mk("AI Engineer", "SeniorCo", _HIGH_YOE_JD, "Remote - Worldwide", is_remote=True),
    ]
