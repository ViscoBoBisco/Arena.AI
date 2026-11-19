"""Safety and integration tests for the single-command local qualification tool."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/local_gameplay.py"
spec = importlib.util.spec_from_file_location("arena_local_gameplay", SCRIPT)
local = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = local
spec.loader.exec_module(local)


class LocalGameplayAudit(unittest.TestCase):
    def test_complete_package_inventory_migration_order_and_toc_versions(self):
        result = local.audit_package()
        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertEqual(set(result["addon_interfaces"].values()), {"30300"})
        self.assertEqual(result["handoff_migration_order"], list(local.MIGRATIONS))
        self.assertEqual(len(result["module_migrations"]), 7)

    def test_checkout_dependency_probe_runs_without_bundle_directory(self):
        import subprocess
        result = subprocess.run([
            sys.executable, str(ROOT / "tools/check_gameplay_handoff_dependencies.py"),
            "--core-source", str(ROOT),
        ], capture_output=True, text=True)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("pinned_core", result.stdout)


class MySQLClientApiProbe(unittest.TestCase):
    def test_mysql_84_probe_checks_version_headers_and_linked_api(self):
        from unittest.mock import patch
        import subprocess
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[1:] == ["--version"]:
                return subprocess.CompletedProcess(argv, 0, "8.4.13\n", "")
            if argv[1:] == ["--cflags"]:
                return subprocess.CompletedProcess(argv, 0, "-I/test/mysql/include", "")
            if argv[1:] == ["--libs"]:
                return subprocess.CompletedProcess(argv, 0, "-L/test/mysql/lib -lmysqlclient", "")
            source = next(Path(arg) for arg in argv if arg.endswith("mysql_api_probe.c"))
            text = source.read_text()
            self.assertIn("mysql_stmt_bind_named_param", text)
            self.assertIn("MYSQL_OPT_SSL_MODE", text)
            self.assertIn("-lmysqlclient", argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.object(local.shutil, "which", side_effect=lambda name: {
                "mysql_config": "/usr/bin/mysql_config", "cc": "/usr/bin/cc"}.get(name)), patch.object(
                local.subprocess, "run", side_effect=fake_run):
            result = local.mysql_api_report()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["probe"], "COMPILE_AND_LINK_PASS")
        self.assertEqual(len(calls), 4)

    def test_incompatible_client_api_blocks_build_even_when_cmake_and_compilers_exist(self):
        dependencies = {name: {"status": "PASS"} for name in ("git", "cmake", "cxx")}
        dependencies["mysql_client_api"] = {"status": "INCOMPATIBLE"}
        ready, missing = local.pinned_build_prerequisites(dependencies)
        self.assertFalse(ready)
        self.assertEqual(missing, ["mysql_client_api"])

    def test_mariadb_version_is_rejected_before_probe_or_build(self):
        from unittest.mock import patch
        import subprocess
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "10.11.8-MariaDB\n", "")

        with patch.object(local.shutil, "which", side_effect=lambda name: {
                "mariadb_config": "/usr/bin/mariadb_config", "cc": "/usr/bin/cc"}.get(name)), patch.object(
                local.subprocess, "run", side_effect=fake_run):
            result = local.mysql_api_report()
        self.assertEqual(result["status"], "INCOMPATIBLE")
        self.assertEqual(len(calls), 1)
        self.assertIn("libmysqlclient-dev=8.4.13-1ubuntu24.04", result["install_guidance"])
        self.assertIn("cmake git libboost-all-dev", result["install_guidance"])


class WorkspaceSafety(unittest.TestCase):
    def test_workspace_is_external_and_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="arena-local-tool-") as temp:
            target = Path(temp) / "work"
            metadata = local.create_workspace(target)
            self.assertEqual(metadata["schema"], local.WORKSPACE_SCHEMA)
            with self.assertRaises(FileExistsError):
                local.create_workspace(target)
            with self.assertRaises(ValueError):
                local.create_workspace(ROOT / "not-allowed")

    def test_workspace_paths_reject_symlink_roots_and_parents(self):
        with tempfile.TemporaryDirectory(prefix="arena-local-symlink-") as temp:
            root = Path(temp)
            real_parent = root / "real"
            real_parent.mkdir()
            linked_parent = root / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinks"):
                local.create_workspace(linked_parent / "workspace")
            workspace = real_parent / "workspace"
            local.create_workspace(workspace)
            alias = root / "workspace-alias"
            alias.symlink_to(workspace, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinks"):
                local.validate_resume_workspace(alias)

    def test_resume_requires_matching_recorded_checkout_and_source_hash(self):
        with tempfile.TemporaryDirectory(prefix="arena-local-resume-") as temp:
            workspace = Path(temp) / "work"
            local.create_workspace(workspace)
            resolved, marker = local.validate_resume_workspace(workspace)
            self.assertEqual(resolved, workspace.resolve())
            marker_path = workspace / ".arena-local-integration.json"
            marker["source_snapshot_sha256"] = "0" * 64
            marker_path.write_text(json.dumps(marker))
            with self.assertRaisesRegex(ValueError, "source tree hash differs"):
                local.validate_resume_workspace(workspace)

    def test_run_logged_handles_stdin_sql_and_records_output(self):
        with tempfile.TemporaryDirectory(prefix="arena-local-tool-") as temp:
            workspace = Path(temp)
            source = workspace / "input.sql"
            source.write_text("SELECT 7;\n")
            result = local.run_logged(workspace, "echo-sql", [sys.executable, "-c",
                "import sys; print(sys.stdin.read().strip())"], input_file=source)
            self.assertEqual(result["status"], "PASS")
            self.assertIn("SELECT 7", (workspace / result["log"]).read_text())

    def test_mysql_environment_is_local_only_and_requires_all_values(self):
        import os
        from unittest.mock import patch
        names = {
            "ARENA_DISPOSABLE_MYSQL_HOST": "127.0.0.1",
            "ARENA_DISPOSABLE_MYSQL_PORT": "3306",
            "ARENA_DISPOSABLE_MYSQL_USER": "arena_test",
            "ARENA_DISPOSABLE_MYSQL_PASSWORD": "not-logged",
        }
        with patch.dict(os.environ, names, clear=False):
            self.assertEqual(local._mysql_credentials()["host"], "127.0.0.1")
            with patch.dict(os.environ, {"ARENA_DISPOSABLE_MYSQL_HOST": "db.production.example"}):
                with self.assertRaisesRegex(ValueError, "local MySQL"):
                    local._mysql_credentials()


class HandoffEvidence(unittest.TestCase):
    def test_embedded_ci_evidence_must_match_source_core_and_module_trees(self):
        with tempfile.TemporaryDirectory(prefix="arena-handoff-evidence-") as temp:
            bundle = Path(temp)
            modules = ("mod-vault-terminal", "mod-portable-hole", "mod-runic-codex",
                       "mod-argent-commission")
            module_hashes = {}
            for name in modules:
                root = bundle / "modules" / name
                root.mkdir(parents=True)
                (root / "source.txt").write_text(name)
                module_hashes[name] = local._tree_hash(root)
            evidence = {
                "schema": "arena-public-core-integration-evidence/1",
                "source": {"repository": local.REPOSITORY, "head": "source-head"},
                "core": {"revision": "core-pin"}, "modules": module_hashes,
                "combined_worldserver_build_link": "PASS",
                "worldserver_binary_sha256": "a" * 64,
                "worldserver_version_command": {"status": "PASS", "output": "worldserver version"},
                "mysql_client": {"mysql_config_version": "8.4.13",
                                 "libmysqlclient_dev_package": "8.4.13-1ubuntu24.04",
                                 "required_api_probe": "PASS"},
                "worldserver_boot": "NOT_RUN_NO_VERIFIED_SERVER_DATA_AND_DATABASE_BOOT_FIXTURE",
            }
            evidence_path = bundle / "qualification/ci-integration-evidence.json"
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_text(json.dumps(evidence))
            snapshot = "b" * 64
            manifest = {"source": {"head": "source-head",
                                   "worktree_snapshot_sha256": snapshot},
                        "core": {"revision": "core-pin"},
                        "files": {"qualification/ci-integration-evidence.json": {
                            "sha256": local.sha256_file(evidence_path)}}}
            (bundle / "bundle.json").write_text(json.dumps(manifest))
            metadata = {"source_head": "source-head", "source_snapshot_sha256": snapshot,
                        "core_pin": "core-pin"}
            self.assertEqual(local.verify_handoff_provenance(bundle, metadata), evidence)
            (bundle / "modules/mod-runic-codex/source.txt").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "module hash mismatch"):
                local.verify_handoff_provenance(bundle, metadata)


class QualificationStatus(unittest.TestCase):
    def _args(self, **overrides):
        from argparse import Namespace
        values = {"require_build": False, "create_disposable_db": False,
                  "install_addons": False, "attempt_public_boot": False,
                  "ci_evidence": None}
        values.update(overrides)
        return Namespace(**values)

    def test_optional_unassessed_build_does_not_fail_qualification(self):
        gates = {name: {"status": "PASS"} for name in (
            "package_audit", "repository_tests", "source_handoff", "handoff_integrity")}
        gates["pinned_four_module_build_link"] = {"status": "NOT_ASSESSED"}
        self.assertEqual(local.qualification_failures(self._args(), gates), [])

    def test_failed_requested_build_db_boot_and_ci_evidence_are_unsuccessful(self):
        gates = {name: {"status": "PASS"} for name in (
            "package_audit", "repository_tests", "source_handoff", "handoff_integrity")}
        gates.update({"pinned_four_module_build_link": {"status": "NOT_ASSESSED"},
                      "disposable_module_migrations": {"status": "BLOCKED"},
                      "test_client_addons": {"status": "NOT_ASSESSED"},
                      "public_server_boot": {"status": "BLOCKED"},
                      "ci_source_compile_link": {"status": "NOT_ASSESSED"}})
        args = self._args(require_build=True, create_disposable_db=True,
                          attempt_public_boot=True, ci_evidence=Path("evidence.json"))
        self.assertEqual(set(local.qualification_failures(args, gates)), {
            "pinned_four_module_build_link", "disposable_module_migrations",
            "public_server_boot", "ci_source_compile_link"})

    def test_requested_failed_execution_is_not_hidden_by_optional_gates(self):
        gates = {name: {"status": "PASS"} for name in (
            "package_audit", "repository_tests", "source_handoff", "handoff_integrity")}
        gates["pinned_four_module_build_link"] = {"status": "FAIL"}
        self.assertEqual(local.qualification_failures(self._args(), gates),
                         ["pinned_four_module_build_link"])


class BuildResume(unittest.TestCase):
    def _cached_workspace(self, workspace: Path):
        core = workspace / "core"
        module_hashes = {}
        for name in ("mod-vault-terminal", *(module for module, _ in local.COMPONENTS)):
            root = core / "modules" / name
            root.mkdir(parents=True)
            (root / "source.txt").write_text(name)
            module_hashes[name] = local._tree_hash(root)
        binary = workspace / "build/src/server/apps/worldserver"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho worldserver-version\n")
        binary.chmod(0o755)
        head = __import__("subprocess").check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        snapshot, _ = local.source_snapshot()
        saved = {"status": "PASS", "source_head": head,
                 "source_snapshot_sha256": snapshot, "core_revision": local.source_pin(),
                 "modules": module_hashes, "worldserver": str(binary),
                 "worldserver_sha256": local.sha256_file(binary),
                 "version_command": {"status": "PASS", "output": "worldserver-version"}}
        (workspace / "local-build-evidence.json").write_text(json.dumps(saved))
        return saved

    def test_valid_build_evidence_resumes_without_clone_or_rebuild(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix="arena-build-resume-") as temp:
            workspace = Path(temp)
            self._cached_workspace(workspace)
            with patch.object(local, "run_logged", return_value={"status": "PASS"}) as logged:
                result = local.build_pinned_core(workspace, 10)
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["resumed"])
            self.assertEqual(logged.call_count, 1)
            self.assertIn("worldserver-version", logged.call_args.args[1])

    def test_resume_refuses_changed_module_source_instead_of_rebuilding(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix="arena-build-resume-") as temp:
            workspace = Path(temp)
            self._cached_workspace(workspace)
            (workspace / "core/modules/mod-runic-codex/source.txt").write_text("tampered")
            with patch.object(local, "run_logged") as logged:
                result = local.build_pinned_core(workspace, 10)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertIn("module tree changed", result["reason"])
            logged.assert_not_called()


class ClientAndConfigRecovery(unittest.TestCase):
    def _fixture(self, base: Path):
        workspace = base / "workspace"
        client = base / "test-client"
        workspace.mkdir()
        (workspace / "handoff/addons").mkdir(parents=True)
        (client / "Interface/AddOns").mkdir(parents=True)
        (client / "Wow.exe").write_bytes(b"TEST CLIENT STUB; not a game executable")
        (client / "ARENA_TEST_CLIENT.txt").write_text(local.CLIENT_MARKER)
        for addon in local.ADDONS:
            root = workspace / "handoff/addons" / addon
            root.mkdir()
            (root / f"{addon}.toc").write_text("## Interface: 30300\n")
            if addon == "RunicCodex":
                old = client / "Interface/AddOns" / addon
                old.mkdir()
                (old / "preserve.txt").write_text("original user addon")
        return workspace, client

    def test_install_requires_explicit_consent_and_recovers_original_addons(self):
        with tempfile.TemporaryDirectory(prefix="arena-client-recovery-") as temp:
            workspace, client = self._fixture(Path(temp))
            with self.assertRaisesRegex(ValueError, "confirm-test-client-write"):
                local.install_addons(workspace, client, False)
            installed = local.install_addons(workspace, client, True, "12340")
            self.assertEqual(installed["state"], "INSTALLED")
            self.assertTrue((client / "Interface/AddOns/PortableHole/PortableHole.toc").exists())
            result = local.recover_client(workspace, client, True)
            self.assertEqual(result["state"], "RECOVERED_PRE_INSTALL_CLIENT_STATE")
            self.assertFalse((client / "Interface/AddOns/PortableHole").exists())
            self.assertEqual((client / "Interface/AddOns/RunicCodex/preserve.txt").read_text(),
                             "original user addon")
            self.assertTrue((workspace / "recovery/client-current/PortableHole").is_dir())

    def test_interrupted_addon_swap_has_persisted_recovery_and_restores_all_paths(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix="arena-client-interrupted-") as temp:
            workspace, client = self._fixture(Path(temp))
            real_replace = local.os.replace

            def fail_one_staged_swap(source, destination):
                if Path(source).name == "RunicCodex" and "staging/client-addons" in str(source):
                    raise OSError("simulated interruption during addon swap")
                return real_replace(source, destination)

            with patch.object(local.os, "replace", side_effect=fail_one_staged_swap):
                with self.assertRaisesRegex(OSError, "simulated interruption"):
                    local.install_addons(workspace, client, True, "12340")
            manifest = json.loads((workspace / "client-install.json").read_text())
            self.assertEqual(manifest["state"], "INSTALLING")
            self.assertFalse((client / "Interface/AddOns/RunicCodex").exists())
            recovered = local.recover_client(workspace, client, True)
            self.assertEqual(recovered["state"], "RECOVERED_PRE_INSTALL_CLIENT_STATE")
            self.assertFalse((client / "Interface/AddOns/PortableHole").exists())
            self.assertEqual((client / "Interface/AddOns/RunicCodex/preserve.txt").read_text(),
                             "original user addon")

    def test_module_disable_and_restore_are_hash_guarded_and_scoped_to_isolated_core(self):
        with tempfile.TemporaryDirectory(prefix="arena-module-config-") as temp:
            workspace = Path(temp) / "workspace"
            config = workspace / "core/etc/worldserver.conf"
            config.parent.mkdir(parents=True)
            config.write_text('PortableHole.Enable = 1\nRunicCodex.Enable = 1\n')
            original = config.read_bytes()
            disabled = local.disable_modules(workspace, config, True)
            self.assertEqual(disabled["status"], "DISABLED_IN_ISOLATED_CONFIG")
            current = config.read_text()
            for key in ("PortableHole.Enable", "RunicCodex.Enable", "ArgentCommission.Enable", "VaultTerminal.Enable"):
                self.assertIn(f"{key} = 0", current)
            restored = local.restore_module_config(workspace, True)
            self.assertEqual(restored["status"], "ORIGINAL_ISOLATED_CONFIG_RESTORED")
            self.assertEqual(config.read_bytes(), original)

    def test_config_restore_refuses_operator_edits_after_disable(self):
        with tempfile.TemporaryDirectory(prefix="arena-module-config-") as temp:
            workspace = Path(temp) / "workspace"
            config = workspace / "core/worldserver.conf"
            config.parent.mkdir(parents=True)
            config.write_text("DataDir = \"/tmp/data\"\n")
            local.disable_modules(workspace, config, True)
            config.write_text(config.read_text() + "# operator edit\n")
            with self.assertRaisesRegex(ValueError, "changed since disable"):
                local.restore_module_config(workspace, True)

    def test_runtime_database_config_fails_closed_on_non_disposable_or_remote_targets(self):
        with tempfile.TemporaryDirectory(prefix="arena-config-test-") as temp:
            config = Path(temp) / "worldserver.conf"
            config.write_text('''LoginDatabaseInfo = "127.0.0.1;3306;u;p;arena_disposable_auth"\nWorldDatabaseInfo = "127.0.0.1;3306;u;p;arena_disposable_world"\nCharacterDatabaseInfo = "127.0.0.1;3306;u;p;arena_disposable_chars"\n''')
            records = local._config_db_records(config)
            self.assertEqual(records["WorldDatabaseInfo"]["database"], "arena_disposable_world")
            config.write_text(config.read_text().replace("127.0.0.1", "production.example", 1))
            with self.assertRaisesRegex(ValueError, "local disposable"):
                local._config_db_records(config)


if __name__ == "__main__":
    unittest.main()
