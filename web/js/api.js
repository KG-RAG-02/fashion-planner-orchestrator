// Same-origin client for the orchestrator. Every call returns {ok, status, body};
// callers turn problem bodies into messages with model.problemMessage().

const JSON_HEADERS = { 'Content-Type': 'application/json', Accept: 'application/json' };
export const RUN_TIMEOUT_MS = 600_000;

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { credentials: 'same-origin', ...options });
  } catch (error) {
    if (error?.name === 'AbortError') return { ok: false, status: 0, body: { error: { code: 'CLIENT_TIMEOUT', message: '응답을 기다리는 시간이 지났습니다. 실행 이력에서 결과를 확인해 주세요.' } } };
    return { ok: false, status: 0, body: { error: { code: 'NETWORK', message: '오케스트레이터에 연결할 수 없습니다.' } } };
  }
  let body = null;
  const type = response.headers.get('Content-Type') || '';
  if (type.includes('application/json')) {
    try { body = await response.json(); } catch { body = null; }
  }
  return { ok: response.ok, status: response.status, body };
}

export function systemStatus() {
  return request('/api/system/status', { headers: { Accept: 'application/json' } });
}

export function uploadImage(file, requestId) {
  return request('/v1/input-images', {
    method: 'POST',
    headers: { 'Content-Type': file.type, 'X-Request-ID': requestId, Accept: 'application/json' },
    body: file,
  });
}

export function runWorkflow({ requestId, text, images }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), RUN_TIMEOUT_MS);
  const payload = { contract_version: 'vogue-workflow/0.1', request_id: requestId, input: { user_input: { text, images } } };
  return request('/api/workflow/run', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload), signal: controller.signal })
    .finally(() => clearTimeout(timer));
}

export function getRun(requestId) {
  return request(`/api/workflow/runs/${encodeURIComponent(requestId)}`, { headers: { Accept: 'application/json' } });
}

export function listRuns(limit = 20) {
  return request(`/api/workflow/runs?limit=${encodeURIComponent(limit)}`, { headers: { Accept: 'application/json' } });
}

export function getDecisions(requestId) {
  return request(`/api/workflow/runs/${encodeURIComponent(requestId)}/md-decision`, { headers: { Accept: 'application/json' } });
}

export function saveDecision(requestId, { integratedSha, decision, note }) {
  const payload = { contract_version: 'vogue-workflow/0.1', request_id: requestId, integrated_response_sha256: integratedSha, decision };
  if (note) payload.note = note;
  return request(`/api/workflow/runs/${encodeURIComponent(requestId)}/md-decision`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) });
}

export function retryImage(requestId, retryKey) {
  return request('/v1/orchestrations/image-retries', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ request_id: requestId, retry_key: retryKey }) });
}

export function artifactUrl(artifactId) {
  return `/api/workflow/artifacts/${encodeURIComponent(artifactId)}`;
}
