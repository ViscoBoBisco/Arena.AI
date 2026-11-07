"""Operator console API contracts: workspace browsing, task inspection, and tenant scope."""
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.broker import CapabilityBroker
from unified.engine import Engine
from unified.evidence import EvidenceStore
from unified.gateway import serve
from unified.ledger import Ledger
from unified.automation import AutomationConnector
from unified.memory import ConversationMemory
from unified.role_registry import RoleRegistry


class ConsoleApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.workspace = os.path.join(cls.tmp.name, "workspace")
        os.makedirs(cls.workspace)
        with open(os.path.join(cls.workspace, "hello.txt"), "w") as f:
            f.write("console preview content")
        with open(os.path.join(cls.workspace, "large.txt"), "wb") as f:
            f.write(b"x" * 40000)
        evidence = EvidenceStore(os.path.join(cls.tmp.name, "artifacts"))
        cls.engine = Engine(
            Ledger(), CapabilityBroker(evidence, workspace_root=cls.workspace), evidence,
            roles=RoleRegistry(),
            memory=ConversationMemory(os.path.join(cls.tmp.name, "memory.db")),
        )
        cls.engine.automation = AutomationConnector(cls.engine, "test-secret")
        cls.server = serve(cls.engine, "127.0.0.1", 8131)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:8131"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, path, method="GET", project="console-test", body=None, auth=True):
        headers = {"X-Project-Id": project}
        if auth:
            headers["Authorization"] = "Bearer dev-key"
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as response:
                content = response.read()
                try:
                    payload = json.loads(content) if content else {}
                except json.JSONDecodeError:
                    payload = {"raw": content}
                return response.status, payload
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, json.loads(raw) if raw else {}

    def test_console_is_guided_and_exposes_all_planner_intents(self):
        with urllib.request.urlopen(self.base + "/") as response:
            page = response.read().decode()
        for phrase in ("Task composer", "Task queue", "Workspace", "Observability",
                       "Web research", "Repo change", "Binary triage", "DAG trace",
                       "service_links", "model_complete"):
            self.assertIn(phrase, page)
        self.assertNotIn("JSON.stringify({ project_id: 'ui'", page)

    def test_workspace_file_list_preview_and_traversal_rejection(self):
        code, listed = self.request("/v1/workspace")
        self.assertEqual(code, 200)
        self.assertIn("hello.txt", [f["path"] for f in listed["files"]])
        code, preview = self.request("/v1/workspace?path=hello.txt")
        self.assertEqual(code, 200)
        self.assertEqual(preview["preview"], "console preview content")
        self.assertEqual(len(preview["sha256"]), 64)
        code, large = self.request("/v1/workspace?path=large.txt")
        self.assertEqual(code, 200)
        self.assertEqual(large["sha256"], hashlib.sha256(b"x" * 40000).hexdigest())
        code, _ = self.request("/v1/workspace?path=../outside")
        self.assertEqual(code, 403)

    def test_task_inspection_includes_dag_receipts_and_tenant_scope(self):
        code, accepted = self.request("/v1/tasks?sync=1", method="POST", body={
            "idempotency_key": "console-read-1",
            "request": {"intent": "read_file", "path": "hello.txt"},
            "permissions": ["repo.read"],
        })
        self.assertEqual(code, 201)
        task_id = accepted["task_id"]
        code, task = self.request(f"/v1/tasks/{task_id}")
        self.assertEqual(code, 200)
        self.assertEqual(task["steps"][0]["tool"], "native.fs.read")
        self.assertEqual(task["receipts"][0]["status"], "succeeded")
        self.assertIn("pending_approvals", task)
        artifact_path = task["result"]["artifacts"][0]["uri"].replace("artifact://", "/v1/artifacts/")
        code, _ = self.request(artifact_path)
        self.assertEqual(code, 200)
        code, _ = self.request(artifact_path, project="another-project")
        self.assertEqual(code, 403)
        code, _ = self.request(f"/v1/tasks/{task_id}", project="another-project")
        self.assertEqual(code, 403)

    def test_automation_trigger_is_scoped_to_console_project(self):
        code, out = self.request("/v1/automation/trigger", method="POST", body={
            "workflow_id": "ui-workflow", "execution_id": "ui-run-1",
            "request": {"intent": "echo", "instructions": "triggered"},
            "permissions": [],
        })
        self.assertEqual(code, 200)
        task_id = out["task_id"]
        code, task = self.request(f"/v1/tasks/{task_id}")
        self.assertEqual(code, 200)
        self.assertEqual(task["project_id"], "console-test")
        code, _ = self.request(f"/v1/tasks/{task_id}", project="another-project")
        self.assertEqual(code, 403)

    def test_memory_scope_and_stream_auth(self):
        code, _ = self.request("/v1/memory/console-test", method="POST",
                               body={"key": "tone", "value": {"style": "concise"}})
        self.assertEqual(code, 200)
        code, memories = self.request("/v1/memory/console-test")
        self.assertEqual(memories["memories"][0]["value"], {"style": "concise"})
        code, _ = self.request("/v1/memory/console-test", project="another-project")
        self.assertEqual(code, 403)
        code, _ = self.request("/v1/tasks/not-a-task/events/stream", auth=False)
        self.assertEqual(code, 401)


if __name__ == "__main__":
    unittest.main()
