"""
Worldwide-remote AI/ML job scan (v2 — ATS-registry pipeline).

Sources: config/companies_remote.yaml (Greenhouse/Lever/Ashby JSON APIs).
Tuning:  config/remote.yaml  ·  Profile: config/profile.yaml

  python job/job_remote.py            # live scan
  python job/job_remote.py --dry-run  # mock data, no network / no LLM
  python job/job_remote.py --fresh    # ignore the seen-cache for this run (tuning)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import run

if __name__ == "__main__":
    run("remote", dry_run="--dry-run" in sys.argv, fresh="--fresh" in sys.argv)
