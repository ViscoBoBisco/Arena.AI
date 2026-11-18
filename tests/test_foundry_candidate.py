"""Candidate export is integrity-checked but cannot install or authorize."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

REPO = helpers.REPO
FOUNDRY = REPO / "tools" / "foundry.py"
SPEC = REPO / "specs" / "vault-terminal.json"


class CandidateExport(unittest.TestCase):
    def _build(self, root: Path) -> Path:
        package = root / "package"
        result = helpers.run_tool(FOUNDRY, "build", SPEC, "--out", package)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return package

    def test_export_contains_exact_package_and_explicit_non_authorizing_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._build(root)
            readme = (package / "README.md").read_text(encoding="utf-8")
            self.assertIn("Integration candidate export (non-installing, non-authorizing)", readme)
            archive = root / "candidate.zip"
            result = helpers.run_tool(FOUNDRY, "export-candidate", package, "--out", archive)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["status"], "EXPORTED_NON_AUTHORIZING_CANDIDATE")
            self.assertFalse(receipt["installable"])
            self.assertFalse(receipt["release_authorized"])

            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                self.assertIn("CANDIDATE.json", names)
                envelope = json.loads(bundle.read("CANDIDATE.json"))
                self.assertEqual(envelope["schema"], "arena-integration-candidate/1")
                self.assertTrue(envelope["candidate_only"])
                self.assertTrue(envelope["non_authorizing"])
                self.assertFalse(envelope["installable"])
                self.assertFalse(envelope["release_authorized"])
                self.assertEqual(envelope["qualification"], "UNCHANGED")
                self.assertEqual(envelope["campaign_admission"], "NOT_ASSESSED")
                self.assertEqual(envelope["campaign_live"], "NOT_ASSESSED")
                self.assertIn("package/manifest.json", names)
                self.assertIn("package/evidence.json", names)
                for member in names:
                    if member.startswith("package/"):
                        relative = member.removeprefix("package/")
                        self.assertEqual(bundle.read(member), (package / relative).read_bytes())

    def test_export_is_deterministic_and_does_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._build(root)
            before = {p.relative_to(package).as_posix(): p.read_bytes()
                      for p in package.rglob("*") if p.is_file()}
            first, second = root / "one.zip", root / "two.zip"
            for target in (first, second):
                result = helpers.run_tool(FOUNDRY, "export-candidate", package, "--out", target)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            after = {p.relative_to(package).as_posix(): p.read_bytes()
                     for p in package.rglob("*") if p.is_file()}
            self.assertEqual(after, before)

    def test_tampered_or_unlisted_source_is_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._build(root)
            (package / "unknown.txt").write_text("not in manifest", encoding="utf-8")
            out = root / "candidate.zip"
            result = helpers.run_tool(FOUNDRY, "export-candidate", package, "--out", out)
            self.assertEqual(result.returncode, 1)
            self.assertIn("UNLISTED unknown.txt", result.stderr)
            self.assertFalse(out.exists())

    def test_refuses_overwrite_inside_source_and_non_zip_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._build(root)
            inside = helpers.run_tool(FOUNDRY, "export-candidate", package,
                                      "--out", package / "candidate.zip")
            self.assertEqual(inside.returncode, 1)
            self.assertFalse((package / "candidate.zip").exists())
            bad_suffix = helpers.run_tool(FOUNDRY, "export-candidate", package,
                                          "--out", root / "candidate.tar")
            self.assertEqual(bad_suffix.returncode, 1)
            self.assertFalse((root / "candidate.tar").exists())
            existing = root / "existing.zip"
            existing.write_bytes(b"operator-owned")
            refused = helpers.run_tool(FOUNDRY, "export-candidate", package, "--out", existing)
            self.assertEqual(refused.returncode, 1)
            self.assertEqual(existing.read_bytes(), b"operator-owned")


if __name__ == "__main__":
    unittest.main()
