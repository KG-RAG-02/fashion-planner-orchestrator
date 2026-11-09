"""Show query structuring end to end on the synthetic fixtures. No LLM, no network, no key.

Run from the repository root (needs pydantic: pip install -e ".[query]"):
  PYTHONPATH=src python examples/query_structuring_demo.py                  # summary of every fixture
  PYTHONPATH=src python examples/query_structuring_demo.py --case brand-unsupported   # full result of one case
  PYTHONPATH=src python examples/query_structuring_demo.py --show-prompt "가을 여성 코트"  # prompt sent to an LLM
On Windows PowerShell: $env:PYTHONPATH="src"; py examples/query_structuring_demo.py

The draft step uses FixtureDraftProvider, which returns the pre-written draft stored in each
fixture (examples/fixtures/query/*.synthetic.json). It shows what code does with a model's draft;
it is not a language model.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "query"


def main() -> int:
    parser = argparse.ArgumentParser(description="Query structuring demo (synthetic, no LLM)")
    parser.add_argument("--case", help="print the full result of one fixture case")
    parser.add_argument("--show-prompt", metavar="QUERY", help="print the prompt bundle for QUERY and exit")
    args = parser.parse_args()

    try:
        from vogue_orchestrator.query import FixtureDraftProvider, build_prompt, structure_query
    except ImportError:
        print("pydantic is required: pip install -e \".[query]\"", file=sys.stderr)
        return 1

    if args.show_prompt:
        bundle = build_prompt(args.show_prompt)
        print(f"# prompt version: {bundle.version}\n\n## system\n{bundle.system}\n\n## user\n{bundle.user}")
        return 0

    fixtures = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIXTURE_DIR.glob("*.synthetic.json"))]
    if args.case:
        fixtures = [f for f in fixtures if f["case"] == args.case]
        if not fixtures:
            print(f"unknown case: {args.case}", file=sys.stderr)
            return 1

    for fixture in fixtures:
        provider = FixtureDraftProvider({fixture["query"]: fixture["draft"]})
        result = structure_query(
            fixture["query"], request_id=fixture["request_id"], draft_provider=provider,
            today=date.fromisoformat(fixture["today"]),
        )
        trend_query = result.trend_query.model_dump(mode="json")
        matches = trend_query == fixture["expected_trend_query"]
        if args.case:
            print(json.dumps({"case": fixture["case"], "query": fixture["query"], "today": fixture["today"],
                              "matches_expected": matches, **result.to_dict()}, ensure_ascii=False, indent=2))
            continue
        season = trend_query["trend_terms"]["season"]
        categories = trend_query["requested"]["categories"]
        print(f"[{'OK ' if matches else 'DIFF'}] {fixture['case']}: {fixture['query']}")
        print(f"       season={season['year']} {season['season']} ({season['origin']})"
              f" include={[c['minor'] or c['major'] for c in categories['include']]}"
              f" exclude={[c['minor'] or c['major'] for c in categories['exclude']]}"
              f" attributes={[(a['type'], a['value']) for a in trend_query['requested']['attributes']]}"
              f" unsupported={[(u['type'], u['raw']) for u in trend_query['unsupported']]}"
              f" warnings={list(result.warnings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
