"""Arena Content Foundry — declarative spec -> qualified package pipeline.

Modules:
  spec        versioned feature-spec schema + fail-closed validation
  ids         per-table ID registry, limits, collision detection
  dbc_writer  lossless DBC writer (read -> delta -> write -> verify)
  assets      asset discovery index + semantic search + patch-cost class
  generate    deterministic artifact generators (SQL/C++/Lua/DBC/conf)
  package     bundle assembly, manifest, evidence + compatibility reports
  adapter     Campaign Party adapter contract (v1) + fake host
  cli         command line entry point (tools/foundry.py)

Evidence levels (see docs/12): ARENA-STATIC, ARENA-SYNTHETIC, ARENA-DB,
ARENA-COMPILE, ARENA-PACK, CAMPAIGN-ADMISSION, CAMPAIGN-LIVE. Nothing here
claims a level it has not earned.
"""

__version__ = "1.0.0"
SPEC_VERSION = 1
ADAPTER_VERSION = "1.1.0"
