"""Prompt for the LLM draft step, loaded from versioned text files. No model call, no key, no network.

Source of truth: prompts/<name>/<version>/ at the repository root (version chosen in
configs/query_structuring.json):
  system_template.md  system text with {{VOCABULARY}} and {{EXAMPLES}} placeholders
  user_template.md    user text with a {{QUERY}} placeholder
  examples.json       few-shot examples [{title, query, draft}], rendered into {{EXAMPLES}}
  rendered_system.md  GENERATED full system text, for reading only (scripts/render_query_prompt.py)

The standard vocabulary is injected from the generated vocabulary module, so the prompt can not
drift from the contract snapshot. To change the prompt, add a new version folder (prompts/README.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import vocabulary as vocab
from .config import CONFIG, QueryConfigError, prompt_version_dir

PROMPT_ID_PREFIX = "query-structuring-prompt"
PLACEHOLDERS = ("{{VOCABULARY}}", "{{EXAMPLES}}", "{{QUERY}}")


@dataclass(frozen=True)
class PromptTemplate:
    version: str  # folder name, for example "v1"
    system: str  # fully rendered system text
    user_template: str
    examples: tuple[dict[str, Any], ...]

    @property
    def prompt_id(self) -> str:
        return f"{PROMPT_ID_PREFIX}/{self.version}"


def vocabulary_section() -> str:
    lines = ["[표준 카테고리]", "상분류(major): " + ", ".join(vocab.CATEGORY_MAJOR), "하분류(minor), 상분류별:"]
    for major in vocab.CATEGORY_MAJOR:
        minors = [m for m in vocab.CATEGORY_MINOR if vocab.CATEGORY_MINOR_TO_MAJOR[m] == major]
        if minors:
            lines.append(f"- {major}: " + ", ".join(minors))
    lines += ["", "[표준 속성 값]"]
    for attribute_type in vocab.ATTRIBUTE_TYPES:
        lines.append(f"- {attribute_type}: " + ", ".join(vocab.ATTRIBUTE_VALUES[attribute_type]))
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


@lru_cache(maxsize=None)
def load_prompt(version: str | None = None) -> PromptTemplate:
    """Load and render one prompt version (default: the version in the config)."""
    version = version or CONFIG.prompt_version
    directory = prompt_version_dir(CONFIG.prompt_name, version)
    try:
        examples = tuple(json.loads(_read(directory, "examples.json")))
    except json.JSONDecodeError as exc:
        raise QueryConfigError(f"examples.json is not valid JSON in {directory}") from exc
    system = (
        _read(directory, "system_template.md")
        .replace("{{VOCABULARY}}", vocabulary_section())
        .replace("{{EXAMPLES}}", examples_section(examples))
    )
    user_template = _read(directory, "user_template.md")
    if any(token in system for token in PLACEHOLDERS) or user_template.count("{{QUERY}}") != 1:
        raise QueryConfigError(f"unresolved or missing placeholders in {directory}")
    return PromptTemplate(version=version, system=system, user_template=user_template, examples=examples)


_ACTIVE = load_prompt()
PROMPT_VERSION = _ACTIVE.prompt_id
SYSTEM_PROMPT = _ACTIVE.system
EXAMPLES = _ACTIVE.examples


@dataclass(frozen=True)
class PromptBundle:
    system: str
    user: str
    version: str
    query: str  # the original query; a draft provider may need it (test doubles key on it)

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
    )
