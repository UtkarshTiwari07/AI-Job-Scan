# AI-Job-Scan 🤖🔎

**An open-source, autonomous job-search agent for AI/ML engineers.**

Point it at your profile, pick your favourite LLM, and it will scour the web for
fresh, relevant openings, filter out the noise, score each match, and draft a
tailored proposal / cover letter — for **worldwide-remote**, **freelance**, and
**India MNC / startup** markets.

It's tuned for the AI/ML niche on purpose: the query clusters, target sites
(HuggingFace, Cohere, Mistral, Together, Modal, Replicate, Wellfound, Naukri,
Upwork, Toptal…) and the "reject anything that isn't real AI/LLM work" filters
are what make the matches good. You bring **your** stack, experience, rate and
geo; the agent does the rest.

> Inspired by open job-hunt tooling like CareerOps — but sharp on AI/ML.

---

## How it works

Each run is a deterministic 4-phase pipeline:

1. **Search** — [Serper.dev](https://serper.dev) Google queries across curated
   AI/ML job boards + broad free-text + direct search URLs (last-week freshness).
2. **Scrape** — [Crawl4AI](https://github.com/unclecode/crawl4ai) (headless
   browser) uses your LLM to extract structured job records.
3. **Pre-filter** — pure-Python gates (freshness, seniority/YOE, recruiters,
   geo-lock, domain allowlist, pay floor, AI-relevance). Zero LLM cost, fully
   deterministic, with 7-day cross-run de-duplication.
4. **Evaluate & draft** — your LLM scores each surviving job (0–100) and writes a
   ready-to-send proposal for the matches.

Output is a timestamped `report_*.json` per run (plus a raw scrape dump and an
audit trail of rejected jobs with reasons).

**Design principle:** all the matching is done by deterministic Python filters
driven by editable config — the LLM only *reads* and *writes*, it never decides
the filters. That keeps results reproducible and avoids hallucinated matches.

---

## Quickstart

```bash
# 1. Install
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure keys
cp .env.example .env
#   edit .env → set LLM_MODEL, ONE matching API key, and SERPER_API_KEY

# 3. Create your profile  (either way works)
python job/init_profile.py resume.pdf      # let the LLM draft it from your CV
#   ...or:
cp config/profile.example.yaml config/profile.yaml   # then hand-edit

# 4. Try it with no API calls (mock data), then go live
python job/job_remote.py --dry-run
python job/job_remote.py                    # live run
```

Run whichever markets you want:

```bash
python job/job_remote.py       # worldwide remote roles
python job/job_freelance.py    # freelance / contract gigs
python job/job_india_mnc.py    # India MNCs + funded startups
```

---

## Bring your own LLM

One env var picks the model for the whole pipeline, in
[litellm](https://docs.litellm.ai/docs/providers) `provider/model` form:

| Provider  | `LLM_MODEL` example                   | Key env var         |
|-----------|---------------------------------------|---------------------|
| DeepSeek  | `deepseek/deepseek-chat`              | `DEEPSEEK_API_KEY`  |
| Google    | `gemini/gemini-2.0-flash`             | `GEMINI_API_KEY`    |
| OpenAI    | `openai/gpt-4o-mini`                  | `OPENAI_API_KEY`    |
| Anthropic | `anthropic/claude-sonnet-4-20250514`  | `ANTHROPIC_API_KEY` |
| Groq      | `groq/llama-3.3-70b-versatile`        | `GROQ_API_KEY`      |

Set `LLM_MODEL` and the matching key in `.env` — that's it. (Search still needs
`SERPER_API_KEY`.)

---

## Configure it for you

Two kinds of config, cleanly separated:

### `config/profile.yaml` — who you are
Your name, stack, quantified achievements, location, years of experience, salary
floor, and target roles. This feeds the proposals and a couple of filters.
Create it with `python job/init_profile.py` or by copying
`config/profile.example.yaml`. It's gitignored — your details stay yours.

### `config/{remote,freelance,india_mnc}.yaml` — what to search
The AI/ML search tuning for each mode. Everything is editable data:

- `query_clusters`, `target_sites`, `direct_urls` — where and what to search
- `ai_relevance_keywords` — a job must mention at least one of these
- `title_reject_terms`, `seniority_reject_terms`, `experience_years_reject`,
  `geo_lock_tokens`, `education_reject_tokens` — the deterministic filters
- `max_posting_age_days`, `min_pay_per_hour_usd` — thresholds
- `scrape_instruction`, `eval_system`, `format_instructions` — the LLM prompts

Ships with working AI/ML defaults, so it's useful out of the box. Narrow it to
your sub-niche (e.g. only NLP, only voice AI, only computer vision) by editing
the keywords and clusters — no code changes.

---

## Repo layout

```
config/
  profile.example.yaml    # template for your profile
  remote.yaml             # per-mode search tuning (edit to retarget)
  freelance.yaml
  india_mnc.yaml
job/
  jobscan_config.py       # loads YAML → Config, compiles filter regexes
  jobscan_llm.py          # provider-agnostic LLM layer (litellm)
  init_profile.py         # build config/profile.yaml (LLM or Q&A)
  job_remote.py           # the three mode agents
  job_freelance.py
  job_india_mnc.py
.env.example
requirements.txt
```

---

## Disclaimer

This tool automates web search and scraping. **Respect each site's Terms of
Service and `robots.txt`, keep request volumes reasonable, and review every
drafted proposal before sending it.** It is not affiliated with any job board or
LLM provider. Use it to support genuine applications, not spam. See
[CONTRIBUTING.md](CONTRIBUTING.md) to help improve it.

## License

[MIT](LICENSE)
