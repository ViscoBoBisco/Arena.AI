"""Unified gateway (manual 3.1) — the only front door for AI tasks.

HTTP API using stdlib only, exposing the recommended endpoints:
  POST /v1/tasks
  GET  /v1/tasks/{id}
  GET  /v1/tasks/{id}/events
  POST /v1/tasks/{id}/approve
  POST /v1/tasks/{id}/cancel
  POST /v1/tasks/{id}/reconcile
  GET  /v1/tasks/{id}/artifacts
  GET  /v1/tools
Binds to 0.0.0.0 for the sandbox preview proxy.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .engine import Engine
from .ledger import LedgerError
from .policy import KNOWN_GRANTS
from .ratelimit import TokenBucket

# Development operator grants. Production tokens should carry the smallest
# grant set needed by their service identity.
OPERATOR_GRANTS = [
    "web.read.public", "data.market.read", "repo.read",
    "workspace.write.sandbox", "binary.inspect.static", "model.infer",
    "task.approve", "task.cancel", "task.reconcile", "ops.read",
    "memory.read", "memory.write",
]


def _setting(name: str) -> str:
    """Read a setting directly or from an orchestrator-mounted secret file."""
    value = os.environ.get(name, "")
    path = os.environ.get(name + "_FILE", "")
    if value and path:
        raise RuntimeError(f"configure only one of {name} and {name}_FILE")
    if not path:
        return value.strip()
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        raise RuntimeError(f"cannot read configured {name}_FILE") from exc


def _load_api_keys() -> dict[str, dict[str, set[str]]]:
    """Load token -> {projects, grants}; explicit keys default to no grants."""
    raw = _setting("UNIFIED_API_KEYS")
    if not raw:
        if os.environ.get("UNIFIED_ENV", "").strip().lower() in {"prod", "production"}:
            return {}
        return {"dev-key": {"projects": {"*"}, "grants": set(OPERATOR_GRANTS)}}
    try:
        config = json.loads(raw)
        if not isinstance(config, dict):
            return {}
        keys = {}
        for token, profile in config.items():
            if not isinstance(token, str) or not token:
                continue
            # Legacy shorthand is still accepted for tenant identity, but
            # intentionally grants no tools unless grants are declared.
            if isinstance(profile, dict):
                projects = profile.get("projects", [])
                grants = profile.get("grants", [])
            else:
                projects, grants = profile, []
            if isinstance(projects, str):
                projects = [projects]
            if isinstance(grants, str):
                grants = [grants]
            if not (isinstance(projects, list) and projects
                    and all(isinstance(p, str) and p for p in projects)):
                continue
            if not (isinstance(grants, list)
                    and all(isinstance(g, str) and g in KNOWN_GRANTS for g in grants)):
                continue
            keys[token] = {"projects": set(projects), "grants": set(grants)}
        return keys
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


API_KEYS = _load_api_keys()


def validate_production_configuration() -> None:
    """Fail startup rather than expose the development wildcard in production."""
    if os.environ.get("UNIFIED_ENV", "").strip().lower() not in {"prod", "production"}:
        return
    callback_secret = _setting("CALLBACK_SECRET")
    if len(callback_secret) < 32 or any(marker in callback_secret.lower()
                                        for marker in ("change-me", "replace", "dev-secret", "test")):
        raise RuntimeError("production requires CALLBACK_SECRET to be a non-placeholder secret of at least 32 characters")
    raw = _setting("UNIFIED_API_KEYS")
    if not raw or not API_KEYS:
        raise RuntimeError("production requires at least one explicit UNIFIED_API_KEYS profile")
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("UNIFIED_API_KEYS must be valid JSON in production") from exc
    if not isinstance(configured, dict) or len(API_KEYS) != len(configured):
        raise RuntimeError("every production API key must have a valid project and grant profile")
    for token, profile in API_KEYS.items():
        if (len(token) < 32 or any(marker in token.lower()
                                   for marker in ("replace", "change-me", "dev-key", "test"))):
            raise RuntimeError("production API keys must be non-placeholder tokens of at least 32 characters")
        if "*" in profile["projects"]:
            raise RuntimeError("wildcard project scope is forbidden in production API-key profiles")


MAX_BODY_BYTES = 1_048_576
_RATE_LIMITS: dict[str, TokenBucket] = {}
_RATE_LOCK = threading.Lock()


def _rate_limit_for(key: str) -> TokenBucket:
    with _RATE_LOCK:
        return _RATE_LIMITS.setdefault(key, TokenBucket(capacity=120, refill_per_sec=25.0))


class RequestBodyError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def make_handler(engine: Engine):
    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "UnifiedGateway/0.1"

        def version_string(self):
            return self.server_version

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
            super().end_headers()

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        def _raw_body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                raise RequestBodyError(400, "invalid Content-Length")
            if length < 0:
                raise RequestBodyError(400, "invalid Content-Length")
            if length > MAX_BODY_BYTES:
                raise RequestBodyError(413, "request body exceeds 1 MiB limit")
            return self.rfile.read(length) if length else b""

        def _body(self) -> dict:
            raw = self._raw_body()
            if not raw:
                return {}
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise RequestBodyError(400, "request body must be valid JSON")
            if not isinstance(body, dict):
                raise RequestBodyError(400, "JSON request body must be an object")
            return body

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
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                last_event_id = min(2**63 - 1, max(0, int(self.headers.get("Last-Event-ID", "0") or "0")))
            except ValueError:
                last_event_id = 0
            try:
                while True:
                    events = engine.get_events(task_id, after_id=last_event_id, limit=500)
                    for ev in events:
                        last_event_id = ev["id"]
                        payload = json.dumps(ev)
                        self.wfile.write(f"id: {last_event_id}\nevent: task\ndata: {payload}\n\n".encode())
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
            profile = API_KEYS.get(key)
            if profile is None:
                return None, self._send(403, {"error": "invalid api key"})
            ok, retry = _rate_limit_for(key).allow()
            if not ok:
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", str(retry))
                self.end_headers()
                self.wfile.write(json.dumps({"error": "rate limited"}).encode())
                return None, True
            self._allowed = set(profile["projects"])
            self._caller_grants = set(profile["grants"])
            return True, None

        def _caller_project(self) -> str:
            """Tenant requested by the caller; checked against the bearer key."""
            return self.headers.get("X-Project-Id", "default").strip()

        def _authorize_project(self) -> bool:
            project = self._caller_project()
            if not project or len(project) > 128:
                self._send(400, {"error": "X-Project-Id must be 1–128 characters"})
                return False
            allowed = getattr(self, "_allowed", set())
            if "*" not in allowed and project not in allowed:
                self._send(403, {"error": "API key is not authorized for this project"})
                return False
            return True

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
                if path.startswith("/v1/") and not self._authorize_project():
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
            if path == "/v1/session":
                return self._send(200, {
                    "project_id": self._caller_project(),
                    "projects": sorted(self._allowed),
                    "grants": sorted(self._caller_grants),
                })
            if path == "/v1/tools":
                return self._send(200, {"tools": engine.broker.list_tools()})
            if path == "/v1/roles":
                return self._send(200, {"roles": engine.roles.list() if engine.roles else []})
            if path == "/v1/workspace":
                import hashlib
                import os
                from urllib.parse import parse_qs
                if "repo.read" not in self._caller_grants:
                    return self._send(403, {"error": "API key lacks repo.read grant"})
                ws = engine.broker.workspace_for_project(self._caller_project())
                q = parse_qs(urlparse(self.path).query)
                rel_req = q.get("path", [None])[0]
                if rel_req:
                    try:
                        full = engine.broker._sandbox_path(rel_req, self._caller_project())
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
                ok = True
                try:
                    engine.ledger.ping()
                    if not engine.evidence.is_ready():
                        ok = False
                except Exception:
                    ok = False
                return self._send(200 if ok else 503, {"status": "ok" if ok else "not ready"})
            if path == "/v1/metrics":
                return self._send(200, engine.metrics_for_project(self._caller_project()))
            if path == "/v1/audit/verify":
                if "ops.read" not in self._caller_grants:
                    return self._send(403, {"error": "API key lacks ops.read grant"})
                return self._send(200, engine.ledger.verify_audit_chain())
            if path == "/v1/metrics/summary":
                m = engine.metrics_for_project(self._caller_project())
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
                if "memory.read" not in self._caller_grants:
                    return self._send(403, {"error": "API key lacks memory.read grant"})
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
                try:
                    limit = max(1, min(int(q.get("limit", ["50"])[0]), 100))
                    offset = min(2**63 - 1, max(0, int(q.get("offset", ["0"])[0])))
                except ValueError:
                    return self._send(400, {"error": "limit and offset must be integers"})
                proj = self._caller_project()
                page = engine.ledger.list_tasks(project_id=proj, limit=limit, offset=offset)
                total = engine.ledger.count_tasks(project_id=proj)
                return self._send(200, {"tasks": page, "total": total,
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
                from urllib.parse import parse_qs
                q = parse_qs(urlparse(self.path).query)
                try:
                    after_id = min(2**63 - 1, max(0, int(q.get("after_id", ["0"])[0])))
                    limit = max(1, min(int(q.get("limit", ["100"])[0]), 500))
                except ValueError:
                    return self._send(400, {"error": "after_id and limit must be integers"})
                events = engine.get_events(parts[2], after_id=after_id, limit=limit)
                return self._send(200, {"events": events,
                                        "next_after_id": events[-1]["id"] if events else after_id,
                                        "limit": limit})
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
            try:
                self._do_POST()
            except RequestBodyError as e:
                self._send(e.status, {"error": e.message})

        def _do_POST(self):
            path = urlparse(self.path).path
            parts = [p for p in path.split("/") if p]
            proj, err = self._auth()
            if err:
                return
            if not self._authorize_project():
                return
            if path == "/v1/tasks":
                b = self._body()
                if not isinstance(b.get("idempotency_key"), str) or not 1 <= len(b["idempotency_key"]) <= 200:
                    return self._send(400, {"error": "idempotency_key must be a 1–200 character string"})
                if not isinstance(b.get("request", {}), dict):
                    return self._send(400, {"error": "request must be a JSON object"})
                if not isinstance(b.get("permissions", []), list) or not all(
                        isinstance(p, str) for p in b.get("permissions", [])):
                    return self._send(400, {"error": "permissions must be a list of strings"})
                if not isinstance(b.get("inputs", []), list):
                    return self._send(400, {"error": "inputs must be a JSON array"})
                if b.get("budgets") is not None and not isinstance(b["budgets"], dict):
                    return self._send(400, {"error": "budgets must be a JSON object"})
                try:
                    out = engine.submit(
                        project_id=self._caller_project(),
                        idempotency_key=b["idempotency_key"],
                        request=b.get("request", {}),
                        permissions=b.get("permissions", []),
                        grants=sorted(self._caller_grants),
                        inputs=b.get("inputs", []),
                        budgets=b.get("budgets"))
                except KeyError as e:
                    return self._send(400, {"error": f"missing field: {e}"})
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                if out.get("conflict"):
                    return self._send(409, out)
                if out.get("state") == "REJECTED":
                    return self._send(403, out)
                code = 200 if out.get("duplicate") else 202  # async accepted
                if out.get("state") != "REJECTED" and not out.get("duplicate"):
                    if "sync=1" in urlparse(self.path).query:
                        out["result"] = engine.run(out["task_id"], sorted(self._caller_grants))
                        code = 201
                    else:
                        engine.submit_async(out["task_id"], sorted(self._caller_grants))
                        out["status_url"] = f"/v1/tasks/{out['task_id']}"
                return self._send(code, out)
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] in ("approve", "reject"):
                task, scope_err = self._task_for_caller(parts[2])
                if scope_err:
                    return scope_err
                if "task.approve" not in self._caller_grants:
                    return self._send(403, {"error": "API key lacks task.approve grant"})
                decision = self._body().get("decision", "approved") if parts[3] == "approve" else "rejected"
                if decision not in ("approved", "rejected"):
                    return self._send(400, {"error": "decision must be approved or rejected"})
                return self._send(200, engine.approve(parts[2], decision, sorted(self._caller_grants)))
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "reconcile":
                task, scope_err = self._task_for_caller(parts[2])
                if scope_err:
                    return scope_err
                if "task.reconcile" not in self._caller_grants:
                    return self._send(403, {"error": "API key lacks task.reconcile grant"})
                body = self._body()
                attempt_id = body.get("attempt_id")
                outcome = body.get("outcome")
                receipt = body.get("receipt")
                if not isinstance(attempt_id, str) or not attempt_id:
                    return self._send(400, {"error": "attempt_id is required"})
                if outcome not in {"applied", "not_applied"}:
                    return self._send(400, {"error": "outcome must be applied or not_applied"})
                if receipt is not None and not isinstance(receipt, dict):
                    return self._send(400, {"error": "receipt must be an object"})
                try:
                    result = engine.reconcile_attempt(
                        parts[2], attempt_id, outcome, receipt,
                        sorted(self._caller_grants))
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                except LedgerError as exc:
                    return self._send(409, {"error": str(exc)})
                return self._send(200, {"task_id": parts[2],
                                        "attempt_id": attempt_id,
                                        "outcome": outcome, "result": result,
                                        "state": result.get("state")})
            if len(parts) == 3 and parts[:2] == ["v1", "memory"]:
                if parts[2] != self._caller_project():
                    return self._send(403, {"error": "cross-project access denied"})
                if "memory.write" not in self._caller_grants:
                    return self._send(403, {"error": "API key lacks memory.write grant"})
                b = self._body()
                engine.memory.put(parts[2], b["key"], b.get("value"))
                return self._send(200, {"stored": True})
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "cancel":
                task, scope_err = self._task_for_caller(parts[2])
                if scope_err:
                    return scope_err
                if "task.cancel" not in self._caller_grants:
                    return self._send(403, {"error": "API key lacks task.cancel grant"})
                requested = engine.cancel(parts[2])
                current = engine.get_task(parts[2])
                return self._send(200 if requested else 409, {
                    "task_id": parts[2], "cancel_requested": requested,
                    "state": current["state"] if current else "UNKNOWN"})
            if path == "/v1/automation/trigger":
                b = self._body()
                out = engine.automation.trigger(b.get("workflow_id", ""),
                                                b.get("execution_id", ""),
                                                b.get("request", {}),
                                                b.get("permissions", []),
                                                project_id=self._caller_project(),
                                                grants=sorted(self._caller_grants))
                if out.get("conflict"):
                    return self._send(409, out)
                if out.get("state") == "REJECTED":
                    return self._send(403, out)
                if not out.get("duplicate"):
                    out["result"] = engine.run(out["task_id"], sorted(self._caller_grants))
                return self._send(200, out)
            if path == "/v1/automation/callbacks":
                raw = self._raw_body()
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
