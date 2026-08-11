"""
job/import_wellfound_sheet.py — one-time importer: extract the 507-company
"Master Distinct List" from the user's Wellfound NCR-compilation .docx and
append the ones not already in job/companies_india.yaml's `ats:` list as new
`serper:` entries.

Why this exists (v12): India mode's registry shipped with `serper: []` — the
career-page search path was structurally dead, so job_india_mnc.py only ever
did direct ATS-API fetches from the ~93 `ats:` companies (mostly global firms,
audit-confirmed to yield ~0 India-relevant jobs). The user supplied a 507-company
NCR/Delhi/Gurgaon/Noida list scraped from Wellfound's Discover page — this
script is what turns that list into registry entries `select_companies()` and
`serper_careers_urls()` (job/companies.py) already know how to consume; no
runtime code changes needed for the registry side.

Usage:
    python job/import_wellfound_sheet.py path/to/compilation.docx
    python job/import_wellfound_sheet.py            # uses DEFAULT_DOCX below

Idempotent: re-running against an already-updated companies_india.yaml adds
nothing new (names are deduped against BOTH the existing ats: and serper: lists).
"""

import html
import os
import re
import sys
import zipfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(HERE, "companies_india.yaml")

# The docx this was built against — kept as a documented default so a future
# re-run needs no argument. Not committed (lives outside the repo).
DEFAULT_DOCX = ("/root/.claude/uploads/a95690ae-a0cd-5ec5-9bae-516589921498/"
                "71b87aee-Wellfound_NCR_Company_Compilation.docx")

SECTION_HEADER = "Master Distinct List"


def _paragraphs(docx_path: str) -> list:
    """Every <w:p> paragraph's text, in document order, entities unescaped."""
    with zipfile.ZipFile(docx_path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.DOTALL)
    out = []
    for p in paras:
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.DOTALL)
        out.append(html.unescape("".join(runs)))
    return out


def extract_master_list(docx_path: str) -> list:
    """Pull the alphabetical, deduplicated 507-company bullet list that sits
    right after the "Master Distinct List" section header. Every line in that
    section is a bullet ("• Company Name") until the first non-bullet,
    non-empty paragraph — that's the next section starting."""
    paras = _paragraphs(docx_path)
    start = None
    for i, t in enumerate(paras):
        if SECTION_HEADER in t:
            start = i + 1
            break
    if start is None:
        raise SystemExit(f"Could not find a '{SECTION_HEADER}' section header in {docx_path}")

    # The header is followed by a one-line description paragraph ("Every
    # distinct company name across all five searches...") before the bullets
    # actually start — skip forward to the first bullet line.
    while start < len(paras) and not paras[start].strip().startswith("•"):
        start += 1

    names = []
    for t in paras[start:]:
        stripped = t.strip()
        if not stripped:
            continue  # blank paragraphs are just spacing inside the bulleted list
        if not stripped.startswith("•"):
            break  # first non-bullet paragraph = next section, stop
        name = stripped.lstrip("•").strip()
        if name:
            names.append(name)
    return names


def _normalize(name: str) -> str:
    """Loose dedup key: lowercase, alphanumerics only. False negatives (a real
    duplicate slipping through as e.g. 'Foo' vs 'Foo Pvt Ltd') are harmless —
    worst case a company gets a redundant serper: entry, never a lost one."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def build_serper_entries(names: list, existing_ats: list, existing_serper: list) -> list:
    seen = {_normalize(c.get("name", "")) for c in existing_ats}
    seen |= {_normalize(c.get("name", "")) for c in existing_serper}
    new_entries, added = [], set()
    for name in names:
        key = _normalize(name)
        if not key or key in seen or key in added:
            continue
        added.add(key)
        new_entries.append({"name": name})
    return new_entries


def main():
    docx_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DOCX
    if not os.path.exists(docx_path):
        raise SystemExit(f"docx not found: {docx_path}")

    names = extract_master_list(docx_path)
    print(f"📄 Extracted {len(names)} company names from '{SECTION_HEADER}'")

    with open(REGISTRY_PATH) as f:
        registry = yaml.safe_load(f) or {}
    existing_ats = registry.get("ats") or []
    existing_serper = registry.get("serper") or []
    print(f"📇 Existing registry: {len(existing_ats)} ats, {len(existing_serper)} serper")

    new_entries = build_serper_entries(names, existing_ats, existing_serper)
    skipped = len(names) - len(new_entries)
    print(f"➕ Adding {len(new_entries)} new serper: entries ({skipped} already present / duplicate)")

    merged_serper = existing_serper + new_entries

    header = (
        "# Revived from git history (commit 979065b) for v11 — every ATS token was\n"
        "# live-verified at the time of that probe. Re-run job/probe_companies.py\n"
        "# periodically to catch token rot. Converted to the ats:/serper: schema\n"
        "# job/companies.py expects (was a flat companies: list).\n"
        "#\n"
        "# serper: entries added by job/import_wellfound_sheet.py from the user's\n"
        "# Wellfound NCR-compilation research doc (507 Delhi/Noida/Gurgaon/Gurugram +\n"
        "# remote-default companies). No public ATS confirmed for these — they're\n"
        "# searched via job/companies.py's serper_careers_urls() (Serper + Crawl4AI),\n"
        "# NOT fetched directly. Most are small/onsite NCR firms; expect a low\n"
        "# per-company hit rate (see v12 plan's Honest notes).\n"
    )

    with open(REGISTRY_PATH, "w") as f:
        f.write(header)
        f.write("ats:\n")
        for c in existing_ats:
            f.write(f"  - name: {c['name']!r}\n")
            f.write(f"    ats: {c['ats']}\n")
            f.write(f"    token: {c['token']}\n")
        f.write("serper:\n")
        if not merged_serper:
            f.write("  []\n")
        else:
            for c in merged_serper:
                f.write(f"  - name: {c['name']!r}\n")

    print(f"✅ Wrote {REGISTRY_PATH} — ats: {len(existing_ats)}, serper: {len(merged_serper)}")


if __name__ == "__main__":
    main()
