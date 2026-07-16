"""
company_resolve.py — resolve a bare company NAME to a live ATS board, on demand.

Used by discovery.py when LinkedIn/Serper surfaces a company that isn't already
in the static config/companies_*.yaml registry: guesses a handful of plausible
Greenhouse/Lever/Ashby tokens from the name and probes them live, keeping the
first board that actually returns jobs. This is the same guess-and-probe logic
`probe_registry.py` uses to build the registry, generalized to run at scan time
for companies discovered dynamically.

Resolved companies are cached (in-memory + a small JSON file) so a repeat run
doesn't re-probe the network for a company it already looked up.
"""

import os
import re
import json

import sources

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".company_resolve_cache.json")

_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|labs?|"
    r"technologies|technology|tech|ai|io|hq|group|holdings)\b\.?", re.IGNORECASE)


def _slug_variants(name: str):
    """Yield plausible ATS token slugs for a company display name, best-guess first."""
    base = re.sub(r"[^a-z0-9 ]", "", name.lower())
    stripped = _SUFFIXES.sub("", base).strip()
    stripped = re.sub(r"\s+", " ", stripped).strip()
    candidates = []
    for s in (stripped, base):
        if not s:
            continue
        no_space = s.replace(" ", "")
        dashed = s.replace(" ", "-")
        for v in (no_space, dashed):
            if v and v not in candidates:
                candidates.append(v)
    return candidates[:4]  # bound the probe fan-out per company


def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass


_CACHE = _load_cache()


def resolve(name: str) -> dict:
    """Return {'ats':..., 'token':...} for the first live board found, or {} if none.

    Cached across calls (and across runs, via CACHE_PATH) — a company resolved
    once is never re-probed.
    """
    key = name.strip().lower()
    if not key:
        return {}
    if key in _CACHE:
        return _CACHE[key]

    result = {}
    for token in _slug_variants(name):
        for ats, fn in (("ashby", sources.fetch_ashby),
                        ("lever", sources.fetch_lever),
                        ("greenhouse", lambda t: sources.fetch_greenhouse(t, content=False))):
            try:
                jobs = fn(token)
            except Exception:
                jobs = []
            if jobs:
                result = {"ats": ats, "token": token}
                break
        if result:
            break

    _CACHE[key] = result
    _save_cache(_CACHE)
    return result


def fetch_jobs_for(company_entry: dict) -> list:
    """Fetch full jobs for a resolved {'ats','token'} entry, using the real adapter
    (with content=True for greenhouse, so callers get full descriptions)."""
    ats = company_entry.get("ats")
    token = company_entry.get("token")
    if ats == "greenhouse":
        return sources.fetch_greenhouse(token, content=True)
    if ats == "lever":
        return sources.fetch_lever(token)
    if ats == "ashby":
        return sources.fetch_ashby(token)
    return []
