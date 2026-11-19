"""Reproducible four-module integration-bundle smoke tests."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools/build_gameplay_handoff.py"


class GameplayHandoffBundle(unittest.TestCase):
    def test_bundle_contains_ordered_additive_migrations_and_self_verifies(self):
        with tempfile.TemporaryDirectory(prefix="arena-gameplay-handoff-") as temp:
            bundle = Path(temp) / "bundle"
            subprocess.run([sys.executable, str(BUILDER), "--out", str(bundle)],
                           cwd=ROOT, check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, "verify.py"], cwd=bundle, check=True,
                           capture_output=True, text=True)
            metadata = json.loads((bundle / "bundle.json").read_text())
            self.assertEqual(metadata["schema"], "arena-gameplay-integration-handoff/1")
            self.assertEqual(metadata["components"], [
                "Vault Terminal", "Portable Hole", "Runic Codex", "Argent Commission"])
            self.assertEqual(metadata["migration_order"], [
                "migrations/characters/portable-hole/001_portable_hole_warehouse.sql",
                "migrations/characters/portable-hole/002_portable_hole_vault_access.sql",
                "migrations/characters/runic-codex/001_runic_codex.sql",
                "migrations/characters/runic-codex/002_runic_codex_inscriptions.sql",
                "migrations/characters/runic-codex/003_runic_codex_registry_loadouts.sql",
                "migrations/characters/argent-commission/001_argent_commission.sql",
                "migrations/characters/vault-terminal/vault_terminal_claims.sql",
            ])
            for component in ("portable-hole", "runic-codex", "argent-commission", "vault-terminal"):
                self.assertTrue((bundle / "modules" / f"mod-{component}").is_dir())
            self.assertTrue((bundle / "addons/PortableHole/PortableHole.lua").is_file())
            self.assertTrue((bundle / "addons/RunicCodex/RunicCodex.lua").is_file())
            self.assertTrue((bundle / "addons/ArgentCommission/ArgentCommission.lua").is_file())
            self.assertTrue((bundle / "addons/VaultTerminal/VaultTerminal.lua").is_file())
            self.assertTrue((bundle / "config/mod-portable-hole.conf.dist").is_file())
            self.assertTrue((bundle / "config/mod-argent-commission.conf.dist").is_file())
            self.assertTrue((bundle / "config/mod-runic-codex.conf.dist").is_file())
            self.assertFalse(metadata["safety"]["ready_for_production"])
            self.assertFalse(metadata["safety"]["physical_item_custody_enabled"])
            self.assertEqual(metadata["qualification"]["worldserver_boot"], "NOT_ASSESSED")
            self.assertEqual(metadata["qualification"]["combined_worldserver_build_and_link"],
                             "CI_GATE_CONFIGURED_RESULT_PENDING")
            self.assertTrue((bundle / "check_dependencies.py").is_file())
            for name in ("INSTALL.md", "ROLLBACK.md", "DEPENDENCIES.md",
                         "PRIVATE-QUALIFICATION.md"):
                self.assertTrue((bundle / name).is_file())

    def test_builder_refuses_overwrite_and_repo_output(self):
        with tempfile.TemporaryDirectory(prefix="arena-gameplay-handoff-") as temp:
            existing = Path(temp) / "existing"
            existing.mkdir()
            result = subprocess.run([sys.executable, str(BUILDER), "--out", str(existing)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            result = subprocess.run([sys.executable, str(BUILDER), "--out", str(ROOT / "dist/test")],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((ROOT / "dist/test").exists())


if __name__ == "__main__":
    unittest.main()
