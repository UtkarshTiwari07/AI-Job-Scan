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
import sys
import json
import datetime
from collections import Counter

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

EVAL_BATCH = 4
SEARCH_TEXT = "AI engineer OR machine learning engineer OR LLM engineer"
LINKEDIN_MAX_NEW_COMPANIES = 40  # cap company_resolve probes per run


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


# ─────────────────────────── run ────────────────────────────────────

def discover_and_fetch(mode, cfg, serper_key):
    """Phase D — merge every enabled source into one job pool, tagged with which
    source found it (funnel['per_source']) so an empty report is diagnosable."""
    all_jobs = []
    funnel = {"per_source": {}, "dead_boards": [], "linkedin_companies_found": 0,
              "linkedin_companies_resolved": []}

    def add(jobs, label):
        all_jobs.extend(jobs)
        funnel["per_source"][label] = funnel["per_source"].get(label, 0) + len(jobs)

    known_tokens = {c.get("token") or c.get("tenant") for c in cfg.companies}

    if cfg.sources.get("ats", True) and cfg.companies:
        reg_jobs, reg_summary = registry.fetch_all(cfg.companies, search_text=SEARCH_TEXT,
                                                    serper_key=serper_key)
        add(reg_jobs, "ats_registry")
        funnel["dead_boards"] = reg_summary.get("dead", [])
    elif not cfg.companies:
        print(f"  ⚠️  No companies in config/{cfg.companies_file} — run: python job/probe_registry.py")

    if cfg.sources.get("linkedin", True) and cfg.linkedin_queries:
        print("  🔎 LinkedIn discovery (company names only — never a reported URL)...")
        by_company = discovery.linkedin_discover_companies(cfg.linkedin_queries, max_pages_per_query=2)
        funnel["linkedin_companies_found"] = len(by_company)
        li_jobs, li_summary = discovery.resolve_and_fetch_new(
            set(by_company.keys()), known_tokens, cap=LINKEDIN_MAX_NEW_COMPANIES)
        add(li_jobs, "linkedin_resolved")
        funnel["linkedin_companies_resolved"] = li_summary["resolved"]
        known_tokens |= {r.split("->", 1)[1].split(":", 1)[1] for r in li_summary["resolved"]}

    if cfg.sources.get("remoteok", False):
        rok = registry.tag_multi_company_jobs(sources.fetch_remoteok(), tier=2, tags=["remoteok"])
        add(rok, "remoteok")

    if cfg.sources.get("hn", False):
        hn = registry.tag_multi_company_jobs(sources.fetch_hn_whoishiring(), tier=2, tags=["hn"])
        add(hn, "hn_whoishiring")

    if cfg.serper_discovery_queries and serper_key:
        hits = discovery.serper_broad_discover(cfg.serper_discovery_queries, serper_key)
        new_hits = [(a, t) for a, t in hits if t not in known_tokens]
        for ats, token in new_hits:
            jobs = registry.tag_jobs(sources.ADAPTERS[ats]({"token": token}), token, token, tier=1,
                                     tags=["serper-discovered"])
            add(jobs, "serper_discovered")
            known_tokens.add(token)

    return all_jobs, funnel


def run(mode, dry_run=False):
    cfg = load_config(mode)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    rdir = os.path.join(HERE, f"reports_{mode}")
    os.makedirs(rdir, exist_ok=True)
    raw_path = os.path.join(rdir, f"raw_{mode}_{ts}.ndjson")
    report_path = os.path.join(rdir, f"report_{mode}_{ts}.json")
    audit_path = os.path.join(rdir, f"audit_{mode}_{ts}.json")

    print(f"\n{'='*64}\n🚀 {mode.upper()} scan v3  {'[DRY RUN]' if dry_run else '[LIVE]'}\n{'='*64}")

    cross_run_seen = load_seen(mode)
    funnel = {}
    if dry_run:
        print("[DRY RUN] using mock ATS data (no network, no LLM)")
        jobs = _mock_jobs(mode)
    else:
        serper_key = os.getenv("SERPER_API_KEY", "")
        print(f"\n📡 PHASE D — discovery ({len(cfg.companies)} registry companies"
              f"{' + LinkedIn' if cfg.sources.get('linkedin') and cfg.linkedin_queries else ''}"
              f"{' + RemoteOK' if cfg.sources.get('remoteok') else ''}"
              f"{' + HN' if cfg.sources.get('hn') else ''})...")
        jobs, funnel = discover_and_fetch(mode, cfg, serper_key)
        with open(raw_path, "w") as f:
            for j in jobs:
                f.write(json.dumps(j, default=str) + "\n")
        print(f"  ✓ {len(jobs)} jobs total — {funnel['per_source']}")

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
              f"(evaluating top {cfg.eval_max_candidates})...")
        ranked = filters.rank(candidates, cfg)
        to_eval, over_cap = ranked[:cfg.eval_max_candidates], ranked[cfg.eval_max_candidates:]
        over_cap_audit = [{**_slim(c), "rejection_reason": f"not evaluated (rank #{i}, over cap {cfg.eval_max_candidates})"}
                          for i, c in enumerate(over_cap, start=cfg.eval_max_candidates + 1)]

        report, eval_audit = evaluate_and_draft(to_eval, cfg)
        audit = rejected + over_cap_audit + eval_audit
        save_seen(mode, cross_run_seen)
        with open(report_path, "w") as f:
            json.dump({"mode": mode, "generated_at": ts, "count": len(report),
                       "min_score": cfg.report_min_score, "matches": report}, f, indent=2, default=str)

    with open(audit_path, "w") as f:
        json.dump([{"job_title": a.get("job_title", a.get("title")),
                    "company": a.get("company"), "reason": a.get("rejection_reason")}
                   for a in audit], f, indent=2, default=str)

    _summary(mode, dry_run, funnel, candidates, rejected, report, report_path, audit_path)


def _summary(mode, dry_run, funnel, candidates, rejected, report, report_path, audit_path):
    print(f"\n{'='*64}\n📊 RUN SUMMARY — {mode}\n{'='*64}")
    if funnel:
        print(f"  per-source jobs     : {funnel.get('per_source', {})}")
        dead = funnel.get("dead_boards", [])
        if dead:
            print(f"  registry boards w/ 0 jobs : {len(dead)}  (e.g. {', '.join(dead[:5])}{'…' if len(dead) > 5 else ''})")
        if funnel.get("linkedin_companies_found"):
            print(f"  LinkedIn: {funnel['linkedin_companies_found']} companies found, "
                  f"{len(funnel.get('linkedin_companies_resolved', []))} resolved to a live board")
    print(f"  passed pre-filter   : {len(candidates)}")
    top_reasons = Counter(r.get("rejection_reason", "?").split(":")[0].split("(")[0].strip()
                          for r in rejected)
    print(f"  filtered out        : {len(rejected)}  top reasons: "
          f"{', '.join(f'{k}={v}' for k, v in top_reasons.most_common(5))}")
    if not dry_run:
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
