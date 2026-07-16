"""
registry.py — load the company registry and fetch every company's jobs.

Replaces the old Serper-search + Crawl4AI-scrape discovery (Phases 1-2) with
direct ATS-API fetches from a curated, live-verified company list. Each job is
tagged with its company/tier/tags and a fingerprint, then handed to Phase 3.
"""

import os
import sys
import hashlib
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML required. pip install pyyaml") from exc

import sources

CONFIG_DIR = os.path.join(os.path.dirname(HERE), "config")


def load_registry(companies_file: str) -> list:
    path = os.path.join(CONFIG_DIR, companies_file)
    if not os.path.exists(path):
        raise SystemExit(
            f"Missing company registry: {path}\n"
            f"Build it with:  python job/probe_registry.py")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("companies", [])


def fingerprint(title: str, company: str, location: str = "") -> str:
    """Includes location so the same title/company posted in multiple locations
    (common on big boards) isn't collapsed into a single 'duplicate'."""
    key = f"{title.lower().strip()}|{company.lower().strip()}|{(location or '').lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def tag_jobs(jobs: list, company: str, token: str, tier: int = 1, tags: list = None) -> list:
    """Attach company/tier/tags/fingerprint/timestamp to a batch of raw adapter
    output. Used both for the static registry and for dynamically discovered
    companies (LinkedIn/Serper → company_resolve), so every job — regardless of
    how it was found — carries the same fields the rest of the pipeline expects."""
    now = datetime.datetime.utcnow().isoformat() + "Z"
    for j in jobs:
        j["company"] = company
        j["company_token"] = token
        j["tier"] = tier
        j["source_tier"] = tier
        j["tags"] = tags or []
        j["_fingerprint"] = fingerprint(j.get("title", ""), company, j.get("location_text", ""))
        j["_fetched_at"] = now
    return jobs


def tag_multi_company_jobs(jobs: list, tier: int, tags: list) -> list:
    """Like tag_jobs, but for feeds spanning MANY companies (RemoteOK, HN
    Who's-Hiring) where each job already carries its own `company` field —
    fills in company_token/fingerprint/tier/tags/timestamp per job."""
    now = datetime.datetime.utcnow().isoformat() + "Z"
    for j in jobs:
        company = j.get("company", "") or "Unknown"
        j["company_token"] = company.lower().strip()
        j["tier"] = tier
        j["source_tier"] = tier
        j["tags"] = tags or []
        j["_fingerprint"] = fingerprint(j.get("title", ""), company, j.get("location_text", ""))
        j["_fetched_at"] = now
    return jobs


def fetch_all(companies: list, search_text: str = "", serper_key: str = "") -> tuple:
    """Fetch + normalize + tag jobs from every company. Returns (jobs, summary)."""
    all_jobs, dead = [], []
    per_tier = {1: 0, 2: 0, 3: 0}

    for c in companies:
        adapter = sources.ADAPTERS.get(c.get("ats"))
        if not adapter:
            continue
        try:
            jobs = adapter(c, search_text=search_text, serper_key=serper_key)
        except Exception as e:
            print(f"    ⚠️  {c.get('name')} ({c.get('ats')}) fetch error: {e}")
            jobs = []
        tier = int(c.get("tier", 1))
        token = c.get("token") or c.get("tenant") or c.get("domain") or c.get("name")
        if not jobs:
            dead.append(f"{c.get('name')} [{c.get('ats')}:{token}]")
            continue
        tag_jobs(jobs, c.get("name", ""), token, tier, c.get("tags", []))
        all_jobs.extend(jobs)
        per_tier[tier] = per_tier.get(tier, 0) + len(jobs)

    summary = {
        "companies": len(companies),
        "companies_with_jobs": len(companies) - len(dead),
        "dead": dead,
        "per_tier": per_tier,
        "total_jobs": len(all_jobs),
    }
    return all_jobs, summary
