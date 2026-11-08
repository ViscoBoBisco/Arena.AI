"""Health/readiness endpoint tests."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.broker import CapabilityBroker
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.gateway import serve
from unified.ledger import Ledger


class HealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        ws = os.path.join(d, "ws")
        os.makedirs(ws)
        cls.engine = Engine(Ledger(), CapabilityBroker(EvidenceStore(os.path.join(d, "a")),
                                                        workspace_root=ws),
                            EvidenceStore(os.path.join(d, "a")))
        cls.server = serve(cls.engine, "127.0.0.1", 8125)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _get(self, path):
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:8125{path}")
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_healthz(self):
        code, body = self._get("/healthz")
        self.assertEqual((code, body["status"]), (200, "ok"))

    def test_readyz_is_read_only(self):
        index = os.path.join(self.tmp.name, "a", "index.jsonl")
        def read_index():
            try:
                with open(index, "rb") as f:
                    return f.read()
            except FileNotFoundError:
                return None
        before = read_index()
        code, body = self._get("/readyz")
        after = read_index()
        self.assertEqual((code, body["status"]), (200, "ok"))
        self.assertEqual(after, before)

    def test_readyz_returns_503_when_ledger_is_unavailable(self):
        from unittest.mock import patch
        with patch.object(self.engine.ledger, "ping", side_effect=OSError("closed")):
            code, body = self._get("/readyz")
        self.assertEqual((code, body["status"]), (503, "not ready"))

    def test_responses_include_security_headers_without_python_version(self):
        with urllib.request.urlopen("http://127.0.0.1:8125/healthz") as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            self.assertNotIn("Python/", response.headers["Server"])


if __name__ == "__main__":
    unittest.main()
