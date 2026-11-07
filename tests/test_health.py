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

    def test_readyz(self):
        code, body = self._get("/readyz")
        self.assertEqual((code, body["status"]), (200, "ok"))


if __name__ == "__main__":
    unittest.main()
