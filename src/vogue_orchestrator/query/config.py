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

from .vocabulary import GENDERS

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
    gender_aliases: Mapping[str, tuple[str, ...]]  # standard gender -> spellings (decision G-1)
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
    gender_aliases = _gender_aliases(_require(_require(raw, "gender", dict, "config"), "aliases", dict, "gender"))

    config = QueryConfig(
        prompt_name=_require(prompt, "name", str, "prompt"),
        prompt_version=_require(prompt, "version", str, "prompt"),
        max_query_chars=_require(limits, "max_query_chars", int, "input"),
        draft_year_min=_require(draft, "year_min", int, "draft"),
        draft_year_max=_require(draft, "year_max", int, "draft"),
        unsupported_reasons=dict(reasons),
        generic_unsupported_reason=_require(unsupported, "generic_reason", str, "unsupported"),
        gender_aliases=gender_aliases,
        source=path,
    )
    if config.max_query_chars <= 0 or config.draft_year_min > config.draft_year_max:
        raise QueryConfigError("input/draft limits are inconsistent")
    return config


def _spelling_key(text: str) -> str:
    # same comparison key as lexicon.norm_key (kept local: config must not import the dictionary)
    return "".join(ch for ch in text if not ch.isspace() and ch not in "-_").lower()


def _gender_aliases(table: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Validate the gender table: keys are standard GENDERS, each spelling maps to one value only."""
    owner: dict[str, str] = {_spelling_key(g): g for g in GENDERS}
    out: dict[str, tuple[str, ...]] = {}
    for value, spellings in table.items():
        if value not in GENDERS:
            raise QueryConfigError(f"gender.aliases key {value!r} is not a standard gender {GENDERS}")
        if not isinstance(spellings, list) or not all(isinstance(s, str) and s.strip() for s in spellings):
            raise QueryConfigError(f"gender.aliases.{value} must be a list of non-empty strings")
        for spelling in spellings:
            key = _spelling_key(spelling)
            if not key:  # e.g. "-" normalizes to "" and would match any empty lookup
                raise QueryConfigError(f"gender spelling {spelling!r} is empty after normalization")
            if owner.get(key, value) != value:
                raise QueryConfigError(f"gender spelling {spelling!r} maps to two values")
            owner[key] = value
        out[value] = tuple(spellings)
    return out


def prompt_version_dir(name: str, version: str) -> Path:
    directory = prompts_dir() / name / version
    if not directory.is_dir():
        raise QueryConfigError(f"prompt version folder not found: {directory}")
    return directory


CONFIG = load_config()
