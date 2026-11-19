#!/usr/bin/env python3
"""Assemble a hashed, non-destructive four-module local integration handoff."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    "server/mod-portable-hole",
    "server/mod-runic-codex",
    "server/mod-argent-commission",
)
ADDONS = (
    "server/mod-portable-hole/addon/PortableHole",
    "server/mod-runic-codex/addon/RunicCodex",
    "server/mod-argent-commission/addon/ArgentCommission",
)
TESTS = (
    "tests/helpers.py",
    "tests/test_portable_hole_mysql.py",
    "tests/test_portable_hole_planner.py",
    "tests/test_portable_hole_custody_gate.py",
    "tests/test_runic_codex_mysql.py",
    "tests/test_argent_commission_mysql.py",
    "tests/test_argent_field_dressing_policy.py",
    "tests/test_argent_medic_policy.py",
    "tests/test_wotlk_addon_compat.py",
    "tests/test_gameplay_handoff.py",
    "tests/native/runic_power_registry_test.cpp",
)
DOCS = (
    "docs/17-vault-terminal-client-acceptance.md",
    "docs/18-local-runtime-handoff.md",
    "docs/23-portable-hole-physical-custody.md",
    "docs/24-integrated-gameplay-handoff.md",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True,
                        help="new output directory outside the repository")
    parser.add_argument("--ci-evidence", type=Path,
                        help="optional successful pinned four-module build/link evidence JSON for this exact HEAD")
    args = parser.parse_args()
    out = args.out.expanduser().absolute()
    if out == ROOT or ROOT in out.parents:
        parser.error("output must be outside the repository")
    if out.exists() or out.is_symlink():
        parser.error(f"output already exists; refusing to overwrite: {out}")
    out.mkdir(parents=True)
    try:
        source_head = git("rev-parse", "HEAD")
        source_branch = git("branch", "--show-current")
        status = git("status", "--porcelain=v1")
        ci_evidence = None
        if args.ci_evidence:
            if status:
                raise ValueError("CI evidence can only be bundled from a clean source checkout")
            ci_evidence = json.loads(args.ci_evidence.expanduser().resolve().read_text(encoding="utf-8"))
            if ci_evidence.get("source", {}).get("head") != source_head:
                raise ValueError("CI evidence HEAD does not match the bundle source HEAD")
            if ci_evidence.get("combined_worldserver_build_link") != "PASS":
                raise ValueError("CI evidence does not prove successful combined worldserver build/link")
        pin = json.loads((ROOT / "foundry/core-pins.json").read_text())[
            "azerothcore_wotlk"]["revision"]
        if ci_evidence is not None:
            module_names = {"mod-vault-terminal", "mod-portable-hole", "mod-runic-codex",
                            "mod-argent-commission"}
            modules = ci_evidence.get("modules")
            binary_hash = ci_evidence.get("worldserver_binary_sha256", "")
            version = ci_evidence.get("worldserver_version_command", {})
            mysql = ci_evidence.get("mysql_client", {})
            digest_is_valid = lambda value: (isinstance(value, str) and len(value) == 64
                and all(char in "0123456789abcdef" for char in value))
            if ci_evidence.get("schema") != "arena-public-core-integration-evidence/1":
                raise ValueError("CI evidence schema is missing or unsupported")
            if ci_evidence.get("source", {}).get("repository") != "ViscoBoBisco/Arena.AI":
                raise ValueError("CI evidence repository identity is not this project")
            if ci_evidence.get("core", {}).get("revision") != pin:
                raise ValueError("CI evidence core revision does not match the pinned public core")
            if not isinstance(modules, dict) or set(modules) != module_names or not all(
                    digest_is_valid(value) for value in modules.values()):
                raise ValueError("CI evidence must contain SHA-256 hashes for all four modules")
            if not digest_is_valid(binary_hash):
                raise ValueError("CI evidence is missing the linked worldserver SHA-256")
            if version.get("status") != "PASS" or not version.get("output"):
                raise ValueError("CI evidence does not prove the version command ran")
            if (mysql.get("mysql_config_version") != "8.4.13"
                    or mysql.get("libmysqlclient_dev_package") != "8.4.13-1ubuntu24.04"
                    or mysql.get("required_api_probe") != "PASS"):
                raise ValueError("CI evidence does not prove the pinned compatible MySQL API toolchain")
            if ci_evidence.get("worldserver_boot") != "NOT_RUN_NO_VERIFIED_SERVER_DATA_AND_DATABASE_BOOT_FIXTURE":
                raise ValueError("runtime-boot evidence must remain a distinct, accurately scoped result")
        included: dict[str, dict] = {}
        for module in MODULES:
            destination = out / "modules" / Path(module).name
            shutil.copytree(ROOT / module, destination)
        # Vault Terminal is source-generated rather than hand-maintained.
        with tempfile.TemporaryDirectory(prefix="arena-vault-terminal-") as temporary:
            generated = Path(temporary) / "vault-terminal"
            subprocess.run([sys.executable, str(ROOT / "tools/foundry.py"), "build",
                            str(ROOT / "specs/vault-terminal.json"), "--out", str(generated)],
                           cwd=ROOT, check=True)
            subprocess.run([sys.executable, str(ROOT / "tools/foundry.py"), "verify",
                            str(generated)], cwd=ROOT, check=True)
            shutil.copytree(generated / "server/mod-vault-terminal",
                            out / "modules/mod-vault-terminal")
            shutil.copytree(generated / "addon/VaultTerminal",
                            out / "addons/VaultTerminal")
            for sql in sorted((generated / "sql/characters").glob("*.sql")):
                target_dir = (out / "rollback/sql/characters/vault-terminal"
                              if "rollback" in sql.name.lower()
                              else out / "migrations/characters/vault-terminal")
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sql, target_dir / sql.name)
            config_dir = out / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            for path in (generated / "conf").glob("*"):
                shutil.copy2(path, config_dir / path.name)
            provenance_dir = out / "provenance"
            provenance_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(generated / "manifest.json", provenance_dir / "vault-terminal-manifest.json")
            shutil.copy2(generated / "evidence.json", provenance_dir / "vault-terminal-evidence.json")
        for addon in ADDONS:
            shutil.copytree(ROOT / addon, out / "addons" / Path(addon).name)
        for module in MODULES:
            for config in sorted((ROOT / module / "conf").glob("*.dist")):
                shutil.copy2(config, out / "config" / config.name)
            sql_dir = ROOT / module / "data/sql/characters"
            for sql in sorted(sql_dir.glob("*.sql")):
                component = Path(module).name.removeprefix("mod-")
                target = out / "migrations/characters" / component / sql.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sql, target)
        for test in TESTS:
            if (ROOT / test).is_file():
                target = out / "qualification" / Path(test).name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / test, target)
        for doc in DOCS:
            target = out / "qualification" / Path(doc).name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / doc, target)
        if ci_evidence is not None:
            evidence_target = out / "qualification/ci-integration-evidence.json"
            evidence_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.ci_evidence.expanduser().resolve(), evidence_target)
        for source in ("foundry/core-pins.json", "specs/vault-terminal.json"):
            target = out / "provenance" / Path(source).name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / source, target)
        (out / "INSTALL.md").write_text(INSTALL, encoding="utf-8")
        (out / "ROLLBACK.md").write_text(ROLLBACK, encoding="utf-8")
        (out / "PRIVATE-QUALIFICATION.md").write_text(PRIVATE, encoding="utf-8")
        (out / "DEPENDENCIES.md").write_text(DEPENDENCIES, encoding="utf-8")
        (out / "verify.py").write_text(VERIFY, encoding="utf-8")
        shutil.copy2(ROOT / "tools/check_gameplay_handoff_dependencies.py",
                     out / "check_dependencies.py")
        for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "bundle.json"):
            rel = path.relative_to(out).as_posix()
            payload = path.read_bytes()
            included[rel] = {"sha256": digest(payload), "size": len(payload)}
        metadata = {
            "schema": "arena-gameplay-integration-handoff/1",
            "source": {"repository": "ViscoBoBisco/Arena.AI", "branch": source_branch,
                       "head": source_head, "worktree_dirty": bool(status),
                       "status_porcelain": status.splitlines()},
            "core": {"repository": "https://github.com/azerothcore/azerothcore-wotlk.git",
                     "revision": pin},
            "components": ["Vault Terminal", "Portable Hole", "Runic Codex", "Argent Commission"],
            "migration_order": [
                "migrations/characters/portable-hole/001_portable_hole_warehouse.sql",
                "migrations/characters/portable-hole/002_portable_hole_vault_access.sql",
                "migrations/characters/portable-hole/003_portable_hole_projects.sql",
                "migrations/characters/runic-codex/001_runic_codex.sql",
                "migrations/characters/runic-codex/002_runic_codex_inscriptions.sql",
                "migrations/characters/runic-codex/003_runic_codex_registry_loadouts.sql",
                "migrations/characters/argent-commission/001_argent_commission.sql",
                "migrations/characters/vault-terminal/vault_terminal_claims.sql",
            ],
            "qualification": {"package_integrity": "SELF_VERIFIABLE_HASH_MANIFEST",
                              "source_compile": "PASS" if ci_evidence else "CI_GATE_CONFIGURED_RESULT_PENDING",
                              "combined_worldserver_build_and_link": "PASS" if ci_evidence else "CI_GATE_CONFIGURED_RESULT_PENDING",
                              "worldserver_boot": "NOT_ASSESSED",
                              "database_migrations": "DISPOSABLE_DB_CI_ONLY",
                              "private_campaign_party_client": "REQUIRES_LOCAL_QUALIFICATION",
                              "ci_evidence": "qualification/ci-integration-evidence.json" if ci_evidence else None},
            "safety": {"production_database_modified": False, "client_data_included": False,
                       "credentials_included": False, "physical_item_custody_enabled": False,
                       "ready_for_production": False},
            "files": included,
        }
        missing_migrations = [name for name in metadata["migration_order"] if not (out / name).is_file()]
        if missing_migrations:
            raise ValueError(f"ordered migration list references missing files: {missing_migrations}")
        if ci_evidence and metadata["core"]["revision"] != ci_evidence.get("core", {}).get("revision"):
            raise ValueError("CI evidence core pin does not match this bundle")
        (out / "bundle.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8")
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    print(json.dumps({"output": str(out), "source_head": source_head,
                      "files": len(included), "verified_core_pin": pin,
                      "production_ready": False}, indent=2, sort_keys=True))
    return 0


INSTALL = """# Disposable installation procedure\n\nThis bundle is integrity-checkable source and migration input, not a prebuilt release. Verify it first with `python3 verify.py`. Use only a fresh disposable AzerothCore clone and disposable World/Characters databases. Pin `modules/` sources to the exact core commit in `bundle.json`; if your Campaign Party/Playerbots fork differs, do not overwrite it or presume compatibility.\n\n1. Record and verify restorable backups; capture the exact core/fork revision, module/config conventions, DB versions and client build.\n2. Copy `modules/mod-portable-hole`, `modules/mod-runic-codex`, `modules/mod-argent-commission`, and `modules/mod-vault-terminal` into the disposable core's `modules/`. Enable all four in the build configuration and perform a clean core build.\n3. Configure the additive keys from `config/` using that core's actual module-config loading convention. Configure `PortableHole.Enable=1`; choose a disposable GM for ledger-only credit tests. Keep physical item transfer disabled.\n4. Apply only the numbered Characters SQL files in `bundle.json` order using protected credentials and a database name that proves it is disposable. Run the migration/idempotency tests on a separate synthetic database first. Vault Terminal's SQL only applies to its generated contract.\n5. Copy each folder under `addons/` to the build-12340 client's `Interface/AddOns/`; verify the `.toc` interface numbers.\n6. Run `qualification/` tests and hosted/pinned CI. Then perform the distinct local runtime and client checks in `qualification/18-local-runtime-handoff.md` and `qualification/17-vault-terminal-client-acceptance.md`. Never call compile or mocked protocol tests a runtime pass.\n\nRun `python3 check_dependencies.py --core-source <core> --data-dir <server-data> --client-dir <client>` to report local toolchain/source/data/client presence before building; it never edits those inputs. A client directory match does not prove build 12340. The build does not make private Campaign Party or Playerbots APIs available. Do not enable group-wide storage mutations or physical custody; the included Portable Hole account view remains read-only.\n"""
ROLLBACK = """# Disable/rollback procedure (disposable environment only)\n\n1. Stop the disposable worldserver and archive redacted logs plus the current source/config hashes.\n2. Disable all four modules in the disposable core's build/config, remove their addon folders from the test client, and rebuild/restart the disposable server.\n3. Do not run destructive rollback SQL. Retain every Characters table and row; these migrations are additive and the rollback boundary is module/addon disablement. Restore the verified pre-test database backup into a new disposable database if another clean run is needed.\n4. If startup, module loading, migration, account authorization, or item custody is uncertain, keep the server offline and restore the disposable snapshot. Never copy this procedure to production without a separately reviewed migration and rollback plan.\n"""
PRIVATE = """# Private Campaign Party / client qualification still required\n\nThe public-core pin is not evidence for a private Campaign Party/Playerbots fork. On the exact private fork, capture source commit, local patches, compiler/CMake flags, module loader/config conventions and complete World/Characters schema identities. Build all four modules together; migrate a restored disposable DB; boot with all modules enabled; verify login/module startup and each addon protocol under the real fork; exercise representative claims, read-only vault authorization, recipe advice, Runic discovery/equip/effect, Argent Honored/Exalted healing, relog, restart, duplicate requests, revoked access and failure/replay. Confirm no account-view mutation and no physical-item transfer.\n\nFor the exact legal build-12340 client, record executable/build identity and addon load logs. Verify all four TOCs, slash commands and real `CHAT_MSG_ADDON` exchanges; test with clean SavedVariables/cache and at least two test accounts for vault grants/revocation. Preserve redacted packet logs and screenshots tied to source hashes. Test the project's actual Playerbots and Campaign Party commands, permissions, party lifecycle, logout/reconnect and member removal on their own private implementation. No public CI can substitute for those APIs, client files or credentials.\n"""
DEPENDENCIES = """# Dependencies and gates\n\n- Exact public compatibility pin: recorded in `bundle.json`; private forks must be separately built and qualified.\n- Full AzerothCore build prerequisites: CMake, supported C/C++ toolchain, Boost components, MariaDB/MySQL development headers, OpenSSL, readline, zlib, bzip2 and ncurses (per upstream pinned source).\n- Disposable MySQL/MariaDB server with Characters schema; module-specific SQL remains additive. Never use production credentials.\n- Python 3.9+ for repository tooling; Lua 5.1-compatible runtime is recommended for addon syntax/contract checks.\n- Legal WoW 3.3.5a build-12340 client for UI/protocol acceptance. No DBC, MPQ, map, vmaps, mmaps or client files are included.\n- Gates: migrations and compile may pass remotely; SERVER-BOOT, CLIENT-12340, Playerbots and Campaign-Party qualification remain not assessed until the matching local environment is exercised.\n"""
VERIFY = '''from pathlib import Path\nimport hashlib, json, sys\nroot = Path(__file__).resolve().parent\ndata = json.loads((root / "bundle.json").read_text())\nerrors = []\nfor name, record in data["files"].items():\n    path = root / name\n    if not path.is_file(): errors.append(f"missing: {name}"); continue\n    actual = hashlib.sha256(path.read_bytes()).hexdigest()\n    if actual != record["sha256"]: errors.append(f"hash mismatch: {name}")\nif errors:\n    print("\\n".join(errors)); sys.exit(1)\nprint(f"verified {len(data['files'])} files; server/client runtime qualification remains {data['qualification']['worldserver_boot']}")\n'''


if __name__ == "__main__":
    raise SystemExit(main())
