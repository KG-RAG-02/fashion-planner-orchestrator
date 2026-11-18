"""Real LLM call for query structuring (opt-in; sends the user's query to an external provider).

This module supplies the configured LLM provider used by query structuring: it takes the
rendered prompt (`PromptBundle`: `.system`, `.user`) and returns the model's text, which the query
module parses and validates. It lives outside `query/` on purpose: the query package itself stays
offline (tests/test_query_isolation.py).

Settings
  - `LLM_MODEL`, `LLM_API_KEY` (required) and `LLM_PROVIDER` (optional: anthropic | openai) come from the
    process environment or the repository `.env` file; the environment wins. The key is never logged,
    printed or put in an error message.
  - Without `LLM_PROVIDER`, a model name starting with "claude" uses Anthropic, anything else OpenAI.
  - Timeout, output limit, retries and temperature: `configs/llm.json` (temperature 0,
    structuring is extraction, so the output should not vary between runs; null = omit
    the parameter and use the provider default).

SDKs are imported only when a call is made (`pip install anthropic` or `pip install openai`), so the
orchestrator core keeps working without them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "llm.json"
PROVIDERS = ("anthropic", "openai")


class LLMConfigurationError(RuntimeError):
    """Settings or SDK are missing. Messages never contain the API key."""


class LLMCallError(RuntimeError):
    """The model answered but not with usable text (refusal, cut off, empty)."""


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 60.0
    max_tokens: int = 16000
    max_retries: int = 2
    temperature: float | None = None


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse a simple `.env` file (KEY=VALUE, `#` comments, optional `export` and quotes)."""
    path = path or DEFAULT_ENV_PATH
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:  # inline comment after an unquoted value
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def infer_provider(model: str) -> str:
    return "anthropic" if model.strip().lower().startswith("claude") else "openai"


def _read_config(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LLMConfigurationError(f"LLM settings file is missing or not JSON: {path}") from exc
    out: dict[str, Any] = {}
    for key, kind in (("timeout_seconds", (int, float)), ("max_tokens", int), ("max_retries", int)):
        value = raw.get(key)
        if not isinstance(value, kind) or isinstance(value, bool) or value < 0:
            raise LLMConfigurationError(f"{path.name}: {key} must be a non-negative number")
        out[key] = value
    temperature = raw.get("temperature")
    if temperature is not None and (not isinstance(temperature, (int, float)) or isinstance(temperature, bool)
                                    or not 0 <= temperature <= 1):
        raise LLMConfigurationError(f"{path.name}: temperature must be null or a number from 0 to 1")
    out["temperature"] = temperature
    return out


def load_settings(
    environ: Mapping[str, str] | None = None,
    *,
    env_path: Path | None = None,
    config_path: Path | None = None,
) -> LLMSettings:
    """Read settings; raise `LLMConfigurationError` if the model or key is missing."""
    env = environ if environ is not None else os.environ
    values = {**read_env_file(env_path), **{k: v for k, v in env.items() if k.startswith("LLM_")}}
    model = (values.get("LLM_MODEL") or "").strip()
    api_key = (values.get("LLM_API_KEY") or "").strip()
    if not model or not api_key:
        raise LLMConfigurationError("LLM_MODEL and LLM_API_KEY must be set (environment or .env)")
    provider = (values.get("LLM_PROVIDER") or "").strip().lower() or infer_provider(model)
    if provider not in PROVIDERS:
        raise LLMConfigurationError(f"LLM_PROVIDER must be one of {PROVIDERS}")
    return LLMSettings(provider=provider, model=model, api_key=api_key,
                       **_read_config(config_path or DEFAULT_CONFIG_PATH))


def llm_status(environ: Mapping[str, str] | None = None, *, env_path: Path | None = None) -> dict[str, Any]:
    """What the UI may show before a call: provider and model name only, never the key."""
    try:
        settings = load_settings(environ, env_path=env_path)
    except LLMConfigurationError as exc:
        return {"configured": False, "provider": None, "model": None, "detail": str(exc)}
    return {"configured": True, "provider": settings.provider, "model": settings.model, "detail": None}


# -- provider calls -------------------------------------------------------------------------------

def _anthropic_client(settings: LLMSettings) -> Any:
    try:
        import anthropic
    except ImportError:
        raise LLMConfigurationError("the anthropic SDK is not installed (pip install anthropic)") from None
    return anthropic.Anthropic(api_key=settings.api_key, timeout=settings.timeout_seconds,
                               max_retries=settings.max_retries)


def _openai_client(settings: LLMSettings) -> Any:
    try:
        import openai
    except ImportError:
        raise LLMConfigurationError("the openai SDK is not installed (pip install openai)") from None
    return openai.OpenAI(api_key=settings.api_key, timeout=settings.timeout_seconds,
                         max_retries=settings.max_retries)


def _sampling(settings: LLMSettings) -> dict[str, Any]:
    return {} if settings.temperature is None else {"temperature": settings.temperature}


def _call_anthropic(client: Any, settings: LLMSettings, system: str, user: str) -> str:
    response = client.messages.create(
        model=settings.model,
        max_tokens=settings.max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        **_sampling(settings),
    )
    if response.stop_reason == "refusal":
        raise LLMCallError("the model declined the request")
    if response.stop_reason == "max_tokens":
        raise LLMCallError("the model output was cut off (configs/llm.json max_tokens)")
    text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    if not text.strip():
        raise LLMCallError("the model returned no text")
    return text


def _call_openai(client: Any, settings: LLMSettings, system: str, user: str) -> str:
    sampling = _sampling(settings)
    # GPT-6 reasoning models reject sampling overrides unless reasoning is disabled.
    # Query structuring is a constrained extraction call, so use the supported
    # no-reasoning mode when a deterministic temperature is configured.
    if settings.model.strip().lower().startswith("gpt-6") and sampling:
        sampling["reasoning_effort"] = "none"
    response = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},  # the prompt asks for one JSON object
        **sampling,
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise LLMCallError("the model output was cut off")
    text = choice.message.content or ""
    if not text.strip():
        raise LLMCallError("the model returned no text")
    return text


_CLIENTS: dict[str, Callable[[LLMSettings], Any]] = {"anthropic": _anthropic_client, "openai": _openai_client}
_CALLS: dict[str, Callable[[Any, LLMSettings, str, str], str]] = {"anthropic": _call_anthropic,
                                                                  "openai": _call_openai}


def build_text_call(settings: LLMSettings | None = None, *, client: Any = None) -> Callable[[str, str], str]:
    """Return ``call(system, user) -> text`` on the configured model (the same settings as query structuring).
    Used by the planning-sheet SWOT (swot.py). Configuration problems raise here, before any request is sent."""
    settings = settings or load_settings()
    client = client if client is not None else _CLIENTS[settings.provider](settings)
    call = _CALLS[settings.provider]

    def text_call(system: str, user: str) -> str:
        return call(client, settings, system, user)

    text_call.model = settings.model  # type: ignore[attr-defined]
    return text_call


def build_draft_provider(settings: LLMSettings | None = None, *, client: Any = None) -> Callable[[Any], str]:
    """Return a `DraftProvider` for `structure_query`. `client` lets tests inject a fake SDK client.

    Configuration problems (no key, no SDK) raise here, before any request is sent.
    """
    settings = settings or load_settings()
    client = client if client is not None else _CLIENTS[settings.provider](settings)
    call = _CALLS[settings.provider]

    def provider(bundle: Any) -> str:
        return call(client, settings, bundle.system, bundle.user)

    return provider
