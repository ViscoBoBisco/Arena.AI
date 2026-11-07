"""Entry point: python3 -m unified.server [--port N] [--data DIR]"""
from __future__ import annotations

import argparse
import os

from .broker import CapabilityBroker
from .engine import Engine
from .evidence import EvidenceStore
from .gateway import serve
from .ledger import Ledger


def build_engine(data_dir: str) -> Engine:
    from .adapters import AdapterContext, register_adapters
    from .automation import AutomationConnector
    from .memory import ConversationMemory
    from .model_router import ModelRouter
    from .role_registry import RoleRegistry

    os.makedirs(data_dir, exist_ok=True)
    ws = os.path.join(data_dir, "workspace")
    os.makedirs(ws, exist_ok=True)
    os.makedirs(os.path.join(data_dir, "fixtures"), exist_ok=True)
    ledger = Ledger(os.path.join(data_dir, "ledger.db"))
    evidence = EvidenceStore(os.path.join(data_dir, "artifacts"))
    broker = CapabilityBroker(evidence, workspace_root=ws)
    router = ModelRouter(max_concurrent=1)
    broker.register_model_tool(router)
    register_adapters(broker, AdapterContext(
        evidence=evidence, workspace_root=ws,
        web_allowlist=(),  # fail closed until hosts are allowlisted (manual 5.8)
        fixture_dir=os.path.join(data_dir, "fixtures"),
        graph_db=os.path.join(data_dir, "codegraph.db")))
    roles = RoleRegistry(os.path.join(data_dir, "roles.json")
                         if os.path.exists(os.path.join(data_dir, "roles.json")) else None)
    engine = Engine(ledger, broker, evidence, model_router=router, roles=roles,
                    memory=ConversationMemory(os.path.join(data_dir, "memory.db")))
    engine.automation = AutomationConnector(engine, os.environ.get("CALLBACK_SECRET", "dev-secret"))
    return engine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--data", default="arena-data")
    args = ap.parse_args()
    engine = build_engine(args.data)
    server = serve(engine, args.host, args.port)
    print(f"Unified gateway listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
