"""
discovery.py — find INDIVIDUAL JOBS first, companies second (v6).

v5 and earlier fetched whole company boards and hoped the right jobs were inside
— a live run showed that fails: 12,276 jobs fetched, 76 candidates (0.6%), because
company boards are dominated by sales/senior/off-stack roles no filter tuning can
fix. v6 inverts this: the PRIMARY discovery signal (D1, serper_job_discover) is a
query-first Serper search restricted to ATS sites + junior/role terms, so a hit is
typically already a role-matched posting BEFORE anything is fetched (verified live:
7-9/10 hits per query were per-job posting URLs, not board roots). Company-first
signals survive only as SECONDARY, selection-gated sources:

  * D1 Serper job-level search (this module, serper_job_discover) — per-job URL hits
    go straight to sources.fetch_job_by_ref (one job, full JD, no board download);
    a board-root hit falls back to selection at fetch time (never blind ingestion).
  * D2 LinkedIn guest job search — the public, unauthenticated search-results page.
    Gives title + company + location + date per card with NO login and NO per-job
    detail fetch. We mine it for COMPANY NAMES, resolve each new name to its own ATS
    board, then keep ONLY that board's title-matching jobs (resolve_and_fetch_new
    applies sources.select_relevant) — never the whole board.

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


def serper_job_discover(queries: list, serper_key: str, num: int = 20) -> list:
    """v6 D1 — the PRIMARY discovery net: job-level, site-restricted Serper search.
    Each query targets an ATS site + role/junior terms, so a hit is typically
    already a role-matched posting (verified live this session: 7-9/10 hits per
    query were per-job posting URLs, not board roots). Returns a list of hit dicts:
      * per-job hit: {ats, token, job_id, url, title} — fetched as ONE job via
        sources.fetch_job_by_ref, never its whole board.
      * board-root hit (URL names a board but no job id parses): {ats, token,
        job_id: None, url, title} — the caller applies sources.select_relevant to
        that one board, never blind whole-board ingestion.
      * unresolved hit (URL matches neither ATS pattern): {ats: None, token: None,
        job_id: None, url, title} — the caller's crawl4ai fallback (Phase E) can
        still enrich it from the DETAIL page; nothing is ever fabricated.
    """
    hits = []
    if not serper_key:
        return hits
    seen = set()
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
            url = o.get("link", "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = o.get("title", "")
            job_ref = sources.ats_job_from_url(url)
            if job_ref:
                ats, token, job_id = job_ref
                hits.append({"ats": ats, "token": token, "job_id": job_id, "url": url, "title": title})
                continue
            board_ref = sources.ats_from_url(url)
            if board_ref:
                ats, token = board_ref
                hits.append({"ats": ats, "token": token, "job_id": None, "url": url, "title": title})
                continue
            hits.append({"ats": None, "token": None, "job_id": None, "url": url, "title": title})
    return hits


def resolve_and_fetch_new(company_names: set, known_tokens: set, cfg, cap: int = 40,
                          serper_key: str = None, select_cap: int = 3) -> tuple:
    """Resolve each NEW company name (not already in known_tokens) to a live ATS
    board and keep ONLY its title-matching jobs (sources.select_relevant, capped at
    `select_cap`) — never the whole board. Returns (jobs, summary). Bounded by `cap`
    resolve attempts per run (each is a few HTTP probes) to keep runtime sane. When a
    `serper_key` is given, company_resolve falls back to a Serper board lookup for
    names whose ATS slug can't be guessed — lifting the resolve yield."""
    jobs, resolved, unresolved = [], [], []
    for name in list(company_names)[:cap]:
        entry = company_resolve.resolve(name, serper_key=serper_key)
        if not entry:
            unresolved.append(name)
            continue
        if entry.get("token") in known_tokens:
            continue  # already covered by the static registry
        company_jobs = company_resolve.fetch_jobs_for(entry)
        selected = sources.select_relevant(company_jobs, cfg, cap=select_cap)
        if not selected:
            continue
        registry.tag_jobs(selected, name, entry["token"], tier=1, tags=["discovered"])
        jobs.extend(selected)
        resolved.append(f"{name}->{entry['ats']}:{entry['token']}")
    return jobs, {"resolved": resolved, "unresolved": unresolved,
                  "attempted": min(len(company_names), cap)}
