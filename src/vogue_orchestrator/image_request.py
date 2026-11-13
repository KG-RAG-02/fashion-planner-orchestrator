"""Final-image input package: what P08 sends to the OpenAI Images API, built from the retained sources.

Decisions (2026-11-13, see docs/integration-io-change-report.md):
- values are sent verbatim; nothing is rewritten, paraphrased or truncated;
- the P07 review's raw metrics and evidence are excluded from the image request only (they stay in the
  retained P07 response, the run record and the planning sheet);
- evidence travels as ID codes (evidence_id, product_id); the full records stay in the stored request;
- exact duplicates are sent once (integration.claims repeats draft.claims, limitations repeat across
  stages); integrity metadata (hashes, artifact references, lineage, timestamps) stays in storage;
- product photos travel as real reference images, not as base64 text inside the prompt;
- sections are ordered core-first: request, draft, trend, stock, review, limitations
  (OpenAI image prompting guide: labelled sections, intended use and references first, constraints last).

The package never decides what the image looks like; it only orders and delivers the producers' values.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .workflows.image_generation import IMAGE_REFERENCE_MAX, compose_full_prompt

ADAPTER_VERSION = 'p08-image-input/1'
PROMPT_COMPONENT = 'reviewed_image'
# Trends carried per image request (decision 2026-11-13: first 3 in Trend response order, all verbatim).
MAX_TRENDS = 3
REFERENCE_MEDIA = frozenset({'image/png', 'image/jpeg', 'image/webp'})
MAX_REFERENCE_BYTES = 10_485_760
# Keys that carry integrity or transport metadata, not planning content. They stay in the stored request.
META_KEYS = frozenset({
    'content_sha256', 'source_response_sha256', 'source_request_sha256', 'source_payload_sha256',
    'draft_response_sha256', 'review_response_sha256', 'planning_request_sha256', 'trend_response_sha256',
    'stock_response_sha256', 'stock_input_sha256', 'stock_wire_request_sha256', 'integration_input_sha256',
    'raw_artifact', 'hash_profile', 'lineage', 'artifacts', 'stock_call_context', 'draft_identity',
    'review_identity', 'reviewed_draft', 'observed_at', 'fetched_at', 'source_pointer', 'input_provenance',
    'request_id', 'schema_version', 'agent_id',
})
REVIEW_EXCLUDED_NOTE = ('P07 원본 지표(metrics)·근거(evidence)는 이미지 요청에서 제외했습니다. '
                        '원문은 실행 기록에 보존된 P07 응답에 있습니다.')


class ImageInputError(ValueError):
    """Fails the image stage before any provider HTTP call; ``code`` is recorded as provider_error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReferenceImage:
    index: int
    role: str
    label: str
    media_type: str
    data: bytes
    sha256: str

    def meta(self) -> dict[str, Any]:
        return {'index': self.index, 'role': self.role, 'label': self.label, 'media_type': self.media_type,
                'sha256': self.sha256, 'size_bytes': len(self.data)}


@dataclass(frozen=True)
class ImageInput:
    sections: dict[str, Any]
    planning_json: str
    references: tuple[ReferenceImage, ...]
    system_prompt: str
    user_prompt: str
    full_prompt: str
    prompt_version: str
    provenance: dict[str, Any]
    stats: dict[str, Any]

    def provider_fields(self) -> dict[str, Any]:
        """JSON-safe fields handed to the provider callable (references carry their bytes as base64)."""
        return {'adapter_version': ADAPTER_VERSION, 'prompt_version': self.prompt_version,
                'system_prompt': self.system_prompt, 'user_prompt': self.user_prompt,
                'full_prompt_chars': len(self.full_prompt),
                'full_prompt_sha256': hashlib.sha256(self.full_prompt.encode()).hexdigest(),
                'references': [dict(ref.meta(), data_base64=base64.b64encode(ref.data).decode('ascii'))
                               for ref in self.references]}

    def record(self) -> dict[str, Any]:
        """What was sent, for the private request record (no image bytes; their sha256 identifies them)."""
        return {'adapter_version': ADAPTER_VERSION, 'prompt_version': self.prompt_version,
                'full_prompt': self.full_prompt, 'references': [ref.meta() for ref in self.references],
                'provenance': self.provenance, 'stats': self.stats}


# --- helpers -----------------------------------------------------------------------------------------

def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _without_meta(value: Any) -> Any:
    """Copy a producer value verbatim, minus integrity/transport metadata keys."""
    if isinstance(value, Mapping):
        return {key: _without_meta(item) for key, item in value.items() if key not in META_KEYS}
    if isinstance(value, (list, tuple)):
        return [_without_meta(item) for item in value]
    return value


def _codes(refs: Any) -> list[str]:
    return [ref['evidence_id'] for ref in _list(refs)
            if isinstance(ref, Mapping) and isinstance(ref.get('evidence_id'), str)]


def _claims(claims: Any) -> list[dict[str, Any]]:
    out = []
    for claim in _list(claims):
        if not isinstance(claim, Mapping):
            continue
        item = {'kind': claim.get('kind'), 'text': claim.get('text'), 'evidence': _codes(claim.get('evidence_refs'))}
        extra = {key: _without_meta(value) for key, value in claim.items()
                 if key not in {'kind', 'text', 'evidence_refs'} and key not in META_KEYS}
        out.append({**item, **extra})
    return out


class _Limitations:
    """Limitations in first-occurrence order, each string once (exact duplicates only)."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self.seen: set[str] = set()
        self.duplicates = 0

    def add(self, values: Any) -> None:
        for value in _list(values):
            if not isinstance(value, str):
                continue
            if value in self.seen:
                self.duplicates += 1
                continue
            self.seen.add(value)
            self.items.append(value)


class _References:
    def __init__(self) -> None:
        self.items: list[ReferenceImage] = []
        self.by_sha: dict[str, ReferenceImage] = {}

    def add(self, data: bytes, media_type: str, role: str, label: str) -> int:
        from .workflow_store import checked_image
        if media_type not in REFERENCE_MEDIA or not 0 < len(data) <= MAX_REFERENCE_BYTES:
            raise ImageInputError('IMAGE_REFERENCE_INVALID')
        try:
            checked_image(data, media_type)
        except ValueError:
            raise ImageInputError('IMAGE_REFERENCE_INVALID') from None
        sha = hashlib.sha256(data).hexdigest()
        if sha in self.by_sha:  # identical photo used by several products: send once, name every use
            existing = self.by_sha[sha]
            merged = ReferenceImage(existing.index, existing.role, existing.label + '; ' + label,
                                    existing.media_type, existing.data, existing.sha256)
            self.items[existing.index - 1] = merged
            self.by_sha[sha] = merged
            return existing.index
        if len(self.items) >= IMAGE_REFERENCE_MAX:
            raise ImageInputError('IMAGE_REFERENCE_LIMIT')
        ref = ReferenceImage(len(self.items) + 1, role, label, media_type, data, sha)
        self.items.append(ref)
        self.by_sha[sha] = ref
        return ref.index


@dataclass(frozen=True)
class _TrendScope:
    """Which trends the image request carries (Trend response order, first MAX_TRENDS)."""
    kept: tuple[Mapping[str, Any], ...]
    dropped: tuple[Mapping[str, Any], ...]
    kept_style_ids: frozenset
    kept_codes: frozenset
    dropped_codes: frozenset

    @property
    def scoped(self) -> bool:
        return bool(self.dropped)


def _trend_codes(entry: Mapping[str, Any], evidence: Sequence[Any]) -> set[str]:
    style = _mapping(entry.get('style'))
    codes = {*_list(style.get('focus_evidence_ids')),
             *(code for part in _list(style.get('description')) for code in _list(_mapping(part).get('evidence_ids'))),
             *(code for item in _list(entry.get('items')) for code in _list(_mapping(item).get('evidence_ids')))}
    article = style.get('article_id')
    if article is not None:
        codes |= {item['evidence_id'] for item in evidence if isinstance(item, Mapping)
                  and item.get('article_id') == article and isinstance(item.get('evidence_id'), str)}
    return {code for code in codes if isinstance(code, str)}


def _trend_scope(trend: Mapping[str, Any]) -> _TrendScope:
    entries = [_mapping(entry) for entry in _list(trend.get('trends'))]
    evidence = _list(trend.get('evidence'))
    kept, dropped = entries[:MAX_TRENDS], entries[MAX_TRENDS:]
    kept_codes = set().union(*(_trend_codes(entry, evidence) for entry in kept)) if kept else set()
    dropped_codes = set().union(*(_trend_codes(entry, evidence) for entry in dropped)) - kept_codes if dropped else set()
    kept_ids = frozenset(_mapping(entry.get('style')).get('id') for entry in kept if _mapping(entry.get('style')).get('id'))
    return _TrendScope(tuple(kept), tuple(dropped), kept_ids, frozenset(kept_codes), frozenset(dropped_codes))


def _stock_section(stock: Mapping[str, Any], references: _References, limitations: _Limitations,
                   scope: _TrendScope) -> tuple[dict[str, Any], dict[str, int]]:
    section: dict[str, Any] = {}
    for key, value in stock.items():
        if key in META_KEYS or key in {'evidence', 'products', 'trend_results'}:
            continue
        section[key] = _without_meta(value)
    all_slots = [_mapping(slot) for slot in _list(stock.get('trend_results'))]
    slots = [slot for slot in all_slots
             if not scope.scoped or _mapping(slot.get('style')).get('id') in scope.kept_style_ids
             or not _mapping(slot.get('style')).get('id')]
    used_products = {slot['product_index'] for slot in slots if isinstance(slot.get('product_index'), int)}
    all_products = _list(stock.get('products'))
    products = []
    for index, product in enumerate(all_products):
        if scope.scoped and index not in used_products:
            continue
        product = _mapping(product)
        copy = {'product_index': index,
                **{key: _without_meta(value) for key, value in product.items() if key not in META_KEYS and key != 'image'}}
        image = _mapping(product.get('image'))
        if image:
            kept = {key: value for key, value in image.items() if key not in {'data', 'encoding'} and key not in META_KEYS}
            if image.get('encoding') == 'base64' and isinstance(image.get('data'), str):
                try:
                    data = base64.b64decode(image['data'], validate=True)
                except (binascii.Error, ValueError):
                    raise ImageInputError('IMAGE_REFERENCE_INVALID') from None
                label = ' · '.join(str(part) for part in (
                    f'Stock 매칭 재고 상품 products[{index}]', product.get('category'), product.get('style_id'),
                    f"출처 {image['source']}" if image.get('source') else None) if part)
                kept['reference_image'] = references.add(data, str(image.get('media_type')), 'stock_product', label)
            copy['image'] = kept
        products.append(copy)
    section['products'] = products
    for slot in slots:
        limitations.add(slot.get('limitations'))
    section['trend_results'] = [{key: _without_meta(value) for key, value in slot.items()
                                 if key not in META_KEYS and key != 'limitations'} for slot in slots]
    stock_codes = [item['evidence_id'] for item in _list(stock.get('evidence'))
                   if isinstance(item, Mapping) and isinstance(item.get('evidence_id'), str)]
    if scope.scoped:
        used_codes = {code for slot in slots for code in _list(slot.get('evidence_ids'))}
        stock_codes = [code for code in stock_codes if code in used_codes]
    section['evidence_codes'] = stock_codes
    return section, {'stock_trend_results': len(all_slots) - len(slots), 'stock_products': len(all_products) - len(products)}


def _trend_section(trend: Mapping[str, Any], scope: _TrendScope) -> dict[str, Any]:
    section: dict[str, Any] = {}
    for key, value in trend.items():
        if key in META_KEYS or key in {'evidence', 'limitations', 'trends'}:
            continue
        section[key] = _without_meta(value)
    trends = []
    for entry in scope.kept:
        copy = {key: _without_meta(value) for key, value in entry.items() if key not in META_KEYS and key != 'items'}
        items = []
        for item in _list(entry.get('items')):
            item = _mapping(item)
            row = {key: _without_meta(value) for key, value in item.items() if key not in META_KEYS and key != 'similar_products'}
            if 'similar_products' in item:
                row['similar_product_codes'] = [product.get('product_id') for product in _list(item.get('similar_products'))
                                                if isinstance(product, Mapping) and product.get('product_id')]
            items.append(row)
        copy['items'] = items
        trends.append(copy)
    section['trends'] = trends
    codes = [item['evidence_id'] for item in _list(trend.get('evidence'))
             if isinstance(item, Mapping) and isinstance(item.get('evidence_id'), str)]
    section['evidence_codes'] = [code for code in codes if code in scope.kept_codes] if scope.scoped else codes
    if scope.scoped:
        total = len(scope.kept) + len(scope.dropped)
        section['scope'] = (f'Trend 응답의 트렌드 {total}개 중 앞의 {len(scope.kept)}개만 담았습니다. '
                            f'나머지 {len(scope.dropped)}개는 이미지 요청에서 제외했습니다(이름·코드는 실행 기록에 있음).')
    return section


def _reference_lines(references: Sequence[ReferenceImage]) -> str:
    if not references:
        return '- 없음: 텍스트 데이터만 사용합니다.'
    return '\n'.join(f'- 참고 이미지 {ref.index}: {ref.label}' for ref in references)


def build_image_input(image_request: Mapping[str, Any], *,
                      user_images: Sequence[tuple[bytes, str]] = ()) -> ImageInput:
    """Order and deliver the retained producer values for the final image (no summarising)."""
    from .domain.prompt_registry import load_prompt, prompt_version_id, render_prompt

    request = _mapping(image_request)
    draft_source = _mapping(request.get('planning_draft'))
    review_source = _mapping(request.get('sales_market_review'))
    draft_body = _mapping(_mapping(draft_source.get('raw_response')).get('result'))
    draft = _mapping(draft_body.get('draft'))
    inputs = _mapping(_mapping(draft_body.get('source_request')).get('input'))
    trend_retained, stock_retained = _mapping(inputs.get('trend_response')), _mapping(inputs.get('stock_response'))
    trend = _mapping(_mapping(trend_retained.get('raw_response')).get('result'))
    stock = _mapping(_mapping(stock_retained.get('raw_response')).get('result'))
    review_raw = _mapping(review_source.get('raw_response'))
    review_body = _mapping(review_raw.get('result'))
    review = _mapping(review_body.get('review'))

    scope = _trend_scope(trend)
    limitations, references = _Limitations(), _References()
    limitations.add(trend.get('limitations'))
    stock_section, stock_excluded = _stock_section(stock, references, limitations, scope)
    for index, (data, media_type) in enumerate(user_images):
        references.add(data, media_type, 'user_reference', f'사용자 업로드 참고 이미지 {index + 1} · {media_type} · 근거가 아닌 참고 자료')

    claims = _claims(draft.get('claims'))
    if scope.scoped:  # a fact that only cites excluded trends' evidence leaves with its trend
        kept_claims = [claim for claim in claims if not (claim.get('kind') == 'fact' and claim['evidence']
                                                         and set(claim['evidence']) <= scope.dropped_codes)]
    else:
        kept_claims = claims
    handled = {'title', 'category', 'season', 'claims', 'limitations', 'open_questions', 'md_decision_required'}
    draft_section: dict[str, Any] = {key: draft[key] for key in ('title', 'category', 'season') if key in draft}
    if 'status' in draft_body:
        draft_section['status'] = draft_body['status']
    draft_section['claims'] = kept_claims
    for key in ('open_questions', 'md_decision_required'):
        if key in draft:
            draft_section[key] = draft[key]
    draft_section.update({key: _without_meta(value) for key, value in draft.items() if key not in handled and key not in META_KEYS})
    limitations.add(draft.get('limitations'))
    limitations.add(draft_body.get('limitations'))

    review_section: dict[str, Any] = {}
    if review_source:
        review_section = {'status': review_raw.get('status', review_body.get('status')),
                          'findings': _claims(review.get('findings')),
                          'observation': _without_meta(review.get('observation')),
                          'excluded': REVIEW_EXCLUDED_NOTE}
        review_section.update({key: _without_meta(value) for key, value in review.items()
                               if key not in {'findings', 'observation', 'metrics', 'evidence', 'limitations',
                                              'draft_response_sha256'} and key not in META_KEYS})
        limitations.add(review.get('limitations'))
        limitations.add(review_body.get('limitations'))
    limitations.add(_mapping(request.get('integration')).get('limitations'))
    # integration.claims is draft.claims + review.findings (workflow.py); only claims found nowhere else are sent.
    known = {json.dumps(claim, sort_keys=True, ensure_ascii=False) for claim in (*_list(draft.get('claims')), *_list(review.get('findings')))}
    integration_only = [claim for claim in _list(_mapping(request.get('integration')).get('claims'))
                        if json.dumps(claim, sort_keys=True, ensure_ascii=False) not in known]
    if integration_only:
        draft_section['integration_claims'] = _claims(integration_only)

    sections = {
        '1_request': _without_meta(inputs.get('planning_request')) if inputs.get('planning_request') is not None else None,
        '2_draft': draft_section,
        '3_trend': _trend_section(trend, scope),
        '4_stock': stock_section,
        '5_review': review_section,
        '6_limitations': limitations.items,
    }
    # 1_request keeps the planning request's own id: it is the run's request id, not a hash.
    planning_request = _mapping(inputs.get('planning_request'))
    if planning_request.get('request_id') and isinstance(sections['1_request'], dict):
        sections['1_request'] = {'request_id': planning_request['request_id'], **sections['1_request']}
    planning_json = json.dumps(sections, ensure_ascii=False, separators=(',', ':'))
    system_prompt = load_prompt(PROMPT_COMPONENT, 'system')
    user_prompt = render_prompt(PROMPT_COMPONENT, 'user', {
        'REFERENCE_IMAGES': _reference_lines(references.items), 'PLANNING_DATA_JSON': planning_json})
    full_prompt = compose_full_prompt(system_prompt, user_prompt)
    metrics, evidence = _list(review.get('metrics')), _list(review.get('evidence'))
    provenance = {
        'adapter_version': ADAPTER_VERSION,
        'source_response_sha256': {
            'planning_draft': draft_source.get('content_sha256'), 'sales_market_review': review_source.get('content_sha256'),
            'trend': trend_retained.get('content_sha256'), 'stock': stock_retained.get('content_sha256')},
        'excluded': {
            'sales_market_review.review.metrics': len(metrics),
            'sales_market_review.review.evidence': len(evidence),
            'integration.claims': len(_list(_mapping(request.get('integration')).get('claims'))) - len(integration_only),
            'duplicate_limitations': limitations.duplicates,
            'metadata_keys': sorted(META_KEYS),
            'trends_after_limit': [{'name': _mapping(entry.get('style')).get('name'), 'id': _mapping(entry.get('style')).get('id'),
                                    'evidence_codes': sorted(_trend_codes(entry, _list(trend.get('evidence'))))}
                                   for entry in scope.dropped],
            'draft_claims_of_excluded_trends': len(claims) - len(kept_claims),
            **stock_excluded,
        },
        'trend_limit': MAX_TRENDS,
        'evidence': 'ID codes (evidence_id, product_id); full records stay in the retained responses',
    }
    stats = {'planning_json_chars': len(planning_json), 'full_prompt_chars': len(full_prompt),
             'sections': {name: len(json.dumps(value, ensure_ascii=False, separators=(',', ':'))) for name, value in sections.items()},
             'reference_images': len(references.items),
             'trends_total': len(scope.kept) + len(scope.dropped), 'trends_kept': len(scope.kept)}
    return ImageInput(sections=sections, planning_json=planning_json, references=tuple(references.items),
                      system_prompt=system_prompt, user_prompt=user_prompt, full_prompt=full_prompt,
                      prompt_version=prompt_version_id(PROMPT_COMPONENT), provenance=provenance, stats=stats)


# --- read-only diagnostics (python -m vogue_orchestrator.image_request --diagnostics ...) -------------

def diagnostics(store_dir: str, request_id: str) -> list[dict[str, Any]]:
    """Image-stage records of one run, read-only: prompt size, endpoint, usage, provider error. No secrets."""
    import sqlite3
    from .workflow_contract import VERSION, WorkflowContract
    from .workflow_store import digest

    database = sqlite3.connect(f'file:{store_dir}/checkpoints.sqlite3?mode=ro&immutable=1', uri=True)
    try:
        profile = WorkflowContract().profile_sha256
        out = []
        for attempt, value in database.execute('SELECT attempt, value FROM runs WHERE rid=? ORDER BY attempt', (request_id,)):
            if attempt != 'initial' and not attempt.startswith('image:'):
                continue
            image = _mapping(_mapping(json.loads(value).get('envelope')).get('result')).get('final_image') or {}
            entry = {'attempt': attempt, 'status': image.get('status'), 'reason': image.get('reason'),
                     'provider_calls': image.get('provider_calls')}
            input_sha = _mapping(image.get('lineage')).get('integration_input_sha256')
            if input_sha:
                key = digest(dict(request_id=request_id, stage='final_image', input_sha256=input_sha, contract_version=VERSION,
                                  contract_sha256=profile, attempt=attempt))
                for suffix, name in (('', 'checkpoint'), (':prompt', 'prompt')):
                    row = database.execute('SELECT state, value FROM checkpoints WHERE key=?', (key + suffix,)).fetchone()
                    if row:
                        entry[name] = {'state': row[0], **(json.loads(row[1]) if row[1] else {})}
            out.append(entry)
        return out
    finally:
        database.close()


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Final-image input package: measure a stored envelope or show image diagnostics.')
    parser.add_argument('--diagnostics', action='store_true', help='print the image-stage records of --request-id from --store-dir')
    parser.add_argument('--request-id')
    parser.add_argument('--store-dir', default='.local/workflow')
    parser.add_argument('--input', help='integrated envelope JSON: print the package statistics it would produce')
    args = parser.parse_args(argv)
    if args.diagnostics:
        if not args.request_id:
            parser.error('--diagnostics needs --request-id')
        json.dump(diagnostics(args.store_dir, args.request_id), sys.stdout, ensure_ascii=False, indent=1)
    elif args.input:
        envelope = json.loads(open(args.input, encoding='utf-8').read())
        envelope = envelope.get('envelope', envelope)
        result = _mapping(envelope.get('result'))
        sources = _mapping(result.get('sources'))
        package = build_image_input(dict(request_id=result.get('request_id'), integration=result.get('integration'),
                                         planning_draft=sources.get('planning_draft'), sales_market_review=sources.get('sales_market_review')))
        json.dump({'stats': package.stats, 'excluded': package.provenance['excluded'] | {'metadata_keys': len(META_KEYS)},
                   'references': [ref.meta() for ref in package.references]}, sys.stdout, ensure_ascii=False, indent=1)
    else:
        parser.error('use --diagnostics or --input')
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
