"""Final image providers (fixture | openai) and the image-retry switch (conventions section 9)."""
import importlib.util
import io
import unittest
from unittest.mock import Mock, patch

from _http_support import LiveServerMixin, environment, real_tempdir
from fashion_planner_orchestrator import workflow_http
from fashion_planner_orchestrator.workflow_store import checked_image

HAVE_WORKFLOW_EXTRA = bool(importlib.util.find_spec('jsonschema') and importlib.util.find_spec('PIL'))
LINEAGE = dict(draft_identity={'draft_id': 'd', 'revision': 1, 'content_sha256': 'a' * 64}, draft_response_sha256='b' * 64,
               review_identity={'review_id': 'r', 'revision': 1, 'content_sha256': 'c' * 64}, review_response_sha256='d' * 64,
               integration_input_sha256='e' * 64)
AGENTS = {k: 'http://127.0.0.1:9' for k in ('ORCHESTRATOR_TREND_URL', 'ORCHESTRATOR_INVENTORY_URL',
                                            'ORCHESTRATOR_NOVELTY_URL', 'ORCHESTRATOR_RESEARCH_URL')}


@unittest.skipUnless(HAVE_WORKFLOW_EXTRA, 'workflow extra (jsonschema, Pillow) required')
class FixtureImageProviderTests(unittest.TestCase):
    def test_fixture_png_is_deterministic_valid_and_offline(self):
        from PIL import Image
        with environment(ORCHESTRATOR_WORKFLOW_IMAGES_ENABLED='1'), \
                patch('socket.socket.connect', side_effect=AssertionError('no network')):
            first, mime = workflow_http.fixture_image({'request_id': 'r-1', 'lineage': LINEAGE})
            again, _ = workflow_http.fixture_image({'request_id': 'r-1', 'lineage': LINEAGE})
            other, _ = workflow_http.fixture_image({'request_id': 'r-1', 'lineage': dict(LINEAGE, integration_input_sha256='f' * 64)})
        self.assertEqual((mime, first), ('image/png', again))
        self.assertNotEqual(first, other)
        checked_image(first, 'image/png')
        with Image.open(io.BytesIO(first)) as image:
            self.assertEqual((image.format, image.size), ('PNG', (512, 512)))

    def test_fixture_provider_still_requires_images_enabled(self):
        with environment(), self.assertRaises(ValueError):
            workflow_http.fixture_image({'lineage': LINEAGE})

    def test_build_workflow_selects_the_provider(self):
        base = dict(ORCHESTRATOR_WORKFLOW_ALLOW_PROPOSED='1', FASHION_PLANNER_HTTP_DEV_MODE='1',
                    FASHION_PLANNER_SERVICE_TOKEN='synthetic-test-token', **AGENTS)
        store_dir = str(real_tempdir(self) / 'store')
        cases = [({}, None, False), ({'ORCHESTRATOR_WORKFLOW_IMAGE_PROVIDER': 'fixture'}, None, False),
                 ({'ORCHESTRATOR_WORKFLOW_IMAGES_ENABLED': '1', 'ORCHESTRATOR_WORKFLOW_IMAGE_PROVIDER': 'fixture'}, 'fixture_image', True),
                 ({'ORCHESTRATOR_WORKFLOW_IMAGES_ENABLED': '1', 'ORCHESTRATOR_WORKFLOW_IMAGE_PROVIDER': 'FIXTURE'}, 'fixture_image', True),
                 # 0.2.0: the default stage 06 provider composes the planning sheet; a key never selects an image model.
                 ({'ORCHESTRATOR_WORKFLOW_IMAGES_ENABLED': '1'}, 'planning_sheet_image', False),
                 ({'ORCHESTRATOR_WORKFLOW_IMAGES_ENABLED': '1', 'OPENAI_API_KEY': 'dummy-not-a-key'}, 'planning_sheet_image', False)]
        for extra, expected, labelled in cases:
            with self.subTest(extra=extra), environment(ORCHESTRATOR_WORKFLOW_STORE_DIR=store_dir, **base, **extra):
                workflow = workflow_http.build_workflow()
                self.assertEqual(getattr(workflow.image, '__name__', None), expected)
                self.assertEqual(workflow_http.FIXTURE_IMAGE_LIMITATION in workflow.limitations, labelled)
                self.assertEqual(workflow_http.PLANNING_SHEET_LIMITATION in workflow.limitations, expected == 'planning_sheet_image')

    def test_invalid_provider_is_a_409_when_images_are_enabled(self):
        # openai is refused too: since 0.2.0 orchestrator calls no image model (novelty makes the images).
        for provider in ('dall-e', 'openai'):
            with self.subTest(provider=provider):
                handler = Mock()
                request = dict(contract_version='fashion-planner-workflow', request_id='r-1', input=dict(user_input=dict(text='t', images=[])))
                with environment(ORCHESTRATOR_WORKFLOW_ALLOW_PROPOSED='1', FASHION_PLANNER_HTTP_DEV_MODE='1', ORCHESTRATOR_WORKFLOW_IMAGES_ENABLED='1',
                                 ORCHESTRATOR_WORKFLOW_IMAGE_PROVIDER=provider, ORCHESTRATOR_WORKFLOW_STORE_DIR=str(real_tempdir(self)),
                                 FASHION_PLANNER_SERVICE_TOKEN='synthetic-test-token', **AGENTS):
                    workflow_http.dispatch(handler, request)
                status, body = handler._json.call_args.args
                self.assertEqual((status, body['error']['code']), (409, 'IMAGE_PROVIDER_INVALID'))


class ImageRetrySwitchTests(LiveServerMixin, unittest.TestCase):
    def setUp(self):
        self.start_server()

    def retry(self, **env):
        with environment(**env):
            return self.request('POST', '/v1/orchestrations/image-retries', {'request_id': 'r-1', 'retry_key': 'k-1'})

    def test_retries_are_disabled_by_default(self):
        with patch.object(workflow_http, 'build_workflow', side_effect=AssertionError('no work')):
            for value in (None, '0', 'false'):
                with self.subTest(value=value):
                    env = {} if value is None else {'ORCHESTRATOR_WORKFLOW_IMAGE_RETRY_ENABLED': value}
                    status, body, _ = self.retry(**env)
                    self.assertEqual((status, body['error']['code'], body['error']['retryable']), (409, 'IMAGE_RETRY_DISABLED', False))

    def test_enabled_retry_reaches_the_state_machine(self):
        fake = Mock()
        fake.retry_image.return_value = {'http_status': 200, 'envelope': {'status': 'partial'}}
        with patch.object(workflow_http, 'build_workflow', return_value=fake), \
                environment(ORCHESTRATOR_WORKFLOW_IMAGE_RETRY_ENABLED='1'):
            status, body, _ = self.request('POST', '/v1/orchestrations/image-retries', {'request_id': 'r-1', 'retry_key': 'k-1'})
            wrong = self.request('POST', '/v1/orchestrations/image-retries',
                                 {'contract_version': 'old-workflow', 'request_id': 'r-1', 'retry_key': 'k-2'})
        self.assertEqual((status, body), (200, {'status': 'partial'}))
        fake.retry_image.assert_called_once_with('r-1', 'k-1')
        self.assertEqual((wrong[0], wrong[1]['error']['code']), (422, 'UNSUPPORTED_CONTRACT_VERSION'))
        self.assertEqual(self.retry()[0], 409)  # the switch is read per request


if __name__ == '__main__':
    unittest.main()
