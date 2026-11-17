"""Behavioural tests for the DBC/patch toolchain.

Runs with the standard library only:  python3 -m unittest discover -s tests -v

Every fixture is synthesised here (a WDBC header, int32 records, a string block), so the suite
needs no WoW client files and can run in CI on any platform.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
DBC_TOOL = TOOLS / "dbc_tool.py"
PREFLIGHT = TOOLS / "preflight.sh"
MANIFEST = TOOLS / "manifest.py"
SELFTEST = TOOLS / "selftest.py"


def build_dbc(path: Path, field_count: int, rows, strings: bytes = b"\0") -> Path:
    """Write a minimal valid WDBC file."""
    record_size = field_count * 4
    header = struct.pack("<4sIIII", b"WDBC", len(rows), field_count, record_size, len(strings))
    body = b"".join(struct.pack("<" + "i" * field_count, *row) for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + body + strings)
    return path


def run(*args, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True
    )


class DbcToolSelftest(unittest.TestCase):
    def test_selftest_passes(self):
        result = run(SELFTEST)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("selftest: PASS", result.stdout)


class Preflight(unittest.TestCase):
    def _fixture(self, tmp: Path, drift: bool = False, dangling: bool = False):
        client = tmp / "client"
        server = tmp / "server"
        build_dbc(client / "ItemDisplayInfo.dbc", 25, [(41179, *([0] * 24))])
        build_dbc(client / "Item.dbc", 8, [(800000, 2, 7, -1, 1, 999999 if dangling else 41179, 17, 3)])
        build_dbc(client / "SpellIcon.dbc", 2, [(1, 0)])
        build_dbc(client / "SpellVisual.dbc", 2, [(1, 0)])
        build_dbc(client / "Spell.dbc", 234, [(800000, *([0] * 130), 1, 0, 1, *([0] * 100))])
        for name in ("Item.dbc", "ItemDisplayInfo.dbc", "SpellIcon.dbc", "SpellVisual.dbc", "Spell.dbc"):
            server.mkdir(exist_ok=True)
            (server / name).write_bytes((client / name).read_bytes())
        if drift:
            build_dbc(server / "Item.dbc", 8, [(800000, 2, 7, -1, 1, 41179, 13, 3)])
        return client, server

    def test_passes_when_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, server = self._fixture(Path(tmp))
            result = subprocess.run([str(PREFLIGHT), str(client), str(server)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("preflight: PASS", result.stdout)

    def test_detects_client_server_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, server = self._fixture(Path(tmp), drift=True)
            result = subprocess.run([str(PREFLIGHT), str(client), str(server)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("differs from server copy", result.stdout)

    def test_rejects_missing_server_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, _ = self._fixture(Path(tmp))
            result = subprocess.run([str(PREFLIGHT), str(client), str(Path(tmp) / "typo")],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("does not exist", result.stderr)

    def test_detects_dangling_display_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, server = self._fixture(Path(tmp), dangling=True)
            result = subprocess.run([str(PREFLIGHT), str(client), str(server)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("FAIL  Item.dbc.DisplayInfoID", result.stdout)


class Manifest(unittest.TestCase):
    def _stage(self, tmp: Path) -> Path:
        stage = tmp / "stage"
        (stage / "DBFilesClient").mkdir(parents=True)
        (stage / "DBFilesClient" / "Item.dbc").write_bytes(b"item-table-fixture")
        (stage / "DBFilesClient" / "Spell.dbc").write_bytes(b"spell-table-fixture")
        (tmp / "patch-4.MPQ").write_bytes(b"not-a-real-archive")
        return stage

    def test_roundtrip_and_determinism(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stage = self._stage(tmp)
            out = tmp / "manifest.json"
            r = run(MANIFEST, "make", "--staging", stage, "--version", "1.2.3",
                    "--patch", tmp / "patch-4.MPQ", "--out", out)
            self.assertEqual(r.returncode, 0, r.stderr)

            data = json.loads(out.read_text())
            self.assertEqual(data["content_version"], "1.2.3")
            self.assertEqual(data["summary"]["files"], 2)
            self.assertEqual(len(data["patch"]["sha256"]), 64)
            self.assertNotIn("generated_utc", data)

            first = out.read_bytes()
            r = run(MANIFEST, "make", "--staging", stage, "--version", "1.2.3",
                    "--patch", tmp / "patch-4.MPQ", "--out", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(first, out.read_bytes(), "manifest output must be deterministic")

            r = run(MANIFEST, "verify", "--manifest", out, "--staging", stage,
                    "--patch", tmp / "patch-4.MPQ")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stdout)

    def test_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stage = self._stage(tmp)
            out = tmp / "manifest.json"
            run(MANIFEST, "make", "--staging", stage, "--version", "1.2.3",
                "--patch", tmp / "patch-4.MPQ", "--out", out)

            (stage / "DBFilesClient" / "Item.dbc").write_bytes(b"edited")
            (stage / "DBFilesClient" / "New.dbc").write_bytes(b"added")
            r = run(MANIFEST, "verify", "--manifest", out, "--staging", stage)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("CHANGED  DBFilesClient/Item.dbc", r.stdout)
            self.assertIn("EXTRA    DBFilesClient/New.dbc", r.stdout)

    def test_detects_patch_archive_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stage = self._stage(tmp)
            out = tmp / "manifest.json"
            run(MANIFEST, "make", "--staging", stage, "--version", "1.2.3",
                "--patch", tmp / "patch-4.MPQ", "--out", out)
            (tmp / "patch-4.MPQ").write_bytes(b"rebuilt-archive")
            r = run(MANIFEST, "verify", "--manifest", out, "--staging", stage,
                    "--patch", tmp / "patch-4.MPQ")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("patch archive", r.stdout)


class DbcToolCli(unittest.TestCase):
    def test_info_rejects_truncated_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = build_dbc(Path(tmp) / "Item.dbc", 8, [(1, 0, 0, 0, 0, 0, 0, 0)])
            bad = Path(tmp) / "Trunc.dbc"
            bad.write_bytes(good.read_bytes()[:-4])
            r = run(DBC_TOOL, "info", bad)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("size mismatch", r.stderr)

    def test_free_id_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbc = build_dbc(Path(tmp) / "Item.dbc", 8,
                            [(800000, *([0] * 7)), (800005, *([0] * 7))])
            r = run(DBC_TOOL, "free", dbc, "--from", "800000", "--to", "800009")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("800001-800004", r.stdout)
            self.assertIn("800006-800009", r.stdout)


if __name__ == "__main__":
    unittest.main()
