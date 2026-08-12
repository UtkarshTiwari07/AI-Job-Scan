"""
job/ab_test_search.py — A/B-test Serper career-page discovery techniques.

WHY: v13 fixed the query that finds a company's career page (broadening it fixed
a near-100% drop rate). But "broaden it" was one call; there are several ways to
ask Serper for a company's openings, and which one surfaces the most
role-ALIGNED postings (AI/ML/DS/FDE, on the company's own board) is an empirical
question, not a guess. This script runs 2-3 techniques against the SAME sample of
companies and writes an analytical file so you can SEE which wins — and re-run it
with your own sample and paste the numbers back.

It reuses the REAL filter logic from job/companies.py (ats_job_from_url,
fetch_job_by_ref, _is_negative_host, _is_ats_host, _host_matches_company,
_core_company_name, AI_TITLE_KEYWORDS) — only the QUERY construction differs per
technique, so the comparison is apples-to-apples with what the pipeline actually
does.

The headline metric is ALIGNED ATS-DIRECT HITS: a returned URL that is itself a
per-job Greenhouse/Lever/Ashby/Workable posting, whose real (API-fetched) title
matches the AI/ML/DS/FDE keyword set. Those are verifiable in-sandbox (no browser
needed) and are the closest, cleanest signal of "a real role aligned to me."
Career/board URLs that would need a full crawl are counted too (as reach), but
can't be alignment-verified here without Crawl4AI (which needs the user's
machine) — that limit is stated in the output.

Usage:
  python job/ab_test_search.py                      # default: 12 companies, india mode
  python job/ab_test_search.py --companies 20
  python job/ab_test_search.py --mode remote
  python job/ab_test_search.py --companies 15 --mode india --seed 7

Writes job/reports_ab_search/ab_search_<mode>_<timestamp>.{md,json} and prints a
summary. Needs SERPER_API_KEY (each technique = 1 Serper call per company, so a
run costs ~= techniques × companies API calls — bounded and printed up front).
"""

import argparse
import datetime
import json
import os
import random
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import requests
import companies as co

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
_API = "https://google.serper.dev/search"


# ══════════════════════════════════════════════════════════════════
# Shared query fragments + URL classification
# ══════════════════════════════════════════════════════════════════

_NEGATIVES = " ".join(f"-site:{s}" for s in co._CAREERS_NEGATIVE_SITES)
_ATS_SITES = ("site:boards.greenhouse.io OR site:job-boards.greenhouse.io OR "
              "site:jobs.lever.co OR site:jobs.ashbyhq.com OR site:apply.workable.com")
_ROLE_TERMS = ('("AI engineer" OR "machine learning engineer" OR "ML engineer" OR '
               '"LLM" OR "data scientist" OR "forward deployed engineer")')

# A URL that looks like an INDIVIDUAL job posting (not a bare /careers landing):
# a job-ish path token AND at least 2 path segments (so "/careers" alone is a
# landing page, "/careers/ai-engineer" or "/jobs/12345" is a posting).
_JOB_PATH_HINT = re.compile(r"/(jobs?|careers?|positions?|opening|openings|vacan|"
                            r"roles?|apply|hiring|opportunit|join[\-_]?us)", re.I)
# Role-aligned by URL SLUG alone (no page fetch needed) — the cheap signal for
# own-domain postings where we have no ATS API to get a real title from.
_ROLE_SLUG = re.compile(r"(\bai\b|\bml\b|machine[\-_]?learn|data[\-_]?scien|\bllm\b|genai|"
                        r"generative[\-_]?ai|\bnlp\b|deep[\-_]?learn|engineer|developer|"
                        r"applied[\-_]?scien|forward[\-_]?deployed|research[\-_]?scien)", re.I)


def _looks_like_job_posting_url(url):
    path = urlparse(url or "").path or ""
    depth = len([p for p in path.split("/") if p])
    return bool(_JOB_PATH_HINT.search(path)) and depth >= 2


def _slug_aligned(url):
    return bool(_ROLE_SLUG.search(urlparse(url or "").path or ""))


def _serper(query, num=8):
    resp = requests.post(_API, headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                         data=json.dumps({"q": query, "num": num}), timeout=20)
    resp.raise_for_status()
    return [r.get("link", "").strip() for r in resp.json().get("organic", []) if r.get("link")]


def _blank_metrics():
    # job_urls = individual postings (ATS per-job OR own-domain posting-shaped);
    # job_urls_aligned = of those, role-aligned (ATS title match OR own-domain
    # URL slug names a role) — this is the ATS-INDEPENDENT headline metric.
    # careers_pages = landing/board pages kept for a full crawl (reach, not a
    # direct posting). serper_calls lets techniques that cost >1 call be compared
    # on yield-per-call.
    return {"organic": 0, "job_urls": 0, "job_urls_aligned": 0, "careers_pages": 0,
            "dropped": 0, "serper_calls": 0, "aligned_samples": []}


def _classify_link(link, name, m):
    """Classify one returned URL into the metrics bucket, exactly mirroring the
    real serper_careers_urls() filter order. Mutates `m`."""
    ref = co.ats_job_from_url(link)
    if ref:
        ats_kind, token, job_id = ref
        job = co.fetch_job_by_ref(ats_kind, token, job_id)
        title = (job or {}).get("title", "")
        if job and title:
            m["job_urls"] += 1
            if co.AI_TITLE_KEYWORDS.search(title):
                m["job_urls_aligned"] += 1
                m["aligned_samples"].append(f"{name}: {title}  [{link}]")
            return
        m["dropped"] += 1
        return
    host = co._domain(link)
    if co._is_negative_host(host):
        m["dropped"] += 1
        return
    if co._is_ats_host(host) or co._host_matches_company(host, name):
        if _looks_like_job_posting_url(link):
            m["job_urls"] += 1
            if _slug_aligned(link):
                m["job_urls_aligned"] += 1
                m["aligned_samples"].append(f"{name}: {link}")
        else:
            m["careers_pages"] += 1
    else:
        m["dropped"] += 1


def _run_single_query(name, query):
    m = _blank_metrics()
    try:
        links = _serper(query)
        m["serper_calls"] = 1
    except Exception as e:
        m["error"] = str(e); return m
    m["organic"] = len(links)
    for link in links:
        _classify_link(link, name, m)
    return m


# ══════════════════════════════════════════════════════════════════
# Techniques — each is runner(name) -> metrics. A/B/C are single-query;
# D is the two-step own-domain job-URL hunt.
# ══════════════════════════════════════════════════════════════════

def technique_broad(name):
    """A — v13 production query: broad, find-the-page, relevance decided later."""
    return _run_single_query(name, f'"{co._core_company_name(name)}" (careers OR jobs OR hiring) {_NEGATIVES}')


def technique_ats_restricted(name):
    """B — restrict to the big ATS hosts. Only helps the minority of companies
    that actually have a public ATS board (kept for contrast, not the target)."""
    return _run_single_query(name, f'"{co._core_company_name(name)}" ({_ATS_SITES})')


def technique_role_targeted(name):
    """C — company + explicit AI/ML role terms baked into the query (pre-v13 style,
    minus qdr:m). Tests whether pre-filtering by role in the query helps or hurts."""
    return _run_single_query(name, f'"{co._core_company_name(name)}" {_ROLE_TERMS} (careers OR jobs) {_NEGATIVES}')


def technique_own_domain_jobs(name):
    """D — own-careers-page job-URL hunt (NO ATS dependency; the user's ask).
    Step 1: a broad query to resolve the company's OWN careers host. Step 2: an
    inurl:-biased site: search of that host for INDIVIDUAL posting URLs. This is
    what gets a real per-job URL off a company that has only its own careers
    page and no Greenhouse/Lever/Ashby board."""
    core = co._core_company_name(name)
    m = _blank_metrics()
    # Step 1 — resolve the own-domain host.
    try:
        links1 = _serper(f'"{core}" careers {_NEGATIVES}', num=6)
        m["serper_calls"] = 1
    except Exception as e:
        m["error"] = str(e); return m
    own_host = None
    for link in links1:
        host = co._domain(link)
        if co._is_negative_host(host):
            continue
        if co._host_matches_company(host, name):
            own_host = host
            break
    if not own_host:
        # No resolvable own domain — fall back to classifying step-1 results so D
        # is never worse than "nothing"; record what step 1 alone found.
        m["organic"] = len(links1)
        for link in links1:
            _classify_link(link, name, m)
        m["resolved_host"] = None
        return m
    m["resolved_host"] = own_host
    # Step 2 — deep job-URL search on that host.
    try:
        links2 = _serper(f"site:{own_host} (jobs OR careers OR position OR opening OR apply OR hiring OR role)", num=10)
        m["serper_calls"] = 2
    except Exception as e:
        m["error"] = str(e); return m
    m["organic"] = len(links2)
    for link in links2:
        _classify_link(link, name, m)
    return m


TECHNIQUES = [
    ("A_broad", "Broad find-the-page (v13 production)", technique_broad),
    ("B_ats_restricted", "ATS-host-restricted (minority of companies)", technique_ats_restricted),
    ("C_role_targeted", "Company + AI/ML role terms in query", technique_role_targeted),
    ("D_own_domain_jobs", "Own-careers-page job-URL hunt (2-step, no ATS dependency)", technique_own_domain_jobs),
]


def evaluate_company(name, runner):
    return runner(name)


def inventory():
    """Company counts for both modes — surfaces the newly-added companies."""
    out = {}
    for mode in ("remote", "india"):
        pool = co.load_pool(mode)
        ats, serper = len(pool.get("ats") or []), len(pool.get("serper") or [])
        out[mode] = {"ats": ats, "serper": serper, "total": ats + serper}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", type=int, default=12)
    ap.add_argument("--mode", choices=["india", "remote"], default="india")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not SERPER_API_KEY:
        raise SystemExit("SERPER_API_KEY not set (see .env). Cannot run the A/B test.")

    inv = inventory()
    print("\n📇 COMPANY INVENTORY (all registries, incl. recently-added):")
    for mode, c in inv.items():
        print(f"    {mode:7}: {c['total']:4} total  ({c['ats']} ats + {c['serper']} serper)")

    pool = co.load_pool(args.mode)
    serper_list = [c.get("name", "") for c in (pool.get("serper") or []) if c.get("name")]
    if not serper_list:
        raise SystemExit(f"No serper: companies in the {args.mode} registry to test.")
    rng = random.Random(args.seed)
    sample = rng.sample(serper_list, min(args.companies, len(serper_list)))

    print(f"\n🔬 A/B search test — mode={args.mode}, {len(sample)} companies × "
          f"{len(TECHNIQUES)} techniques (D costs 2 Serper calls/company, others 1)\n")

    # results[technique_key] = list of per-company metric dicts
    results = {key: [] for key, _, _ in TECHNIQUES}
    per_company = {}
    for name in sample:
        per_company[name] = {}
        for key, label, runner in TECHNIQUES:
            m = evaluate_company(name, runner)
            results[key].append(m)
            per_company[name][key] = m
        row = per_company[name]
        # j = aligned job URLs, u = career/landing pages, o = organic results
        print(f"  {name[:30]:32} " + "  ".join(
            f"{key.split('_')[0]}:{row[key]['job_urls_aligned']}j/{row[key]['careers_pages']}c/{row[key]['organic']}o"
            for key, _, _ in TECHNIQUES))

    def agg(key):
        rows = results[key]
        return {
            "organic": sum(r["organic"] for r in rows),
            "job_urls": sum(r["job_urls"] for r in rows),
            "job_urls_aligned": sum(r["job_urls_aligned"] for r in rows),
            "careers_pages": sum(r["careers_pages"] for r in rows),
            "dropped": sum(r["dropped"] for r in rows),
            "serper_calls": sum(r["serper_calls"] for r in rows),
            "companies_with_aligned_job_url": sum(1 for r in rows if r["job_urls_aligned"] > 0),
            "companies_with_any_hit": sum(1 for r in rows if (r["job_urls"] + r["careers_pages"]) > 0),
            "errors": sum(1 for r in rows if r.get("error")),
        }

    summary = {key: agg(key) for key, _, _ in TECHNIQUES}
    # Rank by ATS-INDEPENDENT signal: aligned job URLs first, then any-hit
    # company coverage, then total job URLs.
    ranked = sorted(TECHNIQUES,
                    key=lambda t: (summary[t[0]]["job_urls_aligned"],
                                   summary[t[0]]["companies_with_any_hit"],
                                   summary[t[0]]["job_urls"]),
                    reverse=True)
    winner_key, winner_label, _ = ranked[0]

    print("\n" + "=" * 72)
    print("AGGREGATE (headline = aligned JOB URLs — individual postings, ATS-independent):")
    for key, label, _ in TECHNIQUES:
        s = summary[key]
        print(f"  {key:20} aligned_job_urls={s['job_urls_aligned']:3}  "
              f"job_urls={s['job_urls']:3}  careers_pages={s['careers_pages']:3}  "
              f"any-hit={s['companies_with_any_hit']}/{len(sample)}  calls={s['serper_calls']}")
    print(f"\n🏆 WINNER (by aligned job URLs): {winner_key} — {winner_label}")
    print("=" * 72)

    # Write analytical files
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports_ab_search")
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, f"ab_search_{args.mode}_{ts}.json")
    md_path = os.path.join(out_dir, f"ab_search_{args.mode}_{ts}.md")

    with open(json_path, "w") as f:
        json.dump({"mode": args.mode, "sample": sample, "seed": args.seed,
                   "inventory": inv, "summary": summary,
                   "techniques": {k: l for k, l, _ in TECHNIQUES},
                   "winner": winner_key, "per_company": per_company}, f, indent=2, default=str)

    all_aligned = []
    for key, _, _ in TECHNIQUES:
        for r in results[key]:
            all_aligned.extend(r.get("aligned_samples", []))

    with open(md_path, "w") as f:
        f.write(f"# A/B search-technique test — {args.mode} mode\n\n")
        f.write(f"Run: {ts} · sample: {len(sample)} companies (seed {args.seed})\n\n")
        f.write("Headline metric: **aligned job URLs** — individual job-posting URLs "
                "(a per-job ATS link whose real title is AI/ML/DS/FDE, OR an own-domain "
                "posting URL whose slug names an AI/ML role). Deliberately NOT dependent "
                "on the company having an ATS board.\n\n")
        f.write("## Company inventory (all registries)\n\n")
        f.write("| mode | total | ats | serper |\n|---|---|---|---|\n")
        for mode, c in inv.items():
            f.write(f"| {mode} | {c['total']} | {c['ats']} | {c['serper']} |\n")
        f.write("\n## Techniques\n\n")
        for k, l, _ in TECHNIQUES:
            f.write(f"- **{k}** — {l}\n")
        f.write("\n## Aggregate results\n\n")
        f.write("| technique | aligned_job_urls | job_urls | careers_pages | organic | "
                "any-hit companies | serper_calls |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for k, _, _ in TECHNIQUES:
            s = summary[k]
            f.write(f"| {k} | {s['job_urls_aligned']} | {s['job_urls']} | {s['careers_pages']} | "
                    f"{s['organic']} | {s['companies_with_any_hit']}/{len(sample)} | {s['serper_calls']} |\n")
        f.write(f"\n**Winner (by aligned job URLs): {winner_key} — {winner_label}**\n\n")
        f.write("## Per-company breakdown (aligned_job_urls / careers_pages / organic)\n\n")
        header = "| company | " + " | ".join(k for k, _, _ in TECHNIQUES) + " |\n"
        f.write(header)
        f.write("|---" * (len(TECHNIQUES) + 1) + "|\n")
        for name in sample:
            cells = []
            for k, _, _ in TECHNIQUES:
                m = per_company[name][k]
                cells.append(f"{m['job_urls_aligned']}j / {m['careers_pages']}c / {m['organic']}o")
            f.write(f"| {name} | " + " | ".join(cells) + " |\n")
        f.write("\n## Aligned job URLs found (real — ATS title match or own-domain role slug)\n\n")
        if all_aligned:
            for t in sorted(set(all_aligned)):
                f.write(f"- {t}\n")
        else:
            f.write("_None in this sample._\n")
        f.write("\n## Honest limits\n\n")
        f.write("- **Own-domain alignment is judged by the URL slug**, not the page body "
                "(a company careers page is often a JS app whose text needs a real browser; "
                "Crawl4AI does that on your machine, not this sandbox). So an own-domain "
                "posting at `/careers/ai-engineer` counts aligned; one at `/careers/job/8821` "
                "counts as a job URL but not aligned even if it IS an AI role. ATS per-job "
                "URLs use the real API-fetched title, so those are exact.\n")
        f.write("- Technique D spends 2 Serper calls/company (resolve host, then deep search) "
                "vs 1 for A/B/C — compare yield-per-call, not just totals.\n")
        f.write("- A bigger sample gives a steadier ranking; re-run with `--companies N --seed S`.\n")

    print(f"\n💾 Analytical files:\n    {md_path}\n    {json_path}")


if __name__ == "__main__":
    main()
