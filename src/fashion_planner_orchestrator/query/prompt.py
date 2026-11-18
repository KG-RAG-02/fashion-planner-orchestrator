"""Prompt for the LLM draft step, loaded from text files. No model call, no key, no network.

Source of truth: prompts/<name>/ at the repository root (name in configs/query_structuring.json):
  system_template.md  system text with {{VOCABULARY}} and {{EXAMPLES}} placeholders
  user_template.md    user text with a {{QUERY}} placeholder
  examples.json       few-shot examples [{title, query, draft}], rendered into {{EXAMPLES}}; each draft is a
                      draft.QueryDraft (flat conditions with an `item` field)
  rendered_system.md  GENERATED full system text, for reading only (scripts/render_query_prompt.py)

The standard vocabulary is injected from the generated vocabulary module, so the prompt can not drift
from the contract snapshot (prompts/README.md). A result names the prompt by PROMPT_ID and the sha256 of
the three template files (`prompt_sha256`), so a changed prompt is told apart without version folders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import contract_vocabulary as cvocab
from . import vocabulary as vocab
from .config import CONFIG, QueryConfigError, prompt_dir

PROMPT_ID = "query-structuring"
TEMPLATE_FILES = ("system_template.md", "user_template.md", "examples.json")
PLACEHOLDERS = ("{{VOCABULARY}}", "{{EXAMPLES}}", "{{QUERY}}")


@dataclass(frozen=True)
class PromptTemplate:
    sha256: str  # sha256 of the template files (TEMPLATE_FILES, in that order)
    system: str  # fully rendered system text
    user_template: str
    examples: tuple[dict[str, Any], ...]


def vocabulary_section() -> str:
    """The [표준 ...] block: standard categories, the StructuredQuery attribute kinds and the genders."""
    lines = ["[표준 카테고리]", "상분류(major): " + ", ".join(vocab.CATEGORY_MAJOR), "하분류(minor), 상분류별:"]
    for major in vocab.CATEGORY_MAJOR:
        minors = [m for m in vocab.CATEGORY_MINOR if vocab.CATEGORY_MINOR_TO_MAJOR[m] == major]
        if minors:
            lines.append(f"- {major}: " + ", ".join(minors))
    lines += ["", "[표준 속성 값]"]
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


def template_sha256(directory) -> str:
    digest = hashlib.sha256()
    for name in TEMPLATE_FILES:
        digest.update(name.encode("utf-8") + b"\0" + (directory / name).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=None)
def load_prompt() -> PromptTemplate:
    """Load and render the prompt named in the config."""
    directory = prompt_dir(CONFIG.prompt_name)
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
    return PromptTemplate(
        sha256=template_sha256(directory), system=system, user_template=user_template, examples=examples,
    )


_ACTIVE = load_prompt()
PROMPT_SHA256 = _ACTIVE.sha256
SYSTEM_PROMPT = _ACTIVE.system
EXAMPLES = _ACTIVE.examples


@dataclass(frozen=True)
class PromptBundle:
    system: str
    user: str
    prompt_id: str
    prompt_sha256: str
    query: str  # the original query; a draft provider may need it (test doubles key on it)

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self.system}, {"role": "user", "content": self.user}]


def build_prompt(query: str) -> PromptBundle:
    template = load_prompt()
    # The user text is fenced by <질의> tags; neutralise those tags if the query itself contains them.
    fenced = query.replace("<질의>", "＜질의＞").replace("</질의>", "＜/질의＞")
    return PromptBundle(
        system=template.system,
        user=template.user_template.replace("{{QUERY}}", fenced),
        prompt_id=PROMPT_ID,
        prompt_sha256=template.sha256,
        query=query,
    )
