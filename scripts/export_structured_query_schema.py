"""Export the StructuredQuery 1.0 JSON Schema to contracts/agents/orchestrator/output/structured-query.schema.json.

The schema is generated from the pydantic models in src/fashion_planner_orchestrator/query/models.py
(this repository owns the contract, decision Y1) and is the file handed to receivers.
Serialization mode: it describes the JSON this repository emits, so every field is required.

Usage (from the repository root; needs pydantic, optional extra "query"):
  py scripts/export_structured_query_schema.py          # rewrite the schema file
  py scripts/export_structured_query_schema.py --check  # exit 1 when the file is out of date

Output is deterministic: sorted keys, 2-space indent, UTF-8 without escaping, trailing newline.
Drift check: tests/test_query_structured_models.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
TARGET_PATH = ROOT / "contracts" / "agents" / "orchestrator" / "output" / "structured-query.schema.json"
sys.path.insert(0, str(ROOT / "src"))

from fashion_planner_orchestrator.query.models import export_json_schema  # noqa: E402


def render() -> str:
    """Return the full text of structured-query.schema.json for the current models."""
    return json.dumps(export_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the schema file is out of date")
    args = parser.parse_args(argv)

    expected = render()
    current = TARGET_PATH.read_text(encoding="utf-8") if TARGET_PATH.exists() else None

    if args.check:
        if current != expected:
            print("structured-query.schema.json is out of date; run: py scripts/export_structured_query_schema.py")
            return 1
        print("structured-query.schema.json matches the models")
        return 0

    TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGET_PATH.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
