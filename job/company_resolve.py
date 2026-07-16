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
_SERPER_TRIED = set()  # names Serper-probed this process (avoids re-probing a cached miss)

# ATS boards a Serper lookup can extract a token from (via sources.ats_from_url).
_SERPER_SITES = ("boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com")


def _resolve_via_serper(name: str, serper_key: str) -> dict:
    """v4 — find a company's OWN ATS board with a targeted Serper search when
    slug-guessing misses. Extracts (ats, token) straight from the first ATS URL in
    the results (never an aggregator), so the reported application_url is still the
    company's own board. This is what lifts the LinkedIn discovery yield: most
    surfaced companies don't have a guessable Greenhouse/Lever/Ashby slug."""
    sites = " OR ".join(f"site:{s}" for s in _SERPER_SITES)
    q = f'"{name}" (careers OR jobs OR hiring) ({sites})'
    r = sources._request(
        "POST", "https://google.serper.dev/search",
        headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
        data=json.dumps({"q": q, "num": 10}))
    if r is None or r.status_code != 200:
        return {}
    try:
        organic = r.json().get("organic", [])
    except ValueError:
        return {}
    for o in organic:
        hit = sources.ats_from_url(o.get("link", ""))
        if hit:
            return {"ats": hit[0], "token": hit[1]}
    return {}


def resolve(name: str, serper_key: str = None) -> dict:
    """Return {'ats':..., 'token':...} for the first live board found, or {} if none.

    Two stages: (1) guess ATS token slugs from the name and probe Ashby/Lever/
    Greenhouse; (2) if that misses and a `serper_key` is given, a targeted Serper
    search finds the company's own ATS board. Cached across calls and runs
    (CACHE_PATH). A previously-cached miss is retried through Serper ONCE per
    process when a key becomes available, so enabling Serper widens coverage
    without re-probing every dead name every run.
    """
    key = name.strip().lower()
    if not key:
        return {}
    cached = _CACHE.get(key)
    if cached:                       # non-empty hit → resolved earlier, reuse it
        return cached

    already_missed = (cached == {})  # a persisted "not found"
    if already_missed and (not serper_key or key in _SERPER_TRIED):
        return {}

    result = {}
    if not already_missed:           # first time we see this name → slug-guess probes
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

    if not result and serper_key:    # slug-guess missed → Serper board lookup
        _SERPER_TRIED.add(key)
        result = _resolve_via_serper(name, serper_key)

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
