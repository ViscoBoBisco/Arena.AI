"""Protocol-level tests for the real HTTP inference adapters.

These run against a loopback stub that speaks the OpenAI-compatible and Ollama
wire formats. They verify request/response parsing, usage and cost sourcing,
and error redaction. They are NOT live provider qualification: no real model
or billing endpoint is exercised.
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.model_router import OllamaProvider, OpenAICompatibleProvider


class _Stub(BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_GET(self):
        _Stub.seen.append({"path": self.path, "body": None,
                           "auth": self.headers.get("Authorization")})
        self._reply(*_Stub.routes.get(self.path, (404, {"error": "no route"})))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        _Stub.seen.append({"path": self.path, "body": body,
                           "auth": self.headers.get("Authorization")})
        self._reply(*_Stub.routes.get(self.path, (404, {"error": "no route"})))

    def _reply(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ProviderWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Stub)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Stub.routes = {}
        _Stub.seen = []

    def test_openai_compatible_sends_key_and_reports_provider_usage(self):
        _Stub.routes["/v1/chat/completions"] = (200, {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        provider = OpenAICompatibleProvider(self.base + "/v1", "m", "tok-123")
        out = provider.complete("ping", max_output_tokens=16)
        self.assertEqual(out["completion"], "hi")
        self.assertEqual(out["usage"]["usage_source"], "provider")
        self.assertEqual(out["usage"]["model_tokens"], 15)
        self.assertNotIn("cost_usd", out["usage"])
        self.assertEqual(_Stub.seen[0]["auth"], "Bearer tok-123")
        self.assertEqual(_Stub.seen[0]["body"]["max_tokens"], 16)

    def test_openai_compatible_rate_card_is_marked_as_estimate(self):
        _Stub.routes["/v1/chat/completions"] = (200, {
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}})
        provider = OpenAICompatibleProvider(self.base + "/v1", "m", "",
                                            True, 2.0, 8.0)
        out = provider.complete("p", max_output_tokens=4)
        self.assertEqual(out["usage"]["cost_source"], "configured_rate_card")
        self.assertAlmostEqual(out["usage"]["cost_usd"], 2.0)
        self.assertEqual(_Stub.seen[0]["auth"], None)

    def test_provider_error_is_redacted(self):
        _Stub.routes["/v1/chat/completions"] = (500, {"error": "secret echo tok-123"})
        provider = OpenAICompatibleProvider(self.base + "/v1", "m", "tok-123")
        with self.assertRaises(RuntimeError) as ctx:
            provider.complete("p", max_output_tokens=4)
        self.assertEqual(str(ctx.exception), "inference provider returned HTTP 500")
        self.assertNotIn("tok-123", str(ctx.exception))

    def test_ollama_reports_counts_when_present(self):
        _Stub.routes["/api/chat"] = (200, {
            "message": {"role": "assistant", "content": "yo"},
            "prompt_eval_count": 7, "eval_count": 3})
        out = OllamaProvider(self.base, "llama3.2").complete("p", max_output_tokens=8)
        self.assertEqual(out["completion"], "yo")
        self.assertEqual(out["usage"]["usage_source"], "provider")
        self.assertEqual(out["usage"]["model_tokens"], 10)
        self.assertFalse(out["usage"].get("token_consumption_uncertain", False))
        self.assertFalse(_Stub.seen[0]["body"]["stream"])
        self.assertEqual(_Stub.seen[0]["body"]["options"]["num_predict"], 8)

    def test_ollama_missing_counts_are_unavailable_not_zero_provider_usage(self):
        _Stub.routes["/api/chat"] = (200, {"message": {"content": "yo"}})
        out = OllamaProvider(self.base, "llama3.2").complete("p", max_output_tokens=8)
        self.assertEqual(out["usage"]["usage_source"], "unavailable")
        self.assertTrue(out["usage"]["token_consumption_uncertain"])


class TransportGuardTests(unittest.TestCase):
    def _make(self, url, key="", extra=None):
        from unittest.mock import patch
        from unified.model_router import provider_from_environment
        env = {"CONFLUX_MODEL_PROVIDER": "openai_compatible",
               "CONFLUX_MODEL_BASE_URL": url, "CONFLUX_MODEL_NAME": "m",
               "CONFLUX_MODEL_API_KEY": key, "UNIFIED_ENV": ""}
        env.update(extra or {})
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CONFLUX_MODEL_API_KEY_FILE", None)
            return provider_from_environment()

    def test_https_with_key_is_allowed(self):
        self.assertIsInstance(self._make("https://api.example.com/v1", "k"),
                              OpenAICompatibleProvider)

    def test_loopback_http_with_key_is_allowed(self):
        self.assertIsInstance(self._make("http://127.0.0.1:9/v1", "k"),
                              OpenAICompatibleProvider)

    def test_key_over_remote_plain_http_is_refused(self):
        secret = "sekrit-token-value"
        with self.assertRaises(RuntimeError) as ctx:
            self._make("http://api.example.com/v1", secret)
        self.assertNotIn(secret, str(ctx.exception))

    def test_remote_plain_http_without_key_is_refused_in_production(self):
        with self.assertRaises(RuntimeError):
            self._make("http://internal.example/v1", "",
                       {"UNIFIED_ENV": "production"})

    def test_remote_plain_http_without_key_is_allowed_outside_production(self):
        self.assertIsInstance(self._make("http://internal.example/v1", ""),
                              OpenAICompatibleProvider)

    def test_non_http_scheme_is_refused(self):
        with self.assertRaises(RuntimeError):
            self._make("file:///etc/passwd", "")


class OllamaDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Stub)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Stub.routes = {}
        _Stub.seen = []

    def test_untagged_model_matches_latest_tag(self):
        _Stub.routes["/api/tags"] = (200, {"models": [
            {"name": "llama3.2:latest", "model": "llama3.2:latest"}]})
        self.assertTrue(OllamaProvider(self.base, "llama3.2").has_model())

    def test_explicit_tag_must_match_exactly(self):
        _Stub.routes["/api/tags"] = (200, {"models": [{"name": "phi4-mini:3.8b"}]})
        self.assertTrue(OllamaProvider(self.base, "phi4-mini:3.8b").has_model())
        self.assertFalse(OllamaProvider(self.base, "phi4-mini:latest").has_model())

    def test_discovery_accepts_chat_url_base(self):
        _Stub.routes["/api/tags"] = (200, {"models": []})
        provider = OllamaProvider(self.base + "/api/chat", "llama3.2")
        self.assertEqual(provider.list_models(), [])
        self.assertEqual(_Stub.seen[-1]["path"], "/api/tags")

    def test_discovery_error_is_redacted(self):
        _Stub.routes["/api/tags"] = (500, {"error": "leak"})
        with self.assertRaises(RuntimeError) as ctx:
            OllamaProvider(self.base, "llama3.2").has_model()
        self.assertEqual(str(ctx.exception), "inference provider returned HTTP 500")


if __name__ == "__main__":
    unittest.main()
