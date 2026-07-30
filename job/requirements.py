"""
job/requirements.py — v11 shared deterministic policy engine.

WHY THIS FILE EXISTS (root-cause fix, not a style refactor):
v10's per-script `min_years_required()` read ONLY `experience_text`. No ATS adapter
(job/companies.py) ever populates that field — verified empirically: every
Greenhouse/Lever/Ashby/Workable job has `experience_text == ""`. So the "candidate
is 1-2 YOE, reject 3+" rule was a 100%-dead no-op on every company-page-sourced job,
the exact source this project just spent an entire pass building out. A live test
proved it: a job requiring "5+ years" and one requiring "at least 6 years" both
sailed through. This module fixes that by reading the FULL job description too, and
centralizes the three per-script gates (years, geo, seniority) that had already
drifted out of sync across job_remote.py / job_india_mnc.py / job_freelance.py
(e.g. only job_remote.py's seniority regex has "staff engineer").

DESIGN: two layers, not one.
  - The V-stage functions here (`min_years_required`, `pre_kill_location`,
    `classify_seniority`) are CHEAP, DETERMINISTIC, and CONSERVATIVE — they only
    reject what they are confident about, so they cost nothing and never produce a
    false "this is fine" the way the old code did. Genuinely ambiguous cases (a job
    whose only location signal is "Europe" or "Toronto" — no confident verdict is
    possible from the raw fields alone) fall through as PASS so they still reach the
    LLM extraction stage, which reads the full JD to make the call a keyword list
    structurally cannot.
  - `decide_match()` is the D-stage: a deterministic decision over the LLM's
    STRUCTURED extraction (see job_remote.py's REQUIREMENTS_SCHEMA), not over free
    text. Same input -> same verdict, every run, unit-testable — unlike letting an
    LLM both read the JD and make the accept/reject call in one unauditable step.
"""

import re
from typing import Optional


# ══════════════════════════════════════════════════════════════════
# YEARS-OF-EXPERIENCE — the fix for the 100%-dead gate
# ══════════════════════════════════════════════════════════════════

_ENTRY_WORDS = ("fresher", "entry level", "entry-level", "no experience",
                "0 years", "any level", "new grad", "recent graduate")

# Bare "<n>[-<m>] years" — safe on experience_text (a short, structured field with
# no unrelated prose) but NOT safe on free-form JD text (see below).
_BARE_YEARS = re.compile(r"\b(\d+)\+?\s*(?:-|to)?\s*\d*\+?\s*years?\b", re.IGNORECASE)

# On free-form JD text, a bare number is unreliable — "Founded in 2015, we've spent
# 10 years building AI products" is not a candidate requirement. Require the number
# to be anchored to language that actually states a REQUIREMENT: "<n>+ years of
# [...] experience", "experience: <n> years", or "minimum/at least <n> years". This
# is deliberately narrower than v10's (nonexistent) attempt — a modest, disclosed
# false-positive risk (a JD's company-history paragraph phrased as "N years of
# experience in fintech") in exchange for fixing a gate that was previously 100%
# blind on every ATS-sourced job.
_YEARS_IN_PROSE = [
    re.compile(r"\b(\d+)\+?\s*(?:-|to)?\s*\d*\+?\s*years?\s+(?:of\s+)?"
               r"(?:[a-z][a-z\-]*\s+){0,3}?experience\b", re.IGNORECASE),
    re.compile(r"\bexperience\s*(?:of|:)?\s*(\d+)\+?\s*(?:-|to)?\s*\d*\+?\s*years?\b", re.IGNORECASE),
    re.compile(r"\b(?:minimum(?:\s+of)?|at least|no less than)\s+(\d+)\+?\s*years?\b", re.IGNORECASE),
]


def min_years_required(experience_text: str = "", description: str = "") -> Optional[int]:
    """The MAXIMUM of every MINIMUM year-requirement found in experience_text (bare
    numbers OK) and description (prose, requires "experience"/"minimum"/"at least"
    context). Returns None when nothing is found — never blocks a candidate on
    silence. Taking the max (not the first match) fixes a real bug: a naive
    `re.search` on "1 year degree, 8 years industry experience" would return 1, not
    the binding 8."""
    minimums = []
    et = (experience_text or "").strip().lower()
    if et:
        if any(w in et for w in _ENTRY_WORDS):
            minimums.append(0)
        for m in _BARE_YEARS.finditer(et):
            minimums.append(int(m.group(1)))
    desc = (description or "").lower()
    if desc:
        for pattern in _YEARS_IN_PROSE:
            for m in pattern.finditer(desc):
                minimums.append(int(m.group(1)))
    return max(minimums) if minimums else None


def experience_ok(experience_text: str, description: str, profile_years: float, slack: float = 0.0) -> tuple:
    """(ok: bool, detail: str). ok=True when no requirement was found (benefit of
    the doubt — the LLM stage still sees the full JD) or the requirement is within
    profile_years + slack."""
    req = min_years_required(experience_text, description)
    if req is None:
        return True, "no explicit years requirement found"
    if req > profile_years + slack:
        return False, f"requires {req}+ yrs (profile: {profile_years}, slack: {slack})"
    return True, f"requires {req}+ yrs (within profile: {profile_years} +{slack} slack)"


# ══════════════════════════════════════════════════════════════════
# LOCATION — confident-reject only; ambiguous cases pass through to the LLM
# ══════════════════════════════════════════════════════════════════

# Default home-location tokens (India + its major tech cities) — used only when a
# profile doesn't specify its own `location`/`home_cities`. This keeps the existing
# India-based user's behavior unchanged with a zero-config profile, while
# `build_home_pattern()` below makes the actual home location a profile field, not
# a hardcoded constant — required for "anyone can fill their résumé" generalization.
_DEFAULT_HOME_TOKENS = ["india", "bangalore", "bengaluru", "mumbai", "delhi", "gurgaon",
                        "gurugram", "noida", "hyderabad", "pune", "chennai", "kolkata", "ncr"]


def build_home_pattern(profile: dict) -> "re.Pattern":
    """Build the home-location regex from a profile's `location` (e.g. "India",
    "Berlin, Germany") plus any explicit `home_cities` list. Falls back to the
    India-tech-city default when the profile gives nothing — so a bare/absent
    profile still behaves like the original India-based tool."""
    tokens = list(_DEFAULT_HOME_TOKENS)
    location = (profile or {}).get("location", "")
    home_cities = (profile or {}).get("home_cities") or []
    custom = [t.strip().lower() for t in ([location] + list(home_cities)) if t and t.strip()]
    if custom:
        # location is often "City, Country" — split on comma so each part becomes
        # its own whole-word token instead of one unmatchable multi-word phrase.
        tokens = []
        for c in custom:
            tokens.extend(p.strip() for p in c.split(",") if p.strip())
    escaped = [re.escape(t) for t in tokens]
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


# Default pattern (India) — used when callers don't pass a profile-derived one.
INDIA_LOCATION_TOKENS = re.compile(
    r"\b(" + "|".join(_DEFAULT_HOME_TOKENS) + r")\b", re.IGNORECASE,
)

WORLDWIDE_TOKENS = re.compile(
    r"\b(worldwide|anywhere|globally|global\s+remote|remote[\s\-]*first|"
    r"remote\s*[,\-]?\s*(?:global|worldwide|anywhere))\b", re.IGNORECASE,
)

# Confident, DECORATED region-lock phrases only — every entry requires a specific
# named country/region attached, unlike v10's bare "must be located in" (which
# matched "must be located in India" and rejected an explicitly-accepted case; A4).
# Deliberately does NOT try to enumerate every city — a bare city name ("Toronto",
# "Paris") is exactly the ambiguous case the LLM stage resolves by reading the JD,
# per the chosen design: a keyword list can't tell "HQ shown as Toronto, remote OK
# worldwide" from "must work from our Toronto office."
_REGION_LOCK_PATTERNS = [
    re.compile(r"\b(?:united states|u\.s\.a?\.?|usa|us)\s+only\b", re.IGNORECASE),
    re.compile(r"\bus[\-\s]based\b", re.IGNORECASE),
    re.compile(r"\bus\s+residents?\b", re.IGNORECASE),
    re.compile(r"\bus\s+citizens?\b", re.IGNORECASE),
    re.compile(r"\bmust\s+(?:be\s+(?:located|based)|reside)\s+in\s+the\s+"
               r"(?:us|u\.s\.a?\.?|united states|uk|united kingdom)\b", re.IGNORECASE),
    re.compile(r"\bauthorized\s+to\s+work\s+in\s+the\s+(?:us|united states)\b", re.IGNORECASE),
    re.compile(r"\bright\s+to\s+work\s+in\s+the\s+uk\b", re.IGNORECASE),
    re.compile(r"\buk[\-\s]based\b", re.IGNORECASE),
    re.compile(r"\buk\s+residents?\b", re.IGNORECASE),
    re.compile(r"\b(?:canada|australia)\s+only\b", re.IGNORECASE),
    re.compile(r"\b(?:eu|europe|emea)\s+only\b", re.IGNORECASE),
    re.compile(r"\beu\s+residents?\b", re.IGNORECASE),
    re.compile(r"\beu[\-\s]based\b", re.IGNORECASE),
    re.compile(r"\bmust\s+(?:be\s+eu[\-\s]based|reside\s+in\s+the\s+eu)\b", re.IGNORECASE),
    re.compile(r"\bremote\s*[\(\-,]\s*(?:us|usa|uk|canada|europe|eu|emea|united\s+states)\b", re.IGNORECASE),
    re.compile(r"\b(?:united\s+states|usa|us|uk|united\s+kingdom|canada|australia|europe|eu)"
               r"\s*\(\s*remote\s*\)", re.IGNORECASE),
    re.compile(r"\bus\s+permanent\s+resident\b", re.IGNORECASE),
    re.compile(r"\bgreen\s+card\b", re.IGNORECASE),
]


def pre_kill_location(location_text: str, description: str, home_pattern=None) -> Optional[str]:
    """Cheap, confident rejection ONLY. Returns a reason string if the job is
    unambiguously region-locked to somewhere that isn't the candidate's home
    location; returns None otherwise — meaning "not confidently rejectable here,"
    NOT "accepted." A bare country/city name alone is intentionally never enough to
    reject or accept — see module docstring. `home_pattern` should come from
    `build_home_pattern(profile)`; defaults to India for a bare/absent profile."""
    home_pattern = home_pattern or INDIA_LOCATION_TOKENS
    text = f"{location_text or ''} {description or ''}"
    if home_pattern.search(text):
        return None  # home-location-accessible is always accepted, never region-locked
    for pattern in _REGION_LOCK_PATTERNS:
        if pattern.search(text):
            return f"region-locked: matched {pattern.pattern[:40]}..."
    return None


def is_confidently_worldwide_or_home(location_text: str, description: str, is_remote, home_pattern=None) -> bool:
    """Fast-accept for the unambiguous case — an explicit 'worldwide'/'anywhere'
    phrase, or home-location presence. Saves an LLM extraction call when the
    answer is already obvious; anything else (including a bare 'Europe'/'Toronto'/
    'Remote' with no further qualifier) still goes to the LLM extraction stage."""
    home_pattern = home_pattern or INDIA_LOCATION_TOKENS
    text = f"{location_text or ''} {description or ''}"
    if home_pattern.search(text):
        return True
    return bool(WORLDWIDE_TOKENS.search(text))


# ══════════════════════════════════════════════════════════════════
# SENIORITY — expanded title regex (v10's missed 5 of 7 real senior titles)
# ══════════════════════════════════════════════════════════════════

SENIORITY_PATTERN = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|director|vp\b|head\s+of|"
    r"member\s+of\s+technical\s+staff|"
    r"(?:engineer|scientist|developer)\s+(?:ii|iii|iv|v|2|3|4|5)\b|"
    r"\b(?:ii|iii|iv|v)\s+(?:engineer|scientist|developer)\b|"
    r"\bsde\s*(?:ii|iii|iv|2|3|4)\b|"
    r"\blevel\s*[3-5]\b)",
    re.IGNORECASE,
)


def classify_seniority(title: str) -> Optional[str]:
    """Returns a rejection reason if the title carries a seniority signal v10's
    narrower regex missed (Staff Software Engineer, Sr. ML Engineer, Engineer III,
    Member of Technical Staff, SDE 2/3, ...), else None."""
    if not title:
        return None
    m = SENIORITY_PATTERN.search(title)
    return f"senior-level title: matched '{m.group(0)}'" if m else None


# ══════════════════════════════════════════════════════════════════
# STAGE D — deterministic decision over the LLM's STRUCTURED extraction
# ══════════════════════════════════════════════════════════════════
# job_remote.py's Phase X asks the LLM to extract facts (not judge them) into:
#   {min_years, max_years, location_policy: "worldwide_remote"|"country_locked"|
#    "onsite", eligible_countries: [...], home_eligible: bool, role_family: str}
# `home_eligible` means "accessible to someone based at profile['location']" — a
# generic field, not hardcoded to any one country, so this works for any résumé's
# home location, not just India.
# decide_match() is pure Python over that structure — same input, same verdict,
# every run, unlike letting the LLM both read the JD and rule on it in one step.

def decide_match(profile: dict, extraction: dict) -> tuple:
    """(is_match: bool, reason: str). `profile` needs years_experience, yoe_slack,
    target_role_families (list[str], case-insensitive substring match against the
    extracted role_family)."""
    min_years = extraction.get("min_years")
    if min_years is not None:
        cap = profile.get("years_experience", 0) + profile.get("yoe_slack", 0)
        if min_years > cap:
            return False, f"requires {min_years}+ yrs (profile allows up to {cap})"

    home_eligible = bool(extraction.get("home_eligible"))
    location_policy = (extraction.get("location_policy") or "").lower()
    if not (home_eligible or location_policy == "worldwide_remote"):
        return False, (f"location_policy={extraction.get('location_policy')!r}, "
                       f"home_eligible={home_eligible} — needs worldwide-remote or home location")

    role_family = (extraction.get("role_family") or "").lower()
    target_families = [f.lower() for f in profile.get("target_role_families", [])]
    if target_families and role_family:
        if not any(tf in role_family or role_family in tf for tf in target_families):
            return False, f"role_family {extraction.get('role_family')!r} not in target roles"

    return True, "meets years, location, and role requirements"
