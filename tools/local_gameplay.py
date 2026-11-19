#!/usr/bin/env python3
"""Safe, isolated local qualification/bootstrap for the four maintained gameplay modules.

Default execution is non-destructive: it runs repository tests, audits/assembles the
handoff, inventories dependencies, and reports gates. Building uses a fresh pinned
public-core clone. Database creation and client-addon writes require distinct explicit
flags and are confined to newly-created, named disposable schemas / a marked test client.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = ROOT / "foundry/core-pins.json"
REPOSITORY = "ViscoBoBisco/Arena.AI"
WORKSPACE_SCHEMA = "arena-local-gameplay-workspace/1"
CLIENT_MARKER = "Arena.AI disposable TEST CLIENT; addon writes explicitly authorized.\n"
COMPONENTS = (
    ("mod-portable-hole", "AddPortableHoleScripts"),
    ("mod-runic-codex", "AddRunicCodexScripts"),
    ("mod-argent-commission", "AddArgentCommissionScripts"),
)
ADDONS = ("PortableHole", "RunicCodex", "ArgentCommission", "VaultTerminal")
MIGRATIONS = (
    "migrations/characters/portable-hole/001_portable_hole_warehouse.sql",
    "migrations/characters/portable-hole/002_portable_hole_vault_access.sql",
    "migrations/characters/portable-hole/003_portable_hole_projects.sql",
    "migrations/characters/runic-codex/001_runic_codex.sql",
    "migrations/characters/runic-codex/002_runic_codex_inscriptions.sql",
    "migrations/characters/runic-codex/003_runic_codex_registry_loadouts.sql",
    "migrations/characters/argent-commission/001_argent_commission.sql",
    "migrations/characters/vault-terminal/vault_terminal_claims.sql",
)
CMAKE_FLAGS = (
    "-DWITHOUT_GIT=1", "-DNOJEM=1", "-DUSE_COREPCH=1", "-DUSE_SCRIPTPCH=1",
    "-DTOOLS_BUILD=none", "-DWITH_WARNINGS=0", "-DMODULES=static",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_pin() -> str:
    return json.loads(PIN_FILE.read_text(encoding="utf-8"))["azerothcore_wotlk"]["revision"]


def source_snapshot() -> tuple[str, int]:
    names = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           cwd=ROOT, check=True, capture_output=True).stdout.split(b"\0")
    files: dict[str, str] = {}
    for raw in names:
        if not raw:
            continue
        name = raw.decode("utf-8", errors="surrogateescape")
        path = ROOT / name
        if path.is_symlink():
            files[name] = "SYMLINK:" + os.readlink(path)
        elif path.is_file():
            mode = path.stat().st_mode & 0o777
            files[name] = f"FILE:{mode:o}:{sha256_file(path)}"
        else:
            files[name] = "DELETED"
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest(), len(files)


def mysql_api_report() -> dict[str, Any]:
    guidance = (
        "Ubuntu 24.04 amd64: install the verified Oracle MySQL 8.4 client API used by CI:\n"
        "  sudo apt-get update && sudo apt-get install -y ca-certificates curl gnupg\n"
        "  curl -fsSL https://repo.mysql.com/RPM-GPG-KEY-mysql-2025 -o /tmp/mysql-repo.asc\n"
        "  gpg --dearmor < /tmp/mysql-repo.asc | sudo tee /usr/share/keyrings/mysql-apt.gpg >/dev/null\n"
        "  echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/mysql-apt.gpg] https://repo.mysql.com/apt/ubuntu/ noble mysql-8.4-lts' | sudo tee /etc/apt/sources.list.d/mysql-8.4-lts.list\n"
        "  sudo apt-get update && sudo apt-get install -y build-essential cmake git libboost-all-dev libmysqlclient-dev=8.4.13-1ubuntu24.04 libssl-dev libreadline-dev zlib1g-dev libbz2-dev libncurses5-dev\n"
        "The pinned AzerothCore core requires APIs absent from MariaDB client headers. The preflight "
        "compiles and links a probe for mysql_stmt_bind_named_param and MYSQL_OPT_SSL_MODE; package "
        "version alone is not trusted. Rerun `python3 tools/local_gameplay.py preflight` before building.")
    mysql_config = shutil.which("mysql_config") or shutil.which("mariadb_config")
    cc = shutil.which("cc") or shutil.which("gcc")
    if not mysql_config:
        return {"status": "MISSING", "mysql_config": None, "compiler": cc,
                "reason": "mysql_config was not found", "install_guidance": guidance}
    if not cc:
        return {"status": "MISSING", "mysql_config": mysql_config, "compiler": None,
                "reason": "a C compiler (cc/gcc) is required for the client API probe",
                "install_guidance": guidance}
    try:
        version_result = subprocess.run([mysql_config, "--version"], check=False,
                                        capture_output=True, text=True, timeout=15)
        version = version_result.stdout.strip()
        if version_result.returncode or not re.match(r"^8\.4\.", version):
            return {"status": "INCOMPATIBLE", "mysql_config": mysql_config, "compiler": cc,
                    "version": version or None,
                    "reason": "the pinned AzerothCore toolchain requires the Oracle MySQL 8.4 client API",
                    "install_guidance": guidance}
        cflags = subprocess.run([mysql_config, "--cflags"], check=True, capture_output=True,
                                text=True, timeout=15).stdout
        libs = subprocess.run([mysql_config, "--libs"], check=True, capture_output=True,
                              text=True, timeout=15).stdout
        probe = ("#include <mysql.h>\n"
                 "int main(void) {\n"
                 "  enum mysql_ssl_mode mode = SSL_MODE_REQUIRED;\n"
                 "  MYSQL *connection = 0; MYSQL_STMT *statement = 0; MYSQL_BIND *bindings = 0;\n"
                 "  (void)mysql_options(connection, MYSQL_OPT_SSL_MODE, &mode);\n"
                 "  return mysql_stmt_bind_named_param(statement, bindings, 0, 0);\n}\n")
        with tempfile.TemporaryDirectory(prefix="arena-mysql-api-") as temp:
            source = Path(temp) / "mysql_api_probe.c"
            binary = Path(temp) / "mysql_api_probe"
            source.write_text(probe, encoding="utf-8")
            command = [cc, "-Werror", *shlex.split(cflags), str(source),
                       *shlex.split(libs), "-o", str(binary)]
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
        if result.returncode:
            return {"status": "INCOMPATIBLE", "mysql_config": mysql_config, "compiler": cc,
                    "version": version, "probe_command": command,
                    "reason": (result.stderr or result.stdout).strip()[-4000:],
                    "install_guidance": guidance}
        return {"status": "PASS", "mysql_config": mysql_config, "compiler": cc,
                "version": version, "probe": "COMPILE_AND_LINK_PASS",
                "probe_command": command}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "INCOMPATIBLE", "mysql_config": mysql_config, "compiler": cc,
                "reason": str(exc), "install_guidance": guidance}


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def pinned_build_prerequisites(dependencies: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [name for name in ("git", "cmake", "cxx")
               if dependencies.get(name, {}).get("status") != "PASS"]
    if dependencies.get("mysql_client_api", {}).get("status") != "PASS":
        missing.append("mysql_client_api")
    return not missing, missing


def prerequisite_report(data_dir: Path | None = None,
                        client_dir: Path | None = None) -> dict[str, Any]:
    programs = {
        "python": {"status": "PASS" if sys.version_info >= (3, 9) else "FAIL",
                   "version": sys.version.split()[0]},
        "git": shutil.which("git"), "cmake": shutil.which("cmake"),
        "cxx": next((shutil.which(name) for name in ("c++", "g++", "clang++")
                     if shutil.which(name)), None),
        "mysql": shutil.which("mysql") or shutil.which("mariadb"),
        "mysql_config": shutil.which("mysql_config") or shutil.which("mariadb_config"),
        "mysqldump": shutil.which("mysqldump") or shutil.which("mariadb-dump"),
    }
    programs["mysql_client_api"] = mysql_api_report()
    for name in ("git", "cmake", "cxx", "mysql", "mysql_config", "mysqldump"):
        programs[name] = {"path": programs[name],
                          "status": "PASS" if programs[name] else "MISSING"}
    for name in ("boost", "openssl", "readline", "zlib", "bzip2", "ncurses"):
        # CMake's exact pinned-core configure step is authoritative for development libraries.
        programs[f"lib_{name}"] = {"status": "CHECKED_BY_PINNED_CMAKE_CONFIGURE"}
    if data_dir:
        data = data_dir.expanduser().resolve()
        data_checks = {name: (data / name).is_dir() for name in ("dbc", "maps", "vmaps", "mmaps")}
        programs["server_data"] = {"path": str(data), **data_checks,
                                   "status": "PASS" if all(data_checks.values()) else "MISSING_REQUIRED_DATA"}
    else:
        programs["server_data"] = {"status": "NOT_SUPPLIED"}
    if client_dir:
        client = client_dir.expanduser().resolve()
        exe = next((client / name for name in ("Wow.exe", "wow.exe", "WoW.exe")
                    if (client / name).is_file()), None)
        programs["test_client"] = {
            "path": str(client), "executable": str(exe) if exe else None,
            "build_12340": "MUST_BE_VERIFIED_LOCALLY",
            "status": "PRESENT_BUILD_UNVERIFIED" if exe else "MISSING_EXECUTABLE",
        }
    else:
        programs["test_client"] = {"status": "NOT_SUPPLIED"}
    try:
        import pymysql  # type: ignore  # noqa: F401
        programs["pymysql"] = {"status": "PASS"}
    except ImportError:
        programs["pymysql"] = {"status": "OPTIONAL_MISSING",
                                "install": "python3 -m pip install -r requirements-world-qualification.txt"}
    return programs


def audit_package() -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    expected_sources = ["examples/vault-terminal/server/mod-vault-terminal"] + [
        f"server/{name}" for name, _ in COMPONENTS]
    source_dirs = {path: (ROOT / path).is_dir() for path in expected_sources}
    for path, exists in source_dirs.items():
        if not exists:
            errors.append(f"missing module source: {path}")
    addon_roots = [ROOT / "examples/vault-terminal/addon/VaultTerminal"] + [
        ROOT / f"server/mod-{name.removeprefix('mod-')}/addon/{name.removeprefix('mod-').replace('-', '')}"
        for name, _ in COMPONENTS]
    # Use explicit addon directory names; do not infer on-disk paths from module slugs.
    addon_roots = [ROOT / "examples/vault-terminal/addon/VaultTerminal",
                   ROOT / "server/mod-portable-hole/addon/PortableHole",
                   ROOT / "server/mod-runic-codex/addon/RunicCodex",
                   ROOT / "server/mod-argent-commission/addon/ArgentCommission"]
    interfaces: dict[str, str | None] = {}
    for addon in addon_roots:
        toc = next(addon.glob("*.toc"), None) if addon.is_dir() else None
        interface = None
        if toc:
            match = re.search(r"^##\s*Interface:\s*(\d+)\s*$", toc.read_text(encoding="utf-8"), re.M)
            interface = match.group(1) if match else None
        interfaces[addon.name] = interface
        if not addon.is_dir() or interface != "30300":
            errors.append(f"missing addon or WotLK Interface 30300 TOC: {addon}")

    actual_migrations = []
    for module in ("portable-hole", "runic-codex", "argent-commission"):
        actual_migrations.extend(sorted(
            p.relative_to(ROOT).as_posix()
            for p in (ROOT / f"server/mod-{module}/data/sql/characters").glob("*.sql")))
    generated_sql = sorted((ROOT / "examples/vault-terminal/sql").glob("*.sql"))
    # The generated migration's canonical output name is fixed by the source spec.
    if not generated_sql or not (ROOT / "specs/vault-terminal.json").is_file():
        errors.append("Vault Terminal source spec or SQL generation inputs are missing")
    expected_module_migrations = [
        "server/mod-portable-hole/data/sql/characters/001_portable_hole_warehouse.sql",
        "server/mod-portable-hole/data/sql/characters/002_portable_hole_vault_access.sql",
        "server/mod-portable-hole/data/sql/characters/003_portable_hole_projects.sql",
        "server/mod-runic-codex/data/sql/characters/001_runic_codex.sql",
        "server/mod-runic-codex/data/sql/characters/002_runic_codex_inscriptions.sql",
        "server/mod-runic-codex/data/sql/characters/003_runic_codex_registry_loadouts.sql",
        "server/mod-argent-commission/data/sql/characters/001_argent_commission.sql",
    ]
    if actual_migrations != expected_module_migrations:
        errors.append(f"unexpected module migration inventory/order: {actual_migrations}")
    if len(MIGRATIONS) != len(set(MIGRATIONS)):
        errors.append("handoff migration order contains duplicate paths")
    registry = ROOT / "server/mod-runic-codex/src/RunicPowerRegistry.h"
    readme = ROOT / "server/mod-runic-codex/README.md"
    if registry.is_file() and readme.is_file():
        count_match = re.search(r"std::array<PowerDefinition,\s*(\d+)>\s+POWERS", registry.read_text())
        readme_text = readme.read_text(encoding="utf-8")
        documented_powers = re.findall(r"^- \*\*[^*]+\*\*:", readme_text, re.M)
        if not count_match or int(count_match.group(1)) != len(documented_powers):
            errors.append("Runic Codex registry power count and README feature register disagree")
    workflow = (ROOT / ".github/workflows/azerothcore-compile.yml").read_text(encoding="utf-8")
    if "/tmp/azerothcore-build/src/server/apps/worldserver" not in workflow:
        errors.append("pinned build workflow is missing the verified worldserver binary path")
    if "/tmp/azerothcore-build/bin/worldserver" in workflow:
        errors.append("pinned build workflow retains the stale worldserver binary path")
    for name, _ in COMPONENTS:
        if name not in workflow:
            errors.append(f"pinned combined build does not install {name}")
    for module, key in (("mod-runic-codex", "RunicCodex.Enable"),
                        ("mod-argent-commission", "ArgentCommission.Enable"),
                        ("mod-portable-hole", "PortableHole.Enable")):
        config_file = ROOT / f"server/{module}/conf/mod-{module.removeprefix('mod-')}.conf.dist"
        source_file = ROOT / f"server/{module}/src/{'RunicCodex.cpp' if module == 'mod-runic-codex' else 'ArgentCommission.cpp' if module == 'mod-argent-commission' else 'PortableHole.cpp'}"
        if not config_file.is_file() or key not in config_file.read_text(encoding="utf-8"):
            errors.append(f"missing documented disable/master-switch config: {key}")
        if module != "mod-portable-hole" and key not in source_file.read_text(encoding="utf-8"):
            errors.append(f"module source does not enforce disable/master-switch config: {key}")
    register = json.loads((ROOT / "designs/gameplay-systems-register.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in register["systems"]}
    for key in ("portable-hole-commodity-vault", "runic-codex", "faction-operations-argent",
                "public-core-runtime-qualification"):
        item = by_id.get(key)
        if not item:
            errors.append(f"missing feature register entry: {key}")
        elif item["implementation_state"] == "SYNTHETIC":
            warnings.append(f"feature status may be stale (still SYNTHETIC): {key}")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors,
            "warnings": warnings, "module_sources": source_dirs,
            "addon_interfaces": interfaces, "module_migrations": actual_migrations,
            "handoff_migration_order": list(MIGRATIONS)}


def write_log(workspace: Path, name: str, content: str) -> str:
    path = workspace / "logs" / f"{name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")
    return str(path.relative_to(workspace))


def write_json_atomic(path: Path, value: dict[str, Any], *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{uuid.uuid4().hex[:8]}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if mode is not None:
        os.chmod(temporary, mode)
    os.replace(temporary, path)


def run_logged(workspace: Path, name: str, argv: list[str], *, cwd: Path = ROOT,
               timeout: int | None = None, input_file: Path | None = None,
               output_file: Path | None = None) -> dict[str, Any]:
    try:
        if input_file:
            with input_file.open("rb") as stream:
                result = subprocess.run(argv, cwd=cwd, stdin=stream, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False)
            output = result.stdout.decode("utf-8", errors="replace")
        elif output_file:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with output_file.open("wb") as stream:
                result = subprocess.run(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                        timeout=timeout, check=False)
            output = output_file.read_text(encoding="utf-8", errors="replace")
            status = "PASS" if result.returncode == 0 else "FAIL"
            return {"name": name, "command": argv, "status": status,
                    "exit_code": result.returncode, "log": str(output_file)}
        else:
            result = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False)
            output = result.stdout
        rel = write_log(workspace, name, output)
        return {"name": name, "command": argv, "status": "PASS" if result.returncode == 0 else "FAIL",
                "exit_code": result.returncode, "log": rel}
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        rel = write_log(workspace, name, str(output) + f"\nTIMED OUT after {timeout}s\n")
        return {"name": name, "command": argv, "status": "TIMEOUT", "exit_code": None, "log": rel}
    except (OSError, subprocess.SubprocessError) as exc:
        rel = write_log(workspace, name, f"could not execute: {exc}\n")
        return {"name": name, "command": argv, "status": "BLOCKED", "exit_code": None, "log": rel}


def create_workspace(path: Path) -> dict[str, Any]:
    target = path.expanduser().absolute()
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise ValueError("workspace path and its parents must not traverse symlinks")
    resolved = target.resolve()
    if inside(resolved, ROOT) or inside(ROOT, resolved):
        raise ValueError("workspace must be outside and disjoint from the repository")
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing workspace: {target}")
    target.mkdir(parents=True)
    source_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout.strip()
    snapshot_sha256, snapshot_files = source_snapshot()
    metadata = {"schema": WORKSPACE_SCHEMA, "run_id": uuid.uuid4().hex[:12],
                "created_utc": utc_now(), "repository": REPOSITORY,
                "source_head": source_head, "source_snapshot_sha256": snapshot_sha256,
                "source_snapshot_file_count": snapshot_files, "core_pin": source_pin()}
    (target / ".arena-local-integration.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "logs").mkdir()
    return metadata


def validate_workspace(path: Path) -> tuple[Path, dict[str, Any]]:
    requested = path.expanduser().absolute()
    if any(part.is_symlink() for part in (requested, *requested.parents)):
        raise ValueError("workspace path and its parents must not traverse symlinks")
    workspace = requested.resolve()
    if inside(workspace, ROOT) or inside(ROOT, workspace):
        raise ValueError("workspace must be outside and disjoint from the repository")
    meta_path = workspace / ".arena-local-integration.json"
    if not meta_path.is_file() or meta_path.is_symlink():
        raise ValueError("missing Arena local-workspace marker; refusing to operate")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if metadata.get("schema") != WORKSPACE_SCHEMA or metadata.get("repository") != REPOSITORY:
        raise ValueError("workspace marker does not identify this integration procedure")
    return workspace, metadata


def validate_resume_workspace(path: Path) -> tuple[Path, dict[str, Any]]:
    workspace, metadata = validate_workspace(path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()
    snapshot_sha256, snapshot_files = source_snapshot()
    if metadata.get("source_head") != head or metadata.get("core_pin") != source_pin():
        raise ValueError("resume refused: repository HEAD or pinned core differs from this workspace marker")
    if (metadata.get("source_snapshot_sha256") != snapshot_sha256
            or metadata.get("source_snapshot_file_count") != snapshot_files):
        raise ValueError("resume refused: source tree hash differs from the workspace's recorded source")
    handoff_dir = workspace / "handoff"
    if handoff_dir.exists() or handoff_dir.is_symlink():
        if handoff_dir.is_symlink() or not handoff_dir.is_dir():
            raise ValueError("resume refused: handoff path is not a regular directory")
    bundle = handoff_dir / "bundle.json"
    if bundle.exists() or bundle.is_symlink():
        if bundle.is_symlink() or not bundle.is_file():
            raise ValueError("resume refused: handoff bundle path is not a regular file")
        metadata_bundle = json.loads(bundle.read_text(encoding="utf-8"))
        source = metadata_bundle.get("source", {})
        if (source.get("head") != head
                or source.get("worktree_snapshot_sha256") != snapshot_sha256
                or metadata_bundle.get("core", {}).get("revision") != source_pin()):
            raise ValueError("resume refused: existing handoff provenance does not match this checkout")
        verifier = workspace / "handoff/verify.py"
        if not verifier.is_file():
            raise ValueError("resume refused: existing handoff verifier is missing")
        checked = subprocess.run([sys.executable, str(verifier)], cwd=verifier.parent,
                                 capture_output=True, text=True, check=False)
        if checked.returncode:
            raise ValueError("resume refused: existing handoff integrity check failed: "
                             + (checked.stdout + checked.stderr)[-2000:])
        try:
            verify_handoff_provenance(bundle.parent, metadata)
            existing_manifest = json.loads(bundle.read_text(encoding="utf-8"))
            embedded_evidence = bundle.parent / "qualification/ci-integration-evidence.json"
            with tempfile.TemporaryDirectory(prefix=".resume-handoff-check-", dir=workspace) as temp:
                rebuilt = Path(temp) / "generated"
                command = [sys.executable, str(ROOT / "tools/build_gameplay_handoff.py"),
                           "--out", str(rebuilt)]
                if embedded_evidence.is_file():
                    command.extend(["--ci-evidence", str(embedded_evidence)])
                generated = subprocess.run(command, cwd=ROOT, capture_output=True,
                                           text=True, timeout=600, check=False)
                if generated.returncode:
                    raise ValueError("source handoff could not be reproduced: "
                                     + (generated.stdout + generated.stderr)[-2000:])
                regenerated = json.loads((rebuilt / "bundle.json").read_text(encoding="utf-8"))
                if regenerated.get("files") != existing_manifest.get("files"):
                    raise ValueError("existing handoff file hashes differ from the reproducible source bundle")
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            raise ValueError(f"resume refused: existing handoff evidence invalid: {exc}") from exc
    core = workspace / "core"
    if core.exists():
        if core.is_symlink() or not core.is_dir() or not (core / ".git").exists():
            raise ValueError("resume refused: existing isolated core is not a regular Git checkout")
        revision = subprocess.run(["git", "-C", str(core), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=False)
        if revision.returncode or revision.stdout.strip() != source_pin():
            raise ValueError("resume refused: isolated public-core checkout is not at the pinned revision")
    return workspace, metadata


def _check_client(client_dir: Path, workspace: Path, affirmative: bool) -> Path:
    client = client_dir.expanduser().resolve()
    if inside(client, ROOT) or inside(ROOT, client) or inside(client, workspace) or inside(workspace, client):
        raise ValueError("test client must be separate from both the checkout and the isolated workspace")
    marker = client / "ARENA_TEST_CLIENT.txt"
    if not marker.is_file() or marker.is_symlink() or marker.read_text(encoding="utf-8") != CLIENT_MARKER:
        raise ValueError(f"test client needs an explicit {marker} marker containing exactly:\n{CLIENT_MARKER!r}")
    if not affirmative:
        raise ValueError("client writes require --confirm-test-client-write")
    executable = next((client / name for name in ("Wow.exe", "wow.exe", "WoW.exe")
                       if (client / name).is_file()), None)
    if not executable:
        raise ValueError("designated test client directory has no WoW executable")
    addon_root = client / "Interface/AddOns"
    if not addon_root.is_dir() or addon_root.is_symlink():
        raise ValueError("test client Interface/AddOns directory must already exist and not be a symlink")
    return client


def _tree_hash(path: Path) -> str:
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"expected regular addon directory: {path}")
    files = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"addon source contains symlink: {item}")
        if item.is_file():
            files[item.relative_to(path).as_posix()] = sha256_file(item)
    raw = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def verify_handoff_provenance(bundle_dir: Path, metadata: dict[str, Any]) -> dict[str, Any] | None:
    bundle_path = bundle_dir / "bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    source = bundle.get("source", {})
    if (source.get("head") != metadata["source_head"]
            or source.get("worktree_snapshot_sha256") != metadata["source_snapshot_sha256"]
            or bundle.get("core", {}).get("revision") != metadata["core_pin"]):
        raise ValueError("handoff source/core provenance does not match the marked workspace")
    evidence_path = bundle_dir / "qualification/ci-integration-evidence.json"
    if not evidence_path.is_file():
        return None
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    expected_modules = {"mod-vault-terminal", "mod-portable-hole", "mod-runic-codex",
                        "mod-argent-commission"}
    modules = evidence.get("modules")
    digest_ok = lambda value: (isinstance(value, str) and len(value) == 64
                               and all(c in "0123456789abcdef" for c in value))
    if (evidence.get("schema") != "arena-public-core-integration-evidence/1"
            or evidence.get("source", {}).get("repository") != REPOSITORY
            or evidence.get("source", {}).get("head") != metadata["source_head"]
            or evidence.get("core", {}).get("revision") != metadata["core_pin"]
            or evidence.get("combined_worldserver_build_link") != "PASS"
            or not isinstance(modules, dict) or set(modules) != expected_modules
            or not all(digest_ok(value) for value in modules.values())
            or not digest_ok(evidence.get("worldserver_binary_sha256"))
            or evidence.get("worldserver_version_command", {}).get("status") != "PASS"
            or not evidence.get("worldserver_version_command", {}).get("output")
            or evidence.get("mysql_client", {}).get("mysql_config_version") != "8.4.13"
            or evidence.get("mysql_client", {}).get("libmysqlclient_dev_package") != "8.4.13-1ubuntu24.04"
            or evidence.get("mysql_client", {}).get("required_api_probe") != "PASS"
            or evidence.get("worldserver_boot") != "NOT_RUN_NO_VERIFIED_SERVER_DATA_AND_DATABASE_BOOT_FIXTURE"):
        raise ValueError("embedded CI evidence is incomplete, failed, or not bound to this exact source/pin")
    for module, expected in modules.items():
        root = bundle_dir / "modules" / module
        if not root.is_dir() or _tree_hash(root) != expected:
            raise ValueError(f"embedded CI module hash mismatch: {module}")
    record = bundle.get("files", {}).get("qualification/ci-integration-evidence.json", {})
    if record.get("sha256") != sha256_file(evidence_path):
        raise ValueError("embedded CI evidence hash does not match the handoff manifest")
    return evidence


def install_addons(workspace: Path, client_dir: Path, affirmative: bool,
                   client_build: str | None = None) -> dict[str, Any]:
    client = _check_client(client_dir, workspace, affirmative)
    handoff = workspace / "handoff"
    install_manifest = workspace / "client-install.json"
    if install_manifest.exists():
        raise ValueError("this workspace already has a client install record; use recover-client first")
    backup_root = workspace / "backups/client-original"
    quarantine = workspace / "backups/client-arena-addons"
    stage_root = workspace / "staging/client-addons"
    backup_root.mkdir(parents=True)
    quarantine.mkdir(parents=True)
    stage_root.mkdir(parents=True)
    addon_root = client / "Interface/AddOns"
    plans = {}
    # Validate and stage all four addons before touching any client path.
    for name in ADDONS:
        source = handoff / "addons" / name
        target = addon_root / name
        if not source.is_dir() or source.is_symlink():
            raise ValueError(f"handoff addon missing or unsafe: {source}")
        if target.is_symlink():
            raise ValueError(f"refusing to replace addon symlink: {target}")
        existed = target.exists()
        if existed and not target.is_dir():
            raise ValueError(f"addon destination is not a directory: {target}")
        original_hash = None
        if existed:
            backup = backup_root / name
            shutil.copytree(target, backup)
            original_hash = _tree_hash(backup)
            if _tree_hash(target) != original_hash:
                raise ValueError(f"backup verification failed for existing addon: {name}")
        staged = stage_root / name
        shutil.copytree(source, staged)
        installed_hash = _tree_hash(staged)
        if installed_hash != _tree_hash(source):
            raise ValueError(f"staged addon verification failed: {name}")
        plans[name] = {"target": target, "staged": staged, "existed_before": existed,
                       "original_sha256": original_hash, "installed_sha256": installed_hash}
    records = {name: {"existed_before": plan["existed_before"],
                      "original_sha256": plan["original_sha256"],
                      "installed_sha256": plan["installed_sha256"]}
               for name, plan in plans.items()}
    manifest = {"schema": "arena-test-client-addon-install/1", "client": str(client),
                "client_build_operator_assertion": client_build,
                "created_utc": utc_now(), "addons": records,
                "installed_addons": [], "state": "INSTALLING"}
    # Persist recovery metadata before the first client rename so interruption cannot
    # leave an untracked partial install.
    write_json_atomic(install_manifest, manifest)
    for name, plan in plans.items():
        target = plan["target"]
        if target.exists():
            current_hash = _tree_hash(target)
            if not plan["existed_before"] or current_hash != plan["original_sha256"]:
                raise ValueError(f"client addon changed after staging; refusing swap: {name}")
            os.replace(target, quarantine / name)
        os.replace(plan["staged"], target)
        manifest["installed_addons"].append(name)
        write_json_atomic(install_manifest, manifest)
    manifest["state"] = "INSTALLED"
    manifest["installed_utc"] = utc_now()
    write_json_atomic(install_manifest, manifest)
    return manifest


def recover_client(workspace: Path, client_dir: Path, affirmative: bool) -> dict[str, Any]:
    client = _check_client(client_dir, workspace, affirmative)
    path = workspace / "client-install.json"
    if not path.is_file():
        raise ValueError("no client install manifest exists in this workspace")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "arena-test-client-addon-install/1" or manifest.get("client") != str(client):
        raise ValueError("client install manifest does not match this test-client path")
    state = manifest.get("state")
    if state not in ("INSTALLING", "INSTALLED", "RECOVERING"):
        raise ValueError(f"client addon state is {state}; refusing ambiguous recovery")
    addon_root = client / "Interface/AddOns"
    recovery = workspace / "recovery/client-current"
    recovery.mkdir(parents=True, exist_ok=True)
    for name, record in manifest["addons"].items():
        target = addon_root / name
        if target.is_symlink():
            raise ValueError(f"client addon {name} became a symlink; manual review required")
        if target.exists():
            current_hash = _tree_hash(target)
            allowed = {record["installed_sha256"]}
            if record["existed_before"]:
                allowed.add(record["original_sha256"])
            if current_hash not in allowed:
                raise ValueError(f"installed addon {name} changed after bootstrap; manual review required")
        if record["existed_before"]:
            backup = workspace / "backups/client-original" / name
            if not backup.is_dir() or _tree_hash(backup) != record["original_sha256"]:
                raise ValueError(f"original addon backup failed its hash check: {name}")
    manifest["state"] = "RECOVERING"
    manifest.setdefault("recovered_addons", [])
    write_json_atomic(path, manifest)
    for name, record in manifest["addons"].items():
        target = addon_root / name
        if target.exists():
            current_hash = _tree_hash(target)
            if record["existed_before"] and current_hash == record["original_sha256"]:
                pass
            elif current_hash == record["installed_sha256"]:
                destination = recovery / name
                if destination.exists():
                    destination = recovery / f"{name}-{uuid.uuid4().hex[:8]}"
                os.replace(target, destination)
            else:
                raise ValueError(f"client addon {name} changed during recovery; manual review required")
        if record["existed_before"] and not target.exists():
            backup = workspace / "backups/client-original" / name
            stage = addon_root / f".{name}.arena-restore-{uuid.uuid4().hex[:8]}"
            shutil.copytree(backup, stage)
            os.replace(stage, target)
        if name not in manifest["recovered_addons"]:
            manifest["recovered_addons"].append(name)
        write_json_atomic(path, manifest)
    manifest["state"] = "RECOVERED_PRE_INSTALL_CLIENT_STATE"
    manifest["recovered_utc"] = utc_now()
    write_json_atomic(path, manifest)
    return manifest


def disable_modules(workspace: Path, runtime_config: Path, consent: bool) -> dict[str, Any]:
    if not consent:
        raise ValueError("module disable requires --confirm-isolated-config-write")
    core = workspace / "core"
    config = runtime_config.expanduser().resolve()
    if not inside(config, core) or not config.is_file() or config.is_symlink():
        raise ValueError("module disable may edit only a regular config inside this workspace's isolated public-core clone")
    manifest_path = workspace / "module-config-state.json"
    if manifest_path.exists():
        raise ValueError("module-config-state.json already exists; restore before repeating disable")
    original = config.read_bytes()
    backup = workspace / "backups/runtime-config" / config.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(original)
    os.chmod(backup, 0o600)
    text = original.decode("utf-8")
    settings = ("PortableHole.Enable", "RunicCodex.Enable", "ArgentCommission.Enable", "VaultTerminal.Enable")
    for key in settings:
        pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*).*$", re.M)
        if pattern.search(text):
            text = pattern.sub(r"\g<1>0", text)
        else:
            text += ("\n" if text and not text.endswith("\n") else "") + f"{key} = 0\n"
    temporary = config.with_name(config.name + f".arena-disable-{uuid.uuid4().hex[:8]}")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, config.stat().st_mode & 0o777)
    os.replace(temporary, config)
    state = {"schema": "arena-module-disable/1", "config_path": str(config),
             "backup_path": str(backup.relative_to(workspace)),
             "original_sha256": hashlib.sha256(original).hexdigest(),
             "disabled_sha256": sha256_file(config), "disabled_keys": list(settings),
             "status": "DISABLED_IN_ISOLATED_CONFIG", "created_utc": utc_now()}
    manifest_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return state


def restore_module_config(workspace: Path, consent: bool) -> dict[str, Any]:
    if not consent:
        raise ValueError("config recovery requires --confirm-isolated-config-write")
    path = workspace / "module-config-state.json"
    if not path.is_file():
        raise ValueError("no isolated module-disable record exists")
    state = json.loads(path.read_text(encoding="utf-8"))
    config = Path(state["config_path"]).resolve()
    if not inside(config, workspace / "core") or not config.is_file() or sha256_file(config) != state["disabled_sha256"]:
        raise ValueError("isolated config changed since disable; refusing to overwrite operator edits")
    backup = workspace / state["backup_path"]
    if not backup.is_file() or sha256_file(backup) != state["original_sha256"]:
        raise ValueError("config backup hash mismatch; refusing recovery")
    temporary = config.with_name(config.name + f".arena-restore-{uuid.uuid4().hex[:8]}")
    shutil.copy2(backup, temporary)
    os.chmod(temporary, config.stat().st_mode & 0o777)
    os.replace(temporary, config)
    state["status"] = "ORIGINAL_ISOLATED_CONFIG_RESTORED"
    state["restored_utc"] = utc_now()
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return state

def _mysql_credentials() -> dict[str, str]:
    names = ("ARENA_DISPOSABLE_MYSQL_HOST", "ARENA_DISPOSABLE_MYSQL_PORT",
             "ARENA_DISPOSABLE_MYSQL_USER", "ARENA_DISPOSABLE_MYSQL_PASSWORD")
    missing = [name for name in names if name not in os.environ]
    if missing:
        raise ValueError("disposable DB creation requires environment variables: " + ", ".join(missing))
    values = {name: os.environ[name] for name in names}
    host = values[names[0]].strip().lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("database writes are restricted to a local MySQL server; remote hosts are refused")
    if not values[names[2]] or "\n" in values[names[2]] or "\r" in values[names[2]]:
        raise ValueError("invalid local MySQL username")
    if "\n" in values[names[3]] or "\r" in values[names[3]]:
        raise ValueError("MySQL password may not contain line breaks")
    if not values[names[1]].isdigit() or not 1 <= int(values[names[1]]) <= 65535:
        raise ValueError("invalid MySQL port")
    return {"host": host, "port": values[names[1]], "user": values[names[2]],
            "password": values[names[3]]}


def _mysql_defaults(credentials: dict[str, str]):
    # MySQL requires this option file to be the first option. Keep it outside the artifact,
    # mode 0600, and remove it as soon as the operation is complete.
    fd, filename = tempfile.mkstemp(prefix="arena-mysql-", suffix=".cnf")
    os.chmod(filename, 0o600)
    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\t", "\\t").replace('"', '\\"')
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("[client]\n")
        for key, value in credentials.items():
            stream.write(f'{key}="{escape(value)}"\n')
    return Path(filename)


def apply_disposable_migrations(workspace: Path, metadata: dict[str, Any],
                                consent: bool) -> dict[str, Any]:
    if not consent:
        raise ValueError("database creation requires --create-disposable-db")
    mysql = shutil.which("mysql") or shutil.which("mariadb")
    if not mysql:
        raise ValueError("mysql/mariadb client is missing")
    credentials = _mysql_credentials()
    db = f"arena_disposable_{metadata['run_id']}_characters"
    if len(db) > 64 or not re.fullmatch(r"arena_disposable_[a-z0-9_]+", db):
        raise ValueError("generated disposable schema name failed safety validation")
    cnf = _mysql_defaults(credentials)
    created = False
    logs = []
    try:
        create = run_logged(workspace, "create-disposable-characters-db",
                            [mysql, f"--defaults-extra-file={cnf}", "--batch", "--skip-column-names",
                             "--execute", f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"])
        logs.append(create)
        if create["status"] != "PASS":
            raise RuntimeError("new disposable schema creation failed (no existing database was overwritten)")
        created = True
        marker_sql = ("CREATE TABLE `arena_local_handoff_provenance` ("
                      "`run_id` varchar(32) NOT NULL,`source_head` char(40) NOT NULL,"
                      "`core_pin` char(40) NOT NULL,`created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                      "PRIMARY KEY (`run_id`)) ENGINE=InnoDB;\n"
                      f"INSERT INTO `arena_local_handoff_provenance` (`run_id`,`source_head`,`core_pin`) "
                      f"VALUES ('{metadata['run_id']}','{metadata['source_head']}','{metadata['core_pin']}');\n")
        marker_path = workspace / "logs/disposable-schema-marker.sql"
        marker_path.write_text(marker_sql, encoding="utf-8")
        result = run_logged(workspace, "mark-disposable-characters-db",
                            [mysql, f"--defaults-extra-file={cnf}", f"--database={db}"], input_file=marker_path)
        logs.append(result)
        if result["status"] != "PASS":
            raise RuntimeError("failed to mark the newly-created disposable schema")
        applied = []
        for relative in MIGRATIONS:
            sql = workspace / "handoff" / relative
            if not sql.is_file():
                raise FileNotFoundError(f"handoff migration missing: {relative}")
            result = run_logged(workspace, "migration-" + Path(relative).stem,
                                [mysql, f"--defaults-extra-file={cnf}", f"--database={db}"],
                                input_file=sql)
            logs.append(result)
            if result["status"] != "PASS":
                raise RuntimeError(f"migration failed: {relative}; schema retained for inspection")
            applied.append({"path": relative, "sha256": sha256_file(sql)})
        dump = shutil.which("mysqldump") or shutil.which("mariadb-dump")
        backup = workspace / "backups/database/characters-after-module-migrations.sql"
        result = run_logged(workspace, "backup-disposable-characters-db",
                            [dump, f"--defaults-extra-file={cnf}", "--single-transaction", db],
                            output_file=backup)
        logs.append(result)
        if result["status"] != "PASS" or not backup.is_file() or backup.stat().st_size == 0:
            raise RuntimeError("disposable schema backup failed; do not use or drop the schema")
        db_record = {"status": "MODULE_MIGRATIONS_PASS", "schema": db,
                     "server_host": credentials["host"], "created_by_this_run": created,
                     "schema_is_full_acore_characters_db": False,
                     "migrations": applied, "backup": str(backup.relative_to(workspace)),
                     "backup_sha256": sha256_file(backup), "logs": logs,
                     "recovery": "Use recover-db to restore this snapshot into a new disposable schema; original is never dropped."}
        (workspace / "disposable-db.json").write_text(json.dumps(db_record, indent=2, sort_keys=True) + "\n")
        return db_record
    except Exception as exc:
        return {"status": "FAIL" if created else "BLOCKED", "schema": db if created else None,
                "created_by_this_run": created,
                "reason": str(exc), "logs": logs,
                "safety": "Any schema created by this run is retained; this tool never drops or alters an existing schema."}
    finally:
        cnf.unlink(missing_ok=True)


def recover_database(workspace: Path, consent: bool) -> dict[str, Any]:
    if not consent:
        raise ValueError("database recovery requires --restore-to-new-disposable-db")
    manifest_path = workspace / "disposable-db.json"
    if not manifest_path.is_file():
        raise ValueError("no disposable database manifest exists")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backup = workspace / manifest.get("backup", "")
    if not manifest.get("schema", "").startswith("arena_disposable_") or not backup.is_file():
        raise ValueError("source schema or backup is not an Arena disposable artifact")
    if sha256_file(backup) != manifest.get("backup_sha256"):
        raise ValueError("backup hash mismatch; refusing restore")
    mysql = shutil.which("mysql") or shutil.which("mariadb")
    if not mysql:
        raise ValueError("mysql/mariadb client is missing")
    credentials = _mysql_credentials()
    run_id = uuid.uuid4().hex[:12]
    target = f"arena_disposable_{run_id}_restore"
    cnf = _mysql_defaults(credentials)
    try:
        create = subprocess.run([mysql, f"--defaults-extra-file={cnf}", "--batch", "--skip-column-names",
                                 "--execute", f"CREATE DATABASE `{target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        if create.returncode:
            raise RuntimeError("new restore schema creation failed; no existing database was modified")
        with backup.open("rb") as stream:
            restored = subprocess.run([mysql, f"--defaults-extra-file={cnf}", f"--database={target}"],
                                      stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, check=False)
        log = write_log(workspace, "restore-new-disposable-schema", restored.stdout)
        record = {"status": "PASS" if restored.returncode == 0 else "FAIL",
                  "restored_to_new_schema": target, "source_schema_retained": manifest["schema"],
                  "exit_code": restored.returncode, "log": log}
        (workspace / "database-recovery.json").write_text(json.dumps(record, indent=2) + "\n")
        return record
    finally:
        cnf.unlink(missing_ok=True)


def build_pinned_core(workspace: Path, timeout: int) -> dict[str, Any]:
    pin = source_pin()
    core = workspace / "core"
    build = workspace / "build"
    source_url = "https://github.com/azerothcore/azerothcore-wotlk.git"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()
    snapshot_sha256, _ = source_snapshot()
    module_names = ("mod-vault-terminal", *(name for name, _ in COMPONENTS))
    evidence_path = workspace / "local-build-evidence.json"
    expected_binary = build / "src/server/apps/worldserver"
    if evidence_path.is_file():
        saved = json.loads(evidence_path.read_text(encoding="utf-8"))
        if (saved.get("source_head") != head or saved.get("source_snapshot_sha256") != snapshot_sha256
                or saved.get("core_revision") != pin
                or saved.get("worldserver") != str(expected_binary)
                or not expected_binary.is_file()
                or not os.access(expected_binary, os.X_OK)
                or saved.get("worldserver_sha256") != sha256_file(expected_binary)):
            return {"status": "BLOCKED", "reason": "saved build evidence or binary is incompatible with this source/workspace"}
        for module in module_names:
            root = core / "modules" / module
            if not root.is_dir() or _tree_hash(root) != saved.get("modules", {}).get(module):
                return {"status": "BLOCKED", "reason": f"saved module tree changed or is missing: {module}"}
        version = run_logged(workspace, "worldserver-version-resume",
                             [str(expected_binary), "--version"], timeout=30)
        if version["status"] != "PASS":
            return {"status": "BLOCKED", "reason": "saved worldserver no longer passes --version", "version_command": version}
        saved["resumed"] = True
        saved["resume_version_check"] = version
        return saved

    steps = []
    if core.exists():
        if core.is_symlink() or not core.is_dir() or not (core / ".git").exists():
            return {"status": "BLOCKED", "reason": "existing core path is not a regular Git checkout"}
        current = subprocess.run(["git", "-C", str(core), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=False)
        if current.returncode or current.stdout.strip() != pin:
            return {"status": "BLOCKED", "reason": "existing core checkout is not at the pinned revision"}
        rev = current.stdout.strip()
        steps.append({"name": "reuse-pinned-core", "status": "PASS", "revision": rev})
    else:
        clone = run_logged(workspace, "clone-pinned-core", ["git", "clone", "--filter=blob:none",
                           "--no-checkout", source_url, str(core)], timeout=timeout)
        steps.append(clone)
        if clone["status"] != "PASS":
            return {"status": "FAIL", "steps": steps}
        checkout = run_logged(workspace, "checkout-core-pin", ["git", "-C", str(core), "checkout", "--detach", pin], timeout=timeout)
        steps.append(checkout)
        if checkout["status"] != "PASS":
            return {"status": "FAIL", "steps": steps}
        rev = subprocess.run(["git", "-C", str(core), "rev-parse", "HEAD"], check=True,
                             capture_output=True, text=True).stdout.strip()
        if rev != pin:
            return {"status": "FAIL", "reason": f"core pin mismatch: expected {pin}, got {rev}", "steps": steps}
    for module in module_names:
        source = workspace / "handoff/modules" / module
        target = core / "modules" / module
        if not source.is_dir():
            return {"status": "BLOCKED", "reason": f"handoff module source is missing: {module}", "steps": steps}
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_dir() or _tree_hash(target) != _tree_hash(source):
                return {"status": "BLOCKED", "reason": f"existing isolated module differs from handoff: {module}", "steps": steps}
        else:
            shutil.copytree(source, target)
    if build.is_symlink():
        return {"status": "BLOCKED", "reason": "isolated build directory may not be a symlink", "steps": steps}
    cache = build / "CMakeCache.txt"
    if cache.exists():
        cache_text = cache.read_text(encoding="utf-8", errors="replace")
        if f"CMAKE_HOME_DIRECTORY:INTERNAL={core}" not in cache_text:
            return {"status": "BLOCKED", "reason": "existing CMake build cache belongs to a different source directory", "steps": steps}
    configure = run_logged(workspace, "configure-pinned-core",
                           ["cmake", "-S", str(core), "-B", str(build), *CMAKE_FLAGS], timeout=timeout)
    steps.append(configure)
    if configure["status"] != "PASS":
        return {"status": "FAIL", "steps": steps}
    for module, registration in (("mod-vault-terminal", "AddVaultTerminalScripts"), *COMPONENTS):
        step = run_logged(workspace, f"compile-{module}",
                          [sys.executable, str(ROOT / "tools/compile_generated_module.py"),
                           "--build-dir", str(build), "--core-source", str(core),
                           "--module", module, "--registration", registration], timeout=timeout)
        steps.append(step)
        if step["status"] != "PASS":
            return {"status": "FAIL", "steps": steps}
    linked = run_logged(workspace, "link-worldserver",
                        ["cmake", "--build", str(build), "--target", "worldserver", "--parallel", "2"],
                        timeout=timeout)
    steps.append(linked)
    binary = expected_binary
    if linked["status"] != "PASS" or not binary.is_file() or not os.access(binary, os.X_OK):
        return {"status": "FAIL", "steps": steps, "binary": str(binary)}
    version = run_logged(workspace, "worldserver-version", [str(binary), "--version"], timeout=30)
    steps.append(version)
    modules_digest = {module: _tree_hash(core / "modules" / module) for module in module_names}
    result = {"status": "PASS" if version["status"] == "PASS" else "FAIL",
              "source_head": head, "source_snapshot_sha256": snapshot_sha256,
              "core_revision": rev, "modules": modules_digest,
              "worldserver": str(binary), "worldserver_sha256": sha256_file(binary),
              "version_command": version, "steps": steps,
              "boot": "NOT_ASSESSED_UNTIL_EXPLICIT_RUNTIME_OPTIONS_SUPPLIED",
              "client": "NOT_ASSESSED"}
    write_json_atomic(evidence_path, result)
    return result


def stage_runtime_config(workspace: Path, source: Path) -> Path:
    core = workspace / "core"
    candidate = source.expanduser().resolve()
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("runtime config source must be an existing regular file")
    if inside(candidate, core):
        return candidate
    target = core / "arena-local-worldserver.conf"
    if target.exists() or target.is_symlink():
        raise ValueError("isolated runtime config path already exists; refusing to overwrite")
    shutil.copy2(candidate, target)
    os.chmod(target, 0o600)
    return target


def _config_db_records(config: Path) -> dict[str, dict[str, str]]:
    text = config.read_text(encoding="utf-8", errors="replace")
    wanted = ("LoginDatabaseInfo", "WorldDatabaseInfo", "CharacterDatabaseInfo")
    records = {}
    for name in wanted:
        match = re.search(rf"^\s*{name}\s*=\s*\"([^\"]+)\"", text, re.M)
        if not match:
            raise ValueError(f"runtime config lacks {name}")
        fields = match.group(1).split(";")
        if len(fields) != 5:
            raise ValueError(f"{name} must be host;port;user;password;database")
        host, port, user, _password, db = fields
        if host.lower() not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError(f"{name} must target a local disposable database host")
        if not db.startswith("arena_disposable_"):
            raise ValueError(f"{name} database name must start arena_disposable_")
        records[name] = {"host": host, "port": port, "user": user, "database": db}
    return records


def attempt_bounded_boot(workspace: Path, data_dir: Path, runtime_config: Path,
                         seconds: int) -> dict[str, Any]:
    core = workspace / "core"
    binary = workspace / "build/src/server/apps/worldserver"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return {"status": "NOT_ASSESSED", "reason": "isolated linked worldserver is unavailable"}
    data = data_dir.expanduser().resolve()
    missing = [name for name in ("dbc", "maps", "vmaps", "mmaps") if not (data / name).is_dir()]
    if missing:
        return {"status": "NOT_ASSESSED", "reason": "verified server data missing", "missing": missing}
    config = runtime_config.expanduser().resolve()
    if not inside(config, core) or not config.is_file():
        return {"status": "REFUSED", "reason": "runtime config must be a file inside the isolated public-core clone"}
    try:
        dbs = _config_db_records(config)
        config_text = config.read_text(encoding="utf-8", errors="replace")
        data_match = re.search(r'^\s*DataDir\s*=\s*"([^"]+)"', config_text, re.M)
        if not data_match:
            raise ValueError("runtime config lacks quoted DataDir")
        configured_data = Path(data_match.group(1))
        if not configured_data.is_absolute():
            configured_data = core / configured_data
        if configured_data.resolve() != data:
            raise ValueError("runtime config DataDir does not match the explicitly supplied server data directory")
    except (OSError, ValueError) as exc:
        return {"status": "REFUSED", "reason": str(exc)}
    log_path = workspace / "logs/bounded-worldserver-boot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        proc = subprocess.Popen([str(binary), "-c", str(config)], cwd=core,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + max(10, min(seconds, 180))
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            current = log_path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"world initialized", current, re.I):
                ready = True
                break
            time.sleep(1)
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=15)
            except (subprocess.TimeoutExpired, OSError):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=5)
    content = log_path.read_text(encoding="utf-8", errors="replace")
    errors = re.findall(r"^.*(?:ERROR|FATAL).*?$", content, re.I | re.M)
    return {"status": "PASS" if ready else "FAIL",
            "ready_marker": "World initialized" if ready else None,
            "process_exit_after_shutdown": proc.returncode,
            "db_names": {key: value["database"] for key, value in dbs.items()},
            "worldserver_log": str(log_path.relative_to(workspace)),
            "error_lines": errors[-20:],
            "live_module_requests": "NOT_ASSESSED_REQUIRES_BUILD_12340_CLIENT_AND_OPERATOR_SESSION"}


def zip_handoff(workspace: Path) -> dict[str, Any]:
    import zipfile
    handoff = workspace / "handoff"
    archive = workspace / "arena-gameplay-handoff.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(handoff.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(handoff).as_posix())
    return {"path": str(archive), "sha256": sha256_file(archive), "size": archive.stat().st_size}


def render_report(report: dict[str, Any]) -> str:
    lines = ["# Local four-module qualification", "",
             f"- Source revision: `{report['source']['head']}`",
             f"- AzerothCore pin: `{report['core_pin']}`",
             f"- Generated UTC: `{report['generated_utc']}`", "",
             "## Gate summary", "", "| Gate | Status | Detail |", "|---|---|---|"]
    for name, item in report["gates"].items():
        status = item.get("status", "NOT_ASSESSED") if isinstance(item, dict) else str(item)
        detail = item.get("reason", item.get("summary", "")) if isinstance(item, dict) else ""
        lines.append(f"| {name} | {status} | {str(detail).replace('|', '/')} |")
    handoff = report.get("handoff")
    if handoff:
        lines += ["", "## Source/evidence provenance", "",
                  f"- Worktree snapshot SHA-256: `{handoff['source'].get('worktree_snapshot_sha256')}`",
                  f"- Handoff manifest SHA-256: `{handoff['bundle_sha256']}`",
                  f"- Public core pin: `{handoff['core'].get('revision')}`"]
        if report.get("ci_evidence"):
            lines.append(f"- Verified CI worldserver SHA-256: `{report['ci_evidence']['worldserver_binary_sha256']}`")
            lines.append("- Boot/client/private-fork runtime qualification remains not assessed.")
        lines += ["", "### Ordered migrations and hashes", ""]
        for migration in handoff.get("migration_order", []):
            record = handoff.get("file_hashes", {}).get(migration, {})
            lines.append(f"- `{migration}` — SHA-256 `{record.get('sha256', 'MISSING')}`")
        qual = handoff.get("qualification", {})
        safety = handoff.get("safety", {})
        lines += ["", "### Known limitations", "",
                  f"- Server boot: `{qual.get('worldserver_boot', 'NOT_ASSESSED')}`.",
                  f"- Private Campaign Party/client: `{qual.get('private_campaign_party_client', 'REQUIRES_LOCAL_QUALIFICATION')}`.",
                  f"- Physical-item custody enabled: `{safety.get('physical_item_custody_enabled', False)}`.",
                  f"- Production ready: `{safety.get('ready_for_production', False)}`."]
    lines += ["", "## Required local acceptance", "",
              "1. Run the pinned public-core build in this isolated workspace; preserve the recorded SHA-256s.",
              "2. Initialize matching disposable auth/world/characters databases from the exact pinned core; use only `arena_disposable_*` schemas.",
              "3. Verify `DataDir` has DBC, maps, vmaps, and mmaps; boot with `--attempt-public-boot` and confirm `World initialized`.",
              "4. Install addons only to a marked disposable build-12340 client with the explicit write flag; exercise each protocol and retain redacted logs.",
              "5. Run `recover-client` to restore pre-install addon folders, or `recover-db` to restore a database snapshot into a new disposable schema. Original schemas are never dropped.",
              "6. Keep physical item custody and private Campaign Party/Playerbots qualification out of scope until their separate acceptance gates pass.", ""]
    return "\n".join(lines)


def qualification_failures(args: argparse.Namespace, gates: dict[str, Any]) -> list[str]:
    failures = []
    for gate in ("package_audit", "repository_tests", "source_handoff", "handoff_integrity"):
        if gates.get(gate, {}).get("status") != "PASS":
            failures.append(gate)
    build_status = gates.get("pinned_four_module_build_link", {}).get("status")
    if args.require_build and build_status != "PASS":
        failures.append("pinned_four_module_build_link")
    elif build_status in ("FAIL", "TIMEOUT", "BLOCKED"):
        failures.append("pinned_four_module_build_link")
    if args.create_disposable_db and gates.get("disposable_module_migrations", {}).get("status") != "MODULE_MIGRATIONS_PASS":
        failures.append("disposable_module_migrations")
    if args.install_addons and gates.get("test_client_addons", {}).get("status") != "PASS":
        failures.append("test_client_addons")
    if args.attempt_public_boot and gates.get("public_server_boot", {}).get("status") != "PASS":
        failures.append("public_server_boot")
    if args.ci_evidence and gates.get("ci_source_compile_link", {}).get("status") != "PASS":
        failures.append("ci_source_compile_link")
    return sorted(set(failures))


def qualify(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    if args.resume:
        workspace, metadata = validate_resume_workspace(workspace)
    else:
        metadata = create_workspace(workspace)
        workspace = workspace.resolve()
    report: dict[str, Any] = {"schema": "arena-local-gameplay-qualification/1",
                              "generated_utc": utc_now(),
                              "source": {"repository": REPOSITORY,
                                         "head": metadata["source_head"],
                                         "worktree_snapshot_sha256": metadata["source_snapshot_sha256"],
                                         "worktree_snapshot_file_count": metadata["source_snapshot_file_count"],
                                         "worktree_dirty": bool(subprocess.run(
                                             ["git", "status", "--porcelain"], cwd=ROOT,
                                             check=True, capture_output=True, text=True).stdout.strip())},
                              "core_pin": metadata["core_pin"], "workspace": str(workspace),
                              "gates": {}, "notes": [
                                  "This tool never modifies a Campaign Party/private core checkout.",
                                  "Only an isolated public core clone is built.",
                                  "No runtime pass is inferred from compile, SQL, or model tests."]}
    report["gates"]["preflight"] = prerequisite_report(args.data_dir, args.test_client)
    audit = audit_package()
    report["gates"]["package_audit"] = audit
    report["gates"]["repository_tests"] = run_logged(workspace, "repository-make-check", ["make", "check"], timeout=args.test_timeout)
    bundle_out = workspace / "handoff"
    bundle_out = workspace / "handoff"
    existing_evidence = bundle_out / "qualification/ci-integration-evidence.json"
    if bundle_out.exists():
        if not args.resume:
            build_bundle = {"status": "BLOCKED", "reason": "handoff already exists; use --resume to verify and reuse it"}
        else:
            build_bundle = {"status": "PASS", "resumed": True,
                            "bundle": str(bundle_out / "bundle.json")}
            if args.ci_evidence:
                evidence_path = args.ci_evidence.expanduser().resolve()
                if (not evidence_path.is_file() or evidence_path.is_symlink()
                        or not existing_evidence.is_file()
                        or sha256_file(evidence_path) != sha256_file(existing_evidence)):
                    build_bundle = {"status": "BLOCKED",
                                    "reason": "resume evidence differs from the evidence embedded in the verified handoff"}
    else:
        builder_command = [sys.executable, str(ROOT / "tools/build_gameplay_handoff.py"),
                           "--out", str(bundle_out)]
        if args.ci_evidence:
            evidence_path = args.ci_evidence.expanduser().resolve()
            if not evidence_path.is_file() or evidence_path.is_symlink():
                build_bundle = {"status": "BLOCKED", "reason": "--ci-evidence must name a regular JSON evidence file"}
            else:
                builder_command.extend(["--ci-evidence", str(evidence_path)])
                build_bundle = run_logged(workspace, "build-source-handoff", builder_command,
                                          timeout=args.test_timeout)
        else:
            build_bundle = run_logged(workspace, "build-source-handoff", builder_command,
                                      timeout=args.test_timeout)
    report["gates"]["source_handoff"] = build_bundle
    if build_bundle["status"] == "PASS":
        report["gates"]["handoff_integrity"] = run_logged(workspace, "verify-source-handoff",
                              [sys.executable, str(bundle_out / "verify.py")], cwd=bundle_out,
                              timeout=args.test_timeout)
        bundle_manifest_path = bundle_out / "bundle.json"
        if report["gates"]["handoff_integrity"]["status"] == "PASS" and bundle_manifest_path.is_file():
            try:
                embedded_evidence = verify_handoff_provenance(bundle_out, metadata)
                bundle_manifest = json.loads(bundle_manifest_path.read_text(encoding="utf-8"))
                report["handoff"] = {
                    "bundle_sha256": sha256_file(bundle_manifest_path),
                    "source": bundle_manifest.get("source"),
                    "core": bundle_manifest.get("core"),
                    "migration_order": bundle_manifest.get("migration_order"),
                    "qualification": bundle_manifest.get("qualification"),
                    "safety": bundle_manifest.get("safety"),
                    "file_hashes": bundle_manifest.get("files", {}),
                }
                if embedded_evidence is not None:
                    report["ci_evidence"] = embedded_evidence
                    report["ci_evidence_sha256"] = sha256_file(bundle_out / "qualification/ci-integration-evidence.json")
                    report["gates"]["ci_source_compile_link"] = {"status": "PASS",
                        "source_head": embedded_evidence["source"]["head"],
                        "worldserver_binary_sha256": embedded_evidence["worldserver_binary_sha256"]}
                else:
                    report["gates"]["ci_source_compile_link"] = {"status": "NOT_ASSESSED",
                        "reason": "no matching CI evidence supplied or embedded in the verified bundle"}
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                report["gates"]["handoff_integrity"] = {"status": "FAIL", "reason": str(exc)}
        report["handoff_archive"] = zip_handoff(workspace)
    else:
        report["gates"]["handoff_integrity"] = {"status": "BLOCKED", "reason": "bundle generation failed or an existing bundle was refused"}
    dependencies = prerequisite_report(args.data_dir, args.test_client)
    build_prereqs, missing_build_tools = pinned_build_prerequisites(dependencies)
    mysql_api_status = dependencies["mysql_client_api"]["status"]
    if args.skip_build:
        report["gates"]["pinned_four_module_build_link"] = {"status": "NOT_ASSESSED", "reason": "--skip-build"}
    elif not build_prereqs:
        reasons = list(missing_build_tools)
        if mysql_api_status != "PASS":
            reasons.append("mysql_client_api")
        report["gates"]["pinned_four_module_build_link"] = {
            "status": "BLOCKED" if args.require_build else "NOT_ASSESSED",
            "reason": "required build prerequisites are unavailable" if args.require_build
                      else "optional build prerequisites are unavailable",
            "missing": reasons, "mysql_client_api": dependencies["mysql_client_api"]}
    elif build_bundle["status"] != "PASS":
        report["gates"]["pinned_four_module_build_link"] = {"status": "BLOCKED", "reason": "handoff module sources unavailable"}
    else:
        report["gates"]["pinned_four_module_build_link"] = build_pinned_core(workspace, args.build_timeout)
    if args.create_disposable_db:
        try:
            report["gates"]["disposable_module_migrations"] = apply_disposable_migrations(
                workspace, metadata, args.create_disposable_db)
        except (ValueError, OSError) as exc:
            report["gates"]["disposable_module_migrations"] = {"status": "BLOCKED", "reason": str(exc)}
    else:
        report["gates"]["disposable_module_migrations"] = {
            "status": "NOT_ASSESSED", "reason": "requires --create-disposable-db and local ARENA_DISPOSABLE_MYSQL_* variables"}
    if args.install_addons:
        try:
            report["gates"]["test_client_addons"] = install_addons(
                workspace, args.test_client, args.confirm_test_client_write, args.client_build)
            report["gates"]["test_client_addons"]["status"] = "PASS"
        except (ValueError, OSError) as exc:
            report["gates"]["test_client_addons"] = {"status": "BLOCKED", "reason": str(exc)}
    else:
        report["gates"]["test_client_addons"] = {"status": "NOT_ASSESSED", "reason": "no client writes requested"}
    if args.attempt_public_boot:
        if not args.data_dir or not args.runtime_config:
            report["gates"]["public_server_boot"] = {
                "status": "BLOCKED", "reason": "--attempt-public-boot requires --data-dir and --runtime-config"}
        elif report["gates"]["pinned_four_module_build_link"].get("status") != "PASS":
            report["gates"]["public_server_boot"] = {"status": "BLOCKED", "reason": "requested boot requires a successful isolated build"}
        else:
            try:
                runtime_config = stage_runtime_config(workspace, args.runtime_config)
                report["gates"]["public_server_boot"] = attempt_bounded_boot(
                    workspace, args.data_dir, runtime_config, args.boot_timeout)
                report["gates"]["public_server_boot"]["runtime_config_staged_inside_workspace"] = str(runtime_config.relative_to(workspace))
            except (OSError, ValueError) as exc:
                report["gates"]["public_server_boot"] = {"status": "BLOCKED", "reason": str(exc)}
    else:
        report["gates"]["public_server_boot"] = {"status": "NOT_ASSESSED", "reason": "boot is opt-in and requires disposable DB config and verified data"}
    failures = qualification_failures(args, report["gates"])
    build_status = report["gates"]["pinned_four_module_build_link"].get("status")
    report["failures"] = failures
    if failures:
        report["final_status"] = "QUALIFICATION_FAILED"
    elif build_status == "PASS" or report["gates"].get("ci_source_compile_link", {}).get("status") == "PASS":
        report["final_status"] = "PUBLIC_COMPILE_LINK_QUALIFIED_RUNTIME_GATES_SEPARATE"
    else:
        report["final_status"] = "SOURCE_AND_STATIC_QUALIFICATION_ONLY"
    write_json_atomic(workspace / "qualification.json", report)
    (workspace / "QUALIFICATION.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"workspace": str(workspace), "final_status": report["final_status"],
                      "exit_code": 1 if failures else 0, "failures": report["failures"],
                      "gates": {key: value.get("status", "NOT_ASSESSED")
                                if isinstance(value, dict) else "RECORDED"
                                for key, value in report["gates"].items()},
                      "report": str(workspace / "QUALIFICATION.md")}, indent=2))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="read-only prerequisite and data inventory")
    pre.add_argument("--data-dir", type=Path)
    pre.add_argument("--test-client", type=Path)
    audit = sub.add_parser("audit", help="audit module/addon/migration/register integration")
    q = sub.add_parser("qualify", help="tests, handoff, isolated build, and opt-in local gates")
    q.add_argument("--workspace", required=True, type=Path)
    q.add_argument("--resume", action="store_true",
                   help="resume only a marked workspace whose source, pin, handoff and isolated core hashes still match")
    q.add_argument("--ci-evidence", type=Path,
                   help="successful integration-evidence.json from CI for this exact clean source revision")
    q.add_argument("--require-build", action="store_true",
                   help="treat missing pinned-build prerequisites as a requested-operation failure")
    q.add_argument("--data-dir", type=Path)
    q.add_argument("--test-client", type=Path)
    q.add_argument("--install-addons", action="store_true")
    q.add_argument("--confirm-test-client-write", action="store_true")
    q.add_argument("--client-build", choices=("12340",),
                   help="operator attests selected test client build; no binary-version detector is bundled")
    q.add_argument("--create-disposable-db", action="store_true",
                   help="explicitly create a unique local arena_disposable_* Characters schema and apply migrations")
    q.add_argument("--attempt-public-boot", action="store_true")
    q.add_argument("--runtime-config", type=Path)
    q.add_argument("--skip-build", action="store_true")
    q.add_argument("--test-timeout", type=int, default=900)
    q.add_argument("--build-timeout", type=int, default=7200)
    q.add_argument("--boot-timeout", type=int, default=90)
    rec = sub.add_parser("recover-client", help="restore original test-client addon folders from verified backups")
    rec.add_argument("--workspace", type=Path, required=True)
    rec.add_argument("--test-client", type=Path, required=True)
    rec.add_argument("--confirm-test-client-write", action="store_true")
    db = sub.add_parser("recover-db", help="restore a verified disposable DB snapshot into a new schema")
    db.add_argument("--workspace", type=Path, required=True)
    db.add_argument("--restore-to-new-disposable-db", action="store_true")
    disable = sub.add_parser("disable-modules", help="turn off the four module switches in isolated worldserver config")
    disable.add_argument("--workspace", type=Path, required=True)
    disable.add_argument("--runtime-config", type=Path, required=True)
    disable.add_argument("--confirm-isolated-config-write", action="store_true")
    restore = sub.add_parser("restore-modules", help="restore the verified isolated worldserver config backup")
    restore.add_argument("--workspace", type=Path, required=True)
    restore.add_argument("--confirm-isolated-config-write", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            print(json.dumps(prerequisite_report(args.data_dir, args.test_client), indent=2, sort_keys=True))
            return 0
        if args.command == "audit":
            result = audit_package()
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "PASS" else 1
        if args.command == "qualify":
            if args.require_build and args.skip_build:
                parser.error("--require-build cannot be combined with --skip-build")
            if args.install_addons and (not args.test_client or args.client_build != "12340"
                                        or not args.confirm_test_client_write):
                parser.error("addon install requires --test-client, --client-build 12340, and --confirm-test-client-write")
            return qualify(args)
        workspace, _ = validate_workspace(args.workspace)
        if args.command == "recover-client":
            result = recover_client(workspace, args.test_client, args.confirm_test_client_write)
        elif args.command == "recover-db":
            result = recover_database(workspace, args.restore_to_new_disposable_db)
        elif args.command == "disable-modules":
            result = disable_modules(workspace, args.runtime_config, args.confirm_isolated_config_write)
        else:
            result = restore_module_config(workspace, args.confirm_isolated_config_write)
        print(json.dumps(result, indent=2, sort_keys=True))
        success_statuses = {"PASS", "DISABLED_IN_ISOLATED_CONFIG", "ORIGINAL_ISOLATED_CONFIG_RESTORED",
                            "RECOVERED_PRE_INSTALL_CLIENT_STATE"}
        return 0 if result.get("status") in success_statuses or result.get("state") in success_statuses else 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"local integration blocked safely: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
