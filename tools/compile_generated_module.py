#!/usr/bin/env python3
"""Compile generated AzerothCore module translation units from compile_commands.

This is a pinned-core API/linkage smoke gate, not a complete server build. It
compiles the feature TU, its module-local loader, and AzerothCore's generated
per-module loader; then checks the registration symbols connect across units.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path


def _compile(entry: dict) -> Path:
    command = entry.get("arguments")
    if command is None:
        command = shlex.split(entry["command"])
    else:
        command = list(command)
    subprocess.run(command, cwd=entry["directory"], check=True)
    try:
        output_index = command.index("-o")
        output = Path(command[output_index + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"compile command has no object output: {entry['file']}") from exc
    if not output.is_absolute():
        output = Path(entry["directory"]) / output
    if not output.is_file():
        raise ValueError(f"compiler did not create object {output}")
    return output


def _symbol(output: str, kind: str, name: str) -> bool:
    # GNU nm's demangled output appends () to functions; callers may provide
    # either the source spelling or the nm spelling.
    bare_name = name[:-2] if name.endswith("()") else name
    pattern = rf"\s{kind}\s+{re.escape(bare_name)}\(\)?$"
    return any(re.search(pattern, line) for line in output.splitlines())


def verify(build_dir: Path, core_source: Path, module: str, registration: str) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pin = json.loads((repo_root / "foundry/core-pins.json").read_text(encoding="utf-8"))
    expected_revision = pin["azerothcore_wotlk"]["revision"]
    core_source = core_source.resolve()
    revision = subprocess.run(["git", "-C", str(core_source), "rev-parse", "HEAD"],
                              check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    if revision != expected_revision:
        raise ValueError(f"core source revision {revision} does not match pinned {expected_revision}")
    database = build_dir / "compile_commands.json"
    commands = json.loads(database.read_text(encoding="utf-8"))
    module_src = (core_source / "modules" / module / "src").resolve()
    module_entries = []
    module_entry = "Add" + module.replace("-", "_") + "Scripts()"
    cmake_loader_entry = None
    for entry in commands:
        source = Path(entry["file"])
        if not source.is_absolute():
            source = Path(entry["directory"]) / source
        normalized = source.as_posix()
        try:
            source.relative_to(module_src)
            in_module_source = True
        except ValueError:
            in_module_source = False
        if in_module_source and source.suffix == ".cpp":
            module_entries.append((source, entry))
        if source.name == "ModulesLoader.cpp" and "gen_scriptloader" in normalized:
            if module_entry in source.read_text(encoding="utf-8"):
                cmake_loader_entry = entry

    local_loader = next((entry for source, entry in module_entries
                         if source.name == f"{module}_loader.cpp"), None)
    feature_entries = [entry for source, entry in module_entries
                       if source.name != f"{module}_loader.cpp"]
    if local_loader is None or len(feature_entries) != 1 or cmake_loader_entry is None:
        raise ValueError(
            f"expected one feature TU, one module loader and CMake loader for {module}; "
            f"found module TUs={[source.name for source, _ in module_entries]} "
            f"CMake loader={cmake_loader_entry is not None}")

    objects = {
        "feature": _compile(feature_entries[0]),
        "module_loader": _compile(local_loader),
        "cmake_loader": _compile(cmake_loader_entry),
    }
    symbols = {}
    for role, obj in objects.items():
        result = subprocess.run(["nm", "-C", str(obj)], check=True, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        symbols[role] = result.stdout

    checks = (
        ("feature", "T", registration),
        ("module_loader", "U", registration),
        ("module_loader", "T", module_entry),
        ("cmake_loader", "U", module_entry),
    )
    for role, kind, symbol in checks:
        if not _symbol(symbols[role], kind, symbol):
            raise ValueError(f"{role} object missing expected nm symbol `{kind} {symbol}`")
    for role, obj in objects.items():
        print(f"COMPILED {role}: {obj}")
    print(f"LINKAGE PASS: {registration} -> {module_entry} -> AzerothCore loader")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--core-source", type=Path, required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--registration", required=True)
    args = parser.parse_args(argv)
    try:
        verify(args.build_dir, args.core_source, args.module, args.registration)
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"compile qualification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
