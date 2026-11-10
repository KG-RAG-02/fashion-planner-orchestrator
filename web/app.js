const form = document.querySelector('#run-form');
const queryInput = document.querySelector('#query');
const queryCount = document.querySelector('#query-count');
const runButton = document.querySelector('#run-button');
const notice = document.querySelector('#notice');
const results = document.querySelector('#results');
const runMode = document.querySelector('#run-mode');
const connectedConsent = document.querySelector('#connected-consent');
const allowConnected = document.querySelector('#allow-connected');
let currentMode = 'demo';

const stageLabels = {
  trend: ['01', '쿼리 · 트렌드 분석', 'trend-analysis-agent'],
  stock: ['02', '자사 상품 · 재고', 'stock-agent'],
  project_07: ['03', '판매 · 시장 조사', 'sales-market-agent'],
  project_06: ['04', '기획 초안', 'new-product-agent'],
};
const statusLabels = { ok: '완료', partial: '부분 완료', no_results: '자료 없음', error: '오류' };
const stageNotes = {
  trend: '현재 합성 모드에서는 trend 서비스에 연결하지 않고 결과 없음 fixture를 사용합니다.',
  stock: '현재 합성 모드에서는 재고 DB나 stock API를 조회하지 않습니다.',
  project_07: 'sales-market-agent 단계의 합성 결과입니다. 실제 판매·시장 자료를 조회하지 않습니다.',
  project_06: 'new-product-agent 단계의 합성 응답입니다. 실제 기획 생성기는 호출하지 않습니다.',
};
const connectedStageNotes = {
  trend: '주입한 trend adapter가 반환한 원본 TrendAnalysisResult입니다.',
  stock: 'stock-agent HTTP API 응답입니다. 상세 상품·트렌드 관계는 raw 결과를 확인하세요.',
  project_07: 'sales-market-agent의 MarketResearchAgent 결과입니다. 원본 producer ID는 market-research-agent이며 기본 source는 NoConfiguredSources입니다.',
  project_06: 'new-product-agent의 planning-draft endpoint 응답입니다. 최종 판단은 MD 검토로 남습니다.',
};

queryInput.addEventListener('input', () => {
  queryCount.textContent = `${queryInput.value.length.toLocaleString()} / 1,000`;
});

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderConnections(data) {
  const list = document.querySelector('#connector-list');
  const connectors = data?.connectors || [];
  list.replaceChildren(...connectors.map((item) => {
    const card = el('article', 'connector-card');
    const title = el('div', 'connector-title');
    title.append(el('span', '', item.name));
    const badge = el('span', 'connector-badge', item.configured ? 'CONFIGURED' : 'NOT CONFIGURED');
    badge.dataset.ready = String(Boolean(item.configured));
    title.append(badge);
    card.append(title);
    if (item.target) card.append(el('div', 'connector-target', item.target));
    card.append(el('div', 'connector-detail', item.detail || ''));
    return card;
  }));
  const ready = document.querySelector('#connections-ready');
  if (!data?.configuration_complete) {
    ready.textContent = '일부 설정 필요';
  } else if (data?.known_blockers?.length) {
    ready.textContent = `설정 입력 완료 · 미해결 호환 문제 ${data.known_blockers.length}건`;
  } else {
    ready.textContent = '설정 입력 완료 · 실연결 미검증';
  }
  const targets = connectors.filter((item) => item.configured && item.target).map((item) => `${item.name} (${item.target})`);
  const destinationText = targets.length ? targets.join(', ') : '설정된 서비스가 없습니다';
  document.querySelector('#connected-consent-copy').textContent =
    `요청과 앞 단계 결과를 ${destinationText}에 전송하는 것을 확인합니다. Trend query와 sales-market-agent 요청 scope는 아직 자동 구조화되지 않습니다. sales-market-agent에는 미지정 geography/channel/period를 null로 전달하며, stock-agent 설정에 따라 trend 결과가 해당 서비스의 LLM 제공자에게도 전달될 수 있습니다.`;
  const checkScope = [
    `연결 상태는 ${data?.endpoint_health || 'not_checked'} (endpoint health), ` +
      `${data?.contract_compatibility || 'not_verified'} (계약 호환), ` +
      `${data?.source_permission || 'not_verified'} (자료 권한), ` +
      `${data?.operational_approval || 'not_verified'} (운영 승인)입니다.`,
    data?.readiness_note || '',
  ].filter(Boolean).join(' ');
  document.querySelector('.connections-footnote').textContent = checkScope;
}

async function loadConnectionStatus() {
  try {
    const response = await fetch('/api/connections');
    renderConnections(await response.json());
  } catch {
    document.querySelector('#connections-ready').textContent = '상태 확인 실패';
  }
}

runMode.addEventListener('change', () => {
  const connected = runMode.value === 'connected';
  currentMode = runMode.value;
  connectedConsent.hidden = !connected;
  allowConnected.checked = false;
  const note = document.querySelector('.demo-note');
  if (connected) {
    note.innerHTML = '<strong>연결 모드</strong> 요청은 설정된 각 agent로 전달됩니다. 실행 전에 수신 대상을 확인해 주세요.';
  } else {
    note.innerHTML = '<strong>합성 데모</strong> 실제 트렌드·재고·판매 자료나 운영 서비스는 호출하지 않습니다.';
  }
});

function renderStage(stage) {
  const [number, label, producer] = stageLabels[stage.stage] || ['—', stage.stage, stage.producer_id || ''];
  const card = el('article', 'stage-card');
  const top = el('div', 'stage-top');
  top.append(el('span', 'stage-num', number));
  const title = el('div', 'stage-title');
  title.append(el('span', 'stage-name', label), el('span', 'stage-producer', producer));
  top.append(title);
  const state = el('span', 'stage-status', statusLabels[stage.status] || stage.status);
  state.dataset.status = stage.status;
  top.append(state);
  card.append(top);

  const detail = el('div', 'stage-detail');
  const notes = currentMode === 'connected' ? connectedStageNotes : stageNotes;
  const note = stage.error_message || notes[stage.stage] || '';
  if (note) detail.append(el('p', '', note));
  const metaParts = [];
  if (stage.producer_schema_version) metaParts.push(`producer schema: ${stage.producer_schema_version}`);
  else if (stage.stage === 'trend') metaParts.push('producer schema: 미정의');
  if (stage.error_code) metaParts.push(`오류 코드: ${stage.error_code}`);
  const raw = stage.raw_response;
  if (raw) {
    const evidence = Array.isArray(raw.evidence) ? raw.evidence.length : 0;
    const missing = Array.isArray(raw.missing_data) ? raw.missing_data.length : 0;
    const errors = Array.isArray(raw.errors) ? raw.errors.length : 0;
    const limitations = Array.isArray(raw.limitations) ? raw.limitations.length : 0;
    if (evidence || missing || errors || limitations) {
      detail.append(el('p', 'stage-counts', `Evidence ${evidence} · 미확보 ${missing} · 제한 ${limitations} · 오류 ${errors}`));
    }
  }
  if (metaParts.length) detail.append(el('p', '', metaParts.join(' · ')));
  if (stage.raw_response) {
    const disclosure = document.createElement('details');
    disclosure.append(el('summary', '', '단계 응답 보기'));
    const pre = el('pre');
    pre.textContent = JSON.stringify(stage.raw_response, null, 2);
    disclosure.append(pre);
    detail.append(disclosure);
  }
  card.append(detail);
  return card;
}

function renderExecution(data) {
  document.querySelector('#request-id').textContent = data.request_id;
  const overall = document.querySelector('#overall-status');
  overall.textContent = data.status === 'ok' ? 'PIPELINE COMPLETE' : data.status === 'error' ? 'PIPELINE STOPPED' : 'PIPELINE PARTIAL';
  overall.dataset.status = data.status;
  const list = document.querySelector('#stage-list');
  list.replaceChildren(...data.stages.map(renderStage));
  const finalStage = data.stages.find((item) => item.stage === 'project_06');
  const draftCard = document.querySelector('#draft-card');
  if (finalStage?.raw_response?.draft) {
    document.querySelector('#draft-title').textContent = finalStage.raw_response.draft.title || '기획 초안';
    document.querySelector('#draft-summary').textContent = finalStage.raw_response.draft.summary || '';
    draftCard.hidden = false;
  } else {
    draftCard.hidden = true;
  }
  results.hidden = false;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  notice.hidden = true;
  const query = queryInput.value.trim();
  if (!query) {
    notice.textContent = '요청 내용을 입력해 주세요.';
    notice.hidden = false;
    queryInput.focus();
    return;
  }
  if (runMode.value === 'connected' && !allowConnected.checked) {
    notice.textContent = '실제 연결 모드에서는 요청과 앞 단계 결과가 configured endpoint로 전달됩니다. 대상을 확인하고 동의란을 체크해 주세요.';
    notice.hidden = false;
    return;
  }
  const requestMode = runMode.value;
  runButton.disabled = true;
  runButton.querySelector('span:first-child').textContent = '실행 중…';
  try {
    const response = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, mode: requestMode }),
    });
    const data = await response.json();
    if (!response.ok) {
      if (data.connections) renderConnections(data.connections);
      throw new Error(data.message || data.error || '실행을 완료하지 못했습니다.');
    }
    currentMode = requestMode;
    renderExecution(data);
    results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    notice.textContent = error.message;
    notice.hidden = false;
  } finally {
    runButton.disabled = false;
    runButton.querySelector('span:first-child').textContent = '파이프라인 실행';
  }
});

loadConnectionStatus();
