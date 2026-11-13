"""Bounded workflow HTTP transport and thin UI/public API boundary."""
from __future__ import annotations

import hashlib
import http.client
import os
from pathlib import Path
import re
import socket
import ssl
from urllib.parse import parse_qs, urlsplit, unquote

from .connectors import (ConnectorConfigurationError, WORKFLOW_CONNECT_TIMEOUT_SECONDS,
                         WORKFLOW_READ_TIMEOUT_SECONDS, _service_url, timeout_seconds)
from .workflow import PEER_NAMESPACES, Workflow, WireResponse, StageFailure
from .workflow_contract import (WorkflowContract, VERSION, DRAFT_MODES, require, contract_gate_status,
                                development_limitation, truthy)
from .workflow_store import (ArtifactError, MD_DECISIONS, PeerArtifactReader, RunStateError, SHA256_HEX,
                             WorkflowStore, canonical, p08_artifact, strict_json)

ROOT = Path(__file__).resolve().parents[2]


class WorkflowUnavailable(ConnectorConfigurationError):
    """Configuration or gate failure with a stable problem code; the message never holds secrets."""

    def __init__(self, code, http_status=409, message=None):
        self.code, self.http_status = code, http_status
        super().__init__(message or code)


def _repo_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def store_root(env=None):
    """VOGUE_WORKFLOW_STORE_DIR (default <repo>/.local/workflow; container /app/.local/workflow)."""
    value = setting(env, 'VOGUE_WORKFLOW_STORE_DIR')
    return _repo_path(value) if value else ROOT / '.local/workflow'


def store():
    return WorkflowStore(store_root())


def peer_artifact_reader(env=None):
    """PeerArtifactReader rooted at VOGUE_PEER_ARTIFACT_ROOT (p07 reviews, p06 visuals), or None when unset."""
    value = setting(env, 'VOGUE_PEER_ARTIFACT_ROOT')
    return PeerArtifactReader(_repo_path(value), namespaces=PEER_NAMESPACES) if value else None


def ensure_live_contract(contract=None, env=None):
    """Return the run limitations the contract gate requires, or raise 409 CONTRACT_NOT_FROZEN.

    Reads only contracts/workflow (or VOGUE_WORKFLOW_CONTRACT_BASELINE); see conventions section 9.
    """
    status = contract_gate_status(env, getattr(contract, 'root', None))
    if status['gate'] == 'open':
        return []
    if status['gate'] == 'development':
        return [development_limitation(status)]
    raise WorkflowUnavailable('CONTRACT_NOT_FROZEN', 409, 'The workflow contract baseline is not frozen.')


def post_wire(url, payload, token=None, *, connect_timeout=WORKFLOW_CONNECT_TIMEOUT_SECONDS,
              read_timeout=WORKFLOW_READ_TIMEOUT_SECONDS):
    """One POST, no retries. The URL was validated by _service_url (HTTPS with CA verification,
    allowlisted private host with a bearer, or loopback in development mode)."""
    parts = urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
    kwargs = {'context': ssl.create_default_context()} if parts.scheme == 'https' else {}
    conn = cls(parts.hostname, parts.port, timeout=connect_timeout, **kwargs)
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        conn.connect()
        if conn.sock:
            conn.sock.settimeout(read_timeout)
        conn.request('POST', parts.path, body=canonical(payload), headers=headers)
        response = conn.getresponse()
        raw = response.read(16777217)
        return WireResponse(raw, response.status, response.getheader('Content-Type','application/json').split(';')[0], len(raw) > 16777216)
    except (socket.timeout, TimeoutError):  # before OSError: TimeoutError is an OSError
        raise StageFailure('UPSTREAM_TIMEOUT', 504) from None
    except (OSError, http.client.HTTPException):
        raise StageFailure('UPSTREAM_UNAVAILABLE', 503) from None
    finally:
        conn.close()


# Stage 06 providers (workflow-contract 0.2.0, 2026-11-13 decision): P08 calls no image model. The product and worn
# (outfit) images are P06's Draft visuals; stage 06 composes the planning sheet (기획안 사진) from the run.
IMAGE_PROVIDERS = ('planning_sheet', 'fixture')
FIXTURE_IMAGE_LIMITATION = ('Final image comes from the deterministic local fixture provider '
                            '(VOGUE_WORKFLOW_IMAGE_PROVIDER=fixture): a placeholder, not a generated concept.')
PLANNING_SHEET_LIMITATION = ('The final image (stage 06) is the planning sheet composed by P08 from this run '
                             '(VOGUE_WORKFLOW_IMAGE_PROVIDER=planning_sheet); P08 calls no image model. '
                             'The product and outfit images are P06 Draft visuals.')
# P06 makes two images inside the Draft call when draft_with_visuals is requested; its reads may take minutes.
DRAFT_WITH_VISUALS_READ_TIMEOUT_SECONDS = 600.0


def setting(env, name):
    """An optional setting; compose passes unset optional values as empty strings, so blank means unset."""
    return str((os.environ if env is None else env).get(name) or '').strip()


def images_enabled(env=None):
    return (os.environ if env is None else env).get('VOGUE_WORKFLOW_IMAGES_ENABLED') == '1'


def image_provider_name(env=None):
    """VOGUE_WORKFLOW_IMAGE_PROVIDER: planning_sheet (default) or fixture; anything else (openai included) is None."""
    value = setting(env, 'VOGUE_WORKFLOW_IMAGE_PROVIDER').lower() or 'planning_sheet'
    return value if value in IMAGE_PROVIDERS else None


def draft_mode(env=None):
    """VOGUE_WORKFLOW_DRAFT_MODE: draft_with_visuals (default) or draft_only; anything else is None (invalid)."""
    value = setting(env, 'VOGUE_WORKFLOW_DRAFT_MODE').lower() or 'draft_with_visuals'
    return value if value in DRAFT_MODES else None


def image_retry_enabled(env=None):
    return truthy((os.environ if env is None else env).get('VOGUE_WORKFLOW_IMAGE_RETRY_ENABLED'))


def fixture_image(payload):
    """Deterministic local PNG for offline runs: no network, no model, colours derived from the lineage.

    The state machine counts provider_calls exactly as for the real provider.
    """
    require(images_enabled(), 'IMAGE_PROVIDER_DISABLED')
    import io
    from PIL import Image, ImageDraw
    lineage = hashlib.sha256(canonical(payload['lineage'])).digest()
    image = Image.new('RGB', (512, 512), tuple(lineage[0:3]))
    draw = ImageDraw.Draw(image)
    for band in range(8):
        colour = tuple(lineage[3 + band * 3:6 + band * 3])
        draw.rectangle((32, 96 + band * 44, 480, 128 + band * 44), fill=colour)
    draw.rectangle((8, 8, 503, 503), outline=(255, 255, 255), width=4)
    draw.text((32, 32), 'VOGUE FIXTURE IMAGE (synthetic)', fill=(255, 255, 255))
    draw.text((32, 56), 'lineage ' + lineage.hex()[:16], fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue(), 'image/png'


def planning_sheet_image(payload):
    """Stage 06 provider: the planning sheet PNG of this run (planning_sheet.py), deterministic and local.

    ``payload['envelope']`` is the run as it stands when stage 06 starts; ``payload['load_artifact']`` returns
    verified bytes for the P06 visuals already imported into the store. No network, no model.
    """
    require(images_enabled(), 'IMAGE_PROVIDER_DISABLED')
    from .planning_sheet import planning_sheet_png
    return planning_sheet_png(payload['envelope'], load_artifact=payload.get('load_artifact')), 'image/png'


_FROM_ENV = object()


def build_workflow(*, artifact_reader=_FROM_ENV):
    contract = WorkflowContract()
    limitations = ensure_live_contract(contract)
    env = os.environ
    if artifact_reader is _FROM_ENV:
        artifact_reader = peer_artifact_reader(env)
    dev = env.get('VOGUE_AGENT_HTTP_DEV_MODE', '').lower() in {'1','true','yes'}
    token = setting(env, 'VOGUE_AGENT_SERVICE_BEARER_TOKEN') or None
    if not dev and not token:
        raise ConnectorConfigurationError('Service Bearer token is required.')
    specs = [('trend','VOGUE_TREND_AGENT_URL','trend-analysis-agent/runs'),
             ('stock','VOGUE_STOCK_AGENT_URL','stock-agent/runs'),
             ('draft','VOGUE_NEW_PRODUCT_AGENT_URL','new-product-agent/drafts'),
             ('review','VOGUE_SALES_MARKET_AGENT_URL','sales-market-agent/reviews')]
    connect_timeout = timeout_seconds(env, 'VOGUE_WORKFLOW_CONNECT_TIMEOUT_SECONDS', WORKFLOW_CONNECT_TIMEOUT_SECONDS)
    read_timeout = timeout_seconds(env, 'VOGUE_WORKFLOW_READ_TIMEOUT_SECONDS', WORKFLOW_READ_TIMEOUT_SECONDS)
    mode = draft_mode(env)
    if mode is None:
        raise WorkflowUnavailable('DRAFT_MODE_INVALID', 409, 'VOGUE_WORKFLOW_DRAFT_MODE must be draft_with_visuals or draft_only.')
    # P06 generates its two images inside the Draft call, so that one read may take minutes.
    draft_timeout = timeout_seconds(env, 'VOGUE_WORKFLOW_DRAFT_READ_TIMEOUT_SECONDS',
                                    max(read_timeout, DRAFT_WITH_VISUALS_READ_TIMEOUT_SECONDS) if mode == 'draft_with_visuals' else read_timeout)
    adapters = {}
    for key, variable, route in specs:
        if not env.get(variable):
            raise ConnectorConfigurationError(variable + ' is required.')
        url = _service_url(env, variable, '/v1/agents/' + route)
        adapters[key] = lambda payload, u=url, t=(draft_timeout if key == 'draft' else read_timeout): post_wire(
            u, payload, token, connect_timeout=connect_timeout, read_timeout=t)
    image = None
    if images_enabled(env):
        provider = image_provider_name(env)
        if provider is None:
            raise WorkflowUnavailable('IMAGE_PROVIDER_INVALID', 409,
                                      'VOGUE_WORKFLOW_IMAGE_PROVIDER must be planning_sheet or fixture (P08 calls no image model).')
        if provider == 'fixture':
            image = fixture_image
            limitations = [*limitations, FIXTURE_IMAGE_LIMITATION]
        else:
            image = planning_sheet_image
            limitations = [*limitations, PLANNING_SHEET_LIMITATION]
    return Workflow(**adapters, image=image, store=store(), contract=contract, artifact_reader=artifact_reader,
                    limitations=limitations, draft_mode=mode)


def problem(handler, status, code, message=None, *, request_id=None, stage='input', headers=None):
    """Problem body (common.schema.json#/$defs/problem); messages never carry secrets or upstream bodies."""
    body = {'contract_version': VERSION, 'request_id': request_id, 'status': 'error',
            'error': {'code': code, 'message': message or code, 'stage': stage, 'fatal': True,
                      'retryable': status in {503, 504}}}
    if headers:
        handler._json(status, body, headers=headers)
    else:
        handler._json(status, body)


def _configuration_problem(handler, exc, request_id=None):
    problem(handler, getattr(exc, 'http_status', 409), getattr(exc, 'code', 'CONTRACT_OR_CONNECTORS_NOT_READY'),
            request_id=request_id)


ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')  # common.schema.json#/$defs/id


def dispatch(handler, value):
    from .server import structure_query_for_ui
    rid = None
    try:
        require(isinstance(value, dict), 'INPUT_OBJECT_REQUIRED')
        if value.get('contract_version') != VERSION:
            # Rejected before any model, DB or network work (conventions section 1).
            problem(handler, 422, 'UNSUPPORTED_CONTRACT_VERSION')
            return
        rid = value.get('request_id')
        require(isinstance(rid, str) and 0 < len(rid) <= 128, 'REQUEST_ID_REQUIRED')
        incoming = value.get('input', {})
        require(isinstance(incoming, dict), 'INPUT_OBJECT_REQUIRED')
        user = incoming.get('user_input')
        require(isinstance(user, dict) and isinstance(user.get('text'), str), 'USER_INPUT_REQUIRED')
        # Check the live gate before invoking even the query LLM.
        workflow = build_workflow()
        trend = incoming.get('trend_query')
        if trend is None:
            structured = structure_query_for_ui(user['text'], rid, enabled=True)
            require(structured.get('status') == 'ok' and structured.get('trend_query') is not None, 'TREND_QUERY_REQUIRED')
            trend = structured['trend_query']
        planning = incoming.get('planning_request')
        if planning is None:
            planning = dict(schema_version='1.0', request_id=rid, query=user['text'])
            context = {}
            season = trend.get('trend_terms', {}).get('season', {})
            if season.get('origin') == 'user_requested' and isinstance(season.get('year'),int) and season.get('season') in {'SS','FW'}:
                context['season'] = str(season['year']) + season['season']
            categories = trend.get('requested', {}).get('categories', {}).get('include', [])
            if len(categories) == 1 and isinstance(categories[0],dict):
                category = categories[0].get('minor') or categories[0].get('major')
                if category: context['category'] = category
            if context: planning['context'] = context
        response = workflow.run(request_id=rid, user_input=user, planning_request=planning, trend_query=trend)
        handler._json(response['http_status'], response['envelope'])
    except ConnectorConfigurationError as exc:
        _configuration_problem(handler, exc, _known_id(rid))
    except (ValueError, KeyError, TypeError):
        problem(handler, 422, 'WORKFLOW_INPUT_INVALID', request_id=_known_id(rid))
    except ImportError:
        problem(handler, 503, 'WORKFLOW_DEPENDENCIES_MISSING', request_id=_known_id(rid))


def _known_id(value):
    return value if isinstance(value, str) and ID_PATTERN.fullmatch(value) else None


# Explicit route table: (method -> handler name). Nothing else under the workflow namespace runs.
EXACT_ROUTES = {
    '/api/workflow/run': {'POST': 'start_run'},
    '/api/workflow/runs': {'GET': 'list_runs'},
    '/api/system/status': {'GET': 'system_status'},
    '/v1/input-images': {'POST': 'upload_image'},
    '/v1/orchestrations/image-retries': {'POST': 'retry_image'},
}
PATTERN_ROUTES = (
    (('runs', None), {'GET': 'get_run'}),
    (('runs', None, 'md-decision'), {'GET': 'get_md_decisions', 'POST': 'post_md_decision'}),
    (('runs', None, 'planning-sheet.png'), {'GET': 'get_planning_sheet'}),
    (('artifacts', None), {'GET': 'get_artifact'}),
)
NAMESPACE = '/api/workflow'


def match(path):
    """Return (methods, params) for a documented route, ({}, {}) for an unknown path inside the
    workflow namespace, or None when the path does not belong to the workflow boundary."""
    if path in EXACT_ROUTES:
        return EXACT_ROUTES[path], {}
    if path != NAMESPACE and not path.startswith(NAMESPACE + '/'):
        return None
    segments = path[len(NAMESPACE) + 1:].split('/')
    for pattern, methods in PATTERN_ROUTES:
        if len(segments) == len(pattern) and all(segment for segment in segments) and all(
                expected is None or expected == segment for expected, segment in zip(pattern, segments)):
            values = [unquote(segment) for expected, segment in zip(pattern, segments) if expected is None]
            return methods, {'value': values[0]} if len(values) == 1 else {'values': values}
    return {}, {}


def owns(path):
    """Paths served by the workflow boundary (problem bodies, trusted-local only)."""
    return match(path) is not None


def routes(handler, path):
    """Serve the workflow boundary. Return False for paths it does not own.

    Unknown paths under /api/workflow/ answer 404 and undocumented methods 405 (problem bodies);
    no path other than POST /api/workflow/run (and the versioned POST /v1/orchestrations) starts a run.
    """
    from .server import _request_is_trusted_local
    route = match(path)
    if route is None:
        return False
    methods, params = route
    if not methods:
        problem(handler, 404, 'ROUTE_NOT_FOUND')
        return True
    if not _request_is_trusted_local(handler):
        problem(handler, 403, 'LOCAL_WORKFLOW_ONLY')
        return True
    if handler.command not in methods:
        problem(handler, 405, 'METHOD_NOT_ALLOWED', headers={'Allow': ', '.join(sorted(methods))})
        return True
    try:
        ROUTE_HANDLERS[methods[handler.command]](handler, **params)
    except ArtifactError as exc:
        problem(handler, exc.http_status, exc.code)
    except ConnectorConfigurationError as exc:
        _configuration_problem(handler, exc)
    except (ValueError, KeyError, TypeError):
        problem(handler, 422, 'WORKFLOW_INPUT_INVALID')
    except ImportError:
        problem(handler, 503, 'WORKFLOW_DEPENDENCIES_MISSING')
    return True


def _content_length(handler, limit):
    """Validated Content-Length, or None after answering 400/413 (checked before the media type)."""
    try:
        size = int(handler.headers.get('Content-Length', '0'))
    except ValueError:
        size = -1
    if size > limit:
        problem(handler, 413, 'BODY_TOO_LARGE')
        return None
    if size <= 0:
        problem(handler, 400, 'BODY_REQUIRED')
        return None
    return size


def _read_exact(handler, size):
    data = handler.rfile.read(size)
    if len(data) != size:
        problem(handler, 400, 'BODY_TRUNCATED')
        return None
    return data


_INVALID = object()


def _read_json(handler, limit):
    """Strict JSON body (application/json), or _INVALID after answering 400/413/415."""
    size = _content_length(handler, limit)
    if size is None:
        return _INVALID
    if handler.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
        problem(handler, 415, 'JSON_MIME_REQUIRED')
        return _INVALID
    data = _read_exact(handler, size)
    if data is None:
        return _INVALID
    try:
        return strict_json(data)
    except (ValueError, RecursionError):
        problem(handler, 400, 'MALFORMED_JSON')
        return _INVALID


def _start_run(handler):
    value = _read_json(handler, 8388608)
    if value is not _INVALID:
        dispatch(handler, value)


def _upload_image(handler):
    size = _content_length(handler, 10485760)
    if size is None:
        return
    mime = handler.headers.get('Content-Type', '').split(';')[0].strip().lower()
    if mime not in {'image/png', 'image/jpeg', 'image/webp'}:
        problem(handler, 415, 'IMAGE_MIME_UNSUPPORTED')
        return
    data = _read_exact(handler, size)
    if data is None:
        return
    rid = handler.headers.get('X-Request-ID', '')
    require(0 < len(rid) <= 128, 'REQUEST_ID_REQUIRED')
    handler._json(201, store().upload(data, mime, rid))


def _retry_image(handler):
    if not image_retry_enabled():
        problem(handler, 409, 'IMAGE_RETRY_DISABLED', 'Image retries are disabled (VOGUE_WORKFLOW_IMAGE_RETRY_ENABLED).')
        return
    value = _read_json(handler, 8388608)
    if value is _INVALID:
        return
    require(isinstance(value, dict), 'INPUT_OBJECT_REQUIRED')
    if 'contract_version' in value and value['contract_version'] != VERSION:
        problem(handler, 422, 'UNSUPPORTED_CONTRACT_VERSION')
        return
    workflow = build_workflow()
    result = workflow.retry_image(value['request_id'], value['retry_key'])
    handler._json(result['http_status'], result['envelope'])


def _get_run(handler, value):
    rid = value
    if not ID_PATTERN.fullmatch(rid):
        problem(handler, 404, 'RUN_NOT_FOUND')
        return
    private = store()
    result = private.run(rid, 'current') or private.run(rid) or private.run(rid, 'progress')
    if not result:
        problem(handler, 404, 'RUN_NOT_FOUND', request_id=rid)
        return
    handler._json(200, result['envelope'])


LIMIT_PATTERN = re.compile(r'[0-9]{1,6}')
RUNS_DEFAULT_LIMIT, RUNS_MAX_LIMIT = 20, 100


def _list_runs(handler):
    """GET /api/workflow/runs?limit=N (default 20, at most 100), newest first."""
    values = parse_qs(urlsplit(handler.path).query, keep_blank_values=True).get('limit')
    limit = RUNS_DEFAULT_LIMIT
    if values is not None:
        if len(values) != 1 or not LIMIT_PATTERN.fullmatch(values[0]) or int(values[0]) < 1:
            problem(handler, 422, 'LIMIT_INVALID', 'limit must be an integer from 1 to 100.')
            return
        limit = min(int(values[0]), RUNS_MAX_LIMIT)
    handler._json(200, {'runs': store().list_runs(limit), 'limit': limit})


MD_DECISION_FIELDS = {'contract_version', 'request_id', 'integrated_response_sha256', 'decision', 'note'}
MD_NOTE_MAX_CHARS = 2000
MD_DECISION_MAX_BYTES = 65536


def _md_decision_error(body, rid):
    """422 code for an invalid MD decision body, or None (strict: unknown keys are rejected)."""
    if not isinstance(body, dict):
        return 'MD_DECISION_INVALID'
    if body.get('contract_version') != VERSION:
        return 'UNSUPPORTED_CONTRACT_VERSION'
    if set(body) - MD_DECISION_FIELDS:
        return 'MD_DECISION_UNKNOWN_FIELD'
    if body.get('request_id') != rid:
        return 'REQUEST_ID_MISMATCH'
    sha = body.get('integrated_response_sha256')
    if not isinstance(sha, str) or not SHA256_HEX.fullmatch(sha):
        return 'INTEGRATED_RESPONSE_SHA256_INVALID'
    if not isinstance(body.get('decision'), str) or body['decision'] not in MD_DECISIONS:
        return 'MD_DECISION_INVALID'
    note = body.get('note')
    if note is not None and (not isinstance(note, str) or len(note) > MD_NOTE_MAX_CHARS):
        return 'MD_NOTE_INVALID'
    return None


def _post_md_decision(handler, value):
    """Append an MD decision (adopt | revise | hold) bound to the stored integrated response hash."""
    rid = value
    if not ID_PATTERN.fullmatch(rid):
        problem(handler, 404, 'RUN_NOT_FOUND')
        return
    body = _read_json(handler, MD_DECISION_MAX_BYTES)
    if body is _INVALID:
        return
    error = _md_decision_error(body, rid)
    if error:
        problem(handler, 422, error, request_id=rid)
        return
    try:
        record = store().add_md_decision(rid, body['integrated_response_sha256'], body['decision'], body.get('note'))
    except RunStateError as exc:
        problem(handler, exc.http_status, exc.code, request_id=rid)
        return
    handler._json(201, record)


def _get_md_decisions(handler, value):
    """{request_id, integrated_response_sha256 (what POST verifies against), decisions oldest first}."""
    rid = value
    private = store()
    if not ID_PATTERN.fullmatch(rid) or not private.run_exists(rid):
        problem(handler, 404, 'RUN_NOT_FOUND', request_id=_known_id(rid))
        return
    current = private.integrated_response(rid)
    handler._json(200, {'request_id': rid, 'integrated_response_sha256': current[1] if current else None,
                        'decisions': private.md_decisions(rid)})


def _get_planning_sheet(handler, value):
    """GET /api/workflow/runs/{id}/planning-sheet.png: the 기획서 PNG of a finished run.

    Presentation only (planning_sheet.py): renders the stored integrated envelope, the verified final
    image bytes and the MD decision history; it calls no agent and changes no stored value."""
    from .planning_sheet import PlanningSheetError, planning_sheet_png
    rid = value
    if not ID_PATTERN.fullmatch(rid):
        problem(handler, 404, 'RUN_NOT_FOUND')
        return
    private = store()
    final = private.run(rid, 'current') or private.run(rid)
    if not final:
        if private.run(rid, 'progress'):
            problem(handler, 409, 'RUN_NOT_FINISHED', request_id=rid)
        else:
            problem(handler, 404, 'RUN_NOT_FOUND', request_id=rid)
        return
    try:
        png = planning_sheet_png(final['envelope'], load_artifact=private.verify, decisions=private.md_decisions(rid))
    except PlanningSheetError as exc:
        problem(handler, exc.http_status, exc.code, request_id=rid)
        return
    handler._send(200, png, 'image/png',
                  {'Content-Disposition': f'attachment; filename="vogue-planning-sheet-{rid}.png"'})


def _system_status(handler):
    from .system_status import system_status
    handler._json(200, system_status())


SERVED_MEDIA_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'application/json'}


def _get_artifact(handler, value):
    """Serve stored bytes after verification. Only images and JSON keep their media type; anything
    else (e.g. an upstream text/html error body) is an attachment, never same-origin active content."""
    artifact_id = value
    private = store()
    if SHA256_HEX.fullmatch(artifact_id):
        registered = private.media_types(artifact_id)
        if not registered:
            problem(handler, 404, 'ARTIFACT_NOT_FOUND')
            return
        rank = lambda item: (not item[0].startswith('image/'), item[0] != 'application/json', item[0])  # noqa: E731
        media, size = sorted(registered, key=rank)[0]
        meta = p08_artifact(artifact_id, media, size)
    else:
        # Legacy artifact://local/<uuid4 hex> rows minted before content addressing.
        require(len(artifact_id) == 32 and all(c in '0123456789abcdef' for c in artifact_id), 'ARTIFACT_ID_INVALID')
        with private.db() as db:
            row = db.execute('SELECT metadata FROM artifacts WHERE uri=?', ('artifact://local/'+artifact_id,)).fetchone()
        if row is None:
            problem(handler, 404, 'ARTIFACT_NOT_FOUND')
            return
        meta = strict_json(row[0].encode())
    data = private.verify(meta)
    if meta['media_type'] in SERVED_MEDIA_TYPES:
        handler._send(200, data, meta['media_type'])
    else:
        handler._send(200, data, 'application/octet-stream',
                      {'Content-Disposition': 'attachment; filename="artifact-' + artifact_id + '.bin"'})


ROUTE_HANDLERS = {
    'start_run': _start_run,
    'list_runs': _list_runs,
    'system_status': _system_status,
    'upload_image': _upload_image,
    'retry_image': _retry_image,
    'get_run': _get_run,
    'get_md_decisions': _get_md_decisions,
    'post_md_decision': _post_md_decision,
    'get_artifact': _get_artifact,
    'get_planning_sheet': _get_planning_sheet,
}


def readiness():
    """Configuration observations only. Never equate env presence to live readiness."""
    urls = ['VOGUE_TREND_AGENT_URL','VOGUE_STOCK_AGENT_URL','VOGUE_NEW_PRODUCT_AGENT_URL','VOGUE_SALES_MARKET_AGENT_URL']
    gate = contract_gate_status()
    return dict(configured=all(bool(os.environ.get(key)) for key in urls), ready=False,
                endpoint_health='not_checked', authentication='not_verified', schema_compatibility='not_verified',
                data_verified=False, end_to_end=False, baseline_state=gate['state'], contract_gate=gate['gate'],
                service_token_configured=bool(os.environ.get('VOGUE_AGENT_SERVICE_BEARER_TOKEN')),
                description='Trend → Stock → P06 Draft-only → P07 full Draft Review → P08 final image; MD pending.')
