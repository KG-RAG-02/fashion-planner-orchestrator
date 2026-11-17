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

// The only photo origin the page's CSP admits (img-src); other URLs would be blocked, so they are not photos.
export const TREND_PHOTO_ORIGIN = 'https://img.vogue.co.kr';

export function trendPhotoUrl(value) {
  const url = safeUrl(value);
  return url && new URL(url).origin === TREND_PHOTO_ORIGIN ? url : null;
}

// Every item a style cites, in order, with its own article photos (items[].image_urls). Items with the same
// name are one tile; a tile without a usable photo is kept, so no cited item is dropped.
export function itemTiles(items) {
  const tiles = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const name = typeof item?.name === 'string' && item.name.trim() ? item.name.trim() : '이름 없는 아이템';
    const tile = tiles.get(name) ?? { name, photos: [] };
    for (const url of Array.isArray(item?.image_urls) ? item.image_urls : []) {
      const photo = trendPhotoUrl(url);
      if (photo && !tile.photos.includes(photo)) tile.photos.push(photo);
    }
    tiles.set(name, tile);
  }
  return [...tiles.values()];
}

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
      itemTiles: itemTiles(items),
      quotes,
      evidenceIds: ids.filter((id) => evidence.has(id)),
      // The style's example images stay links in the side panel; the cards show the items' own photos.
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
  // [key, label, preferred holder]: 'draft' reads result.draft first, 'result' reads the result first.
  const sourceFields = [
    ['product_profile', '제품 프로필', 'draft'],
    ['attributes', '속성 및 제안', 'draft'],
    ['field_origins', '필드 출처', 'draft'],
    ['input_conflicts', '입력 충돌', 'draft'],
    ['input_statuses', '상류 입력 상태', 'draft'],
    ['planning_analysis', '기획 분석', 'result'],
    ['observations', '관측값', 'result'],
    ['proposals', '제안', 'result'],
    ['questions_for_validation', '검증 질문', 'result'],
    ['conflicts', '충돌', 'result'],
    ['validation_issues', '검증 이슈', 'result'],
    ['generation_metadata', '생성 정보', 'result'],
    ['compatibility', '입력 호환성 정보', 'result'],
  ].map(([key, label, first]) => {
    const order = first === 'draft' ? [['draft', draft], ['result', draftResult]] : [['result', draftResult], ['draft', draft]];
    const hit = order.find(([, holder]) => holder[key] !== undefined && holder[key] !== null);
    return hit ? { key, label, value: hit[1][key], path: hit[0] === 'draft' ? `draft.${key}` : key } : null;
  }).filter(Boolean);
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
    // Where the lists live in the raw response, so a row can open them in the side panel.
    listPaths: {
      limitations: Array.isArray(draft.limitations) && draft.limitations.length ? 'draft.limitations' : 'limitations',
      errors: 'errors',
    },
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
    .map(([key, label]) => ({ key, label, value: result[key], path: result === reviewResult ? key : `result.${key}` }));
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
    listPaths: Object.fromEntries(['errors', 'missing_data', 'limitations'].map((key) => [key, result === reviewResult ? key : `result.${key}`])),
    missingData: Array.isArray(result.missing_data) ? result.missing_data : [],
    limitations: unique([...(Array.isArray(result.limitations) ? result.limitations : []), ...(Array.isArray(reviewResult.limitations) ? reviewResult.limitations : [])]),
  };
}

function marketNumber(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value.toLocaleString('ko-KR', { maximumFractionDigits: 2 });
}

function marketPrice(value, currency) {
  if (typeof value === 'string' && value.trim()) return value.trim();
  const shown = marketNumber(value);
  if (!shown) return null;
  return `${shown}${currency === 'KRW' ? '원' : currency === 'USD' ? ' USD' : currency ? ` ${currency}` : ''}`;
}

// P07 owns the source values. The workspace shows only a few decision-useful summaries;
// the complete response stays available in the JSON panel.
export function marketView(reviewResult) {
  if (!reviewResult || typeof reviewResult !== 'object') return null;
  const result = reviewResult.result && typeof reviewResult.result === 'object' ? reviewResult.result
    : reviewResult.review && typeof reviewResult.review === 'object' ? reviewResult.review : reviewResult;
  const sections = [];
  const price = result.price_analysis && typeof result.price_analysis === 'object' ? result.price_analysis : {};
  const priceRows = Array.isArray(result.category_price_comparison) ? result.category_price_comparison : [];
  const priceRow = priceRows.find((row) => row && typeof row === 'object') ?? {};
  const priceStats = priceRow.scaled_price_statistics ?? priceRow.price_statistics ?? {};
  const sampleSize = price.sample_size ?? priceRow.sample_count;
  const period = price.period ?? priceRow.period;
  const periodText = typeof period === 'string' ? period.replace('/', '–')
    : period && typeof period.start === 'string' ? `${period.start}${period.end && period.end !== period.start ? `–${period.end}` : ''}` : null;
  const proposedPrice = marketPrice(price.proposed_price, price.currency ?? price.unit);
  const historicalMedian = marketPrice(priceStats.median, priceStats.currency ?? priceStats.unit);
  if (proposedPrice || historicalMedian || sampleSize != null) {
    const note = [
      proposedPrice ? 'P07 연구용 제안' : 'H&M 과거 거래 참고',
      Number.isFinite(sampleSize) ? `표본 ${sampleSize.toLocaleString('ko-KR')}건` : null,
      periodText,
    ].filter(Boolean).join(' · ');
    sections.push({ label: proposedPrice ? '제안 판매가' : '과거 거래가 참고', value: proposedPrice ?? historicalMedian ?? '자료 없음', note });
  }

  const fabric = result.fabric_cost_analysis && typeof result.fabric_cost_analysis === 'object' ? result.fabric_cost_analysis : {};
  const materialRows = Array.isArray(result.material_cost_analysis) ? result.material_cost_analysis : [];
  const materialRow = materialRows.find((row) => row && typeof row === 'object') ?? {};
  const perMeter = materialRow.price_per_meter_krw_statistics ?? {};
  const listingStats = materialRow.listed_price_statistics ?? {};
  const unitCost = marketPrice(fabric.unit_cost, fabric.currency ?? fabric.unit);
  const materialMedian = marketPrice(perMeter.median ?? listingStats.median, perMeter.unit ?? perMeter.currency ?? listingStats.currency ?? materialRow.original_unit);
  const materialCount = materialRow.sample_count;
  if (unitCost || materialMedian || materialCount != null || fabric.status) {
    sections.push({
      label: '원단 가격 참고',
      value: unitCost ?? (materialMedian ? `중앙값 ${materialMedian}` : Number.isFinite(materialCount) ? `목록 ${materialCount.toLocaleString('ko-KR')}건` : '미제공'),
      note: unitCost ? 'P07 응답의 원단 단가' : [Number.isFinite(materialCount) ? `공개 목록 ${materialCount.toLocaleString('ko-KR')}건` : null, '생산 원가 미확정'].filter(Boolean).join(' · '),
    });
  }

  const margin = result.margin_analysis && typeof result.margin_analysis === 'object' ? result.margin_analysis : {};
  const marginValue = marketNumber(margin.margin_rate_percent);
  if (marginValue) {
    const benchmark = margin.estimate_type === 'industry_average_gross_margin_benchmark'
      || ['benchmark_only', 'benchmark_estimate'].includes(margin.status) || Boolean(margin.benchmark_source);
    const region = margin.benchmark_region === 'US' ? '미국' : margin.benchmark_region === 'KR' ? '한국'
      : margin.benchmark_region;
    sections.push({
      label: benchmark ? '업계 마진 참고치' : '마진율', value: `${marginValue}%`,
      note: benchmark ? `${region ? `${region} ` : ''}업계 평균 · 상품 실제 마진 아님` : null,
    });
  }

  const target = result.target_audience && typeof result.target_audience === 'object' ? result.target_audience : {};
  const targetAnalysis = result.target_audience_analysis && typeof result.target_audience_analysis === 'object'
    ? result.target_audience_analysis : {};
  const cohort = target.age_cohort ?? targetAnalysis.age_cohort ?? {};
  const cohortName = cohort.definition ?? cohort.age_band ?? null;
  const share = marketNumber(typeof cohort.share_of_customers_with_known_age === 'number'
    ? cohort.share_of_customers_with_known_age * 100 : null);
  const proposal = target.proposal && typeof target.proposal === 'object' ? target.proposal : {};
  const primarySegment = typeof proposal.primary_segment === 'string' ? proposal.primary_segment.trim() : '';
  if (cohortName || primarySegment) {
    sections.push({
      label: '고객 분석', value: cohortName ?? primarySegment,
      note: cohortName ? [share ? `연령 확인 고객 중 ${share}%` : null, 'H&M 과거 집계'].filter(Boolean).join(' · ')
        : 'P07 LLM 타깃 가설',
    });
  }
  const missingData = Array.isArray(result.missing_data) ? result.missing_data : [];
  const errors = Array.isArray(result.errors) ? result.errors : [];
  const limitations = Array.isArray(result.limitations) ? result.limitations.filter((item) => typeof item === 'string') : [];
  return {
    status: reviewResult.status ?? result.status ?? null,
    sections,
    missingData: missingData.map((item) => typeof item === 'string' ? item : item?.field ?? item?.reason ?? '자료 누락'),
    errors: errors.map((item) => typeof item === 'string' ? item : item?.code ?? 'P07 분석 오류'),
    limitations,
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

// --- One fact, one place -----------------------------------------------------
// The page prints readable summaries; evidence and JSON live only in the side panel, each once.

// The orchestrator's per-stage errors ({stage, code, message}), printed in that stage's section.
export function stageErrors(result, stageKeys) {
  const keys = new Set([stageKeys].flat());
  return (Array.isArray(result?.errors) ? result.errors : [])
    .filter((error) => error && keys.has(error.stage))
    .map((error) => ({ code: error.code ?? 'ERROR', message: typeof error.message === 'string' ? error.message : '' }));
}

// A producer's own error list, minus the codes its section already prints.
export function withoutShownErrors(errors, shown) {
  const codes = new Set((shown ?? []).map((error) => error.code));
  return (errors ?? []).filter((error) => !(error && typeof error === 'object' && codes.has(error.code)));
}

// Keeps the first occurrence of each item across several lists, in order.
export function firstOccurrences(lists) {
  const seen = new Set();
  return lists.map((list) => (list ?? []).filter((item) => {
    const key = typeof item === 'string' ? item : JSON.stringify(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }));
}

// Spine progress: a stage counts once it has a producer outcome.
const SETTLED_TONES = new Set(['ok', 'partial', 'none', 'error']);

export function railProgress(items) {
  const done = items.filter((item) => SETTLED_TONES.has(item.tone)).length;
  return { done, total: items.length, running: items.find((item) => item.tone === 'running') ?? null };
}

export function evidenceItem(evidence) {
  const e = evidence && typeof evidence === 'object' ? evidence : {};
  return {
    id: typeof e.evidence_id === 'string' ? e.evidence_id : null,
    text: e.quote ?? e.analysis?.interpretation ?? null,
    title: e.title ?? e.source_name ?? null,
    url: safeUrl(e.url),
    date: e.published_at ?? e.as_of ?? e.fetched_at ?? null,
    detail: typeof e.locator === 'string' ? e.locator : null,
    raw: e,
  };
}

// Side-panel evidence: trend evidence grouped under the first style that cites it, the trend evidence no
// style cites, then stock and P07 evidence. Every evidence record appears in exactly one group.
export function evidenceGroups(result) {
  const groups = [];
  const trend = sourceResult(result, 'trend');
  const records = new Map((Array.isArray(trend?.evidence) ? trend.evidence : []).map((e) => [e.evidence_id, e]));
  const shown = new Set();
  trendCards(trend).forEach((card, index) => {
    const ids = card.evidenceIds;
    const fresh = ids.filter((id) => !shown.has(id));
    fresh.forEach((id) => shown.add(id));
    groups.push({ anchor: `ev-trend-${index}`, stage: 'trend', title: card.name, items: fresh.map((id) => evidenceItem(records.get(id))),
      sharedCount: ids.length - fresh.length, exampleLinks: card.exampleLinks });
  });
  const rest = [...records.keys()].filter((id) => !shown.has(id));
  if (rest.length) groups.push({ anchor: 'ev-trend-other', stage: 'trend', title: '스타일에 연결되지 않은 트렌드 근거', items: rest.map((id) => evidenceItem(records.get(id))), sharedCount: 0, exampleLinks: [] });
  for (const [stage, title] of [['stock', '재고 근거'], ['sales_market_review', '시장 근거 (P07)']]) {
    const list = sourceResult(result, stage)?.evidence;
    if (Array.isArray(list) && list.length) groups.push({ anchor: `ev-${stage}`, stage, title, items: list.map(evidenceItem), sharedCount: 0, exampleLinks: [] });
  }
  // Different records can carry the same sentence (e.g. P07's interpretation); it is printed under the first.
  const texts = new Map();
  for (const group of groups) {
    group.items = group.items.map((item) => {
      if (!item.text) return item;
      if (texts.has(item.text)) return { ...item, text: null, sameTextAs: texts.get(item.text) };
      texts.set(item.text, item.id);
      return item;
    });
  }
  return groups;
}

// Side-panel JSON: a stage's raw result split into its top-level fields (Draft fields one level deeper),
// each with a stable anchor. `evidence` is printed by the evidence tab, so here it is only pointed to.
const EXPAND = Object.freeze({ planning_draft: 'draft', sales_market_review: 'result' });

export function jsonAnchor(stage, path) {
  return `json-${stage}-${String(path).replace(/[^A-Za-z0-9_.-]/g, '_')}`;
}

export function isEmptyValue(value) {
  return value === null || value === undefined || (Array.isArray(value) ? value.length === 0 : typeof value === 'object' && Object.keys(value).length === 0);
}

export function rawFields(result, stage) {
  const raw = result?.sources?.[stage]?.raw_response;
  if (!raw || typeof raw !== 'object') return [];
  const fields = [];
  const push = (path, value) => fields.push(path.endsWith('evidence') && Array.isArray(value)
    ? { path, anchor: jsonAnchor(stage, path), pointer: 'evidence', count: value.length }
    : { path, anchor: jsonAnchor(stage, path), value, empty: isEmptyValue(value) });
  // The response envelope (contract, producer, status) and the echoed request id are run bookkeeping, not content.
  const body = raw.result && typeof raw.result === 'object' ? raw.result : {};
  for (const [key, value] of Object.entries(body)) {
    if (key === 'request_id') continue;
    if (key === EXPAND[stage] && value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [sub, subValue] of Object.entries(value)) push(`${key}.${sub}`, subValue);
    } else push(key, value);
  }
  return fields;
}

// The panel's JSON tab: every stage's fields, where a value already printed above (producers copy lists such as
// evidence_ids or input_statuses between fields and stages) becomes a pointer to its first print.
export function panelJson(result) {
  const seen = new Map();
  return STAGES.filter((stage) => result?.sources?.[stage.key]).map((stage) => ({
    ...stage,
    fields: rawFields(result, stage.key).map((field) => {
      if (field.pointer || field.empty) return field;
      const text = JSON.stringify(field.value);
      if (text.length <= 40) return field;
      if (seen.has(text)) return { ...field, sameAs: seen.get(text) };
      seen.set(text, { path: `${stage.n} ${field.path}`, anchor: field.anchor });
      return field;
    }),
  }));
}

// Long encoded strings (base64 images) are summarised in the JSON view; the value itself is not changed.
export function compactJson(value, limit = 600) {
  return JSON.stringify(value, (key, item) => (typeof item === 'string' && item.length > limit && /^[A-Za-z0-9+/=\s]+$/.test(item.slice(0, 200))
    ? `…(${item.length.toLocaleString('ko-KR')}자 인코딩 데이터 생략)` : item), 2);
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
