"""Transport-neutral orchestration and handoff validation."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
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
TREND_STATUSES = {"ok", "partial", "no_results", "error"}
CONTRACT_VERSION = "vogue-handoff/0.1"
PROJECT06_CONTRACT_VERSION = "vogue-handoff/0.2"
P07_INPUT_SCHEMA_VERSION = "1.0"
ROUTE_PRODUCERS = {
    StageName.TREND: ("trend-analysis-agent", "trend-analysis-agent"),
    StageName.STOCK: ("stock-agent", "stock-agent"),
    StageName.PROJECT_07: ("sales-market-agent", "sales-market-agent"),
    StageName.PROJECT_06: ("new-product-agent", "new-product-agent"),
}
SUPPORTED_PRODUCER_VERSIONS = {
    StageName.TREND: "1.1",
    StageName.STOCK: "1.0",
    StageName.PROJECT_07: "1.0",
}


class ContractError(ValueError):
    """A producer response violates the minimum orchestration boundary."""


class TransportError(RuntimeError):
    """An agent request failed before a valid producer result was received."""


@dataclass(frozen=True)
class HandoffEnvelope:
    """HTTP v0.1 wrapper that keeps producer schema version distinct."""

    request_id: str
    agent_id: str
    schema_version: str
    status: str
    result: Mapping[str, Any]
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        normalized_result = _normalize_no_results(self.result)
        normalized_status = "partial" if (
            self.status == "no_results"
            or (isinstance(self.result, Mapping) and self.result.get("status") == "no_results")
        ) else self.status
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "producer": {"agent_id": self.agent_id, "schema_version": self.schema_version},
            "status": normalized_status,
            "result": normalized_result,
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
        if "error" in statuses:
            return "error"
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
                    "stage": r.stage.value, "status": "partial" if r.status == "no_results" else r.status, "request_id": r.request_id,
                    "producer_id": r.producer_id,
                    "producer_schema_version": r.producer_schema_version,
                    "raw_response": (r.raw_response if isinstance(r.raw_response, Mapping)
                                     and "contract_version" in r.raw_response
                                     else _normalize_no_results(r.raw_response)),
                    "error_code": r.error_code, "error_message": r.error_message,
                }
                for r in self.stages
            ],
        }


Adapter = Callable[[dict[str, Any]], Mapping[str, Any]]


def _normalize_no_results(value: Any) -> Any:
    """Keep empty producer outcomes non-terminal and never expose no_results."""
    if isinstance(value, Mapping):
        normalized = {key: _normalize_no_results(item) for key, item in value.items()}
        if normalized.get("status") == "no_results":
            normalized["status"] = "partial"
            limitations = normalized.get("limitations")
            if isinstance(limitations, list):
                note = "No usable results were returned for this scope; this partial status does not imply a zero value."
                if note not in limitations:
                    normalized["limitations"] = [*limitations, note]
        return normalized
    if isinstance(value, list):
        return [_normalize_no_results(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_no_results(item) for item in value)
    return deepcopy(value)


def _json_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def idempotency_key(request_id: str, stage: StageName | str, input_payload: Any) -> str:
    """Candidate key only; this function does not persist or enforce deduplication."""
    stage_name = stage.value if isinstance(stage, StageName) else str(stage)
    return f"{request_id}:{stage_name}:{_json_fingerprint(input_payload)}"


def unwrap_p07_for_project06(value: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    """Return P07 body for the current P06 prototype, validating a proposal envelope if present."""
    value = _normalize_no_results(value)
    if "result" not in value:
        _validate_response(value, request_id, expected_agent="sales-market-agent")
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
    _validate_response(result, request_id, expected_agent="sales-market-agent")
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
    if not isinstance(status, str) or status not in VALID_STATUSES:
        raise ContractError("unsupported or missing response status")
    return agent_id, version, status


def _validate_stage_response(
    response: Mapping[str, Any], request_id: str, *, expected_agent: str,
) -> tuple[str, str, str]:
    """Validate an unwrapped legacy producer response."""
    return _validate_response(response, request_id, expected_agent=expected_agent)


def _unpack_handoff_response(
    response: Mapping[str, Any], request_id: str, *, expected_agent: str,
    expected_contract_version: str = CONTRACT_VERSION,
) -> tuple[Mapping[str, Any], str, str, str]:
    """Validate a proposal HTTP envelope and expose its unchanged result body."""
    if response.get("contract_version") != expected_contract_version:
        raise ContractError("unsupported handoff contract_version")
    producer = response.get("producer")
    body = response.get("result")
    if not isinstance(producer, Mapping) or not isinstance(body, Mapping):
        raise ContractError("handoff envelope requires producer and object result")
    if response.get("request_id") != request_id or body.get("request_id") != request_id:
        raise ContractError("handoff envelope/result request_id mismatch")
    agent_id = producer.get("agent_id")
    version = producer.get("schema_version")
    status = response.get("status")
    if agent_id != expected_agent:
        raise ContractError("unexpected handoff producer agent_id")
    if not isinstance(version, str) or not version.strip():
        raise ContractError("handoff producer schema_version is required")
    if not isinstance(status, str) or status not in VALID_STATUSES:
        raise ContractError("handoff envelope has unsupported status")
    if body.get("status") != status:
        raise ContractError("handoff envelope/result status mismatch")
    for field, envelope_value in (("agent_id", agent_id), ("schema_version", version), ("status", status)):
        body_value = body.get(field)
        if body_value is not None and body_value != envelope_value:
            raise ContractError(f"handoff envelope/result {field} mismatch")
    return body, agent_id, version, status


def prepare_trend_result_for_p07(trend_result: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    """Validate and copy TrendAnalysisResult for the in-process P07 adapter unchanged.

    The P07 adapter calls MarketResearchAgent directly, not its CLI. Preserve the
    producer's actual schema version instead of projecting it to the CLI's 1.0.
    """
    if trend_result.get("request_id") != request_id:
        raise ContractError("trend_result request_id mismatch")
    version = trend_result.get("schema_version")
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise ContractError("trend_result schema_version must be non-empty when present")
    return dict(trend_result)


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
        require_envelopes: bool = False,
        project06_version_policy: str = "legacy_equal",
    ) -> None:
        self.adapters = {
            StageName.TREND: trend,
            StageName.STOCK: stock,
            StageName.PROJECT_07: project_07,
            StageName.PROJECT_06: project_06,
        }
        self.request_id_factory = request_id_factory or (lambda: str(uuid.uuid4()))
        self.require_envelopes = require_envelopes
        self.project06_version_policy = project06_version_policy

    def run(
        self, query: str, *, request_id: str | None = None,
        scope: Mapping[str, Any] | None = None,
        trend_query: Mapping[str, Any] | None = None,
    ) -> OrchestrationResult:
        rid = request_id if request_id is not None else self.request_id_factory()
        if not isinstance(rid, str) or not rid.strip():
            raise ValueError("request_id must be non-empty")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        result = OrchestrationResult(request_id=rid)
        normalized_scope = dict(scope) if isinstance(scope, Mapping) else {}
        if trend_query is not None:
            if not isinstance(trend_query, Mapping):
                raise ValueError("trend_query must be an object")
            if trend_query.get("request_id") != rid:
                raise ValueError("trend_query.request_id must match request_id")
            if trend_query.get("original_query") != query:
                raise ValueError("trend_query.original_query must match the submitted query")
        elif self.require_envelopes:
            result.stopped_at = StageName.TREND
            result.stages.append(StageRecord(
                StageName.TREND, "error", rid, error_code="TREND_QUERY_REQUIRED",
                error_message="Structured TrendQuery is required; no query structuring provider is configured.",
            ))
            return result
        shared: dict[str, Any] = {"request_id": rid, "query": query, "scope": normalized_scope}
        trend_payload = dict(trend_query) if trend_query is not None else {
            "request_id": rid, "query": query, "scope": normalized_scope,
        }

        # The canonical HTTP pipeline ends after upstream evidence. P08 then
        # builds the migrated PlanningDraft and requests one P07 review.
        # In-process compatibility mode retains its historical adapters.
        stage_order = ((StageName.TREND, StageName.STOCK)
                       if self.require_envelopes else
                       (StageName.TREND, StageName.STOCK, StageName.PROJECT_07, StageName.PROJECT_06))
        for stage in stage_order:
            raw: Any = None
            try:
                if stage == StageName.STOCK:
                    payload = shared.get("trend_result")
                elif stage == StageName.PROJECT_07:
                    p07_trend_input = prepare_trend_result_for_p07(shared["trend_result"], rid)
                    payload = {"request_id": rid, "planning_request": shared["planning_request"],
                               "trend_result": p07_trend_input,
                               "inventory_product_result": shared.get("inventory_product_result")}
                elif stage == StageName.PROJECT_06:
                    p07_raw = shared.get("sales_market_result")
                    p07_body = (unwrap_p07_for_project06(p07_raw, rid)
                                if p07_raw is not None and not self.require_envelopes else None)
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
                    if self.project06_version_policy == "legacy_equal":
                        compatible = (isinstance(request_version, str) and bool(request_version.strip())
                                      and inventory_version == request_version
                                      and (trend_version is None or trend_version == request_version))
                    elif self.project06_version_policy == "v01_supported_sources":
                        compatible = (request_version == "1.0" and inventory_version == "1.0"
                                      and trend_version == "1.1")
                    else:
                        raise ContractError("unknown Project_06 producer-version policy")
                    if not compatible:
                        raise ContractError("Project_06 input producer schema versions are incompatible.")
                    # Project_06 needs explicit season/category scope before it
                    # can generate a review image. Keep the Sales Market request
                    # unchanged; enrich only this consumer-specific copy, and
                    # only with values the user explicitly supplied in the
                    # validated TrendQuery. Producer result payloads stay raw.
                    p06_planning_request = dict(planning_request)
                    p06_context = dict(planning_request.get("context", {}))
                    trend_terms = trend_payload.get("trend_terms", {})
                    requested_season = trend_terms.get("season", {}) if isinstance(trend_terms, Mapping) else {}
                    if (isinstance(requested_season, Mapping)
                            and requested_season.get("origin") == "user_requested"):
                        year = requested_season.get("year")
                        season_code = requested_season.get("season")
                        if (isinstance(year, int) and not isinstance(year, bool)
                                and season_code in {"SS", "FW"}):
                            # Project_06 inventory values use the four-digit
                            # season form (for example, 2027SS). This scoped
                            # adapter value comes from explicit user input;
                            # the original query and Trend payload stay raw.
                            p06_context["season"] = {
                                "value": f"{year}{season_code}", "source": "user",
                            }
                    requested = trend_payload.get("requested", {})
                    requested = requested if isinstance(requested, Mapping) else {}
                    categories = requested.get("categories", {})
                    categories = categories if isinstance(categories, Mapping) else {}
                    requested_categories = categories.get("include", [])
                    if isinstance(requested_categories, list) and len(requested_categories) == 1:
                        category = requested_categories[0]
                        if isinstance(category, Mapping):
                            category_value = category.get("minor") or category.get("major")
                            if isinstance(category_value, str) and category_value.strip():
                                p06_context["category"] = {
                                    "value": category_value, "source": "user",
                                }
                    if p06_context:
                        p06_planning_request["context"] = p06_context
                    payload = {
                        "request_id": rid,
                        "planning_request": p06_planning_request,
                        "trend_result": trend_result,
                        "inventory_product_result": inventory_result,
                        "sales_market_result": p07_body,
                    }
                else:
                    payload = trend_payload

                raw = self.adapters[stage](payload)
                # Keep canonical envelopes byte-for-byte equivalent at the JSON
                # object level so their status/result consistency remains valid.
                # Legacy producer bodies use the established no_results policy.
                if not (isinstance(raw, Mapping) and "contract_version" in raw):
                    raw = _normalize_no_results(raw)
                response_body = raw
                envelope_metadata = None
                if self.require_envelopes and not (isinstance(raw, Mapping) and "contract_version" in raw):
                    raise ContractError("HTTP v0.1 response envelope is required")
                if isinstance(raw, Mapping) and "contract_version" in raw:
                    _, expected_agent = ROUTE_PRODUCERS[stage]
                    expected_contract = (raw.get("contract_version")
                                         if stage == StageName.PROJECT_06
                                         and raw.get("contract_version") in {CONTRACT_VERSION, PROJECT06_CONTRACT_VERSION}
                                         else PROJECT06_CONTRACT_VERSION if stage == StageName.PROJECT_06
                                         else CONTRACT_VERSION)
                    response_body, envelope_agent, envelope_version, envelope_status = _unpack_handoff_response(
                        raw, rid, expected_agent=expected_agent,
                        expected_contract_version=expected_contract,
                    )
                    result_version = response_body.get("schema_version")
                    if (stage in SUPPORTED_PRODUCER_VERSIONS
                            and envelope_version != SUPPORTED_PRODUCER_VERSIONS[stage]):
                        raise ContractError("unsupported producer schema_version")
                    if result_version is not None and result_version != envelope_version:
                        raise ContractError("producer envelope/result schema_version mismatch")
                    envelope_metadata = (envelope_agent, envelope_version, envelope_status)
                if stage == StageName.PROJECT_06:
                    if not isinstance(response_body, Mapping) or response_body.get("request_id") != rid:
                        raise ContractError("Project_06 response request_id mismatch")
                    status = envelope_metadata[2] if envelope_metadata else response_body.get("status")
                    if not isinstance(status, str) or status not in VALID_STATUSES:
                        raise ContractError("Project_06 response has unsupported status")
                    agent_id, version = ((envelope_metadata[0], envelope_metadata[1]) if envelope_metadata
                                         else ("project-06-planning-draft-api", None))
                elif stage == StageName.TREND:
                    if not isinstance(response_body, Mapping) or response_body.get("request_id") != rid:
                        raise ContractError("TrendAnalysisResult request_id mismatch")
                    status = envelope_metadata[2] if envelope_metadata else response_body.get("status")
                    if not isinstance(status, str) or status not in TREND_STATUSES:
                        raise ContractError("TrendAnalysisResult has unsupported status")
                    # The producer has no agent_id; v1.1 adds schema_version.
                    # Validate optional metadata without changing its raw response.
                    agent_id = response_body.get("agent_id", envelope_metadata[0] if envelope_metadata else "trend-analysis-agent")
                    version = response_body.get("schema_version", envelope_metadata[1] if envelope_metadata else None)
                    if agent_id != "trend-analysis-agent":
                        raise ContractError("unexpected trend producer agent_id")
                    if version is not None and (not isinstance(version, str) or not version.strip()):
                        raise ContractError("TrendAnalysisResult schema_version must be non-empty when present")
                else:
                    expected_agent = "stock-agent" if stage == StageName.STOCK else "sales-market-agent"
                    if envelope_metadata:
                        agent_id, version, status = envelope_metadata
                    else:
                        agent_id, version, status = _validate_stage_response(
                            response_body, rid, expected_agent=expected_agent,
                        )
                if stage == StageName.TREND:
                    planning_request = {"schema_version": P07_INPUT_SCHEMA_VERSION,
                                        "request_id": rid, "query": query}
                    planning_context = dict(normalized_scope)
                    # The query structurer already resolved explicit season data
                    # for Trend. Preserve that same value for Sales Market so a
                    # source can select its period without re-parsing shorthand
                    # such as "27년도" from the raw query.
                    trend_terms = trend_payload.get("trend_terms")
                    season = trend_terms.get("season") if isinstance(trend_terms, Mapping) else None
                    if isinstance(season, Mapping):
                        year = season.get("year")
                        if isinstance(year, int) and not isinstance(year, bool):
                            planning_context.setdefault("season_year", year)
                    if planning_context:
                        planning_request["context"] = planning_context
                record = StageRecord(stage, status, rid, agent_id, version, raw_response=raw)
                result.stages.append(record)
                if status == "error":
                    result.stopped_at = stage
                    break
                if stage == StageName.TREND:
                    shared["trend_result"] = _normalize_no_results(response_body)
                    shared["planning_request"] = planning_request
                elif stage == StageName.STOCK:
                    shared["inventory_product_result"] = _normalize_no_results(response_body)
                elif stage == StageName.PROJECT_07:
                    shared["sales_market_result"] = raw
            except Exception as exc:
                result.stages.append(StageRecord(
                    stage=stage, status="error", request_id=rid,
                    raw_response=raw, error_code=("CONTRACT_ERROR" if isinstance(exc, ContractError) else
                                                  "TRANSPORT_ERROR" if isinstance(exc, TransportError) else "STAGE_FAILURE"),
                    error_message=(str(exc) if isinstance(exc, (ContractError, TransportError))
                                   else "Stage adapter failed; details suppressed."),
                ))
                result.stopped_at = stage
                break
        return result
