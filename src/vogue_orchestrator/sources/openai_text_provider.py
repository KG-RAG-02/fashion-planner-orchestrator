"""OpenAI Responses API adapter for evidence-scoped text planning."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any


class OpenAITextProviderError(RuntimeError):
    """Stable, non-sensitive provider failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "kind": {"type": "string", "enum": [
            "user_requested", "evidence_observation", "proposal", "hypothesis", "unknown",
        ]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["value", "kind", "evidence_ids"],
    "additionalProperties": False,
}

PLANNING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "request_id": {"type": "string"},
        "opportunity": _CLAIM_SCHEMA,
        "target_customer": _CLAIM_SCHEMA,
        "product_definition": _CLAIM_SCHEMA,
        "attributes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"anyOf": [
                        {"type": "string"}, {"type": "number"},
                        {"type": "boolean"}, {"type": "null"},
                    ]},
                    "kind": _CLAIM_SCHEMA["properties"]["kind"],
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "value", "kind", "evidence_ids"],
                "additionalProperties": False,
            },
        },
        "relation_to_own_products": {"type": "array", "items": _CLAIM_SCHEMA},
        "differentiation": {"type": "array", "items": _CLAIM_SCHEMA},
        "risks": {"type": "array", "items": _CLAIM_SCHEMA},
        "questions_for_validation": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "request_id", "opportunity", "target_customer", "product_definition", "attributes",
        "relation_to_own_products", "differentiation", "risks", "questions_for_validation", "limitations",
    ],
    "additionalProperties": False,
}
MAX_PROVIDER_RESPONSE_BYTES = 1_000_000


class OpenAIResponsesTextProvider:
    """Minimal Responses API client: no tools, prompt logging, or stored response."""

    def __init__(self, api_key: str, *, model: str | None = None, timeout_seconds: int | None = None):
        self.api_key = api_key.strip()
        self.model_name = (model or os.getenv("VOGUE_TEXT_GENERATION_MODEL", "gpt-6-astra")).strip()
        if timeout_seconds is None:
            try:
                timeout_seconds = int(os.getenv("VOGUE_TEXT_GENERATION_TIMEOUT_SECONDS", "60"))
            except ValueError:
                timeout_seconds = 60
        self.timeout_seconds = max(5, min(timeout_seconds, 120))

    @staticmethod
    def _max_output_tokens() -> int:
        try:
            configured = int(os.getenv("VOGUE_TEXT_GENERATION_MAX_OUTPUT_TOKENS", "3000"))
        except ValueError:
            configured = 3000
        return max(256, min(configured, 8000))

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key:
            raise OpenAITextProviderError("TEXT_API_KEY_MISSING")
        if not self.model_name:
            raise OpenAITextProviderError("TEXT_MODEL_MISSING")
        body = json.dumps({
            "model": self.model_name,
            "instructions": system_prompt,
            "input": user_prompt,
            "max_output_tokens": self._max_output_tokens(),
            "store": False,
            "tools": [],
            "parallel_tool_calls": False,
            "text": {"format": {
                "type": "json_schema",
                "name": "product_planning_draft",
                "strict": True,
                "schema": PLANNING_OUTPUT_SCHEMA,
            }},
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_bytes = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
                if len(response_bytes) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise OpenAITextProviderError("TEXT_API_RESPONSE_TOO_LARGE")
                payload = json.loads(response_bytes.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OpenAITextProviderError(f"TEXT_API_HTTP_{exc.code}") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise OpenAITextProviderError("TEXT_API_TIMEOUT") from None
            if isinstance(exc.reason, ssl.SSLError):
                raise OpenAITextProviderError("TEXT_API_TLS_FAILURE") from None
            raise OpenAITextProviderError("TEXT_API_UNAVAILABLE") from None
        except TimeoutError:
            raise OpenAITextProviderError("TEXT_API_TIMEOUT") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OpenAITextProviderError("TEXT_API_INVALID_RESPONSE") from None

        if not isinstance(payload, dict) or payload.get("status") != "completed":
            raise OpenAITextProviderError("TEXT_API_NOT_COMPLETED")
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text
        output = payload.get("output")
        if isinstance(output, list):
            chunks = []
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "refusal":
                        raise OpenAITextProviderError("TEXT_API_REFUSED")
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        chunks.append(part["text"])
            if chunks:
                return "".join(chunks)
        raise OpenAITextProviderError("TEXT_API_OUTPUT_MISSING")
