"""Entry point: python3 -m unified.server [--port N] [--data DIR]"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re

from .broker import CapabilityBroker
from .engine import Engine
from .evidence import EvidenceStore
from .gateway import serve
from .ledger import Ledger


def _load_web_allowlist() -> tuple[str, ...]:
    """Parse an explicit JSON hostname allowlist; unset means web access is off."""
    raw = os.environ.get("UNIFIED_WEB_ALLOWLIST", "").strip()
    if not raw:
        return ()
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("UNIFIED_WEB_ALLOWLIST must be a JSON array of hostnames") from exc
    if not isinstance(configured, list) or not all(isinstance(x, str) for x in configured):
        raise RuntimeError("UNIFIED_WEB_ALLOWLIST must be a JSON array of hostnames")
    hosts = []
    for entry in configured:
        host = entry.strip().lower().rstrip(".")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if (len(labels) < 2 or any(not label or len(label) > 63
                    or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                    for label in labels)):
                raise RuntimeError(f"invalid web allowlist hostname: {entry!r}")
        else:
            if not address.is_global:
                raise RuntimeError(f"non-public IP is forbidden in web allowlist: {entry!r}")
        if host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def build_engine(data_dir: str) -> Engine:
    from .gateway import validate_production_configuration
    validate_production_configuration()
    web_allowlist = _load_web_allowlist()
    from .adapters import AdapterContext, register_adapters
    from .automation import AutomationConnector
    from .delivery import DeliveryService
    from .memory import ConversationMemory
    from .model_router import ModelRouter
    from .role_registry import RoleRegistry

    os.makedirs(data_dir, exist_ok=True)
    ws = os.path.join(data_dir, "workspace")
    os.makedirs(ws, exist_ok=True)
    os.makedirs(os.path.join(data_dir, "fixtures"), exist_ok=True)
    ledger = Ledger(os.path.join(data_dir, "ledger.db"))
    evidence = EvidenceStore(os.path.join(data_dir, "artifacts"))
    broker = CapabilityBroker(evidence, workspace_root=ws, project_isolation=True)
    router = ModelRouter(max_concurrent=1)
    broker.register_model_tool(router)
    register_adapters(broker, AdapterContext(
        evidence=evidence, workspace_root=ws,
        web_allowlist=web_allowlist,  # unset means fail closed; no redirect support
        fixture_dir=os.path.join(data_dir, "fixtures"),
        graph_db=os.path.join(data_dir, "codegraph.db")))
    roles = RoleRegistry(os.path.join(data_dir, "roles.json")
                         if os.path.exists(os.path.join(data_dir, "roles.json")) else None)
    engine = Engine(ledger, broker, evidence, model_router=router, roles=roles,
                    memory=ConversationMemory(os.path.join(data_dir, "memory.db")),
                    delivery=DeliveryService(os.environ.get("CALLBACK_SECRET", "dev-secret")))
    engine.automation = AutomationConnector(engine, os.environ.get("CALLBACK_SECRET", "dev-secret"))
    return engine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--data", default="arena-data")
    args = ap.parse_args()
    from .gateway import OPERATOR_GRANTS
    engine = build_engine(args.data)
    recovered = engine.recover(OPERATOR_GRANTS)
    if recovered:
        print(f"recovered {len(recovered)} interrupted task(s): {recovered}")
    server = serve(engine, args.host, args.port)
    print(f"Unified gateway listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
