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
WORLDWIDE_TOKENS = ["worldwide", "anywhere", "global", "globally", "any location"]


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
    """(pass: bool, reason: str|None) for worldwide-remote mode."""
    combined = f"{job.get('location_text','')} {job.get('jd_text','')}".lower()
    has_remote = (job.get("is_remote") is True
                  or job.get("workplace_type") == "remote"
                  or any(t in combined for t in cfg.remote_pass_tokens))
    has_onsite = any(t in combined for t in cfg.remote_reject_tokens)
    worldwide = any(t in combined for t in WORLDWIDE_TOKENS)
    geo_locked = any(t in combined for t in cfg.geo_lock_tokens)
    if not has_remote:
        return False, "Not remote"
    if has_onsite and not worldwide:
        return False, "On-site required"
    if geo_locked and not worldwide:
        return False, "Geo-locked (region-restricted remote)"
    return True, None


def _geo_ok_india(job, cfg):
    tokens = getattr(cfg, "india_location_tokens", None) or DEFAULT_INDIA_LOCATION_TOKENS
    loc = (job.get("location_text", "") or "").lower()
    # Location field is authoritative; fall back to a scan of the JD head.
    hay = loc if loc else (job.get("jd_text", "") or "")[:300].lower()
    if any(t in hay for t in tokens):
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
