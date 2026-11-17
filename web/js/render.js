// DOM rendering for the workspace. Data is inserted with textContent/attributes
// only (never innerHTML), so producer text cannot inject markup.
// One fact, one place: sections print readable summaries; evidence and JSON are printed only in the side panel.
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

// replaceChildren() would print an empty slot (null) as the text "null"; drop empty slots first.
function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
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

// Status set in the line of text (icon + label), used inside lists and rows.
function statusText(status) {
  const meta = model.statusMeta(status);
  return h('span', { class: `status-text tone-${meta.tone}` }, statusIcon(meta.tone), meta.label);
}

function sectionHead({ id, n, title, meta }) {
  return h('header', { class: 'section-head' },
    h('h2', { id }, n ? h('span', { class: 'section-n' }, h('span', {}, n)) : null, h('span', {}, title)),
    meta ? h('span', { class: 'section-meta' }, meta) : null);
}

function emptyNote(text) {
  return h('p', { class: 'empty-note' }, text);
}

// A button that opens the side panel on a tab and scrolls it to an anchor.
function panelLink(label, tab, anchor, { quiet = false } = {}) {
  return h('button', { type: 'button', class: `panel-link${quiet ? ' is-quiet' : ''}`, dataset: { panelTab: tab, panelAnchor: anchor } },
    h('span', {}, label),
    h('span', { class: 'panel-link-arrow', 'aria-hidden': 'true' }));
}

// Errors the orchestrator recorded for a stage, printed once at the top of that stage's section.
function stageErrorList(errors) {
  return errors?.length
    ? h('ul', { class: 'stage-errors' }, errors.map((error) => h('li', {},
        statusIcon('error'), h('span', {}, h('strong', {}, error.code), error.message ? ` ${error.message}` : ''))))
    : null;
}

function foldList(title, items) {
  return items?.length
    ? h('details', { class: 'limits' }, h('summary', {}, `${title} ${items.length}건`),
        h('ul', {}, items.map((item) => h('li', {}, typeof item === 'string' ? item : JSON.stringify(item)))))
    : null;
}

// --- Spine: progress bar and stage links -----------------------------------------

// Stages with a page section: their sidebar row scrolls to it.
export const STAGE_TARGET = Object.freeze({
  trend: 'trend-block', stock: 'stock-block', planning_draft: 'draft-block', sales_market_review: 'market-block', integration: 'concept', final_image: 'lookbook',
});

// Stages without a page section (03 Draft, 04 Review): once they have reported, their sidebar row opens the
// side panel's JSON tab at that stage, where its errors and raw response are printed.
// Keep these for raw JSON/error fallback rendering; both stages now have page sections.
export const PANEL_STAGES = Object.freeze(['planning_draft', 'sales_market_review']);

export function panelStageAnchor(stage) {
  return `json-stage-${stage}`;
}

const SETTLED = new Set(['ok', 'partial', 'none', 'error']);

function railLink(item, { linked, reported }) {
  if (!linked) return h('span', { class: 'stage-link' });
  if (STAGE_TARGET[item.key]) return h('a', { class: 'stage-link', href: `#${STAGE_TARGET[item.key]}` });
  if (!STAGE_TARGET[item.key] && PANEL_STAGES.includes(item.key) && reported.has(item.key)) {
    return h('button', { type: 'button', class: 'stage-link', title: `${item.ko} 오류·원문을 패널에서 보기`,
      dataset: { panelTab: 'json', panelAnchor: panelStageAnchor(item.key) } });
  }
  return h('span', { class: 'stage-link' });
}

export function renderRail(list, items, { linked = false, reported = new Set() } = {}) {
  const previous = new Map([...list.children].map((li) => [li.dataset.stage, li]));
  const rows = items.map((item) => {
    const li = previous.get(item.key) ?? h('li', { dataset: { stage: item.key, target: STAGE_TARGET[item.key] } });
    const before = li.dataset.tone;
    const here = li.classList.contains('is-here');
    li.className = `stage tone-${item.tone}${here ? ' is-here' : ''}`;
    if (before && !SETTLED.has(before) && SETTLED.has(item.tone)) {
      li.classList.add('just-settled');
      li.addEventListener('animationend', () => li.classList.remove('just-settled'), { once: true });
    }
    li.dataset.tone = item.tone;
    // Update the link in place so keyboard focus survives the 1.5 s polling re-render.
    let link = li.firstElementChild;
    const fresh = railLink(item, { linked, reported });
    if (!link || link.tagName !== fresh.tagName) {
      link = fresh;
      li.replaceChildren(link);
    }
    const opensPanel = link.tagName === 'BUTTON';
    fill(link,
      h('span', { class: 'stage-n' }, item.n),
      h('span', { class: 'stage-name' }, item.ko),
      h('span', { class: 'stage-status' }, statusIcon(item.tone), item.label),
      opensPanel ? h('span', { class: 'stage-panel-arrow', 'aria-hidden': 'true' }) : null);
    return li;
  });
  fill(list, rows);
}

export function renderProgress({ count, state, root }, items, { status = null, inFlight = false } = {}) {
  const { done, total, running } = model.railProgress(items);
  count.textContent = `${done} / ${total}`;
  state.textContent = running ? `${running.n} ${running.ko} 진행 중`
    : status ? `실행 결과 ${model.statusMeta(status).label}` : inFlight ? '실행 준비 중' : '실행 전';
  root.style.setProperty('--progress', String(total ? done / total : 0));
  document.body.dataset.run = inFlight ? 'running' : status ? 'done' : 'idle';
  return { done, total, running };
}

// A section whose stage has not reported yet: a glass skeleton that fills in when the result arrives.
export function renderWaiting(section, { id, n, title }, status) {
  fill(section, sectionHead({ id, n, title }),
    h('div', { class: 'skeleton', 'aria-hidden': 'true' }, h('span', {}), h('span', {}), h('span', {})),
    h('p', { class: 'waiting' }, status === 'running' ? '이 단계를 실행하고 있습니다. 결과가 도착하면 이 자리에 채워집니다.' : '앞 단계가 끝나면 시작합니다.'));
}

// --- Concept: integrated draft and the lookbook ----------------------------
// The 기획서 (stage 06 PNG composed by P08's planning_sheet.py) is the fixed deliverable: it is shown
// whole and flat, never cropped, tilted or restyled. P06's two visuals flank it.

function sheetFigure(image, draft, sheet, waiting) {
  if (image.status === 'generated' && image.artifact?.artifact_id) {
    const src = artifactUrl(image.artifact.artifact_id);
    return h('figure', { class: `look look-sheet${sheet ? ' is-sheet' : ''}` },
      h('a', { class: 'look-frame', href: src, target: '_blank', rel: 'noopener', title: '원본 크기로 열기' },
        h('img', { src, alt: sheet ? `${draft?.title ?? '기획'} — 기획안 사진 (P08 합성)` : `${draft?.title ?? '기획'} — 시장 검토 이후 생성한 최종 기획 이미지`, loading: 'lazy' })),
      h('figcaption', {}, h('strong', {}, '기획안 사진'), sheet ? ' P08 합성' : ' 최종 기획 이미지'));
  }
  const label = waiting === 'running' ? '기획서를 합성하고 있습니다' : waiting ? '기획서 합성 대기'
    : image.status === 'failed' ? '이미지 생성 실패' : image.status === 'skipped' ? '이미지 생략' : '이미지 없음';
  const reason = waiting ? '시장 검토와 통합이 끝나면 P08이 한 번 그립니다.' : (image.reason || '시장 검토가 끝난 뒤에만 한 번 생성합니다.');
  return h('figure', { class: `look look-sheet is-empty state-${waiting ? 'waiting' : image.status ?? 'none'}` },
    h('div', { class: 'look-frame look-empty' }, h('strong', {}, label), h('span', {}, reason)),
    h('figcaption', {}, h('strong', {}, '기획안 사진')));
}

function visualFigure(view, item, side) {
  return h('figure', { class: `look look-side look-${side} state-${item.status ?? 'none'}` },
    item.sha256
      ? h('a', { class: 'look-frame', href: artifactUrl(item.sha256), target: '_blank', rel: 'noopener', title: '원본 크기로 열기' },
          h('img', { src: artifactUrl(item.sha256), alt: `${view.title} — ${item.label} 시안 (P06)`, loading: 'lazy' }))
      : h('div', { class: 'look-frame look-empty' }, item.reason || '이미지 없음'),
    h('figcaption', {}, h('strong', {}, item.label), ` ${model.statusMeta(item.status).label}${item.model ? ` · ${item.model}` : ''}`));
}

export function renderDraftStage(section, draft, { errors = [] } = {}) {
  const meta = model.statusMeta(draft?.status);
  if (!draft) {
    fill(section, sectionHead({ id: 'draft-title', n: '03', title: '새상품 생성' }), stageErrorList(errors),
      emptyNote(errors.length ? 'P06 응답에 새상품 결과가 없습니다.' : 'P06 응답을 아직 받지 않았습니다.'));
    return;
  }
  const profile = [
    ['카테고리', draft.category], ['상품 종류', draft.productType], ['성별', draft.gender],
    ['시즌', draft.season], ['소재', draft.material],
  ].filter(([, value]) => typeof value === 'string' && value.trim());
  const visuals = draft.visuals ?? [];
  const evidence = (draft.evidenceClaims ?? []).map((item) => h('li', {},
    h('span', {}, item.text), item.refs ? h('small', {}, `근거 ${item.refs}건`) : null));
  fill(section,
    sectionHead({ id: 'draft-title', n: '03', title: '새상품 생성', meta: `P06 · ${meta.label}` }),
    stageErrorList(errors),
    h('div', { class: 'draft-stage-main' },
      h('div', { class: 'draft-stage-copy' },
        h('p', { class: 'draft-stage-name' }, draft.title),
        draft.concept ? h('p', { class: 'draft-stage-definition' }, draft.concept) : null,
        profile.length ? h('ul', { class: 'draft-profile' }, profile.map(([label, value]) => h('li', {}, h('strong', {}, `${label} · `), value))) : null),
      visuals.length ? h('div', { class: 'draft-visuals' }, visuals.map((item, index) => visualFigure(draft, item, index ? 'right' : 'left'))) : null),
    evidence.length ? h('div', { class: 'draft-evidence' }, h('h3', {}, '근거'), h('ul', {}, evidence)) : null,
    foldList('기획 한계', draft.limitations));
}

export function renderConcept(section, envelope, { retryEnabled = false, onRetry, errors = [], limitations = [], sheetLink = null, sheetWaiting = null } = {}) {
  const result = envelope.result ?? {};
  const draft = model.draftView(model.sourceResult(result, 'planning_draft'));
  const image = result.final_image ?? {};
  // 0.2.0: the visuals are P06's; stage 06 is the planning sheet composed by P08.
  const visuals = draft?.visuals ?? [];
  const sheet = Boolean(visuals.length);
  const product = visuals.find((item) => item.key === 'product');
  const outfit = visuals.find((item) => item.key === 'outfit');
  const retry = retryEnabled && image.status === 'failed' && typeof onRetry === 'function'
    ? h('button', { type: 'button', class: 'btn btn-ghost', onClick: onRetry }, '이미지 단계만 다시 시도')
    : null;
  fill(section,
    h('div', { class: 'feature-intro' },
      h('div', { class: 'feature-head' },
        h('p', { class: 'eyebrow' }, 'INTEGRATED DRAFT'),
        h('h2', { id: 'concept-title', class: 'concept-title' }, draft?.title ?? '통합 결과')),
      h('div', { class: 'feature-copy' },
        h('p', { class: 'muted' }, '기획 초안과 시장 검토를 통합한 결과입니다. 가격·원가·수량·발주·최종 채택은 MD가 결정합니다.'),
        sheetLink || retry ? h('div', { class: 'feature-actions' },
          sheetLink ? h('a', { class: 'btn btn-glass', href: sheetLink.href, download: sheetLink.filename }, '기획서 PNG 저장') : null,
          retry) : null,
        stageErrorList(errors),
        foldList('통합 제한', limitations))),
    h('div', { class: `lookbook${sheet ? '' : ' is-single'}`, id: 'lookbook', tabindex: '-1', role: 'group', 'aria-label': sheet ? '기획안 사진과 P06 시안 이미지' : '기획안 사진' },
      product ? visualFigure(draft, product, 'left') : null,
      sheetFigure(image, draft, sheet, sheetWaiting),
      outfit ? visualFigure(draft, outfit, 'right') : null),
    sheet ? h('p', { class: 'small muted lookbook-note' }, 'P06(new-product-agent)가 트렌드 근거로 만든 검토용 시안') : null);
}

// --- Trend -----------------------------------------------------------------
// Cards carry the style and every item it cites, each with its Vogue article photos in a strip that scrolls
// sideways inside the card; quotes, articles and the style's example-image links open in the side panel.
// The browser loads the photos from img.vogue.co.kr directly (CSP img-src) without sending a Referer.

function photoMissing(frame, text) {
  frame.classList.add('is-missing');
  fill(frame, h('span', {}, text));
}

function itemPhoto(name, url, index, total) {
  const frame = h('a', { class: 'item-photo', href: url, target: '_blank', rel: 'noopener noreferrer', title: '원본 사진 새 탭에서 열기' });
  const img = h('img', { src: url, alt: `${name} — Vogue 기사 사진${total > 1 ? ` ${index + 1}` : ''}`, loading: 'lazy', decoding: 'async', referrerpolicy: 'no-referrer' });
  img.addEventListener('error', () => photoMissing(frame, '사진을 불러오지 못했습니다'), { once: true });
  frame.append(img);
  return frame;
}

// Marks a strip that overflows its card (shows the hint and the right-edge fade) and whether it is scrolled to the end.
const stripWatch = 'ResizeObserver' in window ? new ResizeObserver((entries) => entries.forEach((entry) => markStrip(entry.target))) : null;

function markStrip(strip) {
  const block = strip.parentElement;
  if (!block) return;
  const overflowing = strip.scrollWidth - strip.clientWidth > 2;
  block.classList.toggle('is-overflowing', overflowing);
  block.classList.toggle('at-end', !overflowing || strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 2);
}

function itemStrip(tiles) {
  // The scroll container is a labelled region (keyboard-scrollable); the items inside stay a list.
  const strip = h('div', { class: 'item-strip', role: 'region', tabindex: '0', 'aria-label': `인용 아이템 ${tiles.length}개` },
    // An item without a photo is its name and a note, with no empty frame.
    h('ul', { class: 'item-list' }, tiles.map((tile) => tile.photos.length
      ? h('li', { class: 'item-tile' },
          h('div', { class: 'item-photos' }, tile.photos.map((url, index) => itemPhoto(tile.name, url, index, tile.photos.length))),
          h('span', { class: 'item-name' }, tile.name))
      : h('li', { class: 'item-tile is-photoless' },
          h('span', { class: 'item-name' }, tile.name),
          h('span', { class: 'item-none' }, '관련 사진 없음')))));
  strip.addEventListener('scroll', () => markStrip(strip), { passive: true });
  stripWatch?.observe(strip);
  return h('div', { class: 'item-block' },
    h('p', { class: 'item-count', 'aria-hidden': 'true' }, h('span', {}, `인용 아이템 ${tiles.length}`), h('span', { class: 'item-hint' }, '옆으로 넘겨 보기 →')),
    strip);
}

export function renderTrends(section, cards, { errors = [], groups = [] } = {}) {
  const grid = h('div', { class: 'entry-grid' });
  cards.forEach((card, index) => {
    const group = groups.find((g) => g.anchor === `ev-trend-${index}`);
    const count = group?.items.length ?? 0;
    const label = count ? `근거 ${count}건 보기` : group?.sharedCount ? '근거 보기 · 앞 스타일과 같음' : null;
    grid.append(h('article', { class: 'entry trend-card' },
      h('div', { class: 'entry-top' },
        h('span', { class: 'rank' }, h('span', { class: 'numeral' }, card.rank)),
        card.seasons ? h('span', { class: 'muted small' }, card.seasons) : null),
      h('h3', {}, card.name),
      card.definition ? h('p', { class: 'muted small' }, card.definition) : null,
      card.itemTiles.length ? itemStrip(card.itemTiles) : null,
      label ? panelLink(label, 'evidence', group.anchor) : null));
  });
  fill(section, sectionHead({ id: 'trend-title', n: '01', title: '트렌드 근거', meta: 'Vogue Korea 기사 기반' }),
    stageErrorList(errors), cards.length ? grid : emptyNote('트렌드 결과가 없습니다.'));
}

// --- Stock -----------------------------------------------------------------

export function renderStock(section, slots, { errors = [] } = {}) {
  const grid = h('div', { class: 'entry-grid' });
  for (const slot of slots) {
    const product = slot.product;
    const body = product
      ? [h('div', { class: 'product' },
          product.image ? h('img', { class: 'product-image', src: product.image.src, alt: `${slot.styleName} 매칭 상품 이미지` }) : h('div', { class: 'product-image is-empty', role: 'img', 'aria-label': '상품 이미지 없음' }),
          h('div', { class: 'product-body' },
            h('strong', {}, `${product.category} · ${product.color ?? '색상 미확인'}`),
            h('span', { class: 'muted small' }, [product.materials.join(', '), product.fit].filter(Boolean).join(' · ') || '소재·핏 미확인'),
            h('span', { class: 'muted small' }, product.quantity === null ? '재고 수량 미확인' : `가상 재고 ${product.quantity.toLocaleString('ko-KR')}개`),
            h('span', { class: 'muted small' }, [product.dataNature ?? 'sample', product.asOf ? `기준 ${product.asOf}` : null].filter(Boolean).join(' · ')))),
        slot.reason ? h('p', { class: 'small' }, slot.reason) : null]
      : [h('div', { class: 'no-match' }, h('strong', {}, '매칭 없음 · no_results'), h('span', {}, '재고가 0이라는 뜻이 아닙니다. 조건에 맞는 후보를 찾지 못했습니다.'))];
    grid.append(h('article', { class: 'entry stock-card' },
      h('div', { class: 'entry-top' }, h('span', { class: 'muted small' }, slot.styleName), statusText(slot.status)), body));
  }
  fill(section, sectionHead({ id: 'stock-title', n: '02', title: '재고 매칭', meta: '스타일별 과잉 재고 후보' }),
    stageErrorList(errors), slots.length ? grid : emptyNote('재고 단계 결과가 없습니다.'));
}

function marketValueCard(section) {
  return h('article', { class: 'market-value' },
    h('p', { class: 'market-value-label' }, section.label),
    h('strong', { class: 'market-value-number' }, section.value),
    section.note ? h('p', { class: 'market-value-note' }, section.note) : null);
}

export function renderMarket(container, market, { errors = [] } = {}) {
  if (!market) {
    fill(container, sectionHead({ id: 'market-title', n: '04', title: '판매·시장 분석' }),
      stageErrorList(errors), emptyNote(errors.length ? 'P07 응답에 판매·시장 결과가 없습니다.' : 'P07 응답을 아직 받지 않았습니다.'));
    return;
  }
  const meta = model.statusMeta(market.status);
  fill(container,
    sectionHead({ id: 'market-title', n: '04', title: '판매·시장 분석', meta: `P07 · ${meta.label}` }),
    h('p', { class: 'small muted market-note' }, 'P07 분석 핵심 요약 · 상세 응답은 근거·원문 패널에서 확인할 수 있습니다.'),
    stageErrorList(errors),
    market.sections.length
      ? h('div', { class: 'market-grid' }, market.sections.map(marketValueCard))
      : emptyNote('P07 응답에 표시할 요약 결과가 없습니다.'),
    market.missingData.length ? h('p', { class: 'small muted market-status-note' }, `미확인 자료 ${market.missingData.length}개 · ${market.missingData.slice(0, 2).join(', ')}`) : null,
    market.errors.length ? h('p', { class: 'small market-error-note' }, `P07 분석 오류 ${market.errors.length}건 · ${market.errors.slice(0, 2).join(', ')}`) : null,
    market.limitations.length ? h('details', { class: 'limits' }, h('summary', {}, `분석 한계 ${market.limitations.length}건`),
      h('ul', {}, market.limitations.map((item) => h('li', {}, item)))) : null);
}

// --- MD decision -------------------------------------------------------------

const CHOICES = [['adopt', '채택'], ['revise', '수정'], ['hold', '보류']];
const CHOICE_LABEL = Object.fromEntries(CHOICES);

export function renderMd(section, { decisions, selected, busy, message, disabled, onSelect, onSave }) {
  const note = h('textarea', { id: 'md-note', rows: 4, maxlength: 2000, disabled: disabled || busy });
  const group = h('div', { class: 'segmented', role: 'group', 'aria-label': '결정 선택' },
    CHOICES.map(([value, label]) => h('button', { type: 'button', 'aria-pressed': String(selected === value), disabled: disabled || busy, onClick: () => onSelect(value) }, label)));
  const save = h('button', { type: 'button', class: 'btn btn-dark', disabled: disabled || busy || !selected, onClick: () => onSave(note.value.trim()) }, busy ? '저장 중…' : '결정 저장');
  const history = decisions.length
    ? h('ol', { class: 'decision-list' }, [...decisions].reverse().map((d) => h('li', {}, h('strong', {}, CHOICE_LABEL[d.decision] ?? d.decision), h('span', { class: 'muted small' }, d.decided_at ?? d.created_at ?? ''), d.note ? h('span', { class: 'small as-block' }, d.note) : null)))
    : h('p', { class: 'small muted' }, '아직 저장된 결정이 없습니다.');
  fill(section,
    sectionHead({ id: 'md-title', title: 'MD 판단' }),
    h('div', { class: 'md-grid' },
      h('div', { class: 'md-form' },
        group,
        h('label', { for: 'md-note', class: 'field-label' }, '판단 메모 (선택, 2,000자 이내)'),
        note,
        h('div', { class: 'md-actions' }, save, message ? h('p', { class: 'small notice-inline', role: 'status' }, message) : null)),
      h('div', { class: 'md-side' },
        h('p', { class: 'small muted' }, '결정은 통합 결과 해시에 묶여 기록되며, 결과 원문은 바뀌지 않습니다. 가격·원가·수량·발주는 이 화면에서 정하지 않습니다.'),
        history)));
}

// --- Side panel: evidence and JSON -----------------------------------------------

function lazyJson(value) {
  const pre = h('pre', { class: 'json' });
  return [pre, (event) => { if (event.currentTarget.open && !pre.textContent) pre.textContent = model.compactJson(value); }];
}

function jsonFold(anchor, label, value, extra = null) {
  const [pre, onToggle] = lazyJson(value);
  return h('details', { class: 'json-fold', id: anchor, onToggle }, h('summary', {}, h('span', { class: 'mono' }, label), extra), pre);
}

function evidenceEntry(item) {
  const [pre, onToggle] = lazyJson(item.raw);
  return h('li', { class: 'ev-item' },
    item.text ? h('blockquote', { class: 'quote' }, item.raw?.quote ? `“${item.text}”` : item.text) : null,
    item.sameTextAs ? h('p', { class: 'small muted' }, `내용은 ${item.sameTextAs}와 같습니다.`) : null,
    item.url ? h('a', { class: 'link', href: item.url, target: '_blank', rel: 'noopener noreferrer' }, `${item.title || '원문'}${item.date ? ` · ${item.date}` : ''}`)
      : h('p', { class: 'small muted' }, [item.title, item.detail, item.date].filter(Boolean).join(' · ') || '출처 정보 없음'),
    h('details', { class: 'ev-raw', onToggle }, h('summary', {}, h('span', { class: 'mono' }, item.id ?? 'evidence'), ' JSON'), pre));
}

export function renderEvidence(container, { result, groups, p07Request, p07RequestId }, tab) {
  if (!result) { fill(container, emptyNote('실행 결과가 없습니다.')); return; }
  if (tab === 'json') {
    // 03 and 04 keep a block when their call failed before a response was stored, so their errors stay reachable.
    const stored = model.panelJson(result);
    const stages = model.STAGES.map((def) => stored.find((stage) => stage.key === def.key)
      ?? (PANEL_STAGES.includes(def.key) && model.stageErrors(result, def.key).length ? { ...def, fields: [], unstored: true } : null))
      .filter(Boolean);
    const blocks = stages.map((stage) => {
      const fields = stage.fields;
      const request = stage.key === 'sales_market_review' && p07Request
        ? jsonFold('json-p07-request', 'P07 요청 원문', p07Request) : null;
      const missingRequest = stage.key === 'sales_market_review' && !p07Request && p07RequestId
        ? h('p', { class: 'small muted', id: 'json-p07-request', tabindex: '-1' }, 'P07 요청 원문을 아직 불러오지 못했습니다.') : null;
      // 03 and 04 have no page section, so the errors the orchestrator recorded for them are printed here.
      const panelOnly = PANEL_STAGES.includes(stage.key);
      const errors = panelOnly ? model.stageErrors(result, stage.key) : [];
      return h('section', { class: 'json-stage', id: panelStageAnchor(stage.key), tabindex: '-1' },
        h('h3', {}, h('span', { class: 'stage-mini' }, stage.n), stage.ko),
        panelOnly ? stageErrorList(errors) ?? h('p', { class: 'small muted' }, '이 단계에 기록된 오류가 없습니다.') : null,
        stage.unstored ? h('p', { class: 'small muted' }, '이 단계의 응답 원문은 보존되지 않았습니다.') : null,
        request, missingRequest,
        fields.map((field) => field.pointer
          ? h('p', { class: 'json-line', id: field.anchor, tabindex: '-1' }, h('span', { class: 'mono' }, field.path), ` ${field.count}건`,
              panelLink('근거 탭에서 보기', 'evidence', `ev-${stage.key}`, { quiet: true }))
          : field.sameAs
            ? h('p', { class: 'json-line', id: field.anchor, tabindex: '-1' }, h('span', { class: 'mono' }, field.path), ' 위와 같은 값',
                panelLink(field.sameAs.path, 'json', field.sameAs.anchor, { quiet: true }))
          : field.empty
            ? h('p', { class: 'json-line', id: field.anchor, tabindex: '-1' }, h('span', { class: 'mono' }, field.path), ' 없음')
            : jsonFold(field.anchor, field.path, field.value)));
    });
    fill(container, blocks.length ? blocks : emptyNote('보존된 원문이 없습니다.'));
    return;
  }
  fill(container, groups.length ? groups.map((group) => {
    const open = group.stage === 'trend';
    return h('details', { class: 'ev-group', id: group.anchor, open: open || null },
      h('summary', {}, h('span', { class: 'ev-title' }, group.title), h('span', { class: 'ev-count' }, `${group.items.length}건`)),
      group.sharedCount ? h('p', { class: 'small muted' }, `같은 근거 ${group.sharedCount}건은 앞 스타일 묶음에 있습니다.`) : null,
      group.items.length ? h('ol', { class: 'ev-list' }, group.items.map(evidenceEntry)) : null,
      group.exampleLinks.length ? h('p', { class: 'link-row' }, '예시 이미지 ', group.exampleLinks.map((link, i) => h('a', { class: 'link', href: link.url, target: '_blank', rel: 'noopener noreferrer', title: link.basis }, `${i + 1}`))) : null);
  }) : emptyNote('보존된 근거가 없습니다.'));
}

// --- Runs & system ---------------------------------------------------------

export function renderRuns(list, runs, onOpen) {
  if (!runs.length) { fill(list, h('li', { class: 'empty-note' }, '아직 실행 이력이 없습니다.')); return; }
  fill(list, runs.map((run) => h('li', { class: 'run-item' },
    h('div', { class: 'run-main' }, h('strong', {}, run.query_excerpt || run.request_id), h('span', { class: 'muted small mono' }, run.request_id)),
    h('div', { class: 'run-meta' }, chip(run.status), h('span', { class: 'muted small' }, run.created_at ?? ''),
      h('button', { type: 'button', class: 'btn btn-ghost', onClick: () => onOpen(run.request_id) }, '열기')))));
}

export function renderSystem(container, status) {
  if (!status) { fill(container, emptyNote('시스템 상태를 불러오지 못했습니다.')); return; }
  const agents = Array.isArray(status.agents) ? status.agents : [];
  fill(container,
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
