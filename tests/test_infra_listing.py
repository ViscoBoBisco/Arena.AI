"""service_links infra parsing + project-scoped task listing tests."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.adapters import AdapterContext, register_adapters
from unified.broker import CapabilityBroker
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.gateway import serve
from unified.ledger import Ledger
from unified.policy import PolicyEngine

GRANTS = ["repo.read", "web.read.public"]

COMPOSE = """services:
  gateway:
    build: .
    depends_on:
      - ledger
  ledger:
    image: postgres:15
"""


class InfraListingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ws = os.path.join(d, "ws")
        os.makedirs(self.ws)
        with open(os.path.join(self.ws, "docker-compose.yml"), "w") as f:
            f.write(COMPOSE)
        self.ledger = Ledger()
        self.evidence = EvidenceStore(os.path.join(d, "art"))
        self.broker = CapabilityBroker(self.evidence, workspace_root=self.ws)
        register_adapters(self.broker, AdapterContext(
            evidence=self.evidence, workspace_root=self.ws,
            graph_db=os.path.join(d, "g.db")))
        self.engine = Engine(self.ledger, self.broker, self.evidence)

    def tearDown(self):
        self.tmp.cleanup()

    def test_service_links_edges(self):
        pol = PolicyEngine(set(GRANTS))
        r = self.broker.call("T", "S", "codegraph.service_links", {"root": "."},
                             ["repo.read"], pol)
        self.assertEqual(r.status, "succeeded")
        self.assertIn("gateway", r.data["services"])
        self.assertIn({"from": "gateway", "to": "ledger"}, r.data["edges"])

    def test_task_listing_scoped_and_paginated(self):
        server = serve(self.engine, "127.0.0.1", 8126)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for i in range(3):
                out = self.engine.submit("proj-a", f"l-{i}", {"intent": "echo", "instructions": "x"},
                                         [], GRANTS)
            self.engine.submit("proj-b", "l-b", {"intent": "echo", "instructions": "x"}, [], GRANTS)
            def get(path, proj):
                r = urllib.request.Request(f"http://127.0.0.1:8126{path}",
                                           headers={"X-Project-Id": proj,
                                                    "Authorization": "Bearer dev-key"})
                return json.loads(urllib.request.urlopen(r).read())
            a = get("/v1/tasks?limit=2&offset=0", "proj-a")
            self.assertEqual(a["total"], 3)
            self.assertEqual(len(a["tasks"]), 2)
            a2 = get("/v1/tasks?limit=2&offset=2", "proj-a")
            self.assertEqual(len(a2["tasks"]), 1)
            b = get("/v1/tasks", "proj-b")
            self.assertEqual(b["total"], 1)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
