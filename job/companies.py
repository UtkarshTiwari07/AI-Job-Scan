"""
job/companies.py — company source shared by job_remote.py (mode="remote") and
job_india_mnc.py (mode="india").

Two fetch paths, chosen per company by the mode's registry YAML (built once by
job/probe_companies.py):
  - `ats:` entries have a live-verified Greenhouse/Lever/Ashby/Workable board —
    fetched DIRECTLY via that ATS's JSON API, which returns the FULL job
    description. These bypass Crawl4AI entirely (nothing to crawl — the API
    already gives clean, complete text).
  - `serper:` entries have no public ATS — `serper_careers_urls()` searches their
    own careers domain via Serper and returns URLs for the EXISTING Crawl4AI
    scrape_jobs() path.

`select_companies(n, mode)` picks the next N companies (across both lists) that
have not run yet in the current cycle, via a persistent on-disk cursor. Once
every company has had a turn, the cycle wraps. This guarantees a run never
re-scans the same company until the whole pool has been covered — the user's
explicit ask ("should be unique company running everytime"). "remote" and
"india" mode each keep their OWN registry + cursor, so scanning one doesn't
consume the other's rotation.
"""

import asyncio
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

import requirements as req

_DIR = os.path.dirname(__file__)
_REGISTRY_PATHS = {"remote": os.path.join(_DIR, "companies_remote.yaml"),
                   "india": os.path.join(_DIR, "companies_india.yaml")}
_CURSOR_PATHS = {"remote": os.path.join(_DIR, "companies_cursor.json"),
                 "india": os.path.join(_DIR, "companies_cursor_india.json")}
# Back-compat aliases — job/probe_companies.py and any external reference still
# uses these directly for the default ("remote") mode.
REGISTRY_PATH = _REGISTRY_PATHS["remote"]
CURSOR_PATH = _CURSOR_PATHS["remote"]

def _registry_path(mode: str) -> str:
    return _REGISTRY_PATHS.get(mode, REGISTRY_PATH)

def _cursor_path(mode: str) -> str:
    return _CURSOR_PATHS.get(mode, CURSOR_PATH)

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
    """Two-step: list every job WITHOUT its content field (cheap — `content=false`
    keeps the payload to title/url/location for the whole board), filter by
    AI-relevant title FIRST, then fetch the full content only for jobs that
    survive. v10 fetched full content for EVERY job on the board just to keep the
    ~10-25% that were AI-relevant — Cloudflare (280 jobs), MongoDB (398), Okta
    (348) were each fetched in full to end up with single-digit-to-20-ish
    matches. This is the single most wasteful call in the whole pipeline."""
    listing = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false")
    if not listing: return []
    relevant_meta = [j for j in listing.get("jobs", []) if AI_TITLE_KEYWORDS.search(j.get("title") or "")]
    out = []
    for j in relevant_meta:
        detail = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{j.get('id')}") or {}
        out.append(_job(
            title=detail.get("title") or j.get("title"),
            url=detail.get("absolute_url") or j.get("absolute_url"),
            location_text=((detail.get("location") or j.get("location")) or {}).get("name", ""),
            posted_date=_iso_date(detail.get("first_published") or detail.get("updated_at")),
            description=strip_html(detail.get("content")),
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

def _fmt_ashby_comp(comp) -> str:
    """Best-effort compensation string from an Ashby compensation block. v10
    requested this data (`?includeCompensation=true`) and then never mapped it
    to pay_text — paid for it, threw it away. Field names verified against a
    LIVE response (Ramp/Perplexity postings): the top-level string is
    `compensationTierSummary` (singular) — an earlier version of this function,
    copied from older reference code, looked for a nonexistent
    `compensationTierSummaries` (plural) key that never matches real API output
    and silently always returned ''."""
    if not comp or not isinstance(comp, dict): return ""
    top = comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary")
    if top: return str(top)[:200]
    tiers = comp.get("compensationTiers") or []
    parts = [t.get("tierSummary") or t.get("title") or "" for t in tiers if isinstance(t, dict)]
    return " | ".join(p for p in parts if p)[:200]

def fetch_ashby(token: str) -> list:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    if not data: return []
    out = []
    for j in data.get("jobs", []):
        out.append(_job(
            title=j.get("title"), url=j.get("jobUrl") or j.get("applyUrl"),
            location_text=j.get("location", ""), is_remote=j.get("isRemote"),
            job_type=j.get("employmentType", ""),
            pay_text=_fmt_ashby_comp(j.get("compensation")),
            posted_date=_iso_date(j.get("publishedAt")),
            description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
        ))
    return out

def fetch_workable(account: str, max_detail: int = 40) -> list:
    """Filters by AI-relevant title on the cheap v3 listing BEFORE spending a v2
    detail call — v10 took the first `max_detail` jobs regardless of relevance,
    wasting detail calls on "Sales Manager"/"Support Engineer" postings ahead of
    any real AI/ML role in the list."""
    listing = _post_json(
        f"https://apply.workable.com/api/v3/accounts/{account}/jobs",
        {"query": "", "location": [], "department": [], "worktype": [], "remote": []})
    if not listing: return []
    relevant_posts = [p for p in (listing.get("results") or [])
                      if AI_TITLE_KEYWORDS.search(p.get("title") or "")][:max_detail]
    out = []
    for post in relevant_posts:
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


# ══════════════════════════════════════════════════════════════════
# Per-JOB ATS fetchers — the v12 "ATS-URL shortcut" (revived from git 979065b)
# ══════════════════════════════════════════════════════════════════
# serper_careers_urls() below discovers individual posting URLs. When one of
# those URLs is itself a Greenhouse/Lever/Ashby/Workable PER-JOB link, hitting
# that ATS's per-job endpoint gets the full structured JD directly — no
# Crawl4AI, no LLM extraction, and (unlike the board-level fetchers above)
# never downloads the rest of that company's board.

_ATS_JOB_URL_PATTERNS = (
    ("greenhouse", re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([a-z0-9\-_]+)/jobs/(\d+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9\-_]+)/([0-9a-f\-]{8,36})", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9\-_]+)/([0-9a-f\-]{8,36})", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9\-_]+)/j/([A-Za-z0-9]+)", re.I)),
)


def ats_job_from_url(url: str):
    """Extract (ats, token, job_id) from a PER-JOB posting URL. Returns None if the
    URL isn't a recognised per-job pattern (a board-root URL, or a non-ATS company
    page) — the caller falls back to the host-filter + Crawl4AI crawl path."""
    if not url:
        return None
    for ats, pattern in _ATS_JOB_URL_PATTERNS:
        m = pattern.search(url)
        if m:
            return ats, m.group(1), m.group(2)
    return None


def fetch_greenhouse_job(token: str, job_id) -> dict:
    """Per-job Greenhouse endpoint — one job's full JD, no board download."""
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}")
    if not data:
        return None
    return _job(
        title=data.get("title"),
        url=data.get("absolute_url"),
        location_text=(data.get("location") or {}).get("name", ""),
        posted_date=_iso_date(data.get("first_published") or data.get("updated_at")),
        description=strip_html(data.get("content")),
    )


def fetch_lever_job(token: str, job_id) -> dict:
    """Per-job Lever endpoint — one job's full JD, no board download."""
    data = _get_json(f"https://api.lever.co/v0/postings/{token}/{job_id}")
    if not isinstance(data, dict) or not data:
        return None
    cats = data.get("categories") or {}
    body = data.get("descriptionPlain") or strip_html(data.get("description"))
    extra = "\n".join(
        (lst.get("text", "") + "\n" + strip_html(lst.get("content", "")))
        for lst in (data.get("lists") or []) if isinstance(lst, dict))
    return _job(
        title=data.get("text"), url=data.get("hostedUrl"),
        location_text=cats.get("location", ""),
        job_type=cats.get("commitment", ""),
        is_remote=("remote" in (cats.get("workplaceType") or "").lower()) or None,
        posted_date=_iso_date(data.get("createdAt")),
        description=(body + "\n\n" + extra).strip(),
    )


def fetch_ashby_job(token: str, job_id) -> dict:
    """Ashby has no per-job endpoint — fetches the board ONCE and returns only the
    one job whose URL contains the discovered job_id. A single board GET, but
    nothing else from that board is ever added to the caller's pool."""
    try:
        jobs = fetch_ashby(token)
    except Exception:
        return None
    for j in jobs:
        if job_id and job_id in (j.get("url") or ""):
            return j
    return None


def fetch_workable_job(account: str, shortcode: str) -> dict:
    """Per-job Workable v2 detail endpoint — no listing call needed."""
    d = _get_json(f"https://apply.workable.com/api/v2/accounts/{account}/jobs/{shortcode}")
    if not d:
        return None
    loc = d.get("location") or {}
    city, country = (loc.get("city") or "").strip(), (loc.get("country") or "").strip()
    workplace = (d.get("workplace") or "").strip().lower()
    is_remote = bool(d.get("remote")) or workplace == "remote"
    loc_text = ", ".join(x for x in (city, country) if x)
    if is_remote:
        loc_text = f"Remote{' - ' + loc_text if loc_text else ''}"
    return _job(
        title=d.get("title"), url=f"https://apply.workable.com/{account}/j/{shortcode}/",
        location_text=loc_text, is_remote=is_remote, job_type=d.get("type", ""),
        posted_date=_iso_date(d.get("published")),
        description=strip_html(d.get("description")),
    )


def fetch_job_by_ref(ats: str, token: str, job_id) -> dict:
    """Dispatch a per-job fetch for a job discovered by URL. Returns a normalised
    job dict or None — never raises (a probing caller shouldn't crash a whole
    batch on one bad token/job_id)."""
    try:
        if ats == "greenhouse": return fetch_greenhouse_job(token, job_id)
        if ats == "lever": return fetch_lever_job(token, job_id)
        if ats == "ashby": return fetch_ashby_job(token, job_id)
        if ats == "workable": return fetch_workable_job(token, job_id)
    except Exception:
        return None
    return None


def _manifest_row(name, kind, detail, jobs_found, ai_relevant, status):
    return {"name": name, "kind": kind, "detail": detail, "jobs_found": jobs_found,
            "ai_relevant": ai_relevant, "status": status}


def fetch_ats_jobs(ats_batch: list) -> tuple:
    """Fetch + AI-title-filter jobs for a batch of {name, ats, token} companies.
    Stamps _fingerprint/_scraped_at to match scrape_jobs()'s convention so these
    jobs merge cleanly with Crawl4AI-scraped ones in job_remote.py's raw_jobs.

    Returns (jobs, manifest) — manifest has ONE ROW PER COMPANY IN THE BATCH,
    including zero-yield and failed ones. v10 only printed a line for companies
    that fetched successfully; an unsupported `ats:` value or a failed HTTP call
    silently vanished with no record anywhere, so a scan that fetched nothing
    from half the batch looked identical to a clean run. This is what the
    caller writes into the report's run_manifest so "did it actually fetch
    company X" is answerable from the file, not just scrollback."""
    out, manifest = [], []
    for co in ats_batch:
        name, ats_kind = co.get("name", "?"), co.get("ats")
        fn = _FETCHERS.get(ats_kind)
        if not fn:
            print(f"    ⚠️  {name}: unsupported ats type {ats_kind!r} — skipped")
            manifest.append(_manifest_row(name, "ats", ats_kind, 0, 0, f"unsupported ats type {ats_kind!r}"))
            continue
        try:
            jobs = fn(co["token"])
        except Exception as e:
            print(f"    ⚠️  {name} ({ats_kind}): {e}")
            manifest.append(_manifest_row(name, "ats", ats_kind, 0, 0, f"error: {e}"))
            continue
        relevant = [j for j in jobs if AI_TITLE_KEYWORDS.search(j.get("title") or "")]
        for j in relevant:
            j["company"] = name
            fp = hashlib.md5(f"{j['title'].lower()}|{name.lower()}".encode()).hexdigest()
            j["_fingerprint"] = fp
            j["_scraped_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            # Tells job_remote.py's prefilter to skip the Serper-week freshness
            # gate — an ATS board lists currently-OPEN roles, not week-old search
            # hits, so "posted 3 weeks ago" doesn't mean stale/unavailable.
            j["_source"] = "ats_direct"
            # v12: human-readable source label for the run manifest/report (ats vs
            # careers vs board) — distinct from the internal "_source" flag above.
            j["source"] = "ats"
        print(f"    ✓ {name} ({ats_kind}): {len(jobs)} jobs, {len(relevant)} AI/ML-relevant")
        manifest.append(_manifest_row(name, "ats", ats_kind, len(jobs), len(relevant), "ok"))
        out.extend(relevant)
    return out, manifest


# v12: boards LinkedIn/Naukri/Glassdoor/Ambitionbox/Indeed/Wellfound already
# cover — excluded here so a careers-page query doesn't just re-find the same
# aggregator listing job_india_mnc.py's QUERY_CLUSTERS already search.
# v13: added the .co.in/.co regional siblings of glassdoor/simplyhired and
# foundit.in (already treated as noise elsewhere in this repo, v11) — all three
# were confirmed live, slipping through the -site: list as written.
_CAREERS_NEGATIVE_SITES = ["linkedin.com", "naukri.com", "glassdoor.com", "glassdoor.co.in",
                           "ambitionbox.com", "indeed.com", "wellfound.com",
                           "simplyhired.com", "simplyhired.co.in", "foundit.in"]

# v13: a CODE-level backstop for the same aggregator brands, checked against the
# actual returned host — not just the query string. _CAREERS_NEGATIVE_SITES above
# is a `-site:` exclusion Serper applies server-side; it's easy to miss a ccTLD
# variant there (confirmed live: glassdoor.co.in and simplyhired.co.in both
# slipped through untouched). Bare brand tokens (no TLD), mirroring how
# _ATS_HOST_TOKENS already works, catch any current or future TLD variant of a
# KNOWN brand automatically — closing that gap without hand-listing every ccTLD.
_NEGATIVE_HOST_TOKENS = ("linkedin.", "lnkd.in", "naukri", "glassdoor", "ambitionbox",
                         "indeed", "wellfound", "simplyhired", "foundit")


def _is_negative_host(host: str) -> bool:
    host = (host or "").lower()
    return any(tok in host for tok in _NEGATIVE_HOST_TOKENS)


# Hosts of ATS platforms a returned URL might be on directly — a hit here always
# survives the host filter below, ATS-URL-shortcut or not.
_ATS_HOST_TOKENS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
                    "smartrecruiters.com", "keka.com", "darwinbox.com", "darwinbox.in",
                    "freshteam.com", "zohorecruit.com")


def _is_ats_host(host: str) -> bool:
    host = (host or "").lower()
    return any(tok in host for tok in _ATS_HOST_TOKENS)


def _name_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


# v13: decoration that hides the real brand token from BOTH the Serper query
# (which wraps the whole raw name in quotes — decoration breaks the search
# outright, e.g. '"Return Rabbit (By Auctane)"' -> 0 results vs '"Return
# Rabbit"' -> 8) and the slug match below. Deliberately does NOT touch "&" or
# bare hyphens — checked against every real name in both registry YAMLs and
# found live legal names that would be corrupted by splitting on those ("AI
# Technology & Systems", "Inkling & Co", "Hims & Hers", "Biz-Tech Analytics",
# "Master-O", "U-Turn4Nature"). Confirmed-safe separators: a parenthetical span
# anywhere, "|", "/", or " - " (space-hyphen-space, never a bare hyphen).
_DECORATION_CUT = re.compile(r"\s*[|/]\s*|\s+-\s+")


def _core_company_name(name: str) -> str:
    """Best-effort primary brand token for SEARCHING/MATCHING only — never use
    this for display (job['company'], manifest rows keep the original name)."""
    n = (name or "").strip()
    n = re.sub(r"\([^)]*\)", "", n).strip()          # drop any parenthetical span(s)
    n = _DECORATION_CUT.split(n, maxsplit=1)[0].strip()  # cut at first |, /, or " - "
    n = n.strip(" -|/").strip()
    return n if len(n) >= 2 else (name or "").strip()   # never return an empty/degenerate query


def _host_matches_company(host: str, name: str) -> bool:
    """Loose check that a URL's host is plausibly the company's OWN domain (as
    opposed to some unrelated site Serper happened to return). Not exact —
    false positives here just mean one extra page gets crawled and then killed
    for $0 by page_passes_hardfilter(); false negatives just mean a real
    careers-page hit gets dropped, which is why the ATS-host check above and the
    ATS-URL shortcut are tried FIRST. `name` is cleaned via _core_company_name
    first — a decorated legal name's parenthetical/pipe/dash suffix will never
    appear in a real hostname either, so this is strictly safer, never riskier."""
    slug = _name_slug(_core_company_name(name))
    host_clean = re.sub(r"[^a-z0-9]+", "", (host or "").lower())
    return len(slug) >= 3 and bool(host_clean) and slug in host_clean


def serper_careers_urls(serper_batch: list, serper_api_key: str) -> tuple:
    """For no-ATS companies, search for their OWN careers page (never an
    aggregator — see _CAREERS_NEGATIVE_SITES) for AI/ML/DS/FDE roles.

    Returns (urls, direct_jobs, manifest):
      - `direct_jobs` — fully-fetched, structured job dicts for hits that turned
        out to be a per-job Greenhouse/Lever/Ashby/Workable URL (the "ATS-URL
        shortcut": parsed via ats_job_from_url + fetched via fetch_job_by_ref,
        skipping Crawl4AI and any LLM extraction entirely for these).
      - `urls` — everything else that passed the host filter (an ATS board-root
        URL, or plausibly the company's own domain) and still needs a
        Crawl4AI crawl (scrape_markdown()) for its JD text. A hit on neither an
        ATS host nor the company's own domain is DROPPED, not returned — v11
        returned every raw Serper hit including off-domain aggregator noise.

    If serper_api_key is missing, EVERY company in the batch still gets a
    manifest row explaining why it was skipped — v10 silently dropped the entire
    batch with zero output."""
    manifest = []
    if not serper_api_key:
        for co in serper_batch:
            manifest.append(_manifest_row(co.get("name", "?"), "serper",
                                          co.get("careers_domain"), 0, 0, "skipped: no SERPER_API_KEY"))
        if serper_batch:
            print(f"    ⚠️  SERPER_API_KEY not set — skipping all {len(serper_batch)} "
                  f"Serper-careers companies in this batch")
        return [], [], manifest
    urls, direct_jobs = [], []
    api_url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": serper_api_key, "Content-Type": "application/json"}
    negatives = " ".join(f"-site:{s}" for s in _CAREERS_NEGATIVE_SITES)
    for co in serper_batch:
        domain = co.get("careers_domain") or ""
        name = co.get("name", "")
        # v13: dropped the AI-role-term and (remote OR india) requirements —
        # live A/B testing proved this narrow, compound query suppresses recall
        # so badly that of 28 real companies tested (many with a genuine public
        # career page), only 2 got a correct hit; broadening to just this
        # (careers OR jobs OR hiring) form surfaced the company's REAL,
        # already-recognized ATS board in 4/5 spot-checks the narrow query
        # returned ZERO results for (Manychat, Motive, Modern Treasury,
        # YipitData). AI-relevance/experience/geo are re-checked for real,
        # deterministically, against the ACTUAL page content by
        # page_passes_hardfilter() below once a page is crawled — that's
        # always been this project's own design (push relevance decisions to
        # where real content exists); the query only needs to find the page.
        base = f"site:{domain}" if domain else f'"{_core_company_name(name)}"'
        query = f"{base} (careers OR jobs OR hiring) {negatives}"
        try:
            # v13: dropped "tbs": "qdr:m" (past-month freshness restriction) —
            # a SECOND, independently confirmed recall killer live-verified
            # alongside the query fix above. A company's careers PAGE is an
            # evergreen entity; requiring Google to have seen "fresh" content on
            # it within 30 days doesn't track whether the JOBS on it are
            # current (the ATS APIs/prefilter's own freshness gate already
            # handle that) — it just means Google prefers whatever unrelated
            # content it re-crawled most recently. Confirmed live: with qdr:m,
            # "YipitData"/"Return Rabbit" queries surfaced ONLY Instagram/
            # Facebook/Threads noise (freshly-indexed, irrelevant); removing it
            # immediately surfaced their real, current career pages
            # (yipitdata.com/careers, returnrabbit.com/careers/) instead.
            resp = requests.post(api_url, headers=headers,
                data=json.dumps({"q": query, "num": 8}), timeout=15)
            resp.raise_for_status()
            kept = direct = dropped = 0
            for r in resp.json().get("organic", []):
                link = r.get("link", "").strip()
                if not link:
                    continue
                ref = ats_job_from_url(link)
                if ref:
                    ats_kind, token, job_id = ref
                    job = fetch_job_by_ref(ats_kind, token, job_id)
                    if job and job.get("title") and AI_TITLE_KEYWORDS.search(job["title"]):
                        job["company"] = name
                        job["_fingerprint"] = hashlib.md5(f"{job['title'].lower()}|{name.lower()}".encode()).hexdigest()
                        job["_scraped_at"] = datetime.datetime.utcnow().isoformat() + "Z"
                        job["_source"] = "ats_direct"   # skip freshness gate, same as batch ATS jobs
                        job["source"] = "careers"        # v12-E manifest/report label
                        direct_jobs.append(job)
                        direct += 1
                        continue
                    dropped += 1
                    continue
                host = _domain(link)
                if _is_negative_host(host):
                    dropped += 1
                    continue
                if _is_ats_host(host) or _host_matches_company(host, name):
                    urls.append(link)
                    kept += 1
                else:
                    dropped += 1
            print(f"    ✓ {name}: {direct} ATS-direct jobs, {kept} URLs to crawl, {dropped} dropped (off-domain)")
            manifest.append(_manifest_row(name, "serper", domain, kept + direct, direct, "ok"))
        except Exception as e:
            print(f"    ⚠️  Serper error ({name}): {e}")
            manifest.append(_manifest_row(name, "serper", domain, 0, 0, f"error: {e}"))
    return urls, direct_jobs, manifest


# ══════════════════════════════════════════════════════════════════
# No-LLM crawl + deterministic hard-filter — the v12 efficiency fix
# ══════════════════════════════════════════════════════════════════
# v10/v11's Crawl4AI path used LLMExtractionStrategy, which fires >=1 DeepSeek
# call PER URL to structure the page into a ScrapedJob — BEFORE any relevance
# check. For ~200+ URLs/run that's >90% of LLM spend wasted on pages that turn
# out off-stack, senior-only, over-experience, or foreign-onsite. scrape_markdown
# reads Crawl4AI's raw markdown (zero DeepSeek); page_passes_hardfilter then
# applies the SAME deterministic gates job/requirements.py already uses on
# structured jobs, directly against that markdown — so a page dies for $0
# before a single LLM token is spent on it. Only survivors ever reach a
# DeepSeek call (the single combined evaluate+draft pass in job_india_mnc.py).

# v13: minimum text length to accept a scrapling HTTP-only fetch as "resolved"
# (skip Crawl4AI for that URL). Deliberately HIGHER than page_passes_hardfilter's
# own 200-char content-quality floor — a JS-shell page (all nav/footer
# boilerplate, zero real job content) can easily clear 200 chars while carrying
# no usable JD text (confirmed live on a real Ashby board). Using the same
# threshold here would let scrapling falsely "resolve" a page Crawl4AI might
# have actually rendered correctly, permanently forfeiting that URL's real
# content instead of falling through to the browser attempt.
_SCRAPLING_RESOLVED_MIN_CHARS = 600


async def _scrapling_first_pass(urls: list) -> dict:
    """Optional, zero-browser first attempt at each URL via scrapling's
    HTTP-only AsyncFetcher (curl_cffi TLS/header impersonation — no Chromium).
    Returns {url: text} for URLs judged "good enough" (see
    _SCRAPLING_RESOLVED_MIN_CHARS); everything else is simply absent and falls
    through to the Crawl4AI attempt below. impersonate="safari" specifically —
    live-verified that "chrome"/"firefox" TLS fingerprints get reset by at
    least one real network path this project has run behind, while "safari"
    does not; "safari" also successfully fetched a real Lever board here.
    Purely additive: works only on server-rendered pages (confirmed empty on a
    genuinely client-rendered Ashby SPA) — never a Crawl4AI replacement, and
    guarded so its absence is a silent no-op, not a failure."""
    try:
        from scrapling.fetchers import AsyncFetcher
    except ImportError:
        return {}
    out = {}
    sem = asyncio.Semaphore(8)

    async def _fetch_one(url: str):
        async with sem:
            try:
                resp = await AsyncFetcher.get(url, impersonate="safari", timeout=TIMEOUT)
            except Exception:
                return
            if not resp or getattr(resp, "status", 0) not in range(200, 300):
                return
            try:
                text = (resp.get_all_text(separator="\n", strip=True) or "").strip()
            except Exception:
                return
            if len(text) >= _SCRAPLING_RESOLVED_MIN_CHARS:
                out[url] = text

    await asyncio.gather(*(_fetch_one(u) for u in urls))
    return out


async def scrape_markdown(urls: list) -> dict:
    """Crawl4AI fetch with NO extraction_strategy. Returns {url: markdown_text} —
    one entry per URL that crawled successfully with non-empty content; a failed
    or empty crawl is simply absent (the caller treats a missing key as "no page
    text", same as any other unenrichable job).

    v13: tries scrapling's HTTP-only fetcher FIRST (cheap, no browser — see
    _scrapling_first_pass) and only sends whatever's left to Crawl4AI. This is
    additive, not a replacement: URLs scrapling can't resolve (JS-heavy SPAs, or
    scrapling simply not installed) still go through the exact same Crawl4AI
    path as before."""
    if not urls:
        return {}
    resolved = await _scrapling_first_pass(urls)
    remaining = [u for u in urls if u not in resolved]
    if resolved:
        print(f"    ℹ️  scrapling (no-browser): {len(resolved)}/{len(urls)} resolved, "
              f"{len(remaining)} left for Crawl4AI")
    if not remaining:
        return resolved
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    except ImportError as e:
        print(f"    ⚠️  crawl4ai not installed ({e}) — {len(remaining)} career/board URLs skipped "
              f"this run; ATS-direct jobs still proceed. `pip install -r requirements.txt`.")
        return resolved
    import logging
    logging.getLogger("crawl4ai").setLevel(logging.ERROR)

    run_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)
    browser_cfg = BrowserConfig(headless=True)
    out = dict(resolved)
    crawled = 0
    try:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            results = await crawler.arun_many(urls=remaining, config=run_cfg)
            for result in results:
                if not result.success:
                    continue
                md = getattr(result, "markdown", None)
                text = getattr(md, "raw_markdown", None) if md is not None else None
                if text is None:
                    text = md if isinstance(md, str) else ""
                text = (text or "").strip()
                if text:
                    out[result.url] = text
                    crawled += 1
    except Exception as e:
        # A browser that fails to LAUNCH (missing/mismatched Chromium build,
        # sandboxed egress, etc.) must not crash the whole run — ATS-direct
        # jobs (no crawl needed) should still get through. Per-URL failures are
        # already handled above via result.success; this only catches a launch
        # failure that never got to iterate results at all.
        print(f"    ⚠️  Crawl4AI browser failed to launch/run ({e}) — {len(remaining)} "
              f"career/board URLs skipped this run; ATS-direct jobs still proceed.")
        return out
    print(f"    ℹ️  crawl4ai: {crawled}/{len(remaining)} resolved")
    return out


def page_passes_hardfilter(markdown: str, profile: dict, home_pattern=None) -> tuple:
    """(passes: bool, reason: str) — deterministic AI-relevance + experience +
    seniority + geo gate applied directly to a Crawl4AI markdown page, reusing
    job/requirements.py's gates. No AI signal, senior-only, over-experience, or
    confident foreign-lock/foreign-onsite -> dropped here, for $0, before any
    LLM call. Anything not confidently rejectable passes through (True) — the
    combined extract+evaluate DeepSeek pass still gets the final say, exactly
    like the confident-reject-only design in job/requirements.py."""
    text = (markdown or "").strip()
    if len(text) < 200:
        return False, "thin/empty page (<200 chars)"
    # Best-effort "title" = first non-empty line (Crawl4AI markdown usually opens
    # with the page's H1) — used ONLY for the cheap seniority-in-title check. A
    # miss here never falsely rejects: classify_seniority just returns None and
    # the page proceeds, same as a title-less structured job would.
    first_line = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
    if not req.is_ai_relevant(first_line, text):
        return False, "no AI/ML relevance in page text"
    seniority = req.classify_seniority(first_line)
    if seniority:
        return False, seniority
    yoe_ok, yoe_detail = req.experience_ok("", text, profile.get("years_experience", 0),
                                           profile.get("yoe_slack", 0))
    if not yoe_ok:
        return False, yoe_detail
    if req.geo_ok("", text, profile, home_pattern) is False:
        return False, "geo: confident foreign-lock, no remote/India signal"
    return True, "passed hard filter"


# ══════════════════════════════════════════════════════════════════
# Registry + persistent unique-rotation selection
# ══════════════════════════════════════════════════════════════════

def load_pool(mode: str = "remote") -> dict:
    path = _registry_path(mode)
    if not os.path.exists(path): return {"ats": [], "serper": []}
    try:
        with open(path) as f: data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"  ⚠️  Could not read {path}: {e}")
        return {"ats": [], "serper": []}
    return {"ats": data.get("ats") or [], "serper": data.get("serper") or []}

def _load_cursor(mode: str = "remote") -> dict:
    path = _cursor_path(mode)
    if not os.path.exists(path): return {"done": []}
    try:
        with open(path) as f: return json.load(f)
    except Exception: return {"done": []}

def _save_cursor(state: dict, mode: str = "remote"):
    try:
        with open(_cursor_path(mode), "w") as f: json.dump(state, f)
    except Exception as e:
        print(f"  ⚠️  Could not save companies cursor ({mode}): {e}")

def _interleave(a: list, b: list) -> list:
    """Round-robin interleave so a prefix slice of the combined list is a
    proportional mix of both, instead of exhausting `a` before ever touching
    `b`. v10 used plain concatenation (ats + serper) — with 106 ats entries
    listed before 75 serper ones, requesting 50 companies returned 50 ATS-direct
    and ZERO Serper-careers, for at least the first two runs from a virgin
    cursor. The career-page path the user explicitly asked to lean on was dead."""
    out = []
    ia = ib = 0
    while ia < len(a) or ib < len(b):
        if ia < len(a): out.append(a[ia]); ia += 1
        if ib < len(b): out.append(b[ib]); ib += 1
    return out


def select_companies(n: int, mode: str = "remote") -> tuple:
    """Pick up to `n` companies (ATS + Serper interleaved) that have NOT run yet
    this cycle, from the given mode's registry ("remote" or "india" — each has
    its own registry + cursor, so scanning one never consumes the other's
    rotation). When fewer than `n` remain, top up by wrapping to the start of
    the pool — never placing the same company twice within THIS batch. Returns
    (ats_batch, serper_batch).

    Does NOT persist the cursor — call mark_companies_done() with the resulting
    fetch manifest afterward. v10 saved the cursor here, BEFORE any fetch was
    attempted, so a crash, a missing SERPER_API_KEY, or an unsupported `ats:`
    value still permanently marked those companies done having produced
    nothing."""
    pool = load_pool(mode)
    all_companies = _interleave([{"kind": "ats", **c} for c in pool["ats"]],
                                [{"kind": "serper", **c} for c in pool["serper"]])
    if not all_companies or n <= 0: return [], []
    done = set(_load_cursor(mode).get("done", []))
    remaining = [c for c in all_companies if c.get("name") not in done]
    batch = remaining[:n]
    if len(batch) < n:
        batch_names = {c.get("name") for c in batch}
        topup = [c for c in all_companies if c.get("name") not in batch_names]
        batch = batch + topup[: n - len(batch)]
    return ([c for c in batch if c["kind"] == "ats"],
            [c for c in batch if c["kind"] == "serper"])


def mark_companies_done(manifest: list, mode: str = "remote"):
    """Persist the cursor AFTER fetching. Only companies that were actually
    ATTEMPTED advance — a manifest row whose status starts with "skipped"
    (e.g. no SERPER_API_KEY) means we never even tried, so it doesn't burn that
    company's turn; it'll be retried for real next time. Once every company in
    the pool has been attempted at least once, the cycle resets so selection
    never stalls. This also fixes v10's cycle-boundary double-count: `done` now
    only ever grows from real per-company outcomes recorded here, not from an
    ad-hoc reset embedded inside selection."""
    pool = load_pool(mode)
    total = len(pool["ats"]) + len(pool["serper"])
    done = set(_load_cursor(mode).get("done", []))
    attempted = {m.get("name") for m in manifest if not str(m.get("status", "")).startswith("skipped")}
    done |= attempted
    if total and len(done) >= total:
        done = set()  # full cycle actually covered — start the next one fresh
    _save_cursor({"done": sorted(done)}, mode)

def pool_status(mode: str = "remote") -> tuple:
    """(total_companies, not_yet_run_this_cycle) — for the interactive prompt."""
    pool = load_pool(mode)
    total = len(pool["ats"]) + len(pool["serper"])
    done = len(_load_cursor(mode).get("done", []))
    return total, max(total - done, 0)

def prompt_company_count(default: int = 30, mode: str = "remote") -> int:
    """Ask how many companies to scan this run. Falls back to `default` when stdin
    isn't a TTY (cron/CI) so an automated run never blocks on input()."""
    total, remaining = pool_status(mode)
    if total == 0: return 0
    if not sys.stdin.isatty(): return default
    try:
        raw = input(f"  📇 Company pool ({mode}): {total} total, {remaining} not yet run this cycle. "
                    f"How many to scan now? [default {default}]: ").strip()
        return int(raw) if raw else default
    except (ValueError, EOFError, KeyboardInterrupt):
        return default
