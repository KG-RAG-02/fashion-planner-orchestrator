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
  // 0.2.0: the visuals are P06's (Draft section); stage 06 is the planning sheet composed by P08.
  const sheet = Boolean(draft?.visuals?.length);
  if (image.status === 'generated' && image.artifact?.artifact_id) {
    const src = artifactUrl(image.artifact.artifact_id);
    figure = h('figure', { class: `concept-figure${sheet ? ' is-sheet' : ''}` },
      h('a', { href: src, target: '_blank', rel: 'noopener', title: '원본 크기로 열기' },
        h('img', { src, alt: sheet ? `${draft?.title ?? '기획'} — 기획안 사진 (P08 합성)` : `${draft?.title ?? '기획'} — 시장 검토 이후 생성한 최종 기획 이미지`, loading: 'lazy' })));
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

function draftVisualsFigure(view) {
  const calls = Number.isInteger(view.imageCalls) ? ` · 이미지 호출 ${view.imageCalls}회` : '';
  return h('div', { class: 'draft-visuals', role: 'group', 'aria-label': 'P06 시안 이미지' },
    view.visuals.map((item) => h('figure', { class: `draft-visual state-${item.status ?? 'none'}` },
      item.sha256
        ? h('a', { href: artifactUrl(item.sha256), target: '_blank', rel: 'noopener' },
            h('img', { src: artifactUrl(item.sha256), alt: `${view.title} — ${item.label} 시안 (P06)`, loading: 'lazy' }))
        : h('div', { class: 'draft-visual-empty' }, item.reason || '이미지 없음'),
      h('figcaption', {}, h('strong', {}, item.label), ` ${model.statusMeta(item.status).label}${item.model ? ` · ${item.model}` : ''}`))),
    h('p', { class: 'small muted draft-visuals-note' }, `P06(new-product-agent)가 트렌드 근거로 만든 검토용 시안${calls}`));
}

export function renderDraft(section, view) {
  if (!view) { section.replaceChildren(sectionHead('draft-title', '기획 초안'), emptyNote('초안이 아직 없습니다.')); return; }
  const draftVersion = view.contractVersion?.startsWith('planning-draft/')
    ? view.contractVersion : view.contractVersion ? `planning-draft/${view.contractVersion}` : 'planning-draft/1.0';
  const detailBlock = (title, items) => items.length
    ? h('details', { class: 'limits' }, h('summary', {}, `${title} ${items.length}건`),
        h('ul', {}, items.map((item) => h('li', {}, typeof item === 'string' ? item : JSON.stringify(item)))))
    : null;
  section.replaceChildren(
    sectionHead('draft-title', '기획 초안', `${draftVersion} · ${model.statusMeta(view.status).label}${view.category ? ` · ${view.category}` : ''}${view.season ? ` · ${view.season}` : ''}`),
    h('h3', { class: 'draft-title' }, view.title),
    view.visuals?.length ? draftVisualsFigure(view) : null,
    view.claims.length ? claimList(view.claims) : emptyNote('주장이 없습니다.'),
    h('div', { class: 'open-questions' },
      h('p', { class: 'eyebrow' }, 'OPEN QUESTIONS · MD'),
      view.openQuestions.length ? h('ul', {}, view.openQuestions.map((q) => h('li', {}, q))) : emptyNote('명시된 확인 사항이 없습니다.'),
      view.mdRequired.length ? h('p', { class: 'small muted' }, `MD 결정 필요: ${view.mdRequired.join(' · ')}`) : null),
    detailBlock('P06 오류', view.errors),
    view.sections.length ? h('div', { class: 'review-analyses' }, view.sections.map(({ key, label, value }) => h('details', { class: 'limits review-analysis' },
      h('summary', {}, label), h('pre', { class: 'review-payload' }, JSON.stringify(value, null, 2))))) : null,
    view.limitations.length ? h('details', { class: 'limits' }, h('summary', {}, `제한 ${view.limitations.length}건`), h('ul', {}, view.limitations.map((l) => h('li', {}, l)))) : null);
}

export function renderReview(section, view) {
  if (!view) { section.replaceChildren(sectionHead('review-title', '시장 검토'), emptyNote('시장 검토 결과가 없습니다.')); return; }
  if (view.format === 'native') {
    const dataBlock = (title, items) => items.length
      ? h('details', { class: 'limits' }, h('summary', {}, `${title} ${items.length}건`),
          h('ul', {}, items.map((item) => h('li', {}, typeof item === 'string' ? item : JSON.stringify(item)))))
      : null;
    const sections = view.sections.map(({ key, label, value }) => h('details', { class: 'limits review-analysis' },
      h('summary', {}, label, value && typeof value === 'object' && value.status ? ` · ${model.statusMeta(value.status).label}` : ''),
      h('pre', { class: 'review-payload' }, JSON.stringify(value, null, 2))));
    let inputBlock;
    if (view.inputEvidenceState === 'loading') {
      inputBlock = h('div', { class: 'review-input-card' }, h('p', { class: 'small muted' }, 'P07에 전달한 입력과 필드별 근거를 불러오는 중입니다…'));
    } else if (view.inputFields.length) {
      const rows = view.inputFields.map((field) => h('tr', {},
        h('th', { scope: 'row' }, field.label),
        h('td', { class: 'review-input-value' }, field.value),
        h('td', {}, h('span', { class: `tag review-origin origin-${field.kind ?? 'missing'}` }, field.kindLabel),
          field.sourceRef ? h('code', { class: 'review-source-ref' }, field.sourceRef) : h('span', { class: 'small muted as-block' }, '출처 경로 미전달')),
        h('td', {}, field.evidenceIds.length
          ? h('ul', { class: 'review-evidence-ids' }, field.evidenceIds.map((id) => h('li', { class: 'mono small' }, id)))
          : h('span', { class: 'small muted' }, field.kind === 'user_requested' ? '연결된 연구 Evidence ID 없음' : 'Evidence ID 없음'))));
      inputBlock = h('details', { class: 'review-input-card', open: true },
      h('summary', {}, `P07에 전달한 입력 · ${view.inputContractVersion ?? '계약 미확인'}`),
        h('p', { class: 'small muted review-input-meta' }, `요청 ID ${view.inputRequestId ?? '—'} · ${view.inputEvidenceSent ? '출처 표시는 P07 요청에 함께 보낸 product_evidence입니다.' : '이 실행의 P07 요청에는 product_evidence가 없어 출처는 P06 Draft 기준으로 표시합니다.'}`),
        h('div', { class: 'table-scroll' }, h('table', { class: 'metric-table review-input-table' },
          h('thead', {}, h('tr', {}, ['항목', '전달값', '출처 종류 · 경로', 'Evidence ID'].map((label) => h('th', { scope: 'col' }, label)))),
          h('tbody', {}, rows))),
        view.callRequestArtifactId ? h('a', { class: 'link small', href: artifactUrl(view.callRequestArtifactId), target: '_blank', rel: 'noopener' }, 'P07 요청 원문 JSON 열기') : null);
    } else if (view.inputEvidenceState === 'missing') {
      inputBlock = h('div', { class: 'review-input-card' },
        h('strong', {}, '상품값의 출처 정보가 없습니다.'),
        h('p', { class: 'small muted' }, '이 실행의 P07 요청에 product_evidence가 없고, P06 Draft에서도 전달값과 일치하는 출처를 찾지 못했습니다.'));
    } else if (view.callRequestArtifactId) {
      inputBlock = h('div', { class: 'review-input-card' },
        h('p', { class: 'small muted' }, 'P07 요청 원문을 불러오지 못했습니다.'),
        h('a', { class: 'link small', href: artifactUrl(view.callRequestArtifactId), target: '_blank', rel: 'noopener' }, 'P07 요청 원문 JSON 열기'));
    } else {
      inputBlock = h('div', { class: 'review-input-card' }, emptyNote('P07 호출 요청이 보존되지 않았습니다.'));
    }
    section.replaceChildren(
      sectionHead('review-title', '시장 검토', `${view.contractVersion ?? 'P07 응답'} · ${model.statusMeta(view.status).label}`),
      inputBlock,
      view.sections.length ? h('div', { class: 'review-analyses' }, sections) : emptyNote('P07 응답에 분석 항목이 없습니다.'),
      dataBlock('누락 데이터', view.missingData),
      dataBlock('오류', view.errors),
      dataBlock('제한 사항', view.limitations));
    return;
  }
  section.replaceChildren(sectionHead('review-title', '시장 검토'), emptyNote('현재 P07 응답 계약과 일치하는 결과가 없습니다.'));
}

// --- Aside -----------------------------------------------------------------

export function renderRun(section, summary, { sheet = null } = {}) {
  section.replaceChildren(
    h('h2', { id: 'run-title' }, '실행 요약'),
    h('dl', { class: 'meta-list' },
      h('dt', {}, '상태'), h('dd', {}, chip(summary.status)),
      h('dt', {}, '계약'), h('dd', {}, summary.contract ?? '—'),
      h('dt', {}, '이미지'), h('dd', {}, summary.image.status ? `${model.statusMeta(summary.image.status).label}${summary.image.calls !== null ? ` · 호출 ${summary.image.calls}회` : ''}` : '—'),
      h('dt', {}, '요청 ID'), h('dd', { class: 'mono small' }, summary.requestId ?? '—')),
    sheet ? h('a', { class: 'btn btn-dark btn-block', href: sheet.href, download: sheet.filename },
      '기획서 PNG 저장') : null,
    sheet ? h('p', { class: 'small muted' }, '저장된 실행 결과를 근거 연결 기획 보드 양식으로 그린 PNG입니다. 값은 다시 계산하지 않습니다.') : null,
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
  const agents = Array.isArray(status.agents) ? status.agents : [];
  container.replaceChildren(
    h('ul', { class: 'service-list' }, agents.map((svc) => h('li', {},
      h('span', {}, svc.id),
      h('span', { class: svc.reachable && svc.healthy ? 'ok-text' : 'error-text' },
        svc.reachable ? `${svc.healthy ? '정상' : '비정상'} · ${svc.http_status ?? '—'}${Number.isInteger(svc.latency_ms) ? ` · ${svc.latency_ms}ms` : ''}` : '응답 없음')))),
    h('dl', { class: 'meta-list' },
      h('dt', {}, '계약 baseline'), h('dd', {}, `${status.contract?.state ?? '—'}${status.contract?.gate ? ` · gate ${status.contract.gate}` : ''}`),
      h('dt', {}, '06 기획안 사진'), h('dd', {}, status.image ? `${status.image.provider}${status.image.enabled ? '' : ' (비활성)'}` : '—'),
      h('dt', {}, 'Draft 요청'), h('dd', {}, status.draft?.mode === 'draft_with_visuals' ? 'P06 신상품·착장 이미지 포함' : (status.draft?.mode ?? '—')),
      h('dt', {}, '질의 구조화'), h('dd', {}, status.query_structuring?.mode || 'llm'),
      h('dt', {}, '실행 모드'), h('dd', {}, status.runtime ?? '—')));
}
