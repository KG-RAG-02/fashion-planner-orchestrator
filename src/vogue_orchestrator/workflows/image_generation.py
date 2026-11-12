"""Evidence-aware product concept image prompts and opt-in image providers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import ipaddress
import socket
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from ..domain.concept import ProductConceptDraft, ValidationIssue, validate_concept_draft
from ..domain.context import ProductPlanningContext
from ..domain.generator import RuleBasedConceptGenerator
from ..domain.analysis import dimension_mismatches
from ..domain.prompt_safety import serialize_untrusted_prompt_data
from ..domain.prompt_registry import load_prompt, prompt_version_id, render_prompt
from ..domain.styling import StylingRelationship


ImageStatus = Literal["not_requested", "ready", "generated", "blocked", "failed"]
PROMPT_VERSION = prompt_version_id("concept_image")
DEFAULT_IMAGE_MODEL = "gpt-image-2.5-sunburst"
DEFAULT_IMAGE_DIR = Path("data/local/generated_images")
IMAGE_DESIGNER_SYSTEM_PROMPT = load_prompt("concept_image", "system")
_DESIGN_ATTRIBUTE_NAMES = {
    "color", "colour", "color_group", "colour_group", "색상", "컬러",
    "pattern", "print", "패턴", "프린트", "silhouette", "shape", "실루엣",
    "material", "fabric", "소재", "원단", "fit", "핏", "디테일", "detail",
    "decoration", "장식", "texture", "조직감", "length", "기장",
}

@dataclass(frozen=True)
class ImageGenerationRequest:
    request_id: str
    season: str | None
    category: str | None
    prompt: str
    prompt_version: str
    prompt_sha256: str
    evidence_ids: tuple[str, ...]
    proposal_fields: tuple[str, ...]
    limitations: tuple[str, ...]
    conflicts: tuple[str, ...]
    styling_relationships: tuple[StylingRelationship, ...] = ()
    system_prompt: str = IMAGE_DESIGNER_SYSTEM_PROMPT
    purpose: str = "MD/디자이너 검토용 상품 콘셉트 시안"
    evidence_uses: tuple["ImageEvidenceUse", ...] = ()
    excluded_evidence: tuple["ImageEvidenceExclusion", ...] = ()


@dataclass(frozen=True)
class ImageEvidenceUse:
    evidence_id: str
    source: str
    source_ref: str
    supports: str


@dataclass(frozen=True)
class ImageEvidenceExclusion:
    evidence_id: str
    source: str
    reason: str


@dataclass(frozen=True)
class GeneratedImage:
    image_bytes: bytes
    mime_type: str
    provider: str
    model: str


@dataclass(frozen=True)
class ImageGenerationResult:
    status: ImageStatus
    request: ImageGenerationRequest
    provider: str | None = None
    model: str | None = None
    image_id: str | None = None
    image_path: str | None = None
    limitations: tuple[str, ...] = ()
    error_code: str | None = None
    review_only: bool = True


@dataclass(frozen=True)
class ConceptImageWorkflowResult:
    draft: ProductConceptDraft
    validation_issues: tuple[ValidationIssue, ...]
    image: ImageGenerationResult


class ImageGenerationProvider(Protocol):
    """A provider that returns one image for a reviewed concept request."""

    def generate(self, request: ImageGenerationRequest) -> GeneratedImage: ...


def build_image_generation_request(
    context: ProductPlanningContext,
    draft: ProductConceptDraft,
) -> ImageGenerationRequest:
    """Build a product-only prompt and an auditable Evidence allowlist."""
    known_ids = set(context.evidence_ids)
    synthetic_rag_ids = set()
    if context.planning_knowledge:
        known_ids.update(item.evidence_id for item in context.planning_knowledge.evidence if item.evidence_id)
        synthetic_rag_ids.update(item.evidence_id for item in context.planning_knowledge.evidence
                                 if item.is_synthetic and item.evidence_id)
    used_ids: list[str] = []
    evidence_uses: list[ImageEvidenceUse] = []
    excluded: list[ImageEvidenceExclusion] = []
    user_scope: list[str] = []
    evidence_lines: list[str] = []
    proposal_lines: list[str] = []
    unverified_trends: list[str] = []
    request_usable = context.input_statuses.get("planning_request") in {"ok", "partial"}
    trend_usable = context.input_statuses.get("trend_result") in {"ok", "partial"}

    def use_ids(ids, *, source: str, source_ref: str, supports: str) -> tuple[str, ...]:
        accepted = []
        for evidence_id in dict.fromkeys(ids):
            if evidence_id in synthetic_rag_ids:
                excluded.append(ImageEvidenceExclusion(
                    str(evidence_id), source, "합성 RAG Evidence는 신제품 이미지 provider 입력에 사용하지 않음",
                ))
                continue
            if evidence_id in known_ids:
                accepted.append(evidence_id)
                used_ids.append(evidence_id)
                evidence_uses.append(ImageEvidenceUse(evidence_id, source, source_ref, supports))
            else:
                excluded.append(ImageEvidenceExclusion(
                    str(evidence_id), source, "입력 전체 Evidence 목록에 등록되지 않은 ID",
                ))
        return tuple(accepted)

    def exclude_ids(ids, *, source: str, reason: str) -> None:
        for evidence_id in dict.fromkeys(ids):
            excluded.append(ImageEvidenceExclusion(str(evidence_id), source, reason))

    def design_name(name: str) -> bool:
        return name.strip().casefold() in _DESIGN_ATTRIBUTE_NAMES

    if context.request.original_query and request_usable:
        user_scope.append(f"- 원문 요청: {context.request.original_query}")
    if context.request.season and request_usable:
        origin = context.request.field_origins.get("season", "unknown")
        user_scope.append(f"- 시즌: {context.request.season} (출처: {origin})")
        ids = use_ids(context.request.field_evidence_ids.get("season", ()), source="planning_request",
                      source_ref="season", supports=f"시즌 {context.request.season}")
        if ids:
            user_scope.append("  [Evidence: " + ", ".join(ids) + "]")
    if context.request.category and request_usable:
        origin = context.request.field_origins.get("category", "unknown")
        user_scope.append(f"- 카테고리: {context.request.category} (출처: {origin})")
        ids = use_ids(context.request.field_evidence_ids.get("category", ()), source="planning_request",
                      source_ref="category", supports=f"카테고리 {context.request.category}")
        if ids:
            user_scope.append("  [Evidence: " + ", ".join(ids) + "]")
    for signal in context.request.requested_attributes if request_usable else ():
        if signal.value is None or not design_name(signal.name):
            continue
        label = f"{signal.name}={signal.value}"
        if signal.origin == "user_requested":
            ids = use_ids(signal.evidence_ids, source="planning_request",
                          source_ref=f"requested_attributes.{signal.name}", supports=label)
            user_scope.append(f"- {label} (사용자가 지정한 조건)" + (f" [Evidence: {', '.join(ids)}]" if ids else ""))
        else:
            proposal_lines.append(f"- 출처 확인 필요 속성 {label}: 사용자 고정 조건으로 처리하지 않음")
            exclude_ids(signal.evidence_ids, source="planning_request",
                        reason="요청 속성의 사용자 지정 출처가 확인되지 않음")

    candidate_names = {candidate.name.strip() for candidate in context.trends.candidates if candidate.name.strip()}
    for candidate in context.trends.candidates if trend_usable else ():
        source_ref = f"trend_candidates.{candidate.source_style_id or candidate.name}"
        mismatches = dimension_mismatches(context.request, candidate) if request_usable else ("request_status",)
        if not candidate.name.strip() or mismatches:
            reason = "요청 범위와 일치하지 않음" if mismatches else "후보명이 없음"
            exclude_ids(candidate.evidence_ids, source="trend_result", reason=reason)
            for signal in candidate.attributes:
                exclude_ids(signal.evidence_ids, source="trend_result", reason=reason)
            exclude_ids(candidate.description_evidence_ids, source="trend_result", reason=reason)
            continue
        if candidate.evidence_ids:
            ids = use_ids(candidate.evidence_ids, source="trend_result", source_ref=source_ref,
                          supports=f"트렌드 후보 {candidate.name}")
            if ids:
                evidence_lines.append(f"- 적격 트렌드 후보: {candidate.name} [Evidence: {', '.join(ids)}]")
        else:
            unverified_trends.append(candidate.name)
            proposal_lines.append(f"- 미검증 트렌드명 ‘{candidate.name}’은 디자인 사실로 사용하지 않음")
        for signal in candidate.attributes:
            if signal.value is None or not design_name(signal.name):
                exclude_ids(signal.evidence_ids, source="trend_result", reason="시각 디자인 속성이 아님")
                continue
            value_text = f"{signal.name}={signal.value}"
            if signal.evidence_ids:
                ids = use_ids(signal.evidence_ids, source="trend_result",
                              source_ref=f"{source_ref}.attributes.{signal.name}", supports=value_text)
                if ids:
                    evidence_lines.append(f"- 트렌드 관찰: {value_text} [Evidence: {', '.join(ids)}]")
            else:
                unverified_trends.append(f"{candidate.name}:{value_text}")
                proposal_lines.append(f"- 근거 미연결 속성 {value_text}: 확정 사실이 아닌 검증 필요 항목")
        if candidate.description_evidence_ids and candidate.description:
            ids = use_ids(candidate.description_evidence_ids, source="trend_result",
                          source_ref=f"{source_ref}.description", supports=candidate.description)
            if ids:
                evidence_lines.append(f"- 트렌드 설명: {candidate.description} [Evidence: {', '.join(ids)}]")
    if not trend_usable:
        for candidate in context.trends.candidates:
            exclude_ids(candidate.evidence_ids, source="trend_result", reason="트렌드 입력 상태가 사용 가능 상태가 아님")
            for signal in candidate.attributes:
                exclude_ids(signal.evidence_ids, source="trend_result", reason="트렌드 입력 상태가 사용 가능 상태가 아님")
            exclude_ids(candidate.description_evidence_ids, source="trend_result", reason="트렌드 입력 상태가 사용 가능 상태가 아님")

    # Producer-level IDs without a field/claim relation are retained in input trace,
    # but intentionally cannot support a visual instruction.
    linked_request_ids = {
        evidence_id for signal in context.request.requested_attributes for evidence_id in signal.evidence_ids
    } | {
        evidence_id for ids in context.request.field_evidence_ids.values() for evidence_id in ids
    }
    exclude_ids(
        (eid for eid in context.request.result.evidence_ids if eid not in linked_request_ids),
        source="planning_request", reason="요청 전체 수준 ID이며 시각 속성에 연결되지 않음",
    )
    if not request_usable:
        exclude_ids(linked_request_ids, source="planning_request", reason="요청 입력 상태가 사용 가능 상태가 아님")
    linked_trend_ids = {
        evidence_id
        for candidate in context.trends.candidates
        for evidence_id in (
            *candidate.evidence_ids,
            *candidate.description_evidence_ids,
            *(eid for signal in candidate.attributes for eid in signal.evidence_ids),
        )
    }
    exclude_ids(
        (eid for eid in context.trends.result.evidence_ids if eid not in linked_trend_ids),
        source="trend_result", reason="트렌드 결과 수준 ID이며 후보·속성·설명에 연결되지 않음",
    )
    if not trend_usable:
        exclude_ids(context.trends.result.evidence_ids, source="trend_result", reason="트렌드 입력 상태가 사용 가능 상태가 아님")
    inventory_ids = set(context.inventory.result.evidence_ids)
    for product in context.inventory.products:
        inventory_ids.update(product.evidence_ids)
        for signal in product.attributes:
            inventory_ids.update(signal.evidence_ids)
    exclude_ids(inventory_ids, source="inventory_product_result", reason="재고 근거는 착장 단계 전용")
    sales_ids: set[str] = set()
    def collect_sales_ids(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "evidence_id" and isinstance(child, str):
                    sales_ids.add(child)
                elif key in {"evidence_ids", "input_evidence_ids"}:
                    if isinstance(child, str):
                        sales_ids.add(child)
                    elif isinstance(child, (list, tuple)):
                        sales_ids.update(str(item) for item in child if isinstance(item, str))
                else:
                    collect_sales_ids(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect_sales_ids(child)
    if context.sales_market_result:
        collect_sales_ids(context.sales_market_result)
    exclude_ids(sales_ids, source="sales_market_result", reason="판매·시장 결과는 기획자 분석 입력이며 이미지 API에는 원문을 직접 전달하지 않음")

    planning_reference_ids: set[str] = set()
    knowledge = context.planning_knowledge
    if knowledge:
        for result in knowledge.by_purpose:
            if result.status not in {"ok", "partial"}:
                continue
            for item in result.evidence:
                if item.evidence_id:
                    planning_reference_ids.add(item.evidence_id)
    exclude_ids(
        planning_reference_ids,
        source="planning_knowledge",
        reason="RAG 근거는 초안·텍스트 기획 보드에만 사용하며 이미지 provider prompt에는 전달하지 않음",
    )

    for name, claim in (("opportunity", draft.opportunity), ("product_definition", draft.product_definition)):
        if claim.kind == "proposal" and claim.value:
            proposal_lines.append(f"- 초안 시각화 제안 ({name}): {claim.value}")
    for attribute in draft.attributes:
        if attribute.kind == "proposal" and attribute.value is not None and design_name(attribute.name):
            proposal_lines.append(f"- 초안 시각화 제안: {attribute.name}={attribute.value}")

    # Retain stable, unique exclusions while preserving multiple source-specific reasons.
    excluded = list(dict.fromkeys(excluded))
    limitations = list(draft.limitations)
    if unverified_trends:
        limitations.append("근거 ID가 없는 트렌드명 또는 속성은 신제품 이미지 사실로 사용하지 않았습니다: "
                           + ", ".join(dict.fromkeys(unverified_trends)))
    if not evidence_lines:
        limitations.append("이미지 입력에 원문 출처가 검증된 요청·트렌드 관찰이 없습니다.")
    if context.sales_market_result:
        limitations.append("판매·시장 결과 원문은 이미지 API에 직접 전달하지 않고, 기획자가 만든 시각 제안만 반영했습니다.")
    if any(not claim.evidence_ids for claim in context.request.requested_attributes if claim.value is not None):
        limitations.append("사용자 지정 디자인 속성 중 일부에는 속성별 Evidence ID가 연결되지 않았습니다.")
    if not proposal_lines:
        proposal_lines.append("- 색상·소재·실루엣·패턴·디테일은 미확정이며, 임의 선택은 모두 검토용 시각화 제안")
    prompt_sections = [
        "상품기획 검토용 신제품 콘셉트 이미지 1장을 생성합니다. 완성 상품, 실제 상품 사진, 승인된 디자인 또는 생산 지시가 아닙니다.",
        "사용자 요청 범위 (재고·판매시장 데이터 제외):\n" + ("\n".join(user_scope) if user_scope else "- 미제공"),
        "요청 범위와 일치하며 속성·설명 단위로 Evidence가 연결된 디자인 관찰:\n" + ("\n".join(evidence_lines) if evidence_lines else "- 제공 가능한 검증 관찰 없음"),
        "검토용 제안 또는 미검증 항목 (사실로 표현하지 않음):\n" + "\n".join(proposal_lines),
        "미확인·제외 조건:\n- 재고·판매·시장 결과는 사용하지 않음.\n- 근거 없는 트렌드 의미, 수요, 고객 선호, 생산 가능성을 주장하지 않음.\n- 로고, 문구, 워터마크를 추가하지 않음.",
    ]
    conflict_text: tuple[str, ...] = ()
    prompt = render_prompt("concept_image", "user", {
        "UNTRUSTED_PLANNING_INPUT_JSON": serialize_untrusted_prompt_data(
            {"planning_context_sections": prompt_sections},
        ),
    })
    digest = hashlib.sha256((IMAGE_DESIGNER_SYSTEM_PROMPT + "\n\n" + prompt).encode("utf-8")).hexdigest()
    return ImageGenerationRequest(
        request_id=context.request_id,
        season=draft.season,
        category=draft.category,
        prompt=prompt,
        prompt_version=PROMPT_VERSION,
        prompt_sha256=digest,
        evidence_ids=tuple(dict.fromkeys(used_ids)),
        proposal_fields=tuple(dict.fromkeys(proposal_lines)),
        limitations=tuple(dict.fromkeys(limitations)),
        conflicts=(),
        styling_relationships=(),
        system_prompt=IMAGE_DESIGNER_SYSTEM_PROMPT,
        evidence_uses=tuple(dict.fromkeys(evidence_uses)),
        excluded_evidence=tuple(excluded),
    )


def run_image_generation(
    request: ImageGenerationRequest,
    *,
    provider: ImageGenerationProvider | None = None,
    enabled: bool = False,
    confirmed: bool = False,
    output_dir: Path = DEFAULT_IMAGE_DIR,
) -> ImageGenerationResult:
    """Generate only after both runtime activation and an explicit confirmation."""
    if request.conflicts:
        return ImageGenerationResult(
            status="blocked", request=request,
            limitations=("입력 충돌을 MD가 확인하기 전까지 이미지 생성을 보류했습니다.",),
        )
    if not request.season or not request.category:
        return ImageGenerationResult(
            status="blocked", request=request,
            limitations=("시즌과 카테고리가 확인되기 전까지 이미지 생성을 보류했습니다.",),
        )
    if not confirmed and enabled:
        return ImageGenerationResult(
            status="ready", request=request,
            limitations=("프롬프트가 준비됐습니다. 이 실행에서 실제 생성하려면 호출별 확인이 필요합니다.",),
        )
    if not enabled:
        return ImageGenerationResult(
            status="blocked" if confirmed else "not_requested", request=request,
            limitations=("이미지 공급자가 비활성 상태입니다. 실제 생성에는 설정 활성화와 호출별 확인이 모두 필요합니다.",),
        )
    if provider is None:
        return ImageGenerationResult(
            status="failed", request=request, error_code="IMAGE_PROVIDER_NOT_CONFIGURED",
            limitations=("이미지 공급자가 설정되지 않아 프롬프트만 보존했습니다.",),
        )
    try:
        generated = provider.generate(request)
        if not generated.image_bytes:
            raise ValueError("Provider returned an empty image")
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(generated.mime_type)
        if extension is None:
            raise ValueError("Provider returned an unsupported image type")
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.request_id)[:80] or "concept"
        image_id = "IMG-" + uuid.uuid4().hex[:12].upper()
        image_path = output_dir / f"{safe_id}-{request.prompt_sha256[:12]}-{image_id}{extension}"
        image_path.write_bytes(generated.image_bytes)
        return ImageGenerationResult(
            status="generated", request=request, provider=generated.provider,
            model=generated.model, image_id=image_id, image_path=str(image_path),
            limitations=("생성 결과는 검토용 시안이며 정확한 상품 사양이나 생산 가능성을 보증하지 않습니다.",),
        )
    except Exception as exc:  # Provider errors should not discard the planning draft.
        code = "IMAGE_GENERATION_FAILED"
        if isinstance(exc, ImageProviderError):
            code = exc.code
        return ImageGenerationResult(
            status="failed", request=request, provider=getattr(provider, "provider_name", None),
            model=getattr(provider, "model", None), error_code=code,
            limitations=("이미지 생성이 실패했으며 기획 초안과 프롬프트는 유지됩니다. 오류 본문이나 비밀값은 노출하지 않았습니다.",),
        )


def run_concept_to_image(
    context: ProductPlanningContext,
    *,
    draft: ProductConceptDraft | None = None,
    provider: ImageGenerationProvider | None = None,
    image_generation_enabled: bool = False,
    confirm_image_generation: bool = False,
    output_dir: Path = DEFAULT_IMAGE_DIR,
) -> ConceptImageWorkflowResult:
    """Prepare an image from the supplied planner draft or the rule fallback."""
    if draft is None:
        draft, issues = RuleBasedConceptGenerator().generate(context)
    else:
        issues = validate_concept_draft(context, draft)
    image_request = build_image_generation_request(context, draft)
    image_result = run_image_generation(
        image_request,
        provider=provider,
        enabled=image_generation_enabled,
        confirmed=confirm_image_generation,
        output_dir=output_dir,
    )
    return ConceptImageWorkflowResult(draft=draft, validation_issues=issues, image=image_result)


class ImageProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class OpenAIImageProvider:
    """Minimal OpenAI Images API adapter; never logs credentials or response bodies."""

    api_key: str = field(repr=False)
    model: str = DEFAULT_IMAGE_MODEL
    timeout_seconds: int = 180
    endpoint: str = "https://api.openai.com/v1/images/generations"
    provider_name: str = "OpenAI Images API"

    def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        if not self.api_key.strip():
            raise ImageProviderError("IMAGE_API_KEY_MISSING")
        full_prompt = (
            "SYSTEM INSTRUCTIONS (follow throughout):\n"
            + request.system_prompt
            + "\n\nUSER DESIGN BRIEF:\n"
            + request.prompt
        )
        body = json.dumps({"model": self.model, "prompt": full_prompt, "n": 1}, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ImageProviderError(f"IMAGE_API_HTTP_{exc.code}") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise ImageProviderError("IMAGE_API_TIMEOUT") from None
            if isinstance(reason, OSError) and getattr(reason, "errno", None) in {-2, 8}:
                raise ImageProviderError("IMAGE_API_DNS_FAILURE") from None
            if isinstance(reason, ssl.SSLError):
                raise ImageProviderError("IMAGE_API_TLS_FAILURE") from None
            raise ImageProviderError("IMAGE_API_UNAVAILABLE") from None
        except TimeoutError:
            raise ImageProviderError("IMAGE_API_TIMEOUT") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ImageProviderError("IMAGE_API_INVALID_RESPONSE") from None
        try:
            encoded = payload["data"][0]["b64_json"]
            image_bytes = base64.b64decode(encoded, validate=True)
        except (KeyError, IndexError, TypeError, ValueError):
            raise ImageProviderError("IMAGE_API_IMAGE_MISSING") from None
        return GeneratedImage(image_bytes=image_bytes, mime_type="image/png", provider=self.provider_name, model=self.model)


@dataclass
class OpenAIImageEditProvider:
    """Generate a new image from a prompt and one already generated reference image."""

    api_key: str = field(repr=False)
    model: str = "gpt-image-2.5-sunburst"
    timeout_seconds: int = 240
    endpoint: str = "https://api.openai.com/v1/images/edits"
    provider_name: str = "OpenAI Images Edit API"

    def generate_from_reference(
        self,
        prompt: str,
        reference_image: bytes | tuple[bytes | tuple[bytes, str], ...],
        *,
        mime_type: str = "image/png",
        size: str = "1536x1024",
    ) -> GeneratedImage:
        if not self.api_key.strip():
            raise ImageProviderError("IMAGE_API_KEY_MISSING")
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ImageProviderError("IMAGE_API_REFERENCE_FORMAT_UNSUPPORTED")
        references = (reference_image,) if isinstance(reference_image, bytes) else reference_image
        if not references or len(references) > 4:
            raise ImageProviderError("IMAGE_API_REFERENCE_COUNT_UNSUPPORTED")
        data_urls = []
        for reference in references:
            image, image_mime = reference if isinstance(reference, tuple) else (reference, mime_type)
            if image_mime not in {"image/png", "image/jpeg", "image/webp"}:
                raise ImageProviderError("IMAGE_API_REFERENCE_FORMAT_UNSUPPORTED")
            data_urls.append(f"data:{image_mime};base64,{base64.b64encode(image).decode('ascii')}")
        body = json.dumps({
            "model": self.model,
            "images": [{"image_url": data_url} for data_url in data_urls],
            "prompt": prompt,
            "size": size,
            "quality": "high",
            "output_format": "png",
            "n": 1,
        }, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ImageProviderError(f"IMAGE_API_HTTP_{exc.code}") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise ImageProviderError("IMAGE_API_TIMEOUT") from None
            if isinstance(reason, OSError) and getattr(reason, "errno", None) in {-2, 8}:
                raise ImageProviderError("IMAGE_API_DNS_FAILURE") from None
            if isinstance(reason, ssl.SSLError):
                raise ImageProviderError("IMAGE_API_TLS_FAILURE") from None
            raise ImageProviderError("IMAGE_API_UNAVAILABLE") from None
        except TimeoutError:
            raise ImageProviderError("IMAGE_API_TIMEOUT") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ImageProviderError("IMAGE_API_INVALID_RESPONSE") from None
        try:
            encoded = payload["data"][0]["b64_json"]
            image_bytes = base64.b64decode(encoded, validate=True)
        except (KeyError, IndexError, TypeError, ValueError):
            raise ImageProviderError("IMAGE_API_IMAGE_MISSING") from None
        return GeneratedImage(image_bytes, "image/png", self.provider_name, self.model)


def fetch_inventory_image(url: str, *, timeout_seconds: int = 8, max_bytes: int = 8_000_000) -> tuple[bytes, str]:
    """Fetch a bounded public HTTPS image, rejecting redirects and private hosts."""
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ImageProviderError("INVENTORY_IMAGE_URL_INVALID")
    allowed_hosts = {
        host.strip().casefold()
        for host in os.getenv("VOGUE_INVENTORY_IMAGE_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }
    hostname = parsed.hostname.casefold()
    if not any(hostname == host or hostname.endswith("." + host) for host in allowed_hosts):
        raise ImageProviderError("INVENTORY_IMAGE_HOST_NOT_ALLOWED")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ImageProviderError("INVENTORY_IMAGE_HOST_NOT_PUBLIC")
    except (OSError, ValueError):
        raise ImageProviderError("INVENTORY_IMAGE_HOST_UNAVAILABLE") from None

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={"Accept": "image/png,image/jpeg,image/webp"})
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get_content_type()
            length = response.headers.get("Content-Length")
            if content_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise ImageProviderError("INVENTORY_IMAGE_FORMAT_UNSUPPORTED")
            if length and int(length) > max_bytes:
                raise ImageProviderError("INVENTORY_IMAGE_TOO_LARGE")
            content = response.read(max_bytes + 1)
            if not content or len(content) > max_bytes:
                raise ImageProviderError("INVENTORY_IMAGE_TOO_LARGE")
            signatures = {
                "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
                "image/jpeg": content.startswith(b"\xff\xd8\xff"),
                "image/webp": len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP",
            }
            if not signatures.get(content_type, False):
                raise ImageProviderError("INVENTORY_IMAGE_CONTENT_INVALID")
            return content, content_type
    except urllib.error.HTTPError as exc:
        raise ImageProviderError(f"INVENTORY_IMAGE_HTTP_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ImageProviderError("INVENTORY_IMAGE_UNAVAILABLE") from None


def build_outfit_image_prompt(context: ProductPlanningContext) -> str | None:
    """Build a companion outfit brief, isolated from new-product generation."""
    if context.input_statuses.get("inventory_product_result") not in {"ok", "partial"}:
        return None
    relationships = [
        relation for relation in context.styling_relationships
        if relation.inventory_product_index < len(context.inventory.products)
    ]
    if not relationships:
        return None
    products = []
    for relation in relationships:
        product = context.inventory.products[relation.inventory_product_index]
        role = "상의" if relation.inventory_role == "upper_body" else "하의"
        facts = [f"역할={role}", f"상품={product.style_id or product.category or product.note or '미제공'}"]
        if product.category:
            facts.append(f"카테고리={product.category}")
        for attribute in product.attributes:
            if attribute.value is not None:
                facts.append(f"{attribute.name}={attribute.value}")
        if product.note:
            facts.append(f"참고 메모={product.note}")
        products.append("; ".join(facts))
    return (
        "제공된 신제품 참조 이미지의 의상은 그대로 유지하고, 아래 재고 의상과 함께 입은 검토용 착장 이미지를 생성합니다. "
        "새상품의 색상·형태·디테일을 바꾸거나 재고 상품을 신제품으로 재해석하지 마세요. "
        "재고 의상을 역할에 맞게 착장하고 로고·문구는 추가하지 마세요.\n"
        "재고 착장 아이템:\n" + "\n".join(f"- {item}" for item in products)
    )


def selected_outfit_inventory(context: ProductPlanningContext):
    """Return only inventory products paired by the existing role compatibility rule."""
    return tuple(
        context.inventory.products[relation.inventory_product_index]
        for relation in context.styling_relationships
        if relation.inventory_product_index < len(context.inventory.products)
    )


def load_r2_inventory_image(article_id: str, *, store=None, cache_dir: Path | None = None) -> tuple[bytes, str]:
    """Load one product JPEG from the stock-agent R2 object-key convention."""
    from ..sources.r2_product_images import R2ProductImageSettings, R2ProductImageStore

    settings = R2ProductImageSettings.from_env()
    if settings is None:
        raise ImageProviderError("R2_PRODUCT_IMAGES_NOT_CONFIGURED")
    image_store = store or R2ProductImageStore(settings, cache_dir=cache_dir)
    try:
        return image_store.load(article_id)
    except ValueError:
        raise ImageProviderError("R2_ARTICLE_ID_INVALID") from None
    except Exception:
        raise ImageProviderError("R2_PRODUCT_IMAGE_UNAVAILABLE") from None
