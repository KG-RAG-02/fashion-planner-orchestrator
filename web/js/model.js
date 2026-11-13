// Pure view-model helpers for the VOGUE Atelier workspace.
// No DOM access here, so every rule (null is not zero, producer status is never
// rewritten, links are http(s) only) is unit-tested with `node --test web/tests`.

export const STAGES = Object.freeze([
  { key: 'trend', n: '01', en: 'Trend', ko: '트렌드 분석' },
  { key: 'stock', n: '02', en: 'Stock', ko: '재고 매칭' },
  { key: 'planning_draft', n: '03', en: 'Draft', ko: '기획 초안' },
  { key: 'sales_market_review', n: '04', en: 'Review', ko: '시장 검토' },
  { key: 'integration', n: '05', en: 'Integrate', ko: '통합' },
  { key: 'final_image', n: '06', en: 'Image', ko: '최종 이미지' },
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
export const MAX_IMAGE_BYTES = 10485760;
export const IMAGE_TYPES = Object.freeze(['image/png', 'image/jpeg', 'image/webp']);

export function validateBrief(text) {
  const trimmed = (text ?? '').trim();
  if (!trimmed) return { ok: false, message: '기획 질의를 입력해 주세요.' };
  if (trimmed.length > MAX_BRIEF) return { ok: false, message: `질의는 ${MAX_BRIEF.toLocaleString('ko-KR')}자 이하로 입력해 주세요.` };
  return { ok: true, text: trimmed };
}

export function validateImage(file) {
  if (!file) return { ok: true };
  if (!IMAGE_TYPES.includes(file.type)) return { ok: false, message: 'PNG · JPEG · WebP 이미지만 올릴 수 있습니다.' };
  if (file.size > MAX_IMAGE_BYTES) return { ok: false, message: '이미지는 10MB 이하여야 합니다.' };
  return { ok: true };
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

export function natureMeta(dataNature, synthetic) {
  if (synthetic === true || dataNature === 'synthetic_fixture') return { label: 'synthetic', tone: 'synthetic' };
  if (dataNature === 'sample') return { label: 'sample', tone: 'synthetic' };
  if (dataNature === 'historical_external') return { label: 'historical', tone: 'historical' };
  if (dataNature === 'current_internal') return { label: 'current', tone: 'ok' };
  if (dataNature === 'user_provided') return { label: 'user_provided', tone: 'neutral' };
  return { label: dataNature || 'unknown', tone: 'none' };
}

export function observationView(observation) {
  const obs = observation ?? {};
  return {
    asOf: obs.as_of ?? null,
    observedAt: obs.observed_at ?? null,
    source: obs.source ?? null,
    nature: natureMeta(obs.data_nature, obs.synthetic),
    limitations: Array.isArray(obs.limitations) ? obs.limitations : [],
  };
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
      // Vogue example images are links only (G-10): never downloaded or embedded.
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
  return {
    title: draft.title ?? '제목 없음',
    category: draft.category ?? null,
    season: draft.season ?? null,
    claims: (draft.claims ?? []).map(claimView),
    openQuestions: draft.open_questions ?? [],
    mdRequired: Array.isArray(draft.md_decision_required) ? draft.md_decision_required : [],
    limitations: unique([...(draft.limitations ?? []), ...(draftResult.limitations ?? [])]),
    identity: draftResult.draft_identity ?? null,
  };
}

const METRIC_LABELS = Object.freeze({
  current_company_sales: '현재 자사 판매',
});

function formatNumber(value, unit, currency) {
  const number = Number(value).toLocaleString('ko-KR');
  const suffix = currency ? ` ${currency}` : unit && unit !== 'pieces' ? ` ${unit}` : unit === 'pieces' ? '개' : '';
  return `${number}${suffix}`;
}

export function metricView(metric) {
  const missing = metric?.value === null || metric?.value === undefined;
  const obs = observationView(metric?.observation);
  let value = '미확인';
  if (!missing) value = typeof metric.value === 'number' ? formatNumber(metric.value, metric.unit, metric.currency) : String(metric.value);
  const period = metric?.period && typeof metric.period === 'object'
    ? [metric.period.start, metric.period.end].filter(Boolean).join(' – ')
    : (metric?.period ?? '');
  return {
    label: METRIC_LABELS[metric?.name] ?? metric?.name ?? metric?.metric_id ?? '지표',
    value,
    missing,
    reason: missing ? (metric?.missing_reason ?? '원천 없음') : '',
    period,
    nature: obs.nature,
    asOf: obs.asOf,
    source: obs.source,
  };
}

export function reviewView(reviewResult) {
  const review = reviewResult?.review;
  if (!review) return null;
  return {
    findings: (review.findings ?? []).map(claimView),
    metrics: (review.metrics ?? []).map(metricView),
    limitations: unique([...(review.limitations ?? []), ...(reviewResult.limitations ?? [])]),
    observation: observationView(review.observation),
    identity: reviewResult.review_identity ?? null,
  };
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
