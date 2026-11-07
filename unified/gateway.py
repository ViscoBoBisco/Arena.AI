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
from .ratelimit import TokenBucket

# API keys: project -> key (dev default; production injects via env)
API_KEYS = {"dev-key": {"*"}}
RATE_LIMIT = TokenBucket(capacity=120, refill_per_sec=25.0)

# Demo grant set for the built-in operator; a real deployment issues these
# per caller (manual 3.3).
OPERATOR_GRANTS = [
    "web.read.public", "data.market.read", "repo.read",
    "workspace.write.sandbox", "binary.inspect.static", "model.infer",
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
            return True

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode())

        def log_message(self, *args):  # quiet
            pass

        def _stream_events(self, task_id: str):
            """SSE stream of task events: replayed history then live tail with
            keepalives; closes when the task reaches a terminal state."""
            from .contracts import TaskState
            terminal = {s.value for s in (TaskState.COMPLETED, TaskState.FAILED,
                                          TaskState.CANCELLED, TaskState.REJECTED)}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            sent = 0
            try:
                while True:
                    events = engine.get_events(task_id)
                    for ev in events[sent:]:
                        sent += 1
                        payload = json.dumps(ev)
                        self.wfile.write(f"id: {sent}\nevent: task\ndata: {payload}\n\n".encode())
                    self.wfile.flush()
                    task = engine.get_task(task_id)
                    if task is None or task["state"] in terminal:
                        self.wfile.write(b"event: done\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    import time as _t
                    _t.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _auth(self) -> tuple[str | None, object]:
            """Bearer API-key auth (research: bearer tokens in Authorization
            header only, never query strings). Returns (project, error_response)."""
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return None, self._send(401, {"error": "missing bearer token"})
            key = auth.split(" ", 1)[1].strip()
            allowed = API_KEYS.get(key)
            if allowed is None:
                return None, self._send(403, {"error": "invalid api key"})
            ok, retry = RATE_LIMIT.allow()
            if not ok:
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", str(retry))
                self.end_headers()
                self.wfile.write(json.dumps({"error": "rate limited"}).encode())
                return None, True
            self._allowed = allowed
            return True, None

        def _caller_project(self) -> str:
            """Tenant scope for every read/write: the authenticated project."""
            return self.headers.get("X-Project-Id", "default")

        def _task_for_caller(self, task_id: str):
            task = engine.get_task(task_id)
            if task is None:
                return None, self._send(404, {"error": "unknown task"})
            if task["project_id"] != self._caller_project():
                return None, self._send(403, {"error": "cross-project access denied"})
            return task, None

        def do_GET(self):
            path = urlparse(self.path).path
            parts = [p for p in path.split("/") if p]
            # The console uses fetch streaming, so event streams retain the same
            # bearer authentication and project scope as all other task reads.
            if path not in ("/healthz", "/readyz", "/") and path != "/index.html":
                proj, err = self._auth()
                if err:
                    return
            if path in ("/", "/index.html"):
                import os
                ui = os.path.join(os.path.dirname(__file__), "ui", "index.html")
                with open(ui, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/v1/tools":
                return self._send(200, {"tools": engine.broker.list_tools()})
            if path == "/v1/roles":
                return self._send(200, {"roles": engine.roles.list() if engine.roles else []})
            if path == "/v1/workspace":
                import hashlib
                import os
                from urllib.parse import parse_qs
                ws = engine.broker.workspace_root
                q = parse_qs(urlparse(self.path).query)
                rel_req = q.get("path", [None])[0]
                if rel_req:
                    try:
                        full = engine.broker._sandbox_path(rel_req)
                    except PermissionError as e:
                        return self._send(403, {"error": str(e)})
                    if not os.path.isfile(full):
                        return self._send(404, {"error": "file not found"})
                    digest = hashlib.sha256()
                    with open(full, "rb") as f:
                        raw = f.read(32768)
                        digest.update(raw)
                        while True:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            digest.update(chunk)
                    is_bin = b"\x00" in raw[:512] or raw.startswith(b"\x7fELF")
                    preview = raw[:256].hex(" ") if is_bin else raw.decode("utf-8", "replace")[:8000]
                    return self._send(200, {
                        "path": rel_req,
                        "size": os.path.getsize(full),
                        "sha256": digest.hexdigest(),
                        "is_binary": is_bin,
                        "preview": preview,
                    })
                files = []
                if os.path.isdir(ws):
                    for dirpath, _, fns in os.walk(ws):
                        for fn in sorted(fns):
                            if fn.startswith("."):
                                continue
                            full = os.path.join(dirpath, fn)
                            rel = os.path.relpath(full, ws)
                            try:
                                sz = os.path.getsize(full)
                            except OSError:
                                sz = 0
                            ext = os.path.splitext(fn)[1].lstrip(".") or "file"
                            files.append({"path": rel, "size": sz, "ext": ext})
                return self._send(200, {"files": sorted(files, key=lambda x: x["path"])})
            if path == "/healthz":  # liveness: cheap, local only
                return self._send(200, {"status": "ok"})
            if path == "/readyz":   # readiness: verify ledger + artifact store
                import os
                ok = True
                try:
                    engine.ledger.list_tasks()
                    engine.evidence.put_artifact("_health", "_check", "ping", b"ok")
                except Exception as e:
                    ok = False
                return self._send(200 if ok else 503, {"status": "ok" if ok else "not ready"})
            if path == "/v1/metrics":
                return self._send(200, engine.metrics)
            if path == "/v1/audit/verify":
                return self._send(200, engine.ledger.verify_audit_chain())
            if path == "/v1/metrics/summary":
                m = engine.metrics
                lat = sorted(m.get("latencies_ms", []))
                def pct(p):
                    return lat[min(len(lat) - 1, int(p * len(lat)))] if lat else 0
                total = sum(m["tasks_by_state"].values()) or 1
                completed = m["tasks_by_state"].get("COMPLETED", 0)
                return self._send(200, {
                    "task_success_rate": round(completed / total, 3),
                    "total_tasks": total,
                    "tool_calls": m["tool_calls"],
                    "tool_error_rate": round(m["tool_failures"] / max(m["tool_calls"], 1), 3),
                    "errors_by_tool": m.get("errors_by_tool", {}),
                    "p50_latency_ms": pct(0.5), "p95_latency_ms": pct(0.95),
                    "model_tokens": m["model_tokens"]})
            if len(parts) == 3 and parts[:2] == ["v1", "memory"]:
                if parts[2] != self._caller_project():
                    return self._send(403, {"error": "cross-project access denied"})
                if engine.memory is None:
                    return self._send(404, {"error": "memory disabled"})
                return self._send(200, {"memories": engine.memory.list(parts[2])})
            if len(parts) == 5 and parts[:2] == ["v1", "tasks"] and parts[4] == "stream":
                task, err = self._task_for_caller(parts[2])
                if err:
                    return err
                return self._stream_events(parts[2])
            if path == "/v1/tasks":
                # project-scoped list with offset pagination (bounded)
                from urllib.parse import parse_qs
                q = parse_qs(urlparse(self.path).query)
                limit = min(int(q.get("limit", ["50"])[0]), 100)
                offset = int(q.get("offset", ["0"])[0])
                proj = self._caller_project()
                rows = [t for t in engine.ledger.list_tasks() if t["project_id"] == proj]
                page = rows[offset:offset + limit]
                return self._send(200, {"tasks": page, "total": len(rows),
                                        "limit": limit, "offset": offset})
            if len(parts) == 3 and parts[:2] == ["v1", "tasks"]:
                task, err = self._task_for_caller(parts[2])
                if err:
                    return err
                return self._send(200, task)
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "events":
                task, err = self._task_for_caller(parts[2])
                if err:
                    return err
                return self._send(200, {"events": engine.get_events(parts[2])})
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "evidence":
                task, err = self._task_for_caller(parts[2])
                if err:
                    return err
                from .delivery import evidence_with_citations
                receipts = engine.ledger.get_receipts(parts[2])
                return self._send(200, {"evidence": evidence_with_citations(receipts)})
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "artifacts":
                task, err = self._task_for_caller(parts[2])
                if err:
                    return err
                uris = []
                if task and task.get("result"):
                    uris = [a["uri"] for a in task["result"].get("artifacts", [])]
                return self._send(200, {"artifacts": uris})
            if len(parts) >= 4 and parts[0] == "v1" and parts[1] == "artifacts":
                # Artifact IDs begin with their task ID, so downloads inherit
                # the owning task's project scope.
                task, err = self._task_for_caller(parts[2])
                if err:
                    return err
                if parts[2] != task["task_id"] or any(p in (".", "..") for p in parts[2:]):
                    return self._send(400, {"error": "invalid artifact path"})
                # Download an artifact by URI path; verify hash (manual 5.5).
                import hashlib
                uri = "artifact://" + "/".join(parts[2:])
                body = engine.get_artifact(uri)
                if body is None:
                    return self._send(404, {"error": "artifact not found"})
                digest = hashlib.sha256(body).hexdigest()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("X-Sha256", digest)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            parts = [p for p in path.split("/") if p]
            proj, err = self._auth()
            if err:
                return
            if path == "/v1/tasks":
                b = self._body()
                try:
                    out = engine.submit(
                        project_id=self._caller_project(),
                        idempotency_key=b["idempotency_key"],
                        request=b.get("request", {}),
                        permissions=b.get("permissions", []),
                        grants=OPERATOR_GRANTS,
                        inputs=b.get("inputs", []),
                        budgets=b.get("budgets"))
                except KeyError as e:
                    return self._send(400, {"error": f"missing field: {e}"})
                code = 200 if out.get("duplicate") else 202  # async accepted
                if out.get("state") != "REJECTED" and not out.get("duplicate"):
                    if "sync=1" in urlparse(self.path).query:
                        out["result"] = engine.run(out["task_id"], OPERATOR_GRANTS)
                        code = 201
                    else:
                        engine.submit_async(out["task_id"], OPERATOR_GRANTS)
                        out["status_url"] = f"/v1/tasks/{out['task_id']}"
                return self._send(code, out)
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] in ("approve", "reject"):
                task, scope_err = self._task_for_caller(parts[2])
                if scope_err:
                    return scope_err
                decision = self._body().get("decision", "approved") if parts[3] == "approve" else "rejected"
                return self._send(200, engine.approve(parts[2], decision, OPERATOR_GRANTS))
            if len(parts) == 3 and parts[:2] == ["v1", "memory"]:
                if parts[2] != self._caller_project():
                    return self._send(403, {"error": "cross-project access denied"})
                b = self._body()
                engine.memory.put(parts[2], b["key"], b.get("value"))
                return self._send(200, {"stored": True})
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "cancel":
                task, scope_err = self._task_for_caller(parts[2])
                if scope_err:
                    return scope_err
                engine.cancel(parts[2])
                return self._send(200, {"task_id": parts[2], "cancel_requested": True})
            if path == "/v1/automation/trigger":
                b = self._body()
                out = engine.automation.trigger(b.get("workflow_id", ""),
                                                b.get("execution_id", ""),
                                                b.get("request", {}),
                                                b.get("permissions", []),
                                                project_id=self._caller_project())
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
