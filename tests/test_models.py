import contextlib
import dataclasses
import io
import tempfile
import unittest
from pathlib import Path

from battle_test import grounding
from battle_test.bedrock_client import MAX_ATTEMPTS, BedrockClient, BedrockError
from battle_test.config import BedrockModel, load_config
from battle_test.models import ModelError, Usage, make_client
from battle_test.ollama_client import OllamaClient, OllamaError

ROOT = Path(__file__).resolve().parent.parent

MODELS = {
    "cheap": BedrockModel("vendor.cheap-v1:0", 0.15, 1.20),
    "claude": BedrockModel("us.vendor.claude", 2.0, 10.0, max_tokens=8000, temperature=False),
    "unpriced": BedrockModel("vendor.unpriced"),
    "blank": BedrockModel(""),
}


class FakeRuntime:
    """Stands in for boto3's bedrock-runtime client: no network, no cost."""

    def __init__(self, events=None, error=None):
        self.events = events if events is not None else [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"delta": {"reasoningContent": {"text": "thinking..."}}}},
            {"contentBlockDelta": {"delta": {"text": "Hello, "}}},
            {"contentBlockDelta": {"delta": {"text": "court."}}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {"inputTokens": 120, "outputTokens": 30, "totalTokens": 150}}},
        ]
        self.error = error
        self.requests = []

    def converse_stream(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return {"stream": iter(self.events)}


def client(runtime):
    return BedrockClient("us-west-2", MODELS, max_tokens=16000, temperature=0.4, runtime=runtime)


class BedrockClientTest(unittest.TestCase):
    def test_reply_streams_and_reports_usage_without_the_reasoning(self):
        runtime, pieces, used = FakeRuntime(), [], []
        reply = client(runtime).chat("cheap", "SYSTEM", "USER", on_token=pieces.append, on_usage=used.append)
        self.assertEqual(reply, "Hello, court.")
        self.assertEqual(pieces, ["Hello, ", "court."])
        self.assertEqual(used, [Usage("cheap", 120, 30)])  # under the config's name, which the prices use

    def test_request_uses_the_models_id_and_settings(self):
        runtime = FakeRuntime()
        client(runtime).chat("cheap", "SYSTEM", "USER")
        request = runtime.requests[0]
        self.assertEqual(request["modelId"], "vendor.cheap-v1:0")
        self.assertEqual(request["system"], [{"text": "SYSTEM"}])
        self.assertEqual(request["messages"], [{"role": "user", "content": [{"text": "USER"}]}])
        self.assertEqual(request["inferenceConfig"], {"maxTokens": 16000, "temperature": 0.4})

    def test_per_model_limit_and_no_temperature(self):
        runtime = FakeRuntime()
        client(runtime).chat("claude", "s", "u")
        self.assertEqual(runtime.requests[0]["inferenceConfig"], {"maxTokens": 8000})

    def test_a_name_not_in_the_table_is_used_as_the_id(self):
        runtime = FakeRuntime()
        client(runtime).chat("vendor.some-model-v2:0", "s", "u")
        self.assertEqual(runtime.requests[0]["modelId"], "vendor.some-model-v2:0")

    def test_json_mode_asks_for_json_only(self):
        runtime = FakeRuntime()
        client(runtime).chat("cheap", "s", "List the queries.", json_mode=True)
        text = runtime.requests[0]["messages"][0]["content"][0]["text"]
        self.assertTrue(text.startswith("List the queries."))
        self.assertIn("JSON object only", text)

    def test_blank_model_id_is_refused_before_any_call(self):
        runtime = FakeRuntime()
        with self.assertRaisesRegex(BedrockError, r"bedrock\.models\.blank\.id is empty"):
            client(runtime).chat("blank", "s", "u")
        self.assertEqual(runtime.requests, [])

    def test_empty_reply_is_an_error(self):
        runtime = FakeRuntime(events=[{"messageStop": {"stopReason": "content_filtered"}}])
        with self.assertRaisesRegex(BedrockError, "no text.*content_filtered"):
            client(runtime).chat("cheap", "s", "u")

    def test_reply_cut_off_at_the_output_limit_warns(self):
        runtime = FakeRuntime(events=[{"contentBlockDelta": {"delta": {"text": "Half a dra"}}},
                                      {"messageStop": {"stopReason": "max_tokens"}}])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(client(runtime).chat("cheap", "s", "u"), "Half a dra")
        self.assertIn("cut short", err.getvalue())

    def test_aws_errors_become_model_errors_with_a_hint(self):
        from botocore.exceptions import ClientError, NoCredentialsError
        denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no access"}}, "ConverseStream")
        with self.assertRaisesRegex(ModelError, "Request access"):
            client(FakeRuntime(error=denied)).chat("cheap", "s", "u")
        with self.assertRaisesRegex(ModelError, "aws sso login"):
            client(FakeRuntime(error=NoCredentialsError())).chat("cheap", "s", "u")
        # Seen on the first real call, 2026-10-06. Not a problem with the request, despite the code.
        blocked = ClientError({"Error": {"Code": "ValidationException", "Message":
                               "Error 002: Access to Bedrock models is not allowed for this account"}}, "ConverseStream")
        with self.assertRaisesRegex(ModelError, "AWS Support") as raised:
            client(FakeRuntime(error=blocked)).chat("cheap", "s", "u")
        self.assertNotIn("temperature", str(raised.exception))

    def test_a_bug_in_a_callback_is_not_mistaken_for_an_aws_error(self):
        def broken(piece):
            raise ZeroDivisionError

        with self.assertRaises(ZeroDivisionError):
            client(FakeRuntime()).chat("cheap", "s", "u", on_token=broken)

    def test_retries_are_bounded(self):
        # Every attempt that reaches a model is billed, so retrying forever
        # is a way to run up a bill.
        self.assertLessEqual(MAX_ATTEMPTS, 5)
        real = BedrockClient("us-west-2", MODELS, 16000, 0.4)._client()  # builds a client; calls nothing
        self.assertEqual(real.meta.config.retries["total_max_attempts"], MAX_ATTEMPTS)
        self.assertEqual(real.meta.region_name, "us-west-2")


class ProviderConfigTest(unittest.TestCase):
    def test_laptop_and_server_stay_on_ollama(self):
        for name in ("config.toml", "config.server.toml"):
            with self.subTest(config=name):
                cfg = load_config(ROOT / name)
                self.assertEqual(cfg.provider, "ollama")
                self.assertIsInstance(make_client(cfg), OllamaClient)

    def test_bedrock_config(self):
        cfg = load_config(ROOT / "config.bedrock.toml")
        self.assertEqual((cfg.provider, cfg.bedrock_region), ("bedrock", "us-west-2"))
        self.assertIn(cfg.plaintiff_model, cfg.bedrock_models)
        self.assertIn(cfg.defendant_model, cfg.bedrock_models)
        for name, model in cfg.bedrock_models.items():
            with self.subTest(model=name):
                self.assertGreater(model.input_price, 0)
                self.assertGreater(model.output_price, 0)
        self.assertFalse(cfg.bedrock_models["claude-sonnet-5"].temperature)
        self.assertIsInstance(make_client(cfg), BedrockClient)

    def test_bedrock_web_ui_is_local_and_keeps_its_own_data(self):
        from battle_test.config import load_plans, load_web_config
        web, local = load_web_config(ROOT / "config.bedrock.toml"), load_web_config(ROOT / "config.toml")
        self.assertIsNone(web.public_serving_problem())
        self.assertEqual(web.host, "127.0.0.1")
        self.assertNotEqual(web.port, local.port)
        self.assertNotEqual(web.data_dir, local.data_dir)  # two servers on one queue would run cases twice
        self.assertEqual(web.data_dir.parent.name, "cases")  # gitignored
        self.assertIn("unlimited", load_plans(ROOT / "config.bedrock.toml").by_name)
        # Several cases at once on Bedrock, with a place left when one user is at their cap.
        self.assertGreater(web.workers, web.max_active_cases)
        self.assertEqual(local.workers, 1)
        self.assertEqual(load_web_config(ROOT / "config.server.toml").workers, 1)

    def test_workers_must_be_a_positive_number_and_one_on_ollama(self):
        from battle_test.config import load_web_config
        bedrock = (ROOT / "config.bedrock.toml").read_text(encoding="utf-8")
        ollama = (ROOT / "config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            for text, message in [(bedrock.replace("workers = 4", "workers = 0"), "1 or more"),
                                  (bedrock.replace("workers = 4", "workers = true"), "1 or more"),
                                  (ollama.replace("[web]", "[web]\nworkers = 2"), "one GPU")]:
                path.write_text(text, encoding="utf-8")
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    load_web_config(path)
            path.write_text(ollama.replace("[web]", "[web]\nworkers = 1"), encoding="utf-8")
            self.assertEqual(load_web_config(path).workers, 1)

    def test_web_app_starts_on_the_bedrock_config_without_calling_aws(self):
        try:
            from battle_test.web.app import create_app
        except ImportError:
            self.skipTest("web dependencies not installed")
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(ROOT / "config.bedrock.toml", data_dir=Path(tmp))
            app.state.store.close()
            app.state.auth.close()

    def test_no_credentials_in_the_bedrock_config(self):
        text = (ROOT / "config.bedrock.toml").read_text(encoding="utf-8").lower()
        for marker in ("akia", "secret_access_key", "aws_session_token", "access_key_id"):
            self.assertNotIn(marker, text)

    def _load(self, models: str, extra: str = ""):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text(f'[models]\n{models}\nplaintiff = "m"\ndefendant = "m"\n[generation]\ntemperature = 0.1\n'
                            f'[pipeline]\nrounds = 1\noutput_dir = "o"\n{extra}', encoding="utf-8")
            return load_config(path)

    def test_each_provider_needs_its_own_section(self):
        with self.assertRaisesRegex(ValueError, "bedrock.region"):
            self._load('provider = "bedrock"')
        with self.assertRaisesRegex(ValueError, "ollama.url"):
            self._load("")
        with self.assertRaisesRegex(ValueError, "models.provider"):
            self._load('provider = "openai"')
        self.assertEqual(self._load('provider = "bedrock"', '[bedrock]\nregion = "us-east-1"').max_tokens, 16000)

    def test_both_clients_raise_the_shared_error(self):
        self.assertTrue(issubclass(OllamaError, ModelError))
        self.assertTrue(issubclass(BedrockError, ModelError))


class CostTest(unittest.TestCase):
    CFG = dataclasses.replace(load_config(ROOT / "config.toml"), bedrock_models=MODELS)

    def test_cost_from_tokens_and_prices(self):
        usage = {"cheap": {"calls": 8, "input_tokens": 39_000, "output_tokens": 7_000}}
        self.assertAlmostEqual(self.CFG.cost(usage), 39_000 * 0.15 / 1e6 + 7_000 * 1.20 / 1e6)
        both = {**usage, "claude": {"calls": 1, "input_tokens": 1_000_000, "output_tokens": 0}}
        self.assertAlmostEqual(self.CFG.cost(both), self.CFG.cost(usage) + 2.0)

    def test_unknown_price_gives_no_estimate_rather_than_a_low_one(self):
        self.assertIsNone(self.CFG.cost({"unpriced": {"calls": 1, "input_tokens": 5, "output_tokens": 5}}))
        self.assertIsNone(self.CFG.cost({"qwen2.5:7b": {"calls": 1, "input_tokens": 5, "output_tokens": 5}}))
        self.assertIsNone(self.CFG.cost({}))


class JsonReplyTest(unittest.TestCase):
    def test_json_wrapped_in_a_fence_or_a_sentence_is_still_read(self):
        for reply in ('{"queries": ["venue"]}',
                      '```json\n{"queries": ["venue"]}\n```',
                      'Here are the queries:\n{"queries": ["venue"]}\nLet me know if you need more.'):
            with self.subTest(reply=reply):
                self.assertEqual(grounding.parse_json_list(reply, "queries"), ["venue"])

    def test_unusable_replies_give_nothing(self):
        for reply in ("", "no json here", "{broken", '["a list"]', '{"queries": "not a list"}'):
            with self.subTest(reply=reply):
                self.assertEqual(grounding.parse_json_list(reply, "queries"), [])


if __name__ == "__main__":
    unittest.main()
