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
    os.makedirs(data_dir, exist_ok=True)
    ledger = Ledger(os.path.join(data_dir, "ledger.db"))
    evidence = EvidenceStore(os.path.join(data_dir, "artifacts"))
    broker = CapabilityBroker(evidence, workspace_root=data_dir + "/workspace")
    os.makedirs(data_dir + "/workspace", exist_ok=True)
    return Engine(ledger, broker, evidence)


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
