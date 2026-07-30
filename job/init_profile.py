"""
job/init_profile.py — build config/profile.yaml from your résumé (or a few
questions), so this tool searches for YOUR experience level, stack, and
location instead of the original author's.

  python job/init_profile.py path/to/resume.txt
  python job/init_profile.py resume.pdf          # needs `pip install pypdf`
  pbpaste | python job/init_profile.py -          # paste résumé text via stdin
  python job/init_profile.py                      # no résumé — answer questions instead

Either way this writes config/profile.yaml, which job/candidate_profile.py loads for all
three job_*.py scripts. Review the written file and hand-edit anything the
extraction got wrong — it's a best-effort starting point, not an authoritative
record (see job/candidate_profile.py's DEFAULT_PROFILE for what a missing field falls
back to).

IMPORTANT — the AI/ML domain focus is fixed, not derived from your résumé.
This tool only ever searches AI/ML/LLM/data-science roles (see
job/requirements.py and each script's Serper query clusters); your résumé
only fills in WHO you are (experience, stack, location), never WHAT domain
is searched. If your résumé shows no AI/ML signal at all, this script still
builds your profile but prints a loud warning so you know what you're
pointing the tool at.
"""

import json
import os
import re
import sys

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

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Order + inline docs for the YAML we write. Matches job/candidate_profile.py's DEFAULT_PROFILE keys.
FIELD_ORDER = [
    "name", "headline", "stack", "metrics", "location", "home_cities",
    "years_experience", "yoe_slack", "target_role_families",
    "education_ceiling", "min_rate_usd_per_hour",
]

AI_SIGNAL_PATTERN = re.compile(
    r"\b(ai|ml|machine learning|deep learning|llm|rag|pytorch|tensorflow|"
    r"data scien|nlp|computer vision|neural network|generative ai|langchain)\b",
    re.IGNORECASE,
)

EXTRACT_PROMPT = """You are helping build a job-search profile for an AI/ML-domain job seeker.
From the résumé text below, extract a JSON object with EXACTLY these keys:

- name: full name (string)
- headline: one-line title with seniority, e.g. "AI Engineer (2 YOE)" (string)
- stack: comma-separated core technical stack, AI/ML tools first (string)
- metrics: 2-4 quantified achievements in one paragraph; keep real numbers (string)
- location: city, country the person is based in (string, e.g. "Bangalore, India")
- home_cities: 2-4 major cities near their location that should count as "local"
  for onsite/hybrid roles (array of lowercase strings, e.g. ["bangalore","bengaluru"])
- years_experience: total years of professional experience (number, e.g. 1.5 is fine)
- target_role_families: 3-6 AI/ML-domain role titles they should apply for
  (array of strings), e.g. ["AI Engineer","LLM Engineer","Data Scientist"]
- education_ceiling: highest degree they hold — "bachelor's", "master's", or "phd" (string)
- min_rate_usd_per_hour: minimum acceptable hourly USD rate for freelance work,
  integer; use 30 if you can't tell

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


def check_ai_signal(resume_text: str):
    """The domain focus is fixed (AI/ML only) regardless of what's in the résumé —
    warn loudly rather than silently building a profile for a tool that will never
    surface a matching role for this person's actual background."""
    if not AI_SIGNAL_PATTERN.search(resume_text):
        print("\n⚠️  Your résumé doesn't show an obvious AI/ML/data-science signal.")
        print("   This tool ONLY searches AI/ML-domain roles (job/requirements.py and")
        print("   each script's query clusters) — that's fixed, not something your résumé")
        print("   changes. Fine if you're deliberately switching into AI/ML; otherwise this")
        print("   tool won't find you a match in your current field.\n")


def extract_with_llm(resume_text: str) -> dict:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    print("🧠 Extracting profile with deepseek-chat ...")
    resp = client.chat.completions.create(
        model="deepseek-chat", max_tokens=2000,
        messages=[{"role": "user", "content": EXTRACT_PROMPT + resume_text}],
    )
    content = (resp.choices[0].message.content or "").strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    return json.loads(content)


def ask_interactive() -> dict:
    print("\nLet's build your profile. Press Enter to accept the [default].\n")

    def q(prompt, default=""):
        val = input(f"{prompt} [{default}]: ").strip()
        return val or default

    data = {}
    data["name"] = q("Your name", "Your Name")
    data["headline"] = q("Headline (e.g. 'AI Engineer (2 YOE)')", "AI Engineer")
    data["stack"] = q("Core stack (comma-separated)",
                      "Python, PyTorch, RAG, LLMs, LangChain, FastAPI")
    data["metrics"] = q("Top quantified achievements (one line)",
                        "Add 2-3 real, quantified wins here.")
    data["location"] = q("Location (city, country)", "Bangalore, India")
    cities = q("Nearby cities that count as 'local' for onsite/hybrid roles (comma-separated)", "")
    data["home_cities"] = [c.strip().lower() for c in cities.split(",") if c.strip()]
    try:
        data["years_experience"] = float(q("Years of experience", "2") or 2)
    except ValueError:
        data["years_experience"] = 2
    try:
        data["yoe_slack"] = float(q("Slack above that you'd still consider applying to", "0.5") or 0.5)
    except ValueError:
        data["yoe_slack"] = 0.5
    roles = q("Target role families, AI/ML domain only (comma-separated)",
              "AI Engineer, ML Engineer, LLM/RAG Engineer, Data Scientist")
    data["target_role_families"] = [r.strip() for r in roles.split(",") if r.strip()]
    data["education_ceiling"] = q("Highest degree (bachelor's/master's/phd)", "bachelor's")
    try:
        data["min_rate_usd_per_hour"] = int(q("Minimum USD/hour (freelance floor)", "30") or 30)
    except ValueError:
        data["min_rate_usd_per_hour"] = 30
    return data


def normalise(data: dict) -> dict:
    """Coerce types and keep only known fields, in a stable order."""
    out = {}
    for k in FIELD_ORDER:
        if k not in data or data[k] in (None, ""):
            continue
        if k in ("years_experience", "yoe_slack"):
            try:
                out[k] = float(data[k])
            except (TypeError, ValueError):
                pass
        elif k == "min_rate_usd_per_hour":
            try:
                out[k] = int(data[k])
            except (TypeError, ValueError):
                pass
        elif k in ("target_role_families", "home_cities"):
            v = data[k]
            out[k] = v if isinstance(v, list) else [s.strip() for s in str(v).split(",") if s.strip()]
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
        f.write("# AI/ML domain focus is fixed by the tool, not this file — see README.\n")
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
        check_ai_signal(resume)
        if DEEPSEEK_API_KEY:
            try:
                data = normalise(extract_with_llm(resume))
                print("\nExtracted profile:")
                print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
            except Exception as e:
                print(f"⚠️  LLM extraction failed ({e}). Falling back to questions.")
        else:
            print("⚠️  DEEPSEEK_API_KEY not set (see .env). Falling back to questions.")

    if data is None:
        data = normalise(ask_interactive())

    write_profile(data)


if __name__ == "__main__":
    main()
