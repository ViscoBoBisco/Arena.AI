"""Characters migration qualifier refuses unsafe targets and binds spec bytes."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
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

    def _characters_proof(self, package: Path) -> dict:
        from foundry.generate import characters_claim_transaction_sql
        spec = json.loads((package / "spec.json").read_text(encoding="utf-8"))
        protocol = spec["addon_protocol"]
        table = protocol["claims_table"]
        migration = package / "sql" / "characters" / f"{table}.sql"
        rollback = package / "sql" / "characters" / f"99_{spec['name']}_rollback.sql"
        module = (package / "server" / f"mod-{spec['name']}" / "src" /
                  f"{''.join(part.capitalize() for part in spec['name'].split('-'))}.cpp")
        manifest_sha = hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest()
        pin = json.loads((REPO / "foundry" / "core-pins.json").read_text())["azerothcore_wotlk"]
        templates = characters_claim_transaction_sql(spec)
        request_hash = hashlib.sha256("\n".join(sql for sql, _args in templates).encode()).hexdigest()
        return {
            "schema": "arena-characters-db-qualification/3",
            "qualification": "ARENA-DB-CHARACTERS",
            "status": "PASS",
            "trust": "SYNTHETIC_DISPOSABLE_MYSQL",
            "package": spec["name"],
            "package_version": spec["version"],
            "manifest_sha256": manifest_sha,
            "table": table,
            "database": "arena_foundry_disposable_characters_ci",
            "server_version": "MySQL 8.4.3 test fixture",
            "migration_sha256": hashlib.sha256(migration.read_bytes()).hexdigest(),
            "rollback_note_sha256": hashlib.sha256(rollback.read_bytes()).hexdigest(),
            "request_transaction_sha256": request_hash,
            "module_source_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
            "spec_sha256": hashlib.sha256((package / "spec.json").read_bytes()).hexdigest(),
            "core_source": {"repository": pin["repository"], "revision": pin["revision"]},
            "table_schema": {
                "engine": "InnoDB",
                "columns": [
                    "guid|int unsigned|NO|PRI|1",
                    "total_claims|int unsigned|NO||2",
                    "last_claim_day|date|YES||3",
                    "last_request_id|varchar(64)|YES||4",
                ],
                "primary_key": "guid",
            },
            "request_table_schema": {
                "engine": "InnoDB",
                "columns": [
                    "guid|int unsigned|NO|PRI|1",
                    "request_id|varchar(64)|NO|PRI|2",
                    "status|varchar(24)|NO||3",
                    "total_claims|int unsigned|NO||4",
                    "claim_day|date|NO||5",
                    "created_at|timestamp|NO|MUL|6",
                ],
                "primary_key": "guid,request_id",
            },
            "checks": {key: "PASS" for key in (
                "migration_applied_twice", "legacy_daily_claims_migrated_and_retained",
                "schema_matches_declared_ledger",
                "same_request_concurrent_retry_replayed", "lost_response_retry_replayed",
                "concurrent_distinct_requests_attributed", "claim_day_rollover",
                "concurrent_target_cap_enforced", "transaction_failure_rolled_back",
                "retry_after_transaction_failure", "receipt_survives_new_connection",
                "module_state_query", "duplicate_character_row_rejected", "fixture_rows_removed",
            )},
            "sequence": [
                "seed-legacy-per-day-schema", "migrate-and-verify-history-preservation",
                "verify-state-and-receipt-schema", "apply-migration-idempotently",
                "race-same-request-id", "drop-success-response-and-retry-on-new-connection",
                "race-distinct-request-ids", "advance-database-day-and-retry-same-id",
                "race-at-target-minus-one", "attempt-after-target", "inject-mid-transaction-failure",
                "retry-failed-id", "verify-primary-key", "remove-only-test-guid-rows",
            ],
            "limitations": ["synthetic proof fixture; not an authenticated CI statement"],
            "environment": {
                "workflow_run_id": "44300000000",
                "workflow_job": "qualify-generated-world-sql",
                "workflow_name": "pinned AzerothCore disposable DB qualification",
                "workflow_ref": ("ViscoBoBisco/Arena.AI/.github/workflows/"
                                 "azerothcore-db-qualification.yml@refs/pull/1/merge"),
                "source_commit": "a" * 40,
                "core_repository": pin["repository"],
                "core_revision": pin["revision"],
            },
        }

    def _attest(self, package: Path, *proofs: dict, world_proof=None) -> dict:
        manifest_sha = hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest()
        result = {
            "schema": "arena-qualification-attestations/1",
            "subject": {"manifest_sha256": manifest_sha},
            "characters_database_qualifications": list(proofs),
        }
        if world_proof is not None:
            result["database_qualification"] = world_proof
            result["levels"] = {"ARENA-DB": {"status": "PASS", "evidence": "sha256:forged"}}
        return result

    def test_requires_exact_generated_migration_and_rollback_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._build(Path(tmp))
            spec, protocol, migration, rollback, manifest_hash = qualifier._check_package(package)
            self.assertEqual(spec["name"], "vault-terminal")
            self.assertEqual(protocol["claims_table"], "vault_terminal_claims")
            self.assertIn(b"PRIMARY KEY (`guid`)", migration)
            sql = qualifier._read_json(package / "spec.json")
            from foundry.generate import characters_claim_transaction_sql
            transaction = characters_claim_transaction_sql(sql)
            self.assertEqual(len(transaction), 4)
            self.assertIn("last_request_id", transaction[2][0])
            self.assertIn("status = 'PENDING'", transaction[2][0])
            self.assertIn(b"PRIMARY KEY (`guid`, `request_id`)", migration)
            self.assertIn(b"Keep `vault_terminal_claims`", rollback)
            self.assertIn(b"vault_terminal_claims_requests", rollback)
            self.assertEqual(len(manifest_hash), 64)

    def test_package_tampering_and_wrong_migration_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._build(Path(tmp))
            migration = package / "sql/characters/vault_terminal_claims.sql"
            migration.write_text(migration.read_text() + "DROP TABLE unsafe;\n")
            with self.assertRaisesRegex(ValueError, "integrity check failed"):
                qualifier._check_package(package)

    def test_table_scoped_proof_promotes_only_characters_database_behaviors(self):
        from foundry.qualification import qualify_package

        with tempfile.TemporaryDirectory() as tmp:
            package = self._build(Path(tmp))
            proof = self._characters_proof(package)
            report = qualify_package(package, _attestations_override=self._attest(package, proof))
            gates = {gate["name"]: gate["status"] for gate in report["gates"]}
            maturity = {item["identity"]: item for item in report["artifact_maturity"]["artifacts"]}
            char = maturity["characters_sql:vault_terminal_claims"]["status"]
            migration = maturity["data_migration:vault_terminal_claims"]["status"]

            self.assertEqual(gates["attestation_binding"], "PASS")
            self.assertEqual(gates["database_migrations:characters:vault_terminal_claims"], "PASS")
            self.assertEqual(gates["database_migrations"], "PASS")
            self.assertEqual(char["schema_valid"], "PASS")
            self.assertEqual(char["migration_tested"], "PASS")
            self.assertEqual(migration["schema_valid"], "PASS")
            self.assertEqual(migration["migration_tested"], "PASS")
            self.assertEqual(report["artifact_maturity"]["trust"], "EXTERNAL_UNTRUSTED")
            self.assertEqual(report["status"], "BLOCKED")
            self.assertFalse(report["qualified"])
            self.assertEqual(gates["artifact_maturity:server_module:runtime_exercised"], "BLOCKED")
            self.assertEqual(gates["artifact_maturity:addon_files:runtime_exercised"], "BLOCKED")
            self.assertEqual(gates["live_gameplay"], "BLOCKED")

    def test_forged_stale_or_substituted_characters_proofs_are_rejected(self):
        from foundry.qualification import qualify_package

        mutations = (
            ("bare pass", lambda p: {"schema": "arena-characters-db-qualification/3",
                                     "qualification": "ARENA-DB-CHARACTERS",
                                     "status": "PASS", "table": p["table"]}),
            ("failed status", lambda p: {**p, "status": "FAIL"}),
            ("stale manifest", lambda p: {**p, "manifest_sha256": "0" * 64}),
            ("wrong table", lambda p: {**p, "table": "other_characters_table"}),
            ("wrong database", lambda p: {**p, "database": "arena_foundry_disposable_ci"}),
            ("different Characters database", lambda p: {
                **p, "database": "arena_foundry_disposable_characters_other"}),
            ("wrong schema", lambda p: {**p, "table_schema": {**p["table_schema"], "engine": "MyISAM"}}),
            ("substituted migration", lambda p: {**p, "migration_sha256": "0" * 64}),
            ("substituted query", lambda p: {**p, "request_transaction_sha256": "0" * 64}),
            ("substituted module", lambda p: {**p, "module_source_sha256": "0" * 64}),
            ("wrong core", lambda p: {**p, "core_source": {"repository": "other", "revision": "b" * 40}}),
            ("missing concurrent behavior", lambda p: {**p, "checks": {
                key: value for key, value in p["checks"].items()
                if key != "concurrent_distinct_requests_attributed"}}),
            ("wrong workflow", lambda p: {**p, "environment": {
                **p["environment"], "workflow_name": "user supplied"}}),
        )
        with tempfile.TemporaryDirectory() as tmp:
            package = self._build(Path(tmp))
            for label, mutate in mutations:
                with self.subTest(label=label):
                    candidate = mutate(copy.deepcopy(self._characters_proof(package)))
                    report = qualify_package(
                        package, _attestations_override=self._attest(package, candidate))
                    gates = {gate["name"]: gate["status"] for gate in report["gates"]}
                    self.assertEqual(
                        gates["database_migrations:characters:vault_terminal_claims"], "BLOCKED")
                    self.assertEqual(report["status"], "BLOCKED")

    def test_world_database_proof_cannot_substitute_for_characters_proof(self):
        from foundry.qualification import qualify_package

        with tempfile.TemporaryDirectory() as tmp:
            package = self._build(Path(tmp))
            manifest_sha = hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest()
            world_only = {
                "schema": "arena-qualification-attestations/1",
                "subject": {"manifest_sha256": manifest_sha},
                "levels": {"ARENA-DB": {"status": "PASS", "evidence": "sha256:bare"}},
                "database_qualification": {
                    "schema": "arena-db-qualification/2",
                    "manifest_sha256": manifest_sha,
                },
            }
            report = qualify_package(package, _attestations_override=world_only)
            gates = {gate["name"]: gate["status"] for gate in report["gates"]}
            self.assertEqual(
                gates["database_migrations:characters:vault_terminal_claims"], "BLOCKED")
            self.assertEqual(
                report["artifact_maturity"]["artifacts"][1]["status"]["schema_valid"],
                "NOT_ASSESSED")

    def test_mixed_package_requires_world_and_characters_proofs_independently(self):
        from foundry.qualification import qualify_package

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = json.loads(SPEC.read_text(encoding="utf-8"))
            spec["name"] = "mixed-proof"
            spec["title"] = "Mixed Proof"
            spec["id_range"] = {"start": 900500, "end": 900550}
            spec["addon"]["prefix"] = "MP1"
            spec["addon"]["slash_commands"] = ["/mp"]
            spec["module"]["conf_key"] = "MixedProof.Enable"
            spec["config"] = {"MixedProof.Enable": 1}
            spec["addon_protocol"]["prefix"] = "MP1"
            spec["addon_protocol"]["claims_table"] = "mixed_claims"
            spec["delivery"]["characters_sql_tables"] = ["mixed_claims"]
            spec["items"] = [{"key": "test-token", "name": "Test Token",
                               "description": "mixed database fixture"}]
            spec_path = root / "mixed-proof.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            package = root / "mixed-package"
            result = helpers.run_tool(FOUNDRY, "build", spec_path, "--out", package)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            proof = self._characters_proof(package)
            report = qualify_package(package, _attestations_override=self._attest(package, proof))
            gates = {gate["name"]: gate["status"] for gate in report["gates"]}
            maturity = {item["identity"]: item["status"]
                        for item in report["artifact_maturity"]["artifacts"]}
            self.assertEqual(gates["database_migrations:characters:mixed_claims"], "PASS")
            self.assertEqual(gates["database_migrations:world"], "BLOCKED")
            self.assertEqual(gates["database_migrations"], "BLOCKED")
            self.assertEqual(maturity["characters_sql:mixed_claims"]["schema_valid"], "PASS")
            self.assertEqual(maturity["world_sql"]["schema_valid"], "NOT_ASSESSED")
            self.assertEqual(report["status"], "BLOCKED")

    def test_duplicate_and_unlisted_characters_proofs_fail_inventory(self):
        from foundry.qualification import qualify_package

        with tempfile.TemporaryDirectory() as tmp:
            package = self._build(Path(tmp))
            proof = self._characters_proof(package)
            for proofs in ((proof, copy.deepcopy(proof)),
                           (proof, {**proof, "table": "undeclared_table"})):
                with self.subTest(proof_count=len(proofs), table=proofs[-1]["table"]):
                    report = qualify_package(
                        package, _attestations_override=self._attest(package, *proofs))
                    gates = {gate["name"]: gate["status"] for gate in report["gates"]}
                    self.assertEqual(gates["characters_database_evidence_inventory"], "BLOCKED")
                    if proofs[-1]["table"] == "undeclared_table":
                        self.assertEqual(
                            gates["database_migrations:characters:vault_terminal_claims"], "PASS")
                    else:
                        self.assertEqual(
                            gates["database_migrations:characters:vault_terminal_claims"], "BLOCKED")
                    self.assertEqual(report["status"], "BLOCKED")

    def test_ci_workflow_asserts_the_current_request_receipt_proof_contract(self):
        workflow = (REPO / ".github" / "workflows" / "azerothcore-db-qualification.yml").read_text()
        for token in (
            "arena-characters-db-qualification/3",
            "legacy_daily_claims_migrated_and_retained",
            "same_request_concurrent_retry_replayed",
            "lost_response_retry_replayed",
            "concurrent_distinct_requests_attributed",
            "transaction_failure_rolled_back",
        ):
            self.assertIn(token, workflow)
        self.assertNotIn("concurrent_daily_increment_serialized", workflow)

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
