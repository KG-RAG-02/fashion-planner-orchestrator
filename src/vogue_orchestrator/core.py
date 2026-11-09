"""Transport-neutral orchestration and handoff validation."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping


class StageName(StrEnum):
    TREND = "trend"
    STOCK = "stock"
    PROJECT_07 = "project_07"
    PROJECT_06 = "project_06"


VALID_STATUSES = {"ok", "partial", "no_results", "error"}
TREND_STATUSES = {"ok", "no_results", "error"}
CONTRACT_VERSION = "vogue-handoff/0.1-proposal"
P07_INPUT_SCHEMA_VERSION = "1.0"


class ContractError(ValueError):
    """A producer response violates the minimum orchestration boundary."""


@dataclass(frozen=True)
class HandoffEnvelope:
    """Proposal wrapper that keeps producer schema version distinct."""

    request_id: str
    agent_id: str
    schema_version: str
    status: str
    result: Mapping[str, Any]
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "producer": {"agent_id": self.agent_id, "schema_version": self.schema_version},
            "status": self.status,
            "result": self.result,
        }


@dataclass(frozen=True)
class StageRecord:
    stage: StageName
    status: str
    request_id: str
    producer_id: str | None = None
    producer_schema_version: str | None = None
    raw_response: Any = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class OrchestrationResult:
    request_id: str
    stages: list[StageRecord] = field(default_factory=list)
    stopped_at: StageName | None = None

    @property
    def status(self) -> str:
        if self.stopped_at:
            return "error"
        statuses = [record.status for record in self.stages]
        if "partial" in statuses or "no_results" in statuses:
            return "partial"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "stopped_at": self.stopped_at.value if self.stopped_at else None,
            "stages": [
                {
                    "stage": r.stage.value, "status": r.status, "request_id": r.request_id,
                    "producer_id": r.producer_id,
                    "producer_schema_version": r.producer_schema_version,
                    "raw_response": r.raw_response,
                    "error_code": r.error_code, "error_message": r.error_message,
                }
                for r in self.stages
            ],
        }


Adapter = Callable[[dict[str, Any]], Mapping[str, Any]]


def _json_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def idempotency_key(request_id: str, stage: StageName | str, input_payload: Any) -> str:
    """Candidate key only; this function does not persist or enforce deduplication."""
    stage_name = stage.value if isinstance(stage, StageName) else str(stage)
    return f"{request_id}:{stage_name}:{_json_fingerprint(input_payload)}"


def unwrap_p07_for_project06(value: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    """Return P07 body for the current P06 prototype, validating a proposal envelope if present."""
    if "result" not in value:
        _validate_response(value, request_id, expected_agent="market-research-agent")
        return dict(value)

    if value.get("contract_version") != CONTRACT_VERSION:
        raise ContractError("unsupported handoff contract_version")
    producer = value.get("producer")
    result = value.get("result")
    if not isinstance(producer, Mapping) or not isinstance(result, Mapping):
        raise ContractError("proposal envelope requires producer and object result")
    if value.get("request_id") != request_id or result.get("request_id") != request_id:
        raise ContractError("P07 envelope/result request_id mismatch")
    if producer.get("agent_id") != result.get("agent_id"):
        raise ContractError("P07 envelope/result producer mismatch")
    if producer.get("schema_version") != result.get("schema_version"):
        raise ContractError("P07 envelope/result schema_version mismatch")
    if value.get("status") != result.get("status"):
        raise ContractError("P07 envelope/result status mismatch")
    _validate_response(result, request_id, expected_agent="market-research-agent")
    return dict(result)


def _validate_response(
    response: Mapping[str, Any], request_id: str, *, expected_agent: str | None = None,
) -> tuple[str, str, str]:
    if not isinstance(response, Mapping):
        raise ContractError("stage response must be an object")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ContractError("request_id must be non-empty")
    response_id = response.get("request_id")
    if response_id != request_id:
        raise ContractError("response request_id mismatch")
    agent_id = response.get("agent_id")
    version = response.get("schema_version")
    status = response.get("status")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ContractError("response agent_id is required")
    if expected_agent and agent_id != expected_agent:
        raise ContractError(f"unexpected producer agent_id: {agent_id}")
    if not isinstance(version, str) or not version.strip():
        raise ContractError("producer schema_version is required")
    if status not in VALID_STATUSES:
        raise ContractError("unsupported or missing response status")
    return agent_id, version, status


def _validate_stage_response(
    response: Mapping[str, Any], request_id: str, *, expected_agent: str,
) -> tuple[str, str, str]:
    """Accept either producer raw response or the optional handoff proposal wrapper."""
    if isinstance(response, Mapping) and "contract_version" in response:
        if response.get("contract_version") != CONTRACT_VERSION:
            raise ContractError("unsupported handoff contract_version")
        producer, body = response.get("producer"), response.get("result")
        if not isinstance(producer, Mapping) or not isinstance(body, Mapping):
            raise ContractError("handoff envelope requires producer and object result")
        outer_id = response.get("request_id")
        if outer_id != request_id or body.get("request_id") != request_id:
            raise ContractError("handoff envelope/result request_id mismatch")
        agent_id, version, status = _validate_response(body, request_id, expected_agent=expected_agent)
        if producer.get("agent_id") != agent_id or producer.get("schema_version") != version:
            raise ContractError("handoff producer metadata mismatch")
        if response.get("status") != status:
            raise ContractError("handoff status mismatch")
        return agent_id, version, status
    return _validate_response(response, request_id, expected_agent=expected_agent)


def project_trend_result_for_p07(trend_result: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    """Make the P07 CLI's local 1.0 input projection without mutating trend raw output.

    Trend Analysis Agent currently emits no schema_version. The added version labels
    this P07 ingress projection only; StageRecord retains the producer response as-is.
    """
    if trend_result.get("request_id") != request_id:
        raise ContractError("trend_result request_id mismatch")
    version = trend_result.get("schema_version")
    if version is not None and version != P07_INPUT_SCHEMA_VERSION:
        raise ContractError("P07 currently requires trend_result schema_version 1.0")
    return {**trend_result, "schema_version": P07_INPUT_SCHEMA_VERSION}


class Orchestrator:
    """Runs injected producer adapters sequentially; no transport or retry policy is chosen."""

    def __init__(
        self,
        *,
        trend: Adapter,
        stock: Adapter,
        project_07: Adapter,
        project_06: Adapter,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.adapters = {
            StageName.TREND: trend,
            StageName.STOCK: stock,
            StageName.PROJECT_07: project_07,
            StageName.PROJECT_06: project_06,
        }
        self.request_id_factory = request_id_factory or (lambda: str(uuid.uuid4()))

    def run(self, query: str, *, request_id: str | None = None) -> OrchestrationResult:
        rid = request_id or self.request_id_factory()
        if not isinstance(rid, str) or not rid.strip():
            raise ValueError("request_id must be non-empty")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        result = OrchestrationResult(request_id=rid)
        shared: dict[str, Any] = {"request_id": rid, "query": query}
        stage_inputs: dict[StageName, dict[str, Any]] = {
            StageName.TREND: {"request_id": rid, "query": query},
        }

        for stage in (StageName.TREND, StageName.STOCK, StageName.PROJECT_07, StageName.PROJECT_06):
            raw: Any = None
            try:
                if stage == StageName.STOCK:
                    payload = shared.get("trend_result")
                elif stage == StageName.PROJECT_07:
                    trend_projection = project_trend_result_for_p07(shared["trend_result"], rid)
                    payload = {"request_id": rid, "planning_request": shared["planning_request"],
                               "trend_result": trend_projection,
                               "inventory_product_result": shared.get("inventory_product_result")}
                elif stage == StageName.PROJECT_06:
                    p07_raw = shared.get("sales_market_result")
                    p07_body = unwrap_p07_for_project06(p07_raw, rid) if p07_raw is not None else None
                    planning_request = shared.get("planning_request")
                    trend_result = shared.get("trend_result")
                    inventory_result = shared.get("inventory_product_result")
                    if not all(isinstance(v, Mapping) for v in (planning_request, trend_result, inventory_result)):
                        raise ContractError("Project_06 requires request, trend, and inventory objects.")
                    if any(v.get("request_id") != rid for v in (planning_request, trend_result, inventory_result)):
                        raise ContractError("Project_06 input request_id mismatch.")
                    request_version = planning_request.get("schema_version")
                    inventory_version = inventory_result.get("schema_version")
                    trend_version = trend_result.get("schema_version")
                    if (not isinstance(request_version, str) or not request_version.strip()
                            or inventory_version != request_version
                            or (trend_version is not None and trend_version != request_version)):
                        raise ContractError("Project_06 prototype requires matching input schema_version values.")
                    payload = {
                        "request_id": rid,
                        "planning_request": planning_request,
                        "trend_result": trend_result,
                        "inventory_product_result": inventory_result,
                        "sales_market_result": p07_body,
                    }
                else:
                    payload = stage_inputs[stage]

                raw = self.adapters[stage](payload)
                if stage == StageName.PROJECT_06:
                    if not isinstance(raw, Mapping) or raw.get("request_id") != rid:
                        raise ContractError("Project_06 response request_id mismatch")
                    status = raw.get("status")
                    if status not in VALID_STATUSES:
                        raise ContractError("Project_06 response has unsupported status")
                    agent_id, version = "project-06-planning-draft-api", None
                elif stage == StageName.TREND:
                    if not isinstance(raw, Mapping) or raw.get("request_id") != rid:
                        raise ContractError("TrendAnalysisResult request_id mismatch")
                    status = raw.get("status")
                    if status not in TREND_STATUSES:
                        raise ContractError("TrendAnalysisResult has unsupported status")
                    agent_id, version = "trend-analysis-agent", None
                else:
                    expected_agent = "stock-agent" if stage == StageName.STOCK else "market-research-agent"
                    agent_id, version, status = _validate_stage_response(
                        raw, rid, expected_agent=expected_agent,
                    )
                if stage == StageName.TREND:
                    planning_request = {"schema_version": P07_INPUT_SCHEMA_VERSION,
                                        "request_id": rid, "query": query}
                record = StageRecord(stage, status, rid, agent_id, version, raw_response=raw)
                result.stages.append(record)
                if status == "error":
                    result.stopped_at = stage
                    break
                if stage == StageName.TREND:
                    shared["trend_result"] = raw
                    shared["planning_request"] = planning_request
                elif stage == StageName.STOCK:
                    shared["inventory_product_result"] = raw
                elif stage == StageName.PROJECT_07:
                    shared["sales_market_result"] = raw
            except Exception as exc:
                result.stages.append(StageRecord(
                    stage=stage, status="error", request_id=rid,
                    raw_response=raw, error_code=("CONTRACT_ERROR" if isinstance(exc, ContractError) else "STAGE_FAILURE"),
                    error_message=str(exc) if isinstance(exc, ContractError) else "Stage adapter failed; details suppressed.",
                ))
                result.stopped_at = stage
                break
        return result
