"""Validated, dependency-free TrendBatch input contract.

The upstream Trend Agent supplies names, descriptions, and traceable documents.
It does not supply rank or similarity values, so this contract intentionally
has no fields for either. Source URLs are retained as references and are never
fetched by this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping
from urllib.parse import urlparse


MAX_PAYLOAD_BYTES = 1_000_000
MAX_TRENDS = 500
MAX_EVIDENCE_PER_TREND = 100
MAX_NAME_LENGTH = 300
MAX_DESCRIPTION_LENGTH = 12_000
MAX_TITLE_LENGTH = 500
MAX_EXCERPT_LENGTH = 4_000
MAX_LOCATOR_LENGTH = 1_000


class TrendBatchValidationError(ValueError):
    """Raised when an incoming TrendBatch violates the internal contract."""


def _required_text(value: Any, path: str, *, limit: int) -> str:
    if not isinstance(value, str):
        raise TrendBatchValidationError(f"{path} must be a string.")
    clean = value.strip()
    if not clean:
        raise TrendBatchValidationError(f"{path} must not be empty.")
    if len(clean) > limit:
        raise TrendBatchValidationError(f"{path} exceeds {limit} characters.")
    return clean


def _optional_text(value: Any, path: str, *, limit: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, path, limit=limit)


@dataclass(frozen=True)
class EvidenceDocument:
    document_id: str
    title: str
    source_url: str | None = None
    published_at: date | None = None
    excerpt: str | None = None
    locator: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any, path: str = "evidence") -> "EvidenceDocument":
        if not isinstance(raw, Mapping):
            raise TrendBatchValidationError(f"{path} must be an object.")
        allowed = {"document_id", "title", "source_url", "published_at", "excerpt", "locator"}
        extras = set(raw) - allowed
        if extras:
            raise TrendBatchValidationError(f"{path} has unsupported fields: {', '.join(sorted(extras))}.")
        document_id = _required_text(raw.get("document_id"), f"{path}.document_id", limit=300)
        title = _required_text(raw.get("title"), f"{path}.title", limit=MAX_TITLE_LENGTH)
        source_url = _optional_text(raw.get("source_url"), f"{path}.source_url", limit=2_000)
        if source_url:
            parsed = urlparse(source_url)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc:
                raise TrendBatchValidationError(f"{path}.source_url must be an absolute HTTP(S) URL.")
        published_at_raw = raw.get("published_at")
        published_at: date | None = None
        if published_at_raw is not None:
            if not isinstance(published_at_raw, str):
                raise TrendBatchValidationError(f"{path}.published_at must be an ISO date string.")
            try:
                published_at = date.fromisoformat(published_at_raw)
            except ValueError as exc:
                raise TrendBatchValidationError(f"{path}.published_at must be an ISO date string.") from exc
        excerpt = _optional_text(raw.get("excerpt"), f"{path}.excerpt", limit=MAX_EXCERPT_LENGTH)
        locator = _optional_text(raw.get("locator"), f"{path}.locator", limit=MAX_LOCATOR_LENGTH)
        if not source_url and not locator and not excerpt:
            raise TrendBatchValidationError(
                f"{path} must include a source_url, locator, or excerpt so the evidence can be traced."
            )
        return cls(document_id, title, source_url, published_at, excerpt, locator)


@dataclass(frozen=True)
class TrendInput:
    name: str
    description: str
    evidence: tuple[EvidenceDocument, ...]
    trend_id: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any, path: str = "trend") -> "TrendInput":
        if not isinstance(raw, Mapping):
            raise TrendBatchValidationError(f"{path} must be an object.")
        allowed = {"trend_id", "name", "description", "evidence"}
        extras = set(raw) - allowed
        if extras:
            raise TrendBatchValidationError(f"{path} has unsupported fields: {', '.join(sorted(extras))}.")
        trend_id = _optional_text(raw.get("trend_id"), f"{path}.trend_id", limit=300)
        name = _required_text(raw.get("name"), f"{path}.name", limit=MAX_NAME_LENGTH)
        description = _required_text(raw.get("description"), f"{path}.description", limit=MAX_DESCRIPTION_LENGTH)
        raw_evidence = raw.get("evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise TrendBatchValidationError(f"{path}.evidence must be a non-empty array.")
        if len(raw_evidence) > MAX_EVIDENCE_PER_TREND:
            raise TrendBatchValidationError(f"{path}.evidence exceeds {MAX_EVIDENCE_PER_TREND} items.")
        evidence = tuple(EvidenceDocument.from_mapping(item, f"{path}.evidence[{index}]") for index, item in enumerate(raw_evidence))
        ids = [item.document_id for item in evidence]
        if len(set(ids)) != len(ids):
            raise TrendBatchValidationError(f"{path}.evidence contains duplicate document_id values.")
        return cls(name, description, evidence, trend_id)


@dataclass(frozen=True)
class TrendBatch:
    schema_version: str
    batch_id: str
    trends: tuple[TrendInput, ...]
    received_at: datetime | None = None
    target_season: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any) -> "TrendBatch":
        if not isinstance(raw, Mapping):
            raise TrendBatchValidationError("TrendBatch must be a JSON object.")
        allowed = {"schema_version", "batch_id", "received_at", "target_season", "trends"}
        extras = set(raw) - allowed
        if extras:
            raise TrendBatchValidationError(f"TrendBatch has unsupported fields: {', '.join(sorted(extras))}.")
        schema_version = _required_text(raw.get("schema_version"), "schema_version", limit=40)
        if schema_version != "1.0":
            raise TrendBatchValidationError(f"Unsupported schema_version: {schema_version}.")
        batch_id = _required_text(raw.get("batch_id"), "batch_id", limit=300)
        received_at_raw = raw.get("received_at")
        received_at: datetime | None = None
        if received_at_raw is not None:
            if not isinstance(received_at_raw, str):
                raise TrendBatchValidationError("received_at must be an ISO datetime string.")
            try:
                received_at = datetime.fromisoformat(received_at_raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise TrendBatchValidationError("received_at must be an ISO datetime string.") from exc
        target_season = _optional_text(raw.get("target_season"), "target_season", limit=100)
        raw_trends = raw.get("trends")
        if not isinstance(raw_trends, list) or not raw_trends:
            raise TrendBatchValidationError("trends must be a non-empty array.")
        if len(raw_trends) > MAX_TRENDS:
            raise TrendBatchValidationError(f"trends exceeds {MAX_TRENDS} items.")
        trends = tuple(TrendInput.from_mapping(item, f"trends[{index}]") for index, item in enumerate(raw_trends))
        trend_ids = [item.trend_id for item in trends if item.trend_id]
        if len(set(trend_ids)) != len(trend_ids):
            raise TrendBatchValidationError("TrendBatch contains duplicate trend_id values.")
        return cls(schema_version, batch_id, trends, received_at, target_season)

    @classmethod
    def from_json(cls, payload: str | bytes) -> "TrendBatch":
        encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise TrendBatchValidationError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes.")
        try:
            raw = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrendBatchValidationError("payload must be valid UTF-8 JSON.") from exc
        return cls.from_mapping(raw)
