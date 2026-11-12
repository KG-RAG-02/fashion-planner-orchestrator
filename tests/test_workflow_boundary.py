import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from vogue_orchestrator.server import OrchestratorHandler, _legacy_migrated_followup, _save_latest_run, _load_latest_run
from vogue_orchestrator.workflow_http import dispatch, routes
from vogue_orchestrator.workflow_contract import WorkflowContract


class BoundaryTests(unittest.TestCase):
    def handler(self, body, path='/api/workflow/run'):
        h=object.__new__(OrchestratorHandler)
        h.path=path;h.command='POST';h.client_address=('127.0.0.1',12345)
        raw=json.dumps(body).encode()
        h.headers={'Content-Length':str(len(raw))};h.rfile=io.BytesIO(raw)
        h._json=Mock();return h

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'),'workflow extra required')
    def test_live_gate_blocks_before_any_query_provider(self):
        request=dict(contract_version='vogue-workflow/0.1',request_id='test-gate',input=dict(user_input=dict(text='test',images=[])))
        h=self.handler(request)
        with patch.dict(os.environ),patch('vogue_orchestrator.server.structure_query_for_ui') as llm:
            for key in ('VOGUE_WORKFLOW_ALLOW_PROPOSED','VOGUE_WORKFLOW_CONTRACT_BASELINE'):
                os.environ.pop(key,None)
            dispatch(h,request)
        self.assertEqual(h._json.call_args.args[0],409)
        self.assertEqual(h._json.call_args.args[1]['error']['code'],'CONTRACT_NOT_FROZEN')
        llm.assert_not_called()

    def test_target_boundary_preserves_non_200_terminal_result(self):
        h=self.handler({})
        response={'http_status':502,'envelope':{'status':'error','result':{'sources':{'trend':None}}}}
        fake=Mock();fake.run.return_value=response
        request=dict(contract_version='vogue-workflow/0.1',request_id='test',input=dict(user_input=dict(text='test',images=[]),trend_query={'request_id':'test'}))
        with patch('vogue_orchestrator.workflow_http.build_workflow',return_value=fake):
            dispatch(h,request)
        h._json.assert_called_once_with(502,response['envelope'])

    def test_upload_limits_and_remote_boundary(self):
        h=self.handler({},'/v1/input-images');h.headers={'Content-Length':'10485761'}
        self.assertTrue(routes(h,h.path));self.assertEqual(h._json.call_args.args[0],413)
        h=self.handler({},'/v1/input-images');h.client_address=('192.0.2.1',100)
        routes(h,h.path);self.assertEqual(h._json.call_args.args[0],403)

    def test_legacy_upstream_error_cannot_invoke_generators_or_review(self):
        for status in ['error','no_results']:
            result=dict(status=status,stopped_at='trend' if status=='error' else None,stages=[dict(stage='trend',status=status,raw_response={})])
            with patch('vogue_orchestrator.server.build_migrated_planning_draft') as generate, patch('vogue_orchestrator.server.request_sales_market_review') as review:
                _legacy_migrated_followup(SimpleNamespace(request_id='x'),result,'agent_http_v01','test',{},None)
                generate.assert_not_called();review.assert_not_called()
            self.assertEqual(result['status'],status)

    def test_legacy_http_regression_returns_502_after_trend_error(self):
        from test_public_api import PublicOrchestrationApiTests
        from vogue_orchestrator.core import Orchestrator
        rid='regression-trend-error'
        raw=dict(contract_version='vogue-handoff/0.1',request_id=rid,
                 producer=dict(agent_id='trend-analysis-agent',schema_version='1.1'),status='error',
                 result=dict(request_id=rid,schema_version='1.1',status='error',trends=[],evidence=[],limitations=['fixture failure']))
        downstream=Mock(side_effect=AssertionError('downstream must not run'))
        runner=Orchestrator(trend=lambda _:raw,stock=downstream,project_06=downstream,project_07=downstream,require_envelopes=True)
        with patch('vogue_orchestrator.server.build_v01_http_orchestrator',return_value=runner),patch('vogue_orchestrator.server.build_migrated_planning_draft') as generate:
            status,result=PublicOrchestrationApiTests().post('/v1/orchestrations',dict(mode='agent_http_v01',request_id=rid,input=dict(original_query='fixture',trend_query=dict(request_id=rid,original_query='fixture'))))
        self.assertEqual(status,502)
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['stopped_at'],'trend')
        self.assertEqual(len(result['stages']),1)
        generate.assert_not_called();downstream.assert_not_called()

    def test_error_and_image_failure_history_roundtrip(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as tmp:
            root=Path(tmp)
            with patch('vogue_orchestrator.server.LATEST_RUN_PATH',root/'latest.json'),patch('vogue_orchestrator.server.RUN_HISTORY_DIR',root/'runs'):
                for value in [dict(status='error',stages=[]),dict(status='partial',stages=[],image_generation={'status':'failed'})]:
                    self.assertIsNotNone(_save_latest_run('legacy_migrated',value))
                    self.assertEqual(_load_latest_run()['result']['status'],value['status'])

    def test_manifest_ports_paths_and_container_bind_projection(self):
        from vogue_orchestrator.service_manifest import DEFAULT_MANIFEST, services
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(DEFAULT_MANIFEST,root/'deploy'/'service-manifest.json')
        declared=json.loads(DEFAULT_MANIFEST.read_text(encoding='utf-8'))['services']
        missing=sorted({e['repo'] for e in declared.values() if not (root.parent/e['repo']).is_dir()})
        if missing:self.skipTest('sibling checkouts not present: '+', '.join(missing))
        with patch.dict(os.environ):
            os.environ.pop('VOGUE_SERVICE_MANIFEST',None)
            entries=services(require_runtime=False)
        self.assertEqual(next(e for e in entries if e['name']=='stock')['port'],8002)
        self.assertTrue(all('0.0.0.0' not in e['args'] for e in entries))
        self.assertEqual(next(e for e in entries if e['name']=='sales-market')['repo'].name,'sales-market-agent')

if __name__=='__main__':unittest.main()
