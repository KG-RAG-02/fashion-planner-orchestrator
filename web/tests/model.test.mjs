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

test('validateImage enforces MIME allow-list and 10 MB limit', () => {
  assert.equal(m.validateImage(null).ok, true);
  assert.equal(m.validateImage({ type: 'image/gif', size: 10 }).ok, false);
  assert.equal(m.validateImage({ type: 'image/png', size: 10485761 }).ok, false);
  assert.equal(m.validateImage({ type: 'image/webp', size: 10485760 }).ok, true);
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

test('metricView keeps null distinct from an observed zero', () => {
  const missing = m.metricView({ name: 'current_company_sales', value: null, missing_reason: '원천 없음', unit: 'pieces', observation: { data_nature: 'synthetic_fixture', synthetic: true } });
  assert.equal(missing.value, '미확인');
  assert.equal(missing.missing, true);
  assert.equal(missing.label, '현재 자사 판매');
  assert.equal(missing.nature.label, 'synthetic');
  const zero = m.metricView({ name: 'observed', value: 0, unit: 'pieces', observation: { data_nature: 'historical_external', synthetic: false } });
  assert.equal(zero.value, '0개');
  assert.equal(zero.missing, false);
  assert.equal(zero.nature.label, 'historical');
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

test('the integrated-ok contract fixture renders through every view without loss', () => {
  const envelope = fixture('integrated-ok');
  const result = envelope.result;
  const draft = m.draftView(m.sourceResult(result, 'planning_draft'));
  const review = m.reviewView(m.sourceResult(result, 'sales_market_review'));
  assert.ok(draft.title);
  assert.equal(draft.claims.length, result.sources.planning_draft.raw_response.result.draft.claims.length);
  assert.equal(review.metrics.length, result.sources.sales_market_review.raw_response.result.review.metrics.length);
  assert.ok(review.metrics.some((metric) => metric.missing && metric.value === '미확인'));
  const summary = m.runSummary(envelope);
  assert.equal(summary.status, result.status);
  assert.equal(summary.image.status, 'generated');
  assert.deepEqual(m.rail(result).map((s) => s.status), result.stages.map((s) => (s.stage === 'final_image' ? result.final_image.status : s.status)));
  assert.equal(m.lineageRows(result).length, result.stages.length);
});

test('the integrated-review-no-results fixture keeps the image skipped and the draft visible', () => {
  const result = fixture('integrated-review-no-results').result;
  assert.equal(m.runSummary({ result }).image.status, 'skipped');
  assert.ok(m.draftView(m.sourceResult(result, 'planning_draft')));
  assert.equal(m.rail(result).at(-1).status, 'skipped');
});
