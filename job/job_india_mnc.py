"""
India MNC / GCC / startup AI/ML job scan (v2 — ATS-registry pipeline).

Sources: config/companies_india.yaml (Indian AI startups on Greenhouse/Lever/
Ashby + Workday GCCs + company-domain Serper fallback).
Tuning:  config/india_mnc.yaml  ·  Profile: config/profile.yaml

  python job/job_india_mnc.py            # live scan
  python job/job_india_mnc.py --dry-run  # mock data, no network / no LLM
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import run

if __name__ == "__main__":
    run("india_mnc", dry_run="--dry-run" in sys.argv)
