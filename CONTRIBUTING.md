# Contributing to AI-Job-Scan

Thanks for your interest! AI-Job-Scan is an open-source AI/ML job-search agent.
The goal is a sharp, reliable tool for **AI/ML engineers** — contributions that
improve match quality, add providers, or make the project easier to use are all
welcome.

## Architecture in one minute

The deterministic pipeline lives in `job/job_<mode>.py` (four phases: Serper
search → Crawl4AI scrape → pure-Python pre-filter → LLM evaluate & draft). The
scripts contain **logic only** — all the tunable **data** is loaded from YAML:

- `config/profile.yaml` — the candidate (you). Not committed; generated from
  `config/profile.example.yaml` or via `python job/init_profile.py`.
- `config/{remote,freelance,india_mnc}.yaml` — per-mode search tuning: query
  clusters, target sites, keyword/reject lists, thresholds, prompt templates.

Two small shared modules glue it together:

- `job/jobscan_config.py` — loads + validates the YAML, compiles the reject/
  relevance term lists into regexes, exposes a `Config` object.
- `job/jobscan_llm.py` — one provider-agnostic LLM layer (litellm). `LLM_MODEL`
  selects the model for both the scrape and the evaluation steps.

**Please keep pipeline logic and tunable data separate.** Filtering must stay
deterministic (data-driven regex/keywords in Python), not delegated to the LLM
at runtime — that is what keeps results reproducible and hallucination-free.

## Dev setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # set LLM_MODEL + one API key + SERPER_API_KEY
cp config/profile.example.yaml config/profile.yaml   # or: python job/init_profile.py
```

Every mode has a `--dry-run` that exercises the full pipeline on built-in mock
data with **no API calls** — use it to check your changes:

```bash
python job/job_remote.py --dry-run
python job/job_freelance.py --dry-run
python job/job_india_mnc.py --dry-run
```

## Common contributions

- **Tune the search** — edit `config/<mode>.yaml`: add query clusters, target
  sites, AI-relevance keywords, or reject terms. No code changes needed.
- **Add an LLM provider** — litellm already supports most; if a provider needs a
  new key env var, add it to `PROVIDER_KEY_ENV` in `job/jobscan_llm.py` and to
  `.env.example`.
- **Add a new mode/preset** — add `config/<mode>.yaml`, then a thin
  `job/job_<mode>.py` following the existing pattern (or generalise the shared
  engine — discuss in an issue first).

## Pull requests

1. Keep diffs focused; describe what changed and why.
2. Run all three `--dry-run`s and confirm they still pass.
3. Do **not** commit `config/profile.yaml`, `.env`, `reports_*/`, or
   `seen_fp_*.json` (they are gitignored).
4. Don't hardcode personal data — that belongs in `config/profile.yaml`.

## Please scrape responsibly

Respect each job board's Terms of Service and `robots.txt`, keep request volumes
reasonable, and use the tool to support genuine applications — not spam.
