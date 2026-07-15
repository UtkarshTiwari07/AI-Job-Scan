# AI-Job-Scan 🤖🔎

**An open-source, autonomous job-search agent for AI/ML engineers.**

Give it your profile, plug in any LLM, and it pulls fresh AI/ML openings
**straight from companies' own hiring boards**, filters them hard against your
stack and experience, and drafts a tailored proposal for each genuine match —
for **worldwide-remote** and **India / GCC** markets.

No LinkedIn. No job-board aggregators. No thin, unverifiable listings. Every job
comes from a curated, **live-verified** company board via its official API, so
the description, location, and seniority are real and the filters actually work.

---

## Why v2 (what changed and why)

The first version searched Google (via Serper) and scraped whatever turned up —
mostly LinkedIn/Naukri **search pages**, which hand a scraper thin, login-walled
data. The result was ~95% noise: senior roles, off-stack roles, and jobs the LLM
scored 0 all showed up as "results".

v2 flips the sourcing model:

1. **Fetch** — for each company in a curated registry, call its ATS JSON API
   (**Greenhouse / Lever / Ashby**, plus **Workday** for big GCCs) and get every
   open role **with its full description** — no browser, no login walls.
2. **Filter (deterministic Python — no LLM)** — drop thin JDs, off-stack titles,
   senior/lead titles, roles requiring more years than your bracket, wrong-geo
   roles, and cap per company so one big board can't dominate.
3. **Evaluate (your LLM)** — read the **full JD**, infer required years, score
   the fit 0-100, and draft a proposal.
4. **Report** — a job reaches `report_*.json` **only if** it's a match, scores
   ≥ your threshold (default 50), is within your experience bracket, and is
   location-accessible. Everything else lands in `audit_*.json` with a reason.

That last step is enforced in Python, not left to the model — so **every job in
your report is a genuine ≥50% match within your experience range.** The trade-off
is honest: this is precision over volume — on a quiet week the report may be
short or empty (the run summary tells you that, vs. a fetch failure).

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

`SERPER_API_KEY` is only needed for the optional Tier-3 fallback (searching a
company's own careers domain); Tier-1/2 ATS fetching needs no search key.

---

## The company registry

Jobs come from `config/companies_remote.yaml` and `config/companies_india.yaml`.
Each entry names a company, its ATS, and a live-verified token:

```yaml
companies:
  - name: 'Anthropic'
    ats: greenhouse        # greenhouse | lever | ashby | workday | serper
    token: anthropic
    tier: 1
    tags: [ai-lab]
```

- **Tier 1** — Greenhouse/Lever/Ashby JSON APIs (full JD in one call). Most companies.
- **Tier 2** — Workday (GCCs); list + detail calls.
- **Tier 3** — `serper`: a `site:<company-domain>` search of the company's *own*
  careers site (never an aggregator), best-effort.

Tokens rot as companies rebrand or migrate ATS. Re-verify and regenerate the
registry any time with:

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
  LLM prompt for each mode: `title_include_terms`, `ai_relevance_keywords`,
  `title_reject_terms`, `seniority_reject_terms`, `report_min_score`,
  `per_company_cap`, `yoe_slack`, etc. Tune to narrow or widen the net.

---

## Repo layout

```
config/
  profile.example.yaml         # your profile template
  remote.yaml / india_mnc.yaml # filter taxonomy + thresholds + LLM prompt per mode
  companies_remote.yaml        # curated, live-verified company boards (~95)
  companies_india.yaml         # India startups + GCCs (Tier 1/2/3)
  freelance.yaml               # legacy (deferred) freelance mode
job/
  sources.py        # ATS adapters (greenhouse/lever/ashby/workday/serper)
  registry.py       # load the registry + fetch every board
  filters.py        # deterministic Phase-3 pre-filter + YOE parsing
  pipeline.py       # orchestrator: fetch → filter → eval → report/audit + summary
  probe_registry.py # maintenance: verify/derive ATS tokens
  jobscan_config.py # config loader
  jobscan_llm.py    # provider-agnostic LLM layer (litellm)
  init_profile.py   # build config/profile.yaml (résumé or Q&A)
  job_remote.py / job_india_mnc.py   # thin entrypoints → pipeline.run(mode)
  job_freelance.py  # legacy freelance mode (Serper/scrape; deferred rework)
```

---

## Honest limits

- **Precision, not volume.** Curated boards + hard filters mean fewer but real
  results. A short or empty report is a valid outcome; widen the registry for more.
- **Undocumented APIs.** Greenhouse/Lever/Ashby public endpoints have no SLA; a
  vendor change can break a tier. Adapters fail gracefully (a dead board is
  skipped and reported, never crashes a run).
- **India coverage is thinner** than remote — fewer Indian firms use clean ATS
  APIs, so GCCs lean on the Workday/Tier-3 fallback.
- **`match_score` is model-relative** — swapping `LLM_MODEL` can shift how many
  jobs clear the threshold.

## Disclaimer

Respect each site's Terms of Service and rate limits, and review every drafted
proposal before sending. Not affiliated with any job board, ATS, or LLM provider.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
