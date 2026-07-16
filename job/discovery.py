"""
discovery.py — find NEW companies to fetch from, beyond the static registry.

Two discovery signals, both cheap and both DISCOVERY-ONLY (they never become the
reported application URL — see the module docstring in company_resolve.py for why):

  * LinkedIn guest job search — the public, unauthenticated search-results page.
    Gives title + company + location + date per card with NO login and NO
    per-job detail fetch. We mine it purely for COMPANY NAMES + a sense of what
    roles are open, then resolve each new name to its own ATS board.
  * Serper broad search — a non-site-restricted search for junior AI/ML roles.
    When a hit is itself a Greenhouse/Lever/Ashby URL, the token is extracted
    directly from the URL (no name-guessing needed); otherwise the hit is
    discarded (Tier-3 fetching a company's own marketing site reliably enough
    to get a full JD needs crawl4ai, which isn't available in every environment
    — see sources.fetch_serper_domain for that best-effort path used elsewhere).

Every company discovered here is resolved through company_resolve.py, which
fetches its jobs through the SAME sources.py adapters as the static registry —
so a discovered company's reported jobs go through identical, full-JD
enrichment. Nothing discovered here is ever itself the reported URL.
"""

import re
import json

import sources
import company_resolve
import registry

LI_CARD_RE = re.compile(r"<li>(.*?)</li>", re.S)
LI_URN_RE = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')
LI_TITLE_RE = re.compile(r'<h3 class="base-search-card__title">\s*(.*?)\s*</h3>', re.S)
LI_COMPANY_RE = re.compile(r'<h4 class="base-search-card__subtitle">.*?>\s*(.*?)\s*</a>', re.S)
LI_LOCATION_RE = re.compile(r'<span class="job-search-card__location">\s*(.*?)\s*</span>', re.S)
LI_DATE_RE = re.compile(r'<time class="job-search-card__listdate"[^>]*datetime="([\d-]+)"')


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def linkedin_search(keywords: str, location: str = "", f_e: str = "", f_wt: str = "",
                    start: int = 0) -> list:
    """One page (~25 cards) of LinkedIn's public guest job-search results.
    f_e: experience level codes (e.g. '1,2' = internship,entry). f_wt: 2=remote.
    Returns [{title, company, location_text, posted_date, linkedin_id}]."""
    params = {"keywords": keywords, "start": str(start)}
    if location:
        params["location"] = location
    if f_e:
        params["f_E"] = f_e
    if f_wt:
        params["f_WT"] = f_wt
    qs = "&".join(f"{k}={v.replace(' ', '%20')}" for k, v in params.items())
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{qs}"
    r = sources._request("GET", url)
    if r is None or r.status_code != 200:
        return []
    html = r.text
    out = []
    for card in LI_CARD_RE.findall(html):
        urn = LI_URN_RE.search(card)
        title_m = LI_TITLE_RE.search(card)
        company_m = LI_COMPANY_RE.search(card)
        if not (urn and title_m and company_m):
            continue
        loc_m = LI_LOCATION_RE.search(card)
        date_m = LI_DATE_RE.search(card)
        out.append({
            "linkedin_id": urn.group(1),
            "title": _clean(re.sub(r"<[^>]+>", "", title_m.group(1))),
            "company": _clean(re.sub(r"<[^>]+>", "", company_m.group(1))),
            "location_text": _clean(loc_m.group(1)) if loc_m else "",
            "posted_date": date_m.group(1) if date_m else "",
        })
    return out


def linkedin_discover_companies(query_sets: list, max_pages_per_query: int = 2) -> dict:
    """Run several LinkedIn guest searches and return {company_name: [card, ...]}."""
    by_company = {}
    for qs in query_sets:
        for page in range(max_pages_per_query):
            cards = linkedin_search(
                keywords=qs.get("keywords", ""), location=qs.get("location", ""),
                f_e=qs.get("f_e", ""), f_wt=qs.get("f_wt", ""), start=page * 25)
            if not cards:
                break
            for c in cards:
                by_company.setdefault(c["company"], []).append(c)
    return by_company


def serper_broad_discover(queries: list, serper_key: str, num: int = 20) -> set:
    """Non-site-restricted Serper search; returns a set of (ats, token) tuples
    extracted directly from ATS URLs in the results. Never returns raw non-ATS
    URLs — those would need enrichment we can't reliably verify here."""
    found = set()
    if not serper_key:
        return found
    for q in queries:
        r = sources._request(
            "POST", "https://google.serper.dev/search",
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            data=json.dumps({"q": q, "num": num}))
        if r is None or r.status_code != 200:
            continue
        try:
            organic = r.json().get("organic", [])
        except ValueError:
            continue
        for o in organic:
            hit = sources.ats_from_url(o.get("link", ""))
            if hit:
                found.add(hit)
    return found


def resolve_and_fetch_new(company_names: set, known_tokens: set, cap: int = 40) -> tuple:
    """Resolve each NEW company name (not already in known_tokens) to a live ATS
    board and fetch its jobs. Returns (jobs, summary). Bounded by `cap` resolve
    attempts per run (each is a few HTTP probes) to keep runtime sane."""
    jobs, resolved, unresolved = [], [], []
    for name in list(company_names)[:cap]:
        entry = company_resolve.resolve(name)
        if not entry:
            unresolved.append(name)
            continue
        if entry.get("token") in known_tokens:
            continue  # already covered by the static registry
        company_jobs = company_resolve.fetch_jobs_for(entry)
        registry.tag_jobs(company_jobs, name, entry["token"], tier=1, tags=["discovered"])
        jobs.extend(company_jobs)
        resolved.append(f"{name}->{entry['ats']}:{entry['token']}")
    return jobs, {"resolved": resolved, "unresolved": unresolved,
                  "attempted": min(len(company_names), cap)}
