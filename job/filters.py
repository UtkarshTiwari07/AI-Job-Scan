"""
filters.py — deterministic Phase 3 pre-filter (no LLM, no hallucination).

Runs on the FULL job descriptions the ATS adapters return, so the gates that
used to be defeated by empty fields now actually work. Fail-fast order, cheapest
/ most decisive first. Every rejection is recorded with a reason for the audit
file. The final matching decision (Phase 4) stays deterministic too — see
pipeline.py.
"""

import re
import datetime

# Years-of-experience: capture "3-5 years", "5+ years", "5 years", "3 to 5 yrs".
# The trailing "years|yrs" is required, which keeps out "24/7", "top 10", etc.
_YOE_RE = re.compile(r"(\d{1,2})\s*(?:\+|to|-|–)?\s*(?:\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b",
                     re.IGNORECASE)

# Contexts where a number+years is a *requirement*, used to avoid matching
# "years of impact" style fluff — light touch, we still take the min.
DEFAULT_INDIA_LOCATION_TOKENS = [
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "delhi",
    "gurgaon", "gurugram", "noida", "chennai", "kolkata", "ahmedabad", "apac",
]


def parse_yoe(text: str):
    """Minimum years of experience a posting requires, or None if unstated."""
    if not text:
        return None
    mins = []
    for m in _YOE_RE.finditer(text.lower()):
        n = int(m.group(1))
        if 0 <= n <= 20:          # discard implausible / noise numbers
            mins.append(n)
    return min(mins) if mins else None


def candidate_bracket(cfg) -> int:
    years = cfg.experience_years if isinstance(getattr(cfg, "experience_years", None), int) else 0
    return (years or 0) + int(getattr(cfg, "yoe_slack", 1))


def parse_age_days(posted_date: str):
    """Age in days from a normalized 'YYYY-MM-DD' date, or None."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", posted_date or "")
    if not m:
        return None
    try:
        dt = datetime.datetime.strptime(m.group(1), "%Y-%m-%d")
        return max((datetime.datetime.utcnow() - dt).days, 0)
    except ValueError:
        return None


def _geo_ok_remote(job, cfg):
    """(pass: bool, reason: str|None) for worldwide-remote mode.

    Default-accessible policy: a job passes unless there's an EXPLICIT
    restriction. Two fixes vs. the original version (found while diagnosing a
    live run that filtered out nearly everything):
      1. The on-site/hybrid check now looks at `location_text` (a structured,
         authoritative ATS field, e.g. "San Francisco, CA (Hybrid)") instead of
         scanning the full JD body — a JD merely MENTIONING "hybrid" somewhere
         (e.g. "some teams work hybrid; this role is fully remote") no longer
         causes a false reject.
      2. Passing no longer requires a magic "worldwide/anywhere/global" word to
         be present — silence about geography now means accessible, not
         rejected. Only an EXPLICIT restriction (geo_lock_tokens: "US citizen",
         "must be located in the United States", etc.) rejects.
    """
    loc = (job.get("location_text", "") or "").lower()
    jd = (job.get("jd_text", "") or "").lower()
    has_remote = (job.get("is_remote") is True
                  or job.get("workplace_type") == "remote"
                  or any(t in loc for t in cfg.remote_pass_tokens)
                  or any(t in jd for t in cfg.remote_pass_tokens))
    has_onsite = (job.get("workplace_type") in ("hybrid", "onsite", "on-site")
                  or any(t in loc for t in cfg.remote_reject_tokens))
    geo_locked = any(t in f"{loc} {jd}" for t in cfg.geo_lock_tokens)
    if not has_remote:
        return False, "Not remote"
    if has_onsite:
        return False, "On-site/hybrid required"
    if geo_locked:
        return False, "Geo-locked (region-restricted remote)"
    return True, None


def _geo_ok_india(job, cfg):
    """India-accessible = India-located (onsite/hybrid/remote-India) OR a genuine
    worldwide-remote role a candidate in India can take.

    v5 change: worldwide-remote is now accepted. A live audit showed global
    remote-first firms hire India via EOR and tag the role "Remote"/"Worldwide",
    NOT "India" — so requiring an India token was filtering out exactly the roles
    that fill an India-based candidate's report. A remote role scoped to a foreign
    country still fails (via _geo_ok_remote's geo-lock check); the LLM makes the
    final accessibility call from the full text.
    """
    tokens = getattr(cfg, "india_location_tokens", None) or DEFAULT_INDIA_LOCATION_TOKENS
    loc = (job.get("location_text", "") or "").lower()
    # Location field is authoritative; fall back to a scan of the JD head.
    hay = loc if loc else (job.get("jd_text", "") or "")[:300].lower()
    if any(t in hay for t in tokens):
        return True, None
    # Not India-located → accept only if it's a genuine worldwide-remote role
    # (a foreign-country-scoped "Remote, US" role fails _geo_ok_remote's geo lock).
    ok, _ = _geo_ok_remote(job, cfg)
    if ok:
        return True, None
    return False, "Not India-accessible"


def prefilter(jobs, cfg, cross_run_seen, mode):
    """Return (candidates, rejected). Rejected items carry 'rejection_reason'."""
    candidates, rejected = [], []
    session_seen = set()
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"
    bracket_max = candidate_bracket(cfg)
    min_jd = int(getattr(cfg, "min_jd_chars", 200))
    max_age = int(getattr(cfg, "max_posting_age_days", 0))  # 0 = disabled (listed == open)

    def reject(job, reason):
        job["rejection_reason"] = reason
        rejected.append(job)

    for job in jobs:
        title = (job.get("title") or "").strip()
        title_l = title.lower()
        jd = (job.get("jd_text") or "")
        jd_l = jd.lower()
        fp = job.get("_fingerprint", "")

        # 1. Non-empty JD — kills the old "empty fields pass everything" bug
        if job.get("jd_len", len(jd)) < min_jd:
            reject(job, f"Thin/empty JD ({job.get('jd_len', len(jd))} chars)"); continue

        # 2. Dedup (cross-run then session)
        if fp and fp in cross_run_seen:
            reject(job, f"Already seen ({cross_run_seen[fp][:10]})"); continue
        if fp and fp in session_seen:
            reject(job, "Duplicate within run"); continue
        if fp:
            session_seen.add(fp)

        # 3. Freshness (disabled when max_age<=0; pass on unknown date)
        if max_age > 0:
            age = parse_age_days(job.get("posted_date", ""))
            if age is not None and age > max_age:
                reject(job, f"Stale: {age}d ago (max {max_age}d)"); continue

        # 4. Off-stack title hard-reject
        if cfg.title_reject_re and cfg.title_reject_re.search(title):
            reject(job, f"Off-stack title: {title}"); continue

        # 5. AI/ML relevance — title include OR full-JD relevance
        title_hit = cfg.title_include_re and cfg.title_include_re.search(title)
        jd_hit = cfg.ai_relevance_re and cfg.ai_relevance_re.search(jd_l)
        if not (title_hit or jd_hit):
            reject(job, f"Not AI/ML relevant: {title[:60]}"); continue

        # 6. Seniority — TITLE only (JDs say "work with senior engineers")
        if cfg.seniority_reject_re and cfg.seniority_reject_re.search(title):
            reject(job, f"Senior/lead title: {title}"); continue

        # 7. YOE — required minimum exceeds the candidate's bracket
        yoe = parse_yoe(f"{title} {jd}")
        if yoe is not None and yoe > bracket_max:
            reject(job, f"Requires {yoe}+ yrs (bracket {bracket_max})"); continue

        # 8. Advanced-degree requirement
        if any(tok in jd_l for tok in cfg.education_reject_tokens):
            reject(job, "Advanced degree required"); continue

        # 9. Geo / remote
        ok, why = (_geo_ok_remote(job, cfg) if mode == "remote"
                   else _geo_ok_india(job, cfg) if mode == "india_mnc"
                   else (True, None))
        if not ok:
            reject(job, why); continue

        job["_yoe_min"] = yoe
        candidates.append(job)

    # 10. Per-company cap — keep freshest N per company (stops one big board flooding)
    cap = int(getattr(cfg, "per_company_cap", 5))
    if cap > 0:
        by_company = {}
        for j in candidates:
            by_company.setdefault(j.get("company_token", j.get("company", "")), []).append(j)
        kept = []
        for token, group in by_company.items():
            group.sort(key=lambda j: j.get("posted_date", "") or "", reverse=True)
            kept.extend(group[:cap])
            for j in group[cap:]:
                reject(j, f"Per-company cap ({cap})")
        candidates = kept

    # Mark accepted in the cross-run cache
    for j in candidates:
        if j.get("_fingerprint"):
            cross_run_seen[j["_fingerprint"]] = now_iso

    return candidates, rejected


# ─────────────────────────── Phase R — rank ─────────────────────────

DEFAULT_JUNIOR_TOKENS = [
    "junior", "entry level", "entry-level", "0-2 years", "0-1 year", "1-2 years",
    "early career", "early-career", "new grad", "graduate", "fresher", "intern",
]

_STRUCTURED_SOURCES = {"greenhouse", "lever", "ashby", "workday"}

# v4 ranking-only signals (NOT gates — the LLM makes the authoritative location call).
DEFAULT_WORLDWIDE_TOKENS = ["worldwide", "anywhere", "globally", "global",
                            "remote-first", "fully remote"]
# A location field scoped to a specific foreign region is down-weighted so the capped
# eval budget favours likely-accessible roles. Deliberately small; the LLM verifies
# accessibility from the full raw text — this only orders who gets evaluated.
_COUNTRY_SCOPE_RE = re.compile(
    r"\b(united states|u\.s\.a?|us|usa|america|americas|uk|united kingdom|canada|"
    r"canadian|europe|european|emea|latam|germany|france|brazil|australia|singapore)\b",
    re.IGNORECASE)


def rank(jobs: list, cfg) -> list:
    """Deterministic pre-eval ranking so a capped LLM budget spends itself on the
    MOST promising candidates first — never a silent, unranked truncation.
    Scoring is a simple additive heuristic (title match, junior-language
    density, salary transparency, source structure, recency); ties broken by
    posted_date. Returns jobs sorted best-first; pipeline.py caps and audits
    the remainder with their rank so nothing just disappears.
    """
    junior_tokens = getattr(cfg, "junior_tokens", None) or DEFAULT_JUNIOR_TOKENS
    india_tokens = getattr(cfg, "india_location_tokens", None) or DEFAULT_INDIA_LOCATION_TOKENS
    mode = getattr(cfg, "mode", "")
    scored = []
    for j in jobs:
        title_l = (j.get("title") or "").lower()
        jd_l = (j.get("jd_text") or "").lower()
        loc_l = (j.get("location_text") or "").lower()
        score = 0
        if cfg.title_include_re and cfg.title_include_re.search(title_l):
            score += 3
        score += min(sum(1 for t in junior_tokens if t in jd_l), 3)
        if j.get("pay_text"):
            score += 1
        score += 2 if j.get("source") in _STRUCTURED_SOURCES else 1

        # Accessibility: boost worldwide / in-region roles, down-weight ones whose
        # location field is scoped to a foreign country the candidate can't work from.
        worldwide = any(t in loc_l for t in DEFAULT_WORLDWIDE_TOKENS)
        foreign_scope = bool(_COUNTRY_SCOPE_RE.search(loc_l))
        if mode == "india_mnc":
            if any(t in loc_l for t in india_tokens) or worldwide:
                score += 2
            elif foreign_scope:
                score -= 2
        else:  # remote — worldwide is ideal; a foreign-country-scoped remote is not
            if worldwide:
                score += 2
            elif foreign_scope:
                score -= 2

        scored.append((score, j.get("posted_date") or "", j))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [j for _, _, j in scored]
