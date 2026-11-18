"""The real LLM call (src/fashion_planner_orchestrator/llm_provider.py) with fake SDK clients. No network, no .env.

Every test passes its own environment and a non-existent .env path, so the repository's real .env
(which holds the user's key) is never read.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fashion_planner_orchestrator import llm_provider as llm

NO_ENV_FILE = Path(tempfile.gettempdir()) / "vogue-no-such-dir" / ".env"
BUNDLE = SimpleNamespace(system="SYSTEM PROMPT", user="USER MESSAGE")
SECRET = "sk-test-secret-value"


def settings(provider="anthropic", **overrides):
    values = dict(provider=provider, model="m", api_key=SECRET, timeout_seconds=5, max_tokens=100, max_retries=0)
    values.update(overrides)
    return llm.LLMSettings(**values)


class FakeAnthropic:
    def __init__(self, stop_reason="end_turn", blocks=None):
        self.calls = []
        self.response = SimpleNamespace(
            stop_reason=stop_reason,
            content=blocks if blocks is not None else [SimpleNamespace(type="thinking", thinking=""),
                                                       SimpleNamespace(type="text", text='{"a": 1}')])
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeOpenAI:
    def __init__(self, finish_reason="stop", content='{"a": 1}'):
        self.calls = []
        self.response = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=finish_reason, message=SimpleNamespace(content=content))])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class SettingsTests(unittest.TestCase):
    def load(self, environ, env_text=None):
        if env_text is None:
            return llm.load_settings(environ, env_path=NO_ENV_FILE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(env_text, encoding="utf-8")
            return llm.load_settings(environ, env_path=path)

    def test_env_file_is_parsed_and_the_environment_wins(self):
        text = "# comment\nexport LLM_MODEL='claude-test'\nLLM_API_KEY=\"from-file\"  \nOTHER=x\n"
        loaded = self.load({}, text)
        self.assertEqual((loaded.provider, loaded.model, loaded.api_key), ("anthropic", "claude-test", "from-file"))
        loaded = self.load({"LLM_MODEL": "gpt-test", "LLM_API_KEY": "from-env"}, text)
        self.assertEqual((loaded.provider, loaded.model, loaded.api_key), ("openai", "gpt-test", "from-env"))

    def test_explicit_provider_overrides_the_model_name_guess(self):
        loaded = self.load({"LLM_MODEL": "my-model", "LLM_API_KEY": "k", "LLM_PROVIDER": "Anthropic"})
        self.assertEqual(loaded.provider, "anthropic")
        with self.assertRaises(llm.LLMConfigurationError):
            self.load({"LLM_MODEL": "m", "LLM_API_KEY": "k", "LLM_PROVIDER": "other"})

    def test_missing_model_or_key_fails_without_echoing_the_key(self):
        for environ in ({}, {"LLM_MODEL": "m"}, {"LLM_API_KEY": SECRET}, {"LLM_MODEL": " ", "LLM_API_KEY": SECRET}):
            with self.subTest(keys=sorted(environ)):
                with self.assertRaises(llm.LLMConfigurationError) as caught:
                    self.load(environ)
                self.assertNotIn(SECRET, str(caught.exception))

    def test_settings_repr_and_status_never_show_the_key(self):
        self.assertNotIn(SECRET, repr(settings()))
        status = llm.llm_status({"LLM_MODEL": "claude-x", "LLM_API_KEY": SECRET}, env_path=NO_ENV_FILE)
        self.assertEqual(status, {"configured": True, "provider": "anthropic", "model": "claude-x", "detail": None})
        self.assertNotIn(SECRET, str(status))
        self.assertFalse(llm.llm_status({}, env_path=NO_ENV_FILE)["configured"])

    def test_config_file_values_are_validated(self):
        loaded = self.load({"LLM_MODEL": "m", "LLM_API_KEY": "k"})
        self.assertEqual((loaded.timeout_seconds, loaded.max_tokens, loaded.max_retries), (60, 16000, 2))
        self.assertEqual(loaded.temperature, 0)  # decision OQ-14
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "llm.json"
            path.write_text('{"timeout_seconds": "60", "max_tokens": 1, "max_retries": 0}', encoding="utf-8")
            with self.assertRaises(llm.LLMConfigurationError):
                llm.load_settings({"LLM_MODEL": "m", "LLM_API_KEY": "k"}, env_path=NO_ENV_FILE, config_path=path)


class CallTests(unittest.TestCase):
    def test_temperature_is_sent_only_when_configured(self):
        # decision OQ-14: configs/llm.json temperature 0; null omits the parameter (provider default)
        for provider, fake in (("anthropic", FakeAnthropic), ("openai", FakeOpenAI)):
            with self.subTest(provider=provider):
                client = fake()
                llm.build_draft_provider(settings(provider, temperature=0), client=client)(BUNDLE)
                self.assertEqual(client.calls[-1]["temperature"], 0)
                llm.build_draft_provider(settings(provider), client=client)(BUNDLE)
                self.assertNotIn("temperature", client.calls[-1])

    def test_text_call_uses_the_same_settings_and_names_its_model(self):
        client = FakeOpenAI()
        call = llm.build_text_call(settings("openai"), client=client)
        self.assertEqual(call("SYS", "USR"), '{"a": 1}')
        self.assertEqual(call.model, "m")
        self.assertEqual(client.calls[0]["messages"], [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}])

    def test_anthropic_call_sends_system_and_user_and_returns_the_text_blocks(self):
        client = FakeAnthropic()
        text = llm.build_draft_provider(settings("anthropic"), client=client)(BUNDLE)
        self.assertEqual(text, '{"a": 1}')
        self.assertEqual(client.calls, [{"model": "m", "max_tokens": 100, "system": "SYSTEM PROMPT",
                                         "messages": [{"role": "user", "content": "USER MESSAGE"}]}])

    def test_anthropic_refusal_cut_off_and_empty_output_are_errors(self):
        for client in (FakeAnthropic(stop_reason="refusal"), FakeAnthropic(stop_reason="max_tokens"),
                       FakeAnthropic(blocks=[])):
            with self.subTest(stop=client.response.stop_reason):
                with self.assertRaises(llm.LLMCallError):
                    llm.build_draft_provider(settings("anthropic"), client=client)(BUNDLE)

    def test_openai_call_asks_for_a_json_object(self):
        client = FakeOpenAI()
        text = llm.build_draft_provider(settings("openai"), client=client)(BUNDLE)
        self.assertEqual(text, '{"a": 1}')
        call = client.calls[0]
        self.assertEqual(call["messages"], [{"role": "system", "content": "SYSTEM PROMPT"},
                                            {"role": "user", "content": "USER MESSAGE"}])
        self.assertEqual(call["response_format"], {"type": "json_object"})

    def test_openai_cut_off_and_empty_output_are_errors(self):
        for client in (FakeOpenAI(finish_reason="length"), FakeOpenAI(content=None)):
            with self.subTest(finish=client.response.choices[0].finish_reason):
                with self.assertRaises(llm.LLMCallError):
                    llm.build_draft_provider(settings("openai"), client=client)(BUNDLE)


if __name__ == "__main__":
    unittest.main()
