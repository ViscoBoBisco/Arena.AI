"""Build, integrity, qualification, and release are separate fail-closed phases."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.qualification import (  # noqa: E402
    _compile_qualification_matches,
    _required_gate_names,
    _valid_delivery_contract,
    integrity_check,
    release_package,
)
from foundry.package import _rename_directory_noreplace

FOUNDRY = helpers.REPO / "tools" / "foundry.py"
FROST_SPEC = helpers.foundry_spec_fixture("frost-armory.json", omit=("recipes",))


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _minimal_qualified_fixture(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    package = root / "eligible-package"
    package.mkdir()
    (package / "spec.json").write_text('{"spec_version":1}\n', encoding="utf-8")
    evidence = {
        "allow_unverified": False,
        "unverified_asset_refs": [],
        "lifecycle": {"installable": False, "release": "BLOCKED"},
        "validation": {"ok": True, "verdicts": {"asset-verification": "PASS"}},
        "generated_scope": {"synthetic": "complete fixture implementation"},
        "delivery_requirements": {
            "schema": "arena-delivery-requirements/1",
            "artifacts": [],
            "capabilities": [],
        },
        "acceptance": [{"statement": "synthetic acceptance fixture", "status": "NOT_ASSESSED"}],
    }
    evidence_bytes = (json.dumps(evidence, sort_keys=True) + "\n").encode()
    (package / "evidence.json").write_bytes(evidence_bytes)
    files = {}
    for name in ("spec.json", "evidence.json"):
        content = (package / name).read_bytes()
        files[name] = {"sha256": _hash(content), "size": len(content)}
    manifest = {
        "schema": "arena-foundry-manifest/1",
        "spec": "synthetic-fixture",
        "files": files,
        "metadata_files": {},
        "delivery_requirements": {
            "schema": "arena-delivery-requirements/1",
            "artifacts": [],
            "capabilities": [],
        },
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (package / "manifest.json").write_bytes(manifest_bytes)
    attestations = {
        "schema": "arena-qualification-attestations/1",
        "subject": {"manifest_sha256": _hash(manifest_bytes)},
        "levels": {
            "CAMPAIGN-LIVE": {"status": "PASS", "evidence": "test://synthetic-live-proof"},
        },
        "acceptance": [{
            "statement": "synthetic acceptance fixture",
            "status": "PASS",
            "evidence": "test://synthetic-acceptance-proof",
        }],
        "artifacts": [],
    }
    attestation_path = root / "attestations.json"
    attestation_path.write_text(json.dumps(attestations), encoding="utf-8")
    return package, attestation_path


def _set_delivery_requirements(package: Path, attestation_path: Path, artifacts: list):
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["delivery_requirements"]["artifacts"] = artifacts
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    manifest_path.write_bytes(manifest_bytes)
    attestations = json.loads(attestation_path.read_text(encoding="utf-8"))
    attestations["subject"]["manifest_sha256"] = _hash(manifest_bytes)
    attestation_path.write_text(json.dumps(attestations), encoding="utf-8")


class LifecyclePhases(unittest.TestCase):
    def test_build_verify_and_qualify_are_distinct_and_frost_cannot_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "preview"
            built = helpers.run_tool(FOUNDRY, "build", FROST_SPEC,
                                     "--out", package, "--allow-unverified")
            self.assertEqual(built.returncode, 0, built.stderr)
            evidence_path = package / "evidence.json"
            before_verify = evidence_path.read_bytes()
            evidence = json.loads(before_verify)
            self.assertEqual(evidence["lifecycle"]["status"], "PREVIEW")
            self.assertFalse(evidence["lifecycle"]["installable"])

            verified = helpers.run_tool(FOUNDRY, "verify", package)
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
            self.assertIn("integrity only", verified.stdout)
            self.assertEqual(evidence_path.read_bytes(), before_verify,
                             "integrity verification must not mark qualification or release")

            report_path = root / "qualification.json"
            qualified = helpers.run_tool(FOUNDRY, "qualify", package, "--out", report_path)
            self.assertEqual(qualified.returncode, 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["qualified"])
            self.assertEqual(report["status"], "BLOCKED")
            self.assertTrue(any("asset_provenance" in reason
                                for reason in report["blocked_reasons"]))
            self.assertTrue(any("attestation_binding" in reason
                                for reason in report["blocked_reasons"]))
            self.assertTrue(any("artifact_maturity:server_module:runtime_exercised" in reason
                                for reason in report["blocked_reasons"]))
            self.assertIn("artifact_maturity", report)
            self.assertEqual(report["artifact_maturity"]["schema"], "arena-artifact-maturity/1")

            release_dir = root / "release"
            released = helpers.run_tool(FOUNDRY, "release", package,
                                       "--qualification", report_path, "--out", release_dir)
            self.assertEqual(released.returncode, 1)
            self.assertFalse(release_dir.exists())

    def test_free_text_maturity_labels_cannot_change_structured_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "preview"
            built = helpers.run_tool(FOUNDRY, "build", FROST_SPEC,
                                     "--out", package, "--allow-unverified")
            self.assertEqual(built.returncode, 0, built.stderr)
            first_path = root / "first.json"
            first = helpers.run_tool(FOUNDRY, "qualify", package, "--out", first_path)
            self.assertEqual(first.returncode, 1)
            first_report = json.loads(first_path.read_text(encoding="utf-8"))

            # Rewrite all legacy prose maturity claims, and correctly re-hash
            # the local package metadata. The policy result must be identical.
            evidence_path = package / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["generated_scope"] = {key: "complete and production ready"
                                           for key in evidence["generated_scope"]}
            evidence_path.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n",
                                     encoding="utf-8")
            manifest_path = package / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            evidence_bytes = evidence_path.read_bytes()
            manifest["metadata_files"]["evidence.json"] = {
                "sha256": _hash(evidence_bytes), "size": len(evidence_bytes)}
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                                     encoding="utf-8")
            second_path = root / "second.json"
            second = helpers.run_tool(FOUNDRY, "qualify", package, "--out", second_path)
            self.assertEqual(second.returncode, 1)
            second_report = json.loads(second_path.read_text(encoding="utf-8"))
            self.assertEqual(first_report["artifact_maturity"]["artifacts"],
                             second_report["artifact_maturity"]["artifacts"])
            self.assertTrue(any("artifact_maturity:server_module:runtime_exercised" in reason
                                for reason in second_report["blocked_reasons"]))

    def test_forged_all_pass_report_cannot_release_blocked_frost(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = helpers.REPO / "packages" / "frost-armory"
            checked = integrity_check(package)
            evidence = json.loads((package / "evidence.json").read_text(encoding="utf-8"))
            gate_names = sorted(_required_gate_names(evidence, checked["manifest"]))
            forged = {
                "schema": "arena-qualification-report/1",
                "package": str(package),
                "manifest_sha256": checked["manifest_sha256"],
                "integrity": "PASS",
                "status": "QUALIFIED",
                "qualified": True,
                "blocked_reasons": [],
                "gates": [{"name": name, "status": "PASS",
                           "detail": "operator asserts PASS"} for name in gate_names],
            }
            report_path = Path(tmp) / "fabricated-report.json"
            output = Path(tmp) / "must-not-release"
            report_path.write_text(json.dumps(forged), encoding="utf-8")
            result = helpers.run_tool(FOUNDRY, "release", package,
                                      "--qualification", report_path, "--out", output)
            self.assertEqual(result.returncode, 1,
                             "a self-consistent PASS report must not authorize release")
            self.assertFalse(output.exists())

            # A report that starts from a genuine blocked result is no more
            # authoritative: editing gate/status fields must disagree with a
            # fresh policy evaluation over the package and preserved raw inputs.
            genuine_path = Path(tmp) / "genuine-blocked.json"
            helpers.run_tool(FOUNDRY, "qualify", package, "--out", genuine_path)
            edited = json.loads(genuine_path.read_text(encoding="utf-8"))
            edited["qualified"] = True
            edited["status"] = "QUALIFIED"
            edited["blocked_reasons"] = []
            for gate in edited["gates"]:
                gate["status"] = "PASS"
                gate["detail"] = "fabricated"
            report_path.write_text(json.dumps(edited), encoding="utf-8")
            result = helpers.run_tool(FOUNDRY, "release", package,
                                      "--qualification", report_path, "--out", output)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(output.exists())

    def test_qualified_report_is_manifest_bound_and_release_copy_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, attestations = _minimal_qualified_fixture(root)
            report_path = root / "qualified.json"
            qualified = helpers.run_tool(FOUNDRY, "qualify", package,
                                         "--attestations", attestations,
                                         "--out", report_path)
            self.assertEqual(qualified.returncode, 0, qualified.stdout + qualified.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["qualified"])
            self.assertEqual(report["status"], "QUALIFIED")

            incomplete_path = root / "incomplete.json"
            incomplete = dict(report)
            incomplete["gates"] = report["gates"][:-1]
            incomplete_path.write_text(json.dumps(incomplete), encoding="utf-8")
            refused = helpers.run_tool(FOUNDRY, "release", package,
                                       "--qualification", incomplete_path,
                                       "--out", root / "must-not-release")
            self.assertEqual(refused.returncode, 1)
            self.assertFalse((root / "must-not-release").exists())

            output = root / "released"
            released = helpers.run_tool(FOUNDRY, "release", package,
                                        "--qualification", report_path, "--out", output)
            self.assertEqual(released.returncode, 0, released.stdout + released.stderr)
            self.assertEqual(integrity_check(output)["ok"], True)
            self.assertEqual((package / "manifest.json").read_bytes(),
                             (output / "manifest.json").read_bytes())

    def test_release_does_not_replace_destination_created_after_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, attestations = _minimal_qualified_fixture(root)
            report_path = root / "qualified.json"
            qualified = helpers.run_tool(FOUNDRY, "qualify", package,
                                         "--attestations", attestations,
                                         "--out", report_path)
            self.assertEqual(qualified.returncode, 0, qualified.stdout + qualified.stderr)

            destination = root / "raced-destination"
            sentinel = destination / "operator-data.txt"

            def race_create_output(stage: Path, output: Path):
                output.mkdir()
                sentinel.write_text("must survive", encoding="utf-8")
                _rename_directory_noreplace(stage, output)

            with patch("foundry.qualification._rename_directory_noreplace",
                       side_effect=race_create_output):
                with self.assertRaises(FileExistsError):
                    release_package(package, report_path, destination)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "must survive")
            self.assertEqual(list(destination.iterdir()), [sentinel])
            self.assertFalse(list(root.glob(".raced-destination.stage-*")))

    def test_each_declared_delivery_surface_needs_its_own_qualification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, attestations = _minimal_qualified_fixture(root)
            _set_delivery_requirements(package, attestations, [
                {"kind": "world_sql"},
                {"kind": "server_module"},
                {"kind": "client_dbc", "table": "Item.dbc"},
                {"kind": "server_dbc", "table": "Item.dbc"},
                {"kind": "mpq", "table": "Item.dbc"},
            ])
            raw_attestations = json.loads(attestations.read_text(encoding="utf-8"))
            raw_attestations["levels"]["ARENA-DB"] = {
                "status": "PASS", "evidence": "sha256:" + "0" * 64,
            }
            raw_attestations["database_qualification"] = {
                "schema": "arena-db-qualification/2",
                "manifest_sha256": raw_attestations["subject"]["manifest_sha256"],
            }
            attestations.write_text(json.dumps(raw_attestations), encoding="utf-8")
            report_path = root / "qualification.json"
            qualified = helpers.run_tool(FOUNDRY, "qualify", package,
                                         "--attestations", attestations,
                                         "--out", report_path)
            self.assertEqual(qualified.returncode, 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            blocked = {gate["name"] for gate in report["gates"]
                       if gate["status"] == "BLOCKED"}
            self.assertTrue({"database_migrations", "server_module", "mpq_delivery",
                             "dbc_parity:Item.dbc"} <= blocked,
                            "a free-form ARENA-DB PASS string is not semantic DB proof")

    def test_delivery_contract_rejects_ambiguous_mpq_surfaces(self):
        base = {
            "schema": "arena-delivery-requirements/1",
            "capabilities": [],
            "artifacts": [
                {"kind": "client_dbc", "table": "Item.dbc",
                 "version": "1", "activation": "merge"},
                {"kind": "server_dbc", "table": "Item.dbc",
                 "version": "1", "activation": "install"},
                {"kind": "mpq", "version": "1", "activation": "merge"},
            ],
        }
        self.assertTrue(_valid_delivery_contract(base))
        ambiguous_mpq = json.loads(json.dumps(base))
        ambiguous_mpq["artifacts"][-1]["table"] = "Item.dbc"
        self.assertFalse(_valid_delivery_contract(ambiguous_mpq))
        malformed_kind = json.loads(json.dumps(base))
        malformed_kind["artifacts"][0]["kind"] = ["client_dbc"]
        self.assertFalse(_valid_delivery_contract(malformed_kind))

    def test_compile_evidence_must_match_canonical_record_and_packaged_sources(self):
        package = helpers.REPO / "packages" / "frost-armory"
        evidence = json.loads((package / "evidence.json").read_text(encoding="utf-8"))
        self.assertTrue(_compile_qualification_matches(package, evidence))

        forged = json.loads(json.dumps(evidence))
        forged["compile_qualification"]["environment"]["qualified_commit"] = "0" * 40
        self.assertFalse(_compile_qualification_matches(package, forged))

        with tempfile.TemporaryDirectory() as tmp:
            changed_package = Path(tmp) / "package"
            shutil.copytree(package, changed_package)
            source = changed_package / "server/mod-frost-armory/src/FrostArmory.cpp"
            source.write_text(source.read_text(encoding="utf-8") + "// drift\n",
                              encoding="utf-8")
            self.assertFalse(_compile_qualification_matches(changed_package, evidence))

    def test_release_rejects_manifest_drift_and_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, attestations = _minimal_qualified_fixture(root)
            report_path = root / "qualified.json"
            helpers.run_tool(FOUNDRY, "qualify", package, "--attestations", attestations,
                             "--out", report_path)
            (package / "spec.json").write_text("tampered\n", encoding="utf-8")
            blocked = helpers.run_tool(FOUNDRY, "release", package,
                                       "--qualification", report_path,
                                       "--out", root / "must-not-exist")
            self.assertEqual(blocked.returncode, 1)
            self.assertFalse((root / "must-not-exist").exists())

            package, attestations = _minimal_qualified_fixture(root / "second")
            report_path = root / "second-qualification.json"
            helpers.run_tool(FOUNDRY, "qualify", package, "--attestations", attestations,
                             "--out", report_path)
            destination = root / "pre-existing"
            destination.mkdir()
            blocked = helpers.run_tool(FOUNDRY, "release", package,
                                       "--qualification", report_path, "--out", destination)
            self.assertEqual(blocked.returncode, 1)
            self.assertEqual(list(destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
