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
        client(runtime).chat("claude", "s", "u", temperature=0.0)  # still none for a model that takes none
        self.assertEqual([r["inferenceConfig"] for r in runtime.requests], [{"maxTokens": 8000}] * 2)

    def test_a_call_can_set_its_own_temperature(self):
        runtime = FakeRuntime()
        client(runtime).chat("cheap", "s", "u", temperature=0.0)
        self.assertEqual(runtime.requests[0]["inferenceConfig"], {"maxTokens": 16000, "temperature": 0.0})

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
        # The checker is on Bedrock too, so all-Bedrock runs include both checks.
        self.assertIn(cfg.checker_model, cfg.bedrock_models)
        self.assertFalse(cfg.split_providers)
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

    def test_no_credentials_in_the_bedrock_configs(self):
        for name in ("config.bedrock.toml", "config.tunnel-bedrock.toml"):
            text = (ROOT / name).read_text(encoding="utf-8").lower()
            for marker in ("akia", "secret_access_key", "aws_session_token", "access_key_id"):
                with self.subTest(config=name, marker=marker):
                    self.assertNotIn(marker, text)

    def test_split_config_drafts_on_the_server_and_checks_on_bedrock(self):
        from battle_test.models import SplitClient
        cfg = load_config(ROOT / "config.tunnel-bedrock.toml")
        self.assertTrue(cfg.split_providers)
        self.assertEqual((cfg.provider, cfg.plaintiff_model, cfg.defendant_model), ("ollama", "qwen2.5:14b", "qwen2.5:14b"))
        self.assertEqual((cfg.checker_provider, cfg.provider_of(cfg.checker_model)), ("bedrock", "bedrock"))
        self.assertEqual((cfg.provider_of(cfg.plaintiff_model), cfg.checker_timeout_seconds), ("ollama", 600))
        self.assertIn(cfg.checker_model, cfg.bedrock_models)
        # The same models and prices as config.bedrock.toml.
        self.assertEqual(cfg.bedrock_models, load_config(ROOT / "config.bedrock.toml").bedrock_models)
        client = make_client(cfg)  # builds no AWS session until the first call
        self.assertIsInstance(client, SplitClient)
        self.assertIsInstance(client.drafting, OllamaClient)
        self.assertIsInstance(client.checker, BedrockClient)
        self.assertEqual(client.checker.timeout_seconds, 600)
        # The tunnel config's checker stays on its own provider: no split.
        self.assertFalse(load_config(ROOT / "config.tunnel.toml").split_providers)

    def test_split_client_sends_each_call_by_model_name(self):
        from battle_test.models import SplitClient

        class Fake:
            def __init__(self, name):
                self.name, self.calls = name, []

            def chat(self, model, system, user, **kwargs):
                self.calls.append((model, kwargs))
                return self.name

        drafting, checker = Fake("drafting"), Fake("checker")
        client = SplitClient(drafting, checker, "big")
        self.assertEqual(client.chat("small", "s", "u", json_mode=True), "drafting")
        self.assertEqual(client.chat("big", "s", "u", json_mode=True, on_usage=None), "checker")
        self.assertEqual(checker.calls, [("big", {"json_mode": True, "on_usage": None})])

    def test_split_providers_are_checked(self):
        bedrock = '[bedrock]\nregion = "us-west-2"\n[bedrock.models.big]\nid = "x"\n'
        ollama = '[ollama]\nurl = "http://o"\n'
        for models, extra, message in [
            ('checker_provider = "bedrock"', ollama + bedrock, "no models.checker"),
            ('checker_provider = "bedrock"\nchecker = "m"', ollama + bedrock, "can't also be a drafting model"),
            ('checker_provider = "bedrock"\nchecker = "other"', ollama + bedrock, r"\[bedrock.models.other\]"),
            ('checker_provider = "bedrock"\nchecker = "big"', ollama, "bedrock.region"),
            ('checker_provider = "openai"\nchecker = "big"', ollama, "checker_provider"),
            ('provider = "bedrock"\nchecker_provider = "ollama"\nchecker = "local"', bedrock, "ollama.url"),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self._load(models, extra)
        cfg = self._load('checker_provider = "bedrock"\nchecker = "big"', ollama + bedrock)
        self.assertTrue(cfg.split_providers)
        # Naming the drafting provider is the same as leaving it out.
        same = self._load('checker_provider = "ollama"\nchecker = "m"', ollama)
        self.assertFalse(same.split_providers)

    def test_checker_temperature_defaults_to_zero(self):
        ollama = '[ollama]\nurl = "http://o"\n'
        self.assertEqual(self._load('checker = "m"', ollama).checker_temperature, 0.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text('[models]\nplaintiff = "m"\ndefendant = "m"\nchecker = "m"\n'
                            '[generation]\ntemperature = 0.4\nchecker_temperature = 0.2\n'
                            '[pipeline]\nrounds = 1\noutput_dir = "o"\n' + ollama, encoding="utf-8")
            self.assertEqual(load_config(path).checker_temperature, 0.2)

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

    def test_split_providers_count_only_the_priced_checker(self):
        cfg = dataclasses.replace(self.CFG, checker_model="cheap", checker_provider="bedrock")
        usage = {"qwen2.5:7b": {"calls": 30, "input_tokens": 100_000, "output_tokens": 9_000},
                 "cheap": {"calls": 20, "input_tokens": 40_000, "output_tokens": 3_000}}
        self.assertAlmostEqual(cfg.cost(usage), 40_000 * 0.15 / 1e6 + 3_000 * 1.20 / 1e6)
        # The Ollama drafting model alone (say the checker never ran): nothing to estimate.
        self.assertIsNone(cfg.cost({"qwen2.5:7b": usage["qwen2.5:7b"]}))
        # A Bedrock checker with no price still gives no estimate rather than a low one.
        unpriced = dataclasses.replace(cfg, checker_model="unpriced")
        self.assertIsNone(unpriced.cost({**usage, "unpriced": usage["cheap"]}))


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
