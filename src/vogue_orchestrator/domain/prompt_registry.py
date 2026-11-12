"""Load immutable, versioned prompt assets from the repository prompt tree."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Mapping


PROMPT_ROOT = Path(__file__).resolve().parents[3] / "prompts"
_PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


@lru_cache(maxsize=1)
def _manifest() -> dict:
    try:
        value = json.loads((PROMPT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Prompt manifest is unavailable or invalid.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Prompt manifest must be a JSON object.")
    return value


def prompt_version_id(component: str) -> str:
    entry = _manifest().get(component)
    if not isinstance(entry, dict) or not isinstance(entry.get("version_id"), str):
        raise RuntimeError(f"Prompt component is not registered: {component}")
    return entry["version_id"]


@lru_cache(maxsize=32)
def load_prompt(component: str, part: str) -> str:
    if part not in {"system", "user"}:
        raise ValueError("prompt part must be 'system' or 'user'")
    entry = _manifest().get(component)
    directory = entry.get("directory") if isinstance(entry, dict) else None
    if not isinstance(directory, str):
        raise RuntimeError(f"Prompt component is not registered: {component}")
    root = (PROMPT_ROOT / directory).resolve()
    if PROMPT_ROOT.resolve() not in root.parents:
        raise RuntimeError("Prompt directory must remain inside prompts/.")
    suffix = "system_template.md" if part == "system" else "user_template.md"
    path = root / suffix
    try:
        content = path.read_text(encoding="utf-8").rstrip("\n")
    except OSError as exc:
        raise RuntimeError(f"Prompt asset is missing: {component}/{part}") from exc
    return content


def render_prompt(component: str, part: str, values: Mapping[str, str]) -> str:
    template = load_prompt(component, part)
    expected = set(_PLACEHOLDER.findall(template))
    if expected != set(values):
        raise ValueError(f"Prompt template values do not match placeholders for {component}/{part}.")
    rendered = _PLACEHOLDER.sub(lambda match: values[match.group(1)], template)
    if _PLACEHOLDER.search(rendered):
        raise ValueError(f"Unresolved prompt placeholder in {component}/{part}.")
    return rendered
