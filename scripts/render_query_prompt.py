"""Write prompts/query_structuring/<version>/rendered_system.md: the exact system text sent to the LLM.

The rendered file is for reading and reviewing only (templates + vocabulary + examples put
together). The code never reads it; it renders from the templates at import time.
Every version folder is rendered or checked, whatever its draft format or status (meta.json), so an
archived version stays reviewable and a broken meta.json fails here as well.

Usage (from the repository root; needs pydantic only because the package is imported):
  PYTHONPATH=src python scripts/render_query_prompt.py            # every version folder
  PYTHONPATH=src python scripts/render_query_prompt.py --check    # exit 1 if any rendered file is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vogue_orchestrator.query.config import CONFIG, prompts_dir  # noqa: E402
from vogue_orchestrator.query.prompt import load_prompt  # noqa: E402

RENDERED_NAME = "rendered_system.md"


def version_dirs() -> list[Path]:
    base = prompts_dir() / CONFIG.prompt_name
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "system_template.md").exists())


def main() -> int:
    parser = argparse.ArgumentParser(description="Render prompt versions for review")
    parser.add_argument("--check", action="store_true", help="exit 1 if a rendered file is out of date")
    args = parser.parse_args()

    stale = []
    for directory in version_dirs():
        template = load_prompt(directory.name)
        expected = template.system + "\n"
        target = directory / RENDERED_NAME
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if args.check:
            if current != expected:
                stale.append(directory.name)
            continue
        if current == expected:  # leave an up-to-date file alone; a stale one is regenerated, archived or not (it is derived)
            print(f"unchanged {target.relative_to(ROOT)} (format {template.format}, status {template.status})")
            continue
        target.write_text(expected, encoding="utf-8", newline="\n")
        print(f"wrote {target.relative_to(ROOT)} (format {template.format}, status {template.status})")
    if stale:
        print("stale rendered prompt: " + ", ".join(stale) + "; run scripts/render_query_prompt.py")
        return 1
    if args.check:
        print("rendered prompts are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
