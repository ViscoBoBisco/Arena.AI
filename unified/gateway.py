"""Unified gateway (manual 3.1) — the only front door for AI tasks.

HTTP API using stdlib only, exposing the recommended endpoints:
  POST /v1/tasks
  GET  /v1/tasks/{id}
  GET  /v1/tasks/{id}/events
  POST /v1/tasks/{id}/approve
  POST /v1/tasks/{id}/cancel
  GET  /v1/tasks/{id}/artifacts
  GET  /v1/tools
Binds to 0.0.0.0 for the sandbox preview proxy.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .engine import Engine

# Demo grant set for the built-in operator; a real deployment issues these
# per caller (manual 3.3).
OPERATOR_GRANTS = [
    "web.read.public", "data.market.read", "repo.read",
    "workspace.write.sandbox",
]


def make_handler(engine: Engine):
    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "UnifiedGateway/0.1"

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode())

        def log_message(self, *args):  # quiet
            pass

        def do_GET(self):
            path = urlparse(self.path).path
            parts = [p for p in path.split("/") if p]
            if path == "/v1/tools":
                return self._send(200, {"tools": engine.broker.list_tools()})
            if path == "/v1/roles":
                return self._send(200, {"roles": engine.roles.list() if engine.roles else []})
            if len(parts) == 3 and parts[:2] == ["v1", "tasks"]:
                task = engine.get_task(parts[2])
                if task is None:
                    return self._send(404, {"error": "unknown task"})
                return self._send(200, task)
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "events":
                return self._send(200, {"events": engine.get_events(parts[2])})
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "artifacts":
                task = engine.get_task(parts[2])
                uris = []
                if task and task.get("result"):
                    uris = [a["uri"] for a in task["result"].get("artifacts", [])]
                return self._send(200, {"artifacts": uris})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            parts = [p for p in path.split("/") if p]
            if path == "/v1/tasks":
                b = self._body()
                try:
                    out = engine.submit(
                        project_id=b.get("project_id", "default"),
                        idempotency_key=b["idempotency_key"],
                        request=b.get("request", {}),
                        permissions=b.get("permissions", []),
                        grants=OPERATOR_GRANTS,
                        inputs=b.get("inputs", []),
                        budgets=b.get("budgets"))
                except KeyError as e:
                    return self._send(400, {"error": f"missing field: {e}"})
                code = 200 if out.get("duplicate") else 201
                if out.get("state") != "REJECTED" and not out.get("duplicate"):
                    out["result"] = engine.run(out["task_id"], OPERATOR_GRANTS)
                return self._send(code, out)
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "approve":
                return self._send(200, engine.approve(parts[2]))
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "cancel":
                engine.cancel(parts[2])
                return self._send(200, {"task_id": parts[2], "cancel_requested": True})
            if path == "/v1/automation/trigger":
                b = self._body()
                out = engine.automation.trigger(b.get("workflow_id", ""),
                                                b.get("execution_id", ""),
                                                b.get("request", {}),
                                                b.get("permissions", []))
                if not out.get("duplicate") and out.get("state") != "REJECTED":
                    out["result"] = engine.run(out["task_id"], OPERATOR_GRANTS)
                return self._send(200, out)
            if path == "/v1/automation/callbacks":
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                out = engine.automation.callback(
                    raw,
                    self.headers.get("X-Signature-256", ""),
                    self.headers.get("X-Timestamp", ""))
                return self._send(200 if out.get("accepted") else 403, out)
            return self._send(404, {"error": "not found"})

    return GatewayHandler


def serve(engine: Engine, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(engine))
    return server
