"""DBC writer: lossless round trips and refusal of bad deltas."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

sys.path.insert(0, str(helpers.REPO))
from foundry.dbc_writer import DbcFile, DbcWriteError, apply_delta, verify_roundtrip  # noqa: E402


def base_dbc(tmp: Path) -> Path:
    return helpers.write_dbc(tmp / "Item.dbc", 8,
                             [(800000, 2, 7, -1, 1, 41179, 17, 3),
                              (800001, 2, 7, -1, 1, 41180, 17, 3)])


class RoundTrip(unittest.TestCase):
    def test_untouched_records_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = DbcFile.load(base_dbc(Path(tmp)))
            out = apply_delta(base, [{"id": 800002,
                                      "fields": [800002, 2, 7, -1, 1, 41181, 17, 3]}])
            self.assertEqual(verify_roundtrip(base, out), [])
            by_id = {r[0]: r for r in out.records}
            self.assertEqual(by_id[800000], base.records[0])
            self.assertEqual(by_id[800001], base.records[1])
            self.assertEqual(len(out.records), 3)

    def test_replace_existing_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = DbcFile.load(base_dbc(Path(tmp)))
            out = apply_delta(base, [{"id": 800000,
                                      "fields": [800000, 2, 7, -1, 1, 999, 17, 3]}])
            by_id = {r[0]: r for r in out.records}
            self.assertEqual(by_id[800000][5], 999)
            self.assertEqual(len(out.records), 2)

    def test_string_fields_append_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = DbcFile.load(base_dbc(Path(tmp)))
            out = apply_delta(base, [{"id": 800002,
                                      "fields": [800002, "Frostbrand", 7, -1, 1, 41181, 17, 3]}],
                              string_fields=[1])
            self.assertTrue(out.strings.startswith(base.strings))
            self.assertEqual(out.string_at(out.records[-1][1]), "Frostbrand")

    def test_verifier_rejects_changed_untouched_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = DbcFile.load(base_dbc(Path(tmp)))
            changed = DbcFile(base.field_count,
                              [(800000, 99, 7, -1, 1, 41179, 17, 3), base.records[1]],
                              base.strings)
            self.assertIn("untouched record 800000 changed", verify_roundtrip(base, changed))

    def test_verifier_checks_only_declared_string_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = DbcFile.load(base_dbc(Path(tmp)))
            base = DbcFile(loaded.field_count,
                           [(800000, 99, 7, -1, 1, 41179, 17, 3), loaded.records[1]],
                           loaded.strings)
            out = DbcFile(base.field_count, list(base.records), base.strings)
            self.assertEqual(verify_roundtrip(base, out), [])  # 99 is an ordinary int here
            problems = verify_roundtrip(base, out, string_fields=[1])
            self.assertIn("record 800000: string offset 99 outside string block", problems)


class Refusals(unittest.TestCase):
    def test_wrong_field_count_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = DbcFile.load(base_dbc(Path(tmp)))
            with self.assertRaises(DbcWriteError):
                apply_delta(base, [{"id": 1, "fields": [1, 2, 3]}])

    def test_id_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = DbcFile.load(base_dbc(Path(tmp)))
            with self.assertRaises(DbcWriteError):
                apply_delta(base, [{"id": 5, "fields": [6, 2, 7, -1, 1, 1, 17, 3]}])

    def test_duplicate_base_ids_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = helpers.write_dbc(Path(tmp) / "Item.dbc", 8,
                                     [(1, 2, 7, -1, 1, 1, 17, 3),
                                      (1, 2, 7, -1, 1, 1, 17, 3)])
            base = DbcFile.load(path)
            with self.assertRaises(DbcWriteError):
                apply_delta(base, [])

    def test_non_int_field_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = DbcFile.load(base_dbc(Path(tmp)))
            with self.assertRaises(DbcWriteError):
                apply_delta(base, [{"id": 9, "fields": [9, "x", 7, -1, 1, 1, 17, 3]}])

    def test_duplicate_delta_ids_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = DbcFile.load(base_dbc(Path(tmp)))
            row = {"id": 9, "fields": [9, 2, 7, -1, 1, 1, 17, 3]}
            with self.assertRaisesRegex(DbcWriteError, "duplicate id 9"):
                apply_delta(base, [row, row])

    def test_out_of_range_integer_refused_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = DbcFile.load(base_dbc(Path(tmp)))
            with self.assertRaisesRegex(DbcWriteError, "signed 32-bit"):
                apply_delta(base, [{"id": 9, "fields": [9, 2**40, 7, -1, 1, 1, 17, 3]}])


if __name__ == "__main__":
    unittest.main()
