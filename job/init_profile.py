"""
init_profile.py — build your config/profile.yaml.

Two ways to use it:

  # 1. Let an LLM read your résumé and draft the profile for you
  python job/init_profile.py path/to/resume.txt
  python job/init_profile.py resume.pdf          # if pypdf is installed
  pbpaste | python job/init_profile.py -          # read résumé text from stdin

  # 2. No résumé / no LLM key — answer a few questions instead
  python job/init_profile.py

Either way it writes config/profile.yaml, which you can hand-edit afterwards.
The candidate identity lives only in this file; the AI/ML search tuning stays
in config/{remote,freelance,india_mnc}.yaml.
"""

import os
import re
import sys
import json

try:
    import yaml
except ImportError:
    raise SystemExit("PyYAML is required. Install it with:  pip install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CONFIG_DIR = os.path.join(os.path.dirname(HERE), "config")
PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.yaml")

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(HERE), ".env"))

import jobscan_llm

# Order + inline docs for the YAML we write.
FIELD_ORDER = [
    "name", "headline", "stack", "metrics",
    "location", "experience_years", "salary_min_usd_per_hour", "target_roles",
]

EXTRACT_PROMPT = """You are helping build a job-search profile for an AI/ML engineer.
From the résumé text below, extract a JSON object with EXACTLY these keys:

- name: full name (string)
- headline: one-line title with seniority, e.g. "AI Engineer (3 YOE)" (string)
- stack: comma-separated core technical stack, AI/ML tools first (string)
- metrics: 2-4 quantified achievements in one paragraph; keep real numbers (string)
- location: city/country the person is based in (string)
- experience_years: total years of professional experience (integer)
- salary_min_usd_per_hour: minimum acceptable hourly USD rate, integer; use 30 if unknown
- target_roles: list of 3-6 role titles they should apply for (array of strings)

Return ONLY the JSON object, no markdown, no commentary.

RÉSUMÉ:
"""


def read_resume(arg: str) -> str:
    if arg == "-":
        print("Paste résumé text, then press Ctrl-D (Unix) / Ctrl-Z Enter (Windows):")
        return sys.stdin.read()
    if not os.path.exists(arg):
        raise SystemExit(f"File not found: {arg}")
    if arg.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader
            except ImportError:
                raise SystemExit(
                    "Reading PDFs needs pypdf. Install it (`pip install pypdf`) or "
                    "paste the text instead:  python job/init_profile.py -"
                )
        reader = PdfReader(arg)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    with open(arg, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def extract_with_llm(resume_text: str) -> dict:
    print(f"🧠 Extracting profile with {jobscan_llm.get_model()} ...")
    # Budget generously: reasoning models (e.g. deepseek-v4-pro) spend a chunk of
    # the token budget on an internal reasoning trace before emitting the JSON, so
    # a small max_tokens can leave the actual answer empty on a full-length résumé.
    content, _ = jobscan_llm.chat_completion(
        messages=[{"role": "user", "content": EXTRACT_PROMPT + resume_text}],
        max_tokens=8000,
    )
    content = (content or "").strip()
    if not content:
        raise ValueError(
            "the model returned no text (its token budget was likely consumed by "
            "reasoning). Try a shorter résumé, or answer the questions below."
        )
    # Strip a ```json ... ``` fence if the model wrapped the answer.
    fence = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    if fence:
        content = fence.group(1).strip()
    # Fall back to the outermost { ... } if there is any surrounding prose.
    if not content.startswith("{"):
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            content = content[start:end + 1]
    return json.loads(content)


def ask_interactive() -> dict:
    print("\nLet's build your profile. Press Enter to accept the [default].\n")

    def q(prompt, default=""):
        val = input(f"{prompt} [{default}]: ").strip()
        return val or default

    data = {}
    data["name"] = q("Your name", "Your Name")
    data["headline"] = q("Headline (e.g. 'AI Engineer (3 YOE)')", "AI Engineer")
    data["stack"] = q("Core stack (comma-separated)",
                      "Python, PyTorch, RAG, LLMs, LangChain, FastAPI")
    data["metrics"] = q("Top quantified achievements (one line)",
                        "Add 2-3 real, quantified wins here.")
    data["location"] = q("Location / base country", "India")
    try:
        data["experience_years"] = int(q("Years of experience", "2") or 2)
    except ValueError:
        data["experience_years"] = 2
    try:
        data["salary_min_usd_per_hour"] = int(q("Minimum USD/hour (freelance floor)", "30") or 30)
    except ValueError:
        data["salary_min_usd_per_hour"] = 30
    roles = q("Target roles (comma-separated)",
              "LLM engineer, RAG engineer, AI agent engineer")
    data["target_roles"] = [r.strip() for r in roles.split(",") if r.strip()]
    return data


def normalise(data: dict) -> dict:
    """Coerce types and keep only known fields, in a stable order."""
    out = {}
    for k in FIELD_ORDER:
        if k not in data or data[k] in (None, ""):
            continue
        if k == "experience_years":
            try:
                out[k] = int(data[k])
            except (TypeError, ValueError):
                pass
        elif k == "salary_min_usd_per_hour":
            try:
                out[k] = int(data[k])
            except (TypeError, ValueError):
                pass
        elif k == "target_roles":
            v = data[k]
            out[k] = v if isinstance(v, list) else [s.strip() for s in str(v).split(",") if s.strip()]
        elif k in ("stack", "metrics"):
            v = data[k]
            out[k] = ", ".join(str(x).strip() for x in v) if isinstance(v, list) else str(v).strip()
        else:
            out[k] = str(data[k]).strip()
    return out


def write_profile(data: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(PROFILE_PATH):
        ans = input(f"\n{PROFILE_PATH} already exists. Overwrite? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted. Nothing written.")
            return
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        f.write("# Generated by init_profile.py — review and edit freely.\n")
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)
    print(f"\n✅ Wrote {PROFILE_PATH}")
    print("   Review it, then run a scan, e.g.:  python job/job_remote.py --dry-run")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None

    data = None
    if arg:
        resume = read_resume(arg)
        if not resume.strip():
            raise SystemExit("No résumé text found.")
        if jobscan_llm.resolve_token():
            try:
                data = normalise(extract_with_llm(resume))
                print("\nExtracted profile:")
                print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
            except Exception as e:
                print(f"⚠️  LLM extraction failed ({e}). Falling back to questions.")
        else:
            print("⚠️  No LLM API key set (see .env). Falling back to questions.")

    if data is None:
        data = normalise(ask_interactive())

    write_profile(data)


if __name__ == "__main__":
    main()
