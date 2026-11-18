"""Evaluate the StructuredQuery path (the current prompt, flat conditions with `item`) on data/query-structuring-eval/dev.json.

Each query is run through `structured.structure_query` and the produced StructuredQuery is compared
with the item's `expected_structured_query` as a set of facts. Condition facts carry the ITEM they belong to
(its position i in `items`, which follows the query order):

  category  (i, "item", major, minor) = the item itself; (i, "exclude", major, minor) = an excluded category
  gender / attributes  (i, side, kind, value) - an unresolved value compares by its raw text
  unsupported (i, type, polarity, raw)      out_of_scope (raw)
  season    (year, season, origin), one per entry of `seasons`   style_names / product_model_names  (raw)

Brands are NOT scored: unsupported facts of type brand are dropped from both sides, and so is a produced
raw-text fact containing a labelled brand ("<브랜드> 스타일" as a style name). The labels still hold the brands.

So a condition put on the wrong item counts as one missing and one extra fact. `match` is compared
separately (same fact, different provenance). A query is "exact" when it has no missing and no extra
fact; "exact+match" also needs every match to agree.

Providers:
  fixture  the item's expected_draft is returned as the LLM output -> measures the CODE part only
           (resolver + assembly); should be 100 % unless code and labels disagree. Labels are grouped
           drafts; they are written in the model's flat shape first (draft.flatten_draft), so the
           grouping code is measured too.
  llm      the real model from .env (LLM_MODEL / LLM_API_KEY, src/fashion_planner_orchestrator/llm_provider.py).
           Sends every query to the external provider (paid): run it only on purpose.
  replay   the model outputs saved in an earlier report (--replay <report.json>) are fed in again, so a
           CODE change can be measured on the same LLM answers without any external call.

Usage (repository root):
  PYTHONPATH=src py scripts/evaluate_query_structuring.py --provider fixture
  PYTHONPATH=src py scripts/evaluate_query_structuring.py --provider llm --out .local/eval/<name>.json
  PYTHONPATH=src py scripts/evaluate_query_structuring.py --provider replay --replay .local/eval/<name>.json
The JSON report holds per-item facts, the produced draft and StructuredQuery, warnings and latency.
It never contains the API key.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data" / "query-structuring-eval" / "dev.json"

GROUPS = ("category", "gender", "color", "material", "pattern", "detail", "fit", "body_length", "sleeve_length",
          "unsupported", "out_of_scope", "season", "style", "product_model")


def _key(text: str) -> str:
    return "".join(text.lower().split()).replace("-", "").replace("_", "")


def _value(ref: dict[str, Any]) -> str:
    return ref["value"] if ref["value"] is not None else "raw:" + _key(ref["raw"])


def facts(sq: dict[str, Any]) -> dict[tuple, str | None]:
    """fact -> match (None for facts without provenance). fact[0] is the group name."""
    out: dict[tuple, str | None] = {}
    for i, item in enumerate(sq["items"]):
        category = item["category"]
        out[("category", i, "item", category and category["major"], category and category["minor"])] = (
            category["match"] if category else None)
        for ref in item["excluded_categories"]:
            out[("category", i, "exclude", ref["major"], ref["minor"])] = ref["match"]
        for side in ("include", "exclude"):
            for ref in item["gender"][side]:
                out[("gender", i, side, _value(ref))] = ref["match"]
            for kind, refs in item["attributes"][side].items():
                for ref in refs:
                    out[(kind, i, side, _value(ref))] = ref["match"]
        for entry in item["unsupported"]:
            out[("unsupported", i, entry["type"], entry["polarity"], _key(entry["raw"]))] = None
    for entry in sq["out_of_scope_items"]:
        out[("out_of_scope", _key(entry["raw"]))] = None
    for season in sq["trend_terms"]["seasons"]:
        out[("season", season["year"], season["season"], season["origin"])] = None
    for term in sq["trend_terms"]["style_names"]:
        out[("style", _key(term["raw"]))] = None
    for term in sq["trend_terms"]["product_model_names"]:
        out[("product_model", _key(term["raw"]))] = None
    return out


UNCHECKED_UNSUPPORTED_TYPES = ("brand",)  # decision BR-1 (2026-11-11): documented, not scored


def checked(expected: dict[tuple, str | None], produced: dict[tuple, str | None]
            ) -> tuple[dict[tuple, str | None], dict[tuple, str | None]]:
    """Decision BR-1: brands are not scored. Labels keep them (W-11); here every unsupported fact of an
    unchecked type is dropped from both sides, and so is any produced raw-text fact (style, model name,
    unsupported, out-of-scope) that contains a labelled brand ("<브랜드> 스타일" written as a style name)."""
    def unchecked(fact: tuple) -> bool:
        return fact[0] == "unsupported" and fact[2] in UNCHECKED_UNSUPPORTED_TYPES

    brands = {fact[4] for fact in expected if unchecked(fact)}

    def mentions_brand(fact: tuple) -> bool:
        text = fact[-1] if fact[0] in ("unsupported", "out_of_scope", "style", "product_model") else None
        return isinstance(text, str) and any(brand in text for brand in brands)

    return ({f: m for f, m in expected.items() if not unchecked(f)},
            {f: m for f, m in produced.items() if not unchecked(f) and not mentions_brand(f)})


def compare(expected: dict[tuple, str | None], produced: dict[tuple, str | None]) -> dict[str, Any]:
    missing = sorted(set(expected) - set(produced), key=str)
    extra = sorted(set(produced) - set(expected), key=str)
    match_diff = sorted(
        (f for f in set(expected) & set(produced) if expected[f] != produced[f]), key=str,
    )
    return {
        "missing": [list(f) for f in missing],
        "extra": [list(f) for f in extra],
        "match_diff": [[*f, expected[f], produced[f]] for f in match_diff],
        "exact": not missing and not extra,
        "exact_with_match": not missing and not extra and not match_diff,
    }


def _provider(name: str, item: dict[str, Any], replayed: dict[str, Any] | None = None):
    if name == "fixture":
        from fashion_planner_orchestrator.query.draft import flatten_draft

        return lambda bundle: flatten_draft(item["expected_draft"])
    if name == "replay":
        row = (replayed or {}).get(item["id"])
        if row is None:
            raise SystemExit(f"replay report has no item {item['id']}")
        saved = row.get("raw_output") if row.get("raw_output") is not None else row.get("draft")
        return lambda bundle: saved
    raise AssertionError(name)


def run(data: dict[str, Any], provider_name: str, ids: Iterable[str] | None,
        replay: dict[str, Any] | None = None) -> dict[str, Any]:
    from fashion_planner_orchestrator.query.errors import QueryStructuringError
    from fashion_planner_orchestrator.query.prompt import PROMPT_ID, PROMPT_SHA256
    from fashion_planner_orchestrator.query.structured import structure_query

    today = date.fromisoformat(data["reference_today"])
    llm = None
    if provider_name == "llm":
        from fashion_planner_orchestrator.llm_provider import build_draft_provider, llm_status

        llm = build_draft_provider()
        status = llm_status()
        model = {"provider": status.get("provider"), "model": status.get("model")}
    elif provider_name == "replay":
        model = {**(replay or {}).get("model", {}), "replayed": True}
    else:
        model = {"provider": "fixture", "model": None}
    replayed = {row["id"]: row for row in (replay or {}).get("items", [])}
    wanted = set(ids) if ids else None
    rows = []
    for item in data["items"]:
        if wanted is not None and item["id"] not in wanted:
            continue
        provider = llm if llm is not None else _provider(provider_name, item, replayed)
        raw_output: dict[str, Any] = {}

        def recording(bundle, _provider=provider):
            produced = _provider(bundle)
            raw_output["text"] = produced if isinstance(produced, str) else json.dumps(produced, ensure_ascii=False)
            return produced

        started = time.perf_counter()
        row: dict[str, Any] = {"id": item["id"], "query": item["query"], "tags": item["tags"]}
        try:
            result = structure_query(item["query"], request_id=item["id"], draft_provider=recording, today=today)
        except QueryStructuringError as exc:
            row.update(error=exc.code, raw_output=raw_output.get("text"), exact=False, exact_with_match=False)
        else:
            produced = result.structured_query.model_dump(mode="json")
            row.update(compare(*checked(facts(item["expected_structured_query"]), facts(produced))))
            row.update(draft=result.draft.model_dump(mode="json"), structured_query=produced,
                       warnings=list(result.warnings), expected_warnings=item.get("expected_warnings", []))
            if provider_name == "llm":
                row["raw_output"] = raw_output.get("text")
        row["seconds"] = round(time.perf_counter() - started, 2)
        rows.append(row)
    return {"model": model, "prompt_id": PROMPT_ID, "prompt_sha256": PROMPT_SHA256, "today": data["reference_today"],
            "summary": summarize(data, rows), "items": rows}


def summarize(data: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected_by_id = {item["id"]: checked(facts(item["expected_structured_query"]), {})[0] for item in data["items"]}
    per_group = {g: Counter() for g in GROUPS}
    match_total = match_same = 0
    for row in rows:
        expected = expected_by_id[row["id"]]
        missing = {tuple(f) for f in row.get("missing", [])}
        if "error" in row:
            missing = set(expected)
        extra = {tuple(f) for f in row.get("extra", [])}
        for fact in expected:
            per_group[fact[0]]["expected"] += 1
            per_group[fact[0]]["found" if fact not in missing else "missed"] += 1
        for fact in extra:
            per_group[fact[0]]["extra"] += 1
        if "error" not in row:
            common = [f for f in expected if f not in missing and expected[f] is not None]
            match_total += len(common)
            match_same += len(common) - len(row["match_diff"])
    groups = {}
    for g, c in per_group.items():
        if not c["expected"] and not c["extra"]:
            continue
        found, extra, expected = c["found"], c["extra"], c["expected"]
        groups[g] = {
            "expected": expected, "found": found, "missed": c["missed"], "extra": extra,
            "recall": round(found / expected, 3) if expected else None,
            "precision": round(found / (found + extra), 3) if (found + extra) else None,
        }
    n = len(rows)
    return {
        "items": n,
        "errors": dict(Counter(row["error"] for row in rows if "error" in row)),
        "exact": sum(row["exact"] for row in rows),
        "exact_with_match": sum(row["exact_with_match"] for row in rows),
        "match_agreement": round(match_same / match_total, 3) if match_total else None,
        "mean_seconds": round(sum(row["seconds"] for row in rows) / n, 2) if n else None,
        "groups": groups,
    }


def print_report(report: dict[str, Any]) -> None:
    s = report["summary"]
    print(f"model: {report['model']}  prompt: {report['prompt_id']} {report['prompt_sha256'][:12]}  today: {report['today']}")
    print(f"items {s['items']}  exact {s['exact']}  exact+match {s['exact_with_match']}  errors {s['errors']}  "
          f"match agreement {s['match_agreement']}  mean {s['mean_seconds']}s")
    print(f"{'group':<14}{'expected':>9}{'found':>7}{'missed':>8}{'extra':>7}{'recall':>8}{'precision':>11}")
    for g, v in s["groups"].items():
        print(f"{g:<14}{v['expected']:>9}{v['found']:>7}{v['missed']:>8}{v['extra']:>7}"
              f"{str(v['recall']):>8}{str(v['precision']):>11}")
    for row in report["items"]:
        if row.get("exact_with_match"):
            continue
        print(f"- {row['id']} {row['query']}")
        if "error" in row:
            print(f"    error {row['error']}")
            continue
        for label in ("missing", "extra", "match_diff"):
            if row[label]:
                print(f"    {label}: {row[label]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=("fixture", "llm", "replay"), default="fixture")
    parser.add_argument("--replay", type=Path, help="report whose saved model outputs --provider replay uses")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--ids", nargs="*", help="only these item ids")
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    args = parser.parse_args(argv)
    data = json.loads(args.data.read_text(encoding="utf-8"))
    replay = None
    if args.provider == "replay":
        if not args.replay:
            parser.error("--provider replay needs --replay <report.json>")
        replay = json.loads(args.replay.read_text(encoding="utf-8"))
    report = run(data, args.provider, args.ids, replay)
    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
