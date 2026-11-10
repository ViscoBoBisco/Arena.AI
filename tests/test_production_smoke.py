"""Run the real gateway process in production mode and exercise the CI smoke path.

This mirrors the container smoke job in .github/workflows/ci.yml without Docker:
it starts ``python -m unified.server`` with UNIFIED_ENV=production, file-backed
secrets, and no inherited environment, then checks the same API contract. It
guards against startup regressions such as a production-only crash at boot.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ProductionSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.secrets_dir = os.path.join(root, "secrets")
        self.data_dir = os.path.join(root, "data")
        os.makedirs(self.secrets_dir)
        os.makedirs(self.data_dir)
        self.writer = secrets.token_urlsafe(48)
        self.reviewer = secrets.token_urlsafe(48)
        with open(os.path.join(self.secrets_dir, "callback_secret"), "w") as f:
            f.write(secrets.token_urlsafe(48) + "\n")
        profiles = {
            self.writer: {"projects": ["ci-project", "ci-other"], "grants": [
                "repo.read", "workspace.write.sandbox", "task.cancel",
                "ops.read", "memory.read", "memory.write"]},
            self.reviewer: {"projects": ["ci-project"], "grants": ["task.approve"]},
        }
        keys_path = os.path.join(self.secrets_dir, "api_keys.json")
        with open(keys_path, "w") as f:
            json.dump(profiles, f)
        self.port = _free_port()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": REPO,
            "PYTHONDONTWRITEBYTECODE": "1",
            "UNIFIED_ENV": "production",
            "CALLBACK_SECRET_FILE": os.path.join(self.secrets_dir, "callback_secret"),
            "UNIFIED_API_KEYS_FILE": keys_path,
            "UNIFIED_WEB_ALLOWLIST": "[]",
        }
        self.log_path = os.path.join(root, "server.log")
        self.log = open(self.log_path, "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "unified.server", "--host", "127.0.0.1",
             "--port", str(self.port), "--data", self.data_dir],
            cwd=REPO, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.log.close()
        self.tmp.cleanup()

    def _request(self, path, project="ci-project", method="GET", body=None, token=None):
        headers = {"Authorization": "Bearer " + (token or self.writer),
                   "X-Project-Id": project}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return resp.status, dict(resp.headers), json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            raw = err.read()
            return err.code, dict(err.headers), json.loads(raw) if raw else {}

    def _wait_ready(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.fail("server exited during startup:\n" + open(self.log_path).read())
            try:
                status, _, _ = self._request("/readyz")
                if status == 200:
                    return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.2)
        self.fail("server did not become ready:\n" + open(self.log_path).read())

    def test_production_contract(self):
        self._wait_ready()
        status, headers, _ = self._request("/readyz")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

        status, _, session = self._request("/v1/session")
        self.assertEqual(status, 200)
        self.assertIn("workspace.write.sandbox", session["grants"])
        self.assertNotIn("task.approve", session["grants"])

        status, _, _ = self._request("/v1/memory/ci-project", method="POST",
                                     body={"key": "ci", "value": {"ok": True}})
        self.assertEqual(status, 200)

        status, _, task = self._request("/v1/tasks?sync=1", method="POST", body={
            "idempotency_key": "ci-production-smoke",
            "request": {"intent": "write_file", "path": "ci.txt", "content": "isolated"},
            "permissions": ["workspace.write.sandbox"]})
        self.assertEqual(status, 201)
        self.assertEqual(task["result"]["state"], "WAITING_APPROVAL")

        status, _, reviewer = self._request("/v1/session", token=self.reviewer)
        self.assertEqual(reviewer["grants"], ["task.approve"])
        status, _, result = self._request(
            f"/v1/tasks/{task['task_id']}/approve", method="POST",
            body={"decision": "approved"}, token=self.reviewer)
        self.assertEqual(status, 200)
        self.assertEqual(result["result"]["state"], "COMPLETED")

        status, _, preview = self._request("/v1/workspace?path=ci.txt")
        self.assertEqual(preview["preview"], "isolated")
        status, _, _ = self._request("/v1/workspace?path=ci.txt", project="ci-other")
        self.assertEqual(status, 404)

        status, _, metrics = self._request("/v1/metrics", project="ci-other")
        self.assertEqual(metrics["tasks_by_state"], {})

        status, _, audit = self._request("/v1/audit/verify")
        self.assertTrue(audit["intact"])

    def test_unauthenticated_requests_are_rejected(self):
        self._wait_ready()
        req = urllib.request.Request(self.base + "/v1/session")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
