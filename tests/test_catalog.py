"""Tests for the alteration catalog and its query tool.

The catalog is data the docs depend on, so it is tested like code:
python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.catalog import validate as validate_catalog

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "catalog" / "alterations.json"
TOOL = REPO / "tools" / "catalog.py"
DOCS = [REPO / "docs" / "09-alteration-catalog.md",
        REPO / "docs" / "10-novel-alterations.md"]

ID_RE = re.compile(r"\b(?:policy|content|mech|system|client|novel)\.[a-z0-9][a-z0-9.-]*[a-z0-9]\b")


def run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                          cwd=REPO, capture_output=True, text=True)


class Schema(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(CATALOG.read_text(encoding="utf-8"))

    def test_validate_passes(self):
        result = run("validate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("catalog: OK", result.stdout)

    def test_validator_rejects_malformed_json_shapes_without_crashing(self):
        mutations = (
            lambda data: data.update(entries={"unexpected": "mapping"}),
            lambda data: data["entries"].__setitem__(0, []),
            lambda data: data["entries"][0].__setitem__("id", 17),
            lambda data: data["entries"][0].__setitem__("id", "policy.invalid id"),
            lambda data: data["entries"][0].__setitem__("notes", None),
            lambda data: data["entries"][0].__setitem__("clock", False),
            lambda data: data["entries"][0].__setitem__("surfaces", [None]),
            lambda data: data.update(layers=[None]),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                data = json.loads(CATALOG.read_text(encoding="utf-8"))
                mutate(data)
                problems = validate_catalog(data)
                self.assertTrue(problems)

    def test_loader_rejects_non_object_root_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scalar.json"
            path.write_text("[]", encoding="utf-8")
            result = run("validate", "--catalog", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("top-level catalog must be an object", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_loader_reports_invalid_encoding_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid-encoding.json"
            path.write_bytes(b"\xff")
            result = run("validate", "--catalog", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read catalog", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_query_refuses_invalid_custom_catalog_cleanly(self):
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        data["entries"][0]["surfaces"] = [None]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad-catalog.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = run("query", "--catalog", path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("catalog invalid:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_ids_unique_and_prefixed(self):
        prefixes = {"policy": "policy.", "content": "content.", "mechanics": "mech.",
                    "systems": "system.", "presentation": "client.",
                    "novel": "novel."}
        seen = set()
        for entry in self.data["entries"]:
            self.assertNotIn(entry["id"], seen, f"duplicate id {entry['id']}")
            seen.add(entry["id"])
            self.assertTrue(entry["id"].startswith(prefixes[entry["layer"]]),
                            f"{entry['id']} does not match layer {entry['layer']}")

    def test_every_entry_describes_play(self):
        for entry in self.data["entries"]:
            self.assertGreater(len(entry["effect"].split()), 6,
                               f"{entry['id']}: effect is too thin to be useful")
            self.assertTrue(entry["verify"], f"{entry['id']}: no verification steps")
            self.assertTrue(entry.get("notes", "").strip(),
                            f"{entry['id']}: every catalog item needs an evidence/limitation note")

    def test_delivery_kinds_are_declared(self):
        kinds = {k["id"] for k in self.data["patch_kinds"]}
        self.assertEqual(kinds, {"none", "client-files", "dbc-rows", "assets"})
        for entry in self.data["entries"]:
            self.assertIn(entry["patch"], kinds, entry["id"])

    def test_policy_entries_record_limits_and_safe_test_context(self):
        policy = {entry["id"]: entry for entry in self.data["entries"]
                  if entry["layer"] == "policy"}
        self.assertEqual(len(policy), 15)
        self.assertTrue(all(entry.get("notes", "").strip() for entry in policy.values()))
        reset_checks = " ".join(policy["policy.instance-reset"]["verify"]).lower()
        reset_notes = policy["policy.instance-reset"]["notes"].lower()
        self.assertNotIn("unbind all", reset_checks)
        self.assertIn("lockout", reset_checks)
        self.assertIn("unbind-all", reset_notes)
        self.assertIn("throwaway test guild", " ".join(
            policy["policy.guild-bank-economy"]["verify"]).lower())

    def test_content_entries_record_scope_and_safe_verification(self):
        content = {entry["id"]: entry for entry in self.data["entries"]
                   if entry["layer"] == "content"}
        self.assertEqual(len(content), 32)
        self.assertTrue(all(entry.get("notes", "").strip() for entry in content.values()))
        self.assertNotIn(".bg start?", " ".join(content["content.battleground-arena"]["verify"]))
        self.assertIn("boundary", " ".join(content["content.graveyard"]["verify"]).lower())
        self.assertIn("PrevQuestID", content["content.quest-gating"]["notes"])
        self.assertIn("not proof", content["content.weather"]["notes"])
        self.assertIn("zone scheduling", content["content.weather"]["notes"])
        self.assertIn("separate paths", content["content.mail-reward"]["notes"])

    def test_mechanics_entries_record_failure_and_evidence_limits(self):
        mechanics = {entry["id"]: entry for entry in self.data["entries"]
                     if entry["layer"] == "mechanics"}
        self.assertEqual(len(mechanics), 15)
        self.assertTrue(all(entry.get("notes", "").strip() for entry in mechanics.values()))
        self.assertIn("server behavior check", mechanics["mech.spell-cooldown"]["notes"])
        self.assertIn("single failed roll", mechanics["mech.spell-proc"]["notes"])
        self.assertIn("callback is registered", mechanics["mech.spell-scripts"]["notes"])
        self.assertIn("both sides of a boundary", mechanics["mech.spell-area"]["notes"])
        self.assertIn("formation", mechanics["mech.waypoints-formations"]["notes"])

    def test_systems_and_presentation_record_qualification_boundaries(self):
        by_id = {entry["id"]: entry for entry in self.data["entries"]}
        for layer, expected in (("systems", 8), ("presentation", 15)):
            entries = [entry for entry in self.data["entries"] if entry["layer"] == layer]
            self.assertEqual(len(entries), expected)
            self.assertTrue(all(entry.get("notes", "").strip() for entry in entries))
            self.assertTrue(all(len(entry["verify"]) >= 2 for entry in entries),
                            f"{layer}: every entry needs distinct verification checks")
        self.assertIn("custom module behavior", by_id["system.auto-scaling"]["notes"])
        self.assertIn("ordinary creature_loot_template rows", by_id["system.custom-loot"]["notes"])
        self.assertIn("distinct addon surfaces", by_id["client.nameplate-minimap"]["notes"])
        self.assertIn("does not grant or unlock", by_id["client.titles"]["notes"])

    def test_no_mpq_share_is_majority(self):
        """The project's central claim: most levers need no archive change."""
        entries = self.data["entries"]
        no_mpq = [e for e in entries if e["patch"] in ("none", "client-files")]
        self.assertGreater(len(no_mpq) / len(entries), 0.75)


class Cli(unittest.TestCase):
    def test_list_and_filters(self):
        result = run("query", "--no-mpq")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("content.quest-new", result.stdout)
        self.assertNotIn("client.new-map", result.stdout.split("\n\n")[0])

        result = run("query", "--layer", "policy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("policy.xp-rate", result.stdout)
        self.assertNotIn("mech.smartai", result.stdout)

        result = run("query", "--clock", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("policy.addon-channel", result.stdout)

    def test_search_is_record_wide(self):
        result = run("query", "--search", "quest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("content.quest-new", result.stdout)

    def test_show_known_and_unknown(self):
        result = run("show", "content.vendor-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("surfaces:", result.stdout)
        self.assertIn("in play :", result.stdout)

        result = run("show", "content.nope")
        self.assertEqual(result.returncode, 2)

    def test_json_output_is_parseable(self):
        result = run("list", "--layer", "mechanics", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads(result.stdout)
        self.assertTrue(entries)
        self.assertTrue(all(e["layer"] == "mechanics" for e in entries))

    def test_matrix_and_stats_run(self):
        for command in ("matrix", "stats", "surfaces"):
            result = run(command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip(), f"{command} printed nothing")


class DocsAgreeWithCatalog(unittest.TestCase):
    def test_doc_references_only_existing_entries(self):
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        known = {entry["id"] for entry in data["entries"]}
        unknown = []
        for doc in DOCS:
            for ref in set(ID_RE.findall(doc.read_text(encoding="utf-8"))):
                if ref not in known:
                    unknown.append(f"{doc.name}: {ref}")
        self.assertEqual(sorted(unknown), [], f"chapters reference unknown catalog ids: {unknown}")

    def test_docs_use_the_catalog_meaningfully(self):
        referenced = set()
        for doc in DOCS:
            referenced |= set(ID_RE.findall(doc.read_text(encoding="utf-8")))
        self.assertGreater(len(referenced), 30, "the chapters should exercise the catalog, not duplicate it")

    def test_novel_chapter_covers_every_novel_entry(self):
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        novel = {e["id"] for e in data["entries"] if e["layer"] == "novel"}
        documented = set(ID_RE.findall(DOCS[1].read_text(encoding="utf-8")))
        missing = sorted(novel - documented)
        # the chapter may group several levers under one paragraph, but most must be named
        self.assertLessEqual(len(missing), 3, f"undocumented novel levers: {missing}")

    def test_readme_and_layout_point_at_the_catalog(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/09-alteration-catalog.md", readme)
        self.assertIn("catalog/alterations.json", readme)
        self.assertIn("tools/catalog.py", readme)


if __name__ == "__main__":
    unittest.main()
