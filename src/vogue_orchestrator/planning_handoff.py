"""Versioned Project_06 handoff input registry and compatibility adapter."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


CONTRACT_VERSION = "vogue-handoff/0.2"
ADAPTER_VERSION = "project08-p06-planning-migration/1.0"
INPUT_KEYS = frozenset({
    "planning_request", "trend_result", "inventory_product_result", "sales_market_result",
})
SUPPORTED_VERSIONS = {
    "planning_request": frozenset({"1.0"}),
    "trend_result": frozenset({"1.1"}),
    "inventory_product_result": frozenset({"1.0"}),
    "sales_market_result": frozenset({"1.0"}),
}
EXPECTED_PRODUCERS = {
    "trend_result": frozenset({"trend-analysis-agent", None}),
    "inventory_product_result": frozenset({"stock-agent"}),
    "sales_market_result": frozenset({"sales-market-agent"}),
}
DOMAIN_STATUSES = frozenset({"ok", "partial", "no_results", "error"})


class AdapterCompatibilityError(ValueError):
    """Safe, machine-readable input compatibility failure."""

    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True)
class NormalizedHandoffInputs:
    request_id: str
    source_inputs: Mapping[str, Any]
    normalized_inputs: Mapping[str, Any]
    provenance: Mapping[str, Any]


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_handoff_inputs(request_id: str, inputs: Mapping[str, Any]) -> NormalizedHandoffInputs:
    """Validate the producer matrix and return independent raw/normalized copies.

    This adapter supports only the reviewed versions in ``SUPPORTED_VERSIONS``.
    It changes Trend's schema marker only on the internal copy required by the
    existing domain context; the producer payload remains unchanged in
    ``source_inputs`` and is fingerprinted for traceability.
    """
    if not isinstance(request_id, str) or not request_id.strip():
        raise AdapterCompatibilityError("INVALID_REQUEST_ID", "request_id는 비어 있지 않은 문자열이어야 합니다.")
    if not isinstance(inputs, Mapping) or set(inputs) != INPUT_KEYS:
        raise AdapterCompatibilityError("INVALID_INPUT", "input에는 합의된 네 producer 입력 필드가 모두 필요합니다.")

    try:
        source_inputs = copy.deepcopy(dict(inputs))
    except Exception as exc:
        raise AdapterCompatibilityError("INVALID_PRODUCER_PAYLOAD", "입력 payload를 보존할 수 없습니다.") from exc
    normalized_inputs = copy.deepcopy(source_inputs)
    schema_trace: dict[str, Any] = {}

    for name, payload in source_inputs.items():
        if name == "sales_market_result" and payload is None:
            schema_trace[name] = {
                "producer_id": None, "source_schema_version": None,
                "normalized_schema_version": None, "internal_schema_version": None,
                "adapter_version": ADAPTER_VERSION, "transformed": False,
                "source_payload_sha256": _fingerprint(None), "status": "not_provided",
            }
            continue
        if not isinstance(payload, dict):
            raise AdapterCompatibilityError("INVALID_PRODUCER_PAYLOAD", f"{name}는 JSON 객체여야 합니다.")
        if payload.get("request_id") != request_id:
            raise AdapterCompatibilityError("REQUEST_ID_MISMATCH", "모든 입력 request_id는 envelope request_id와 일치해야 합니다.")
        source_version = payload.get("schema_version")
        if not isinstance(source_version, str) or source_version not in SUPPORTED_VERSIONS[name]:
            raise AdapterCompatibilityError("UNSUPPORTED_PRODUCER_SCHEMA", f"지원하지 않는 {name} schema_version입니다.")
        producer_id = payload.get("agent_id")
        if name in EXPECTED_PRODUCERS and (
            (producer_id is not None and not isinstance(producer_id, str))
            or producer_id not in EXPECTED_PRODUCERS[name]
        ):
            raise AdapterCompatibilityError("PRODUCER_MISMATCH", f"{name} producer ID가 지원 목록과 일치하지 않습니다.")
        status = payload.get("status")
        if name != "planning_request" and (not isinstance(status, str) or status not in DOMAIN_STATUSES):
            raise AdapterCompatibilityError("INVALID_DOMAIN_STATUS", f"{name} status가 지원되지 않습니다.")
        schema_trace[name] = {
            "producer_id": producer_id,
            "source_schema_version": source_version,
            "normalized_schema_version": source_version,
            "internal_schema_version": source_version,
            "adapter_version": ADAPTER_VERSION,
            "transformed": False,
            "source_payload_sha256": _fingerprint(payload),
        }

    planning = source_inputs["planning_request"]
    if not isinstance(planning, dict) or planning.get("request_id") != request_id:
        raise AdapterCompatibilityError("REQUEST_ID_MISMATCH", "PlanningRequest request_id가 envelope와 일치해야 합니다.")

    trend = normalized_inputs["trend_result"]
    trend["schema_version"] = "1.0"
    schema_trace["trend_result"]["normalized_schema_version"] = "1.0"
    schema_trace["trend_result"]["internal_schema_version"] = "1.0"
    schema_trace["trend_result"]["transformed"] = True

    return NormalizedHandoffInputs(
        request_id=request_id,
        source_inputs=source_inputs,
        normalized_inputs=normalized_inputs,
        provenance={
            "adapter_version": ADAPTER_VERSION,
            "source_payloads_preserved": True,
            "input_schemas": schema_trace,
        },
    )
