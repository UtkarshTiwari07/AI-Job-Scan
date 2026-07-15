"""
sources.py — job source adapters.

Each adapter fetches jobs from a company's Applicant Tracking System (ATS) via
its public JSON API and returns a list of NORMALISED job dicts (schema below).
Unlike scraping search/listing pages, these APIs return the FULL job description,
which is what makes the downstream deterministic filters actually work.

Verified public endpoints (browser User-Agent required):
  greenhouse : GET  boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
  lever      : GET  api.lever.co/v0/postings/<token>?mode=json
  ashby      : GET  api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true
  workday    : POST <tenant>.<dc>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs   (list)
               + GET .../<externalPath>  (detail, for the full description)

Tier 3 (fragile fallback) is `fetch_serper_domain`: a Serper `site:<domain>`
search restricted to a company's OWN careers domain — never an aggregator.

Normalised job dict emitted by every adapter:
  title, url, location_text, is_remote(bool|None), workplace_type, employment_type,
  department, team, posted_date(YYYY-MM-DD or ""), pay_text, jd_text(full plain text),
  jd_len(int), source(str)
(registry.py adds company, company_token, tier, tags, source_tier, _fingerprint,
_fetched_at.)
"""

import re
import json
import time
import html
import datetime

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise SystemExit("The 'requests' package is required. pip install requests") from exc

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept": "application/json"})

TIMEOUT = 20
_RETRY_STATUS = {429, 500, 502, 503, 504}


# ─────────────────────────── helpers ────────────────────────────────

def _request(method: str, url: str, **kw):
    """HTTP with light backoff on rate-limit / transient 5xx. Returns Response or None."""
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
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _post_json(url: str, payload: dict):
    r = _request("POST", url, data=json.dumps(payload),
                 headers={"Content-Type": "application/json"})
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def strip_html(raw: str) -> str:
    """Turn an HTML job description into readable plain text."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def _iso_date(value) -> str:
    """Normalise a date to 'YYYY-MM-DD'. Accepts ISO strings and epoch ms/s."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e12 else value
        try:
            return datetime.datetime.utcfromtimestamp(secs).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
    return m.group(1) if m else ""


def _workday_posted_to_date(posted_on: str) -> str:
    """Convert Workday's relative 'Posted N Days Ago' text to a YYYY-MM-DD date."""
    if not posted_on:
        return ""
    t = posted_on.lower()
    now = datetime.datetime.utcnow()
    if "today" in t:
        return now.strftime("%Y-%m-%d")
    if "yesterday" in t:
        return (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return (now - datetime.timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\+?\s*(week|month)", t)
    if m:
        mult = 7 if m.group(2) == "week" else 30
        return (now - datetime.timedelta(days=int(m.group(1)) * mult)).strftime("%Y-%m-%d")
    return ""


def _job(**kw) -> dict:
    """Build a normalised job dict with defaults, deriving jd_len."""
    jd = (kw.get("jd_text") or "").strip()
    return {
        "title": (kw.get("title") or "").strip(),
        "url": kw.get("url") or "",
        "location_text": (kw.get("location_text") or "").strip(),
        "is_remote": kw.get("is_remote"),
        "workplace_type": (kw.get("workplace_type") or "").strip().lower(),
        "employment_type": (kw.get("employment_type") or "").strip().lower(),
        "department": (kw.get("department") or "").strip(),
        "team": (kw.get("team") or "").strip(),
        "posted_date": kw.get("posted_date") or "",
        "pay_text": (kw.get("pay_text") or "").strip(),
        "jd_text": jd,
        "jd_len": len(jd),
        "source": kw.get("source") or "",
    }


def _fmt_comp(comp) -> str:
    """Best-effort compensation string from an Ashby compensation block."""
    if not comp or not isinstance(comp, dict):
        return ""
    summaries = comp.get("compensationTierSummaries") or []
    parts = [s.get("compensationTierSummary") or s.get("title") or ""
             for s in summaries if isinstance(s, dict)]
    return " | ".join(p for p in parts if p)[:200]


# ─────────────────────────── adapters ───────────────────────────────

def fetch_greenhouse(token: str, content: bool = True) -> list:
    flag = "true" if content else "false"
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content={flag}")
    if not data:
        return []
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        depts = j.get("departments") or []
        out.append(_job(
            title=j.get("title"),
            url=j.get("absolute_url"),
            location_text=loc,
            department=depts[0].get("name") if depts else "",
            posted_date=_iso_date(j.get("first_published") or j.get("updated_at")),
            jd_text=strip_html(j.get("content")),
            source="greenhouse",
        ))
    return out


def fetch_lever(token: str) -> list:
    data = _get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        cats = j.get("categories") or {}
        # descriptionPlain is the body; `lists` holds requirements/nice-to-haves.
        body = j.get("descriptionPlain") or strip_html(j.get("description"))
        extra = "\n".join(
            (lst.get("text", "") + "\n" + strip_html(lst.get("content", "")))
            for lst in (j.get("lists") or []) if isinstance(lst, dict)
        )
        jd = (body + "\n\n" + extra).strip()
        out.append(_job(
            title=j.get("text"),
            url=j.get("hostedUrl"),
            location_text=cats.get("location", ""),
            employment_type=cats.get("commitment", ""),
            team=cats.get("team", ""),
            workplace_type=cats.get("workplaceType", ""),
            posted_date=_iso_date(j.get("createdAt")),
            jd_text=jd,
            source="lever",
        ))
    return out


def fetch_ashby(token: str) -> list:
    data = _get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    if not data:
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(_job(
            title=j.get("title"),
            url=j.get("jobUrl") or j.get("applyUrl"),
            location_text=j.get("location", ""),
            is_remote=j.get("isRemote"),
            workplace_type=j.get("workplaceType", ""),
            employment_type=j.get("employmentType", ""),
            department=j.get("department", ""),
            team=j.get("team", ""),
            posted_date=_iso_date(j.get("publishedAt")),
            pay_text=_fmt_comp(j.get("compensation")),
            jd_text=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
            source="ashby",
        ))
    return out


def fetch_workday(tenant: str, dc: str, site: str, search_text: str = "",
                  max_detail: int = 25) -> list:
    """Workday: list (searchText server-side) then per-job detail for the full JD.

    `max_detail` bounds the number of detail calls (Workday boards are huge).
    """
    base = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    listing = _post_json(f"{base}/jobs",
                         {"appliedFacets": {}, "limit": 20, "offset": 0,
                          "searchText": search_text})
    if not listing:
        return []
    out = []
    for post in (listing.get("jobPostings") or [])[:max_detail]:
        path = post.get("externalPath") or ""
        detail = _get_json(f"{base}{path}") if path else None
        info = (detail or {}).get("jobPostingInfo") or {}
        jd = strip_html(info.get("jobDescription"))
        out.append(_job(
            title=post.get("title"),
            url=info.get("externalUrl") or f"https://{tenant}.{dc}.myworkdayjobs.com{path}",
            location_text=info.get("location") or post.get("locationsText", ""),
            employment_type=info.get("timeType", ""),
            posted_date=_iso_date(info.get("startDate")) or _workday_posted_to_date(post.get("postedOn", "")),
            jd_text=jd,
            source="workday",
        ))
    return out


def fetch_serper_domain(domain: str, terms: str, serper_key: str,
                        num: int = 10, enrich: bool = True) -> list:
    """Tier-3 fallback: Serper `site:<domain>` discovery of real JD pages.

    Never touches aggregators — only the company's own careers domain. When
    `enrich` and crawl4ai are available, fetches each URL for the full JD;
    otherwise jobs come back with an empty JD (and get dropped by the
    non-empty-JD gate — an intentional, honest limitation of this tier).
    """
    if not serper_key:
        return []
    q = f"site:{domain} ({terms})"
    r = _request("POST", "https://google.serper.dev/search",
                 headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                 data=json.dumps({"q": q, "num": num}))
    if r is None or r.status_code != 200:
        return []
    try:
        organic = r.json().get("organic", [])
    except ValueError:
        return []
    urls = [o.get("link", "") for o in organic if o.get("link")]
    jd_by_url = _crawl_jds(urls) if enrich else {}
    out = []
    for o in organic:
        url = o.get("link", "")
        jd = jd_by_url.get(url, "") or o.get("snippet", "")
        out.append(_job(
            title=o.get("title", ""),
            url=url,
            jd_text=jd,
            source="serper",
        ))
    return out


def _crawl_jds(urls: list) -> dict:
    """Best-effort full-JD fetch for Tier-3 URLs via crawl4ai (optional dep)."""
    if not urls:
        return {}
    try:
        import asyncio
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    except Exception:
        return {}

    async def _run():
        results = {}
        cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
        async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
            for res in await crawler.arun_many(urls=urls, config=cfg):
                if res.success:
                    results[res.url] = (res.markdown or "")[:6000]
        return results

    try:
        return asyncio.run(_run())
    except Exception as e:
        print(f"    ⚠️  crawl4ai enrichment skipped: {e}")
        return {}


ADAPTERS = {
    "greenhouse": lambda c, **kw: fetch_greenhouse(c["token"]),
    "lever": lambda c, **kw: fetch_lever(c["token"]),
    "ashby": lambda c, **kw: fetch_ashby(c["token"]),
    "workday": lambda c, **kw: fetch_workday(c["tenant"], c["dc"], c["site"],
                                             search_text=kw.get("search_text", "")),
    "serper": lambda c, **kw: fetch_serper_domain(c["domain"], kw.get("search_text", ""),
                                                  kw.get("serper_key", "")),
}
