"""Domain adapters registered with the capability broker (manual 3.4).

Each adapter declares real capability limits: missing providers / empty
allowlists produce explicit typed errors rather than fabricated data.
Tool names follow the domain.noun.verb namespace convention.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .broker import CapabilityBroker, ToolSpec
from .evidence import EvidenceStore, sha256_bytes


@dataclass
class AdapterContext:
    evidence: EvidenceStore
    workspace_root: str
    web_allowlist: tuple = ()          # empty -> web fetch unavailable
    fixture_dir: str | None = None      # offline "captured pages" stand-in
    graph_db: str | None = None


def register_adapters(broker: CapabilityBroker, ctx: AdapterContext) -> None:
    ev, ws = ctx.evidence, ctx.workspace_root

    # ---- scrapling.fetch (manual 2.2) --------------------------------
    def scrapling_fetch(args):
        url = args.get("url", "")
        host = urllib.parse.urlsplit(url).hostname or ""
        if not any(host == a or host.endswith("." + a) for a in ctx.web_allowlist):
            return {"result_type": "error.capability.v1",
                    "summary": f"host not in web allowlist: {host or '(none)'}",
                    "errors": [{"type": "capability_unavailable",
                                "detail": "web.read.public allowlist is empty or excludes host"}]}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "arena-gateway/0.1"})
            with urllib.request.urlopen(req, timeout=args.get("timeout_s", 10)) as resp:
                body = resp.read()
                final_url = resp.geturl()
        except Exception as e:
            return {"result_type": "error.fetch.v1", "summary": f"fetch failed: {e}",
                    "errors": [{"type": "fetch_error", "detail": str(e)}]}
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
        # Honest stub: without installed provider extensions this is an
        # explicit capability error, never fabricated market data.
        return {"result_type": "error.capability.v1",
                "summary": "OpenBB provider extensions not installed in this deployment",
                "data": {"command": args.get("command"), "provider": args.get("provider")},
                "errors": [{"type": "capability_unavailable",
                            "detail": "install provider extension or supply dataset artifact"}]}

    # ---- codegraph (manual 2.6) ---------------------------------------
    def _graph_conn():
        db = ctx.graph_db or os.path.join(ws, ".codegraph.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS symbols (name TEXT, kind TEXT, file TEXT, line INTEGER, \"commit\" TEXT)")
        return conn

    def codegraph_index(args):
        root = os.path.abspath(os.path.join(ws, args.get("root", ".")))
        if not root.startswith(os.path.abspath(ws)):
            return {"result_type": "error.policy.v1", "summary": "root escapes workspace",
                    "errors": [{"type": "policy_denied"}]}
        conn = _graph_conn()
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
                        with open(p, encoding="utf-8", errors="replace") as f:
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
        conn = _graph_conn()
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
        conn = _graph_conn()
        rows = conn.execute(
            'SELECT file, line FROM symbols WHERE "commit" = (SELECT MAX("commit") FROM symbols)').fetchall()
        impacted = []
        for file, _line in rows:
            p = os.path.join(ws, file)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    if sym and sym in f.read():
                        impacted.append(file)
            except OSError:
                pass
        return {"result_type": "codegraph.impact.v1",
                "summary": f"{len(impacted)} files reference '{sym}'",
                "data": {"symbol": sym, "impacted_files": sorted(set(impacted))[:50]}}

    # ---- rea.inspect (manual 2.7, static only) ------------------------
    MAGIC = {b"\x7fELF": "ELF", b"MZ": "PE", b"\xfe\xed\xfa\xce": "Mach-O",
             b"\xcf\xfa\xed\xfe": "Mach-O-64", b"PK\x03\x04": "zip/jar/apk"}

    def rea_inspect(args):
        target = args.get("path", "")
        path = os.path.abspath(os.path.join(ws, target))
        if not path.startswith(os.path.abspath(ws)) or not os.path.isfile(path):
            return {"result_type": "error.policy.v1", "summary": "target outside workspace or missing",
                    "errors": [{"type": "policy_denied", "detail": target}]}
        with open(path, "rb") as f:
            head = f.read(4096)
            f.seek(0)
            digest = hashlib.sha256(f.read()).hexdigest()
        fmt = next((v for k, v in MAGIC.items() if head.startswith(k)), "unknown")
        strings = re.findall(rb"[ -~]{6,}", head)
        return {"result_type": "rea.inspect.v1",
                "summary": f"static inspection of {target}: format={fmt}",
                "data": {"sha256": digest, "format": fmt, "size": os.path.getsize(path),
                         "strings_sample": [s.decode() for s in strings[:20]]},
                "evidence": [ev.evidence_ref(f"file://{path}", f"sha256:{digest}")]}

    broker.register(ToolSpec("scrapling.fetch", ["web.read.public"], "read_only",
                             scrapling_fetch, "Allowlist-guarded web page capture."))
    broker.register(ToolSpec("scrapling.fetch_fixture", ["web.read.public"], "read_only",
                             fixture_fetch, "Offline captured-page fixture (demo)."))
    broker.register(ToolSpec("openbb.query", ["data.market.read"], "read_only",
                             openbb_query, "Typed market/economic data (requires provider extensions)."))
    broker.register(ToolSpec("codegraph.index", ["repo.read"], "read_only",
                             codegraph_index, "Index workspace Python symbols into graph store."))
    broker.register(ToolSpec("codegraph.find", ["repo.read"], "read_only",
                             codegraph_find, "Find symbols in the code graph."))
    broker.register(ToolSpec("codegraph.impact", ["repo.read"], "read_only",
                             codegraph_impact, "Impact trace: files referencing a symbol."))
    broker.register(ToolSpec("rea.inspect", ["binary.inspect.static"], "read_only",
                             rea_inspect, "Static binary/application triage (hash, format, strings)."))


def _title(text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return m.group(1).strip()[:200] if m else ""
