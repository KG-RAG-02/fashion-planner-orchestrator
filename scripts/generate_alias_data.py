"""Regenerate src/vogue_orchestrator/query/alias_data.py from the alias-map snapshot.

The snapshot (docs/contracts/alias-map.snapshot.json) is a read-only copy of the alias dictionary
owned by trend-analysis-agent (configs/alias_map/*.yaml). It is produced by
scripts/export_alias_snapshot.py. This script turns it into plain Python constants so the query
module needs neither the docs folder nor pyyaml at run time.

Usage (from the repository root):
  py scripts/generate_alias_data.py          # rewrite the module
  py scripts/generate_alias_data.py --check  # exit 1 when the module is out of date

Standard library only. Description of the snapshot: docs/contracts/alias-map.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "docs" / "contracts" / "alias-map.snapshot.json"
TARGET_PATH = ROOT / "src" / "vogue_orchestrator" / "query" / "alias_data.py"

# The 11 attributes AliasMap normalizes (trend alias_map.STANDARD), in that order.
ATTRIBUTES = (
    "color", "category", "material", "pattern", "detail", "neckline", "collar",
    "fit", "shape", "body_length", "sleeve_length",
)
# Sections the port (query/lexicon.py) knows how to apply. An unknown section in a new snapshot
# stops generation on purpose: the port has to be reviewed against the new trend code first.
KNOWN_SECTIONS = {"standard", "aliases", "approx", "null_values", "route", "hint", "modifiers"}
MAPPING_SECTIONS = ("aliases", "approx", "route", "hint")
LIST_SECTIONS = ("null_values", "modifiers")

HEADER = '''"""GENERATED FILE - do not edit by hand.

Alias dictionary of trend-analysis-agent (configs/alias_map/*.yaml), generated from the read-only
snapshot docs/contracts/alias-map.snapshot.json. The dictionary is owned by trend-analysis-agent;
changes follow its rule D-29 and arrive here only through a new snapshot.

Entry order inside each section is the YAML order (the original code depends on it: the first
alias wins when two values share a spelling).

Regenerate:  <trend .venv python> scripts/export_alias_snapshot.py --trend-repo <path>
             py scripts/generate_alias_data.py
Drift check: tests/test_query_lexicon.py (fails when this file and the snapshot disagree)
"""

from __future__ import annotations

from typing import Any
'''


def snapshot_data(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """The data part of the snapshot (everything except `_provenance`), in file order."""
    return {key: value for key, value in snapshot.items() if key != "_provenance"}


def content_sha256(data: Mapping[str, Any]) -> str:
    """Hash of the data part: compact JSON, keys in their stored order, UTF-8."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _literal(value: Any, indent: int) -> str:
    """Python literal for a JSON value (lists become tuples). Mapping order is preserved."""
    pad = "    " * indent
    inner = "    " * (indent + 1)
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        if not value:
            return "()"
        if all(isinstance(item, str) for item in value):
            items = ", ".join(_literal(item, 0) for item in value)
            one_line = f"({items},)" if len(value) == 1 else f"({items})"
            if len(inner) + len(one_line) <= 80:
                return one_line
        lines = ["("] + [f"{inner}{_literal(item, indent + 1)}," for item in value] + [f"{pad})"]
        return "\n".join(lines)
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        if all(item is None or isinstance(item, (str, bool, int, float)) for item in value.values()):
            items = ", ".join(f"{_literal(key, 0)}: {_literal(item, 0)}" for key, item in value.items())
            if len(inner) + len(items) <= 80:
                return "{" + items + "}"
        lines = ["{"]
        for key, item in value.items():
            lines.append(f"{inner}{_literal(key, 0)}: {_literal(item, indent + 1)},")
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    raise TypeError(f"unsupported snapshot value: {value!r}")


def _check_snapshot(snapshot: Mapping[str, Any]) -> None:
    attributes = snapshot["attributes"]
    if set(attributes) != set(ATTRIBUTES):
        raise SystemExit(f"snapshot error: attributes {sorted(attributes)} differ from {sorted(ATTRIBUTES)}")
    for name, section in attributes.items():
        unknown = set(section) - KNOWN_SECTIONS
        if unknown:
            raise SystemExit(f"snapshot error: {name} has sections the port does not know: {sorted(unknown)}")
        if not section.get("standard"):
            raise SystemExit(f"snapshot error: {name} has no standard values")
    if "modifiers" not in attributes["color"]:
        raise SystemExit("snapshot error: color.modifiers is missing")
    if set(snapshot["text_guard"]) != {"guards"}:
        raise SystemExit("snapshot error: text_guard must contain only `guards`")
    expected = snapshot["_provenance"]["content_sha256"]
    actual = content_sha256(snapshot_data(snapshot))
    if expected != actual:
        raise SystemExit(
            "snapshot error: content_sha256 does not match the data (was the snapshot edited by hand?)"
        )


def render(snapshot: Mapping[str, Any]) -> str:
    """Return the full text of alias_data.py for the given snapshot."""
    _check_snapshot(snapshot)
    provenance = snapshot["_provenance"]
    attributes = snapshot["attributes"]

    parts = [HEADER]
    prov = {
        "source_repo": provenance["source_repo"],
        "source_ref": provenance["source_ref"],
        "content_sha256": provenance["content_sha256"],
        "generated_from": "docs/contracts/alias-map.snapshot.json",
    }
    parts.append(f"PROVENANCE: dict[str, str] = {_literal(prov, 0)}")
    parts.append(f"ATTRIBUTES: tuple[str, ...] = {_literal(list(ATTRIBUTES), 0)}")
    parts.append(
        "STANDARD_VALUES: dict[str, tuple[str, ...]] = "
        + _literal({name: attributes[name]["standard"] for name in ATTRIBUTES}, 0)
    )
    parts.append(
        "ALIASES: dict[str, dict[str, tuple[str, ...]]] = "
        + _literal({name: attributes[name].get("aliases") or {} for name in ATTRIBUTES}, 0)
    )
    parts.append(
        "APPROX: dict[str, dict[str, tuple[str, ...]]] = "
        + _literal({name: attributes[name].get("approx") or {} for name in ATTRIBUTES}, 0)
    )
    parts.append(
        "NULL_VALUES: dict[str, tuple[str, ...]] = "
        + _literal({name: attributes[name].get("null_values") or [] for name in ATTRIBUTES}, 0)
    )
    parts.append(
        "ROUTES: dict[str, dict[str, dict[str, Any]]] = "
        + _literal({name: attributes[name].get("route") or {} for name in ATTRIBUTES}, 0)
    )
    parts.append(
        "HINTS: dict[str, dict[str, dict[str, Any]]] = "
        + _literal({name: attributes[name].get("hint") or {} for name in ATTRIBUTES}, 0)
    )
    parts.append(
        "COLOR_MODIFIERS: tuple[str, ...] = " + _literal(attributes["color"]["modifiers"], 0)
    )
    parts.append(
        "COMPOUND_VALUES: tuple[str, ...] = " + _literal(snapshot["code_constants"]["compound_values"], 0)
    )
    parts.append(
        "TEXT_GUARDS: dict[str, tuple[str, ...]] = " + _literal(snapshot["text_guard"]["guards"], 0)
    )
    return "\n\n".join(parts) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if alias_data.py is out of date")
    args = parser.parse_args(argv)

    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    expected = render(snapshot)
    current = TARGET_PATH.read_text(encoding="utf-8") if TARGET_PATH.exists() else None

    if args.check:
        if current != expected:
            print("alias_data.py is out of date; run: py scripts/generate_alias_data.py")
            return 1
        print("alias_data.py matches the alias-map snapshot")
        return 0

    TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGET_PATH.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
