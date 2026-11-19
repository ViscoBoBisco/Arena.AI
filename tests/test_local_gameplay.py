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
