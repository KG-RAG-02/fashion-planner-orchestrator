"""Prompt for the LLM draft step, loaded from versioned text files. No model call, no key, no network.

Source of truth: prompts/<name>/<version>/ at the repository root (version chosen in
configs/query_structuring.json):
  meta.json           {"format": draft format of the output, "status": "current" | "archived"}
  system_template.md  system text with {{VOCABULARY}} and {{EXAMPLES}} placeholders
  user_template.md    user text with a {{QUERY}} placeholder
  examples.json       few-shot examples [{title, query, draft}], rendered into {{EXAMPLES}}
  rendered_system.md  GENERATED full system text, for reading only (scripts/render_query_prompt.py)

`format` names the shape of the JSON the version asks the model for, so callers and tests pick the
matching draft model: "draft_v2" -> draft.QueryDraft (prompts v2, v3),
"draft_v3" -> draft_v3.QueryDraftV3 (prompt v4, conditions grouped per item), "draft_v4" ->
draft_v4.QueryDraftV4 (prompt v5, flat conditions with an `item` field, grouped by code; FL-1).
`status` is documentation of the folder's life cycle ("current" = the format new work targets,
"archived" = kept only to trace old results; never edited again, QD-4). Which version actually runs is
chosen by the config, not by `status`.

The standard vocabulary is injected from the generated vocabulary module, so the prompt can not
drift from the contract snapshot. To change the prompt, add a new version folder (prompts/README.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import contract_vocabulary as cvocab
from . import vocabulary as vocab
from .config import CONFIG, QueryConfigError, prompt_version_dir

PROMPT_ID_PREFIX = "query-structuring-prompt"
PLACEHOLDERS = ("{{VOCABULARY}}", "{{EXAMPLES}}", "{{QUERY}}")
META_FILE = "meta.json"
DRAFT_FORMATS = ("draft_v2", "draft_v3", "draft_v4")
PROMPT_STATUSES = ("current", "archived")


@dataclass(frozen=True)
class PromptTemplate:
    version: str  # folder name, for example "v6"
    system: str  # fully rendered system text
    user_template: str
    examples: tuple[dict[str, Any], ...]
    format: str  # meta.json "format": one of DRAFT_FORMATS
    status: str  # meta.json "status": one of PROMPT_STATUSES

    @property
    def prompt_id(self) -> str:
        return f"{PROMPT_ID_PREFIX}/{self.version}"


def vocabulary_section(draft_format: str = "draft_v4") -> str:
    """Render standard values for the selected structured-query draft format."""
    lines = ["[표준 카테고리]", "상분류(major): " + ", ".join(vocab.CATEGORY_MAJOR), "하분류(minor), 상분류별:"]
    for major in vocab.CATEGORY_MAJOR:
        minors = [m for m in vocab.CATEGORY_MINOR if vocab.CATEGORY_MINOR_TO_MAJOR[m] == major]
        if minors:
            lines.append(f"- {major}: " + ", ".join(minors))
    lines += ["", "[표준 속성 값]"]
    if draft_format == "draft_v2":
        attribute_types, attribute_values = vocab.ATTRIBUTE_TYPES, vocab.ATTRIBUTE_VALUES
    else:
        attribute_types, attribute_values = cvocab.ATTRIBUTE_TYPES, cvocab.ATTRIBUTE_VALUES
    for attribute_type in attribute_types:
        lines.append(f"- {attribute_type}: " + ", ".join(attribute_values[attribute_type]))
    lines += ["", "[표준 성별 값]", ", ".join(vocab.GENDERS)]
    return "\n".join(lines)


def examples_section(examples: tuple[dict[str, Any], ...]) -> str:
    blocks = ["[예시]"]
    for index, example in enumerate(examples, start=1):
        blocks.append(
            f"예시 {index}\n질의: {example['query']}\n"
            f"출력: {json.dumps(example['draft'], ensure_ascii=False)}"
        )
    return "\n\n".join(blocks)


def _read(directory, name: str) -> str:
    path = directory / name
    try:
        return path.read_text(encoding="utf-8").rstrip("\n")
    except OSError as exc:
        raise QueryConfigError(f"prompt file not found: {path}") from exc


def _read_meta(directory) -> tuple[str, str]:
    try:
        meta = json.loads(_read(directory, META_FILE))
    except json.JSONDecodeError as exc:
        raise QueryConfigError(f"{META_FILE} is not valid JSON in {directory}") from exc
    if (
        not isinstance(meta, dict)
        or set(meta) != {"format", "status"}
        or meta["format"] not in DRAFT_FORMATS
        or meta["status"] not in PROMPT_STATUSES
    ):
        raise QueryConfigError(
            f"{META_FILE} in {directory} must be exactly {{format: {'|'.join(DRAFT_FORMATS)}, "
            f"status: {'|'.join(PROMPT_STATUSES)}}}"
        )
    return meta["format"], meta["status"]


@lru_cache(maxsize=None)
def load_prompt(version: str | None = None) -> PromptTemplate:
    """Load and render one prompt version (default: the version in the config). Any version folder
    can be loaded, whatever its format or status."""
    version = version or CONFIG.prompt_version
    directory = prompt_version_dir(CONFIG.prompt_name, version)
    draft_format, status = _read_meta(directory)
    try:
        examples = tuple(json.loads(_read(directory, "examples.json")))
    except json.JSONDecodeError as exc:
        raise QueryConfigError(f"examples.json is not valid JSON in {directory}") from exc
    system = (
        _read(directory, "system_template.md")
        .replace("{{VOCABULARY}}", vocabulary_section(draft_format))
        .replace("{{EXAMPLES}}", examples_section(examples))
    )
    user_template = _read(directory, "user_template.md")
    if any(token in system for token in PLACEHOLDERS) or user_template.count("{{QUERY}}") != 1:
        raise QueryConfigError(f"unresolved or missing placeholders in {directory}")
    return PromptTemplate(
        version=version, system=system, user_template=user_template, examples=examples,
        format=draft_format, status=status,
    )


_ACTIVE = load_prompt()
PROMPT_VERSION = _ACTIVE.prompt_id
PROMPT_FORMAT = _ACTIVE.format
SYSTEM_PROMPT = _ACTIVE.system
EXAMPLES = _ACTIVE.examples


@dataclass(frozen=True)
class PromptBundle:
    system: str
    user: str
    version: str
    query: str  # the original query; a draft provider may need it (test doubles key on it)
    format: str  # draft format the model is asked for (meta.json of the version), one of DRAFT_FORMATS

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self.system}, {"role": "user", "content": self.user}]


def build_prompt(query: str, version: str | None = None) -> PromptBundle:
    template = load_prompt(version)
    # The user text is fenced by <질의> tags; neutralise those tags if the query itself contains them.
    fenced = query.replace("<질의>", "＜질의＞").replace("</질의>", "＜/질의＞")
    return PromptBundle(
        system=template.system,
        user=template.user_template.replace("{{QUERY}}", fenced),
        version=template.prompt_id,
        query=query,
        format=template.format,
    )
