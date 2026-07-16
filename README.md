# AI-Job-Scan 🤖🔎

**An open-source, autonomous job-search agent for AI/ML engineers.**

Give it your profile, plug in any LLM, and it discovers fresh AI/ML openings from
company hiring boards and job feeds, verifies every one against its **full job
description**, filters them hard against your stack and experience, and drafts a
tailored proposal for each genuine match — for **worldwide-remote** and
**India / GCC** markets.

No aggregator listings, no thin cards, no fabricated summaries. Every job in your
report was fetched from a real source with its full description in hand — an
ATS's own JSON API, RemoteOK, HN's "Who is hiring", or a company board discovered
through search — so the description, location, and seniority are real and the
filters actually work. LinkedIn is used for **discovery only** (see below): it
never provides the link you'd apply through.

---

## How it works (v3)

Earlier versions taught two hard lessons, both still visible in the design:

- **v1** searched broadly (Google + LinkedIn/aggregator listing pages) and found
  the right *kinds* of jobs, but scraped thin, login-walled cards — the
  extraction step then **fabricated** filler text for what it couldn't read.
  ~95% noise.
- **v2** fixed the fabrication by fetching only from a curated list of elite
  companies' own ATS APIs (full JD, no scraping) — but that pool skewed senior
  and US-centric, so a junior/India-based profile matched **zero** jobs. Correct
  filtering on the wrong pool.

**v3 combines both lessons**: discover broadly, but never evaluate or report
anything without its real, full description in hand.

1. **Discover** — merge several sources: a curated company registry (Greenhouse /
   Lever / Ashby / Workday JSON APIs), RemoteOK, HN's monthly "Who is hiring"
   thread, and **LinkedIn's public job-search results** (title/company/location
   only — no login, and its detail pages carry no apply-method info without one,
   verified live). Every company LinkedIn surfaces is resolved to its **own** ATS
   board or dropped — so a LinkedIn-discovered job is never itself the reported
   link, and Easy-Apply-flooded listings never enter the pool at all.
2. **Filter (deterministic Python — no LLM)** — drop thin JDs, off-stack titles,
   senior/lead titles, roles requiring more years than your bracket, and cap per
   company so one big board can't dominate. A remote job is treated as
   accessible unless something *explicit* restricts it — silence about
   geography no longer causes a false rejection.
3. **Rank** — score every surviving candidate on junior-language density, title
   match, source reliability, and recency; only the top N (default 60) go to
   the LLM, so a big discovery run doesn't burn unlimited API budget.
4. **Evaluate (your LLM)** — read the **full raw JD** (not a truncated snippet;
   fetched with crawl4ai when a source only gave a link) plus the structured
   location field, and **verify** the things a token list can't: is this role
   actually accessible from *your* country (a remote job scoped to another country
   — e.g. "Remote, United States" or one requiring foreign work authorization — is
   *not*), does the stated experience fit, is it a genuine stack match. Then score
   the fit 0-100 and draft a proposal. Location, remote-eligibility and experience
   are judged by the model from the real text — never inferred from hard-coded
   keyword lists.
5. **Report** — a job reaches `report_*.json` **only if** it's a match, scores
   ≥ your threshold, is within your experience bracket, and is location-
   accessible. Everything else lands in `audit_*.json` with a reason, and a
   per-source funnel in the run summary tells you *where* the pipeline thinned
   — so an empty report reads as an honest "no match this week," not a mystery.

That report/audit split is enforced in Python, not left to the model — so
**every job in your report is a genuine match within your experience range.**
The trade-off is honest: this is precision over volume, and coverage still
depends on which companies/feeds you configure — see Honest limits below.

---

## Quickstart

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # set LLM_MODEL + one API key + SERPER_API_KEY (optional, Tier-3 only)
python job/init_profile.py    # build config/profile.yaml (from a résumé or Q&A)

python job/job_remote.py --dry-run   # mock data, no network/LLM — sanity check
python job/job_remote.py             # live: worldwide-remote AI/ML roles
python job/job_india_mnc.py          # live: India / GCC / startup AI/ML roles
```

Each live run writes three files under `job/reports_<mode>/`:
`report_*.json` (your matches + drafted proposals), `audit_*.json` (everything
filtered out, with reasons), and `raw_*.ndjson` (all fetched jobs).

---

## Bring your own LLM

One env var picks the model for evaluation, in [litellm](https://docs.litellm.ai/docs/providers)
`provider/model` form:

| Provider  | `LLM_MODEL` example                  | Key env var         |
|-----------|--------------------------------------|---------------------|
| DeepSeek  | `deepseek/deepseek-v4-pro`           | `DEEPSEEK_API_KEY`  |
| Google    | `gemini/gemini-2.0-flash`            | `GEMINI_API_KEY`    |
| OpenAI    | `openai/gpt-4o-mini`                 | `OPENAI_API_KEY`    |
| Anthropic | `anthropic/claude-sonnet-4-...`      | `ANTHROPIC_API_KEY` |
| Groq      | `groq/llama-3.3-70b-versatile`       | `GROQ_API_KEY`      |

`SERPER_API_KEY` powers the broad-discovery queries (`serper_discovery_queries`)
and the Tier-3 company-domain fallback; the registry/RemoteOK/HN/LinkedIn sources
don't need it.

---

## Sources — the registry and beyond

The static base is `config/companies_remote.yaml` / `config/companies_india.yaml`
— every entry names a company, its ATS, and a live-verified token:

```yaml
companies:
  - name: 'Anthropic'
    ats: greenhouse        # greenhouse | lever | ashby | workday | serper
    token: anthropic
    tier: 1
    tags: [ai-lab]
```

On top of that, each mode's YAML (`sources:` block) turns on additional discovery:
**LinkedIn** (public job-search results — company names only, resolved to the
company's own ATS board via `job/company_resolve.py`; never a reported URL),
**RemoteOK** and **HN "Who is hiring"** (both give full descriptions directly),
and broad **Serper** queries that extract a company token straight from a
Greenhouse/Lever/Ashby URL in the results. Tune `linkedin_queries` /
`serper_discovery_queries` per mode to widen or narrow what gets discovered.

For a LinkedIn-surfaced company whose ATS token can't be guessed from its name,
`company_resolve.py` falls back to a targeted Serper search for its **own** board
(`SERPER_API_KEY` required) — so far more discovered companies resolve to a real
careers page, and the reported link is still never `linkedin.com`. Any source that
yields only a link (no full JD) is passed through crawl4ai to fetch the real page
text before it's ever evaluated — nothing is judged on a thin or fabricated
snippet (`enrich_max_crawls` bounds the crawls per run).

Static-registry tokens rot as companies rebrand or migrate ATS. Re-verify and
regenerate the registry any time with:

```bash
python job/probe_registry.py        # probes candidates, keeps only live tokens with AI/ML roles
```

Add companies by editing the `*_CANDIDATES` lists in `job/probe_registry.py` and
re-running it, or by hand-adding a verified entry to the YAML.

---

## Configure it for you

- **`config/profile.yaml`** — you: name, stack, quantified metrics, location,
  `experience_years`, target roles. Feeds the proposals and the experience-bracket
  filter. Create it with `python job/init_profile.py` or copy
  `config/profile.example.yaml`. Gitignored — your details stay yours.
- **`config/{remote,india_mnc}.yaml`** — the AI/ML filter taxonomy + thresholds +
  discovery sources + LLM prompt for each mode: `title_include_terms`,
  `ai_relevance_keywords`, `title_reject_terms`, `seniority_reject_terms`,
  `report_min_score`, `per_company_cap`, `yoe_slack`, `eval_max_candidates`,
  `sources`, `linkedin_queries`, `serper_discovery_queries`. Tune to narrow or
  widen the net.

---

## Repo layout

```
config/
  profile.example.yaml         # your profile template
  remote.yaml / india_mnc.yaml # filter taxonomy + thresholds + sources + LLM prompt
  companies_remote.yaml        # curated, live-verified company boards (~95)
  companies_india.yaml         # India startups + GCCs (Tier 1/2/3)
  freelance.yaml               # legacy (deferred) freelance mode
job/
  sources.py           # ATS adapters + RemoteOK + HN Who's-Hiring + ATS-URL parsing
  discovery.py         # LinkedIn guest search + broad Serper discovery
  company_resolve.py   # resolve a bare company name to its live ATS board (+cache)
  registry.py          # load the static registry + fetch/tag every board
  filters.py           # deterministic pre-filter + YOE parsing + rank()
  pipeline.py          # orchestrator: discover → filter → rank → eval → report/audit + funnel
  probe_registry.py    # maintenance: verify/derive static-registry ATS tokens
  jobscan_config.py    # config loader
  jobscan_llm.py       # provider-agnostic LLM layer (litellm)
  init_profile.py      # build config/profile.yaml (résumé or Q&A)
  job_remote.py / job_india_mnc.py   # thin entrypoints → pipeline.run(mode)
  job_freelance.py     # legacy freelance mode (Serper/scrape; deferred rework)
```

---

## Honest limits

- **Precision, not volume.** Hard filters + a full-JD requirement mean fewer but
  real results. A short or empty report is a valid outcome — the run summary's
  per-source funnel tells you whether that's "no match this week" or a source
  that broke; widen `sources`/`linkedin_queries`/the registry for more volume.
- **LinkedIn's guest access is unofficial.** It can rate-limit or change markup
  without notice; a break there just drops that source's contribution to the
  funnel, it never produces bad data (nothing from LinkedIn is ever reported
  directly — see Sources above).
- **Undocumented APIs generally.** Greenhouse/Lever/Ashby/RemoteOK/HN have no
  SLA; a vendor change can break a source. Adapters fail gracefully (skipped
  and counted in the funnel, never crashes a run).
- **India coverage is thinner** than remote — fewer Indian firms use clean ATS
  APIs, so GCCs lean on the Workday/Tier-3 fallback and India-specific LinkedIn
  discovery matters more there.
- **Company-name resolution is a best-effort guess** (`company_resolve.py` tries
  a few slug variants) — small companies without a Greenhouse/Lever/Ashby board
  simply won't resolve, which is correct behavior, not a bug.
- **`match_score` is model-relative** — swapping `LLM_MODEL` can shift how many
  jobs clear the threshold. Location/experience verification is model-driven too:
  a remote role scoped to a country other than yours is treated as inaccessible by
  default, which is deliberately strict (it stops US-only-remote roles leaking into
  an India-based report) — a genuinely global role that tags one country and never
  says "worldwide" can occasionally be dropped. Every such drop is recorded in
  `audit_*.json` with its reason, so the strictness is visible and tunable.

## Disclaimer

Respect each site's Terms of Service and rate limits, and review every drafted
proposal before sending. Not affiliated with any job board, ATS, or LLM provider.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
