"""Make ``flybrain`` importable: it lives in the agent dir, not on the module path."""

import os
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_DIR.parents[1]
# This directory has no __init__.py (a package named ``tests`` would collide
# with the repo's), so pytest does not put the repo root on the path itself.
for entry in (str(REPO_ROOT), str(AGENT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# The opt-in full-graph test needs the prepared dataset from this install's
# agent home; every other test uses tmp_path.
os.environ.setdefault(
    "CONDOR_FLY_DATA",
    str(REPO_ROOT / ".condor" / "agents" / "market_making_fly" / "data"),
)
