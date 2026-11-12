"""Single sequential workflow state machine, independent of HTTP handlers."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
from .workflow_contract import VERSION, STAGES, require, walk
from .workflow_store import ArtifactError, RESOLVER_CODES, canonical, digest, strict_json, checked_image

# Namespaces whose artifacts P08 imports through a reader (conventions section 8).
PEER_NAMESPACES = ('p07',)
# An upstream that itself answers 503/504 keeps that meaning (the contract's error table): the same
# codes as a refused connection or a socket timeout, retryable. Every other non-2xx is 502.
UPSTREAM_STATUS_CODES = {503: 'UPSTREAM_UNAVAILABLE', 504: 'UPSTREAM_TIMEOUT'}


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WireResponse:
    body: bytes
    status: int = 200
    media_type: str = 'application/json'
    truncated: bool = False


class StageFailure(RuntimeError):
    def __init__(self, code, http_status=502, artifact=None):
        self.code, self.http_status, self.artifact = code, http_status, artifact
        super().__init__(code)


def stage_failure(exc, artifact=None):
    """Outbound failure mapping (conventions section 4) for one producer call.

    Timeouts are OSErrors too, so they are matched first (QA-O-01: a read timeout used to
    surface as 503 UPSTREAM_UNAVAILABLE). TLS certificate errors are both OSError and
    ValueError; they are an unavailable dependency, not a contract violation.
    """
    if isinstance(exc, StageFailure):
        return exc
    if isinstance(exc, ArtifactError) and exc.code in RESOLVER_CODES:  # section 8 codes keep their status
        return StageFailure(exc.code, exc.http_status, artifact)
    if isinstance(exc, TimeoutError):  # socket.timeout is TimeoutError
        return StageFailure('UPSTREAM_TIMEOUT', 504, artifact)
    if isinstance(exc, (OSError, http.client.HTTPException)):  # refused, reset, DNS, TLS
        return StageFailure('UPSTREAM_UNAVAILABLE', 503, artifact)
    if isinstance(exc, ValueError):  # ContractViolation, strict JSON, schema
        return StageFailure('CONTRACT_VIOLATION', 502, artifact)
    if artifact is not None and isinstance(exc, (KeyError, TypeError, IndexError, AttributeError)):
        return StageFailure('CONTRACT_VIOLATION', 502, artifact)  # malformed body after a response arrived
    return StageFailure('UPSTREAM_UNAVAILABLE', 503, artifact)


class Workflow:
    """Injected producers return actual WireResponse bytes. No implicit fallbacks."""
    def __init__(self, *, trend, stock, draft, review, image, store, contract, artifact_reader=None, limitations=()):
        self.adapters = dict(trend=trend, stock=stock, planning_draft=draft, sales_market_review=review)
        self.image, self.store, self.contract = image, store, contract
        self.artifact_reader = artifact_reader
        # Run-level limitations decided by the caller (e.g. a development run on a proposed contract).
        self.limitations = tuple(limitations)

    def _retained(self, raw, artifact):
        body = raw['result']
        # Never replace source dates by receipt time. Preserve multiple observations in raw.
        inventories = [n['inventory'] for n in body.get('products', []) if isinstance(n, dict) and isinstance(n.get('inventory'), dict)]
        obs = body.get('review', {}).get('observation') if isinstance(body.get('review'), dict) else None
        if obs:
            observation = copy.deepcopy(obs)
        else:
            dates = {n.get('as_of') for n in inventories}
            natures = {n.get('data_nature') for n in inventories}
            sample = 'sample' in natures
            observation = dict(observed_at=now(), as_of=next(iter(dates)) if len(dates) == 1 else None,
                               synthetic=True if sample else None,
                               data_nature='sample' if natures == {'sample'} else 'mixed' if len(natures) > 1 else 'unknown',
                               source=raw['producer']['agent_id'],
                               limitations=['Receipt time is not source observation time; unspecified provenance remains unknown.'])
        return dict(raw_response=copy.deepcopy(raw), content_sha256=digest(raw), hash_profile='python-json-sort/1',
                    raw_artifact=artifact, observation=observation)

    def _key(self, rid, stage, payload, attempt='initial'):
        return digest(dict(request_id=rid, stage=stage, input_sha256=digest(payload), contract_version=VERSION,
                           contract_sha256=self.contract.profile_sha256, attempt=attempt))

    def _call(self, stage, payload, rid, known):
        key = self._key(rid, stage, payload)
        cached = self.store.checkpoint(key)
        if cached:
            state, value = cached
            if state == 'done':
                self.contract.semantic(value, rid, self.store, known)
                return value
            if state == 'failed':
                raise StageFailure(value['code'], value['http_status'], value.get('artifact'))
            raise StageFailure('STAGE_OUTCOME_UNKNOWN', 409)
        request_artifact = self.store.put(canonical(payload))
        self.store.checkpoint_write(key + ':request', 'done', dict(stage=stage, request_artifact=request_artifact))
        self.store.checkpoint_write(key, 'started')
        artifact = None
        try:
            wire = self.adapters[stage](copy.deepcopy(payload))
            require(isinstance(wire, WireResponse), 'WIRE_BYTES_REQUIRED')
            artifact = self.store.put(wire.body, 'application/vnd.vogue.truncated-response' if wire.truncated else wire.media_type)
            if wire.truncated or len(wire.body) > 16777216:
                raise StageFailure('UPSTREAM_BODY_TOO_LARGE', 502, artifact)
            if not 200 <= wire.status < 300:
                # Upstream 401/403 -> UPSTREAM_AUTH; 503/504 keep their meaning; any other non-2xx -> 502.
                # The body stays retained privately as the rejected response artifact.
                if wire.status in {401, 403}:
                    raise StageFailure('UPSTREAM_AUTH', wire.status, artifact)
                if wire.status in UPSTREAM_STATUS_CODES:
                    raise StageFailure(UPSTREAM_STATUS_CODES[wire.status], wire.status, artifact)
                raise StageFailure('UPSTREAM_HTTP_ERROR', 502, artifact)
            raw = strict_json(wire.body)
            self.contract.envelope(raw, stage, rid)
            self._import_peer_artifacts(raw)
            if stage == 'trend' and raw['status'] == 'ok':
                season = payload['input'].get('trend_terms', {}).get('season', {})
                if season.get('origin') == 'user_requested':
                    applied = raw['result']['applied_filters']['season'] or {}
                    require(all(applied.get(k) == season.get(k) for k in ('year','season')), 'APPLIED_FILTER_CONFLICT')
            retained = self._retained(raw, artifact)
            self.contract.semantic(retained, rid, self.store, known)
            if stage == 'planning_draft':
                require(raw['result']['source_request'] == payload, 'SOURCE_REQUEST_REWRITTEN')
                if raw['status'] == 'ok':
                    require(payload['input']['stock_response']['raw_response']['status'] == 'ok', 'UPSTREAM_STATUS_HIDDEN')
            if stage == 'sales_market_review':
                require(raw['result']['review']['draft_response_sha256'] == payload['input']['planning_draft']['content_sha256'], 'REVIEW_DRAFT_HASH_MISMATCH')
            self.store.checkpoint_write(key, 'done', retained)
            return retained
        except Exception as exc:
            failure = stage_failure(exc, artifact)
            self.store.checkpoint_write(key, 'failed', dict(code=failure.code, http_status=failure.http_status, artifact=failure.artifact))
            raise failure from None

    def _import_peer_artifacts(self, raw):
        """Import peer (P07) artifact references through the reader and the store's import_verified.

        P08's own references are checked by the semantic pass; a peer reference that is not yet in
        the store needs a configured reader (503 ARTIFACT_READER_REQUIRED otherwise).
        """
        prefixes = tuple('artifact://' + namespace + '/' for namespace in PEER_NAMESPACES)
        for node in walk(raw):
            if not {'artifact_id', 'uri', 'sha256', 'immutable'} <= node.keys():
                continue
            if not isinstance(node['uri'], str) or not node['uri'].startswith(prefixes):
                continue
            try:
                self.store.verify(node)
                continue
            except ValueError:
                pass
            if self.artifact_reader is None:
                raise ArtifactError('ARTIFACT_READER_REQUIRED')
            self.store.import_verified(node, self.artifact_reader(node))

    def _trace(self, body, stage, payload, start, status, response_hash=None, errors=None):
        logical_hash = digest(payload)
        if stage == 'trend':
            logical_hash = digest(body['planning_request'])
        elif stage == 'stock':
            logical_hash = digest(body['sources']['trend']['raw_response']['result'])
        elif stage == 'sales_market_review':
            logical_hash = body['sources']['planning_draft']['content_sha256']
        elif stage == 'integration':
            logical_hash = digest({k + '_response_sha256': body['sources'][v]['content_sha256'] for k,v in [('draft','planning_draft'),('review','sales_market_review')]})
        body['stages'].append(dict(sequence=len(body['stages']) + 1, stage=stage, request_id=body['request_id'],
                                   status=status, input_sha256=logical_hash, response_sha256=response_hash,
                                   started_at=start, completed_at=now(), errors=errors or []))

    def _image(self, body, attempt):
        if self.image is None:
            body['status'] = 'partial'
            body['final_image'] = dict(status='skipped', artifact=None, lineage=None, reason='Image provider is not configured or enabled.', provider_calls=0)
            return
        sources = body['sources']
        draft, review = sources['planning_draft'], sources['sales_market_review']
        dr, rr = draft['raw_response']['result'], review['raw_response']['result']
        payload = dict(draft_response_sha256=draft['content_sha256'], review_response_sha256=review['content_sha256'], integration=body['integration'])
        lineage = dict(draft_identity=dr['draft_identity'], draft_response_sha256=draft['content_sha256'],
                       review_identity=rr['review_identity'], review_response_sha256=review['content_sha256'], integration_input_sha256=digest(payload))
        rid = body['request_id']
        image_request = dict(request_id=rid, **payload, lineage=lineage, planning_draft=draft, sales_market_review=review)
        key = self._key(rid, 'final_image', payload, attempt)
        start = now()
        checkpoint = self.store.checkpoint(key)
        if checkpoint and checkpoint[0] == 'done':
            body['final_image'] = checkpoint[1]
        elif checkpoint:
            # Crash after provider invocation must not silently issue a second charge.
            body['final_image'] = dict(status='failed', artifact=None, lineage=lineage, reason='IMAGE_OUTCOME_UNKNOWN', provider_calls=1)
        else:
            request_artifact = self.store.put(canonical(image_request))
            self.store.checkpoint_write(key + ':request', 'done', dict(stage='final_image', request_artifact=request_artifact))
            self.store.checkpoint_write(key, 'started')
            try:
                data, mime = self.image(copy.deepcopy(image_request))
                checked_image(data, mime)
                artifact = self.store.put(data, mime)
                body['final_image'] = dict(status='generated', artifact=artifact, lineage=lineage, reason=None, provider_calls=1)
            except Exception:
                body['final_image'] = dict(status='failed', artifact=None, lineage=lineage, reason='IMAGE_GENERATION_FAILED', provider_calls=1)
            self.store.checkpoint_write(key, 'done', body['final_image'])
        image = body['final_image']
        errors = [] if image['status'] == 'generated' else [dict(code=image['reason'], message='Final image unavailable; prior sources retained.', stage='final_image', fatal=False, retryable=True)]
        self._trace(body, 'final_image', payload, start, 'ok' if not errors else 'error', image['artifact']['sha256'] if image['artifact'] else None, errors)
        body['errors'].extend(errors)
        if errors:
            body['status'] = 'partial'

    def run(self, *, request_id, user_input, planning_request, trend_query):
        # All semantic input failures occur before any producer invocation.
        require(isinstance(request_id, str) and 0 < len(request_id) <= 128, 'REQUEST_ID_INVALID')
        require(isinstance(user_input, dict) and isinstance(planning_request, dict) and isinstance(trend_query, dict), 'INPUT_OBJECT_REQUIRED')
        require(planning_request.get('request_id') == request_id and trend_query.get('request_id') == request_id, 'REQUEST_ID_MISMATCH')
        require(user_input.get('text') == planning_request.get('query') == trend_query.get('original_query'), 'USER_TEXT_MISMATCH')
        # Validate input definitions with a local registry, before building upstream requests.
        for value, name in ((user_input, 'user_input'), (planning_request, 'planning_request')):
            validator = self.contract.validators['common.schema.json'].evolve(schema={'$ref': 'https://contracts.vogue.invalid/workflow/0.1/common.schema.json#/$defs/' + name})
            require(validator.is_valid(value), 'INPUT_SCHEMA_INVALID')
        for image in user_input['images']:
            data = self.store.verify(image, owner=request_id)
            checked_image(data, image['media_type'])
        submitted = dict(user_input=user_input, planning_request=planning_request, trend_query=trend_query)
        with self.store.lock(request_id):
            self.store.bind(request_id, digest(submitted))
            self.store.index_run(request_id, user_input['text'])
            previous = self.store.run(request_id, 'current') or self.store.run(request_id)
            if previous:
                return previous
            body = dict(schema_version='integrated-result/1.0', agent_id='vogue-orchestrator', request_id=request_id, status='ok',
                        user_input=copy.deepcopy(user_input), planning_request=copy.deepcopy(planning_request),
                        sources={s: None for s in STAGES[:4]}, stages=[], integration=None,
                        final_image=dict(status='skipped', artifact=None, lineage=None, reason='No eligible review.', provider_calls=0),
                        limitations=['Only the first item and first season are searched; full user text is retained.',
                                     'Input images are references for Draft and integration; Trend receives a text search query.',
                                     *self.limitations],
                        errors=[], rejected_response_artifacts=[],
                        md_review=dict(status='pending', choices=['adopt','revise','hold'], reserved_decisions=['price','cost','quantity','purchase_order','adoption']))
            http_status, known = 200, {}
            payload = dict(contract_version='vogue-handoff/0.1', request_id=request_id, input=trend_query)
            for stage in STAGES[:4]:
                start = now()
                try:
                    if stage == 'stock':
                        payload = dict(contract_version='vogue-handoff/0.1', request_id=request_id, input=body['sources']['trend']['raw_response']['result'])
                    elif stage == 'planning_draft':
                        trend, stock = body['sources']['trend'], body['sources']['stock']
                        context = dict(planning_request_sha256=digest(planning_request), trend_response_sha256=trend['content_sha256'],
                                       stock_input_sha256=digest(trend['raw_response']['result']), stock_wire_request_sha256=digest(payload), stock_response_sha256=stock['content_sha256'])
                        payload = dict(contract_version=VERSION, request_id=request_id, input=dict(mode='draft_only', user_input=user_input,
                                       planning_request=planning_request, trend_response=trend, stock_response=stock, stock_call_context=context))
                        self.contract.schema(payload, 'planning-draft-request.schema.json')
                        self.contract.semantic(payload, request_id, self.store, known)
                    elif stage == 'sales_market_review':
                        source = body['sources']['planning_draft']
                        dr = source['raw_response']['result']['draft']
                        payload = dict(contract_version=VERSION, request_id=request_id, input=dict(planning_draft=source,
                                       derived_view=dict(purpose='search_only', adapter_version='p08-search/1', source_response_sha256=source['content_sha256'],
                                                         source_pointers=['/result/draft/category','/result/draft/season'], product={k: dr[k] for k in ('category','season')})))
                        self.contract.schema(payload, 'sales-market-review-request.schema.json')
                        self.contract.semantic(payload, request_id, self.store, known)
                    retained = self._call(stage, payload, request_id, known)
                    body['sources'][stage] = retained
                    known[retained['content_sha256']] = retained['raw_response']
                    raw = retained['raw_response']
                    status = raw['status']
                    errors = [dict(code=e.get('code','UPSTREAM_ERROR'), message=e.get('message','Producer error.'), stage=stage, fatal=status == 'error', retryable=bool(e.get('retryable',False))) for e in raw['result'].get('errors', [])]
                    self._trace(body, stage, payload, start, status, retained['content_sha256'], errors)
                    body['limitations'].extend(raw['result'].get('limitations', []))
                    body['errors'].extend(errors)
                    if status == 'error':
                        body['status'], http_status = 'error', 502
                        if not errors:
                            body['errors'].append(dict(code='UPSTREAM_DOMAIN_ERROR', message='Producer returned an error.', stage=stage, fatal=True, retryable=False))
                        break
                    if status == 'no_results' and stage in {'trend', 'planning_draft'}:
                        body['status'] = 'no_results'
                        break
                    if status in {'partial', 'no_results'}:
                        body['status'] = 'partial'
                    if stage == 'sales_market_review' and status == 'no_results':
                        break
                except Exception as exc:
                    failure = exc if isinstance(exc, StageFailure) else StageFailure('CONTRACT_VIOLATION' if isinstance(exc, ValueError) else 'STAGE_FAILURE')
                    error = dict(code=failure.code, message='Stage stopped; see private retained response and trace.', stage=stage, fatal=True, retryable=failure.http_status in {503,504})
                    body['errors'].append(error)
                    if failure.artifact and failure.artifact['size_bytes'] > 0:
                        body['rejected_response_artifacts'].append(failure.artifact)
                    elif failure.artifact:
                        body['limitations'].append('Empty upstream bytes retained privately at ' + failure.artifact['uri'])
                    self._trace(body, stage, payload, start, 'error', errors=[error])
                    body['status'], http_status = 'error', failure.http_status
                    break
                finally:
                    # Save even if the process exits before the next stage or an image fails.
                    self.store.save_run(request_id, 'progress', self._response(body, http_status))
            if body['sources']['sales_market_review'] and body['status'] not in {'error','no_results'} and body['sources']['sales_market_review']['raw_response']['status'] in {'ok','partial'}:
                start = now()
                draft = body['sources']['planning_draft']['raw_response']['result']['draft']
                review = body['sources']['sales_market_review']['raw_response']['result']['review']
                body['integration'] = dict(claims=copy.deepcopy(draft['claims'] + review['findings']),
                                           limitations=list(dict.fromkeys(body['limitations'] + draft['limitations'] + review['limitations'])))
                self._trace(body, 'integration', body['sources'], start, body['status'], digest(body['integration']))
                self.store.save_run(request_id, 'progress', self._response(body, http_status))
                self._image(body, 'initial')
            response = self._response(body, http_status)
            self.contract.schema(response['envelope'], 'integrated-result.schema.json')
            self.contract.semantic(response['envelope'], request_id, self.store, known)
            self.store.save_run(request_id, 'initial', response)
            self.store.save_run(request_id, 'current', response)
            return response

    @staticmethod
    def _response(body, http_status):
        return dict(http_status=http_status, envelope=dict(contract_version=VERSION, request_id=body['request_id'],
                    producer=dict(agent_id='vogue-orchestrator', schema_version='integrated-result/1.0'), status=body['status'], result=copy.deepcopy(body)))

    def retry_image(self, request_id, retry_key):
        require(isinstance(retry_key, str) and 0 < len(retry_key) <= 128 and retry_key != 'initial', 'RETRY_KEY_REQUIRED')
        with self.store.lock(request_id):
            previous = self.store.run(request_id, 'image:' + retry_key)
            if previous:
                return previous
            original = self.store.run(request_id, 'current') or self.store.run(request_id)
            require(original is not None, 'RUN_NOT_FOUND')
            body = copy.deepcopy(original['envelope']['result'])
            if body['final_image']['status'] == 'generated':
                self.store.save_run(request_id, 'image:' + retry_key, original)
                return original
            require(body['final_image']['status'] in {'failed','skipped'} and body['integration'] is not None, 'IMAGE_RETRY_NOT_ELIGIBLE')
            known = {s['content_sha256']:s['raw_response'] for s in body['sources'].values() if s}
            self.contract.semantic(body['sources'], request_id, self.store, known)
            body['stages'] = body['stages'][:5]
            body['errors'] = [e for e in body['errors'] if e['code'] not in {'IMAGE_GENERATION_FAILED','IMAGE_OUTCOME_UNKNOWN'}]
            body['status'] = 'partial' if any(s['raw_response']['status'] != 'ok' for s in body['sources'].values()) else 'ok'
            self._image(body, 'image:' + retry_key)
            response = self._response(body, 200)
            self.contract.schema(response['envelope'], 'integrated-result.schema.json')
            self.contract.semantic(response['envelope'], request_id, self.store, known)
            self.store.save_run(request_id, 'image:' + retry_key, response)
            self.store.save_run(request_id, 'current', response)
            return response
