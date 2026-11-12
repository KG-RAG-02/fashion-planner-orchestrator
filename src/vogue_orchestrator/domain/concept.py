"""Internal concept-draft model and provenance safety checks.

This is an internal review model, not the shared ProductConcept wire schema.
It can validate a candidate produced by the deterministic local generator
without selecting a model provider or committing the team to a public payload
layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from .context import (
    ContextInputStatus,
    ContextConflict,
    ContextOrigin,
    ProductPlanningContext,
)


ClaimKind = Literal[
    "user_requested",
    "evidence_observation",
    "interpretation",
    "hypothesis",
    "proposal",
    "to_validate",
    "unknown",
]


@dataclass(frozen=True)
class SourcedClaim:
    value: str | None
    kind: ClaimKind
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposedAttribute:
    name: str
    value: str | int | float | bool | None
    kind: ClaimKind
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpportunityFinding:
    """Evidence-linked comparison for human review, not a market-gap claim."""

    statement: SourcedClaim
    trend_name: str | None = None
    style_id: str | None = None
    attribute_name: str | None = None
    questions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductConceptDraft:
    """Internal candidate shape for human review before wire serialization."""

    request_id: str
    season: str | None
    category: str | None
    opportunity: SourcedClaim
    target_customer: SourcedClaim
    product_definition: SourcedClaim
    market: tuple[str, ...] = ()
    channel: tuple[str, ...] = ()
    request_constraints: tuple[str, ...] = ()
    field_origins: Mapping[str, ContextOrigin] = field(default_factory=dict)
    input_statuses: Mapping[str, ContextInputStatus] = field(default_factory=dict)
    input_conflicts: tuple[ContextConflict, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    attributes: tuple[ProposedAttribute, ...] = ()
    opportunity_findings: tuple[OpportunityFinding, ...] = ()
    relation_to_own_products: tuple[SourcedClaim, ...] = ()
    differentiation: tuple[SourcedClaim, ...] = ()
    risks: tuple[SourcedClaim, ...] = ()
    questions_for_validation: tuple[str, ...] = ()
    # Optional Project 07 handoff payload, retained verbatim until the shared
    # result schema is agreed. It is deliberately an internal compatibility
    # field and must not be serialized as an approved wire contract.
    sales_market_result: Mapping[str, Any] | None = None
    # Authorized planning sources are retained as references, not product claims.
    planning_references: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str


def _check_claim(
    claim: SourcedClaim,
    *,
    path: str,
    known_evidence_ids: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if claim.kind not in {
        "user_requested",
        "evidence_observation",
        "interpretation",
        "hypothesis",
        "proposal",
        "to_validate",
        "unknown",
    }:
        issues.append(
            ValidationIssue(
                path=path,
                code="UNSUPPORTED_CLAIM_KIND",
                message="The claim kind is not recognized by this internal model.",
            )
        )
    if claim.value is not None and not claim.value.strip():
        issues.append(
            ValidationIssue(
                path=path,
                code="EMPTY_CLAIM_VALUE",
                message="A claim value must contain text or be null.",
            )
        )
    if claim.kind == "unknown" and claim.value is not None:
        issues.append(
            ValidationIssue(
                path=path,
                code="UNKNOWN_HAS_VALUE",
                message="An unknown claim must not carry an asserted value.",
            )
        )
    if claim.kind == "evidence_observation" and not claim.evidence_ids:
        issues.append(
            ValidationIssue(
                path=path,
                code="OBSERVATION_WITHOUT_EVIDENCE",
                message="An evidence observation must cite at least one input Evidence ID.",
            )
        )
    for evidence_id in claim.evidence_ids:
        if evidence_id not in known_evidence_ids:
            issues.append(
                ValidationIssue(
                    path=path,
                    code="UNKNOWN_EVIDENCE_ID",
                    message=f"Evidence ID '{evidence_id}' is not present in the inputs.",
                )
            )
    return issues


def validate_concept_draft(
    context: ProductPlanningContext,
    draft: ProductConceptDraft,
) -> tuple[ValidationIssue, ...]:
    """Check provenance and user-request fidelity for an internal draft.

    This does not validate a public schema, infer whether evidence is licensed,
    decide whether a concept is commercially viable, or approve the concept.
    """

    issues: list[ValidationIssue] = []
    if draft.request_id != context.request_id:
        issues.append(
            ValidationIssue(
                path="request_id",
                code="REQUEST_ID_MISMATCH",
                message="The concept draft must retain the input request_id.",
            )
        )

    if draft.sales_market_result != context.sales_market_result:
        issues.append(
            ValidationIssue(
                path="sales_market_result",
                code="SALES_MARKET_RESULT_NOT_PRESERVED",
                message="The Project 07 result and its provenance must be retained unchanged in the draft.",
            )
        )

    for field_name, origin in draft.field_origins.items():
        if origin not in {"user", "inferred", "unknown"}:
            issues.append(
                ValidationIssue(
                    path=f"field_origins.{field_name}",
                    code="UNSUPPORTED_FIELD_ORIGIN",
                    message="A request field origin must be user, inferred, or unknown.",
                )
            )

    known_evidence_ids = set(context.evidence_ids)
    if context.planning_knowledge is not None:
        known_evidence_ids.update(item.evidence_id for item in context.planning_knowledge.evidence)
    for path, claim in (
        ("opportunity", draft.opportunity),
        ("target_customer", draft.target_customer),
        ("product_definition", draft.product_definition),
    ):
        issues.extend(
            _check_claim(
                claim,
                path=path,
                known_evidence_ids=known_evidence_ids,
            )
        )

    for collection_name, claims in (
        ("relation_to_own_products", draft.relation_to_own_products),
        ("differentiation", draft.differentiation),
        ("risks", draft.risks),
    ):
        for index, claim in enumerate(claims):
            issues.extend(
                _check_claim(
                    claim,
                    path=f"{collection_name}[{index}]",
                    known_evidence_ids=known_evidence_ids,
                )
            )

    for index, finding in enumerate(draft.opportunity_findings):
        issues.extend(
            _check_claim(
                finding.statement,
                path=f"opportunity_findings[{index}].statement",
                known_evidence_ids=known_evidence_ids,
            )
        )
        for question_index, question in enumerate(finding.questions):
            if not question.strip():
                issues.append(
                    ValidationIssue(
                        path=f"opportunity_findings[{index}].questions[{question_index}]",
                        code="EMPTY_VALIDATION_QUESTION",
                        message="An opportunity review question must contain text.",
                    )
                )

    requested_attributes = {
        (signal.name.strip().casefold(), signal.value)
        for signal in context.request.requested_attributes
        if signal.value is not None
    }
    for index, attribute in enumerate(draft.attributes):
        path = f"attributes[{index}]"
        if not attribute.name.strip():
            issues.append(
                ValidationIssue(
                    path=path,
                    code="EMPTY_ATTRIBUTE_NAME",
                    message="An attribute must have a non-empty name.",
                )
            )

        if attribute.kind not in {
            "user_requested",
            "evidence_observation",
            "interpretation",
            "hypothesis",
            "proposal",
            "to_validate",
            "unknown",
        }:
            issues.append(
                ValidationIssue(
                    path=path,
                    code="UNSUPPORTED_CLAIM_KIND",
                    message="The attribute kind is not recognized by this internal model.",
                )
            )
        if isinstance(attribute.value, str) and not attribute.value.strip():
            issues.append(
                ValidationIssue(
                    path=path,
                    code="EMPTY_ATTRIBUTE_VALUE",
                    message="An attribute value must contain text or be null.",
                )
            )
        if attribute.kind == "unknown" and attribute.value is not None:
            issues.append(
                ValidationIssue(
                    path=path,
                    code="UNKNOWN_ATTRIBUTE_HAS_VALUE",
                    message="An unknown attribute must not carry an asserted value.",
                )
            )
        if attribute.kind == "evidence_observation" and not attribute.evidence_ids:
            issues.append(
                ValidationIssue(
                    path=path,
                    code="OBSERVATION_WITHOUT_EVIDENCE",
                    message="An observed attribute must cite input Evidence IDs.",
                )
            )
        if attribute.kind == "user_requested" and attribute.value is not None:
            key = (attribute.name.strip().casefold(), attribute.value)
            if key not in requested_attributes:
                issues.append(
                    ValidationIssue(
                        path=path,
                        code="UNREQUESTED_VALUE_MARKED_AS_USER_INPUT",
                        message=(
                            "A user_requested attribute must match a non-empty "
                            "attribute in the original planning request."
                        ),
                    )
                )
        for evidence_id in attribute.evidence_ids:
            if evidence_id not in known_evidence_ids:
                issues.append(
                    ValidationIssue(
                        path=path,
                        code="UNKNOWN_EVIDENCE_ID",
                        message=f"Evidence ID '{evidence_id}' is not present in the inputs.",
                    )
                )

    for index, reference in enumerate(draft.planning_references):
        evidence_id = reference.get("evidence_id")
        if evidence_id not in known_evidence_ids:
            issues.append(ValidationIssue(
                path=f"planning_references[{index}].evidence_id",
                code="UNKNOWN_RETRIEVED_EVIDENCE_ID",
                message="A planning reference must cite an authorized Evidence ID returned by retrieval.",
            ))
        allowed_synthetic = (
            reference.get("permission_status") == "synthetic_only"
            and reference.get("is_synthetic") is True
            and bool(reference.get("dataset_id"))
        )
        if reference.get("permission_status") != "authorized" and not allowed_synthetic:
            issues.append(ValidationIssue(
                path=f"planning_references[{index}].permission_status",
                code="UNAUTHORIZED_PLANNING_REFERENCE",
                message="Only explicitly authorized planning references may appear in the draft.",
            ))
        if "product_planning" not in reference.get("permitted_uses", ()):
            issues.append(ValidationIssue(
                path=f"planning_references[{index}].permitted_uses",
                code="PLANNING_USE_NOT_PERMITTED",
                message="A planning reference must permit product_planning use.",
            ))

    for index, question in enumerate(draft.questions_for_validation):
        if not question.strip():
            issues.append(
                ValidationIssue(
                    path=f"questions_for_validation[{index}]",
                    code="EMPTY_VALIDATION_QUESTION",
                    message="A validation question must contain text.",
                )
            )

    return tuple(issues)
