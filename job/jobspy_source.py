"""
jobspy_source.py — the PRIMARY keyword-role search (v18): real job boards via
python-jobspy, returning FULL job descriptions with NO browser crawl.

WHY THIS EXISTS (root-cause fix, proven from two real 100-company runs):
The company-crawl pipeline was FINDING the right jobs and then THROWING THEM
AWAY. In both runs, the good AI-engineer postings (Sarvam, Wellfound, dozens of
LinkedIn /jobs/view/...) all landed in run_manifest.unverified_urls — i.e. they
were discovered by search but FAILED to crawl: LinkedIn returns HTTP 429
(anti-bot), and Wellfound/Naukri/Sarvam are client-rendered JS shells a headless
browser can't read here. Result: ~90% of real matches were lost, and the report
showed 1 (rejected) job per run.

python-jobspy (https://github.com/speedyapply/JobSpy) solves this at the source:
one scrape_jobs() call hits Indeed / LinkedIn / Google Jobs / Naukri (and more)
through their STRUCTURED endpoints and returns a uniform DataFrame — title,
company, location, date, salary, is_remote, and a full markdown `description` —
with NO browser needed. The job the crawler couldn't read is handed to us
already parsed. This is a keyword-role search ("AI Engineer", "LLM Engineer", …)
that actually produces readable, evaluable results, which is exactly what the
user asked for.

Import is lazy + guarded: python-jobspy is an optional dependency (like crawl4ai
and scrapling). If it's missing, fetch_jobspy returns [] plus a loud message the
caller prints — the primary source being OFF must never look like "the market
has no jobs this week."

HONEST LIMITS (documented, not hidden):
- Board scrapers are unofficial and can rate-limit or change markup. Each board
  fails INDEPENDENTLY (one search throwing never takes the others down), and a
  dead board shows as a loud zero in the run's source_mix, never as silent
  garbage. The ATS-company source (free JSON APIs) still runs in parallel.
- From a datacenter IP, Naukri has historically returned a 406/recaptcha and
  Google Jobs sometimes 0 results; both are expected to behave better from a
  residential Indian IP. This normalization layer is unit-tested here; live
  board coverage is verified on the user's machine.
"""


def _clean(v):
    """None-or-NaN-safe passthrough. JobSpy's DataFrame mixes None and
    float('nan') across columns depending on dtype inference; this avoids
    importing pandas/math just to test for NaN."""
    try:
        if v is None or (isinstance(v, float) and v != v):  # NaN != NaN
            return None
    except TypeError:
        pass
    return v


def _row_to_job(row, companies_mod):
    """Normalize one JobSpy DataFrame row into companies._job's schema, tagged
    so it flows through job_india_mnc.py's STRUCTURED (no-crawl) path exactly
    like an ATS-direct job.

    URL preference: `job_url_direct` (the company's OWN careers/apply page, when
    JobSpy resolved it) over the board's posting URL — keeps the project's
    "report the company's own apply page where possible" property. When a board
    exposes no direct URL (common on LinkedIn), the board posting URL is reported
    instead: still a real job with a real, fully-fetched description (NOT the old
    fabricated-thin-card failure mode), just visibly a board link.
    """
    title = _clean(row.get("title")) or ""
    if not title:
        return None
    direct = _clean(row.get("job_url_direct"))
    board_url = _clean(row.get("job_url")) or ""
    url = direct or board_url
    if not url:
        return None

    company = str(_clean(row.get("company")) or "Unknown")
    location = str(_clean(row.get("location")) or "")
    desc = str(_clean(row.get("description")) or "")
    site = str(_clean(row.get("site")) or "unknown")
    job_type = str(_clean(row.get("job_type")) or "")

    posted = row.get("date_posted")
    posted_clean = _clean(posted)
    posted_str = (posted.isoformat() if hasattr(posted, "isoformat")
                  else (str(posted_clean) if posted_clean else ""))

    is_remote = bool(_clean(row.get("is_remote")) or False)
    min_amt = _clean(row.get("min_amount"))
    max_amt = _clean(row.get("max_amount"))
    currency = _clean(row.get("currency"))
    pay_text = (f"{currency or ''} {min_amt or ''}-{max_amt or ''}".strip()
                if (min_amt or max_amt) else "")

    job = companies_mod._job(
        title=title, company=company, url=url, site=site,
        location_text=location, is_remote=is_remote, job_type=job_type,
        posted_date=posted_str, pay_text=pay_text, description=desc,
    )
    # Tags that route this job through the no-crawl structured path:
    #  - source = "jobspy_<site>"  -> visible per-board in the run's source_mix
    #  - _source = "jobspy"        -> prefilter skips the site-allowlist (Indeed/
    #    Google aren't on it) AND the 3-day freshness reject (JobSpy's hours_old
    #    already bounds recency at fetch time), same treatment as ATS-direct.
    job["source"] = f"jobspy_{site}"
    job["_source"] = "jobspy"
    return job


def fetch_jobspy(searches: list) -> tuple:
    """Run every configured JobSpy search. Returns (jobs, error):
      * jobs: a flat list of normalized job dicts (full description, no crawl),
        deduped by (title|company) so the same posting surfaced by two searches
        isn't double-counted.
      * error: a message string if python-jobspy itself isn't installed (whole
        source OFF), else None. A single search failing is caught per-search and
        simply contributes nothing.

    Each `search` is a dict passed to jobspy.scrape_jobs, e.g.
      {"sites": ["indeed","linkedin","google"], "search_term": "AI Engineer",
       "location": "India", "results_wanted": 30, "hours_old": 168,
       "country_indeed": "India", "google_search_term": "AI engineer jobs india"}
    'sites' is normalized to jobspy's 'site_name' kwarg.
    """
    try:
        import jobspy
    except ImportError:
        return [], ("python-jobspy is not installed — the PRIMARY keyword-search "
                    "source is OFF. Install it with: pip install python-jobspy")

    import companies  # only needed once jobspy itself is confirmed present

    jobs, seen = [], set()
    for search in searches or []:
        kwargs = {k: v for k, v in (search or {}).items() if v is not None}
        sites = kwargs.pop("sites", None)
        if sites:
            kwargs["site_name"] = sites
        if "site_name" not in kwargs or not kwargs.get("search_term"):
            continue
        kwargs.setdefault("results_wanted", 25)
        kwargs.setdefault("description_format", "markdown")
        label = f"{kwargs.get('site_name')} · {str(kwargs.get('search_term'))[:40]}"
        try:
            df = jobspy.scrape_jobs(**kwargs)
        except Exception as e:
            print(f"    ⚠️  jobspy search failed ({label}): {e}")
            continue
        if df is None or getattr(df, "empty", True):
            print(f"    · jobspy {label}: 0")
            continue
        added = 0
        for _, row in df.iterrows():
            job = _row_to_job(row, companies)
            if not job:
                continue
            key = f"{job['title'].lower()}|{job['company'].lower()}"
            if key in seen:
                continue
            seen.add(key)
            import hashlib, datetime
            job["_fingerprint"] = hashlib.md5(key.encode()).hexdigest()
            job["_scraped_at"] = (datetime.datetime.now(datetime.timezone.utc)
                                  .replace(tzinfo=None).isoformat() + "Z")
            jobs.append(job)
            added += 1
        print(f"    · jobspy {label}: {added}")
    return jobs, None
