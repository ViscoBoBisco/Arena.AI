#!/usr/bin/env python3
"""foundry.py - Arena Content Foundry CLI (thin wrapper over foundry/)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foundry.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
