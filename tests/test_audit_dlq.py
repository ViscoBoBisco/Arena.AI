"""Tamper-evident hash-chained audit log + SSRF/DLQ webhook delivery tests."""
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.broker import CapabilityBroker
from unified.delivery import DeliveryService, check_callback_url
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.ledger import Ledger

GRANTS = ["repo.read"]


class AuditAndDlqTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        ws = os.path.join(d, "ws")
        os.makedirs(ws)
        ev = EvidenceStore(os.path.join(d, "a"))
        self.ledger = Ledger(os.path.join(d, "l.db"))
        self.engine = Engine(self.ledger, CapabilityBroker(ev, workspace_root=ws), ev)

    def tearDown(self):
        self.tmp.cleanup()

    def test_audit_hash_chain_intact_and_tamper_detected(self):
        self.engine.submit("p", "a-1", {"intent": "echo", "instructions": "hi"}, [], GRANTS)
        v = self.ledger.verify_audit_chain()
        self.assertTrue(v["intact"])
        self.assertGreaterEqual(v["checked"], 2)
        # Tamper with an old row directly in SQLite — chain check must catch it
        with self.ledger._db:
            self.ledger._db.execute("UPDATE events SET detail='{\"tampered\":true}' WHERE id=1")
        v2 = self.ledger.verify_audit_chain()
        self.assertFalse(v2["intact"])
        self.assertEqual(v2["broken_at_id"], 1)

    def test_ssrf_blocks_http_and_private_ips(self):
        self.assertFalse(check_callback_url("http://example.com/hook")[0])
        self.assertFalse(check_callback_url("https://127.0.0.1/hook")[0])
        self.assertFalse(check_callback_url("https://169.254.169.254/latest/meta-data/")[0])
        svc = DeliveryService("s", allow_private=False)
        out = svc.deliver("https://127.0.0.1/hook", {"x": 1})
        self.assertFalse(out["delivered"])
        self.assertTrue(out["dlq"])
        self.assertEqual(len(svc.dlq), 1)

    def test_4xx_routes_straight_to_dlq_without_retrying(self):
        calls = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(1)
                self.send_response(400)
                self.end_headers()
            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            svc = DeliveryService("s", allow_private=True, max_attempts=3)
            out = svc.deliver(f"http://127.0.0.1:{port}/bad", {"a": 1})
            self.assertFalse(out["delivered"])
            self.assertTrue(out["dlq"])
            self.assertEqual(len(calls), 1)  # 4xx is terminal: 1 attempt only
        finally:
            srv.shutdown()
            srv.server_close()

    def test_5xx_retries_then_moves_to_dlq(self):
        calls = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(1)
                self.send_response(503)
                self.end_headers()
            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            svc = DeliveryService("s", allow_private=True, max_attempts=3)
            out = svc.deliver(f"http://127.0.0.1:{port}/flaky", {"a": 1})
            self.assertFalse(out["delivered"])
            self.assertTrue(out["dlq"])
            self.assertEqual(len(calls), 3)  # retried all 3 attempts
            self.assertEqual(len(svc.dlq), 1)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
