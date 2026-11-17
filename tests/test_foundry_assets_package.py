"""Asset intelligence + packaging: search, patch cost, reproducibility."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.assets import ingest_dbc_dir, ingest_inventory, patch_cost, search  # noqa: E402

FOUNDRY = helpers.REPO / "tools" / "foundry.py"
SPEC = helpers.REPO / "specs" / "frost-armory.json"


class Assets(unittest.TestCase):
    def test_inventory_search_and_patch_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "inv.json"
            inv.write_text(json.dumps({"assets": [
                {"kind": "item_display", "id": 41179, "tags": ["frost", "sword"],
                 "verified": True},
                {"kind": "spell_visual", "id": 7, "tags": ["frost"], "verified": False},
            ]}), encoding="utf-8")
            index = ingest_inventory(inv)
            hits = search(index, "frost")
            self.assertEqual(len(hits), 2)
            self.assertEqual(patch_cost(index, "item_display", 41179), "none")
            self.assertEqual(patch_cost(index, "item_display", 999999), "dbc-rows")
            self.assertEqual(patch_cost(index, "new_art", 1), "assets")

    def test_dbc_dir_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            helpers.write_dbc(Path(tmp) / "ItemDisplayInfo.dbc", 25,
                              [(41179, *([0] * 24)), (41180, *([0] * 24))])
            index = ingest_dbc_dir(Path(tmp))
            self.assertEqual(len(index["item_display"]), 2)
            self.assertTrue(all(m["verified"] for m in index["item_display"].values()))


class Packaging(unittest.TestCase):
    def test_cli_build_verify_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pkg"
            built = helpers.run_tool(FOUNDRY, "build", SPEC, "--out", out,
                                     "--allow-unverified")
            self.assertEqual(built.returncode, 0, built.stderr)
            evidence = json.loads((out / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(len(evidence["acceptance"]), 8)
            self.assertTrue(all(c["status"] == "NOT_ASSESSED" for c in evidence["acceptance"]))
            verified = helpers.run_tool(FOUNDRY, "verify", out)
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_build_without_allowance_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pkg"
            built = helpers.run_tool(FOUNDRY, "build", SPEC, "--out", out)
            self.assertEqual(built.returncode, 1, built.stdout + built.stderr)
            self.assertFalse((out / "manifest.json").exists())

    def test_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pkg"
            helpers.run_tool(FOUNDRY, "build", SPEC, "--out", out, "--allow-unverified")
            (out / "sql" / "01_world.sql").write_text("-- tampered\n", encoding="utf-8")
            verified = helpers.run_tool(FOUNDRY, "verify", out)
            self.assertEqual(verified.returncode, 1)
            self.assertIn("CHANGED", verified.stdout)

    def test_unlisted_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pkg"
            helpers.run_tool(FOUNDRY, "build", SPEC, "--out", out, "--allow-unverified")
            (out / "unknown.MPQ").write_bytes(b"not ours")
            verified = helpers.run_tool(FOUNDRY, "verify", out)
            self.assertEqual(verified.returncode, 1)
            self.assertIn("UNLISTED  unknown.MPQ", verified.stdout)

    def test_manifest_path_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pkg"
            helpers.run_tool(FOUNDRY, "build", SPEC, "--out", out, "--allow-unverified")
            manifest_path = out / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["../outside"] = {"sha256": "0" * 64, "size": 1}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            verified = helpers.run_tool(FOUNDRY, "verify", out)
            self.assertEqual(verified.returncode, 1)
            self.assertIn("INVALID", verified.stdout)

    def test_rebuild_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            helpers.run_tool(FOUNDRY, "build", SPEC, "--out", a, "--allow-unverified")
            helpers.run_tool(FOUNDRY, "build", SPEC, "--out", b, "--allow-unverified")
            files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
            files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
            self.assertEqual(files_a, files_b)
            for rel in files_a:
                self.assertEqual((a / rel).read_bytes(), (b / rel).read_bytes(), str(rel))

    def test_validate_cli_exit_codes(self):
        ok = helpers.run_tool(FOUNDRY, "validate", SPEC, "--allow-unverified")
        self.assertEqual(ok.returncode, 1)  # SKIPPED essential check is not a pass
        missing = helpers.run_tool(FOUNDRY, "validate", "/nonexistent.json")
        self.assertEqual(missing.returncode, 2)


if __name__ == "__main__":
    unittest.main()
