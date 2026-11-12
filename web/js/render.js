// DOM rendering for the workspace. Data is inserted with textContent/attributes
// only (never innerHTML), so producer text cannot inject markup.
import * as model from './model.js';
import { artifactUrl } from './api.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function svg(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  return el;
}

const GLYPH = { ok: 'M5 8.3l2 2 4-4.3', error: 'M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4', none: 'M5 8h6' };

export function statusIcon(tone) {
  const icon = svg('svg', { width: 14, height: 14, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.7, 'aria-hidden': 'true', focusable: 'false' });
  if (tone === 'running') {
    icon.append(svg('path', { d: 'M8 1.5a6.5 6.5 0 1 1-6.5 6.5' }));
    icon.classList.add('spin');
    return icon;
  }
  const ring = svg('circle', { cx: 8, cy: 8, r: 6.5 });
  if (tone === 'pending') ring.setAttribute('stroke-dasharray', '2 2.4');
  icon.append(ring);
  if (tone === 'partial') icon.append(svg('path', { d: 'M8 1.5a6.5 6.5 0 0 1 0 13z', fill: 'currentColor' }));
  else if (GLYPH[tone]) icon.append(svg('path', { d: GLYPH[tone] }));
  return icon;
}

export function chip(status) {
  const meta = model.statusMeta(status);
  return h('span', { class: `chip tone-${meta.tone}` }, statusIcon(meta.tone), meta.label);
}

function badge(nature) {
  return h('span', { class: `badge nature-${nature.tone}` }, nature.label);
}

function sectionHead(id, title, meta) {
  return h('div', { class: 'section-head' }, h('h2', { id }, title), meta ? h('span', { class: 'section-meta' }, meta) : null);
}

function emptyNote(text) {
  return h('p', { class: 'empty-note' }, text);
}

// --- Rail ------------------------------------------------------------------

export function renderRail(list, items) {
  list.replaceChildren(...items.map((item) => h('li', { class: `rail-step tone-${item.tone}` },
    h('span', { class: 'rail-index' }, `${item.n} · ${item.en}`),
    h('span', { class: 'rail-name' }, item.ko),
    h('span', { class: `chip tone-${item.tone}` }, statusIcon(item.tone), item.label))));
}

// --- Concept / final image -------------------------------------------------

export function renderConcept(section, envelope, { retryEnabled = false, onRetry } = {}) {
  const result = envelope.result ?? {};
  const draft = model.draftView(model.sourceResult(result, 'planning_draft'));
  const image = result.final_image ?? {};
  const lineage = image.lineage ?? {};
  let figure;
  if (image.status === 'generated' && image.artifact?.artifact_id) {
    figure = h('figure', { class: 'concept-figure' },
      h('img', { src: artifactUrl(image.artifact.artifact_id), alt: `${draft?.title ?? '기획'} — 시장 검토 이후 생성한 최종 기획 이미지`, loading: 'lazy' }));
  } else {
    const label = image.status === 'failed' ? '이미지 생성 실패' : image.status === 'skipped' ? '이미지 생략' : '이미지 없음';
    figure = h('figure', { class: `concept-figure is-empty state-${image.status ?? 'none'}` },
      h('figcaption', {}, h('strong', {}, label), h('span', {}, image.reason || '시장 검토가 끝난 뒤에만 한 번 생성합니다.')));
  }
  const retry = retryEnabled && image.status === 'failed' && typeof onRetry === 'function'
    ? h('button', { type: 'button', class: 'btn btn-ghost', onClick: onRetry }, '이미지 단계만 다시 시도')
    : null;
  section.replaceChildren(figure, h('div', { class: 'concept-body' },
    h('p', { class: 'eyebrow' }, 'INTEGRATED DRAFT'),
    h('h2', { id: 'concept-title', class: 'concept-title' }, draft?.title ?? '통합 결과'),
    h('p', { class: 'muted' }, '기획 초안과 시장 검토를 통합한 결과입니다. 가격·원가·수량·발주·최종 채택은 MD가 결정합니다.'),
    h('div', { class: 'chip-row' },
      chip(result.status),
      draft?.identity ? h('span', { class: 'tag' }, `Draft r${draft.identity.revision ?? '?'}`) : null,
      image.status ? h('span', { class: 'tag' }, `이미지 ${model.statusMeta(image.status).label}${Number.isInteger(image.provider_calls) ? ` · 호출 ${image.provider_calls}회` : ''}`) : null),
    h('dl', { class: 'meta-list' },
      h('dt', {}, 'Draft'), h('dd', { class: 'mono' }, model.shortSha(lineage.draft_response_sha256)),
      h('dt', {}, 'Review'), h('dd', { class: 'mono' }, model.shortSha(lineage.review_response_sha256)),
      h('dt', {}, 'Image'), h('dd', { class: 'mono' }, model.shortSha(image.artifact?.sha256))),
    retry));
}

// --- Trend -----------------------------------------------------------------

export function renderTrends(section, cards) {
  const grid = h('div', { class: 'card-grid' });
  for (const card of cards) {
    const quote = card.quotes[0];
    grid.append(h('article', { class: 'card trend-card' },
      h('div', { class: 'card-top' }, h('span', { class: 'eyebrow' }, `STYLE ${card.rank}`), card.seasons ? h('span', { class: 'muted small' }, card.seasons) : null),
      h('h3', {}, card.name),
      card.definition ? h('p', { class: 'muted small' }, card.definition) : null,
      card.items.length ? h('ul', { class: 'pill-list', 'aria-label': '아이템' }, card.items.slice(0, 6).map((item) => h('li', {}, item))) : null,
      quote ? h('blockquote', { class: 'quote' }, `“${quote.quote}”`) : null,
      quote?.url ? h('a', { class: 'link', href: quote.url, target: '_blank', rel: 'noopener noreferrer' }, `${quote.title || '원문'}${quote.date ? ` · ${quote.date}` : ''}`) : null,
      card.exampleLinks.length ? h('p', { class: 'link-row' }, '예시 이미지 ', card.exampleLinks.map((link, i) => h('a', { class: 'link', href: link.url, target: '_blank', rel: 'noopener noreferrer', title: link.basis }, `${i + 1}`))) : null));
  }
  section.replaceChildren(sectionHead('trend-title', '트렌드 근거', 'Trend 1.1 · Vogue Korea 기사 그래프'), cards.length ? grid : emptyNote('트렌드 결과가 없습니다.'));
}

// --- Stock -----------------------------------------------------------------

export function renderStock(section, slots) {
  const grid = h('div', { class: 'card-grid' });
  for (const slot of slots) {
    const product = slot.product;
    const body = product
      ? [h('div', { class: 'product' },
          product.image ? h('img', { class: 'product-image', src: product.image.src, alt: `${slot.styleName} 매칭 상품 이미지` }) : h('div', { class: 'product-image is-empty', role: 'img', 'aria-label': '상품 이미지 없음' }),
          h('div', { class: 'product-body' },
            h('strong', {}, `${product.category} · ${product.color ?? '색상 미확인'}`),
            h('span', { class: 'muted small' }, [product.materials.join(', '), product.fit].filter(Boolean).join(' · ') || '소재·핏 미확인'),
            h('span', { class: 'muted small' }, product.quantity === null ? '재고 수량 미확인' : `가상 재고 ${product.quantity.toLocaleString('ko-KR')}개`))),
        h('div', { class: 'chip-row' }, h('span', { class: 'badge nature-synthetic' }, product.dataNature ?? 'sample'), product.asOf ? h('span', { class: 'tag' }, `as_of ${product.asOf}`) : null),
        slot.reason ? h('p', { class: 'small' }, slot.reason) : null]
      : [h('div', { class: 'no-match' }, h('strong', {}, '매칭 없음 · no_results'), h('span', {}, '재고가 0이라는 뜻이 아닙니다. 조건에 맞는 후보를 찾지 못했습니다.'))];
    grid.append(h('article', { class: 'card stock-card' },
      h('div', { class: 'card-top' }, h('span', { class: 'muted small' }, slot.styleName), chip(slot.status)), body));
  }
  section.replaceChildren(sectionHead('stock-title', '재고 매칭', 'Stock 1.0 · 스타일별 과잉 재고 후보'), slots.length ? grid : emptyNote('재고 단계 결과가 없습니다.'));
}

// --- Draft / Review --------------------------------------------------------

function claimList(claims) {
  return h('ol', { class: 'claim-list' }, claims.map((claim) => h('li', {},
    h('span', { class: 'tag tag-strong' }, claim.kind),
    h('span', { class: 'claim-text' }, claim.text),
    h('span', { class: 'muted small' }, `근거 ${claim.refs}건`))));
}

export function renderDraft(section, view) {
  if (!view) { section.replaceChildren(sectionHead('draft-title', '기획 초안'), emptyNote('초안이 아직 없습니다.')); return; }
  section.replaceChildren(
    sectionHead('draft-title', '기획 초안', `planning-draft/1.0${view.category ? ` · ${view.category}` : ''}${view.season ? ` · ${view.season}` : ''}`),
    h('h3', { class: 'draft-title' }, view.title),
    view.claims.length ? claimList(view.claims) : emptyNote('주장이 없습니다.'),
    h('div', { class: 'open-questions' },
      h('p', { class: 'eyebrow' }, 'OPEN QUESTIONS · MD'),
      view.openQuestions.length ? h('ul', {}, view.openQuestions.map((q) => h('li', {}, q))) : emptyNote('명시된 확인 사항이 없습니다.'),
      view.mdRequired.length ? h('p', { class: 'small muted' }, `MD 결정 필요: ${view.mdRequired.join(' · ')}`) : null),
    view.limitations.length ? h('details', { class: 'limits' }, h('summary', {}, `제한 ${view.limitations.length}건`), h('ul', {}, view.limitations.map((l) => h('li', {}, l)))) : null);
}

export function renderReview(section, view) {
  if (!view) { section.replaceChildren(sectionHead('review-title', '시장 검토'), emptyNote('시장 검토 결과가 없습니다.')); return; }
  const table = h('table', { class: 'metric-table' },
    h('thead', {}, h('tr', {}, ['지표', '값', '기간 · 출처', '성격'].map((label) => h('th', { scope: 'col' }, label)))),
    h('tbody', {}, view.metrics.map((metric) => h('tr', {},
      h('td', {}, metric.label),
      h('td', { class: metric.missing ? 'value is-missing' : 'value' }, metric.value, metric.missing ? h('span', { class: 'small muted as-block' }, metric.reason) : null),
      h('td', { class: 'muted small' }, [metric.period, metric.source].filter(Boolean).join(' · ') || '—'),
      h('td', {}, badge(metric.nature), metric.asOf ? h('span', { class: 'small muted as-block' }, `as_of ${metric.asOf}`) : null)))));
  section.replaceChildren(
    sectionHead('review-title', '시장 검토', `sales-market-review/1.0 · ${view.observation.nature.label}`),
    view.findings.length ? claimList(view.findings) : emptyNote('검토 소견이 없습니다.'),
    view.metrics.length ? h('div', { class: 'table-scroll' }, table) : emptyNote('지표가 없습니다.'),
    view.limitations.length ? h('details', { class: 'limits' }, h('summary', {}, `제한 ${view.limitations.length}건`), h('ul', {}, view.limitations.map((l) => h('li', {}, l)))) : null);
}

// --- Aside -----------------------------------------------------------------

export function renderRun(section, summary) {
  section.replaceChildren(
    h('h2', { id: 'run-title' }, '실행 요약'),
    h('dl', { class: 'meta-list' },
      h('dt', {}, '상태'), h('dd', {}, chip(summary.status)),
      h('dt', {}, '계약'), h('dd', {}, summary.contract ?? '—'),
      h('dt', {}, '이미지'), h('dd', {}, summary.image.status ? `${model.statusMeta(summary.image.status).label}${summary.image.calls !== null ? ` · 호출 ${summary.image.calls}회` : ''}` : '—'),
      h('dt', {}, '요청 ID'), h('dd', { class: 'mono small' }, summary.requestId ?? '—')),
    summary.errors.length ? h('div', { class: 'error-box', role: 'status' }, h('strong', {}, '오류'), h('ul', {}, summary.errors.map((e) => h('li', {}, e)))) : null,
    summary.limitations.length ? h('div', {}, h('p', { class: 'eyebrow' }, 'LIMITATIONS'), h('ul', { class: 'small' }, summary.limitations.map((l) => h('li', {}, l)))) : null);
}

const CHOICES = [['adopt', '채택'], ['revise', '수정'], ['hold', '보류']];
const CHOICE_LABEL = Object.fromEntries(CHOICES);

export function renderMd(section, { decisions, selected, busy, message, disabled, onSelect, onSave }) {
  const note = h('textarea', { id: 'md-note', rows: 4, maxlength: 2000, disabled: disabled || busy });
  const group = h('div', { class: 'segmented', role: 'group', 'aria-label': '결정 선택' },
    CHOICES.map(([value, label]) => h('button', { type: 'button', 'aria-pressed': String(selected === value), disabled: disabled || busy, onClick: () => onSelect(value) }, label)));
  const save = h('button', { type: 'button', class: 'btn btn-dark btn-block', disabled: disabled || busy || !selected, onClick: () => onSave(note.value.trim()) }, busy ? '저장 중…' : '결정 저장');
  const history = decisions.length
    ? h('ol', { class: 'decision-list' }, [...decisions].reverse().map((d) => h('li', {}, h('strong', {}, CHOICE_LABEL[d.decision] ?? d.decision), h('span', { class: 'muted small' }, d.decided_at ?? d.created_at ?? ''), d.note ? h('span', { class: 'small as-block' }, d.note) : null)))
    : h('p', { class: 'small muted' }, '아직 저장된 결정이 없습니다.');
  section.replaceChildren(
    h('h2', { id: 'md-title' }, 'MD 판단'),
    group,
    h('label', { for: 'md-note', class: 'field-label' }, '판단 메모 (선택, 2,000자 이내)'),
    note, save,
    message ? h('p', { class: 'small notice-inline', role: 'status' }, message) : null,
    h('p', { class: 'small muted' }, '결정은 통합 결과 해시에 묶여 기록되며, 결과 원문은 바뀌지 않습니다. 가격·원가·수량·발주는 이 화면에서 정하지 않습니다.'),
    history);
}

// --- Evidence drawer -------------------------------------------------------

export function renderEvidence(container, envelope, tab) {
  const result = envelope?.result ?? {};
  if (tab === 'raw') {
    const blocks = model.STAGES.filter((s) => result.sources?.[s.key]).map((s) => h('details', { class: 'raw-block' },
      h('summary', {}, `${s.en} · ${s.ko}`),
      h('pre', { class: 'mono small' }, JSON.stringify(result.sources[s.key].raw_response, null, 2))));
    container.replaceChildren(...(blocks.length ? blocks : [emptyNote('보존된 원문이 없습니다.')]));
    return;
  }
  if (tab === 'lineage') {
    const rows = model.lineageRows(result);
    container.replaceChildren(rows.length ? h('div', { class: 'table-scroll' }, h('table', { class: 'metric-table' },
      h('thead', {}, h('tr', {}, ['#', '단계', '상태', '입력 sha', '응답 sha', '완료'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', {}, rows.map((r) => h('tr', {}, h('td', { class: 'mono' }, r.n), h('td', {}, r.stage), h('td', {}, chip(r.status)), h('td', { class: 'mono' }, r.input), h('td', { class: 'mono' }, r.response), h('td', { class: 'small muted' }, r.completed))))))
      : emptyNote('실행 trace가 없습니다.'));
    return;
  }
  const trend = model.sourceResult(result, 'trend');
  const evidence = trend?.evidence ?? [];
  container.replaceChildren(...(evidence.length ? evidence.map((e) => {
    const url = model.safeUrl(e.url);
    return h('article', { class: 'evidence-item' },
      h('p', { class: 'mono small muted' }, e.evidence_id),
      h('blockquote', { class: 'quote' }, `“${e.quote ?? ''}”`),
      url ? h('a', { class: 'link', href: url, target: '_blank', rel: 'noopener noreferrer' }, `${e.title || '원문'}${e.published_at ? ` · ${e.published_at}` : ''}`) : h('p', { class: 'small muted' }, e.title || '출처 링크 없음'));
  }) : [emptyNote('트렌드 근거가 없습니다.')]));
}

// --- Runs & system ---------------------------------------------------------

export function renderRuns(list, runs, onOpen) {
  if (!runs.length) { list.replaceChildren(h('li', { class: 'empty-note' }, '아직 실행 이력이 없습니다.')); return; }
  list.replaceChildren(...runs.map((run) => h('li', { class: 'run-item' },
    h('div', { class: 'run-main' }, h('strong', {}, run.query_excerpt || run.request_id), h('span', { class: 'muted small mono' }, run.request_id)),
    h('div', { class: 'run-meta' }, chip(run.status), h('span', { class: 'muted small' }, run.created_at ?? ''),
      h('button', { type: 'button', class: 'btn btn-ghost', onClick: () => onOpen(run.request_id) }, '열기')))));
}

export function renderSystem(container, status) {
  if (!status) { container.replaceChildren(emptyNote('시스템 상태를 불러오지 못했습니다.')); return; }
  const services = Object.entries(status.services ?? {});
  container.replaceChildren(
    h('ul', { class: 'service-list' }, services.map(([name, svc]) => h('li', {},
      h('span', {}, name),
      h('span', { class: svc.reachable ? 'ok-text' : 'error-text' }, svc.reachable ? `응답${svc.http_status ? ` · ${svc.http_status}` : ''}` : '응답 없음')))),
    h('dl', { class: 'meta-list' },
      h('dt', {}, '계약 baseline'), h('dd', {}, status.contract?.state ?? status.contract_state ?? '—'),
      h('dt', {}, '이미지 공급자'), h('dd', {}, status.image_provider ?? '—'),
      h('dt', {}, '질의 구조화'), h('dd', {}, status.query_structuring_mode ?? '—'),
      h('dt', {}, '실행 모드'), h('dd', {}, status.runtime ?? '—')));
}
