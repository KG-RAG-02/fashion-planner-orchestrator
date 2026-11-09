"""Load the query-structuring settings and locate the prompt files. Standard library only.

Values that used to be hard-coded in this package live in configs/query_structuring.json; the
prompt text lives in prompts/<name>/<version>/. Both folders sit at the repository root.

Default locations assume a source checkout (src/ layout). To use the package from elsewhere
(another repository, a non-editable install), point to the folders explicitly:
  VOGUE_QUERY_CONFIG       path to a query_structuring.json
  VOGUE_QUERY_PROMPTS_DIR  path to a prompts/ folder
The configuration is read once, at import, and validated; a broken file fails loudly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "query_structuring.json"
DEFAULT_PROMPTS_DIR = REPO_ROOT / "prompts"


class QueryConfigError(RuntimeError):
    """The settings file or a prompt folder is missing or malformed."""


@dataclass(frozen=True)
class QueryConfig:
    prompt_name: str
    prompt_version: str
    max_query_chars: int
    draft_year_min: int
    draft_year_max: int
    unsupported_reasons: Mapping[str, str]  # placeholders {major_count}/{minor_count} still unformatted
    generic_unsupported_reason: str
    source: Path


def config_path() -> Path:
    return Path(os.environ.get("VOGUE_QUERY_CONFIG", DEFAULT_CONFIG_PATH))


def prompts_dir() -> Path:
    return Path(os.environ.get("VOGUE_QUERY_PROMPTS_DIR", DEFAULT_PROMPTS_DIR))


def _require(mapping: Any, key: str, kind: type, where: str) -> Any:
    if not isinstance(mapping, Mapping) or key not in mapping or not isinstance(mapping[key], kind):
        raise QueryConfigError(f"{where}.{key} must be a {kind.__name__}")
    return mapping[key]


def load_config(path: Path | None = None) -> QueryConfig:
    path = path or config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QueryConfigError(f"cannot read query config: {path}") from exc

    prompt = _require(raw, "prompt", dict, "config")
    limits = _require(raw, "input", dict, "config")
    draft = _require(raw, "draft", dict, "config")
    unsupported = _require(raw, "unsupported", dict, "config")
    reasons = _require(unsupported, "reasons", dict, "unsupported")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in reasons.items()):
        raise QueryConfigError("unsupported.reasons must map strings to strings")

    config = QueryConfig(
        prompt_name=_require(prompt, "name", str, "prompt"),
        prompt_version=_require(prompt, "version", str, "prompt"),
        max_query_chars=_require(limits, "max_query_chars", int, "input"),
        draft_year_min=_require(draft, "year_min", int, "draft"),
        draft_year_max=_require(draft, "year_max", int, "draft"),
        unsupported_reasons=dict(reasons),
        generic_unsupported_reason=_require(unsupported, "generic_reason", str, "unsupported"),
        source=path,
    )
    if config.max_query_chars <= 0 or config.draft_year_min > config.draft_year_max:
        raise QueryConfigError("input/draft limits are inconsistent")
    return config


def prompt_version_dir(name: str, version: str) -> Path:
    directory = prompts_dir() / name / version
    if not directory.is_dir():
        raise QueryConfigError(f"prompt version folder not found: {directory}")
    return directory


CONFIG = load_config()
