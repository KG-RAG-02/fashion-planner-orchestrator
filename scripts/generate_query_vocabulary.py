"""Regenerate src/vogue_orchestrator/query/vocabulary.py from the TrendQuery contract snapshot.

The snapshot (docs/contracts/trend-query.contract.json) is a read-only copy of the contract
owned by trend-analysis-agent. This script turns it into plain Python constants so the query
module has no runtime dependency on the docs folder and can be moved to another repository.

Usage (from the repository root):
  py scripts/generate_query_vocabulary.py          # rewrite the module
  py scripts/generate_query_vocabulary.py --check  # exit 1 when the module is out of date

Standard library only. Update procedure: docs/query-structuring.md section 9.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contracts" / "trend-query.contract.json"
TARGET_PATH = ROOT / "src" / "vogue_orchestrator" / "query" / "vocabulary.py"

HEADER = '''"""GENERATED FILE - do not edit by hand.

Standard vocabulary of the TrendQuery contract, generated from the read-only snapshot
docs/contracts/trend-query.contract.json (a copy of the contract owned by trend-analysis-agent).

Regenerate:  py scripts/generate_query_vocabulary.py
Drift check: tests/test_query_vocabulary.py (fails when this file and the snapshot disagree)
"""

from __future__ import annotations
'''


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _tuple_block(name: str, values: Sequence[str]) -> str:
    lines = [f"{name}: tuple[str, ...] = ("]
    lines += [f"    {_q(value)}," for value in values]
    lines.append(")")
    return "\n".join(lines)


def _check_contract(contract: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    vocab = contract["vocabulary"]
    defs = contract["json_schema"]["$defs"]
    attribute_types = tuple(defs["RequestedAttribute"]["properties"]["type"]["enum"])
    season_origins = tuple(defs["SeasonRequest"]["properties"]["origin"]["enum"])
    if set(vocab["attribute_values"]) != set(attribute_types):
        raise SystemExit("contract error: attribute_values keys differ from the attribute type enum")
    minor_to_major = contract["category_minor_to_major"]
    if set(minor_to_major) != set(vocab["category_minor"]):
        raise SystemExit("contract error: category_minor_to_major does not cover category_minor")
    if not set(minor_to_major.values()) <= set(vocab["category_major"]):
        raise SystemExit("contract error: category_minor_to_major points outside category_major")
    return attribute_types, season_origins


def render(contract: Mapping[str, Any]) -> str:
    """Return the full text of vocabulary.py for the given contract snapshot."""
    attribute_types, season_origins = _check_contract(contract)
    vocab = contract["vocabulary"]
    provenance = contract["_provenance"]

    parts = [HEADER]
    parts.append(
        "PROVENANCE: dict[str, str] = {\n"
        f"    \"contract\": {_q(provenance['contract'])},\n"
        f"    \"source_commit\": {_q(provenance['source_commit'])},\n"
        f"    \"content_sha256\": {_q(provenance['content_sha256'])},\n"
        f"    \"generated_from\": {_q(provenance['generated_from'])},\n"
        "}"
    )
    parts.append(_tuple_block("SEASONS", vocab["season"]))
    parts.append(_tuple_block("SEASON_ORIGINS", season_origins))
    parts.append(_tuple_block("GENDERS", vocab["gender"]))
    parts.append(_tuple_block("CATEGORY_MAJOR", vocab["category_major"]))
    parts.append(_tuple_block("CATEGORY_MINOR", vocab["category_minor"]))
    mapping_lines = ["CATEGORY_MINOR_TO_MAJOR: dict[str, str] = {"]
    mapping_lines += [
        f"    {_q(minor)}: {_q(contract['category_minor_to_major'][minor])},"
        for minor in vocab["category_minor"]
    ]
    mapping_lines.append("}")
    parts.append("\n".join(mapping_lines))
    parts.append(_tuple_block("ATTRIBUTE_TYPES", attribute_types))
    value_lines = ["ATTRIBUTE_VALUES: dict[str, tuple[str, ...]] = {"]
    for attribute_type in attribute_types:
        value_lines.append(f"    {_q(attribute_type)}: (")
        value_lines += [f"        {_q(value)}," for value in vocab["attribute_values"][attribute_type]]
        value_lines.append("    ),")
    value_lines.append("}")
    parts.append("\n".join(value_lines))
    return "\n\n".join(parts) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if vocabulary.py is out of date")
    args = parser.parse_args(argv)

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    expected = render(contract)
    current = TARGET_PATH.read_text(encoding="utf-8") if TARGET_PATH.exists() else None

    if args.check:
        if current != expected:
            print("vocabulary.py is out of date; run: py scripts/generate_query_vocabulary.py")
            return 1
        print("vocabulary.py matches the contract snapshot")
        return 0

    TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGET_PATH.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
