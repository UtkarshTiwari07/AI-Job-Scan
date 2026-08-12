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
# v12 — geo_ok(): india_mnc's "India-or-worldwide-remote" policy
# ══════════════════════════════════════════════════════════════════

india_profile = {"location": "India", "home_cities": []}
india_home = req.build_home_pattern(india_profile)

check("geo_ok: onsite India -> accept (True)",
     req.geo_ok("Bengaluru, India (Hybrid)", "", india_profile, india_home) is True)

check("geo_ok: 'Remote - Worldwide' -> accept (True)",
     req.geo_ok("Remote - Worldwide", "", india_profile, india_home) is True)

check("geo_ok: 'Remote, United States' with no worldwide language -> reject (False)",
     req.geo_ok("Remote, United States", "", india_profile, india_home, is_remote=False) is False)

check("geo_ok: structured foreign-onsite (location='Austin, TX', is_remote=False) -> reject (False) "
     "— this is what pre_kill_location's ambiguous-city rule used to miss on ATS jobs",
     req.geo_ok("Austin, TX", "", india_profile, india_home, is_remote=False, job_type="onsite") is False)

check("geo_ok: foreign city but is_remote=True, no lock phrase -> ambiguous (None), not a false reject",
     req.geo_ok("Austin, TX", "", india_profile, india_home, is_remote=True) is None)

check("geo_ok: bare 'Remote' with no country, no JD signal -> ambiguous (None), deferred to the LLM",
     req.geo_ok("Remote", "", india_profile, india_home) is None)

check("geo_ok: no location_text at all (markdown-sourced candidate) -> never hits the "
     "foreign-onsite branch, stays ambiguous (None) even with no remote signal",
     req.geo_ok("", "Build ML models with PyTorch.", india_profile, india_home) is None)

check("geo_ok: 'must be US-based' in description body -> reject (False)",
     req.geo_ok("", "Must be US-based. Build ML pipelines.", india_profile, india_home) is False)


# ══════════════════════════════════════════════════════════════════
# v14 — remote_priority(): remote-FIRST ordering tier (0 best). India mode
# accepts India-accessible AND worldwide-remote; the user wants do-it-from-
# anywhere / remote-India roles ranked above hybrid/onsite. Ordering, not filter.
# ══════════════════════════════════════════════════════════════════

check("remote_priority: worldwide remote -> tier 0 (top)",
     req.remote_priority("Remote - Worldwide", "", is_remote=True, profile=india_profile, home_pattern=india_home)
     == req.REMOTE_PRIORITY_WORLDWIDE)

check("remote_priority: remote India -> tier 1",
     req.remote_priority("Remote, India", "", is_remote=True, profile=india_profile, home_pattern=india_home)
     == req.REMOTE_PRIORITY_REMOTE_HOME)

check("remote_priority: onsite/hybrid India -> tier 2",
     req.remote_priority("Bengaluru, India (Hybrid)", "", is_remote=False, profile=india_profile, home_pattern=india_home)
     == req.REMOTE_PRIORITY_HOME_ONSITE)

check("remote_priority: bare 'Remote' (no country) -> tier 1, above onsite",
     req.remote_priority("Remote", "", is_remote=None, profile=india_profile, home_pattern=india_home)
     == req.REMOTE_PRIORITY_REMOTE_HOME)

check("remote_priority: no signal at all -> tier 3 (bottom)",
     req.remote_priority("", "", is_remote=None, profile=india_profile, home_pattern=india_home)
     == req.REMOTE_PRIORITY_OTHER)

check("remote_priority: worldwide beats remote-India beats onsite-India beats nothing",
     req.REMOTE_PRIORITY_WORLDWIDE < req.REMOTE_PRIORITY_REMOTE_HOME
     < req.REMOTE_PRIORITY_HOME_ONSITE < req.REMOTE_PRIORITY_OTHER)


# ══════════════════════════════════════════════════════════════════
# v12 — companies.py: ATS-URL shortcut parsing + Serper careers host filter
# ══════════════════════════════════════════════════════════════════

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "job"))
import companies as co

check("ats_job_from_url: Greenhouse per-job URL parses to (ats, token, id)",
     co.ats_job_from_url("https://job-boards.greenhouse.io/paytm/jobs/1234567")
     == ("greenhouse", "paytm", "1234567"))

check("ats_job_from_url: Lever per-job URL parses correctly",
     co.ats_job_from_url("https://jobs.lever.co/eternal/abcdef12-3456-7890-abcd-ef1234567890")
     == ("lever", "eternal", "abcdef12-3456-7890-abcd-ef1234567890"))

check("ats_job_from_url: a board-ROOT URL (no job id) does not match — falls back to the crawl path",
     co.ats_job_from_url("https://boards.greenhouse.io/sarvam") is None)

check("ats_job_from_url: non-ATS URL returns None",
     co.ats_job_from_url("https://example.com/careers/123") is None)

check("_is_ats_host: recognises a Greenhouse job-boards host",
     co._is_ats_host("job-boards.greenhouse.io"))

check("_is_ats_host: rejects an unrelated host",
     not co._is_ats_host("randomblog.com"))

check("_host_matches_company: company's own subdomain matches",
     co._host_matches_company("careers.sarvam.ai", "Sarvam AI"))

check("_host_matches_company: unrelated host does not match",
     not co._host_matches_company("randomblog.com", "Sarvam AI"))

check("serper careers query hygiene: negative sites cover linkedin/naukri/glassdoor(.co.in)/"
     "ambitionbox/indeed/wellfound/simplyhired(.co.in)/foundit.in (v13: added ccTLD siblings + foundit.in)",
     set(co._CAREERS_NEGATIVE_SITES) == {"linkedin.com", "naukri.com", "glassdoor.com", "glassdoor.co.in",
                                         "ambitionbox.com", "indeed.com", "wellfound.com",
                                         "simplyhired.com", "simplyhired.co.in", "foundit.in"})


# ══════════════════════════════════════════════════════════════════
# v13 — companies.py: query-recall fix (dropped role-terms + qdr:m), name
# cleaning, and the code-level negative-host backstop
# ══════════════════════════════════════════════════════════════════
# Every case here traces to the live diagnostic that found only 2/28 real
# companies got a correct hit under the old (role-term + remote/india +
# qdr:m) query — see job/companies.py's serper_careers_urls() docstring.

check("_core_company_name: strips a trailing parenthetical",
     co._core_company_name("Return Rabbit (By Auctane)") == "Return Rabbit")

check("_core_company_name: strips a YCombinator-batch parenthetical",
     co._core_company_name("SureBright (YCombinator S24)") == "SureBright")

check("_core_company_name: cuts at ' - ' (space-hyphen-space)",
     co._core_company_name("Skedler - Guidanz") == "Skedler")

check("_core_company_name: cuts at '|'",
     co._core_company_name("QuillAudits | Web3 Security") == "QuillAudits")

check("_core_company_name: cuts at the first parenthetical even with trailing words",
     co._core_company_name("System Two Advisors (Tara Capital)") == "System Two Advisors")

check("_core_company_name: does NOT touch a real '&' in a legal name",
     co._core_company_name("AI Technology & Systems") == "AI Technology & Systems")

check("_core_company_name: does NOT touch 'Hims & Hers'-style real names",
     co._core_company_name("Hims & Hers") == "Hims & Hers")

check("_core_company_name: does NOT touch a bare (unspaced) hyphen in a brand token",
     co._core_company_name("Biz-Tech Analytics") == "Biz-Tech Analytics")

check("_core_company_name: does NOT touch 'Master-O' (bare hyphen, no spaces)",
     co._core_company_name("Master-O") == "Master-O")

check("_core_company_name: never returns empty — degenerate input falls back to the original",
     co._core_company_name("()") == "()")

check("_host_matches_company now uses the cleaned name — a decorated legal name still "
     "matches its real (undecorated) domain",
     co._host_matches_company("returnrabbit.com", "Return Rabbit (By Auctane)"))

check("_is_negative_host: catches a ccTLD variant a -site: query exclusion could miss "
     "(glassdoor.co.in)",
     co._is_negative_host("www.glassdoor.co.in"))

check("_is_negative_host: catches simplyhired.co.in",
     co._is_negative_host("simplyhired.co.in"))

check("_is_negative_host: catches foundit.in",
     co._is_negative_host("foundit.in"))

check("_is_negative_host: does not false-positive on a real ATS host",
     not co._is_negative_host("job-boards.greenhouse.io"))

check("_is_negative_host: does not false-positive on an unrelated company domain",
     not co._is_negative_host("manychat.com"))


# ══════════════════════════════════════════════════════════════════
# v12 — companies.page_passes_hardfilter(): the $0 gate before any LLM call
# ══════════════════════════════════════════════════════════════════

_hf_profile = {"location": "India", "home_cities": [], "years_experience": 1.5, "yoe_slack": 0.5}
_hf_home = req.build_home_pattern(_hf_profile)

_good_page = ("# AI Engineer - Remote India\n"
             "We are hiring an AI Engineer to build LLM/RAG pipelines with PyTorch "
             "and LangChain. Requirements: 1-2 years of experience in AI/ML "
             "engineering. Location: Remote (India).\n") * 3

_senior_page = ("# Staff AI Engineer\nBuild LLM pipelines with PyTorch. "
               "Requires 6+ years of experience in ML engineering.\n") * 3

_no_ai_page = ("# Sales Executive\nDrive B2B SaaS sales for enterprise clients. "
              "1-2 years of experience required.\n") * 3

_over_exp_page = ("# AI Engineer\nBuild LLM/RAG pipelines with PyTorch and LangChain. "
                  "Requires at least 8 years of experience.\n") * 3

_locked_page = ("# AI Engineer\nBuild ML pipelines with PyTorch. Must be US-based. "
               "1-2 years of experience required.\n") * 3

check("hardfilter: real junior India-remote AI page passes",
     co.page_passes_hardfilter(_good_page, _hf_profile, _hf_home)[0] is True)

check("hardfilter: senior title (first line) rejected for $0",
     co.page_passes_hardfilter(_senior_page, _hf_profile, _hf_home)[0] is False)

check("hardfilter: no AI/ML relevance anywhere on the page rejected",
     co.page_passes_hardfilter(_no_ai_page, _hf_profile, _hf_home)[0] is False)

check("hardfilter: over-experience requirement (8 yrs vs 2-yr bracket) rejected",
     co.page_passes_hardfilter(_over_exp_page, _hf_profile, _hf_home)[0] is False)

check("hardfilter: confident foreign-lock phrase ('must be US-based') rejected",
     co.page_passes_hardfilter(_locked_page, _hf_profile, _hf_home)[0] is False)

check("hardfilter: thin page (<200 chars) rejected before any other check",
     co.page_passes_hardfilter("AI Engineer at Foo", _hf_profile, _hf_home)[0] is False)


# ══════════════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"TOTAL: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
print(f"{'='*60}")
sys.exit(1 if FAIL else 0)
