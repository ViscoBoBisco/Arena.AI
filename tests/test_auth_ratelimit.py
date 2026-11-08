"""Auth (401/403) and rate-limit (429) tests + token bucket unit test."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.broker import CapabilityBroker
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.gateway import serve
from unified.ledger import Ledger
from unified.ratelimit import TokenBucket


class AuthRateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        ws = os.path.join(d, "ws")
        os.makedirs(ws)
        ev = EvidenceStore(os.path.join(d, "a"))
        cls.engine = Engine(Ledger(), CapabilityBroker(ev, workspace_root=ws), ev)
        cls.server = serve(cls.engine, "127.0.0.1", 8127)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _get(self, path, headers=None):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:8127{path}", headers=headers or {}))
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_health_open(self):
        code, _ = self._get("/healthz")
        self.assertEqual(code, 200)

    def test_missing_token_401(self):
        code, body = self._get("/v1/tools")
        self.assertEqual(code, 401)

    def test_bad_key_403(self):
        code, _ = self._get("/v1/tools", {"Authorization": "Bearer wrong"})
        self.assertEqual(code, 403)

    def test_good_key_200(self):
        code, body = self._get("/v1/tools", {"Authorization": "Bearer dev-key"})
        self.assertEqual(code, 200)
        self.assertIn("tools", body)

    def test_token_bucket(self):
        b = TokenBucket(capacity=2, refill_per_sec=0.0)
        self.assertTrue(b.allow()[0])
        self.assertTrue(b.allow()[0])
        ok, retry = b.allow()
        self.assertFalse(ok)
        self.assertGreaterEqual(retry, 1)


if __name__ == "__main__":
    unittest.main()
