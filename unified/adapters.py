"""Domain adapters registered with the capability broker (manual 3.4).

Each adapter declares real capability limits: missing providers / empty
allowlists produce explicit typed errors rather than fabricated data.
Tool names follow the domain.noun.verb namespace convention.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .broker import CapabilityBroker, ToolSpec
from .evidence import EvidenceStore, sha256_bytes


MAX_FETCH_BYTES = 2 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Outbound fetches fail on redirects so allowlists cannot be bypassed."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class AdapterContext:
    evidence: EvidenceStore
    workspace_root: str
    web_allowlist: tuple = ()          # empty -> web fetch unavailable
    fixture_dir: str | None = None      # offline "captured pages" stand-in
    graph_db: str | None = None


def register_adapters(broker: CapabilityBroker, ctx: AdapterContext) -> None:
    ev, ws = ctx.evidence, ctx.workspace_root

    def workspace_for(args):
        return os.path.realpath(broker.workspace_for_project(args.get("_project_id")))

    def workspace_path(args, rel="."):
        root = workspace_for(args)
        path = os.path.realpath(os.path.join(root, rel))
        try:
            if os.path.commonpath((root, path)) != root:
                raise PermissionError("path escapes workspace")
        except ValueError as exc:
            raise PermissionError("path escapes workspace") from exc
        return path

    # ---- scrapling.fetch (manual 2.2) --------------------------------
    def scrapling_fetch(args):
        url = args.get("url", "")
        cancel_check = args.get("_cancelled") or (lambda: False)
        deadline_at = args.get("_deadline_at")
        if cancel_check():
            return {"result_type": "cancelled.v1", "summary": "fetch cancelled before dispatch",
                    "errors": [{"type": "cancelled"}]}
        if deadline_at is not None and time.time() >= deadline_at:
            return {"result_type": "error.budget.v1", "summary": "task time budget exceeded",
                    "errors": [{"type": "budget_exceeded", "budget": "time_seconds"}]}
        try:
            parsed = urllib.parse.urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except (TypeError, ValueError):
            host, port, parsed = "", None, None
        if (not parsed or parsed.scheme.lower() != "https" or not host
                or parsed.username or parsed.password or port not in (None, 443)):
            return {"result_type": "error.policy.v1", "summary": "only credential-free HTTPS URLs on port 443 are allowed",
                    "errors": [{"type": "policy_denied"}]}
        if not any(host == a or host.endswith("." + a) for a in ctx.web_allowlist):
            return {"result_type": "error.capability.v1",
                    "summary": f"host not in web allowlist: {host or '(none)'}",
                    "errors": [{"type": "capability_unavailable",
                                "detail": "web.read.public allowlist is empty or excludes host"}]}
        try:
            addresses = {ipaddress.ip_address(info[4][0].split("%", 1)[0])
                         for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            if not addresses or any(not ip.is_global for ip in addresses):
                raise PermissionError("host resolves to a non-public address")
        except (OSError, ValueError) as exc:
            return {"result_type": "error.policy.v1", "summary": "host resolution failed or was not public",
                    "errors": [{"type": "policy_denied", "detail": type(exc).__name__}]}
        try:
            if cancel_check():
                return {"result_type": "cancelled.v1", "summary": "fetch cancelled",
                        "errors": [{"type": "cancelled"}]}
            req = urllib.request.Request(url, headers={"User-Agent": "arena-gateway/0.1"})
            timeout = min(30.0, max(1.0, float(args.get("timeout_s", 10))))
            if deadline_at is not None:
                timeout = min(timeout, max(0.1, deadline_at - time.time()))
            opener = urllib.request.build_opener(_NoRedirectHandler())
            chunks = []
            total_bytes = 0
            with opener.open(req, timeout=timeout) as resp:
                final_url = resp.geturl()
                while total_bytes <= MAX_FETCH_BYTES:
                    if cancel_check():
                        return {"result_type": "cancelled.v1", "summary": "fetch cancelled",
                                "errors": [{"type": "cancelled"}]}
                    if deadline_at is not None:
                        remaining = deadline_at - time.time()
                        if remaining <= 0:
                            return {"result_type": "error.budget.v1",
                                    "summary": "task time budget exceeded",
                                    "errors": [{"type": "budget_exceeded",
                                                "budget": "time_seconds"}]}
                        sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
                        if sock is not None:
                            sock.settimeout(min(30.0, remaining))
                    chunk = resp.read(min(64 * 1024, MAX_FETCH_BYTES + 1 - total_bytes))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_bytes += len(chunk)
            body = b"".join(chunks)
            if deadline_at is not None and time.time() >= deadline_at:
                return {"result_type": "error.budget.v1", "summary": "task time budget exceeded",
                        "errors": [{"type": "budget_exceeded", "budget": "time_seconds"}]}
            if len(body) > MAX_FETCH_BYTES:
                return {"result_type": "error.resource.v1", "summary": "web response exceeds 2 MiB limit",
                        "errors": [{"type": "resource_limit", "limit_bytes": MAX_FETCH_BYTES}]}
        except Exception as exc:
            if cancel_check():
                return {"result_type": "cancelled.v1", "summary": "fetch cancelled",
                        "errors": [{"type": "cancelled"}]}
            if deadline_at is not None and time.time() >= deadline_at:
                return {"result_type": "error.budget.v1", "summary": "task time budget exceeded",
                        "errors": [{"type": "budget_exceeded", "budget": "time_seconds"}]}
            return {"result_type": "error.fetch.v1", "summary": "outbound fetch failed",
                    "errors": [{"type": "fetch_error", "detail": type(exc).__name__}]}
        art = ev.put_artifact(args.get("_task_id", "adhoc"), args.get("_step_id", "adhoc"),
                              "page.html", body)
        text = body.decode("utf-8", "replace")
        selector = (args.get("extract") or {}).get("selector")
        excerpt = text[:2000]
        return {"result_type": "web.extract.v1",
                "summary": f"captured {len(body)} bytes from {final_url}",
                "data": {"final_url": final_url, "title": _title(text),
                         "excerpt": excerpt, "selector": selector},
                "artifacts": [art],
                "evidence": [ev.evidence_ref(url, f"http:get:{final_url}")]}

    def fixture_fetch(args):
        name = args.get("name", "")
        if not ctx.fixture_dir or not name:
            return {"result_type": "error.capability.v1",
                    "summary": "no fixture dir configured",
                    "errors": [{"type": "capability_unavailable"}]}
        if name != os.path.basename(name) or name in (".", ".."):
            return {"result_type": "error.policy.v1", "summary": "path traversal rejected",
                    "errors": [{"type": "policy_denied"}]}
        path = os.path.join(ctx.fixture_dir, name)
        if not os.path.abspath(path).startswith(os.path.abspath(ctx.fixture_dir)):
            return {"result_type": "error.policy.v1", "summary": "path traversal rejected",
                    "errors": [{"type": "policy_denied"}]}
        with open(path, "rb") as f:
            body = f.read()
        art = ev.put_artifact(args.get("_task_id", "adhoc"), args.get("_step_id", "adhoc"),
                              name, body)
        return {"result_type": "web.extract.v1",
                "summary": f"loaded fixture {name} ({len(body)} bytes)",
                "data": {"final_url": f"fixture://{name}", "title": _title(body.decode('utf-8','replace')),
                         "excerpt": body.decode("utf-8", "replace")[:2000]},
                "artifacts": [art],
                "evidence": [ev.evidence_ref(f"fixture://{name}", "bytes:0-" + str(len(body)))]}

    # ---- openbb.query (manual 2.3) ------------------------------------
    def openbb_query(args):
        # Typed path via dataset artifact when provider extensions are absent:
        # schema-validated, units/metadata preserved, never fabricated.
        uri = args.get("dataset_uri")
        if uri:
            body = ev.get_artifact(uri)
            if body is None:
                return {"result_type": "error.v1", "summary": "dataset artifact not found",
                        "errors": [{"type": "not_found", "uri": uri}]}
            try:
                rows = json.loads(body.decode())
            except Exception as e:
                return {"result_type": "error.v1", "summary": f"dataset parse failed: {e}",
                        "errors": [{"type": "schema", "detail": str(e)}]}
            if not isinstance(rows, list) or (rows and not isinstance(rows[0], dict)):
                return {"result_type": "error.v1", "summary": "dataset must be a JSON array of objects",
                        "errors": [{"type": "schema"}]}
            cols = sorted({k for r in rows for k in r})
            return {"result_type": "data.series.v1",
                    "summary": f"typed dataset: {len(rows)} rows x {len(cols)} cols",
                    "data": {"rows": len(rows), "columns": cols,
                             "sample": rows[:5],
                             "unit": args.get("unit"), "provider": args.get("provider", "artifact"),
                             "sha256": sha256_bytes(body)},
                    "evidence": [ev.evidence_ref(uri, f"sha256:{sha256_bytes(body)}")]}
        # No provider extensions installed: explicit capability error.
        return {"result_type": "error.capability.v1",
                "summary": "OpenBB provider extensions not installed in this deployment",
                "data": {"command": args.get("command"), "provider": args.get("provider")},
                "errors": [{"type": "capability_unavailable",
                            "detail": "install provider extension or supply dataset_uri artifact"}]}

    # ---- codegraph (manual 2.6) ---------------------------------------
    def _graph_conn(args=None):
        project_ws = workspace_for(args or {})
        db = (os.path.join(project_ws, ".codegraph.db") if broker.project_isolation
              else ctx.graph_db or os.path.join(project_ws, ".codegraph.db"))
        os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS symbols (name TEXT, kind TEXT, file TEXT, line INTEGER, \"commit\" TEXT)")
        return conn

    def codegraph_index(args):
        root = workspace_path(args, args.get("root", "."))
        if not root.startswith(os.path.abspath(ws)):
            return {"result_type": "error.policy.v1", "summary": "root escapes workspace",
                    "errors": [{"type": "policy_denied"}]}
        conn = _graph_conn(args)
        n = 0
        with conn:
            conn.execute("DELETE FROM symbols")
            for dirpath, _, files in os.walk(root):
                for fn in files:
                    if not fn.endswith(".py"):
                        continue
                    p = os.path.join(dirpath, fn)
                    rel = os.path.relpath(p, root)
                    try:
                        safe_path = workspace_path(
                            args, os.path.relpath(p, workspace_for(args)))
                        with open(safe_path, encoding="utf-8", errors="replace") as f:
                            for i, line in enumerate(f, 1):
                                m = re.match(r"\s*(?:def|class)\s+(\w+)", line)
                                if m:
                                    conn.execute("INSERT INTO symbols VALUES(?,?,?,?,?)",
                                                 (m.group(1), "def" if "def" in line else "class",
                                                  rel, i, args.get("commit", "unknown")))
                                    n += 1
                    except OSError:
                        pass
        return {"result_type": "codegraph.index.v1", "summary": f"indexed {n} symbols",
                "data": {"symbols": n, "root": args.get("root", "."), "commit": args.get("commit", "unknown")}}

    def codegraph_find(args):
        conn = _graph_conn(args)
        rows = conn.execute("SELECT name,kind,file,line,\"commit\" FROM symbols WHERE name LIKE ?",
                            (f"%{args.get('query','')}%",)).fetchall()
        return {"result_type": "codegraph.find.v1",
                "summary": f"{len(rows)} matches",
                "data": {"matches": [{"name": r[0], "kind": r[1], "file": r[2],
                                      "line": r[3], "commit": r[4]} for r in rows[:50]]}}

    def codegraph_impact(args):
        """Impact tracing (manual 2.6): which indexed files import/reference
        the given symbol — a bounded reverse-dependency query."""
        sym = args.get("symbol", "")
        conn = _graph_conn(args)
        rows = conn.execute(
            'SELECT file, line FROM symbols WHERE "commit" = (SELECT MAX("commit") FROM symbols)').fetchall()
        impacted = []
        for file, _line in rows:
            try:
                p = workspace_path(args, file)
                with open(p, encoding="utf-8", errors="replace") as f:
                    if sym and sym in f.read():
                        impacted.append(file)
            except OSError:
                pass
        return {"result_type": "codegraph.impact.v1",
                "summary": f"{len(impacted)} files reference '{sym}'",
                "data": {"symbol": sym, "impacted_files": sorted(set(impacted))[:50]}}

    def codegraph_freshness(args):
        """Freshness/staleness report (manual 5.10 test 6): index commit vs
        requested commit — never invent current-line attribution."""
        conn = _graph_conn(args)
        rows = conn.execute('SELECT DISTINCT "commit" FROM symbols').fetchall()
        commits = [r[0] for r in rows] or ["none"]
        requested = args.get("commit", "unknown")
        stale = requested not in commits
        return {"result_type": "codegraph.freshness.v1",
                "summary": f"index commits: {commits}; requested: {requested}; stale={stale}",
                "data": {"index_commits": commits, "requested_commit": requested,
                         "stale": stale}}

    def codegraph_trace(args):
        """Transitive upstream callers of a symbol via the import graph
        (cycle-safe DFS; research: upstream = 'who is affected?')."""
        sym = args.get("symbol", "")
        conn = _graph_conn(args)
        rows = conn.execute("SELECT name,file FROM symbols").fetchall()
        files_by_sym: dict[str, set] = {}
        for name, file in rows:
            files_by_sym.setdefault(name, set()).add(file)
        # module name -> file, and file -> imported module names; scan ALL
        # workspace .py files so import edges exist even without symbols
        imports: dict[str, set] = {}
        all_files = []
        for dirpath, _, files in os.walk(workspace_for(args)):
            for fn in files:
                if fn.endswith(".py"):
                    all_files.append(os.path.relpath(os.path.join(dirpath, fn), workspace_for(args)))
        for file in all_files:
            try:
                p = workspace_path(args, file)
                with open(p, encoding="utf-8", errors="replace") as f:
                    src = f.read()
                mods = {a or b for a, b in
                        re.findall(r"(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", src)}
                imports[file] = {m.split(".")[0] for m in mods}
            except OSError:
                imports[file] = set()
        # BFS over reverse import edges from files defining sym (cycle-safe)
        targets = files_by_sym.get(sym, set())
        target_mods = {file[:-3] for file in targets}
        affected, seen, stack = set(), set(targets), list(targets)
        while stack:
            cur = stack.pop()
            for f, imps in imports.items():
                if f in seen:
                    continue
                if imps & target_mods or f in targets:
                    seen.add(f)
                    affected.add(f)
                    target_mods.add(f[:-3])
                    stack.append(f)
        return {"result_type": "codegraph.trace.v1",
                "summary": f"{len(affected)} transitive dependents of '{sym}'",
                "data": {"symbol": sym, "defining_files": sorted(targets),
                         "transitive_callers": sorted(affected)[:50]}}

    def codegraph_service_links(args):
        """Service links from infra files (manual 2.6 infra indexing):
        parses docker-compose.yml depends_on/links into directed edges."""
        root = workspace_path(args, args.get("root", "."))
        if not root.startswith(os.path.abspath(ws)):
            return {"result_type": "error.policy.v1", "summary": "root escapes workspace",
                    "errors": [{"type": "policy_denied"}]}
        edges, services = [], []
        for dirpath, _, files in os.walk(root):
            for fn in files:
                if fn not in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
                    continue
                p = os.path.join(dirpath, fn)
                p = workspace_path(args, os.path.relpath(p, workspace_for(args)))
                with open(p, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                # minimal YAML scan: services: block + depends_on entries
                in_services = False
                svc = None
                for line in lines:
                    if line.startswith("services:"):
                        in_services = True
                        continue
                    if in_services and line and not line.startswith(" "):
                        in_services = False
                    if not in_services:
                        continue
                    m = re.match(r"^  ([\w-]+):\s*$", line)
                    if m:
                        svc = m.group(1)
                        services.append(svc)
                    elif svc and re.match(r"^    depends_on:\s*$", line):
                        pass
                    elif svc and re.match(r"^      - ([\w-]+)\s*$", line):
                        edges.append((svc, re.match(r"^      - ([\w-]+)\s*$", line).group(1)))
                    elif svc and re.match(r"^      ([\w-]+):", line):
                        edges.append((svc, re.match(r"^      ([\w-]+):", line).group(1)))
        return {"result_type": "codegraph.service_links.v1",
                "summary": f"{len(services)} services, {len(edges)} dependency edges",
                "data": {"services": sorted(set(services)),
                         "edges": [{"from": a, "to": b} for a, b in edges]}}

    # ---- rea.inspect (manual 2.7, static only) ------------------------
    MAGIC = {b"\x7fELF": "ELF", b"MZ": "PE", b"\xfe\xed\xfa\xce": "Mach-O",
             b"\xcf\xfa\xed\xfe": "Mach-O-64", b"PK\x03\x04": "zip/jar/apk"}

    def rea_inspect(args):
        target = args.get("path", "")
        path = workspace_path(args, target)
        if not path.startswith(os.path.abspath(ws)) or not os.path.isfile(path):
            return {"result_type": "error.policy.v1", "summary": "target outside workspace or missing",
                    "errors": [{"type": "policy_denied", "detail": target}]}
        with open(path, "rb") as f:
            head = f.read(4096)
            f.seek(0)
            digest = hashlib.sha256(f.read()).hexdigest()
        fmt = next((v for k, v in MAGIC.items() if head.startswith(k)), "unknown")
        if os.path.getsize(path) <= 1 << 20:
            with open(path, "rb") as f:
                blob = head + f.read()
        else:
            blob = head
        strings = re.findall(rb"[ -~]{6,}", blob)
        # IOC extraction from strings (static only; sample is never executed)
        text = blob.decode("latin-1")
        iocs = {
            "urls": re.findall(r"https?://[^\s\"'<>]+", text)[:10],
            "domains": sorted(set(re.findall(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", text)))[:10],
            "ips": sorted(set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)))[:10],
        }
        # Shannon entropy of the header region (packed/encrypted indicator)
        import math
        from collections import Counter
        counts = Counter(head)
        entropy = -sum((c / len(head)) * math.log2(c / len(head)) for c in counts.values()) if head else 0
        return {"result_type": "rea.inspect.v1",
                "summary": f"static inspection of {target}: format={fmt}, entropy={entropy:.2f}",
                "data": {"sha256": digest, "format": fmt, "size": os.path.getsize(path),
                         "entropy": round(entropy, 3), "iocs": iocs,
                         "strings_sample": [s.decode() for s in strings[:20]],
                         "mode": "static-only"},
                "evidence": [ev.evidence_ref(f"file://{path}", f"sha256:{digest}")]}

    broker.register(ToolSpec("scrapling.fetch", ["web.read.public"], "read_only",
                             scrapling_fetch, "Allowlist-guarded web page capture.",
                             external_request=True, supports_cancellation=True))
    broker.register(ToolSpec("scrapling.fetch_fixture", ["web.read.public"], "read_only",
                             fixture_fetch, "Offline captured-page fixture (demo)."))
    broker.register(ToolSpec("openbb.query", ["data.market.read"], "read_only",
                             openbb_query, "Typed market/economic data (requires provider extensions)."))
    broker.register(ToolSpec("codegraph.index", ["repo.read"], "internal_write",
                             codegraph_index, "Rebuild the internal workspace symbol index.",
                             read_only=False, repeatable=True,
                             requires_approval=False))
    broker.register(ToolSpec("codegraph.find", ["repo.read"], "read_only",
                             codegraph_find, "Find symbols in the code graph."))
    broker.register(ToolSpec("codegraph.impact", ["repo.read"], "read_only",
                             codegraph_impact, "Impact trace: files referencing a symbol."))
    broker.register(ToolSpec("codegraph.freshness", ["repo.read"], "read_only",
                             codegraph_freshness, "Report index commit staleness."))
    broker.register(ToolSpec("codegraph.trace", ["repo.read"], "read_only",
                             codegraph_trace, "Transitive upstream callers of a symbol."))
    broker.register(ToolSpec("codegraph.service_links", ["repo.read"], "read_only",
                             codegraph_service_links, "Service dependency edges from compose files."))
    broker.register(ToolSpec("rea.inspect", ["binary.inspect.static"], "read_only",
                             rea_inspect, "Static binary/application triage (hash, format, strings)."))


def _title(text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return m.group(1).strip()[:200] if m else ""
