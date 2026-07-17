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

## How it works (v7)

Earlier versions taught four hard lessons, all still visible in the design:

- **v1** searched broadly (Google + LinkedIn/aggregator listing pages) and found
  the right *kinds* of jobs, but scraped thin, login-walled cards — the
  extraction step then **fabricated** filler text for what it couldn't read.
  ~95% noise.
- **v2–v5** fixed the fabrication by fetching only real ATS APIs (full JD, no
  scraping) — but stayed **company-first**: pick companies, download their whole
  boards, hope the right jobs are inside. A live run showed that fails even with
  a curated, non-prestige registry: 12,000+ jobs fetched, 0.6% were ever
  candidates, because a company's board is dominated by sales/support/senior
  roles no filter can turn into AI/ML matches.
- **v6 inverted it: hunt jobs, not companies.** Query-first, site-restricted
  searches for your actual target roles became the primary net, and every
  remaining company-first source became **selection-gated** — it contributes its
  AI/ML-relevant jobs or nothing, never a whole board. That fixed the *ratio*
  (candidate density rose from ~1% to 20–40%), but the ATS registry + guest
  scrapes it searched still don't reach where most of the real market is: Indeed,
  Naukri (India's dominant board), and companies with no public ATS API at all —
  the *volume* stayed too small.
- **v7 plugs into the real job market and fixes a self-sabotaging dedup bug.**
  Real board search (Indeed/Naukri/LinkedIn/Google, via the open-source
  [JobSpy](https://github.com/Bunsly/JobSpy) scraper) is now the primary volume
  source — it's what most of "the market" actually runs on, ATS APIs included.
  And a real bug is fixed: earlier versions marked every *candidate* as
  cross-run-"seen," not just reported ones — re-running the tool while tuning it
  silently drained the whole pool to near-nothing. Now only a job that's actually
  **reported** is remembered, so repeated runs no longer sabotage themselves.

1. **Discover** — most job-targeted sources first:
   - **Real job boards** (primary volume): Indeed, Naukri, LinkedIn, Google Jobs
     and others via JobSpy — one uniform call returns full descriptions and,
     wherever the board resolved it, the company's own careers URL
     (`job_url_direct`) instead of the board's posting page.
   - **Job-level search**: Serper queries built from your `profile.target_roles`,
     restricted to `jobs.lever.co` / `jobs.ashbyhq.com` / `boards.greenhouse.io` /
     `apply.workable.com`. A per-job hit is fetched as ONE job via that ATS's own
     per-job endpoint — never its whole board.
   - **Watchlist registry** (Greenhouse/Lever/Ashby/Workday/Workable) — every
     board is fetched, then only its AI/ML-relevant, non-senior jobs survive
     (capped per board) before joining the pool.
   - **Company-targeted search** (india_mnc): companies with no clean ATS API —
     most of them, live-verified — get a rotating batch of direct
     `"<role> <company>"` searches each run (e.g. `ai engineer Housing.com`).
   - **LinkedIn's public job-search results** (title/company/location only — no
     login) resolves each company to its **own** ATS board and keeps only that
     board's matching jobs — never a reported Easy-Apply link.
   - **RemoteOK + HN's "Who is hiring"** — already job-targeted feeds with full
     JDs included.
   - Anything discovered by URL that matches no known ATS pattern is enriched via
     **crawl4ai** on its own detail page (never a search/listing page — that was
     v1's fabrication source) before it's ever evaluated.
2. **Filter (deterministic Python — no LLM)** — a guardrail now, not the primary
   selector: drop thin JDs, off-stack titles, senior/lead titles, roles requiring
   more years than your bracket, already-reported duplicates, and cap per company.
3. **Rank** — score every surviving candidate on junior-language density, title
   match, source reliability, recency, and (india_mnc) proximity to your own city;
   the top N (default 60) go to the LLM, with a **hard cap on eval slots per
   company** so one big board can never dominate the budget.
4. **Evaluate (your LLM)** — read the **full raw JD** (not a truncated snippet)
   plus the structured location field, and **verify** the things a token list
   can't: is this role actually accessible from *your* country (a remote job
   scoped to another country — e.g. "Remote, United States" or one requiring
   foreign work authorization — is *not*), does the stated experience fit, is it
   a genuine stack match. Then score the fit 0-100 and draft a proposal.
5. **Report** — a job reaches `report_*.json` **only if** it's a match, scores
   ≥ your threshold, is within your experience bracket, and is location-
   accessible. Only THEN is it marked seen so it's never re-reported. Everything
   else lands in `audit_*.json` with a reason, and a per-source funnel in the run
   summary tells you *where* the pipeline thinned — so an empty report reads as
   an honest "no match this week," not a mystery.

That report/audit split is enforced in Python, not left to the model — so
**every job in your report is a genuine match within your experience range.**
The trade-off is honest: this is precision over volume, and coverage still
depends on the market and your `target_roles`/companies — see Honest limits below.

---

## Quickstart

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # set LLM_MODEL + one API key + SERPER_API_KEY (optional but recommended)
python job/init_profile.py    # build config/profile.yaml (from a résumé or Q&A)

python job/job_remote.py --dry-run   # mock data, no network/LLM — sanity check
python job/job_remote.py             # live: worldwide-remote AI/ML roles
python job/job_india_mnc.py          # live: India / GCC / startup AI/ML roles
python job/job_remote.py --fresh     # ignore the seen-cache for this run (useful while tuning)
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

`SERPER_API_KEY` powers the primary job-level discovery search (built at runtime
from your `profile.target_roles`; extend it with `serper_job_queries` per mode)
and the Tier-3 company-domain fallback; the registry/RemoteOK/HN/LinkedIn sources
don't need it, though discovery is far weaker without it.

---

## Sources — real job boards, the registry, and beyond

**Primary volume: real job boards, via [JobSpy](https://github.com/Bunsly/JobSpy).**
`pip install python-jobspy` unlocks one call that fans out to Indeed, Naukri,
LinkedIn, Google Jobs, Glassdoor, ZipRecruiter, Bayt and BDJobs, returning full
descriptions and — wherever a board resolved it — the company's own careers URL
(`job_url_direct`) instead of the board's own posting page. Configure searches in
each mode's `jobspy_searches`. Without this dependency the run prints a loud
warning; discovery still works, just from a much smaller net (the ATS registry +
guest-scrape sources below).

**Delhi NCR is a special case.** A live study of 40 NCR tech companies (Housing.com/
REA India, MakeMyTrip, PolicyBazaar, Lenskart, Delhivery, Urban Company, Cars24,
OYO, Blinkit, BharatPe, and more) found only **3 of 40** expose a clean
Greenhouse/Lever/Ashby API (Paytm, Zomato/Eternal, Info Edge — see the registry).
The other 37, listed in `india_mnc.yaml`'s `ncr_target_companies`, are structurally
invisible to any ATS-only design — they're reached instead through rotating,
company-targeted JobSpy searches (`"<role> <company>"`), a bounded batch per run so
one run doesn't issue 30+ extra searches.

*(Considered and not used: [Scrapling](https://github.com/D4Vinci/Scrapling), an
adaptive anti-bot fetcher — it's a crawl4ai alternative, not a job-board scraper, so
it wouldn't have added a new source. Documented here in case that changes.)*

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

The registry is now a **secondary, selection-gated** source: every board is
fetched, but only its AI/ML-relevant, non-senior jobs survive (capped per board
by `watchlist_cap_per_board`) before joining the pool — a giant board contributes
a handful of matching roles or nothing, never its whole size. On top of it, each
mode's YAML (`sources:` block) turns on additional discovery: **LinkedIn** (public
job-search results — company names only, resolved to the company's own ATS board
via `job/company_resolve.py`, then selection-gated the same way; never a reported
URL), and **RemoteOK** / **HN "Who is hiring"** (both give full descriptions
directly, already job-targeted). Tune `linkedin_queries` / `serper_job_queries`
per mode to widen or narrow what gets discovered.

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
  optional `city`/`city_aliases` (e.g. "Delhi NCR" — ranks a nearby onsite/hybrid
  role above a distant one, india_mnc only), `experience_years`, `target_roles`
  (drives BOTH the job-level Serper search and the JobSpy search terms). Feeds the
  proposals and the experience-bracket filter. Create it with
  `python job/init_profile.py` or copy `config/profile.example.yaml`. Gitignored —
  your details stay yours.
- **`config/{remote,india_mnc}.yaml`** — the AI/ML filter taxonomy + thresholds +
  discovery sources + LLM prompt for each mode: `title_include_terms`,
  `ai_relevance_keywords`, `title_reject_terms`, `seniority_reject_terms`,
  `report_min_score`, `per_company_cap`, `yoe_slack`, `eval_max_candidates`,
  `eval_slots_per_company`, `watchlist_cap_per_board`, `sources`,
  `linkedin_queries`, `serper_job_queries`, `jobspy_searches`,
  `ncr_target_companies` (india_mnc). Tune to narrow or widen the net.

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
  jobspy_source.py     # PRIMARY volume: Indeed/Naukri/LinkedIn/Google via python-jobspy
  sources.py           # ATS adapters (+ per-job endpoints) + RemoteOK + HN + URL parsing
  discovery.py         # job-level Serper search + LinkedIn guest search
  company_resolve.py   # resolve a bare company name to its live ATS board (+cache)
  registry.py          # load the static registry + fetch/select/tag every board
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
- **JobSpy scrapes real boards — expect occasional rate-limiting or IP-based
  blocks**, especially Naukri (its API can demand a captcha from a datacenter IP;
  a residential IP is far less likely to hit this) and Google Jobs (indexing can
  be sparse for a given query). Each board fails independently to a loud per-source
  zero in the funnel — it never silently shrinks the net or fabricates a result.
- **A JobSpy row's reported link is the board's own posting page when the board
  didn't resolve a `job_url_direct`** — most common on LinkedIn, whose apply
  mechanism needs a login session. That's still a real job with a real, fully
  fetched description (not the old thin-card fabrication problem) — just not
  always the company's own page. The domain in the URL always tells you which.
- **Cross-run dedup now only remembers REPORTED jobs** (fixed in v7 — earlier
  versions marked every *candidate* as seen, which meant re-running the tool while
  tuning it silently drained the whole pool). Use `--fresh` to ignore the cache
  entirely for one run.

## Disclaimer

Respect each site's Terms of Service and rate limits, and review every drafted
proposal before sending. Not affiliated with any job board, ATS, or LLM provider.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
