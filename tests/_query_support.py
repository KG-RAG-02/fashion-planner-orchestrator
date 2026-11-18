"""Shared helpers for the query-structuring tests (not collected as tests: no test_ prefix)."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "contracts" / "agents" / "trend" / "input" / "trend-query.contract.json"
FIXTURE_DIR = ROOT / "examples" / "fixtures" / "query"
QUERY_PACKAGE_DIR = ROOT / "src" / "fashion_planner_orchestrator" / "query"
SOURCE_DIR = ROOT / "src" / "fashion_planner_orchestrator"

# The query module is the only part of the repository that needs pydantic (optional extra "query").
# Tests that need it are skipped when it is missing so the stdlib-only suite keeps passing.
HAS_PYDANTIC = importlib.util.find_spec("pydantic") is not None
SKIP_REASON = "pydantic (optional extra 'query') is not installed"


def load_snapshot():
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def load_fixtures():
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(FIXTURE_DIR.glob("*.synthetic.json"))]
