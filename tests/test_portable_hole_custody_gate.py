import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "server/mod-portable-hole/src/PortableHole.cpp"
ADDON = ROOT / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua"
README = ROOT / "server/mod-portable-hole/README.md"
GATE = ROOT / "docs/23-portable-hole-physical-custody.md"
REGISTER = ROOT / "designs/gameplay-systems-register.json"


class PortableHolePhysicalCustodyGate(unittest.TestCase):
    def test_custody_contract_stays_design_only_and_identifies_core_pin(self):
        gate = GATE.read_text(encoding="utf-8")
        self.assertIn("**Status: design-only.**", gate)
        self.assertIn("6ffb62abb867dabb258de6813ff5d06d6b011543", gate)
        for heading in (
            "### A. Define the eligible item subset",
            "### B. Durable transfer identity and idempotency",
            "### C. Atomic persistence",
            "### D. Live in-memory reconciliation",
            "### E. Crash and adversarial qualification",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, gate)
        self.assertIn("keep `DEPOSIT`/`WITHDRAW` absent", gate)

    def test_unqualified_physical_transfer_is_not_exposed_in_module_or_addon(self):
        module = MODULE.read_text(encoding="utf-8")
        addon = ADDON.read_text(encoding="utf-8")
        for token, source in (("module", module), ("addon", addon)):
            with self.subTest(source=token):
                self.assertNotRegex(source, r"\b(?:DEPOSIT|WITHDRAW)\b")
        for mutation in ("RemoveItem(", "StoreItem(", "DestroyItem("):
            with self.subTest(mutation=mutation):
                self.assertNotIn(mutation, module)
        self.assertIn("does **not** remove or materialize physical bag items", README.read_text())

    def test_register_tracks_custody_as_design_only(self):
        register = json.loads(REGISTER.read_text(encoding="utf-8"))
        entry = next(system for system in register["systems"]
                     if system["id"] == "portable-hole-unique-instance-custody")
        self.assertEqual(entry["implementation_state"], "DESIGN_ONLY")
        self.assertIn("docs/23-portable-hole-physical-custody.md", entry["source_evidence"])
        self.assertIn("Probe the pinned core's async transaction completion",
                      entry["next_milestone"])


if __name__ == "__main__":
    unittest.main()
