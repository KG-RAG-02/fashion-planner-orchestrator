"""Contract-neutral normalization for New Product Planning inputs.

These types are internal domain models, not the shared wire contract. A transport
adapter should map the agreed PlanningRequest, TrendResult, and
InventoryProductResult schemas into these models after those schemas are
available and synchronized across repositories. Project_07 results are
consumed as producer-authored findings; Project_06 does not recalculate them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence

if TYPE_CHECKING:
    from .retrieval import PlanningKnowledgeContext
    from .styling import StylingRelationship


InputStatus = Literal["ok", "partial", "no_results", "error"]
ContextInputStatus = InputStatus | Literal["not_provided"]
ContextOrigin = Literal["user", "inferred", "unknown"]
AttributeOrigin = Literal[
    "user_requested",
    "evidence_observation",
    "agent_proposal",
    "unknown",
]


class InputConsistencyError(ValueError):
    """Raised when inputs cannot safely be combined into one planning request."""


@dataclass(frozen=True)
class AgentResult:
    """Common metadata for one upstream agent result."""

    agent_id: str
    request_id: str
    schema_version: str
    status: InputStatus
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    producer_id_origin: Literal["payload", "adapter_assumption"] = "payload"
    source_schema_version: str | None = None
    schema_version_origin: Literal["payload", "planning_request_fallback"] = "payload"
    status_origin: Literal["payload", "adapter_default"] = "payload"
    errors: tuple[Any, ...] = ()
    missing_data: tuple[Any, ...] = ()


@dataclass(frozen=True)
class AttributeSignal:
    """A normalized attribute value and its provenance."""

    name: str
    value: str | int | float | bool | None
    origin: AttributeOrigin
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanningRequest:
    result: AgentResult
    original_query: str | None = None
    season: str | None = None
    market: tuple[str, ...] = ()
    channel: tuple[str, ...] = ()
    category: str | None = None
    product_role: str | None = None
    target_customer: str | None = None
    requested_attributes: tuple[AttributeSignal, ...] = ()
    missing_fields: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    field_origins: Mapping[str, ContextOrigin] = field(default_factory=dict)
    field_evidence_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendCandidate:
    name: str
    qualification: str
    season: str | None = None
    market: tuple[str, ...] = ()
    channel: tuple[str, ...] = ()
    category: str | None = None
    attributes: tuple[AttributeSignal, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    description: str | None = None
    description_evidence_ids: tuple[str, ...] = ()
    source_style_id: str | None = None


@dataclass(frozen=True)
class TrendResult:
    result: AgentResult
    candidates: tuple[TrendCandidate, ...] = ()


@dataclass(frozen=True)
class InventoryProduct:
    style_id: str | None
    sku_ids: tuple[str, ...]
    season: str | None = None
    category: str | None = None
    market: tuple[str, ...] = ()
    channel: tuple[str, ...] = ()
    attributes: tuple[AttributeSignal, ...] = ()
    quantity: int | float | None = None
    unit: str | None = None
    inventory_definition: str | None = None
    as_of: str | None = None
    evidence_ids: tuple[str, ...] = ()
    product_role: str | None = None
    note: str | None = None
    note_origin: ContextOrigin = "unknown"
    source_product_index: int | None = None
    image_url: str | None = None
    article_id: str | None = None


@dataclass(frozen=True)
class InventoryTrendResult:
    """Stock-agent per-trend output with its explicit product_index relation."""

    trend_result_index: int
    style_id: str | None
    style_name: str | None
    status: str | None
    product_index: int | None
    mapping_status: Literal["linked", "no_product", "invalid_index", "out_of_range"]
    trend_input_match: Literal["matched", "style_id_missing", "no_matching_trend", "trend_input_empty"] = "style_id_missing"
    product_style_id: str | None = None
    rank: int | None = None
    summary: str | None = None
    evidence_ids: tuple[str, ...] = ()
    evidence_reference_source: Literal["trend_result", "root_evidence_by_style", "none"] = "none"
    limitations: tuple[str, ...] = ()
    errors: tuple[Any, ...] = ()
    missing_data: tuple[Any, ...] = ()


@dataclass(frozen=True)
class InventoryProductResult:
    result: AgentResult
    products: tuple[InventoryProduct, ...] = ()
    trend_results: tuple[InventoryTrendResult, ...] = ()
    mapping_issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanningInputs:
    """Planning inputs, including the producer-authored Project_07 result."""

    request: PlanningRequest
    trend: TrendResult
    inventory: InventoryProductResult
    sales_market_result: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ContextConflict:
    field: str
    values: Mapping[str, object]
    message: str


@dataclass(frozen=True)
class ProductPlanningContext:
    """Evidence-preserving context prepared for the internal concept generator.

    This is deliberately not serialized as ProductConcept. The shared output
    contract and model/provider boundary still require team agreement.
    """

    request_id: str
    schema_version: str
    request: PlanningRequest
    trends: TrendResult
    inventory: InventoryProductResult
    input_statuses: Mapping[str, ContextInputStatus]
    styling_relationships: tuple[StylingRelationship, ...] = ()
    conflicts: tuple[ContextConflict, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    questions_for_validation: tuple[str, ...] = ()
    sales_market_result: Mapping[str, Any] | None = None
    planning_knowledge: PlanningKnowledgeContext | None = None
    hm_visual_references: Mapping[str, Any] | None = None


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))



def _project07_evidence_ids(payload: Any) -> tuple[str, ...]:
    """Collect evidence references recursively without interpreting results."""
    found: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key == "evidence_id" and isinstance(item, str):
                    found.append(item)
                elif key in {"evidence_ids", "input_evidence_ids"} and isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                    found.extend(str(entry) for entry in item if isinstance(entry, str))
                visit(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                visit(item)

    visit(payload)
    return _unique(found)


def _project07_limitations(payload: Any) -> tuple[str, ...]:
    """Retain block-level limitations, errors and missing-data notes."""
    found: list[str] = []

    def visit(value: Any, parent: str = "") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in {"limitations", "errors", "missing_data", "data_limitations"} and isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                    for entry in item:
                        if isinstance(entry, Mapping):
                            code = entry.get("code") or entry.get("reason")
                            message = entry.get("message")
                            found.append(f"Project_07 {key} ({code}): {message}" if code and message else f"Project_07 {key}: {dict(entry)}")
                        else:
                            found.append(f"Project_07 {key}: {entry}" if key == "errors" else str(entry))
                visit(item, key)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                visit(item, parent)

    visit(payload)
    return _unique(found)


def _comparable_context(
    inputs: PlanningInputs,
    paired_inventory_indexes: frozenset[int] = frozenset(),
) -> Mapping[str, Mapping[str, object]]:
    request = inputs.request
    trends = inputs.trend.candidates
    products = inputs.inventory.products

    values: dict[str, dict[str, object]] = {
        "season": {},
        "category": {},
        "market": {},
        "channel": {},
    }
    if request.season is not None:
        values["season"]["request"] = request.season
    if request.category is not None:
        values["category"]["request"] = request.category
    if request.market:
        values["market"]["request"] = request.market
    if request.channel:
        values["channel"]["request"] = request.channel

    trend_seasons = _unique([candidate.season for candidate in trends if candidate.season])
    trend_categories = _unique(
        [candidate.category for candidate in trends if candidate.category]
    )
    trend_markets = _unique(
        [market for candidate in trends for market in candidate.market]
    )
    trend_channels = _unique(
        [channel for candidate in trends for channel in candidate.channel]
    )
    inventory_seasons = _unique([product.season for product in products if product.season])
    inventory_markets = _unique(
        [market for product in products for market in product.market]
    )
    inventory_channels = _unique(
        [channel for product in products for channel in product.channel]
    )

    if trend_seasons:
        values["season"]["trend"] = trend_seasons
    if inventory_seasons:
        values["season"]["inventory"] = inventory_seasons
    if trend_categories:
        values["category"]["trend"] = trend_categories
    if trend_markets:
        values["market"]["trend"] = trend_markets
    if inventory_markets:
        values["market"]["inventory"] = inventory_markets
    if trend_channels:
        values["channel"]["trend"] = trend_channels
    if inventory_channels:
        values["channel"]["inventory"] = inventory_channels

    request_attributes: dict[str, set[object]] = {}
    for signal in request.requested_attributes:
        if signal.value is not None:
            request_attributes.setdefault(signal.name.strip().casefold(), set()).add(
                signal.value
            )
    trend_attributes: dict[str, set[object]] = {}
    for candidate in trends:
        for signal in candidate.attributes:
            if signal.value is not None:
                trend_attributes.setdefault(signal.name.strip().casefold(), set()).add(
                    signal.value
                )
    inventory_attributes: dict[str, set[object]] = {}
    for index, product in enumerate(products):
        if index in paired_inventory_indexes:
            # A complementary styling item (for example denim bottoms paired
            # with a requested top) is a separate product, so its attributes
            # must not conflict with the requested new-product attributes.
            continue
        for signal in product.attributes:
            if signal.value is not None:
                inventory_attributes.setdefault(signal.name.strip().casefold(), set()).add(
                    signal.value
                )
    for name, requested_values in request_attributes.items():
        field_name = f"attribute:{name}"
        values[field_name] = {
            "request": tuple(sorted(requested_values, key=str))
        }
        if name in trend_attributes:
            values[field_name]["trend"] = tuple(sorted(trend_attributes[name], key=str))
        if name in inventory_attributes:
            values[field_name]["inventory"] = tuple(
                sorted(inventory_attributes[name], key=str)
            )
    return values


def _conflicts(
    inputs: PlanningInputs,
    paired_inventory_indexes: frozenset[int] = frozenset(),
) -> tuple[ContextConflict, ...]:
    conflicts: list[ContextConflict] = []
    for field_name, sources in _comparable_context(inputs, paired_inventory_indexes).items():
        normalized = {key: value for key, value in sources.items() if value not in (None, (), [])}
        if "request" not in normalized:
            continue

        def value_set(value: object) -> set[str]:
            items = value if isinstance(value, tuple) else (value,)
            return {str(item).strip().casefold() for item in items if item is not None}

        request_values = value_set(normalized["request"])
        for source, value in normalized.items():
            if source == "request" or not value_set(value):
                continue
            if request_values.isdisjoint(value_set(value)):
                conflicts.append(
                    ContextConflict(
                        field=field_name,
                        values={"request": normalized["request"], source: value},
                        message=(
                            f"Request {field_name} does not match the {source} result. "
                            "Keep both values and request review before using the "
                            "mismatched result as support."
                        ),
                    )
                )
    return tuple(conflicts)


def build_planning_context(inputs: PlanningInputs) -> ProductPlanningContext:
    """Validate shared request metadata and assemble an evidence-aware context.

    The function does not generate or rank product concepts, interpret inventory
    as demand, query sources, invoke an LLM, or serialize the shared output
    schema. Those are separate decisions and responsibilities.
    """

    if not isinstance(inputs.request, PlanningRequest):
        raise InputConsistencyError("PlanningRequest is required and must be normalized.")
    if not isinstance(inputs.trend, TrendResult):
        raise InputConsistencyError("TrendResult is required and must be normalized.")
    if not isinstance(inputs.inventory, InventoryProductResult):
        raise InputConsistencyError("InventoryProductResult is required and must be normalized.")
    if not isinstance(inputs.request.result, AgentResult):
        raise InputConsistencyError("PlanningRequest must contain a normalized AgentResult.")
    if not isinstance(inputs.trend.result, AgentResult):
        raise InputConsistencyError("TrendResult must contain a normalized AgentResult.")
    if not isinstance(inputs.inventory.result, AgentResult):
        raise InputConsistencyError("InventoryProductResult must contain a normalized AgentResult.")

    results = [inputs.request.result, inputs.trend.result, inputs.inventory.result]
    request_ids = {result.request_id for result in results}
    if len(request_ids) != 1 or not next(iter(request_ids), ""):
        raise InputConsistencyError("Inputs must share one non-empty request_id.")

    schema_versions = {result.schema_version for result in results}
    if len(schema_versions) != 1 or not next(iter(schema_versions), ""):
        raise InputConsistencyError(
            "Inputs must use one non-empty, compatible schema_version."
        )

    statuses: dict[str, ContextInputStatus] = {}
    named_results = [
        ("planning_request", inputs.request.result),
        ("trend_result", inputs.trend.result),
        ("inventory_product_result", inputs.inventory.result),
    ]
    for input_name, result in named_results:
        if result.status not in {"ok", "partial", "no_results", "error"}:
            raise InputConsistencyError(
                f"Unsupported upstream status '{result.status}' from {result.agent_id}."
            )
        if not result.agent_id:
            raise InputConsistencyError("Each upstream result must include an agent_id.")
        statuses[input_name] = result.status
    upstream_sales_market = inputs.sales_market_result
    if upstream_sales_market is not None:
        if not isinstance(upstream_sales_market, Mapping):
            raise InputConsistencyError("Project_07 result must be a mapping payload.")
        if upstream_sales_market.get("request_id") != next(iter(request_ids)):
            raise InputConsistencyError("Project_07 result must match the planning request_id.")
        if upstream_sales_market.get("agent_id") != "sales-market-agent":
            raise InputConsistencyError("Project_07 handoff must identify sales-market-agent.")
        upstream_status = upstream_sales_market.get("status")
        if upstream_status not in {"ok", "partial", "no_results", "error"}:
            raise InputConsistencyError("Project_07 result has an unsupported status.")
        statuses["sales_market_result"] = upstream_status
    else:
        statuses["sales_market_result"] = "not_provided"
    evidence_ids = _unique(
        [
            *inputs.request.result.evidence_ids,
            *inputs.trend.result.evidence_ids,
            *inputs.inventory.result.evidence_ids,
            *(evidence_id for item in inputs.inventory.trend_results for evidence_id in item.evidence_ids),
            *(evidence_id for candidate in inputs.trend.candidates for evidence_id in candidate.evidence_ids),
            *(evidence_id for product in inputs.inventory.products for evidence_id in product.evidence_ids),
            *(evidence_id for attr in inputs.request.requested_attributes for evidence_id in attr.evidence_ids),
            *(evidence_id for ids in inputs.request.field_evidence_ids.values() for evidence_id in ids),
            *(evidence_id for candidate in inputs.trend.candidates for attr in candidate.attributes for evidence_id in attr.evidence_ids),
            *(evidence_id for product in inputs.inventory.products for attr in product.attributes for evidence_id in attr.evidence_ids),
            *(
                str(item.get("evidence_id"))
                for item in (upstream_sales_market or {}).get("evidence", ())
                if isinstance(item, Mapping) and item.get("evidence_id")
            ),
            *(
                str(evidence_id)
                for finding in (upstream_sales_market or {}).get("findings", ())
                if isinstance(finding, Mapping)
                for evidence_id in finding.get("evidence_ids", ())
            ),
            *(
                str(evidence_id)
                for observation in ((upstream_sales_market or {}).get("sales_data") or {}).get("observations", ())
                if isinstance(observation, Mapping)
                for evidence_id in observation.get("evidence_ids", ())
            ),
            *_project07_evidence_ids(upstream_sales_market),
        ]
    )

    limitations = _unique(
        [
            *inputs.request.result.limitations,
            *inputs.trend.result.limitations,
            *(str(item) for item in (upstream_sales_market or {}).get("limitations", ())),
            *(str(item) for item in (((upstream_sales_market or {}).get("sales_data") or {}).get("limitations", ()))),
            *(f"Project_07 error {item.get('code')}: {item.get('message')}" for item in (upstream_sales_market or {}).get("errors", ()) if isinstance(item, Mapping)),
            *(str(item) for item in (((upstream_sales_market or {}).get("sales_data") or {}).get("errors", ()))),
            *_project07_limitations(upstream_sales_market),
            *inputs.inventory.result.limitations,
            *inputs.inventory.mapping_issues,
            *(
                f"Stock trend mapping: {issue}"
                for issue in inputs.inventory.mapping_issues
            ),
            *(str(item) for item in inputs.request.result.errors),
            *(str(item) for item in inputs.request.result.missing_data),
            *(str(item) for item in inputs.trend.result.errors),
            *(str(item) for item in inputs.trend.result.missing_data),
            *(str(item) for item in inputs.inventory.result.errors),
            *(str(item) for item in inputs.inventory.result.missing_data),
            *(
                f"Stock trend result {item.trend_result_index} has status '{item.status}'."
                for item in inputs.inventory.trend_results
                if item.status not in (None, "ok")
            ),
            *(str(value) for item in inputs.inventory.trend_results for value in item.limitations),
            *(str(value) for item in inputs.inventory.trend_results for value in item.errors),
            *(str(value) for item in inputs.inventory.trend_results for value in item.missing_data),
            *(
                f"Inventory note ({product.note_origin}; not independently verified): {product.note}"
                for product in inputs.inventory.products
                if product.note and product.note.strip()
            ),
            *(
                f"Upstream result from {result.agent_id} has status '{result.status}'."
                for result in results
                if result.status != "ok"
            ),
        ]
    )
    questions = _unique(
        [
            f"Confirm missing planning input: {field_name}."
            for field_name in inputs.request.missing_fields
        ] + [
            str(item)
            for item in (((upstream_sales_market or {}).get("sales_data") or {}).get("follow_up_questions", ()))
        ]
    )
    from .styling import build_styling_relationships

    styling_relationships = build_styling_relationships(
        inputs.request, inputs.inventory.products
    ) if statuses["inventory_product_result"] in {"ok", "partial"} else ()
    paired_inventory_indexes = frozenset(
        relation.inventory_product_index for relation in styling_relationships
    )
    conflicts = _conflicts(inputs, paired_inventory_indexes)
    questions = _unique(
        [
            *questions,
            *(
                f"상의·하의 코디 관계는 {relation.rule_version} 규칙과 "
                f"{relation.request_role_source}, {relation.inventory_role_source} 분류로 연결했습니다. "
                "코디 적합성은 Evidence로 입증된 사실이 아니므로 MD 확인이 필요합니다."
                for relation in styling_relationships
            ),
            *(
                f"Resolve the {conflict.field} mismatch across input sources."
                for conflict in conflicts
            ),
        ]
    )

    return ProductPlanningContext(
        request_id=next(iter(request_ids)),
        schema_version=next(iter(schema_versions)),
        request=inputs.request,
        trends=inputs.trend,
        inventory=inputs.inventory,
        input_statuses=statuses,
        styling_relationships=styling_relationships,
        conflicts=conflicts,
        evidence_ids=evidence_ids,
        limitations=limitations,
        questions_for_validation=questions,
        sales_market_result=upstream_sales_market,
    )
