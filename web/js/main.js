// VOGUE Atelier — MD workspace controller.
import * as api from './api.js';
import * as model from './model.js';
import * as view from './render.js';

const $ = (selector) => document.querySelector(selector);
const LAST_RUN_KEY = 'vogue-atelier:last-request-id';
const POLL_MS = 1500;
const SYSTEM_REFRESH_MS = 30000;
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)');

const el = {
  form: $('#brief-form'), brief: $('#brief'), counter: $('#brief-counter'), run: $('#run-button'), runLabel: $('#run-label'), notice: $('#notice'),
  rail: $('#rail'), progressCount: $('#progress-count'), progressState: $('#progress-state'),
  results: $('#results'), concept: $('#concept'), trends: $('#trend-block'), stock: $('#stock-block'), draft: $('#draft-block'), market: $('#market-block'),
  md: $('#md-card'), mdLink: $('#md-link'),
  evidenceButton: $('#evidence-button'), evidenceDialog: $('#evidence-dialog'), evidenceBody: $('#evidence-body'),
  systemButton: $('#system-button'), systemDot: $('#system-dot'), systemSummary: $('#system-summary'), systemDialog: $('#system-dialog'), systemBody: $('#system-body'),
  workspace: $('#workspace'), runsView: $('#runs'), runsList: $('#runs-list'), runsRefresh: $('#runs-refresh'),
};

// Page sections follow their execution stages; the lead concept card remains the integrated result.
const SECTIONS = [
  { el: el.concept, stage: 'integration', head: { id: 'concept-title', title: '통합 기획안' } },
  { el: el.trends, stage: 'trend', head: { id: 'trend-title', n: '01', title: '트렌드 근거' } },
  { el: el.stock, stage: 'stock', head: { id: 'stock-title', n: '02', title: '재고 매칭' } },
  { el: el.draft, stage: 'planning_draft', head: { id: 'draft-title', n: '03', title: '새상품 생성' } },
  { el: el.market, stage: 'sales_market_review', head: { id: 'market-title', n: '04', title: '판매·시장 분석' } },
];

const state = {
  envelope: null, requestId: null, inFlight: false, poller: null,
  p07CallRequest: null, p07CallRequestArtifactId: null, p07CallRequestState: 'unavailable',
  md: { decisions: [], integratedSha: null, selected: null, busy: false, message: '' },
  system: null, evidenceTab: 'evidence',
};

function showNotice(text) {
  el.notice.textContent = text;
  el.notice.hidden = !text;
}

function setRunning(running) {
  state.inFlight = running;
  el.run.disabled = running;
  el.runLabel.textContent = running ? '실행 중…' : '기획 시작';
  el.brief.readOnly = running;
  document.body.dataset.run = running ? 'running' : (state.envelope ? 'done' : 'idle');
}

function updateCounter() {
  el.counter.textContent = `${el.brief.value.length.toLocaleString('ko-KR')} / ${model.MAX_BRIEF.toLocaleString('ko-KR')}`;
}

// --- Motion: sections clear like frosted glass when their result is in and they come into view ---------

const motion = 'IntersectionObserver' in window && !REDUCED.matches;
if (motion) document.documentElement.classList.add('has-motion');
const visible = new Set();

function revealIfReady(section) {
  if (section.dataset.state === 'settled' && (!motion || visible.has(section))) section.classList.add('is-revealed');
}

const revealer = motion ? new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (entry.isIntersecting) { visible.add(entry.target); revealIfReady(entry.target); } else visible.delete(entry.target);
  }
}, { threshold: 0.06 }) : null;

// Scrollspy: the stage whose section crosses the upper third of the viewport is marked in the sidebar.
let here = null;
function markHere() {
  for (const li of el.rail.children) {
    const target = document.getElementById(li.dataset.target);
    li.classList.toggle('is-here', Boolean(here && target && (target === here || here.contains(target))));
  }
}
const spy = 'IntersectionObserver' in window ? new IntersectionObserver((entries) => {
  for (const entry of entries) if (entry.isIntersecting) { here = entry.target; markHere(); }
}, { rootMargin: '-30% 0px -66% 0px' }) : null;

for (const section of [...SECTIONS.map((s) => s.el), el.md]) {
  section.tabIndex = -1;
  revealer?.observe(section);
  spy?.observe(section);
}

function setSectionState(section, value) {
  if (section.dataset.state !== value && value !== 'settled') section.classList.remove('is-revealed');
  section.dataset.state = value;
  revealIfReady(section);
}

// --- Rendering -------------------------------------------------------------

function renderSpine(result, { inFlight = false } = {}) {
  const items = model.rail(result, { inFlight });
  // A stage has something to open in the side panel once it has a stored response or a recorded error.
  const reported = new Set([...Object.keys(result?.sources ?? {}), ...(Array.isArray(result?.errors) ? result.errors.map((error) => error?.stage) : [])]);
  view.renderRail(el.rail, items, { linked: !el.results.hidden, reported });
  const progress = view.renderProgress({ count: el.progressCount, state: el.progressState, root: document.documentElement }, items,
    { status: inFlight ? null : result?.status ?? null, inFlight });
  if (state.inFlight) el.runLabel.textContent = progress.running ? `실행 중 · ${progress.running.n} ${progress.running.ko}` : '실행 중…';
  el.mdLink.hidden = el.results.hidden;
  el.evidenceButton.hidden = el.results.hidden || !result;
  markHere();
  return Object.fromEntries(items.map((item) => [item.key, item.status]));
}

// Producer limitations live in the side panel's raw JSON; the concept lists only the run's own.
function runOnlyLimitations(result) {
  const draft = model.draftView(model.sourceResult(result, 'planning_draft'));
  const review = model.reviewView(model.sourceResult(result, 'sales_market_review'));
  return model.firstOccurrences([draft?.limitations, review?.limitations, result?.limitations])[2];
}

function renderEnvelope(envelope, { inFlight = false } = {}) {
  const result = envelope?.result;
  if (!result) return;
  state.envelope = envelope;
  const p07ArtifactId = result.sources?.sales_market_review?.call_request_artifact?.artifact_id ?? null;
  if (p07ArtifactId !== state.p07CallRequestArtifactId) {
    state.p07CallRequestArtifactId = p07ArtifactId;
    state.p07CallRequest = null;
    state.p07CallRequestState = p07ArtifactId ? 'loading' : 'unavailable';
  }
  el.results.hidden = false;
  const stageStatus = renderSpine(result, { inFlight });
  // Only a stage that has not run yet waits; a stage skipped after a stop prints its (empty) result.
  const waiting = (key) => (stageStatus[key] === 'running' || stageStatus[key] === 'pending' ? stageStatus[key] : null);
  const runLimits = runOnlyLimitations(result);
  const groups = model.evidenceGroups(result);

  const section = (index, render) => {
    const { el: node, stage, head } = SECTIONS[index];
    const wait = waiting(stage);
    if (wait) view.renderWaiting(node, head, wait); else render(node);
    setSectionState(node, wait ?? 'settled');
  };
  section(0, (node) => view.renderConcept(node, envelope, {
    retryEnabled: Boolean(state.system?.image?.retry_enabled), onRetry: retryImage,
    errors: model.stageErrors(result, ['integration', 'final_image']), limitations: runLimits,
    sheetLink: model.planningSheetLink({ requestId: result.request_id ?? envelope.request_id, status: result.status }, { inFlight }),
    sheetWaiting: waiting('final_image'),
  }));
  section(1, (node) => view.renderTrends(node, model.trendCards(model.sourceResult(result, 'trend')), { errors: model.stageErrors(result, 'trend'), groups }));
  section(2, (node) => view.renderStock(node, model.stockSlots(model.sourceResult(result, 'stock')), { errors: model.stageErrors(result, 'stock') }));
  section(3, (node) => view.renderDraftStage(node, model.draftView(model.sourceResult(result, 'planning_draft')), { errors: model.stageErrors(result, 'planning_draft') }));
  section(4, (node) => view.renderMarket(node, model.marketView(model.sourceResult(result, 'sales_market_review')), { errors: model.stageErrors(result, 'sales_market_review') }));
  void loadP07CallRequest(result);
  setSectionState(el.md, 'settled');
  renderMd();
  if (el.evidenceDialog.open) renderEvidence();
}

// The P07 request is printed in the side panel's JSON tab ("P07 요청 원문").
async function loadP07CallRequest(result) {
  const artifactId = result?.sources?.sales_market_review?.call_request_artifact?.artifact_id;
  if (!artifactId || artifactId !== state.p07CallRequestArtifactId || state.p07CallRequest || state.p07CallRequestState !== 'loading') return;
  if (!/^[a-f0-9]{64}$/.test(artifactId)) {
    state.p07CallRequestState = 'error';
    return;
  }
  const requestId = result?.request_id;
  const response = await api.getArtifact(artifactId);
  if (state.requestId !== requestId || state.p07CallRequestArtifactId !== artifactId) return;
  state.p07CallRequest = response.ok && response.body && typeof response.body === 'object' && !Array.isArray(response.body) ? response.body : null;
  state.p07CallRequestState = state.p07CallRequest ? 'loaded' : 'error';
  if (el.evidenceDialog.open) renderEvidence();
}

function renderMd() {
  view.renderMd(el.md, {
    ...state.md,
    disabled: state.inFlight || !state.md.integratedSha,
    onSelect: (value) => { state.md.selected = value; state.md.message = ''; renderMd(); },
    onSave: saveDecision,
  });
}

async function loadDecisions(requestId) {
  const response = await api.getDecisions(requestId);
  if (response.ok && response.body) {
    state.md.decisions = Array.isArray(response.body.decisions) ? response.body.decisions : [];
    state.md.integratedSha = response.body.integrated_response_sha256 ?? null;
  } else {
    state.md.decisions = [];
    state.md.integratedSha = null;
  }
  renderMd();
}

async function saveDecision(note) {
  if (!state.requestId || !state.md.selected) return;
  state.md.busy = true; state.md.message = ''; renderMd();
  const response = await api.saveDecision(state.requestId, { integratedSha: state.md.integratedSha, decision: state.md.selected, note });
  state.md.busy = false;
  state.md.message = response.ok ? '결정을 저장했습니다.' : model.problemMessage(response.body, response.status);
  if (response.ok) state.md.selected = null;
  await loadDecisions(state.requestId);
}

async function retryImage() {
  if (!state.requestId) return;
  const response = await api.retryImage(state.requestId, crypto.randomUUID());
  if (response.body?.result) renderEnvelope(response.body);
  if (!response.ok) showNotice(model.problemMessage(response.body, response.status));
}

// A fresh run: every section is laid out as a waiting glass skeleton, then fills stage by stage.
function resetSections() {
  const items = model.rail(null, { inFlight: true });
  for (const { el: node, stage, head } of SECTIONS) {
    const status = items.find((item) => item.key === stage)?.status ?? 'pending';
    view.renderWaiting(node, head, status);
    setSectionState(node, status);
  }
}

// --- Navigation and the side panel -------------------------------------------------

function scrollBehavior() {
  return REDUCED.matches ? 'auto' : 'smooth';
}

function jumpTo(target) {
  if (!el.runsView.hidden) { history.pushState(null, '', '#workspace'); route(); }
  target.scrollIntoView({ behavior: scrollBehavior(), block: 'start' });
  target.focus({ preventScroll: true });
}

function renderEvidence() {
  for (const tab of el.evidenceDialog.querySelectorAll('[role="tab"]')) {
    const selected = tab.dataset.tab === state.evidenceTab;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  const result = state.envelope?.result ?? null;
  view.renderEvidence(el.evidenceBody, {
    result, groups: model.evidenceGroups(result), p07Request: state.p07CallRequest, p07RequestId: state.p07CallRequestArtifactId,
  }, state.evidenceTab);
}

function openDialog(dialog, trigger) {
  if (!dialog.open) {
    if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
  }
  trigger?.setAttribute('aria-expanded', 'true');
  dialog.addEventListener('close', () => trigger?.setAttribute('aria-expanded', 'false'), { once: true });
}

function openPanel(tab, anchor = null) {
  state.evidenceTab = tab;
  renderEvidence();
  openDialog(el.evidenceDialog, el.evidenceButton);
  el.evidenceBody.classList.remove('is-switching');
  void el.evidenceBody.offsetWidth;
  el.evidenceBody.classList.add('is-switching');
  const target = anchor ? document.getElementById(anchor) : null;
  if (!target) { el.evidenceBody.scrollTop = 0; return; }
  if (target.tagName === 'DETAILS') target.open = true;
  requestAnimationFrame(() => {
    target.scrollIntoView({ behavior: scrollBehavior(), block: 'start' });
    target.classList.remove('is-target');
    void target.offsetWidth;
    target.classList.add('is-target');
  });
}

// In-page links (sidebar stages, wordmark, skip link, MD) scroll without rewriting the hash, so the
// workspace/runs routing keeps working; panel buttons open the side panel at their anchor.
function onDocumentClick(event) {
  const panel = event.target.closest('[data-panel-tab]');
  if (panel) { openPanel(panel.dataset.panelTab, panel.dataset.panelAnchor || null); return; }
  const link = event.target.closest('a[href^="#"]');
  if (!link) return;
  const id = link.getAttribute('href').slice(1);
  if (id === 'workspace' || id === 'runs') return;
  const target = document.getElementById(id);
  if (!target || !el.workspace.contains(target)) return;
  event.preventDefault();
  jumpTo(target);
}

// --- Run lifecycle ---------------------------------------------------------

function stopPolling() {
  if (state.poller) clearTimeout(state.poller);
  state.poller = null;
}

function poll(requestId, { untilTerminal = false, deadline = Date.now() + api.RUN_TIMEOUT_MS } = {}) {
  stopPolling();
  const tick = async () => {
    if (state.requestId !== requestId) return;
    const response = await api.getRun(requestId);
    if (response.ok && response.body?.result) {
      const terminal = Boolean(response.body.result.final_image?.status) || ['error', 'no_results'].includes(response.body.result.status);
      renderEnvelope(response.body, { inFlight: !(untilTerminal && terminal) && state.inFlight });
      if (untilTerminal && terminal) { setRunning(false); renderEnvelope(response.body); await loadDecisions(requestId); return; }
    }
    if ((state.inFlight || untilTerminal) && Date.now() < deadline) state.poller = setTimeout(tick, POLL_MS);
    else if (untilTerminal) setRunning(false);
  };
  state.poller = setTimeout(tick, POLL_MS);
}

async function startRun(event) {
  event.preventDefault();
  showNotice('');
  const brief = model.validateBrief(el.brief.value);
  if (!brief.ok) { showNotice(brief.message); el.brief.focus(); return; }

  const requestId = model.newRequestId();
  state.requestId = requestId;
  state.envelope = null;
  state.md = { decisions: [], integratedSha: null, selected: null, busy: false, message: '' };
  setRunning(true);
  el.results.hidden = false;
  resetSections();
  renderSpine(null, { inFlight: true });
  setSectionState(el.md, 'pending');
  renderMd();

  // Image input was removed from the workspace: the request keeps the contract's user_input.images as [].
  const images = [];
  localStorage.setItem(LAST_RUN_KEY, requestId);
  poll(requestId);
  const response = await api.runWorkflow({ requestId, text: brief.text, images });
  if (state.requestId !== requestId) return;
  stopPolling();
  if (!response.ok) {
    showNotice(model.problemMessage(response.body, response.status));
    // Network drop or client timeout: the run may still finish server-side; keep polling the durable record.
    if (response.status === 0) { poll(requestId, { untilTerminal: true }); return; }
  }
  setRunning(false);
  if (response.body?.result) {
    renderEnvelope(response.body);
    await loadDecisions(requestId);
    el.concept.scrollIntoView({ behavior: scrollBehavior(), block: 'start' });
  } else if (state.envelope) {
    renderEnvelope(state.envelope);
  } else {
    el.results.hidden = true;
    renderSpine(null);
  }
}

async function openRun(requestId) {
  history.pushState(null, '', '#workspace');
  route();
  state.requestId = requestId;
  const response = await api.getRun(requestId);
  if (!response.ok || !response.body?.result) { showNotice(model.problemMessage(response.body, response.status)); return; }
  localStorage.setItem(LAST_RUN_KEY, requestId);
  renderEnvelope(response.body);
  await loadDecisions(requestId);
  jumpTo(el.concept);
}

// --- Views, dialogs, system status ------------------------------------------

async function showRuns() {
  const response = await api.listRuns(30);
  const runs = Array.isArray(response.body?.runs) ? response.body.runs : Array.isArray(response.body) ? response.body : [];
  view.renderRuns(el.runsList, runs, openRun);
}

function route() {
  const runs = location.hash === '#runs';
  el.workspace.hidden = runs;
  el.runsView.hidden = !runs;
  for (const link of document.querySelectorAll('[data-view]')) {
    const active = link.dataset.view === (runs ? 'runs' : 'workspace');
    link.classList.toggle('is-active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  if (runs) showRuns();
}

async function refreshSystem() {
  const response = await api.systemStatus();
  state.system = response.ok ? response.body : null;
  const services = Array.isArray(state.system?.agents) ? state.system.agents : [];
  const up = services.filter((svc) => svc.reachable && svc.healthy).length;
  const contract = state.system?.contract?.state;
  const tone = !state.system ? 'error' : up === services.length ? 'ok' : 'partial';
  el.systemDot.dataset.tone = tone;
  el.systemSummary.textContent = state.system
    ? `서비스 ${up}/${services.length} 응답${contract ? ` · 계약 ${contract}` : ''}`
    : '시스템 상태 확인 불가';
  view.renderSystem(el.systemBody, state.system);
}

function bind() {
  el.brief.addEventListener('input', updateCounter);
  el.form.addEventListener('submit', startRun);
  el.runsRefresh.addEventListener('click', showRuns);
  window.addEventListener('hashchange', route);
  document.addEventListener('click', onDocumentClick);
  el.systemButton.addEventListener('click', () => { refreshSystem(); openDialog(el.systemDialog, el.systemButton); });
  el.evidenceButton.addEventListener('click', () => openPanel(state.evidenceTab));
  for (const tab of el.evidenceDialog.querySelectorAll('[role="tab"]')) {
    tab.addEventListener('click', () => openPanel(tab.dataset.tab));
  }
  for (const button of document.querySelectorAll('[data-close]')) {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  }
}

async function restore() {
  const requestId = localStorage.getItem(LAST_RUN_KEY);
  if (!requestId) return;
  const response = await api.getRun(requestId);
  if (response.ok && response.body?.result) {
    state.requestId = requestId;
    renderEnvelope(response.body);
    await loadDecisions(requestId);
  }
}

bind();
updateCounter();
renderSpine(null);
route();
refreshSystem();
setInterval(refreshSystem, SYSTEM_REFRESH_MS);
restore();
