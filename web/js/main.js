// VOGUE Atelier — MD workspace controller.
import * as api from './api.js';
import * as model from './model.js';
import * as view from './render.js';

const $ = (selector) => document.querySelector(selector);
const LAST_RUN_KEY = 'vogue-atelier:last-request-id';
const POLL_MS = 1500;
const SYSTEM_REFRESH_MS = 30000;

const el = {
  form: $('#brief-form'), brief: $('#brief'), counter: $('#brief-counter'), run: $('#run-button'), notice: $('#notice'),
  file: $('#ref-image'), preview: $('#ref-preview'), fileName: $('#ref-name'), clearFile: $('#ref-clear'),
  pipeline: $('#pipeline'), rail: $('#rail'), results: $('#results'),
  concept: $('#concept'), trends: $('#trend-block'), stock: $('#stock-block'), draft: $('#draft-block'), review: $('#review-block'),
  md: $('#md-card'), runCard: $('#run-card'),
  evidenceButton: $('#evidence-button'), evidenceDialog: $('#evidence-dialog'), evidenceBody: $('#evidence-body'),
  systemButton: $('#system-button'), systemDot: $('#system-dot'), systemSummary: $('#system-summary'), systemDialog: $('#system-dialog'), systemBody: $('#system-body'),
  workspace: $('#workspace'), runsView: $('#runs'), runsList: $('#runs-list'), runsRefresh: $('#runs-refresh'),
};

const state = {
  envelope: null, requestId: null, inFlight: false, poller: null, previewUrl: null,
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
  el.run.textContent = running ? '실행 중…' : '기획 시작';
  el.brief.readOnly = running;
  el.file.disabled = running;
}

// --- Composer --------------------------------------------------------------

function updateCounter() {
  el.counter.textContent = `${el.brief.value.length.toLocaleString('ko-KR')} / ${model.MAX_BRIEF.toLocaleString('ko-KR')}`;
}

function clearImage() {
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
  el.file.value = '';
  el.preview.hidden = true;
  el.preview.removeAttribute('src');
  el.fileName.textContent = 'PNG · JPEG · WebP, 최대 10MB 1장';
  el.clearFile.hidden = true;
}

function onImageChosen() {
  const file = el.file.files[0];
  if (!file) { clearImage(); return; }
  const check = model.validateImage(file);
  if (!check.ok) { clearImage(); showNotice(check.message); return; }
  showNotice('');
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = URL.createObjectURL(file);
  el.preview.src = state.previewUrl;
  el.preview.alt = `선택한 참고 이미지: ${file.name}`;
  el.preview.hidden = false;
  el.fileName.textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)}MB`;
  el.clearFile.hidden = false;
}

// --- Rendering -------------------------------------------------------------

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
  el.pipeline.hidden = false;
  view.renderRail(el.rail, model.rail(result, { inFlight }));
  el.results.hidden = false;
  view.renderConcept(el.concept, envelope, { retryEnabled: Boolean(state.system?.image?.retry_enabled), onRetry: retryImage });
  view.renderTrends(el.trends, model.trendCards(model.sourceResult(result, 'trend')));
  view.renderStock(el.stock, model.stockSlots(model.sourceResult(result, 'stock')));
  view.renderDraft(el.draft, model.draftView(model.sourceResult(result, 'planning_draft')));
  renderReview();
  void loadP07CallRequest(result);
  const summary = model.runSummary(envelope);
  view.renderRun(el.runCard, summary, { sheet: model.planningSheetLink(summary, { inFlight }) });
  renderMd();
}

function renderReview() {
  const source = state.envelope?.result?.sources?.sales_market_review;
  view.renderReview(el.review, model.reviewView(
    source?.raw_response?.result ?? null,
    state.p07CallRequest,
    source?.call_request_artifact?.artifact_id ?? null,
    state.p07CallRequestState,
    model.sourceResult(state.envelope?.result, 'planning_draft'),
  ));
}

async function loadP07CallRequest(result) {
  const artifactId = result?.sources?.sales_market_review?.call_request_artifact?.artifact_id;
  if (!artifactId || artifactId !== state.p07CallRequestArtifactId || state.p07CallRequest || state.p07CallRequestState !== 'loading') return;
  if (!/^[a-f0-9]{64}$/.test(artifactId)) {
    state.p07CallRequestState = 'error';
    renderReview();
    return;
  }
  const requestId = result?.request_id;
  const response = await api.getArtifact(artifactId);
  if (state.requestId !== requestId || state.p07CallRequestArtifactId !== artifactId) return;
  state.p07CallRequest = response.ok && response.body && typeof response.body === 'object' && !Array.isArray(response.body) ? response.body : null;
  state.p07CallRequestState = state.p07CallRequest ? 'loaded' : 'error';
  renderReview();
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
      if (untilTerminal && terminal) { setRunning(false); await loadDecisions(requestId); return; }
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
  const file = el.file.files[0] ?? null;
  const fileCheck = model.validateImage(file);
  if (!fileCheck.ok) { showNotice(fileCheck.message); return; }

  const requestId = model.newRequestId();
  state.requestId = requestId;
  state.envelope = null;
  state.md = { decisions: [], integratedSha: null, selected: null, busy: false, message: '' };
  setRunning(true);
  el.pipeline.hidden = false;
  el.results.hidden = true;
  view.renderRail(el.rail, model.rail(null, { inFlight: true }));

  const images = [];
  if (file) {
    const upload = await api.uploadImage(file, requestId);
    if (!upload.ok) { setRunning(false); showNotice(model.problemMessage(upload.body, upload.status)); return; }
    images.push(upload.body);
  }
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
  if (response.body?.result) { renderEnvelope(response.body); await loadDecisions(requestId); }
  el.results.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
}

async function openRun(requestId) {
  location.hash = '#workspace';
  state.requestId = requestId;
  const response = await api.getRun(requestId);
  if (!response.ok || !response.body?.result) { showNotice(model.problemMessage(response.body, response.status)); return; }
  localStorage.setItem(LAST_RUN_KEY, requestId);
  renderEnvelope(response.body);
  await loadDecisions(requestId);
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

function openDialog(dialog, trigger) {
  if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
  trigger?.setAttribute('aria-expanded', 'true');
  dialog.addEventListener('close', () => trigger?.setAttribute('aria-expanded', 'false'), { once: true });
}

function renderEvidence() {
  for (const tab of el.evidenceDialog.querySelectorAll('[role="tab"]')) {
    const selected = tab.dataset.tab === state.evidenceTab;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  view.renderEvidence(el.evidenceBody, state.envelope, state.evidenceTab);
}

function bind() {
  el.brief.addEventListener('input', updateCounter);
  el.file.addEventListener('change', onImageChosen);
  el.clearFile.addEventListener('click', clearImage);
  el.form.addEventListener('submit', startRun);
  el.runsRefresh.addEventListener('click', showRuns);
  window.addEventListener('hashchange', route);
  el.systemButton.addEventListener('click', () => { refreshSystem(); openDialog(el.systemDialog, el.systemButton); });
  el.evidenceButton.addEventListener('click', () => { renderEvidence(); openDialog(el.evidenceDialog, el.evidenceButton); });
  for (const tab of el.evidenceDialog.querySelectorAll('[role="tab"]')) {
    tab.addEventListener('click', () => { state.evidenceTab = tab.dataset.tab; renderEvidence(); });
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
route();
refreshSystem();
setInterval(refreshSystem, SYSTEM_REFRESH_MS);
restore();
