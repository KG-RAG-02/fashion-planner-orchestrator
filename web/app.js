const form = document.querySelector('#run-form');
const queryInput = document.querySelector('#query');
const queryCount = document.querySelector('#query-count');
const runButton = document.querySelector('#run-button');
const notice = document.querySelector('#notice');
const results = document.querySelector('#results');
const runMode = document.querySelector('#run-mode');
const localServicesStatus = document.querySelector('#local-services-status');
const restartLocalServicesButton = document.querySelector('#restart-local-services');
const historyToolbar = document.querySelector('#history-toolbar');
const historySelect = document.querySelector('#history-select');
const loadHistoryButton = document.querySelector('#load-history');
const deleteRunButton = document.querySelector('#delete-run');
const clearHistoryButton = document.querySelector('#clear-history');
const generateSavedImagesButton = document.querySelector('#generate-saved-images');
const savedImageCostNote = document.querySelector('#saved-image-cost-note');
const openPlanningDraftButton = document.querySelector('#open-planning-draft');
const planningDraftDialog = document.querySelector('#planning-draft-dialog');
const closePlanningDraftButton = document.querySelector('#close-planning-draft');
const imageViewerDialog = document.querySelector('#image-viewer-dialog');
const expandedImage = document.querySelector('#expanded-image');
const imageViewerCaption = document.querySelector('#image-viewer-caption');
const closeImageViewerButton = document.querySelector('#close-image-viewer');
let currentMode = runMode.value;
let currentExecution = null;

const stageLabels = {
  trend: ['01', '쿼리 · 트렌드 분석', 'trend-analysis-agent'],
  stock: ['02', '자사 상품 · 재고', 'stock-agent'],
  project_07: ['03', '판매 · 시장 조사', 'sales-market-agent'],
  project_06: ['04', '기획 초안', 'new-product-agent'],
};
const statusLabels = { ok: '완료', partial: '부분 완료', no_results: '조회 결과 없음', error: '호출 오류' };
const stageNotes = {
  trend: '현재 합성 모드에서는 trend 서비스에 연결하지 않고 결과 없음 fixture를 사용합니다.',
  stock: '현재 합성 모드에서는 재고 DB나 stock API를 조회하지 않습니다.',
  project_07: 'sales-market-agent 단계의 합성 결과입니다. 실제 판매·시장 자료를 조회하지 않습니다.',
  project_06: 'new-product-agent 단계의 합성 응답입니다. 실제 기획 생성기는 호출하지 않습니다.',
};
const connectedStageNotes = {
  trend: 'trend-analysis-agent의 HTTP v0.1 응답 envelope와 원본 TrendAnalysisResult입니다.',
  stock: 'stock-agent의 HTTP v0.1 응답입니다. Trend 1.1 원본 식별 정보와 adapter provenance를 확인하세요.',
  project_07: 'sales-market-agent의 HTTP v0.1 응답입니다. sibling artifacts는 downstream에 전달하지 않습니다.',
  project_06: 'new-product-agent의 HTTP v0.1 초안 응답입니다. 최종 판단은 MD 검토로 남습니다.',
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
  const summary = document.querySelector('#connection-summary');
  const connectors = data?.connectors || [];
  const connectorCards = connectors.map((item) => {
    const card = el('article', 'connector-card');
    const title = el('div', 'connector-title');
    title.append(el('span', '', item.name));
    const badge = el('span', 'connector-badge', item.configured ? 'CONFIGURED' : 'NOT CONFIGURED');
    badge.dataset.ready = String(Boolean(item.configured));
    title.append(badge);
    card.append(title);
    if (item.target) card.append(el('div', 'connector-target', item.target));
    else if (item.local_development_base_url) {
      card.append(el('div', 'connector-target', `로컬 기본 주소(미설정): ${item.local_development_base_url}`));
    }
    card.append(el('div', 'connector-detail', item.detail || ''));
    return card;
  });
  const ready = document.querySelector('#connections-ready');
  const httpMode = data?.connection_modes?.agent_http_v01;
  const routes = httpMode?.routes || [];
  const routeCards = routes.map((route) => {
    const card = el('article', 'connector-card');
    const title = el('div', 'connector-title');
    title.append(el('span', '', `${route.route_agent_id} → ${route.producer_id}`));
    const badge = el('span', 'connector-badge', route.configured ? 'URL CONFIGURED' : 'URL NOT SET');
    badge.dataset.ready = String(Boolean(route.configured));
    title.append(badge);
    card.append(title);
    const target = route.target || route.local_development_base_url;
    if (target) card.append(el('div', 'connector-target', route.target || `local default only: ${target}`));
    card.append(el('div', 'connector-detail',
      `${route.known_status}; implemented ${route.implemented}; health ${route.endpoint_health}; schema ${route.schema_compatibility}; result ${route.producer_result_schema_version || 'unknown'}; ${route.authentication}`));
    if (route.expected_input) card.append(el('div', 'connector-detail', `input: ${route.expected_input}`));
    return card;
  });
  list.replaceChildren(...connectorCards, ...routeCards);
  const configuredCount = routes.filter((route) => route.configured).length;
  ready.textContent = routes.length ? `주소 ${configuredCount}/${routes.length} 설정` : '연결 정보 없음';
  ready.dataset.ready = String(routes.length > 0 && configuredCount === routes.length);
  const agentLabels = {
    'trend-analysis-agent': '트렌드 분석',
    'stock-agent': '상품·재고',
    'sales-market-agent': '시장 조사',
    'new-product-agent': '기획 초안',
  };
  summary.replaceChildren(...routes.map((route) => {
    const item = el('div', 'connection-summary-item');
    item.append(el('span', '', agentLabels[route.route_agent_id] || route.route_agent_id));
    const state = el('span', 'connection-summary-state', route.configured ? '주소 설정됨' : '주소 미설정');
    state.dataset.ready = String(Boolean(route.configured));
    item.append(state);
    return item;
  }));
  const checkScope = [
    `연결 상태는 ${data?.endpoint_health || 'not_checked'} (endpoint health), ` +
      `${data?.contract_compatibility || 'not_verified'} (계약 호환), ` +
      `${data?.source_permission || 'not_verified'} (자료 권한), ` +
      `${data?.operational_approval || 'not_verified'} (운영 승인)입니다.`,
    data?.readiness_note || '',
  ].filter(Boolean).join(' ');
  document.querySelector('#connection-technical-note').textContent = checkScope;
  const routeSummary = routes.map((route) =>
    `${route.route_agent_id}: ${route.known_status}, health ${route.endpoint_health}, schema ${route.schema_compatibility}`,
  ).join(' · ');
  document.querySelector('#connection-technical-note').textContent += ` 기존 연결 모드: ${data?.connection_modes?.mixed_connectors?.configured ? '설정됨' : '일부 미설정'}; API 경로: ${routeSummary}`;
}

async function loadConnectionStatus() {
  try {
    const response = await fetch('/api/connections');
    renderConnections(await response.json());
  } catch {
    document.querySelector('#connections-ready').textContent = '상태 확인 실패';
  }
}

async function refreshLocalServicesStatus() {
  const response = await fetch('/api/local-services/status', { cache: 'no-store' });
  if (!response.ok) throw new Error('서버 상태를 확인하지 못했습니다.');
  const data = await response.json();
  localServicesStatus.textContent = `서버 ${data.healthy}/${data.total} 정상`;
  localServicesStatus.dataset.ready = String(Boolean(data.all_healthy));
  localServicesStatus.title = (data.services || [])
    .map((service) => `${service.name}: ${service.status === 'running' ? '실행 중' : '확인 필요'}`)
    .join('\n');
  return data;
}

restartLocalServicesButton.addEventListener('click', async () => {
  restartLocalServicesButton.disabled = true;
  restartLocalServicesButton.textContent = '재시작 중…';
  localServicesStatus.textContent = '서버를 재시작하고 있습니다';
  localServicesStatus.dataset.ready = 'false';
  notice.hidden = true;
  try {
    try {
      const response = await fetch('/api/local-services/restart', { method: 'POST' });
      if (!response.ok) throw new Error('재시작 요청을 전달하지 못했습니다.');
    } catch {
      // The orchestrator may restart before the browser receives its 202 response.
    }
    for (let attempt = 0; attempt < 180; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      try {
        const data = await refreshLocalServicesStatus();
        if (data.all_healthy) {
          localServicesStatus.textContent = '모든 서버 정상';
          setTimeout(() => window.location.reload(), 600);
          return;
        }
      } catch {
        localServicesStatus.textContent = '서버 재시작 중…';
      }
    }
    throw new Error('일부 서버가 응답하지 않습니다. .local/services의 로그를 확인해 주세요.');
  } catch (error) {
    notice.textContent = error.message;
    notice.hidden = false;
    try { await refreshLocalServicesStatus(); } catch {}
  } finally {
    restartLocalServicesButton.disabled = false;
    restartLocalServicesButton.textContent = '서버 재시작';
  }
});

runMode.addEventListener('change', () => {
  const connected = runMode.value === 'connected';
  const httpE2e = runMode.value === 'agent_http_v01';
  currentMode = runMode.value;
  const note = document.querySelector('.demo-note');
  if (connected) {
    note.innerHTML = '<strong>기존 연결로 분석</strong> 질의를 외부 LLM으로 구조화하고 각 저장소의 기존 연결 방식을 사용합니다. 네 에이전트 API를 모두 호출하는 방식과는 다를 수 있습니다.';
  } else if (httpE2e) {
    note.innerHTML = '<strong>에이전트 API로 분석</strong> 질의를 외부 LLM으로 구조화한 뒤 네 에이전트 API를 차례로 호출하고 이미지·기획서 보드도 생성합니다. 이미지 제공자에 초안 입력이 전달되며 API 비용이 발생할 수 있습니다. 서버가 샘플 모드라면 실제 자료원은 조회하지 않습니다.';
  } else {
    note.innerHTML = '<strong>미리보기 · 샘플 결과</strong> 질의는 외부 LLM으로 구조화하지만, 실제 트렌드·재고·판매 자료나 운영 서비스는 호출하지 않습니다.';
  }
});
runMode.dispatchEvent(new Event('change'));

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
  const notes = currentMode === 'connected' || currentMode === 'agent_http_v01'
    ? connectedStageNotes : stageNotes;
  const note = stage.error_message || notes[stage.stage] || '';
  if (note) detail.append(el('p', '', note));
  const metaParts = [];
  if (stage.producer_schema_version) metaParts.push(`producer schema: ${stage.producer_schema_version}`);
  else if (stage.stage === 'trend') metaParts.push('producer schema: 미정의');
  if (stage.error_code) metaParts.push(`오류 코드: ${stage.error_code}`);
  const raw = stage.raw_response;
  if (raw) {
    const body = raw.result && typeof raw.result === 'object' ? raw.result : raw;
    if (typeof body.summary === 'string' && body.summary.trim()) {
      detail.append(el('p', 'stage-summary', body.summary));
    }
    if (Array.isArray(body.trends)) {
      for (const trend of body.trends.slice(0, 3)) {
        const style = trend?.style || {};
        const styleLabel = [style.name, Array.isArray(style.seasons) ? style.seasons.join(', ') : '']
          .filter(Boolean).join(' · ');
        if (styleLabel) detail.append(el('p', 'stage-summary', `트렌드 · ${styleLabel}`));
        if (typeof style.definition === 'string' && style.definition.trim()) {
          detail.append(el('p', 'stage-finding', style.definition));
        }
        for (const item of (Array.isArray(trend?.items) ? trend.items.slice(0, 3) : [])) {
          const category = [item.category_major, item.category_minor].filter(Boolean).join(' / ');
          const colors = Array.isArray(item.colors) && item.colors.length ? ` · 색상 ${item.colors.join(', ')}` : '';
          detail.append(el('p', 'stage-finding', `트렌드 아이템 · ${category ? `${category} · ` : ''}${item.name || '이름 미상'}${colors}`));
          for (const candidate of (Array.isArray(item.similar_products) ? item.similar_products.slice(0, 3) : [])) {
            const candidateCategory = [candidate.category_major, candidate.category_minor].filter(Boolean).join(' / ');
            const candidateColors = Array.isArray(candidate.colors) && candidate.colors.length
              ? ` · 색상 ${candidate.colors.join(', ')}` : '';
            const brand = candidate.brand ? ` · ${candidate.brand}` : '';
            detail.append(el('p', 'stage-limitation', `유사 카탈로그 후보 · ${candidate.name || '이름 미상'}${candidateCategory ? ` · ${candidateCategory}` : ''}${candidateColors}${brand}`));
          }
        }
      }
    }
    if (Array.isArray(body.findings)) {
      for (const finding of body.findings.slice(0, 3)) {
        const text = typeof finding === 'string' ? finding
          : finding?.summary || finding?.statement || finding?.description || finding?.text;
        const value = typeof text === 'string' ? text : text?.value;
        if (typeof value === 'string' && value.trim()) detail.append(el('p', 'stage-finding', value));
      }
    }
    const evidence = Array.isArray(body.evidence) ? body.evidence.length : 0;
    const missing = Array.isArray(body.missing_data) ? body.missing_data.length : 0;
    const errors = Array.isArray(body.errors) ? body.errors.length : 0;
    const limitations = Array.isArray(body.limitations) ? body.limitations.length : 0;
    if (evidence || missing || errors || limitations) {
      detail.append(el('p', 'stage-counts', `Evidence ${evidence} · 미확보 ${missing} · 제한 ${limitations} · 오류 ${errors}`));
    }
    if (Array.isArray(body.limitations)) {
      for (const limitation of body.limitations.slice(0, 3)) {
        if (typeof limitation === 'string' && limitation.trim()) {
          detail.append(el('p', 'stage-limitation', `제한 · ${limitation}`));
        }
      }
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

function conditionEntries(data) {
  const tq = data?.query_structuring?.trend_query;
  if (!tq) return [['요청', data?.query || '조건 미확인', 'user']];
  const entries = [];
  const season = tq.trend_terms?.season;
  if (season) {
    const value = `${season.year || ''} ${season.season || ''}`.trim();
    entries.push(['시즌', value || season.raw || '미지정', season.origin || 'unknown']);
  }
  const gender = tq.requested?.gender;
  if (gender) entries.push(['타깃', gender.raw || gender.value || '미지정', 'user']);
  const categories = tq.requested?.categories?.include || [];
  if (categories.length) {
    entries.push(['카테고리', categories.map((item) => {
      const hierarchy = [item.major, item.minor].filter(Boolean).join(' · ');
      return hierarchy && item.raw && item.raw !== item.minor ? `${hierarchy} (${item.raw})` : hierarchy || item.raw;
    }).filter(Boolean).join(', '), 'user']);
  }
  const styles = (tq.trend_terms?.style_names || []).map((item) => item.raw || item.value).filter(Boolean);
  if (styles.length) entries.push(['무드', styles.join(', '), 'user']);
  const categoryTerms = new Set(categories.map((item) => item.raw).filter(Boolean));
  const items = (tq.trend_terms?.item_terms || []).map((item) => item.raw || item.value)
    .filter((item) => item && !categoryTerms.has(item));
  if (items.length) entries.push(['상품 키워드', items.join(', '), 'user']);
  const attributes = (tq.requested?.attributes || []).map((item) => item.raw || item.value).filter(Boolean);
  if (attributes.length) entries.push(['속성', attributes.join(', '), 'user']);
  return entries.length ? entries : [['요청', data?.query || '조건 미지정', 'user']];
}

function renderPlanConditions(data) {
  const container = document.querySelector('#plan-condition-chips');
  const entries = conditionEntries(data);
  container.replaceChildren(...entries.map(([label, value, origin]) => {
    const chip = el('span', 'condition-chip');
    chip.append(el('span', 'condition-label', label), el('strong', '', value || '미지정'));
    if (origin === 'default_next') chip.append(el('small', 'condition-origin', '기본 시즌'));
    else if (origin === 'unknown') chip.append(el('small', 'condition-origin', '출처 미확인'));
    return chip;
  }));
}

function stageBody(stage) {
  const raw = stage?.raw_response;
  return raw?.result && typeof raw.result === 'object' ? raw.result : raw;
}

function stageDisplayStatus(stage, previousStages, key) {
  if (!stage) {
    const stopped = previousStages.some((item) => item.status === 'error');
    return [stopped ? '조회 건너뜀' : '자료 없음', stopped ? 'skipped' : 'no_results'];
  }
  const body = stageBody(stage) || {};
  const limitations = Array.isArray(body.limitations) ? body.limitations.join(' ').toLowerCase() : '';
  if (stage.status === 'error') return ['호출 오류', 'error'];
  if (key === 'stock' && previousStages.some((item) => item.stage === 'trend' && ['no_results', 'error'].includes(item.status))) {
    return ['조회 건너뜀', 'skipped'];
  }
  if (key === 'project_07' && (limitations.includes('no authorized market research source')
    || limitations.includes('검색 자료원 또는 허용 목록이 설정되지 않았습니다'))) {
    return ['자료원 미연결', 'unconfigured'];
  }
  if (stage.status === 'no_results' && !stage.raw_response && key === 'stock') return ['조회 건너뜀', 'skipped'];
  if (stage.status === 'no_results') return ['조회 결과 없음', 'no_results'];
  if (stage.status === 'partial') return ['일부 자료 확인', 'partial'];
  return ['자료 확인됨', 'ok'];
}

function renderEvidenceCards(data) {
  const container = document.querySelector('#evidence-cards');
  const stages = Array.isArray(data?.stages) ? data.stages : [];
  const stageMap = new Map(stages.map((stage) => [stage.stage, stage]));
  const cards = [
    ['trend', '트렌드 방향', '01'],
    ['stock', '상품 · 재고', '02'],
    ['project_07', '시장 조사', '03'],
  ].map(([key, title, number]) => {
    const stage = stageMap.get(key);
    const [label, state] = stageDisplayStatus(stage, stages, key);
    const body = stageBody(stage) || {};
    const card = el('article', 'evidence-card');
    const top = el('div', 'evidence-card-top');
    top.append(el('span', 'evidence-number', number));
    const status = el('span', 'evidence-status', label);
    status.dataset.status = state;
    top.append(status);
    card.append(top, el('h4', '', title));
    const evidence = Array.isArray(body.evidence) ? body.evidence : [];
    const limitations = Array.isArray(body.limitations) ? body.limitations : [];
    const sourceNames = [...new Set(evidence.map((item) => item.source_name || item.source_id).filter(Boolean))];
    if (sourceNames.length) card.append(el('p', 'evidence-source', sourceNames.slice(0, 2).join(' · ')));
    const sample = evidence.some((item) => item.data_nature === 'sample' || item.source_type === 'synthetic_inventory');
    if (sample) card.append(el('span', 'data-nature-tag', '샘플 데이터'));
    const season = body.applied_filters?.season;
    if (season?.year && season?.season) {
      card.append(el('p', 'evidence-source', `기준 시즌 · ${season.year} ${season.season}${season.origin === 'default_next' ? ' · 기본값' : ''}`));
    } else if (body.season_basis?.season) {
      card.append(el('p', 'evidence-source', `기준 시즌 · ${body.season_basis.season}${body.season_basis.source === 'inferred' ? ' · 추정' : ''}`));
    }
    if (label === '조회 건너뜀') {
      card.append(el('p', 'evidence-explanation', '앞 단계 결과가 없어 이 자료 조회는 진행되지 않았습니다.'));
    } else if (label === '자료원 미연결') {
      card.append(el('p', 'evidence-explanation', '사용할 수 있는 시장 조사 자료원이 설정되지 않았습니다.'));
    } else if (label === '조회 결과 없음') {
      const reason = limitations.find((item) => typeof item === 'string');
      card.append(el('p', 'evidence-explanation', reason || '요청 조건에 맞는 자료를 찾지 못했습니다.'));
    } else if (label === '호출 오류') {
      card.append(el('p', 'evidence-explanation', stage.error_message || '자료를 불러오는 중 오류가 발생했습니다.'));
    } else {
      const count = evidence.length;
      card.append(el('p', 'evidence-explanation', count ? `근거 ${count}건을 응답에서 확인했습니다.` : '응답에 별도 근거 목록이 포함되지 않았습니다.'));
    }
    const limitation = limitations.find((item) => typeof item === 'string');
    if (limitation && label !== '조회 결과 없음' && label !== '자료원 미연결') {
      card.append(el('p', 'evidence-limitation', limitation));
    }
    return card;
  });
  container.replaceChildren(...cards);
}

function focusRequestEditor() {
  if (currentExecution?.query) queryInput.value = currentExecution.query;
  queryCount.textContent = `${queryInput.value.length.toLocaleString()} / 1,000`;
  queryInput.focus();
  queryInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function claimKindLabel(kind) {
  if (kind === 'evidence_observation' || kind === 'evidence_fact') return ['데이터 근거', 'evidence'];
  if (kind === 'to_validate') return ['검토 필요', 'review'];
  if (kind === 'interpretation' || kind === 'proposal') return ['기획 제안', 'proposal'];
  return ['초안 제안', 'proposal'];
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
    if (s.trend_query) {
      for (const [name, text] of structuringSummary(s.trend_query)) {
        detail.append(el('p', '', `${name}: ${text}`));
      }
    } else {
      detail.append(el('p', '', 'trend로 넘길 품목이 없습니다 (표준 카테고리 밖 품목만 있음).'));
    }
    if (s.warnings?.length) detail.append(el('p', 'stage-counts', `경고: ${s.warnings.join(', ')}`));
    detail.append(el('p', '', s.trend_input_preview?.sent ? '구조화된 TrendQuery를 trend 단계 입력으로 전송했습니다.' : '구조화 결과는 미리보기이며 trend 단계로 전송하지 않았습니다.'));
    detail.append(jsonDisclosure('구조화 결과 보기 (TrendQuery)', s.trend_query));
    detail.append(jsonDisclosure('trend 단계 입력 미리보기', s.trend_input_preview));
    detail.append(jsonDisclosure('LLM 출력 (draft)', s.draft));
  }
  card.append(detail);
  return card;
}

function renderExecution(data) {
  if (planningDraftDialog.open) planningDraftDialog.close();
  if (imageViewerDialog.open) imageViewerDialog.close();
  currentExecution = data;
  document.querySelector('#request-id').textContent = data.request_id ||
    (data.historical_preview ? '직전 실행 · 응답 상세 미저장' : '');
  const queryLine = document.querySelector('#run-query');
  queryLine.hidden = !data.query;
  queryLine.textContent = data.query ? `질의 · ${data.query}` : '';
  const historyNote = document.querySelector('#history-note');
  historyNote.hidden = !data.historical_preview && !data.restored_from_cache && !data.restored_from_history && !data.save_warning;
  historyNote.textContent = data.save_warning || (data.historical_preview
    ? '이전 실행의 단계 상태와 이미지는 복원했습니다. 당시 전체 agent 응답과 초안 텍스트는 저장하지 않아 이 요약에 포함되지 않습니다.'
    : data.restored_from_history
      ? `저장된 실행 기록입니다${data.saved_at ? ` · ${data.saved_at}` : ''}.`
      : data.restored_from_cache
      ? '이 컴퓨터에 저장된 최근 실행 결과입니다. 전체 기록 삭제로 이 로컬 기록을 지울 수 있습니다.'
      : '');
  const overall = document.querySelector('#overall-status');
  overall.textContent = data.status === 'ok' ? '검토 준비 완료' : data.status === 'error' ? '실행 중단' : '일부 자료로 작성';
  overall.dataset.status = data.status;
  renderPlanConditions(data);
  renderEvidenceCards(data);
  document.querySelector('.execution-details').open = false;
  const list = document.querySelector('#stage-list');
  list.replaceChildren(...[renderStructuring(data.query_structuring), ...data.stages.map(renderStage)].filter(Boolean));
  const finalStage = data.stages.find((item) => item.stage === 'project_06');
  const draftCard = document.querySelector('#draft-card');
  const finalBody = finalStage?.raw_response?.result || finalStage?.raw_response;
  const imageData = data.image_generation;
  const imageButtonHidden = !data.history_id;
  generateSavedImagesButton.hidden = imageButtonHidden;
  savedImageCostNote.hidden = imageButtonHidden;
  const hasGeneratedImage = imageData?.status === 'generated' || imageData?.image?.status === 'generated'
    || imageData?.planning_board?.status === 'generated';
  generateSavedImagesButton.textContent = hasGeneratedImage ? '이미지 다시 생성' : '이미지 생성';
  const productUrl = imageData?.image_display_url;
  const boardUrl = imageData?.planning_board_display_url;
  if (finalBody?.draft) {
    const draft = finalBody.draft;
    openPlanningDraftButton.hidden = false;
    document.querySelector('#draft-title').textContent = draft.title || '기획 초안';
    const summary = document.querySelector('#draft-summary');
    summary.textContent = draft.summary || '';
    summary.hidden = !draft.summary;
    const detail = document.querySelector('#draft-details');
    const rows = [];
    const seenClaims = new Set();
    const addDraftRow = (label, value, kind) => {
      const key = value.trim().toLocaleLowerCase();
      if (key && !key.startsWith('자료 없음') && seenClaims.has(key)) return;
      if (key && !key.startsWith('자료 없음')) seenClaims.add(key);
      rows.push([label, value, kind]);
    };
    const claimValue = (value) => typeof value === 'string' ? value
      : value && typeof value === 'object' ? (value.value || '') : '';
    for (const [key, label] of [['opportunity', '기회 가설'], ['target_customer', '대상 고객'],
      ['product_definition', '상품 정의']]) {
      const claim = draft[key];
      const value = claimValue(claim);
      addDraftRow(label, value || '자료 없음 · MD 검토 필요', value ? claim?.kind : 'to_validate');
    }
    for (const [key, label] of [['attributes', '제안 속성'], ['differentiation', '차별화'],
      ['risks', '리스크'], ['questions_for_validation', '검토 질문'], ['limitations', '제한사항']]) {
      const values = Array.isArray(draft[key]) ? draft[key] : [];
      for (const claim of values) {
        const value = claimValue(claim);
        if (value) addDraftRow(label, value, claim?.kind || (key === 'risks' || key === 'questions_for_validation' || key === 'limitations' ? 'to_validate' : undefined));
      }
    }
    const attributeValues = (draft.attributes || []).map(claimValue).filter(Boolean);
    if (!attributeValues.length) addDraftRow('디자인 속성 · 실루엣/컬러/소재/디테일', '자료 없음 · MD 검토 필요', 'to_validate');
    const styling = claimValue(draft.styling_direction || draft.styling);
    addDraftRow('스타일링 방향', styling || '자료 없음 · MD 검토 필요', styling ? draft.styling_direction?.kind : 'to_validate');
    for (const [key, label] of [['price', '판매가'], ['cost', '원가'], ['quantity', '기획 수량']]) {
      const value = claimValue(draft[key]);
      addDraftRow(label, value || '자료 없음 · MD 검토 필요', value ? draft[key]?.kind : 'to_validate');
    }
    detail.replaceChildren(...rows.map(([label, value, kind]) => {
      const row = el('p', 'draft-detail-row');
      const [kindLabel, kindClass] = claimKindLabel(kind);
      row.append(el('strong', '', `${label} · `), document.createTextNode(value), el('span', `claim-kind ${kindClass}`, kindLabel));
      return row;
    }));
    draftCard.hidden = false;
  } else if (!productUrl && !boardUrl && !finalStage) {
    openPlanningDraftButton.hidden = true;
    draftCard.hidden = true;
  }
  else {
    openPlanningDraftButton.hidden = true;
    document.querySelector('#draft-title').textContent = productUrl || boardUrl ? '기획 이미지 미리보기' : '시각 시안 미생성';
    document.querySelector('#draft-summary').textContent = imageData?.error_message
      || (productUrl || boardUrl ? '' : '기획 초안 또는 이미지가 준비되지 않았습니다. 연결 상세에서 Project_06 상태를 확인해 주세요.');
    document.querySelector('#draft-summary').hidden = !document.querySelector('#draft-summary').textContent;
    document.querySelector('#draft-details').replaceChildren();
    draftCard.hidden = false;
  }
  const imageNote = document.querySelector('#image-generation-note');
  const imageArea = document.querySelector('#draft-images');
  const productFigure = document.querySelector('#product-image-figure');
  const boardFigure = document.querySelector('#planning-board-figure');
  const imagePlaceholder = document.querySelector('#draft-images-placeholder');
  productFigure.hidden = !productUrl;
  boardFigure.hidden = !boardUrl;
  imagePlaceholder.hidden = Boolean(productUrl || boardUrl);
  if (!productUrl && !boardUrl) {
    imagePlaceholder.textContent = imageData?.status === 'failed'
      ? '이미지 생성에 실패했습니다. 오류와 입력 근거는 연결 상세에서 확인해 주세요.'
      : '생성된 이미지가 아직 없습니다. 시각 시안은 이미지 생성 완료 후 표시됩니다.';
  }
  imageArea.hidden = draftCard.hidden;
  imageArea.dataset.count = String(Number(Boolean(productUrl)) + Number(Boolean(boardUrl)));
  if (productUrl) document.querySelector('#product-image').src = productUrl;
  if (boardUrl) document.querySelector('#planning-board-image').src = boardUrl;
  document.querySelector('#product-image').onerror = () => {
    imageNote.textContent = '상품 시안은 생성됐지만 이미지를 불러오지 못했습니다.';
  };
  document.querySelector('#planning-board-image').onerror = () => {
    imageNote.textContent = '기획 보드는 생성됐지만 이미지를 불러오지 못했습니다.';
  };
  imageNote.hidden = !imageData;
  const fallbackConflicts = imageData?.synthetic_fallback?.blocked_actual_attempt?.conflicts || [];
  const conflictLabels = fallbackConflicts.map((conflict) => {
    const vals = conflict.values || {};
    const inventory = Array.isArray(vals.inventory) ? vals.inventory.join(', ') : String(vals.inventory ?? '');
    return `${conflict.field}: 요청 ${vals.request ?? '미상'} / 재고 ${inventory || '미상'}`;
  });
  imageNote.textContent = !imageData ? '' : imageData.status === 'generated'
    ? `이미지는 실제 상품·재고 근거가 아닌 시각 제안입니다${imageData.visual_request_scope?.season_source === 'trend_result' ? ` · 기준 시즌 ${imageData.visual_request_scope.season}` : ''}.`
    : imageData.status === 'generated_synthetic_fallback'
      ? `샘플 입력으로 보완한 시안입니다. 실제 재고 근거가 아닙니다${conflictLabels.length ? ` · 확인할 차이: ${conflictLabels.join('; ')}` : ''}.`
      : `이미지 생성 ${imageData.status || '상태 미확인'}${imageData.error_code ? ` · ${imageData.error_code}` : ''}${conflictLabels.length ? ` · 확인할 차이: ${conflictLabels.join('; ')}` : ''}.`;
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
  const requestMode = runMode.value;
  runButton.disabled = true;
  runButton.querySelector('span:first-child').textContent = '실행 중…';
  try {
    const response = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, mode: requestMode, structure: true,
        generate_images: requestMode === 'agent_http_v01' }),
    });
    const data = await response.json();
    if (!response.ok) {
      if (data.connections) renderConnections(data.connections);
      throw new Error(data.message || data.error || '실행을 완료하지 못했습니다.');
    }
    currentMode = requestMode;
    await refreshRunHistory(data.history_id || '');
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

loadHistoryButton.addEventListener('click', async () => {
  const historyId = historySelect.value;
  if (!historyId) return;
  try {
    const response = await fetch(`/api/run-history/${encodeURIComponent(historyId)}`);
    const record = await response.json();
    if (!response.ok || !record.result) throw new Error('저장된 실행 기록을 불러오지 못했습니다.');
    runMode.value = record.mode || 'demo';
    runMode.dispatchEvent(new Event('change'));
    currentMode = record.mode || 'demo';
    record.result.restored_from_history = true;
    record.result.saved_at = record.saved_at;
    renderExecution(record.result);
    results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    notice.textContent = error.message;
    notice.hidden = false;
  }
});

historySelect.addEventListener('change', () => {
  const selected = Boolean(historySelect.value);
  loadHistoryButton.disabled = !selected;
  deleteRunButton.disabled = !selected;
});

openPlanningDraftButton.addEventListener('click', () => {
  if (typeof planningDraftDialog.showModal === 'function') planningDraftDialog.showModal();
  else planningDraftDialog.setAttribute('open', '');
});

document.querySelector('#edit-plan-request').addEventListener('click', focusRequestEditor);

closePlanningDraftButton.addEventListener('click', () => {
  if (typeof planningDraftDialog.close === 'function') planningDraftDialog.close();
  else planningDraftDialog.removeAttribute('open');
});

planningDraftDialog.addEventListener('click', (event) => {
  if (event.target === planningDraftDialog && typeof planningDraftDialog.close === 'function') {
    planningDraftDialog.close();
  }
});

function showExpandedImage(image) {
  const source = image.currentSrc || image.src;
  if (!source) return;
  expandedImage.src = source;
  expandedImage.alt = image.alt;
  imageViewerCaption.textContent = image.closest('figure')?.querySelector('figcaption')?.textContent || image.alt;
  imageViewerDialog.showModal();
}

for (const image of [document.querySelector('#product-image'), document.querySelector('#planning-board-image')]) {
  image.addEventListener('click', () => showExpandedImage(image));
  image.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      showExpandedImage(image);
    }
  });
}

closeImageViewerButton.addEventListener('click', () => imageViewerDialog.close());
imageViewerDialog.addEventListener('click', (event) => {
  if (event.target === imageViewerDialog) imageViewerDialog.close();
});
imageViewerDialog.addEventListener('close', () => {
  expandedImage.removeAttribute('src');
  imageViewerCaption.textContent = '';
});

deleteRunButton.addEventListener('click', async () => {
  const historyId = historySelect.value;
  if (!historyId) return;
  try {
    const response = await fetch(`/api/run-history/${encodeURIComponent(historyId)}`, { method: 'DELETE' });
    if (!response.ok) throw new Error('선택한 실행 기록을 삭제하지 못했습니다.');
    if (currentExecution?.history_id === historyId) {
      currentExecution = null;
      results.hidden = true;
    }
    await refreshRunHistory();
    notice.textContent = '선택한 실행 기록을 삭제했습니다.';
    notice.hidden = false;
  } catch (error) {
    notice.textContent = error.message;
    notice.hidden = false;
  }
});

clearHistoryButton.addEventListener('click', async () => {
  try {
    const response = await fetch('/api/run-history', { method: 'DELETE' });
    if (!response.ok) throw new Error('저장 기록을 삭제하지 못했습니다.');
    await refreshRunHistory();
    results.hidden = true;
    notice.textContent = '이 컴퓨터에 저장된 실행 기록을 모두 삭제했습니다.';
    notice.hidden = false;
  } catch (error) {
    notice.textContent = error.message;
    notice.hidden = false;
  }
});

generateSavedImagesButton.addEventListener('click', async () => {
  const historyId = currentExecution?.history_id;
  if (!historyId) return;
  generateSavedImagesButton.disabled = true;
  generateSavedImagesButton.textContent = '이미지 생성 중…';
  notice.hidden = true;
  try {
    const response = await fetch(`/api/run-history/${encodeURIComponent(historyId)}/images`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok || !data.image_generation) {
      throw new Error(data.error || '이미지를 생성하지 못했습니다.');
    }
    currentExecution.image_generation = data.image_generation;
    renderExecution(currentExecution);
    await refreshRunHistory(historyId);
    results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    notice.textContent = error.message;
    notice.hidden = false;
  } finally {
    generateSavedImagesButton.disabled = false;
    const updatedImages = currentExecution?.image_generation;
    const hasGeneratedImage = updatedImages?.status === 'generated' || updatedImages?.image?.status === 'generated'
      || updatedImages?.planning_board?.status === 'generated';
    generateSavedImagesButton.textContent = hasGeneratedImage ? '이미지 다시 생성' : '이미지 생성';
  }
});

async function refreshRunHistory(selectedId = '') {
  try {
    const response = await fetch('/api/run-history');
    const data = await response.json();
    const runs = Array.isArray(data.runs) ? data.runs : [];
    historySelect.replaceChildren(new Option('저장 기록 선택', ''));
    for (const run of runs) {
      const when = (run.saved_at || '').replace('T', ' ').replace('+00:00', ' UTC');
      const query = run.query || '질의 미보관';
      const suffix = run.historical_preview ? ' · 요약만 있음' : '';
      historySelect.add(new Option(`${when} · ${query} · ${run.status || 'unknown'}${suffix}`, run.id));
    }
    historyToolbar.hidden = runs.length === 0;
    if (selectedId && runs.some((run) => run.id === selectedId)) historySelect.value = selectedId;
    const selected = Boolean(historySelect.value);
    loadHistoryButton.disabled = !selected;
    deleteRunButton.disabled = !selected;
  } catch {
    historyToolbar.hidden = true;
  }
}

async function restoreLatestRun() {
  try {
    const response = await fetch('/api/latest-run');
    const cached = await response.json();
    await refreshRunHistory(cached.id || cached.result?.history_id || '');
    if (!response.ok || !cached.available || !cached.result) return;
    runMode.value = cached.mode || 'demo';
    runMode.dispatchEvent(new Event('change'));
    currentMode = cached.mode || 'demo';
    cached.result.restored_from_cache = true;
    cached.result.saved_at = cached.saved_at;
    renderExecution(cached.result);
  } catch {
    // The page stays usable if the optional local result cache is unavailable.
  }
}

loadConnectionStatus();
refreshLocalServicesStatus().catch(() => {
  localServicesStatus.textContent = '서버 상태 확인 실패';
  localServicesStatus.dataset.ready = 'false';
});
restoreLatestRun();
