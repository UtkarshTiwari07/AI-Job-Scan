"""
tests/test_requirements.py — regression suite for job/requirements.py.

This is THE testable core of v11's accuracy fix: the deterministic policy
engine that replaced a per-script pre-filter that had a 100%-dead
years-of-experience gate (it read only `experience_text`, which no ATS
adapter ever populates) plus a geo-lock list that missed bare country/city
names. Every case here traces back to either a catalogued defect (A1-A9 in
the v11 plan) or a live bug reproduced during that session.

No external test framework — plain asserts, so `pip install -r
requirements.txt` is the only dependency. Run:

  python tests/test_requirements.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "job"))
import requirements as req

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), "-", name)


# ══════════════════════════════════════════════════════════════════
# A1/A8 — years-of-experience: the core dead-gate fix
# ══════════════════════════════════════════════════════════════════

check("empty experience_text + empty description -> None (never blocks)",
      req.min_years_required("", "") is None)

check("A1 fix: '5+ years of experience' in DESCRIPTION (not experience_text) is caught",
      req.min_years_required("", "You will need 5+ years of experience deploying enterprise software.") == 5)

check("A1 fix: 'Minimum of 8 years experience required' in description is caught",
      req.min_years_required("", "Minimum of 8 years experience required.") == 8)

check("A1 fix: 'at least 6 years of research experience' in description is caught",
      req.min_years_required("", "We require at least 6 years of research experience.") == 6)

check("'3-5 years of experience' in description extracts the lower bound (3)",
      req.min_years_required("", "3-5 years of experience.") == 3)

check("A8 fix: max-of-minimums, not first match — '1 year degree, 8 years industry' -> 8",
      req.min_years_required("1 year degree, 8 years industry", "") == 8)

check("bare 'X years' with NO requirement anchor in prose does not false-positive "
      "(company-history text, not a candidate requirement)",
      req.min_years_required("", "Founded in 2015, we have 10 years of building AI products.") is None)

check("'0-2 years' in experience_text -> 0",
      req.min_years_required("0-2 years", "") == 0)

check("'fresher' -> 0", req.min_years_required("fresher", "") == 0)

ok, detail = req.experience_ok("", "2 years experience.", profile_years=1.5, slack=0.5)
check("experience_ok: 2 yrs required, profile allows 1.5+0.5=2.0 -> pass", ok)

ok, detail = req.experience_ok("", "5+ years of experience required.", profile_years=1.5, slack=0.5)
check("experience_ok: 5 yrs required > profile 2.0 -> reject", not ok)


# ══════════════════════════════════════════════════════════════════
# A2/A4 — location: word-boundary geo, no bare-country false negatives,
# no truncated-token false positives on the home country
# ══════════════════════════════════════════════════════════════════

check("A2 fix: bare 'Europe' is AMBIGUOUS (not auto-accepted) -> defers to the LLM stage",
      req.pre_kill_location("Europe", "") is None)
check("A2 fix: bare 'Toronto' is ambiguous, not silently accepted",
      req.pre_kill_location("Toronto", "") is None)
check("A2 fix: bare 'Canada' is ambiguous, not silently accepted",
      req.pre_kill_location("Canada", "") is None)

check("A4 fix: 'must be located in India' is NOT region-locked (was a false reject in v10)",
      req.pre_kill_location("must be located in India", "") is None)

check("no false positive: 'Columbus (Remote)' is not caught by the 'US (Remote)' pattern",
      req.pre_kill_location("Columbus (Remote)", "") is None)

check("confident reject: 'United States (Remote)' (live-observed real ATS phrasing)",
      req.pre_kill_location("United States (Remote)", "") is not None)
check("confident reject: 'Remote (US)' (decorated form)",
      req.pre_kill_location("Remote (US)", "") is not None)
check("confident reject: 'US Only'",
      req.pre_kill_location("US Only", "") is not None)

# Generalization: build_home_pattern makes the home location a profile field,
# not a hardcoded India constant.
berlin_profile = {"location": "Berlin, Germany", "home_cities": []}
berlin_pattern = req.build_home_pattern(berlin_profile)
check("build_home_pattern: Berlin profile accepts 'Berlin'", bool(berlin_pattern.search("Berlin")))
check("build_home_pattern: Berlin profile accepts 'Germany'", bool(berlin_pattern.search("Germany")))
check("build_home_pattern: Berlin profile does NOT accept 'India'", not berlin_pattern.search("India"))
check("zero-config default (no profile) still resolves India as home",
      bool(req.INDIA_LOCATION_TOKENS.search("Bangalore")))


# ══════════════════════════════════════════════════════════════════
# A6 — seniority: expanded regex catches titles v10's narrower one missed
# ══════════════════════════════════════════════════════════════════

for title in ["Member of Technical Staff, Research", "Staff Software Engineer",
             "Sr. ML Engineer", "Engineer III", "II Engineer", "Lead Data Scientist",
             "Principal Applied Scientist", "SDE 3"]:
    check(f"classify_seniority rejects {title!r}", req.classify_seniority(title) is not None)

for title in ["AI Engineer", "Data Scientist", "Forward Deployed Engineer", "ML Engineer"]:
    check(f"classify_seniority passes {title!r}", req.classify_seniority(title) is None)


# ══════════════════════════════════════════════════════════════════
# A5 — AI-domain positive gate (job_remote.py had none at all in v10)
# ══════════════════════════════════════════════════════════════════

for title, desc in [("Rust Systems Engineer", ""), ("Solutions Architect", ""), ("Accountant", "")]:
    check(f"is_ai_relevant rejects non-AI title {title!r}", not req.is_ai_relevant(title, desc))

check("is_ai_relevant accepts 'AI Engineer' by title alone", req.is_ai_relevant("AI Engineer", ""))
check("is_ai_relevant accepts a generic title with an AI/ML JD",
      req.is_ai_relevant("Software Engineer", "Build LLM pipelines with LangChain"))

# The Data Scientist / Forward Deployed Engineer nuance: DS is an ACCEPTED role
# family, but a bare "Data Scientist" title must NOT auto-pass — it needs real
# AI/ML signal in the JD, distinguishing it from pure BI/reporting work.
check("bare 'Data Scientist' + non-AI JD (A/B testing, pandas) -> correctly rejected",
      not req.is_ai_relevant("Data Scientist", "Statistical modeling and A/B testing, pandas"))
check("'Data Scientist' + real ML JD (PyTorch, LightGBM) -> correctly accepted",
      req.is_ai_relevant("Data Scientist", "Build ML models using PyTorch and LightGBM"))
check("'Forward Deployed Engineer' passes by title alone (no BI-style ambiguity)",
      req.is_ai_relevant("Forward Deployed Engineer", ""))


# ══════════════════════════════════════════════════════════════════
# Education ceiling — profile-driven, not a fixed "bachelor's only" assumption
# ══════════════════════════════════════════════════════════════════

bachelor_profile = {"education_ceiling": "bachelor's"}
phd_profile = {"education_ceiling": "phd"}
check("bachelor's profile rejected by a PhD requirement",
      not req.education_ok(bachelor_profile, "PhD required for this role"))
check("bachelor's profile passes with no education requirement stated",
      req.education_ok(bachelor_profile, "Great communication skills"))
check("PhD profile is never rejected on education, even by an explicit PhD requirement",
      req.education_ok(phd_profile, "PhD required for this role"))


# ══════════════════════════════════════════════════════════════════
# Stage D — deterministic decision over a structured extraction
# ══════════════════════════════════════════════════════════════════

profile = {"years_experience": 1.5, "yoe_slack": 0.5,
          "target_role_families": ["AI Engineer", "ML Engineer", "Forward Deployed Engineer", "Data Scientist"]}

ok, _ = req.decide_match(profile, {"min_years": 1, "location_policy": "worldwide_remote",
                                   "home_eligible": False, "role_family": "AI Engineer"})
check("decide_match: within-YOE + worldwide_remote + target role -> match", ok)

ok, _ = req.decide_match(profile, {"min_years": 5, "location_policy": "worldwide_remote",
                                   "home_eligible": False, "role_family": "AI Engineer"})
check("decide_match: over-YOE -> reject even with everything else matching", not ok)

ok, _ = req.decide_match(profile, {"min_years": 1, "location_policy": "country_locked",
                                   "home_eligible": False, "role_family": "AI Engineer"})
check("decide_match: country_locked + not home-eligible -> reject", not ok)

ok, _ = req.decide_match(profile, {"min_years": 1, "location_policy": "onsite",
                                   "home_eligible": True, "role_family": "AI Engineer"})
check("decide_match: onsite BUT home-eligible -> match", ok)

ok, _ = req.decide_match(profile, {"min_years": 1, "location_policy": "worldwide_remote",
                                   "home_eligible": False, "role_family": "Backend Engineer"})
check("decide_match: role_family not in target list -> reject", not ok)


# ══════════════════════════════════════════════════════════════════
# Regression: the exact live bug found this session (Cohere ATS jobs via
# job/companies.py). In the v10 pipeline, 3 of these 5 real jobs fully passed
# the deterministic pre-filter and would have reached — or in one case
# survived — the report. The fixed gates below must kill the clear violations
# for $0, before any LLM call.
# ══════════════════════════════════════════════════════════════════

LIVE_BUG_JOBS = [
    ("Forward Deployed Engineer, Agentic Platform (UK/Europe)", "Europe",
     "You will need 5+ years of experience deploying enterprise software."),
    ("Forward Deployed Engineer, Infrastructure Specialist", "United Kingdom",
     "Minimum of 8 years experience required."),
    ("Member of Technical Staff, Research", "Paris",
     "We require at least 6 years of research experience."),
    ("Member of Technical Staff - Sovereign AI", "Canada",
     "3-5 years of experience."),
    ("AI Engineer", "Toronto", "2 years experience."),
]

live_profile_years, live_profile_slack = 1.5, 0.5
would_pass_v_stage = 0
for title, loc, desc in LIVE_BUG_JOBS:
    seniority = req.classify_seniority(title)
    yoe_ok, _ = req.experience_ok("", desc, live_profile_years, live_profile_slack)
    geo_reject = req.pre_kill_location(loc, desc)
    if not seniority and yoe_ok and not geo_reject:
        would_pass_v_stage += 1

check("live-bug regression: at most 1/5 of the Cohere jobs pass the deterministic "
     f"stage alone (got {would_pass_v_stage}; v10's equivalent gates let 3/5 through)",
     would_pass_v_stage <= 1)

# The one survivor (Toronto, 2 yrs, no seniority signal) is a genuine location
# ambiguity, not a false accept — confirm it's specifically the Toronto case.
survivors = [t for t, l, d in LIVE_BUG_JOBS
            if not req.classify_seniority(t)
            and req.experience_ok("", d, live_profile_years, live_profile_slack)[0]
            and not req.pre_kill_location(l, d)]
check("the sole V-stage survivor is the genuinely-ambiguous Toronto/2yr case, "
     "not a false accept on a clear violation",
     survivors == ["AI Engineer"])


# ══════════════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"TOTAL: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
print(f"{'='*60}")
sys.exit(1 if FAIL else 0)
