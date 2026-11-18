"""Characters migration qualifier refuses unsafe targets and binds spec bytes."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

REPO = helpers.REPO
FOUNDRY = REPO / "tools" / "foundry.py"
SPEC = REPO / "specs" / "vault-terminal.json"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "foundry_characters_qualify", REPO / "tools" / "foundry_characters_qualify.py")
qualifier = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(qualifier)


class CharactersQualification(unittest.TestCase):
    def _build(self, root: Path) -> Path:
        package = root / "package"
        result = helpers.run_tool(FOUNDRY, "build", SPEC, "--out", package)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return package

    def test_requires_exact_generated_migration_and_rollback_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._build(Path(tmp))
            spec, protocol, migration, rollback, manifest_hash = qualifier._check_package(package)
            self.assertEqual(spec["name"], "vault-terminal")
            self.assertEqual(protocol["claims_table"], "vault_terminal_claims")
            self.assertIn(b"PRIMARY KEY (`guid`)", migration)
            sql = qualifier._read_json(package / "spec.json")
            from foundry.generate import characters_claim_upsert_sql
            upsert = characters_claim_upsert_sql(sql)
            self.assertIn("ON DUPLICATE KEY UPDATE", upsert)
            self.assertIn("total_claims < 50", upsert)
            self.assertIn(b"Keep `vault_terminal_claims`", rollback)
            self.assertEqual(len(manifest_hash), 64)

    def test_package_tampering_and_wrong_migration_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._build(Path(tmp))
            migration = package / "sql/characters/vault_terminal_claims.sql"
            migration.write_text(migration.read_text() + "DROP TABLE unsafe;\n")
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                qualifier._check_package(package)

    def test_database_name_and_explicit_disposable_flag_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._build(root)
            defaults = root / "mysql.cnf"
            defaults.write_text("[client]\nuser=invalid\n")
            with self.assertRaisesRegex(ValueError, "disposable_characters"):
                qualifier.qualify(package, "acore_characters", defaults)
            output = root / "proof.json"
            result = helpers.run_tool(
                REPO / "tools" / "foundry_characters_qualify.py", package,
                "--database", "arena_foundry_disposable_characters_ci",
                "--defaults-extra-file", defaults, "--out", output)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--confirm-disposable", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
