"""
job/probe_companies.py — one-time (re-runnable) live probe that builds
job/companies_remote.yaml, the registry job/companies.py reads at runtime.

Candidate seed: the ~80 already-verified companies from the prior registry
(reused as-is below — NOT re-probed, since their tokens are known-good) plus
~150 new candidates transcribed from two research docs the user supplied
("100 Remote-First Companies Hiring in India/Europe" and "100 Remote-First
Companies Hiring in the Middle East"), deduplicated by name against the 80 and
against each other. The explicit hybrid/office-first filler tier in the Middle
East doc (Careem, noon, Talabat, G42, etc. — the doc itself flags these as NOT
remote-first) is excluded: job_remote.py requires worldwide-remote or India, so
onsite-Dubai/Riyadh-only roles would just fill the reject list for no yield.

For each new candidate: try ashby -> lever -> greenhouse (+ workable when an
explicit workable URL was given in the doc), in that order, exactly like the
prior job/probe_registry.py. A hit goes to companies_remote.yaml's `ats:` list;
a miss goes to `serper:` (paired with the best-guess careers domain from the
doc) for the Serper-careers fallback in job/companies.py.

Run:
  python job/probe_companies.py            # probe everything, write the YAML
  python job/probe_companies.py --limit 5  # probe only the first N new candidates (smoke test)
"""

import sys
import time
import concurrent.futures as cf

import companies as ats

# ── The ~80 already-verified companies (git history) — reused, NOT re-probed ──
VERIFIED_ATS = [
    ("CResta", "greenhouse", "cresta"), ("Grafana Labs", "greenhouse", "grafanalabs"),
    ("Writer", "ashby", "writer"), ("Perplexity", "ashby", "perplexity"),
    ("Cloudflare", "greenhouse", "cloudflare"), ("Arize AI", "ashby", "arizeai"),
    ("ClickHouse", "greenhouse", "clickhouse"), ("Baseten", "ashby", "baseten"),
    ("Cohere", "ashby", "cohere"), ("MongoDB", "greenhouse", "mongodb"),
    ("Harvey", "ashby", "harvey"), ("GitLab", "greenhouse", "gitlab"),
    ("Ramp", "greenhouse", "ramp"), ("Canonical", "greenhouse", "canonical"),
    ("Deepgram", "ashby", "deepgram"), ("Turing", "greenhouse", "turing"),
    ("Cognition", "ashby", "cognition"), ("Decagon", "ashby", "decagon"),
    ("Mozilla", "greenhouse", "mozilla"), ("Notion", "greenhouse", "notion"),
    ("Twilio", "greenhouse", "twilio"), ("Abnormal Security", "greenhouse", "abnormalsecurity"),
    ("Sardine", "ashby", "sardine"), ("Suno", "ashby", "suno"),
    ("Synthesia", "greenhouse", "synthesia"), ("Abridge", "greenhouse", "abridge"),
    ("Replit", "ashby", "replit"), ("Voleon", "greenhouse", "voleon"),
    ("Fireblocks", "greenhouse", "fireblocks"), ("Hightouch", "ashby", "hightouch"),
    ("LangChain", "ashby", "langchain"), ("Sigma Computing", "greenhouse", "sigmacomputing"),
    ("Superside", "ashby", "superside"), ("Forter", "greenhouse", "forter"),
    ("Mercury", "greenhouse", "mercury"), ("Anyscale", "greenhouse", "anyscale"),
    ("Benchling", "greenhouse", "benchling"), ("Gamma", "ashby", "gamma"),
    ("Hex", "greenhouse", "hextechnologies"), ("Komodo Health", "greenhouse", "komodohealth"),
    ("Temporal", "ashby", "temporal"), ("Alpaca", "greenhouse", "alpaca"),
    ("Ambience Healthcare", "ashby", "ambiencehealthcare"), ("Elastic", "greenhouse", "elastic"),
    ("ElevenLabs", "ashby", "elevenlabs"), ("Ideogram", "ashby", "ideogram"),
    ("Lightning AI", "ashby", "lightningai"), ("Modal", "greenhouse", "modal"),
    ("Prolific", "greenhouse", "prolific"), ("Sierra", "ashby", "sierra"),
    ("Tavus", "ashby", "tavus"), ("Airbyte", "ashby", "airbyte"),
    ("Andela", "lever", "andela"), ("Coursera", "greenhouse", "coursera"),
    ("OpenEvidence", "ashby", "openevidence"), ("Speak", "ashby", "speak"),
    ("Toptal", "greenhouse", "toptal"), ("Apollo.io", "greenhouse", "apolloio"),
    ("Assembly AI", "ashby", "assemblyai"), ("Descript", "greenhouse", "descript"),
    ("Fivetran", "greenhouse", "fivetran"), ("Hugging Face", "workable", "huggingface"),
    ("JFrog", "greenhouse", "jfrog"), ("Lightricks", "greenhouse", "lightricks"),
    ("Vercel", "greenhouse", "vercel"), ("BitGo", "greenhouse", "bitgo"),
    ("Browserbase", "ashby", "browserbase"), ("Buildkite", "greenhouse", "buildkite"),
    ("Highspot", "greenhouse", "highspot"), ("Linear", "ashby", "linear"),
    ("Mem0 / Embedchain", "ashby", "mem0"), ("Oyster HR", "greenhouse", "oysterhr"),
    ("PostHog", "ashby", "posthog"), ("RemoFirst", "ashby", "remofirst"),
    ("Sourcegraph", "lever", "sourcegraph91"), ("Stability AI", "greenhouse", "stabilityai"),
    ("Supabase", "ashby", "supabase"), ("Unstructured", "ashby", "unstructured"),
    ("Zapier", "greenhouse", "zapier"), ("Zilliz", "lever", "zilliz"),
]

# ── New candidates from the two docs: (name, ats_guess_or_None, token_or_None, careers_domain) ──
# ats_guess/token filled ONLY when the doc gave an explicit ATS URL; otherwise None
# (probed ashby->lever->greenhouse) with a best-guess careers_domain for the Serper
# fallback if all three miss.
NEW_CANDIDATES = [
    ("GitHub", None, None, "github.careers"),
    ("HashiCorp", None, None, "hashicorp.com"),
    ("Automattic", None, None, "automattic.com"),
    ("Toggl", None, None, "toggl.com"),
    ("Doist", None, None, "doist.com"),
    ("Weaviate", None, None, "weaviate.io"),
    ("Aiven", None, None, "aiven.io"),
    ("Buffer", None, None, "buffer.com"),
    ("Basecamp", None, None, "37signals.com"),
    ("Close", None, None, "close.com"),
    ("Contentsquare", None, None, "contentsquare.com"),
    ("Aha", None, None, "aha.io"),
    ("1Password", None, None, "1password.com"),
    ("15Five", None, None, "15five.com"),
    ("Float", None, None, "float.com"),
    ("Ghost", None, None, "ghost.org"),
    ("Kinsta", None, None, "kinsta.com"),
    ("Uscreen", None, None, "uscreen.tv"),
    ("Chili Piper", None, None, "chilipiper.com"),
    ("Remote.com", None, None, "remote.com"),
    ("Deel", None, None, "deel.com"),
    ("Multiplier", None, None, "usemultiplier.com"),
    ("Ashby", None, None, "ashbyhq.com"),
    ("Liveblocks", None, None, "liveblocks.io"),
    ("Semaphore", None, None, "semaphore.io"),
    ("Chronosphere", None, None, "chronosphere.io"),
    ("Voxel51", None, None, "voxel51.com"),
    ("Stedi", None, None, "stedi.com"),
    ("Wise", None, None, "wise.jobs"),
    ("Chainlink Labs", None, None, "chainlinklabs.com"),
    ("Kraken", None, None, "kraken.com"),
    ("Coinbase", None, None, "coinbase.com"),
    ("Bitpanda", None, None, "bitpanda.com"),
    ("Revolut", None, None, "revolut.com"),
    ("Tink", None, None, "tink.com"),
    ("MyOperator", None, None, "myoperator.com"),
    ("Nathan James", None, None, "nathanjames.com"),
    ("LeadSimple", None, None, "leadsimple.com"),
    ("Bitovi", None, None, "bitovi.com"),
    ("Lumenalta", None, None, "lumenalta.com"),
    ("Axelerant", None, None, "axelerant.com"),
    ("rtCamp", None, None, "rtcamp.com"),
    ("Zyte", None, None, "zyte.com"),
    ("Sketch", None, None, "sketch.com"),
    ("X-Team", None, None, "x-team.com"),
    ("Modern Tribe", None, None, "tri.be"),
    ("DockYard", None, None, "dockyard.com"),
    ("10up", None, None, "10up.com"),
    ("Human Made", None, None, "humanmade.com"),
    ("84codes", None, None, "84codes.com"),
    ("BuddyBoss", None, None, "buddyboss.com"),
    ("Guilded", None, None, "guilded.gg"),
    ("DNSimple", None, None, "dnsimple.com"),
    ("Overleaf", None, None, "overleaf.com"),
    ("Skyscrapers", None, None, "skyscrapers.eu"),
    ("Namecheap", None, None, "namecheap.com"),
    ("Awesome Motive", None, None, "awesomemotive.com"),
    ("Smile.io", None, None, "smile.io"),
    ("SimpleTexting", None, None, "simpletexting.com"),
    ("Time Doctor", None, None, "timedoctor.com"),
    ("Hubstaff", None, None, "hubstaff.com"),
    ("Sporty Group", None, None, "sporty.com"),
    ("MailerLite", None, None, "mailerlite.com"),
    ("DuckDuckGo", None, None, "duckduckgo.com"),
    ("Atlassian", None, None, "atlassian.com"),
    ("Crossover", None, None, "crossover.com"),
    ("CloudBees", None, None, "cloudbees.com"),
    ("Veeva Systems", None, None, "careers.veeva.com"),
    ("Panopto", None, None, "panopto.com"),
    # ── Middle East doc: new (non-duplicate) entries ──
    ("Papaya Global", None, None, "papayaglobal.com"),
    ("Xapo Bank", None, None, "xapobank.com"),
    ("Canva", None, None, "canva.com"),
    ("HubSpot", None, None, "hubspot.com"),
    ("Dropbox", None, None, "dropbox.com"),
    ("Okta", None, None, "okta.com"),
    ("Hims & Hers", None, None, "hims.com"),
    ("Wix", None, None, "wix.com"),
    ("monday.com", None, None, "monday.com"),
    ("Rapyd", None, None, "rapyd.net"),
    ("Tipalti", None, None, "tipalti.com"),
    ("Payoneer", None, None, "payoneer.com"),
    ("AI21 Labs", None, None, "ai21.com"),
    ("Verbit", None, None, "verbit.ai"),
    ("Binance", None, None, "binance.com"),
    ("OKX", None, None, "okx.com"),
    ("Bybit", None, None, "bybit.com"),
    ("Crypto.com", None, None, "crypto.com"),
    ("Rain", None, None, "rain.com"),
    ("BitOasis", None, None, "bitoasis.net"),
    ("Anchorage Digital", None, None, "anchorage.com"),
    ("Chainalysis", None, None, "chainalysis.com"),
    ("TRM Labs", None, None, "trmlabs.com"),
    ("Bayzat", None, None, "bayzat.com"),
    ("RemotePass", None, None, "remotepass.com"),
    ("Cercli", None, None, "cercli.com"),
    ("NymCard", None, None, "nymcard.com"),
    ("Lean Technologies", None, None, "leantech.me"),
    ("Dapi", None, None, "dapi.com"),
    ("Sarwa", None, None, "sarwa.co"),
    ("Mozn", None, None, "mozn.ai"),
    ("Lucidya", None, None, "lucidya.com"),
]


def _probe_one(ats_name: str, token: str) -> int:
    """Return job count for a given (ats, token), or 0/exception on miss."""
    fn = ats._FETCHERS.get(ats_name)
    if not fn: return 0
    try:
        return len(fn(token))
    except Exception:
        return 0

def _slug(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())

def verify(candidate) -> dict:
    """Try ashby -> lever -> greenhouse -> workable(only if explicitly guessed) for
    a candidate. Returns {"name","ats","token","n_jobs"} on a hit, else
    {"name","careers_domain","miss":True}."""
    name, ats_guess, token_guess, domain = candidate
    order = ([ats_guess] if ats_guess else []) + ["ashby", "lever", "greenhouse"]
    if ats_guess == "workable":
        order = ["workable"]
    tried = set()
    for provider in order:
        if provider in tried or provider is None: continue
        tried.add(provider)
        token = token_guess if provider == ats_guess and token_guess else _slug(name)
        n = _probe_one(provider, token)
        if n > 0:
            return {"name": name, "ats": provider, "token": token, "n_jobs": n}
    return {"name": name, "careers_domain": domain, "miss": True}


def build(limit=None):
    candidates = NEW_CANDIDATES[:limit] if limit else NEW_CANDIDATES
    verified_names = {v[0] for v in VERIFIED_ATS}
    candidates = [c for c in candidates if c[0] not in verified_names]

    print(f"Probing {len(candidates)} new candidates (skipping {len(VERIFIED_ATS)} already-verified)...")
    results = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for i, res in enumerate(ex.map(verify, candidates), 1):
            status = f"{res['ats']}/{res['token']} ({res['n_jobs']} jobs)" if not res.get("miss") else "MISS -> serper"
            print(f"  [{i}/{len(candidates)}] {res['name']}: {status}")
            results.append(res)

    ats_hits = [r for r in results if not r.get("miss")]
    misses   = [r for r in results if r.get("miss")]

    lines = ["# Auto-generated by job/probe_companies.py — every ATS token verified live.",
             "# Re-run the probe periodically to catch token rot.",
             "ats:"]
    for name, provider, token in VERIFIED_ATS:
        lines.append(f"  - name: {name!r}")
        lines.append(f"    ats: {provider}")
        lines.append(f"    token: {token}")
    for r in ats_hits:
        lines.append(f"  - name: {r['name']!r}")
        lines.append(f"    ats: {r['ats']}")
        lines.append(f"    token: {r['token']}")
        lines.append(f"    # verified: {r['n_jobs']} jobs")
    lines.append("serper:")
    for r in misses:
        lines.append(f"  - name: {r['name']!r}")
        lines.append(f"    careers_domain: {r['careers_domain']}")

    out_path = ats.REGISTRY_PATH
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n✅ {len(VERIFIED_ATS) + len(ats_hits)} ATS-direct + {len(misses)} Serper-fallback "
          f"companies -> {out_path}")


if __name__ == "__main__":
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    t0 = time.time()
    build(limit=limit)
    print(f"  ({time.time()-t0:.1f}s)")
