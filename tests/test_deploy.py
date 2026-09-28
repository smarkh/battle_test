import asyncio
import importlib
import os
import tempfile
import time
import unittest
from pathlib import Path

from battle_test import config as config_module
from battle_test.config import WebConfig, load_config, load_corpus_config, load_web_config

ROOT = Path(__file__).resolve().parent.parent

try:
    from fastapi.testclient import TestClient

    from battle_test.web import app as app_module
    from battle_test.web.app import create_app
    from battle_test.web.demo import DemoClient
except ImportError:  # web dependencies are optional
    TestClient = None


def web(host="127.0.0.1", secure=False, proxy=False):
    return WebConfig(host, 8000, Path("x"), secure, 90, proxy)


class PublicServingRuleTest(unittest.TestCase):
    def test_localhost_is_always_allowed(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                self.assertIsNone(web(host).public_serving_problem())

    def test_other_hosts_need_proxy_and_secure_cookies(self):
        self.assertIsNone(web("0.0.0.0", secure=True, proxy=True).public_serving_problem())
        for secure, proxy, missing in [(False, False, "behind_proxy = true and secure_cookies = true"),
                                       (True, False, "behind_proxy = true"),
                                       (False, True, "secure_cookies = true")]:
            with self.subTest(secure=secure, proxy=proxy):
                self.assertIn(missing, web("0.0.0.0", secure, proxy).public_serving_problem())

    def test_laptop_config_stays_local(self):
        w = load_web_config(ROOT / "config.toml")
        self.assertEqual(w.host, "127.0.0.1")
        self.assertFalse(w.behind_proxy)
        self.assertIsNone(w.public_serving_problem())


class ServerConfigTest(unittest.TestCase):
    PATH = ROOT / "config.server.toml"

    def test_server_config_is_complete_and_allowed_to_serve(self):
        w = load_web_config(self.PATH)
        self.assertEqual((w.host, w.secure_cookies, w.behind_proxy), ("0.0.0.0", True, True))
        self.assertIsNone(w.public_serving_problem())
        self.assertEqual(w.data_dir, Path("/data/cases"))
        self.assertEqual(w.retention_days, 90)

    def test_server_uses_host_ollama_14b_and_the_volumes(self):
        cfg = load_config(self.PATH)
        self.assertEqual(cfg.ollama_url, "http://host.docker.internal:11434")
        self.assertEqual((cfg.plaintiff_model, cfg.defendant_model), ("qwen2.5:14b", "qwen2.5:14b"))
        self.assertTrue(str(cfg.output_dir).replace("\\", "/").startswith("/data/cases"))
        self.assertEqual(load_corpus_config(self.PATH).db_path, Path("/data/law/law.sqlite"))

    def test_server_and_laptop_share_the_same_law_snapshot(self):
        self.assertEqual(load_corpus_config(self.PATH).snapshot,
                         load_corpus_config(ROOT / "config.toml").snapshot)


class ConfigOverrideTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("BATTLE_TEST_CONFIG", None)
        importlib.reload(config_module)

    def test_env_var_selects_the_config_file(self):
        os.environ["BATTLE_TEST_CONFIG"] = str(ROOT / "config.server.toml")
        importlib.reload(config_module)
        self.assertEqual(config_module.DEFAULT_CONFIG_PATH, ROOT / "config.server.toml")

    def test_default_is_the_laptop_config(self):
        os.environ.pop("BATTLE_TEST_CONFIG", None)
        importlib.reload(config_module)
        self.assertEqual(config_module.DEFAULT_CONFIG_PATH, ROOT / "config.toml")


class DockerContextTest(unittest.TestCase):
    def test_image_never_contains_data_or_cases(self):
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
        for path in ("data/", "cases/", "output/", ".venv/", ".git/", ".env"):
            with self.subTest(path=path):
                self.assertIn(path, ignored)

    def test_image_runs_as_non_root_and_uses_server_config(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("USER battle", dockerfile)
        self.assertIn("BATTLE_TEST_CONFIG=/app/config.server.toml", dockerfile)

    def active_compose_lines(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        return [line for line in compose.splitlines() if not line.strip().startswith("#")]

    def test_compose_hardening_for_both_services(self):
        active = "\n".join(self.active_compose_lines())
        self.assertIn("host.docker.internal:host-gateway", active)
        for setting in ("cap_drop:", "- ALL", "no-new-privileges:true", "read_only: true",
                        "mem_limit:", "restart: unless-stopped"):
            with self.subTest(setting=setting):
                self.assertEqual(active.count(setting), 2, f"{setting!r} should appear once per service")
        # No port published on the host (private testing uses a separate override file).
        self.assertFalse(any(line.strip() == "ports:" for line in self.active_compose_lines()))

    def test_compose_is_independent_of_smark_iq(self):
        active = "\n".join(self.active_compose_lines())
        for coupling in ("smarkiq", "smark_iq", "external:", "caddy"):
            with self.subTest(coupling=coupling):
                self.assertNotIn(coupling, active)
        self.assertIn("cloudflared:", active)
        self.assertIn("container_name: battle-test-cloudflared", active)

    def test_tunnel_token_only_from_env(self):
        active = "\n".join(self.active_compose_lines())
        self.assertIn("TUNNEL_TOKEN: ${BATTLE_TUNNEL_TOKEN:-}", active)
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("BATTLE_TUNNEL_TOKEN=\n", example)  # placeholder, no value
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        self.assertIn(".env", gitignore)


class KeepAliveTest(unittest.TestCase):
    def test_server_unloads_model_soon_after_runs(self):
        self.assertEqual(load_config(ROOT / "config.server.toml").keep_alive, "30s")
        self.assertIsNone(load_config(ROOT / "config.toml").keep_alive)  # laptop: Ollama's default

    def test_keep_alive_is_sent_only_when_set(self):
        import json
        from unittest import mock

        from battle_test.ollama_client import OllamaClient

        sent = []

        class FakeResponse:
            def __enter__(self):
                return iter([json.dumps({"message": {"content": "ok"}, "done": True}).encode()])

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout):
            sent.append(json.loads(request.data))
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            OllamaClient("http://x", 5, 100, 0.0, keep_alive="30s").chat("m", "s", "u")
            OllamaClient("http://x", 5, 100, 0.0).chat("m", "s", "u")
        self.assertEqual(sent[0]["keep_alive"], "30s")
        self.assertNotIn("keep_alive", sent[1])


@unittest.skipIf(TestClient is None, "web dependencies not installed")
class HealthAndHeartbeatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(client=DemoClient(delay_per_word=0), model_label="demo",
                              data_dir=Path(self.tmp.name) / "cases")
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_healthz_needs_no_sign_in(self):
        response = self.client.get("/healthz")
        self.assertEqual((response.status_code, response.json()), (200, {"status": "ok"}))

    def test_no_hsts_on_the_laptop(self):
        self.assertNotIn("strict-transport-security", self.client.get("/healthz").headers)

    def test_hsts_when_deployed_behind_the_tunnel(self):
        server_like = Path(self.tmp.name) / "server-like.toml"
        text = (ROOT / "config.toml").read_text(encoding="utf-8").replace("behind_proxy = false",
                                                                         "behind_proxy = true")
        server_like.write_text(text, encoding="utf-8")
        app = create_app(server_like, client=DemoClient(delay_per_word=0),
                         data_dir=Path(self.tmp.name) / "cases2")
        with TestClient(app) as client:
            headers = client.get("/healthz").headers
        self.assertEqual(headers["strict-transport-security"], "max-age=31536000; includeSubDomains")

    def collect(self, job_id, until, limit=5.0):
        """Run the event stream directly until `until(chunk)` or `limit` seconds."""
        async def run():
            chunks = []
            stream = app_module.progress_events(self.app.state.worker, self.app.state.store, job_id,
                                                poll_seconds=0.02)
            started = time.monotonic()
            try:
                async for chunk in stream:
                    chunks.append(chunk)
                    if until(chunk) or time.monotonic() - started > limit:
                        break
            finally:
                await stream.aclose()
            return chunks
        return asyncio.run(run())

    def test_silent_run_gets_heartbeats(self):
        # A running case with nothing happening: without a heartbeat the
        # stream would be silent, and Cloudflare would drop it.
        job = self.app.state.store.create(1, "UT", "facts", "x", 1, "t")
        self.app.state.store.update(job.id, status="running")
        original = app_module.HEARTBEAT_SECONDS
        app_module.HEARTBEAT_SECONDS = 0.1
        try:
            chunks = self.collect(job.id, until=lambda c: c.startswith(": keep-alive"))
        finally:
            app_module.HEARTBEAT_SECONDS = original
            self.app.state.store.update(job.id, status="failed")
        self.assertEqual(chunks[-1], ": keep-alive\n\n")

    def test_no_heartbeat_while_events_flow(self):
        job = self.app.state.store.create(1, "UT", "facts", "x", 1, "t")  # queued: an event every poll
        original = app_module.HEARTBEAT_SECONDS
        app_module.HEARTBEAT_SECONDS = 0.1
        try:
            chunks = self.collect(job.id, until=lambda c: False, limit=0.5)
        finally:
            app_module.HEARTBEAT_SECONDS = original
            self.app.state.store.update(job.id, status="failed")
        self.assertTrue(chunks and all(c.startswith("event: queue") for c in chunks))

    def test_stream_ends_if_the_case_is_deleted(self):
        job = self.app.state.store.create(1, "UT", "facts", "x", 1, "t")
        self.app.state.store.update(job.id, status="running")
        self.app.state.store.delete(job.id)
        self.assertEqual(self.collect(job.id, until=lambda c: False, limit=2), [])


if __name__ == "__main__":
    unittest.main()
