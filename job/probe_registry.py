"""
probe_registry.py — MAINTENANCE tool (not part of a scan run).

Verifies candidate company ATS tokens against the live Greenhouse/Lever/Ashby
APIs and writes config/companies_<remote|india>.yaml with ONLY the tokens that
actually return AI/ML-relevant jobs. Run this periodically to catch token rot
(companies rebrand / migrate ATS ~5-15%/yr).

    python job/probe_registry.py            # probe candidates, write both YAMLs
    python job/probe_registry.py --print    # print only, don't write

Tier-2 (Workday) entries are verified live too; Tier-3 (serper-domain) entries
are passed through unverified (they are only a `site:` search domain).
"""

import os
import re
import sys
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CONFIG_DIR = os.path.join(os.path.dirname(HERE), "config")

import sources  # noqa: E402

ML_TITLE_RE = re.compile(
    r"(machine learning|\bml\b|ml engineer|mlops|\bai\b|artificial intelligence|"
    r"\bllm\b|\bnlp\b|deep learning|generative|genai|gen ai|data scien|applied scien|"
    r"research (engineer|scien)|computer vision|ai/ml|ai engineer|"
    r"software engineer.*(ai|ml|platform)|prompt engineer)", re.IGNORECASE)

# ── Candidate companies: (name, ats_guess, token, tags) ──────────────
# ats_guess is tried first; if it yields 0 jobs the other two are tried.
# _REMOTE_BASE is the historical candidate pool; v5 filters out the mega/DSA-driven
# flooders (REMOVE_REMOTE_TOKENS) and appends live-verified remote-first companies
# (ADD_REMOTE) from the user's two attached remote-first lists. See the plan's
# "Redesign v5" section for the evidence behind each removal.
_REMOTE_BASE = [
    # AI labs / model & infra companies
    ("Anthropic", "greenhouse", "anthropic", ["ai-lab"]),
    ("OpenAI", "ashby", "openai", ["ai-lab"]),
    ("Hugging Face", "greenhouse", "huggingface", ["ai-lab"]),
    ("Together AI", "greenhouse", "together", ["ai-infra"]),
    ("Anyscale", "greenhouse", "anyscale", ["ai-infra"]),
    ("Weights & Biases", "greenhouse", "wandb", ["ai-infra"]),
    ("Clarifai", "greenhouse", "clarifai", ["ai"]),
    ("Modal", "greenhouse", "modal", ["ai-infra"]),
    ("Baseten", "ashby", "baseten", ["ai-infra"]),
    ("Runway", "greenhouse", "runwayml", ["ai"]),
    ("Perplexity", "ashby", "perplexity", ["ai"]),
    ("Cohere", "lever", "cohere", ["ai-lab"]),
    ("Mistral AI", "lever", "mistral", ["ai-lab"]),
    ("Scale AI", "lever", "scaleai", ["ai"]),
    ("Databricks", "greenhouse", "databricks", ["data", "ml-platform"]),
    ("Snowflake", "greenhouse", "snowflakecomputing", ["data"]),
    ("Confluent", "greenhouse", "confluent", ["data"]),
    ("dbt Labs", "greenhouse", "dbtlabs", ["data"]),
    ("Sigma Computing", "greenhouse", "sigmacomputing", ["data"]),
    # Dev tools / infra (heavy AI hiring, remote-friendly)
    ("GitLab", "greenhouse", "gitlab", ["devtools", "remote-first"]),
    ("HashiCorp", "greenhouse", "hashicorp", ["infra"]),
    ("Cloudflare", "greenhouse", "cloudflare", ["infra"]),
    ("Elastic", "greenhouse", "elastic", ["infra", "remote-first"]),
    ("MongoDB", "greenhouse", "mongodb", ["data"]),
    ("Datadog", "greenhouse", "datadog", ["infra"]),
    ("PostHog", "ashby", "posthog", ["devtools", "remote-first"]),
    ("Replit", "ashby", "replit", ["devtools"]),
    ("Vercel", "greenhouse", "vercel", ["devtools"]),
    ("Sourcegraph", "greenhouse", "sourcegraph", ["devtools", "remote-first"]),
    ("Grafana Labs", "greenhouse", "grafanalabs", ["infra", "remote-first"]),
    ("Temporal", "ashby", "temporal", ["infra"]),
    ("Render", "ashby", "render", ["infra"]),
    ("Hex", "ashby", "hex", ["data"]),
    ("Retool", "greenhouse", "retool", ["devtools"]),
    ("Linear", "ashby", "linear", ["devtools"]),
    ("Notion", "ashby", "notion", ["product"]),
    ("Figma", "greenhouse", "figma", ["product"]),
    ("Airtable", "greenhouse", "airtable", ["product"]),
    ("Webflow", "greenhouse", "webflow", ["product"]),
    ("Miro", "greenhouse", "miro", ["product"]),
    ("Grammarly", "greenhouse", "grammarly", ["ai", "product"]),
    ("Discord", "greenhouse", "discord", ["product"]),
    ("Reddit", "greenhouse", "reddit", ["product"]),
    ("Twilio", "greenhouse", "twilio", ["infra"]),
    ("CResta", "ashby", "cresta", ["ai"]),
    ("Assembly AI", "ashby", "assemblyai", ["ai"]),
    ("Deepgram", "greenhouse", "deepgram", ["ai", "voice"]),
    ("ElevenLabs", "ashby", "elevenlabs", ["ai", "voice"]),
    ("Pinecone", "greenhouse", "pinecone", ["ai-infra"]),
    ("Weaviate", "ashby", "weaviate", ["ai-infra"]),
    ("LangChain", "ashby", "langchain", ["ai-infra"]),
    ("Vellum", "ashby", "vellum", ["ai-infra"]),
    ("Glean", "greenhouse", "glean", ["ai"]),
    ("Harvey", "ashby", "harvey", ["ai"]),
    ("Sierra", "ashby", "sierra", ["ai"]),
    ("Cursor / Anysphere", "ashby", "anysphere", ["ai"]),
    ("Browserbase", "ashby", "browserbase", ["ai-infra"]),
    # Fintech / marketplaces (strong comp, ML teams)
    ("Ramp", "ashby", "ramp", ["fintech"]),
    ("Mercury", "ashby", "mercury", ["fintech"]),
    ("Brex", "greenhouse", "brex", ["fintech"]),
    ("Plaid", "greenhouse", "plaid", ["fintech"]),
    ("Affirm", "greenhouse", "affirm", ["fintech"]),
    ("Robinhood", "greenhouse", "robinhood", ["fintech"]),
    ("Coinbase", "greenhouse", "coinbase", ["fintech", "crypto"]),
    ("Chime", "greenhouse", "chime", ["fintech"]),
    ("SoFi", "greenhouse", "sofi", ["fintech"]),
    ("Deel", "ashby", "deel", ["hr", "remote-first"]),
    ("Remote.com", "greenhouse", "remotecom", ["hr", "remote-first"]),
    ("Gusto", "greenhouse", "gusto", ["fintech"]),
    ("Instacart", "greenhouse", "instacart", ["marketplace"]),
    ("DoorDash", "greenhouse", "doordash", ["marketplace"]),
    ("Faire", "greenhouse", "faire", ["marketplace"]),
    ("Flexport", "greenhouse", "flexport", ["logistics"]),
    ("Samsara", "greenhouse", "samsara", ["iot"]),
    ("Benchling", "greenhouse", "benchling", ["biotech"]),
    ("Tempus", "greenhouse", "tempus", ["health", "ai"]),
    ("Komodo Health", "greenhouse", "komodohealth", ["health"]),
    ("Cedar", "greenhouse", "cedar", ["health"]),
    ("Hims & Hers", "greenhouse", "himsandhers", ["health"]),
    ("Nuro", "greenhouse", "nuro", ["ai", "robotics"]),
    ("Applied Intuition", "lever", "applied", ["ai", "av"]),
    ("Zipline", "greenhouse", "zipline", ["robotics"]),
    ("Verkada", "greenhouse", "verkada", ["ai", "cv"]),
    ("Vannevar Labs", "lever", "vannevarlabs", ["ai", "defense"]),
    ("Voleon", "lever", "voleon", ["fintech", "ml"]),
    ("Highspot", "lever", "highspot", ["saas"]),
    ("Palantir", "lever", "palantir", ["data"]),
    ("Attentive", "greenhouse", "attentivemobile", ["ai", "martech"]),
    ("Ironclad", "greenhouse", "ironcladinc", ["ai", "legal"]),
    ("Abnormal Security", "ashby", "abnormalsecurity", ["ai", "security"]),
    ("Dropbox", "greenhouse", "dropbox", ["product", "remote-first"]),
    ("Airbnb", "greenhouse", "airbnb", ["marketplace"]),
    ("Pinterest", "greenhouse", "pinterest", ["product"]),
    ("Nebius", "greenhouse", "nebius", ["ai-infra"]),
    ("Turing", "lever", "turing", ["ai", "remote-first"]),
    ("CentML", "ashby", "centml", ["ai-infra"]),
    ("Fireworks AI", "ashby", "fireworks", ["ai-infra"]),
    ("Contextual AI", "ashby", "contextualai", ["ai-lab"]),
    ("Adept", "ashby", "adept", ["ai-lab"]),
    ("Imbue", "ashby", "imbue", ["ai-lab"]),
    ("Luma AI", "ashby", "lumaai", ["ai"]),
    ("Descript", "greenhouse", "descript", ["ai", "media"]),
    ("Synthesia", "ashby", "synthesia", ["ai", "media"]),
    ("Speak", "ashby", "speak", ["ai", "edtech"]),
    ("Codeium / Windsurf", "ashby", "codeium", ["ai", "devtools"]),
    ("Tavus", "ashby", "tavus", ["ai"]),
    ("Decagon", "ashby", "decagon", ["ai"]),
    ("Mem0 / Embedchain", "ashby", "mem0", ["ai-infra"]),
    # AI compute / infra
    ("Cerebras", "greenhouse", "cerebrassystems", ["ai-infra", "compute"]),
    ("SambaNova", "greenhouse", "sambanovasystems", ["ai-infra", "compute"]),
    ("Groq", "greenhouse", "groq", ["ai-infra", "compute"]),
    ("Lambda", "greenhouse", "lambdalabs", ["ai-infra", "compute"]),
    ("CoreWeave", "ashby", "coreweave", ["ai-infra", "compute"]),
    ("Lightning AI", "ashby", "lightningai", ["ai-infra"]),
    ("Predibase", "ashby", "predibase", ["ai-infra"]),
    ("Arize AI", "greenhouse", "arizeai", ["ai-infra", "observability"]),
    ("Unstructured", "ashby", "unstructured", ["ai-infra"]),
    ("LlamaIndex", "ashby", "llamaindex", ["ai-infra"]),
    ("Qdrant", "greenhouse", "qdrant", ["ai-infra", "vectordb"]),
    ("Zilliz", "greenhouse", "zilliz", ["ai-infra", "vectordb"]),
    ("Supabase", "greenhouse", "supabase", ["devtools", "remote-first"]),
    ("Neon", "greenhouse", "neondatabase", ["data"]),
    ("ClickHouse", "ashby", "clickhouse", ["data", "remote-first"]),
    ("Cockroach Labs", "greenhouse", "cockroachlabs", ["data"]),
    ("Fivetran", "greenhouse", "fivetran", ["data"]),
    ("Airbyte", "greenhouse", "airbyte", ["data", "remote-first"]),
    ("Hightouch", "ashby", "hightouch", ["data"]),
    ("Monte Carlo", "ashby", "montecarlo", ["data", "observability"]),
    # GenAI applications
    ("Writer", "greenhouse", "writer", ["ai", "genai"]),
    ("Jasper", "greenhouse", "jasperai", ["ai", "genai"]),
    ("Character AI", "greenhouse", "characterai", ["ai-lab"]),
    ("Cognition", "ashby", "cognition", ["ai"]),
    ("Poolside", "ashby", "poolside", ["ai-lab"]),
    ("AI21 Labs", "greenhouse", "ai21labs", ["ai-lab"]),
    ("Stability AI", "greenhouse", "stabilityai", ["ai-lab"]),
    ("Suno", "ashby", "suno", ["ai", "audio"]),
    ("Ideogram", "ashby", "ideogram", ["ai"]),
    ("Gamma", "ashby", "gamma", ["ai"]),
    ("Hippocratic AI", "greenhouse", "hippocraticai", ["ai", "health"]),
    ("Abridge", "greenhouse", "abridge", ["ai", "health"]),
    ("Ambience Healthcare", "ashby", "ambiencehealthcare", ["ai", "health"]),
    ("OpenEvidence", "ashby", "openevidence", ["ai", "health"]),
    ("Zapier", "greenhouse", "zapier", ["product", "remote-first"]),
    ("Gitpod", "greenhouse", "gitpod", ["devtools", "remote-first"]),
    ("Glean", "ashby", "glean", ["ai"]),
    ("Codeium / Windsurf", "greenhouse", "codeium", ["ai", "devtools"]),
    ("Rippling", "greenhouse", "rippling", ["product"]),
    ("Rubrik", "greenhouse", "rubrik", ["infra", "security"]),
]

# v5/v6 — mega / DSA-driven / prestige-US / giant-generic flooders. Live audit: huge
# senior-heavy, US-centric boards (OpenAI 707 jobs, Databricks 780, Airbnb, Pinterest,
# Reddit…) and, discovered live during a v5 user run, giant crypto-exchange + generic
# SaaS boards (Binance 278 jobs, OKX 310, Bybit 128, Crypto.com 115, Veeva 800, Okta
# 343, Remote.com 226, Payoneer 142, Bitpanda 51) that ATE the top-60 eval budget via
# rank()'s structured-source/pay_text bonus, leaving almost no room for smaller,
# genuinely-junior boards — the LLM correctly rejected nearly all of them, so the
# report stayed near-empty despite a "full" eval batch. v6 adds a per-company
# eval-slot cap (pipeline._cap_eval_slots) as a structural safeguard too, but these
# are removed from the registry outright per the user's explicit "remove the big
# DSA/flooder companies" instruction — named here, not guessed.
REMOVE_REMOTE_TOKENS = {
    "anthropic", "openai", "databricks", "snowflakecomputing", "confluent",
    "reddit", "pinterest", "airbnb", "discord", "dropbox",
    "coinbase", "robinhood", "affirm", "brex", "plaid", "chime", "sofi", "gusto",
    "instacart", "doordash", "faire", "flexport", "samsara",
    "datadog", "figma", "rippling", "rubrik", "retool", "airtable", "webflow", "miro", "grammarly",
    "palantir", "vannevarlabs", "nuro", "zipline", "applied", "verkada",
    "cerebrassystems", "sambanovasystems", "groq", "lambdalabs", "coreweave", "nebius",
    "attentivemobile", "ironcladinc",
    # v6 removals — user-named + giant generic boards, see comment above
    "scaleai", "remotecom",
}

# v5/v6 — genuinely remote-first companies from the user's two attached lists, each
# token VERIFIED live this session (job counts noted). Crypto-exchange GIANTS
# (Binance/OKX/Bybit/Crypto.com) and giant generic boards (Veeva/Okta/Payoneer/
# Bitpanda) were removed here in v6 — see REMOVE_REMOTE_TOKENS comment; small
# crypto/security names with real, modest-sized AI/ML-relevant boards stay.
ADD_REMOTE = [
    # crypto / Web3 (small, real AI/security roles — not giant boards)
    ("Fireblocks", "greenhouse", "fireblocks", ["crypto"]),                 # 61 / 3
    ("BitGo", "greenhouse", "bitgo", ["crypto"]),                           # 37 / 1
    ("Anchorage Digital", "lever", "anchorage", ["crypto"]),                # 44 jobs
    # remote-first infra / devtools / SaaS (confirmed India-friendly in the doc)
    ("Canonical", "greenhouse", "canonical", ["remote-first", "india"]),    # 301 / 5
    ("Mozilla", "greenhouse", "mozilla", ["remote-first"]),                 # 77 / 11
    ("JFrog", "greenhouse", "jfrog", ["devtools", "india"]),                # 42 / 2
    ("Hex", "greenhouse", "hextechnologies", ["data"]),                     # 26 / 3
    ("Buildkite", "greenhouse", "buildkite", ["devtools", "remote-first"]), # 12 / 1
    ("Sourcegraph", "greenhouse", "sourcegraph91", ["devtools", "remote-first"]),
    ("Coursera", "greenhouse", "coursera", ["edtech", "india"]),            # 14 / 3
    # AI / data / talent (remote-first, India-relevant)
    ("Sardine", "ashby", "sardine", ["ai", "fintech"]),                     # 35 / 8
    ("Forter", "greenhouse", "forter", ["ai", "fintech"]),                  # 41 / 6
    ("Prolific", "greenhouse", "prolific", ["ai", "remote-first"]),         # 29 / 4
    ("Apollo.io", "greenhouse", "apolloio", ["ai", "remote-first", "india"]),  # 43 / 2
    ("Lightricks", "greenhouse", "lightricks", ["ai"]),                     # 6 / 2
    ("Hugging Face", "workable", "huggingface", ["ai-lab", "remote-first"]),  # workable
    ("Toptal", "lever", "toptal", ["talent", "remote-first", "india"]),     # 19 / 3
    ("Andela", "ashby", "andela", ["talent", "remote-first", "india"]),     # 14 / 3
    ("Superside", "lever", "superside", ["remote-first", "india"]),         # 18 / 7
    # remote-first fintech / hr
    ("Alpaca", "greenhouse", "alpaca", ["fintech", "remote-first"]),        # 53 / 2
    ("Oyster HR", "ashby", "oyster", ["hr", "remote-first"]),               # 10 / 1
    ("RemoFirst", "lever", "remofirst", ["hr", "remote-first"]),            # 17 / 1
]

REMOTE_CANDIDATES = [c for c in _REMOTE_BASE if c[2] not in REMOVE_REMOTE_TOKENS] + ADD_REMOTE

INDIA_CANDIDATES = [
    ("Sarvam AI", "ashby", "sarvam", ["india", "ai-lab", "voice"]),
    ("Sarvam AI (alt)", "lever", "sarvamai", ["india", "ai-lab"]),
    ("Krutrim", "lever", "krutrim", ["india", "ai-lab"]),
    ("Observe.AI", "greenhouse", "observeai", ["india", "voice", "ai"]),
    ("Yellow.ai", "lever", "yellowai", ["india", "ai"]),
    ("Gupshup", "lever", "gupshup", ["india", "ai"]),
    ("Uniphore", "greenhouse", "uniphore", ["india", "voice", "ai"]),
    ("Sprinklr", "greenhouse", "sprinklr", ["india", "ai"]),
    ("Razorpay", "lever", "razorpay", ["india", "fintech"]),
    ("CRED", "lever", "cred", ["india", "fintech"]),
    ("Groww", "lever", "groww", ["india", "fintech"]),
    ("Meesho", "lever", "meesho", ["india", "ecommerce"]),
    ("Postman", "greenhouse", "postman", ["india", "devtools"]),
    ("Hasura", "greenhouse", "hasura", ["india", "devtools"]),
    ("BrowserStack", "lever", "browserstack", ["india", "devtools"]),
    ("Chargebee", "lever", "chargebee", ["india", "saas"]),
    ("Freshworks", "smartrecruiters", "freshworks", ["india", "saas"]),
    ("InMobi", "lever", "inmobi", ["india", "adtech"]),
    ("Dream11", "lever", "dreamsports", ["india", "gaming"]),
    ("Zepto", "lever", "zepto", ["india", "quickcommerce"]),
    ("PhonePe", "lever", "phonepe", ["india", "fintech"]),
    ("Rippling India", "greenhouse", "rippling", ["india", "hr"]),
    ("Fractal Analytics", "greenhouse", "fractalanalytics", ["india", "ai"]),
    ("Mad Street Den", "lever", "madstreetden", ["india", "ai", "cv"]),
    ("Wysa", "lever", "wysa", ["india", "ai", "health"]),
    ("SigTuple", "lever", "sigtuple", ["india", "ai", "health"]),
    ("Niramai", "lever", "niramai", ["india", "ai", "health"]),
    # More Indian product/AI companies (probe finds the ATS)
    ("Chargebee", "ashby", "chargebee", ["india", "saas"]),
    ("Juspay", "greenhouse", "juspay", ["india", "fintech"]),
    ("Zeta", "greenhouse", "zeta", ["india", "fintech"]),
    ("Cashfree", "greenhouse", "cashfree", ["india", "fintech"]),
    ("Darwinbox", "greenhouse", "darwinbox", ["india", "hr"]),
    ("Whatfix", "greenhouse", "whatfix", ["india", "saas"]),
    ("MoEngage", "greenhouse", "moengage", ["india", "martech"]),
    ("CleverTap", "greenhouse", "clevertap", ["india", "martech"]),
    ("Innovaccer", "greenhouse", "innovaccer", ["india", "health", "ai"]),
    ("HackerRank", "greenhouse", "hackerrank", ["india", "devtools"]),
    ("Atlan", "ashby", "atlan", ["india", "data"]),
    ("Haptik", "lever", "haptik", ["india", "ai", "voice"]),
    ("Yellow.ai", "greenhouse", "yellowmessenger", ["india", "ai"]),
    ("Skit.ai", "lever", "skit", ["india", "ai", "voice"]),
    ("Quantiphi", "greenhouse", "quantiphi", ["india", "ai", "services"]),
    ("Sigmoid", "lever", "sigmoid", ["india", "data", "ai"]),
    ("LatentView", "greenhouse", "latentview", ["india", "analytics"]),
    ("Druva", "greenhouse", "druva", ["india", "infra"]),
    ("Netradyne", "greenhouse", "netradyne", ["india", "ai", "cv"]),
    ("Ola Krutrim", "ashby", "krutrim", ["india", "ai-lab"]),
    # v5 — more Indian product / AI companies (probe verifies the live ATS token)
    ("Eightfold AI", "greenhouse", "eightfold", ["india", "ai", "hr"]),
    ("Locus", "lever", "locus", ["india", "ai", "logistics"]),
    ("Kore.ai", "lever", "koreai", ["india", "ai", "voice"]),
    ("Vymo", "lever", "vymo", ["india", "ai", "saas"]),
    ("Entropik", "lever", "entropik", ["india", "ai"]),
    ("Ushur", "greenhouse", "ushur", ["india", "ai"]),
    ("SquadStack", "lever", "squadstack", ["india", "ai"]),
    ("SpotDraft", "lever", "spotdraft", ["india", "ai", "legal"]),
    ("Zluri", "lever", "zluri", ["india", "saas"]),
    ("Setu", "lever", "setu", ["india", "fintech"]),
    ("M2P Fintech", "lever", "m2p", ["india", "fintech"]),
    ("Tiger Analytics", "greenhouse", "tigeranalytics", ["india", "ai", "analytics"]),
    ("Fractal", "greenhouse", "fractal", ["india", "ai", "analytics"]),
    ("Swiggy", "greenhouse", "swiggy", ["india", "food", "ml"]),
    ("Turing", "greenhouse", "turing", ["india", "ai", "remote-first"]),
    ("Rubrik India", "greenhouse", "rubrik", ["india", "infra"]),
    ("Simpplr", "greenhouse", "simpplr", ["india", "saas"]),
    ("Gong", "greenhouse", "gong", ["india", "ai"]),
    ("Sprinklr", "lever", "sprinklr", ["india", "ai"]),
]

# v5 — FAANG/GCC entries REMOVED. The 21 Tier-3 `ats: serper` GCCs (Google,
# Microsoft, Amazon, Adobe, Walmart, Uber, PayPal, Visa, Mastercard, Goldman,
# JPMorgan, …) and the NVIDIA Workday GCC were the entire reason crawl4ai ran in
# india_mnc (serper → fetch_serper_domain → _crawl_jds), and were DSA-driven,
# senior-heavy boards that flooded the funnel with volume but converted ~0 roles.
# Removed on the user's instruction. India volume now comes from Indian product
# companies (India-located roles) + the shared remote-first set (worldwide-remote
# roles, since india_mnc now accepts those — see filters._geo_ok_india).
WORKDAY_GCC = []
SERPER_REMOTE = []
SERPER_INDIA = []


def _probe(ats, token):
    if ats == "workable":  # list-only (fast) — titles are enough to gauge AI/ML volume
        data = sources._post_json(
            f"https://apply.workable.com/api/v3/accounts/{token}/jobs",
            {"query": "", "location": [], "department": [], "worktype": [], "remote": []})
        jobs = (data or {}).get("results", []) if data else []
        if not jobs:
            return None
        ml = sum(1 for j in jobs if ML_TITLE_RE.search(j.get("title", "")))
        return {"jobs": len(jobs), "ml": ml}
    fn = {"greenhouse": lambda t: sources.fetch_greenhouse(t, content=False),
          "lever": sources.fetch_lever,
          "ashby": sources.fetch_ashby}.get(ats)
    if not fn:
        return None
    try:
        jobs = fn(token)
    except Exception:
        return None
    if not jobs:
        return None
    ml = sum(1 for j in jobs if ML_TITLE_RE.search(j["title"]))
    return {"jobs": len(jobs), "ml": ml}


def verify(candidate):
    name, ats_guess, token, tags = candidate
    # Probe the guessed ATS first, then the standard three. Workable is only probed
    # when it's the explicit guess (it needs a per-account POST, not a slug on a shared host).
    order = [ats_guess] + [a for a in ("ashby", "lever", "greenhouse") if a != ats_guess]
    for ats in order:
        res = _probe(ats, token)
        if res and res["ml"] >= 1:
            return {"name": name, "ats": ats, "token": token, "tier": 1,
                    "tags": tags, "_jobs": res["jobs"], "_ml": res["ml"]}
    return None


def verify_workday(entry):
    name, tenant, dc, site, tags = entry
    try:
        jobs = sources.fetch_workday(tenant, dc, site, search_text="machine learning", max_detail=1)
    except Exception:
        jobs = []
    if jobs:
        return {"name": name, "ats": "workday", "tenant": tenant, "dc": dc,
                "site": site, "tier": 2, "tags": tags}
    return None


def _yaml_block(entries, serper_entries):
    lines = ["# Auto-generated by job/probe_registry.py — every ATS token verified live.",
             "# Re-run the probe periodically to catch token rot.", "companies:"]
    for e in sorted(entries, key=lambda x: (-x.get("_ml", 0), x["name"].lower())):
        lines.append(f"  - name: {e['name']!r}")
        lines.append(f"    ats: {e['ats']}")
        if e["ats"] == "workday":
            lines.append(f"    tenant: {e['tenant']}")
            lines.append(f"    dc: {e['dc']}")
            lines.append(f"    site: {e['site']}")
        else:
            lines.append(f"    token: {e['token']}")
        lines.append(f"    tier: {e['tier']}")
        lines.append(f"    tags: [{', '.join(e['tags'])}]")
        if "_jobs" in e:
            lines.append(f"    # verified: {e['_jobs']} jobs, {e['_ml']} AI/ML titles")
    for name, domain, tags in serper_entries:
        lines.append(f"  - name: {name!r}")
        lines.append(f"    ats: serper")
        lines.append(f"    domain: {domain}")
        lines.append(f"    tier: 3")
        lines.append(f"    tags: [{', '.join(tags)}]")
    return "\n".join(lines) + "\n"


def build(candidates, workday, serper, out_path, do_write, extra_verified=None):
    verified = []
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(verify, candidates):
            if res:
                verified.append(res)
    if extra_verified:  # e.g. the shared remote-first set, reused in the india registry
        verified.extend(extra_verified)
    # de-dup by (ats, token)
    seen, uniq = set(), []
    for e in verified:
        key = (e["ats"], e.get("token"))
        if key not in seen:
            seen.add(key); uniq.append(e)
    for wd in workday:
        r = verify_workday(wd)
        if r:
            uniq.append(r)
    block = _yaml_block(uniq, serper)
    tier1 = sum(1 for e in uniq if e["tier"] == 1)
    tier2 = sum(1 for e in uniq if e["tier"] == 2)
    print(f"\n{out_path}: {tier1} Tier-1 (clean API) + {tier2} Tier-2 (workday) + "
          f"{len(serper)} Tier-3 (serper) = {tier1+tier2+len(serper)} companies")
    for e in sorted(uniq, key=lambda x: -x.get("_ml", 0)):
        if e["tier"] == 1:
            print(f"    ✓ {e['name']:28} {e['ats']:10} {e['token']:22} "
                  f"{e['_jobs']:>4} jobs / {e['_ml']:>3} ML")
    if do_write:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(block)
        print(f"  → wrote {out_path}")
    return uniq


if __name__ == "__main__":
    do_write = "--print" not in sys.argv
    print("Probing REMOTE candidates ...")
    remote_verified = build(REMOTE_CANDIDATES, [], SERPER_REMOTE,
                            os.path.join(CONFIG_DIR, "companies_remote.yaml"), do_write)
    # india_mnc now also accepts worldwide-remote roles, so the verified remote-first
    # companies feed the India registry too (alongside Indian product companies).
    print("\nProbing INDIA candidates (+ shared remote-first set) ...")
    build(INDIA_CANDIDATES, WORKDAY_GCC, SERPER_INDIA,
          os.path.join(CONFIG_DIR, "companies_india.yaml"), do_write,
          extra_verified=remote_verified)
