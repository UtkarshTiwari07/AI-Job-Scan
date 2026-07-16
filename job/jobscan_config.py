"""
jobscan_config.py — load the user profile + per-mode search/filter config.

Design intent
-------------
The *deterministic* pipeline logic lives in the job_*.py scripts and is NOT
changed by this module. All this module does is supply the DATA the pipeline
consumes — candidate profile, Serper query clusters, target sites, keyword /
reject lists, thresholds and prompt templates — from editable YAML files so any
AI/ML engineer can tailor the agent to their own profile without touching code.

Two YAML sources are merged:
  * config/profile.yaml         → the person (name, stack, metrics, geo, rate).
                                   Falls back to profile.example.yaml if absent.
  * config/<mode>.yaml          → the search tuning for a mode (remote /
                                   freelance / india_mnc): clusters, sites,
                                   filters, prompts. Ships with working defaults.

Anything a mode file omits is filled from small generic defaults below, so a
partial config still runs.
"""

import os
import re

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "PyYAML is required for AI-Job-Scan. Install it with:  pip install pyyaml"
    ) from exc

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
CONFIG_DIR = os.path.join(REPO_ROOT, "config")

VALID_MODES = ("remote", "freelance", "india_mnc")

# Generic fallbacks so a partial mode file still runs. The shipped config/*.yaml
# files contain complete, tuned values; these are only a safety net.
_GENERIC = {
    "max_posting_age_days": 3,
    "recruiter_terms": ["recruit", "staffing", "placement agency", "hr solutions", "manpower"],
    "seniority_reject_terms": ["senior", "lead", "principal", "manager", "director", "vp ", "head of"],
    "experience_years_reject": ["4+ years", "5+ years", "6+ years", "7+ years", "8+ years", "10+"],
    "education_reject_tokens": ["master's degree required", "phd required", "ph.d", "doctorate"],
    "title_reject_terms": [],
    "ai_relevance_keywords": [],
    "geo_lock_tokens": [],
    "remote_pass_tokens": [],
    "remote_reject_tokens": [],
    "target_sites": [],
    "portal_sites": [],
    "site_allowlist": [],
    "direct_urls": [],
    "serper_extra": {},
    "query_clusters": [],
    "scrape_instruction": "",
    "eval_system": "",
    "format_instructions": "",
    # v2 (ATS-registry) defaults
    "title_include_terms": [],
    "india_location_tokens": [],
    "report_min_score": 50,     # only matches scoring >= this reach report_*.json
    "per_company_cap": 5,       # keep at most N jobs per company after filtering
    "min_jd_chars": 200,        # reject thin/empty job descriptions
    "yoe_slack": 1,             # allow candidate_years + slack years of experience
    "jd_eval_chars": 3000,      # truncate JD sent to the LLM (cost control)
    "enrich_max_crawls": 30,    # v4: max crawl4ai backfills per run for thin-JD, link-only jobs
    # v3 (hybrid discovery) defaults
    "eval_max_candidates": 60,  # rank, then LLM-evaluate only the top N per run
    "junior_tokens": [],        # JD phrases that boost rank (falls back to filters.DEFAULT_JUNIOR_TOKENS)
    "linkedin_queries": [],     # [{keywords, location, f_e, f_wt}, ...] for discovery
    "serper_discovery_queries": [],  # broad (non-site-restricted) Serper queries
    "sources": {"ats": True, "linkedin": True, "remoteok": False, "hn": False},
}

# Which company registry file each mode reads (v2). Freelance keeps the old
# Serper/scrape path and has no registry.
COMPANIES_FILE = {
    "remote": "companies_remote.yaml",
    "india_mnc": "companies_india.yaml",
}


def _read_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_profile() -> dict:
    """Load the person profile, preferring profile.yaml then the example."""
    prof_path = os.path.join(CONFIG_DIR, "profile.yaml")
    example = os.path.join(CONFIG_DIR, "profile.example.yaml")
    if os.path.exists(prof_path):
        return _read_yaml(prof_path)
    if os.path.exists(example):
        print("  ⚠️  config/profile.yaml not found — using config/profile.example.yaml.")
        print("      Personalise it: run `python job/init_profile.py` OR copy the example")
        print("      to config/profile.yaml and edit your details.")
        return _read_yaml(example)
    print("  ⚠️  No profile found in config/. Using a placeholder profile.")
    return {}


def _compile(terms, word: bool = False):
    """Join a list of regex fragments into one compiled, case-insensitive regex.

    word=True wraps the whole alternation in \\b(...)\\b (used for seniority /
    recruiter word matches); word=False produces a bare (...) group (used for
    title-reject and AI-relevance patterns, whose fragments carry their own
    anchors/lookaheads). Returns None for an empty list.
    """
    terms = [t for t in (terms or []) if t]
    if not terms:
        return None
    body = "|".join(terms)
    pattern = r"\b(" + body + r")\b" if word else "(" + body + ")"
    return re.compile(pattern, re.IGNORECASE)


class Config:
    """Plain attribute bag consumed by the mode scripts."""
    pass


def load_config(mode: str) -> "Config":
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown mode '{mode}'. Expected one of {VALID_MODES}.")

    mode_path = os.path.join(CONFIG_DIR, f"{mode}.yaml")
    if not os.path.exists(mode_path):
        raise SystemExit(
            f"Missing config file: {mode_path}\n"
            f"Expected config/{mode}.yaml. Restore it from the repo or re-clone."
        )

    data = _read_yaml(mode_path)
    profile_raw = _load_profile()

    def g(key):
        val = data.get(key)
        return _GENERIC.get(key) if val is None else val

    cfg = Config()
    cfg.mode = mode

    # ── profile (the person) ─────────────────────────────────────────
    name = (profile_raw.get("name") or "Your Name").strip()
    headline = (profile_raw.get("headline") or "").strip()
    stack = (profile_raw.get("stack") or "").strip()
    if headline and stack:
        stack_full = f"{headline}. {stack}"
    elif headline:
        stack_full = headline
    else:
        stack_full = stack
    profile = {
        "name": name,
        "stack": stack_full,
        "metrics": (profile_raw.get("metrics") or "").strip(),
        "location": (profile_raw.get("location") or "").strip(),
    }
    _years = profile_raw.get("experience_years")
    cfg.experience_years = int(_years) if isinstance(_years, (int, float)) else 0
    cfg.target_roles = profile_raw.get("target_roles") or []

    # ── thresholds ───────────────────────────────────────────────────
    cfg.max_posting_age_days = int(g("max_posting_age_days"))
    cfg.report_min_score = int(g("report_min_score"))
    cfg.per_company_cap = int(g("per_company_cap"))
    cfg.min_jd_chars = int(g("min_jd_chars"))
    cfg.yoe_slack = int(g("yoe_slack"))
    cfg.jd_eval_chars = int(g("jd_eval_chars"))
    cfg.enrich_max_crawls = int(g("enrich_max_crawls"))
    cfg.eval_max_candidates = int(g("eval_max_candidates"))
    cfg.junior_tokens = g("junior_tokens")
    cfg.linkedin_queries = g("linkedin_queries")
    cfg.serper_discovery_queries = g("serper_discovery_queries")
    cfg.sources = {**_GENERIC["sources"], **(g("sources") or {})}

    # Freelance pay floor: the profile's salary_min overrides the mode default.
    min_pay = profile_raw.get("salary_min_usd_per_hour")
    if min_pay is None:
        min_pay = data.get("min_pay_per_hour_usd")
    cfg.min_pay_per_hour_usd = min_pay
    profile["min_rate"] = f"${int(min_pay)}/hr minimum" if min_pay is not None else ""
    cfg.profile = profile

    # ── search data ──────────────────────────────────────────────────
    cfg.query_clusters = g("query_clusters")
    cfg.target_sites = g("target_sites")
    cfg.portal_sites = g("portal_sites")
    cfg.site_allowlist = set(g("site_allowlist") or [])
    cfg.direct_urls = g("direct_urls")
    cfg.serper_extra = g("serper_extra") or {}

    # ── prompt templates ─────────────────────────────────────────────
    cfg.scrape_instruction = (data.get("scrape_instruction") or "").strip()
    cfg.eval_system = data.get("eval_system") or ""
    cfg.format_instructions = (data.get("format_instructions") or "").strip()

    # ── compiled filters (identical semantics to the old inline regex) ─
    cfg.recruiter_re = _compile(g("recruiter_terms"), word=True)
    cfg.title_reject_re = _compile(g("title_reject_terms"), word=False)
    cfg.seniority_reject_re = _compile(g("seniority_reject_terms"), word=True)
    cfg.ai_relevance_re = _compile(g("ai_relevance_keywords"), word=False)
    cfg.title_include_re = _compile(g("title_include_terms"), word=False)

    # ── plain substring token lists ──────────────────────────────────
    cfg.experience_years_reject = g("experience_years_reject")
    cfg.education_reject_tokens = g("education_reject_tokens")
    cfg.geo_lock_tokens = g("geo_lock_tokens")
    cfg.remote_pass_tokens = g("remote_pass_tokens")
    cfg.remote_reject_tokens = g("remote_reject_tokens")
    cfg.india_location_tokens = g("india_location_tokens")

    # ── company registry (v2 modes) ──────────────────────────────────
    cfg.companies_file = COMPANIES_FILE.get(mode)
    cfg.companies = []
    if cfg.companies_file:
        cpath = os.path.join(CONFIG_DIR, cfg.companies_file)
        if os.path.exists(cpath):
            cfg.companies = (_read_yaml(cpath) or {}).get("companies", [])

    return cfg
