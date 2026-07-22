"""
job/companies.py — v10 200-company source for job_remote.py.

Two fetch paths, chosen per company by job/companies_remote.yaml (built once by
job/probe_companies.py):
  - `ats:` entries have a live-verified Greenhouse/Lever/Ashby/Workable board —
    fetched DIRECTLY via that ATS's JSON API, which returns the FULL job
    description. These bypass Crawl4AI entirely (nothing to crawl — the API
    already gives clean, complete text).
  - `serper:` entries have no public ATS — `serper_careers_urls()` searches their
    own careers domain via Serper and returns URLs for the EXISTING Crawl4AI
    scrape_jobs() path in job_remote.py.

`select_companies(n)` picks the next N companies (across both lists) that have
not run yet in the current cycle, via a persistent on-disk cursor
(job/companies_cursor.json). Once every company has had a turn, the cycle wraps.
This guarantees a run never re-scans the same company until the whole pool has
been covered — the user's explicit ask ("should be unique company running
everytime").
"""

import datetime
import hashlib
import html
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import requests
import yaml

_DIR = os.path.dirname(__file__)
REGISTRY_PATH = os.path.join(_DIR, "companies_remote.yaml")
CURSOR_PATH = os.path.join(_DIR, "companies_cursor.json")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept": "application/json"})
TIMEOUT = 20
_RETRY_STATUS = {429, 500, 502, 503, 504}

# Title filter applied to every ATS-fetched job before it enters job_remote.py's
# pipeline — an ATS board can carry hundreds of non-AI roles (sales, support,
# finance); only these are worth the eval budget.
AI_TITLE_KEYWORDS = re.compile(
    r"(\bai\b|\bml\b|machine learning|deep learning|\bllm\b|\brag\b|genai|"
    r"generative ai|\bnlp\b|computer vision|data scientist|applied scientist|"
    r"forward deployed|ml engineer|ai engineer|research engineer|prompt engineer)",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════
# HTTP + text helpers (adapted from job/sources.py, git 979065b)
# ══════════════════════════════════════════════════════════════════

def _request(method: str, url: str, **kw):
    for attempt in range(3):
        try:
            resp = _session.request(method, url, timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            if attempt == 2:
                print(f"    ⚠️  {method} {url[:70]} failed: {e}")
                return None
            time.sleep(2 ** attempt)
            continue
        if resp.status_code in _RETRY_STATUS and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        return resp
    return None

def _get_json(url: str, **kw):
    r = _request("GET", url, **kw)
    if r is None or r.status_code != 200: return None
    try: return r.json()
    except ValueError: return None

def _post_json(url: str, payload: dict):
    r = _request("POST", url, data=json.dumps(payload), headers={"Content-Type": "application/json"})
    if r is None or r.status_code != 200: return None
    try: return r.json()
    except ValueError: return None

def strip_html(raw: str) -> str:
    if not raw: return ""
    text = html.unescape(raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()

def _iso_date(value) -> str:
    if value in (None, ""): return ""
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e12 else value
        try: return datetime.datetime.utcfromtimestamp(secs).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError): return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
    return m.group(1) if m else ""

def _domain(url: str) -> str:
    try: return urlparse(url or "").netloc
    except Exception: return ""

def _job(**kw) -> dict:
    """Job dict in job_remote.py's schema — title/company/url/site/posted_date/
    location_text/is_remote/job_type/pay_text/experience_text/description."""
    return {
        "title": (kw.get("title") or "").strip(),
        "company": kw.get("company") or "",
        "url": kw.get("url") or "",
        "site": kw.get("site") or _domain(kw.get("url")),
        "posted_date": kw.get("posted_date") or "",
        "location_text": (kw.get("location_text") or "").strip(),
        "is_remote": kw.get("is_remote"),
        "job_type": (kw.get("job_type") or "").strip().lower(),
        "pay_text": (kw.get("pay_text") or "").strip(),
        "experience_text": (kw.get("experience_text") or "").strip(),
        "description": (kw.get("description") or "").strip(),
    }


# ══════════════════════════════════════════════════════════════════
# ATS adapters — each returns the FULL job description, no crawl needed
# ══════════════════════════════════════════════════════════════════

def fetch_greenhouse(token: str) -> list:
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    if not data: return []
    out = []
    for j in data.get("jobs", []):
        out.append(_job(
            title=j.get("title"), url=j.get("absolute_url"),
            location_text=(j.get("location") or {}).get("name", ""),
            posted_date=_iso_date(j.get("first_published") or j.get("updated_at")),
            description=strip_html(j.get("content")),
        ))
    return out

def fetch_lever(token: str) -> list:
    data = _get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if not isinstance(data, list): return []
    out = []
    for j in data:
        cats = j.get("categories") or {}
        body = j.get("descriptionPlain") or strip_html(j.get("description"))
        extra = "\n".join(
            (lst.get("text", "") + "\n" + strip_html(lst.get("content", "")))
            for lst in (j.get("lists") or []) if isinstance(lst, dict))
        out.append(_job(
            title=j.get("text"), url=j.get("hostedUrl"),
            location_text=cats.get("location", ""),
            job_type=cats.get("commitment", ""),
            is_remote=("remote" in (cats.get("workplaceType") or "").lower()) or None,
            posted_date=_iso_date(j.get("createdAt")),
            description=(body + "\n\n" + extra).strip(),
        ))
    return out

def fetch_ashby(token: str) -> list:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    if not data: return []
    out = []
    for j in data.get("jobs", []):
        out.append(_job(
            title=j.get("title"), url=j.get("jobUrl") or j.get("applyUrl"),
            location_text=j.get("location", ""), is_remote=j.get("isRemote"),
            job_type=j.get("employmentType", ""),
            posted_date=_iso_date(j.get("publishedAt")),
            description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
        ))
    return out

def fetch_workable(account: str, max_detail: int = 40) -> list:
    listing = _post_json(
        f"https://apply.workable.com/api/v3/accounts/{account}/jobs",
        {"query": "", "location": [], "department": [], "worktype": [], "remote": []})
    if not listing: return []
    out = []
    for post in (listing.get("results") or [])[:max_detail]:
        sc = post.get("shortcode")
        if not sc: continue
        detail = _get_json(f"https://apply.workable.com/api/v2/accounts/{account}/jobs/{sc}") or {}
        loc = post.get("location") or {}
        city, country = (loc.get("city") or "").strip(), (loc.get("country") or "").strip()
        workplace = (post.get("workplace") or "").strip().lower()
        is_remote = bool(post.get("remote")) or workplace == "remote"
        loc_text = ", ".join(x for x in (city, country) if x)
        if is_remote: loc_text = f"Remote{' - ' + loc_text if loc_text else ''}"
        out.append(_job(
            title=post.get("title"), url=f"https://apply.workable.com/{account}/j/{sc}/",
            location_text=loc_text, is_remote=is_remote, job_type=post.get("type", ""),
            posted_date=_iso_date(post.get("published")),
            description=strip_html(detail.get("description")),
        ))
    return out

_FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever,
             "ashby": fetch_ashby, "workable": fetch_workable}


def fetch_ats_jobs(ats_batch: list) -> list:
    """Fetch + AI-title-filter jobs for a batch of {name, ats, token} companies.
    Stamps _fingerprint/_scraped_at to match scrape_jobs()'s convention so these
    jobs merge cleanly with Crawl4AI-scraped ones in job_remote.py's raw_jobs."""
    out = []
    for co in ats_batch:
        fn = _FETCHERS.get(co.get("ats"))
        if not fn:
            continue
        try:
            jobs = fn(co["token"])
        except Exception as e:
            print(f"    ⚠️  {co.get('name')} ({co.get('ats')}): {e}")
            continue
        relevant = [j for j in jobs if AI_TITLE_KEYWORDS.search(j.get("title") or "")]
        for j in relevant:
            j["company"] = co.get("name", "")
            fp = hashlib.md5(f"{j['title'].lower()}|{j['company'].lower()}".encode()).hexdigest()
            j["_fingerprint"] = fp
            j["_scraped_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            # Tells job_remote.py's prefilter to skip the Serper-week freshness
            # gate — an ATS board lists currently-OPEN roles, not week-old search
            # hits, so "posted 3 weeks ago" doesn't mean stale/unavailable.
            j["_source"] = "ats_direct"
        print(f"    ✓ {co.get('name')} ({co.get('ats')}): {len(jobs)} jobs, {len(relevant)} AI/ML-relevant")
        out.extend(relevant)
    return out


def serper_careers_urls(serper_batch: list, serper_api_key: str) -> list:
    """For no-ATS companies, search their careers domain via Serper for AI/ML/DS/FDE
    roles. Returns URLs for the EXISTING scrape_jobs() Crawl4AI path — unlike ATS
    jobs, a careers-page hit still needs a full-page crawl for the JD text."""
    if not serper_api_key or not serper_batch: return []
    urls = []
    api_url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": serper_api_key, "Content-Type": "application/json"}
    role_terms = '("AI engineer" OR "machine learning engineer" OR "LLM" OR "data scientist" OR "forward deployed engineer")'
    for co in serper_batch:
        domain = co.get("careers_domain") or ""
        name = co.get("name", "")
        query = (f"site:{domain} " if domain else f'"{name}" careers ') + role_terms
        try:
            resp = requests.post(api_url, headers=headers,
                data=json.dumps({"q": query, "num": 5, "tbs": "qdr:m"}), timeout=15)
            resp.raise_for_status()
            found = 0
            for r in resp.json().get("organic", []):
                link = r.get("link", "").strip()
                if link: urls.append(link); found += 1
            print(f"    ✓ {name}: {found} URLs")
        except Exception as e:
            print(f"    ⚠️  Serper error ({name}): {e}")
    return urls


# ══════════════════════════════════════════════════════════════════
# Registry + persistent unique-rotation selection
# ══════════════════════════════════════════════════════════════════

def load_pool() -> dict:
    if not os.path.exists(REGISTRY_PATH): return {"ats": [], "serper": []}
    try:
        with open(REGISTRY_PATH) as f: data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"  ⚠️  Could not read {REGISTRY_PATH}: {e}")
        return {"ats": [], "serper": []}
    return {"ats": data.get("ats") or [], "serper": data.get("serper") or []}

def _load_cursor() -> dict:
    if not os.path.exists(CURSOR_PATH): return {"done": []}
    try:
        with open(CURSOR_PATH) as f: return json.load(f)
    except Exception: return {"done": []}

def _save_cursor(state: dict):
    try:
        with open(CURSOR_PATH, "w") as f: json.dump(state, f)
    except Exception as e:
        print(f"  ⚠️  Could not save companies cursor: {e}")

def select_companies(n: int) -> tuple:
    """Pick up to `n` companies (ATS + Serper combined) that have NOT run yet this
    cycle. When fewer than `n` remain, finish the old cycle then top up from a
    fresh one (skipping anything already placed in THIS batch) — so one call
    never returns a duplicate, and across runs nothing repeats until the entire
    pool has had a turn. Returns (ats_batch, serper_batch)."""
    pool = load_pool()
    all_companies = ([{"kind": "ats", **c} for c in pool["ats"]] +
                     [{"kind": "serper", **c} for c in pool["serper"]])
    if not all_companies or n <= 0: return [], []
    state = _load_cursor()
    done = set(state.get("done", []))
    remaining = [c for c in all_companies if c.get("name") not in done]
    batch = remaining[:n]
    if len(batch) < n:
        batch_names = {c.get("name") for c in batch}
        topup = [c for c in all_companies if c.get("name") not in batch_names]
        batch = batch + topup[: n - len(batch)]
        done = set()  # cycle completed — next save starts the new cycle fresh
    done.update(c.get("name") for c in batch)
    _save_cursor({"done": sorted(done)})
    return ([c for c in batch if c["kind"] == "ats"],
            [c for c in batch if c["kind"] == "serper"])

def pool_status() -> tuple:
    """(total_companies, not_yet_run_this_cycle) — for the interactive prompt."""
    pool = load_pool()
    total = len(pool["ats"]) + len(pool["serper"])
    done = len(_load_cursor().get("done", []))
    return total, max(total - done, 0)

def prompt_company_count(default: int = 30) -> int:
    """Ask how many companies to scan this run. Falls back to `default` when stdin
    isn't a TTY (cron/CI) so an automated run never blocks on input()."""
    total, remaining = pool_status()
    if total == 0: return 0
    if not sys.stdin.isatty(): return default
    try:
        raw = input(f"  📇 Company pool: {total} total, {remaining} not yet run this cycle. "
                    f"How many to scan now? [default {default}]: ").strip()
        return int(raw) if raw else default
    except (ValueError, EOFError, KeyboardInterrupt):
        return default
