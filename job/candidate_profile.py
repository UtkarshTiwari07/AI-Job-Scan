"""
job/candidate_profile.py — the SINGLE candidate-profile loader for all three job_*.py
scripts and job/requirements.py.

Generalizes what was previously ~18 hardcoded "1-2 YOE" / stack / geography
references duplicated — and already drifted out of sync — across
job_remote.py's, job_india_mnc.py's, and job_freelance.py's own
CANDIDATE_PROFILE dicts (e.g. only the India script mentioned Groq in its
metrics). Run `python job/init_profile.py` once (from a résumé, or a few
questions) to write config/profile.yaml; every script loads it through here.

AI/ML is NOT a profile field. This tool only ever searches AI/ML-domain roles —
that focus lives in job/requirements.py and each script's query clusters, and is
fixed by design, not derived from a résumé. The profile describes the
JOB-SEEKER: experience, stack, target role families, and home location.
"""

import os

try:
    import yaml
except ImportError:
    yaml = None

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.path.dirname(HERE), "config")
PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.yaml")

# Same defaults the tool originally shipped with (India-based, ~2 YOE, AI/ML
# roles) — a missing or partial config/profile.yaml degrades to this, so an
# un-personalized checkout still runs exactly like the original tool did.
DEFAULT_PROFILE = {
    "name": "Your Name",
    "headline": "AI Engineer",
    "stack": "Python, PyTorch, RAG, LLMs, LangChain, FastAPI",
    "metrics": "Add 2-3 real, quantified achievements here.",
    "location": "India",
    "home_cities": [],
    "years_experience": 2,
    "yoe_slack": 0.5,
    "target_role_families": ["AI Engineer", "ML Engineer", "LLM/RAG Engineer",
                              "Data Scientist", "Forward Deployed Engineer"],
    "education_ceiling": "bachelor's",
    "min_rate_usd_per_hour": 30,
}

_warned = False


def load_profile() -> dict:
    """Load config/profile.yaml, filling any missing/empty keys from
    DEFAULT_PROFILE — a partial or absent profile never crashes a script."""
    global _warned
    data = {}
    if yaml is None:
        if not _warned:
            print("  ⚠️  PyYAML not installed — using built-in defaults. `pip install pyyaml`.")
            _warned = True
    elif os.path.exists(PROFILE_PATH):
        try:
            with open(PROFILE_PATH) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            if not _warned:
                print(f"  ⚠️  Could not read {PROFILE_PATH} ({e}) — using defaults.")
                _warned = True
            data = {}
    else:
        if not _warned:
            print(f"  ⚠️  No config/profile.yaml found — using built-in defaults "
                  f"(India-based, {DEFAULT_PROFILE['years_experience']} YOE). "
                  f"Run `python job/init_profile.py` to personalize.")
            _warned = True
    merged = dict(DEFAULT_PROFILE)
    merged.update({k: v for k, v in data.items() if v not in (None, "")})
    return merged
