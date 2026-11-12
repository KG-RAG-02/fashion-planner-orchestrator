"""Versioned, conservative outfit-role compatibility rules."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Sequence

from .context import InventoryProduct, PlanningRequest


STYLING_RULE_VERSION = "outfit-role-pairing-v1"
ProductRole = Literal["upper_body", "lower_body"]


@dataclass(frozen=True)
class StylingRelationship:
    """A taxonomy-rule pairing opportunity, not evidence of demand or sell-through."""

    inventory_product_index: int
    requested_role: ProductRole
    inventory_role: ProductRole
    relation_type: Literal["styling_complement"]
    rule_version: str
    request_role_source: str
    inventory_role_source: str
    inventory_evidence_ids: tuple[str, ...] = ()


_ROLE_TERMS: dict[ProductRole, tuple[str, ...]] = {
    "upper_body": (
        "upper_body", "upper body", "upperwear", "top", "상의", "아우터", "재킷", "자켓", "점퍼",
        "coat", "jacket", "anorak", "outerwear",
    ),
    "lower_body": (
        "lower_body", "lower body", "bottom", "하의", "청바지", "바지", "팬츠", "데님",
        "denim", "jeans", "trousers", "trouser", "pants", "slacks",
    ),
}
_DISPLAY_CATEGORIES = {
    "denim": "청바지",
    "jeans": "청바지",
    "trousers": "바지",
    "pants": "바지",
    "bottoms": "하의",
    "bottom": "하의",
    "outerwear": "아우터",
    "tops": "상의",
    "top": "상의",
}


def display_inventory_label(product: InventoryProduct) -> str:
    """Return a readable companion label without changing its source category."""
    category = (product.category or "").strip()
    if category:
        return _DISPLAY_CATEGORIES.get(category.casefold(), category)
    note = (product.note or "").casefold()
    for terms, label in (
        (("청바지", "denim", "jeans"), "청바지"),
        (("바지", "팬츠", "trouser", "pants", "slacks"), "하의"),
    ):
        if any(term in note for term in terms):
            return label
    return product.note or "상품명 미제공"


def _role_from_text(value: str | None) -> ProductRole | None:
    normalized = (value or "").strip().casefold()
    if not normalized:
        return None
    def matches_term(term: str) -> bool:
        if term.isascii():
            escaped = re.escape(term)
            return re.search(rf"(?<![a-z]){escaped}(?![a-z])", normalized) is not None
        return term in normalized

    matches = [role for role, terms in _ROLE_TERMS.items() if any(matches_term(term) for term in terms)]
    return matches[0] if len(matches) == 1 else None


def _request_role(request: PlanningRequest) -> tuple[ProductRole | None, str | None]:
    explicit = _role_from_text(request.product_role)
    if explicit:
        return explicit, "PlanningRequest.product_role"
    query_role = _role_from_text(request.original_query)
    if query_role:
        return query_role, "PlanningRequest.original_query keyword mapping"
    return None, None


def _inventory_role(product: InventoryProduct) -> tuple[ProductRole | None, str | None]:
    explicit = _role_from_text(product.product_role)
    if explicit:
        return explicit, "InventoryProduct.product_role"
    category_role = _role_from_text(product.category)
    if category_role:
        return category_role, "inventory category taxonomy mapping"
    note_role = _role_from_text(product.note)
    if note_role:
        return note_role, "inventory note keyword mapping"
    return None, None


def build_styling_relationships(
    request: PlanningRequest,
    products: Sequence[InventoryProduct],
) -> tuple[StylingRelationship, ...]:
    """Pair explicitly recognizable tops and bottoms; leave all other pairs unresolved."""
    requested_role, request_source = _request_role(request)
    if requested_role is None or request_source is None:
        return ()
    relationships: list[StylingRelationship] = []
    for index, product in enumerate(products):
        inventory_role, inventory_source = _inventory_role(product)
        if inventory_role is None or inventory_source is None or inventory_role == requested_role:
            continue
        relationships.append(StylingRelationship(
            inventory_product_index=index,
            requested_role=requested_role,
            inventory_role=inventory_role,
            relation_type="styling_complement",
            rule_version=STYLING_RULE_VERSION,
            request_role_source=request_source,
            inventory_role_source=inventory_source,
            inventory_evidence_ids=product.evidence_ids,
        ))
    return tuple(relationships)
