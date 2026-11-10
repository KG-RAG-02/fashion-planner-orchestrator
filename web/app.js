const form = document.querySelector('#run-form');
const queryInput = document.querySelector('#query');
const queryCount = document.querySelector('#query-count');
const runButton = document.querySelector('#run-button');
const notice = document.querySelector('#notice');
const results = document.querySelector('#results');
const runMode = document.querySelector('#run-mode');
const connectedConsent = document.querySelector('#connected-consent');
const allowConnected = document.querySelector('#allow-connected');
const allowStructuring = document.querySelector('#allow-structuring');
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
  project_07: 'sales-market-agent의 MarketResearchAgent 결과입니다. 기본 source는 NoConfiguredSources입니다.',
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
  const httpMode = data?.connection_modes?.agent_http_v01;
  ready.textContent = httpMode?.ready
    ? 'HTTP v0.1 · 검사 통과'
    : `HTTP v0.1 · E2E 차단 ${httpMode?.routes?.filter((route) => route.known_status !== 'supported').length ?? 4}개 route`;
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
  const routeSummary = (httpMode?.routes || []).map((route) =>
    `${route.route_agent_id}: ${route.known_status}, health ${route.endpoint_health}, schema ${route.schema_compatibility}`,
  ).join(' · ');
  document.querySelector('.connections-footnote').textContent += ` 혼합 connector 설정 상태: ${data?.connection_modes?.mixed_connectors?.configured ? '설정됨' : '일부 미설정'}; HTTP v0.1 route: ${routeSummary}`;
}

async function loadStructuringStatus() {
  const copy = document.querySelector('#structuring-consent-copy');
  try {
    const response = await fetch('/api/query-structuring');
    const data = await response.json();
    const llm = data?.llm || {};
    const prompt = data?.prompt_version ? ` · 프롬프트 ${data.prompt_version}` : '';
    copy.textContent = llm.configured
      ? `00 질의 구조화: 입력한 질의를 외부 LLM(${llm.provider} · ${llm.model})에 보내 구조화하는 것을 확인합니다${prompt}.`
      : `00 질의 구조화: LLM 설정이 없습니다 (${llm.detail || '.env 확인'}). 체크하면 오류로 표시됩니다.`;
  } catch {
    copy.textContent = '00 질의 구조화: 상태 확인 실패';
  }
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

const structuringStatus = { ok: ['완료', 'ok'], error: ['오류', 'error'], skipped: ['건너뜀', 'no_results'] };

function categoryText(ref) {
  const value = ref.minor ? `${ref.major}/${ref.minor}` : (ref.major || '표준 값 없음');
  return `${value} ← "${ref.raw}"`;
}

function structuringSummary(tq) {
  const lines = [];
  const season = tq.trend_terms?.season;
  if (season) lines.push(['시즌', `${season.year} ${season.season} (${season.origin === 'default_next' ? '말하지 않아 기본값' : `사용자: "${season.raw}"`})`]);
  const names = (tq.trend_terms?.style_names || []).map((t) => t.raw);
  if (names.length) lines.push(['트렌드명', names.join(', ')]);
  const items = (tq.trend_terms?.item_terms || []).map((t) => t.raw);
  if (items.length) lines.push(['상품명', items.join(', ')]);
  const req = tq.requested || {};
  const include = (req.categories?.include || []).map(categoryText);
  const exclude = (req.categories?.exclude || []).map(categoryText);
  lines.push(['카테고리 포함', include.length ? include.join(', ') : '전체 (지정 없음)']);
  if (exclude.length) lines.push(['카테고리 제외', exclude.join(', ')]);
  if (req.gender) lines.push(['성별', `${req.gender.value || '표준 값 없음'} ← "${req.gender.raw}"`]);
  const attrs = (req.attributes || []).map((a) => `${a.type}: ${a.value || '표준 값 없음'} ← "${a.raw}"`);
  if (attrs.length) lines.push(['속성', attrs.join(', ')]);
  const unsupported = (tq.unsupported || []).map((u) => `${u.type}: "${u.raw}"`);
  if (unsupported.length) lines.push(['반영 못 한 조건', unsupported.join(', ')]);
  return lines;
}

function jsonDisclosure(title, value) {
  const disclosure = document.createElement('details');
  disclosure.append(el('summary', '', title));
  const pre = el('pre');
  pre.textContent = JSON.stringify(value, null, 2);
  disclosure.append(pre);
  return disclosure;
}

function renderStructuring(s) {
  if (!s) return null;
  const card = el('article', 'stage-card');
  const top = el('div', 'stage-top');
  top.append(el('span', 'stage-num', '00'));
  const title = el('div', 'stage-title');
  const model = s.llm ? ` · ${s.llm.provider} ${s.llm.model}` : '';
  title.append(el('span', 'stage-name', '질의 구조화'), el('span', 'stage-producer', `query-structuring${s.prompt_version ? ` · ${s.prompt_version}` : ''}${model}`));
  top.append(title);
  const [label, statusKey] = structuringStatus[s.status] || [s.status, s.status];
  const state = el('span', 'stage-status', label);
  state.dataset.status = statusKey;
  top.append(state);
  card.append(top);

  const detail = el('div', 'stage-detail');
  if (s.status !== 'ok') {
    if (s.error_message) detail.append(el('p', '', s.error_message));
    const meta = [];
    if (s.error_code) meta.push(`오류 코드: ${s.error_code}`);
    if (s.provider_error) meta.push(`LLM 오류 종류: ${s.provider_error}`);
    if (meta.length) detail.append(el('p', '', meta.join(' · ')));
  } else {
    for (const [name, text] of structuringSummary(s.trend_query)) {
      detail.append(el('p', '', `${name}: ${text}`));
    }
    if (s.warnings?.length) detail.append(el('p', 'stage-counts', `경고: ${s.warnings.join(', ')}`));
    detail.append(el('p', '', 'trend 단계 입력 예정 (아직 전송하지 않음).'));
    detail.append(jsonDisclosure('구조화 결과 보기 (TrendQuery)', s.trend_query));
    detail.append(jsonDisclosure('trend 단계 입력 미리보기', s.trend_input_preview));
    detail.append(jsonDisclosure('LLM 출력 (draft)', s.draft));
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
  list.replaceChildren(...[renderStructuring(data.query_structuring), ...data.stages.map(renderStage)].filter(Boolean));
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
      body: JSON.stringify({ query, mode: requestMode, structure: allowStructuring.checked }),
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
loadStructuringStatus();
