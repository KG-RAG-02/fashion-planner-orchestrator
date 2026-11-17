// Unit tests for the workspace view-model. Run: node --test web/tests
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as m from '../js/model.js';

const fixture = (name) => JSON.parse(readFileSync(new URL(`../../tools/workflow_contract/fixtures/${name}.json`, import.meta.url), 'utf8'));

test('statusMeta maps producer statuses and never invents a known label for unknown values', () => {
  assert.equal(m.statusMeta('ok').tone, 'ok');
  assert.equal(m.statusMeta('no_results').label, '결과 없음 · no_results');
  assert.equal(m.statusMeta('generated').tone, 'ok');
  assert.deepEqual(m.statusMeta('weird'), { tone: 'pending', label: 'weird' });
  assert.equal(m.statusMeta(undefined).label, '미확인');
});

test('validateBrief trims, rejects blank and over-long text', () => {
  assert.equal(m.validateBrief('   ').ok, false);
  assert.equal(m.validateBrief('x'.repeat(8001)).ok, false);
  assert.deepEqual(m.validateBrief('  27SS 스커트  '), { ok: true, text: '27SS 스커트' });
});

test('newRequestId matches the common id grammar', () => {
  const id = m.newRequestId();
  assert.match(id, /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/);
  assert.ok(id.length <= 128);
});

test('safeUrl only allows http(s)', () => {
  assert.equal(m.safeUrl('javascript:alert(1)'), null);
  assert.equal(m.safeUrl('data:text/html,hi'), null);
  assert.equal(m.safeUrl('not a url'), null);
  assert.equal(m.safeUrl('https://www.vogue.co.kr/a'), 'https://www.vogue.co.kr/a');
});

test('rail marks the first untraced stage running while in flight', () => {
  const steps = m.rail({ stages: [{ stage: 'trend', status: 'ok' }, { stage: 'stock', status: 'partial' }] }, { inFlight: true });
  assert.deepEqual(steps.map((s) => s.status), ['ok', 'partial', 'running', 'pending', 'pending', 'pending']);
  assert.deepEqual(m.rail(null, { inFlight: true }).map((s) => s.status), ['running', 'pending', 'pending', 'pending', 'pending', 'pending']);
});

test('rail marks never-run stages skipped after a terminal stop, keeping producer status', () => {
  const steps = m.rail({ stages: [{ stage: 'trend', status: 'ok' }, { stage: 'stock', status: 'error' }], final_image: { status: 'skipped' } });
  assert.deepEqual(steps.map((s) => s.status), ['ok', 'error', 'skipped', 'skipped', 'skipped', 'skipped']);
});

test('stockSlots keeps null slots as no-match and only renders allow-listed base64 images', () => {
  const slots = m.stockSlots({
    products: [{ category: '스커트', season: '2027SS', attributes: { color: '블랙', material: ['가죽'], fit: '레귤러' }, inventory: { quantity: 0, unit: 'pieces', as_of: '2020-09-22', data_nature: 'sample' }, image: { media_type: 'image/png', encoding: 'base64', data: 'AAAA', size_bytes: 3, source: 'r2' }, sku_ids: ['1'] }, null,
      { category: '탑', season: '2027SS', attributes: { color: '화이트', material: [], fit: '오버' }, inventory: { quantity: 3, unit: 'pieces', as_of: '2020-09-22', data_nature: 'sample' }, image: { media_type: 'text/html', encoding: 'base64', data: 'PHNjcmlwdD4=', size_bytes: 8, source: 'x' }, sku_ids: [] }],
    trend_results: [
      { style: { id: 'a', name: '스커트' }, status: 'ok', product_index: 0, summary: '', reason: { llm_reason: '재고 과잉' } },
      { style: { id: 'b', name: '절제된 맥시멀리즘' }, status: 'no_results', product_index: 1, summary: '' },
      { style: { id: 'c', name: '조용한 럭셔리' }, status: 'ok', product_index: 2, summary: '' }],
  });
  assert.equal(slots[0].product.image.src, 'data:image/png;base64,AAAA');
  assert.equal(slots[0].product.quantity, 0);
  assert.equal(slots[1].product, null);
  assert.equal(slots[2].product.image, null);
});

test('trendCards resolves evidence quotes and keeps at most five http(s) example links', () => {
  const cards = m.trendCards({
    trends: [{ style: { id: 's', name: '스커트', definition: 'd', focus_evidence_ids: ['e1'], example_images: Array.from({ length: 7 }, (_, i) => ({ url: i === 0 ? 'javascript:x' : `https://www.vogue.co.kr/i/${i}`, basis: 'article' })) },
      items: [{ id: 'i', name: '롱 스커트', evidence_ids: ['e2'] }] }],
    evidence: [{ evidence_id: 'e1', quote: '첫 인용', title: 't', url: 'https://www.vogue.co.kr/a', published_at: '2026-10-23' }, { evidence_id: 'e2', quote: '둘째', url: null }],
  });
  assert.equal(cards[0].quotes[0].quote, '첫 인용');
  assert.equal(cards[0].quotes.length, 2);
  assert.equal(cards[0].exampleLinks.length, 4);
  assert.ok(cards[0].exampleLinks.every((link) => link.url.startsWith('https://')));
});

test('problemMessage maps known codes and falls back to the producer message', () => {
  assert.match(m.problemMessage({ error: { code: 'UPSTREAM_TIMEOUT', message: 'x' } }, 504), /UPSTREAM_TIMEOUT · 504/);
  assert.equal(m.problemMessage({ error: { code: 'NEW_CODE', message: '원문 메시지' } }), '원문 메시지 [NEW_CODE]');
  assert.equal(m.problemMessage(null), '요청을 완료하지 못했습니다.');
});

test('the integrated-review-no-results fixture keeps the image skipped and the draft visible', () => {
  const result = fixture('integrated-review-no-results').result;
  assert.equal(result.final_image.status, 'skipped');
  assert.ok(m.draftView(m.sourceResult(result, 'planning_draft')));
  assert.equal(m.rail(result).at(-1).status, 'skipped');
});

test('P07 native partial response retains sections and diagnostics', () => {
  const response = {
    contract_version: 'vogue-sales-market-review/0.3-proposal',
    status: 'partial',
    result: {
      schema_version: 'sales-market-review/0.1-internal',
      status: 'partial',
      decision_support: { recommendation: 'review_required', ready_for_human_decision: true },
      price_analysis: { status: 'no_results', proposed_price: null, reason: '매칭 가격 자료 없음' },
      fabric_cost_analysis: { status: 'no_results', reason: '원단 비용 자료 없음' },
      material_cost_analysis: [{ status: 'no_results', material: '니트' }],
      margin_estimate: { status: 'benchmark_only', margin_percent: 56.88 },
      margin_price_range_analysis: { status: 'benchmark_only', ranges: [] },
      draft_context: { category: '니트웨어', material: '니트', season: '2027SS' },
      target_audience: { status: 'ok', age_cohort: null, proposal: '가설' },
      errors: [{ code: 'CUSTOMER_AGE_RESEARCH_FAILED' }],
      missing_data: ['고객 세그먼트 원천 오류'],
      limitations: ['제품별 원가 자료 없음'],
    },
  };
  const view = m.reviewView(response);
  assert.equal(view.format, 'native');
  assert.equal(view.contractVersion, 'vogue-sales-market-review/0.3-proposal');
  assert.equal(view.status, 'partial');
  assert.ok(view.sections.some(({ key, value }) => key === 'price_analysis' && value.status === 'no_results'));
  for (const key of ['material_cost_analysis', 'margin_price_range_analysis', 'draft_context']) {
    assert.ok(view.sections.some((section) => section.key === key), `${key} reaches the review view`);
  }
  assert.equal(view.errors[0].code, 'CUSTOMER_AGE_RESEARCH_FAILED');
  assert.deepEqual(view.missingData, ['고객 세그먼트 원천 오류']);
  assert.deepEqual(view.limitations, ['제품별 원가 자료 없음']);
  const payloadView = m.reviewView(response.result);
  assert.equal(payloadView.format, 'native');
  assert.equal(payloadView.status, 'partial');
  assert.ok(payloadView.sections.some(({ key }) => key === 'price_analysis'));
});

test('P07 input view shows the provenance sent with the 0.3 request', () => {
  const response = { contract_version: 'vogue-sales-market-review/0.3-proposal', status: 'partial', result: {
    schema_version: 'sales-market-review/0.1-internal', status: 'partial', errors: [], missing_data: [], limitations: [],
  } };
  const request = { contract_version: 'vogue-sales-market-review/0.3-proposal', request_id: 'req-1', input: {
    request_id: 'req-1', product: { category: '니트웨어', season: '2027SS' },
    product_evidence: {
      category: { kind: 'user_requested', source_ref: 'p06.draft.product_profile.category', evidence_ids: [] },
      season: { kind: 'evidence_observation', source_ref: 'p06.draft.product_profile.season', evidence_ids: ['trend:e1'] },
    },
  } };
  const p06 = { evidence_ids: [], draft: { product_profile: {} } };  // the sent provenance wins over the Draft
  const view = m.reviewView(response, request, 'a'.repeat(64), 'loaded', p06);
  assert.equal(view.inputEvidenceSent, true);
  assert.equal(view.inputEvidenceState, 'loaded');
  assert.equal(view.inputFields[0].kind, 'user_requested');
  assert.equal(view.inputFields[1].kind, 'evidence_observation');
  assert.equal(view.inputFields[1].sourceRef, 'p06.draft.product_profile.season');
  assert.deepEqual(view.inputFields[1].evidenceIds, ['trend:e1']);
});

test('P07 input view falls back to P06 provenance when the request carries none', () => {
  const response = { contract_version: 'vogue-sales-market-review/0.3-proposal', status: 'partial', result: {
    schema_version: 'sales-market-review/0.1-internal', status: 'partial', errors: [], missing_data: [], limitations: [],
  } };
  const request = { contract_version: 'vogue-sales-market-review/0.2-proposal', request_id: 'req-1', input: {
    request_id: 'req-1', product: { category: '니트웨어', season: '2027SS' },
  } };
  const p06 = { evidence_ids: ['trend:e1'], draft: { category: '니트웨어', product_profile: {
    category: { value: '니트웨어', kind: 'user_requested', evidence_ids: [] },
    season: { value: '2027SS', kind: 'evidence_observation', evidence_ids: ['trend:e1'] },
  } } };
  const view = m.reviewView(response, request, 'a'.repeat(64), 'loaded', p06);
  assert.equal(view.inputEvidenceSent, false);
  assert.equal(view.inputFields[0].kind, 'user_requested');
  assert.equal(view.inputFields[1].kind, 'evidence_observation');
  assert.deepEqual(view.inputFields[1].evidenceIds, ['trend:e1']);
});

test('P06 status and provenance diagnostics survive the Draft view mapping', () => {
  const view = m.draftView({
    schema_version: '1.0', status: 'partial',
    draft: {
      title: '니트웨어 검토 초안', category: '니트웨어', season: '2027SS',
      field_origins: { category: 'unknown', season: 'unknown' },
      attributes: [{ name: 'material', value: '울 혼방', kind: 'to_validate', evidence_ids: [] }],
    },
    input_statuses: { planning_request: 'ok', trend_result: 'ok', inventory_product_result: 'ok' },
    validation_issues: [], errors: [],
  });
  assert.equal(view.status, 'partial');
  assert.equal(view.contractVersion, '1.0');
  assert.ok(view.sections.some(({ key, value }) => key === 'field_origins' && value.season === 'unknown'));
  assert.equal(view.sections.find(({ key }) => key === 'field_origins').path, 'draft.field_origins');
  assert.equal(view.sections.find(({ key }) => key === 'input_statuses').path, 'input_statuses');
  assert.ok(view.sections.some(({ key, value }) => key === 'input_statuses' && value.planning_request === 'ok'));
  assert.ok(view.sections.some(({ key }) => key === 'attributes'));
});

test('planningSheetLink is offered only for a finished run with a safe request id', () => {
  const done = { requestId: 'e2e-live-xxxxxxxxxxxx', status: 'partial' };
  assert.deepEqual(m.planningSheetLink(done), {
    href: '/api/workflow/runs/e2e-live-xxxxxxxxxxxx/planning-sheet.png',
    filename: 'vogue-planning-sheet-e2e-live-xxxxxxxxxxxx.png',
  });
  assert.equal(m.planningSheetLink(done, { inFlight: true }), null);
  assert.equal(m.planningSheetLink({ ...done, status: 'running' }), null);
  assert.equal(m.planningSheetLink({ ...done, status: null }), null);
  assert.equal(m.planningSheetLink({ ...done, requestId: '../x' }), null);
  assert.equal(m.planningSheetLink({ ...done, requestId: 'a"b' }), null);
  assert.equal(m.planningSheetLink(null), null);
});

test('P06 visuals (workflow-contract 0.2.0) are shown only from verified generated references', () => {
  const sha = 'a'.repeat(64);
  const visuals = m.draftVisuals({
    product: { status: 'generated', artifact: { sha256: sha }, model: 'fixture', reason: null },
    outfit: { status: 'failed', artifact: null, model: 'fixture', reason: 'IMAGE_API_HTTP_400:moderation_blocked' },
  });
  assert.deepEqual(visuals.map((v) => [v.key, v.label, v.status, v.sha256]),
    [['product', '신상품', 'generated', sha], ['outfit', '착장', 'failed', null]]);
  assert.equal(visuals[1].reason, 'IMAGE_API_HTTP_400:moderation_blocked');
  assert.deepEqual(m.draftVisuals(undefined), []);
  assert.equal(m.draftVisuals({ product: { status: 'generated', artifact: { sha256: '../x' } } })[0].sha256, null);
  const view = m.draftView({ draft: { title: 't', claims: [], visuals: { product: { status: 'skipped', reason: 'r' } } }, image_generation_calls: 0 });
  assert.equal(view.imageCalls, 0);
  assert.equal(m.STAGES.at(-1).en, 'Sheet');
});

// --- Side panel: evidence and JSON printed once ------------------------------------

const envelopeWith = (sources) => ({ sources: Object.fromEntries(Object.entries(sources).map(([key, result]) => [key, { raw_response: { contract_version: 'x/1', status: 'ok', result } }])) });

test('evidenceGroups files each trend evidence under the first style citing it, then stock and P07', () => {
  const result = envelopeWith({
    trend: {
      trends: [
        { style: { name: 'A', focus_evidence_ids: ['e1', 'e2'], example_images: [{ url: 'https://v/1' }] }, items: [] },
        { style: { name: 'B', focus_evidence_ids: ['e2'] }, items: [{ name: 'i', evidence_ids: ['e3'] }] },
      ],
      evidence: [{ evidence_id: 'e1', quote: 'q1', url: 'https://a' }, { evidence_id: 'e2', quote: 'q2' }, { evidence_id: 'e3', quote: 'q3' }, { evidence_id: 'e4', quote: 'q4' }],
    },
    stock: { evidence: [{ evidence_id: 's1', locator: 'stock_v1' }] },
    sales_market_review: { evidence: [{ evidence_id: 'p1', source_name: 'H&M', analysis: { interpretation: '해석' } }] },
  });
  const groups = m.evidenceGroups(result);
  assert.deepEqual(groups.map((g) => [g.anchor, g.items.map((i) => i.id), g.sharedCount]), [
    ['ev-trend-0', ['e1', 'e2'], 0], ['ev-trend-1', ['e3'], 1], ['ev-trend-other', ['e4'], 0],
    ['ev-stock', ['s1'], 0], ['ev-sales_market_review', ['p1'], 0]]);
  assert.equal(groups[0].exampleLinks.length, 1);
  assert.deepEqual([groups[4].items[0].text, groups[4].items[0].title], ['해석', 'H&M']);
  assert.equal(groups[3].items[0].detail, 'stock_v1');
  assert.deepEqual(m.evidenceGroups(null), []);
});

test('P07 consumer projection (P07 main c662d4d) keeps its sections, Korean limitations and compact evidence', () => {
  const response = fixture('p07-review-v0.3.consumer-projection.response');
  const view = m.reviewView(response);
  assert.equal(view.format, 'native');
  assert.equal(view.status, 'ok');
  const sections = Object.fromEntries(view.sections.map(({ key, value }) => [key, value]));
  for (const key of ['price_analysis', 'fabric_cost_analysis', 'material_cost_analysis', 'margin_analysis', 'target_audience', 'target_audience_analysis', 'evidence']) {
    assert.ok(key in sections, `${key} reaches the review view`);
  }
  assert.ok(!('margin_price_range_analysis' in sections), 'a null section is not listed');
  assert.equal(sections.fabric_cost_analysis.unit_cost, undefined);
  assert.equal(sections.target_audience.proposal.price_positioning, undefined);
  assert.deepEqual(view.limitations, response.result.limitations);
  assert.equal(view.limitations[0], '[마진 참고] 미국 의류 업계 평균 매출총이익률 벤치마크이며 이 상품의 실제 마진이 아닙니다.');
  assert.deepEqual(view.missingData, []);
  const p07 = m.evidenceGroups(envelopeWith({ sales_market_review: response.result })).find((g) => g.anchor === 'ev-sales_market_review');
  assert.equal(p07.items.length, response.result.evidence.length);
  const benchmark = p07.items.find((item) => item.raw.source_type === 'industry_gross_margin_benchmark');
  assert.equal(benchmark.title, 'NYU Stern Damodaran Margins by Sector (US), January 2026');
  assert.match(benchmark.text, /^Sector-level benchmark/);
  assert.equal(benchmark.detail, 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/margin.html');
  assert.equal(benchmark.url, null);  // P07 evidence carries a locator, not a url field
});

test('rawFields splits a raw result into anchored fields and points evidence to the evidence tab', () => {
  const result = envelopeWith({
    planning_draft: { draft: { title: 't', input_conflicts: [] }, input_statuses: { trend_result: 'ok' } },
    trend: { trends: [], evidence: [{ evidence_id: 'e1' }] },
  });
  const draft = m.rawFields(result, 'planning_draft');
  assert.deepEqual(draft.map((f) => f.path), ['draft.title', 'draft.input_conflicts', 'input_statuses']);
  assert.equal(draft.find((f) => f.path === 'draft.input_conflicts').empty, true);
  assert.equal(draft.find((f) => f.path === 'input_statuses').anchor, m.jsonAnchor('planning_draft', 'input_statuses'));
  const trend = m.rawFields(result, 'trend');
  assert.deepEqual(trend.find((f) => f.path === 'evidence'), { path: 'evidence', anchor: 'json-trend-evidence', pointer: 'evidence', count: 1 });
  assert.deepEqual(m.rawFields(result, 'stock'), []);
});

test('draft and review rows link to the same anchors the panel prints', () => {
  const draftResult = { draft: { title: 't', attributes: [] }, planning_analysis: { a: 1 } };
  const result = envelopeWith({ planning_draft: draftResult });
  const anchors = new Set(m.rawFields(result, 'planning_draft').map((f) => f.anchor));
  for (const section of m.draftView(draftResult).sections) assert.ok(anchors.has(m.jsonAnchor('planning_draft', section.path)), section.path);
  const draftLists = m.draftView({ ...draftResult, draft: { ...draftResult.draft, limitations: ['x'] }, errors: [] }).listPaths;
  assert.deepEqual(draftLists, { limitations: 'draft.limitations', errors: 'errors' });
  const review = m.reviewView({ schema_version: 's', status: 'ok', price_analysis: { status: 'ok' }, limitations: ['y'] });
  assert.equal(review.sections[0].path, 'price_analysis');
  const reviewAnchors = new Set(m.rawFields(envelopeWith({ sales_market_review: { schema_version: 's', price_analysis: {}, limitations: ['y'] } }), 'sales_market_review').map((f) => f.anchor));
  assert.ok(reviewAnchors.has(m.jsonAnchor('sales_market_review', review.listPaths.limitations)));
});

test('compactJson summarises long encoded strings only', () => {
  const text = m.compactJson({ data: 'A'.repeat(5000), note: '짧은 문장' });
  assert.match(text, /5,000자 인코딩 데이터 생략/);
  assert.match(text, /짧은 문장/);
});

test('stage errors come from the orchestrator record and are not repeated by the producer list', () => {
  const result = { errors: [{ stage: 'sales_market_review', code: 'TARGET_AUDIENCE_LLM_FAILED', message: 'm' }, { stage: 'planning_draft', code: 'UPSTREAM_UNAVAILABLE' }] };
  const review = m.stageErrors(result, 'sales_market_review');
  assert.deepEqual(review, [{ code: 'TARGET_AUDIENCE_LLM_FAILED', message: 'm' }]);
  assert.deepEqual(m.stageErrors(result, ['integration', 'final_image']), []);
  assert.deepEqual(m.withoutShownErrors([{ code: 'TARGET_AUDIENCE_LLM_FAILED' }, { code: 'OTHER' }, 'text'], review), [{ code: 'OTHER' }, 'text']);
});

test('limitations keep their first occurrence across sections', () => {
  assert.deepEqual(m.firstOccurrences([['a', 'b'], ['b', 'c'], ['a', 'c', 'd', 'd']]), [['a', 'b'], ['c'], ['d']]);
  assert.deepEqual(m.firstOccurrences([undefined, ['x']]), [[], ['x']]);
});

test('railProgress counts stages with a producer outcome and finds the running one', () => {
  const live = m.railProgress(m.rail({ stages: [{ stage: 'trend', status: 'ok' }, { stage: 'stock', status: 'partial' }] }, { inFlight: true }));
  assert.deepEqual([live.done, live.total, live.running.key], [2, 6, 'planning_draft']);
  const stopped = m.railProgress(m.rail({ stages: [{ stage: 'trend', status: 'ok' }, { stage: 'stock', status: 'error' }], final_image: { status: 'skipped' } }));
  assert.deepEqual([stopped.done, stopped.running], [2, null]);
});

test('panelJson prints a long value once and points later copies to it', () => {
  const ids = ['trend:1:aaaaaaaa', 'trend:2:bbbbbbbb', 'trend:3:cccccccc'];
  const result = envelopeWith({
    trend: { status: 'ok', request_id: 'req-1', evidence: [] },
    planning_draft: { status: 'ok', request_id: 'req-1', evidence_ids: ids, draft: { evidence_ids: ids } },
  });
  const stages = m.panelJson(result);
  assert.deepEqual(stages.map((s) => s.key), ['trend', 'planning_draft']);
  const draft = stages[1].fields;
  assert.ok(!draft.some((f) => f.path === 'request_id'));
  assert.equal(draft.find((f) => f.path === 'evidence_ids').sameAs, undefined);
  assert.deepEqual(draft.find((f) => f.path === 'draft.evidence_ids').sameAs, { path: '03 evidence_ids', anchor: 'json-planning_draft-evidence_ids' });
  assert.equal(draft.find((f) => f.path === 'status').sameAs, undefined);  // short scalars are not pointers
});

test('evidence records sharing a sentence print it under the first record only', () => {
  const result = envelopeWith({ sales_market_review: { evidence: [
    { evidence_id: 'p1', analysis: { interpretation: '같은 해석' } }, { evidence_id: 'p2', analysis: { interpretation: '같은 해석' } }] } });
  const items = m.evidenceGroups(result)[0].items;
  assert.deepEqual(items.map((i) => [i.text, i.sameTextAs]), [['같은 해석', undefined], [null, 'p1']]);
});
