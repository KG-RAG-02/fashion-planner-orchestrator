// Pure view-model helpers for the VOGUE Atelier workspace.
// No DOM access here, so every rule (null is not zero, producer status is never
// rewritten, links are http(s) only) is unit-tested with `node --test web/tests`.

export const STAGES = Object.freeze([
  { key: 'trend', n: '01', en: 'Trend', ko: '트렌드 분석' },
  { key: 'stock', n: '02', en: 'Stock', ko: '재고 매칭' },
  { key: 'planning_draft', n: '03', en: 'Draft', ko: '기획 초안' },
  { key: 'sales_market_review', n: '04', en: 'Review', ko: '시장 검토' },
  { key: 'integration', n: '05', en: 'Integrate', ko: '통합' },
  { key: 'final_image', n: '06', en: 'Sheet', ko: '기획안 사진' },
]);

const STATUS = Object.freeze({
  ok: { tone: 'ok', label: '완료 · ok' },
  partial: { tone: 'partial', label: '부분 · partial' },
  no_results: { tone: 'none', label: '결과 없음 · no_results' },
  error: { tone: 'error', label: '오류 · error' },
  running: { tone: 'running', label: '진행 중' },
  pending: { tone: 'pending', label: '대기' },
  skipped: { tone: 'pending', label: '생략' },
  generated: { tone: 'ok', label: '생성됨' },
  failed: { tone: 'error', label: '실패' },
});

export function statusMeta(status) {
  return STATUS[status] ?? { tone: 'pending', label: status ? String(status) : '미확인' };
}

export const MAX_BRIEF = 8000;

export function validateBrief(text) {
  const trimmed = (text ?? '').trim();
  if (!trimmed) return { ok: false, message: '기획 질의를 입력해 주세요.' };
  if (trimmed.length > MAX_BRIEF) return { ok: false, message: `질의는 ${MAX_BRIEF.toLocaleString('ko-KR')}자 이하로 입력해 주세요.` };
  return { ok: true, text: trimmed };
}

export function newRequestId(cryptoObject = globalThis.crypto) {
  return `req-${cryptoObject.randomUUID()}`;
}

export function shortSha(sha) {
  return typeof sha === 'string' && /^[a-f0-9]{8,}$/.test(sha) ? sha.slice(0, 8) : '—';
}

export function safeUrl(value) {
  if (typeof value !== 'string' || !value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

function unique(values) {
  return [...new Set(values.filter((v) => v !== null && v !== undefined))];
}

function seasonLabel(season) {
  if (typeof season === 'string') return season;
  if (season && season.year) return `${season.year} ${season.season ?? ''}`.trim();
  return '';
}

// Six-step rail. While a run is in flight the first stage without a trace is
// "running"; after a terminal response, stages that never ran are "skipped".
export function rail(result, { inFlight = false } = {}) {
  const traced = new Map((result?.stages ?? []).map((stage) => [stage.stage, stage]));
  let runningAssigned = false;
  return STAGES.map((def) => {
    let status;
    if (def.key === 'final_image' && result?.final_image?.status && !inFlight) {
      status = result.final_image.status;
    } else if (traced.has(def.key)) {
      status = traced.get(def.key).status;
    } else if (inFlight && !runningAssigned) {
      status = 'running';
      runningAssigned = true;
    } else {
      status = result && !inFlight ? 'skipped' : 'pending';
    }
    return { ...def, status, ...statusMeta(status) };
  });
}

// --- Trend -----------------------------------------------------------------

export function trendCards(trendResult) {
  if (!trendResult || !Array.isArray(trendResult.trends)) return [];
  const evidence = new Map((trendResult.evidence ?? []).map((e) => [e.evidence_id, e]));
  return trendResult.trends.map((trend, index) => {
    const style = trend.style ?? {};
    const items = trend.items ?? [];
    const ids = unique([...(style.focus_evidence_ids ?? []), ...items.flatMap((item) => item.evidence_ids ?? [])]);
    const quotes = ids.map((id) => evidence.get(id)).filter(Boolean).slice(0, 2).map((e) => ({
      id: e.evidence_id, quote: e.quote ?? '', title: e.title ?? '', url: safeUrl(e.url), date: e.published_at ?? null,
    }));
    return {
      rank: String(index + 1).padStart(2, '0'),
      name: style.name ?? '이름 없음',
      definition: style.definition ?? '',
      seasons: (style.seasons ?? []).map(seasonLabel).filter(Boolean).join(' · '),
      items: unique(items.map((item) => item.name)),
      quotes,
      // The UI shows Vogue example images as links; only the planning poster (stage 06) downloads and embeds them.
      exampleLinks: (style.example_images ?? []).slice(0, 5)
        .map((image) => ({ url: safeUrl(image.url), basis: image.basis ?? '' }))
        .filter((image) => image.url),
    };
  });
}

// --- Stock -----------------------------------------------------------------

const IMAGE_MEDIA = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);

export function productView(product) {
  if (!product) return null;
  const inventory = product.inventory ?? {};
  const attributes = product.attributes ?? {};
  const image = product.image && IMAGE_MEDIA.has(product.image.media_type) && product.image.encoding === 'base64'
    ? { src: `data:${product.image.media_type};base64,${product.image.data}`, source: product.image.source ?? '' }
    : null;
  return {
    category: product.category ?? '미확인',
    season: product.season ?? '미확인',
    color: attributes.color ?? null,
    materials: Array.isArray(attributes.material) ? attributes.material : [],
    fit: attributes.fit ?? null,
    quantity: Number.isInteger(inventory.quantity) ? inventory.quantity : null,
    asOf: inventory.as_of ?? null,
    dataNature: inventory.data_nature ?? null,
    skuCount: Array.isArray(product.sku_ids) ? product.sku_ids.length : 0,
    image,
  };
}

export function stockSlots(stockResult) {
  if (!stockResult || !Array.isArray(stockResult.trend_results)) return [];
  const products = stockResult.products ?? [];
  return stockResult.trend_results.map((slot, index) => {
    const productIndex = Number.isInteger(slot.product_index) ? slot.product_index : index;
    const product = productView(products[productIndex] ?? null);
    return {
      styleName: slot.style?.name ?? `스타일 ${index + 1}`,
      status: slot.status,
      summary: slot.summary ?? '',
      reason: typeof slot.reason?.llm_reason === 'string' ? slot.reason.llm_reason : null,
      product,
    };
  });
}

// --- Draft / Review --------------------------------------------------------

const CLAIM_KIND = Object.freeze({ fact: '근거', interpretation: '해석', proposal: '제안' });

export function claimView(claim) {
  return {
    kind: CLAIM_KIND[claim?.kind] ?? (claim?.kind || '주장'),
    text: claim?.text ?? '',
    refs: Array.isArray(claim?.evidence_refs) ? claim.evidence_refs.length : 0,
  };
}

export function draftView(draftResult) {
  const draft = draftResult?.draft;
  if (!draft) return null;
  const sourceFields = [
    ['product_profile', '제품 프로필', draft.product_profile],
    ['attributes', '속성 및 제안', draft.attributes],
    ['field_origins', '필드 출처', draft.field_origins],
    ['input_conflicts', '입력 충돌', draft.input_conflicts],
    ['input_statuses', '상류 입력 상태', draft.input_statuses ?? draftResult.input_statuses],
    ['planning_analysis', '기획 분석', draftResult.planning_analysis],
    ['observations', '관측값', draftResult.observations ?? draft.observations],
    ['proposals', '제안', draftResult.proposals ?? draft.proposals],
    ['questions_for_validation', '검증 질문', draftResult.questions_for_validation ?? draft.questions_for_validation],
    ['conflicts', '충돌', draftResult.conflicts],
    ['validation_issues', '검증 이슈', draftResult.validation_issues],
    ['generation_metadata', '생성 정보', draftResult.generation_metadata],
    ['compatibility', '입력 호환성 정보', draftResult.compatibility],
  ].filter(([, , value]) => value !== undefined && value !== null)
    .map(([key, label, value]) => ({ key, label, value }));
  return {
    status: draftResult.status ?? null,
    contractVersion: draftResult.schema_version ?? null,
    title: draft.title ?? '제목 없음',
    category: draft.category ?? null,
    season: draft.season ?? null,
    claims: (draft.claims ?? []).map(claimView),
    openQuestions: draft.open_questions ?? [],
    mdRequired: Array.isArray(draft.md_decision_required) ? draft.md_decision_required : [],
    limitations: unique([...(draft.limitations ?? []), ...(draftResult.limitations ?? [])]),
    identity: draftResult.draft_identity ?? null,
    visuals: draftVisuals(draft.visuals),
    imageCalls: Number.isInteger(draftResult.image_generation_calls) ? draftResult.image_generation_calls : null,
    concept: typeof draft.concept?.product_definition?.value === 'string' ? draft.concept.product_definition.value : null,
    errors: Array.isArray(draftResult.errors) ? draftResult.errors : [],
    sections: sourceFields,
  };
}

// workflow-contract 0.2.0: P06 returns the product image and the worn (outfit) image with the Draft.
const DRAFT_VISUALS = Object.freeze([['product', '신상품'], ['outfit', '착장']]);

export function draftVisuals(visuals) {
  if (!visuals || typeof visuals !== 'object') return [];
  return DRAFT_VISUALS.map(([key, label]) => {
    const item = visuals[key] ?? {};
    const sha = typeof item.artifact?.sha256 === 'string' && /^[0-9a-f]{64}$/.test(item.artifact.sha256) ? item.artifact.sha256 : null;
    return { key, label, status: item.status ?? null, reason: item.reason ?? '', model: item.model ?? null,
      sha256: item.status === 'generated' ? sha : null };
  });
}

export function reviewView(reviewResult, callRequest = null, callRequestArtifactId = null, callRequestState = 'unavailable', p06Result = null) {
  // P07's current native response is a partial-capable, analysis-oriented object.
  if (!reviewResult || typeof reviewResult !== 'object') return null;
  const result = reviewResult.result && typeof reviewResult.result === 'object' ? reviewResult.result : reviewResult;
  if (!result.schema_version && !result.decision_support && !result.price_analysis && !result.target_audience) return null;
  const sections = [
    ['decision_support', '의사결정 지원'],
    ['price_analysis', '가격 분석'],
    ['category_price_comparison', '카테고리 가격 비교'],
    ['fabric_cost_analysis', '원단 원가 분석'],
    ['material_cost_analysis', '소재 원가 분석'],
    ['margin_analysis', '마진 분석'],
    ['margin_estimate', '마진 참고치'],
    ['margin_price_range_analysis', '마진·가격 범위 분석'],
    ['customer_segment_analysis', '고객 세그먼트 분석'],
    ['target_audience_analysis', '대상 고객 분석'],
    ['target_audience', '대상 고객'],
    ['draft_context', '검토 대상 상품 정보'],
    ['evidence', '근거'],
  ].filter(([key]) => result[key] !== undefined && result[key] !== null)
    .map(([key, label]) => ({ key, label, value: result[key] }));
  const product = callRequest?.input?.product && typeof callRequest.input.product === 'object' && !Array.isArray(callRequest.input.product)
    ? callRequest.input.product : null;
  // Under 0.3 P08 sends each field's provenance with the request (input.product_evidence); a run whose
  // request carries none shows the provenance read from the P06 Draft instead.
  const sentEvidence = callRequest?.input?.product_evidence && typeof callRequest.input.product_evidence === 'object'
    && !Array.isArray(callRequest.input.product_evidence) ? callRequest.input.product_evidence : null;
  const productEvidence = sentEvidence ?? p06ProductProvenance(p06Result, product);
  const fieldLabels = Object.freeze({
    category: '카테고리', product_type: '상품 종류', gender: '성별', season: '시즌', material: '소재',
  });
  const kindLabels = Object.freeze({
    user_requested: '사용자 요청', evidence_observation: '근거 관측', proposal: '제안',
    hypothesis: '가설', to_validate: '검증 필요', unknown: '미확인',
  });
  const inputFields = product ? Object.entries(fieldLabels)
    .filter(([key]) => Object.prototype.hasOwnProperty.call(product, key))
    .map(([key, label]) => {
      const evidence = productEvidence[key] && typeof productEvidence[key] === 'object' ? productEvidence[key] : {};
      return {
        key, label, value: product[key] === null ? '미지정' : String(product[key]),
        kind: evidence.kind ?? null,
        kindLabel: kindLabels[evidence.kind] ?? '미전달',
        sourceRef: typeof evidence.source_ref === 'string' ? evidence.source_ref : null,
        evidenceIds: Array.isArray(evidence.evidence_ids) ? evidence.evidence_ids.filter((id) => typeof id === 'string') : [],
      };
    }) : [];
  return {
    format: 'native',
    contractVersion: reviewResult.contract_version ?? result.schema_version ?? null,
    status: reviewResult.status ?? result.status ?? null,
    sections,
    inputContractVersion: callRequest?.contract_version ?? null,
    inputRequestId: callRequest?.request_id ?? callRequest?.input?.request_id ?? null,
    inputFields,
    inputEvidenceState: callRequest ? (Object.keys(productEvidence).length ? 'loaded' : 'missing') : callRequestState,
    inputEvidenceSent: Boolean(sentEvidence),
    callRequestArtifactId,
    errors: Array.isArray(result.errors) ? result.errors : [],
    missingData: Array.isArray(result.missing_data) ? result.missing_data : [],
    limitations: unique([...(Array.isArray(result.limitations) ? result.limitations : []), ...(Array.isArray(reviewResult.limitations) ? reviewResult.limitations : [])]),
  };
}

function p06ProductProvenance(p06Result, product) {
  const draft = p06Result?.draft;
  if (!draft || !product) return {};
  const profile = draft.product_profile && typeof draft.product_profile === 'object' ? draft.product_profile : {};
  const origins = draft.field_origins && typeof draft.field_origins === 'object' ? draft.field_origins : {};
  const evidenceIds = new Set(Array.isArray(p06Result.evidence_ids) ? p06Result.evidence_ids.filter((id) => typeof id === 'string') : []);
  const result = {};
  for (const key of ['category', 'product_type', 'gender', 'season', 'material']) {
    if (!Object.hasOwn(product, key)) continue;
    let value, metadata, sourceRef;
    const claim = profile[key];
    if (claim && typeof claim === 'object' && typeof claim.value === 'string' && claim.value.trim()) {
      value = claim.value.trim(); metadata = claim; sourceRef = `p06.draft.product_profile.${key}`;
    } else if (typeof draft[key] === 'string' && draft[key].trim()) {
      value = draft[key].trim(); metadata = {}; sourceRef = `p06.draft.${key}`;
    }
    if (value !== product[key]) continue;
    const ids = Array.isArray(metadata?.evidence_ids) ? [...new Set(metadata.evidence_ids.filter((id) => typeof id === 'string' && evidenceIds.has(id)))] : [];
    let kind = metadata?.kind ?? (origins[key] === 'user' ? 'user_requested' : 'unknown');
    if (!['user_requested', 'evidence_observation', 'proposal', 'hypothesis', 'to_validate', 'unknown'].includes(kind)) kind = 'unknown';
    result[key] = { kind, source_ref: sourceRef, evidence_ids: ids };
  }
  return result;
}

// --- Integrated envelope ---------------------------------------------------

export function sourceResult(result, key) {
  return result?.sources?.[key]?.raw_response?.result ?? null;
}

export function runSummary(envelope) {
  const result = envelope?.result ?? {};
  const image = result.final_image ?? {};
  const imageCalls = Number.isInteger(image.provider_calls) ? image.provider_calls : null;
  return {
    requestId: result.request_id ?? envelope?.request_id ?? null,
    status: result.status ?? envelope?.status ?? null,
    contract: envelope?.contract_version ?? null,
    image: { status: image.status ?? null, reason: image.reason ?? '', calls: imageCalls },
    limitations: unique(result.limitations ?? []),
    errors: (result.errors ?? []).map((error) => `${error.stage ?? '-'} · ${error.code ?? 'ERROR'}`),
    mdReview: result.md_review ?? null,
  };
}

// 기획서 PNG (GET /api/workflow/runs/{id}/planning-sheet.png): offered only for a finished run.
const TERMINAL = new Set(['ok', 'partial', 'no_results', 'error']);
const RUN_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;

export function planningSheetLink(summary, { inFlight = false } = {}) {
  const id = summary?.requestId;
  if (inFlight || typeof id !== 'string' || !RUN_ID.test(id) || !TERMINAL.has(summary?.status)) return null;
  return {
    href: `/api/workflow/runs/${encodeURIComponent(id)}/planning-sheet.png`,
    filename: `vogue-planning-sheet-${id}.png`,
  };
}

export function lineageRows(result) {
  return (result?.stages ?? []).map((stage) => ({
    n: String(stage.sequence ?? '').padStart(2, '0'),
    stage: stage.stage,
    status: stage.status,
    input: shortSha(stage.input_sha256),
    response: shortSha(stage.response_sha256),
    started: stage.started_at ?? '',
    completed: stage.completed_at ?? '',
  }));
}

// --- Errors ----------------------------------------------------------------

const CODES = Object.freeze({
  CONTRACT_NOT_FROZEN: '워크플로 계약이 아직 동결되지 않아 실행할 수 없습니다.',
  UPSTREAM_TIMEOUT: '하위 서비스 응답 시간이 초과되었습니다. 후속 단계는 실행하지 않았습니다.',
  UPSTREAM_UNAVAILABLE: '하위 서비스에 연결할 수 없습니다.',
  UPSTREAM_AUTH: '서비스 간 인증에 실패했습니다.',
  CONTRACT_VIOLATION: '하위 서비스 응답이 계약과 맞지 않아 중단했습니다.',
  ARTIFACT_READER_REQUIRED: '아티팩트 저장소 연결이 설정되지 않았습니다.',
  REQUEST_ID_INPUT_CONFLICT: '같은 요청 ID로 다른 입력이 들어왔습니다. 새 기획으로 다시 시작해 주세요.',
  STAGE_OUTCOME_UNKNOWN: '이전 실행의 단계 결과를 확인할 수 없습니다. 실행 이력을 확인해 주세요.',
  IMAGE_RETRY_DISABLED: '이 환경에서는 이미지 재시도가 비활성화되어 있습니다.',
});

export function problemMessage(body, httpStatus) {
  const error = body?.error;
  const code = typeof error === 'object' && error ? error.code : (typeof body?.code === 'string' ? body.code : null);
  const fallback = (typeof error === 'object' && error?.message) || (typeof error === 'string' && error) || body?.message || '요청을 완료하지 못했습니다.';
  const message = CODES[code] ?? fallback;
  const tag = [code, httpStatus].filter(Boolean).join(' · ');
  return tag ? `${message} [${tag}]` : message;
}
