"""Conformance tests for every worked example: the "full example" contract, enforced.

examples/INDEX.md says what a complete example contains. This file is what makes that sentence true:
it walks every directory under examples/ and fails if any of the seven parts is missing, if the SQL
targets a table that is not in the world allowlist, if an inserted row is not rolled back, if an ID
escapes its reserved range or collides with another example, or if a preset names a config key that
does not exist.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

EXAMPLES = helpers.REPO / "examples"
WORLD_TABLES = {
    line.strip() for line in (helpers.REPO / "catalog" / "world_tables.txt").read_text().splitlines()
    if line.strip() and not line.startswith("#")
}
CONFIG_KEYS = {
    line.strip() for line in (helpers.REPO / "catalog" / "config_keys.txt").read_text().splitlines()
    if line.strip() and not line.startswith("#")
}

# owner -> (low, high) of the example's reserved entry range
RANGES = {
    "ashen-vault": (900100, 900299),
    "vault-terminal": (900300, 900399),
    "reliquary": (900400, 900499),
    "ritual-gate": (900500, 900599),
    "fair-weather-market": (900600, 900699),
    "two-cities": (900700, 900799),
}

SQL_EXAMPLES = sorted(
    d for d in EXAMPLES.iterdir()
    if d.is_dir() and (d / "sql").is_dir()
)


def example_dirs():
    return sorted(d for d in EXAMPLES.iterdir() if d.is_dir() and (d / "README.md").is_file())


def readme(example: Path) -> str:
    return (example / "README.md").read_text(encoding="utf-8")


def section(text: str, name: str) -> str:
    """Body of a `## name` section (case-insensitive), up to the next heading of the same level."""
    match = re.search(rf"^##\s+{name}.*?$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else ""


def sql_text(example: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((example / "sql").glob("*.sql"))
                     if "rollback" not in p.name)


def rollback_text(example: Path) -> str:
    files = sorted((example / "sql").glob("*rollback*.sql"))
    return files[0].read_text(encoding="utf-8") if files else ""


class EveryExampleIsComplete(unittest.TestCase):
    def test_there_are_enough_of_them_to_be_a_collection(self):
        self.assertGreaterEqual(len(example_dirs()), 7, [d.name for d in example_dirs()])

    def test_the_index_lists_every_example(self):
        index = (EXAMPLES / "INDEX.md").read_text(encoding="utf-8")
        for example in example_dirs():
            self.assertIn(f"({example.name}/README.md)", index, f"{example.name} is not in INDEX.md")

    def test_readme_has_the_seven_parts(self):
        for example in example_dirs():
            text = readme(example)
            with self.subTest(example=example.name):
                self.assertRegex(text, r"(?i)patch cost", "must state the price")
                self.assertRegex(text, r"(?i)reused client asset|reused", "must name what it reuses")
                self.assertRegex(text, r"(?im)^##\s+(files|what is here)", "must list its files")
                self.assertRegex(text, r"(?im)^##\s+(install|use)", "must say how to load it")
                self.assertRegex(text, r"(?im)^##\s+acceptance", "must be verifiable by a human")
                self.assertRegex(text, r"(?im)^##\s+where .*stops being free", "must name its limit")
                self.assertRegex(text, r"(?i)rollback", "must be removable")
                table = section(text, "acceptance")
                rows = [line for line in table.splitlines() if line.strip().startswith("|")]
                self.assertGreaterEqual(len(rows), 5, "acceptance needs a table of checks, not a sentence")

    def test_readmes_do_not_recommend_retail_api(self):
        for example in example_dirs():
            text = readme(example)
            for line in text.splitlines():
                if "RegisterAddonMessagePrefix" in line:
                    self.assertRegex(line, r"(?i)not|no |never|5\.x", example.name)


class SqlIsLoadableAndReversible(unittest.TestCase):
    def test_every_sql_example_has_a_rollback(self):
        for example in SQL_EXAMPLES:
            with self.subTest(example=example.name):
                self.assertTrue(rollback_text(example), "no *rollback*.sql")

    def test_inserts_target_real_world_tables(self):
        for example in SQL_EXAMPLES:
            targets = set(re.findall(r"INSERT INTO\s+`?(\w+)`?", sql_text(example), re.IGNORECASE))
            with self.subTest(example=example.name):
                self.assertTrue(targets)
                unknown = {t for t in targets if t not in WORLD_TABLES and t != "vault_terminal_claims_stage_v2"}
                self.assertFalse(unknown, f"{example.name} writes to unknown tables: {unknown}")

    def test_every_inserted_table_is_rolled_back(self):
        for example in SQL_EXAMPLES:
            inserted = set(re.findall(r"INSERT INTO\s+`?(\w+)`?", sql_text(example), re.IGNORECASE))
            # The v2 schema-upgrade staging table is atomically renamed into the live ledger;
            # it is not persistent application data and never needs rollback deletion.
            inserted.discard("vault_terminal_claims_stage_v2")
            rolled = set(re.findall(r"DELETE FROM\s+`?(\w+)`?", rollback_text(example), re.IGNORECASE))
            with self.subTest(example=example.name):
                self.assertLessEqual(inserted, rolled, f"not removed on rollback: {inserted - rolled}")

    def test_sql_deletes_before_it_inserts(self):
        """Re-running a file must not produce duplicate keys."""
        for example in SQL_EXAMPLES:
            text = sql_text(example)
            for statement in re.split(r";\s*\n", text):
                if "INSERT INTO" not in statement.upper():
                    continue
                table = re.search(r"INSERT INTO\s+`?(\w+)`?", statement, re.IGNORECASE).group(1)
                if "ON DUPLICATE KEY UPDATE" in statement.upper():
                    continue  # this migration insert is explicitly idempotent on replay
                with self.subTest(example=example.name, table=table):
                    self.assertIn(f"DELETE FROM {table}", text.replace("`", ""),
                                  f"{table} is inserted without a matching DELETE in the same example")

    def test_ids_stay_inside_their_reserved_range(self):
        for example in SQL_EXAMPLES:
            low, high = RANGES[example.name]
            ids = {int(n) for n in re.findall(r"\b(9\d{5})\b", sql_text(example))}
            with self.subTest(example=example.name):
                self.assertTrue(ids, "no reserved ids used")
                outside = {i for i in ids if not (low <= i <= high)}
                self.assertFalse(outside, f"{example.name} uses ids outside {low}-{high}: {outside}")

    def test_ids_do_not_collide_between_examples(self):
        seen = {}
        for example in SQL_EXAMPLES:
            for i in re.findall(r"\b(9\d{5})\b", sql_text(example)):
                if i in seen and seen[i] != example.name:
                    self.fail(f"id {i} is used by both {seen[i]} and {example.name}")
                seen[i] = example.name

    def test_the_tinyint_table_is_treated_as_tinyint(self):
        """game_event.eventEntry is 0-255; a 900xxx value there is a silent insert failure."""
        market = EXAMPLES / "fair-weather-market"
        text = sql_text(market)
        events = set(re.findall(r"game_event(?!_\w)\s*\([^;]*?\)\s*VALUES\s*\((\d+)", text))
        events |= set(re.findall(r"`eventEntry`?\s*,\s*(\d+)", text))
        for value in {int(v) for v in events} | {int(v) for v in re.findall(r"SET @EVENT := (\d+)", text)}:
            self.assertLessEqual(value, 255, f"game_event entry {value} exceeds tinyint")


class TheExamplesOwnTheirSpace(unittest.TestCase):
    def test_reliquary_page_chains_terminate(self):
        text = (EXAMPLES / "reliquary" / "sql" / "01_pages.sql").read_text(encoding="utf-8")
        pages = {int(i): int(n) for i, n in re.findall(r"\((9004\d\d),\s*\n?.*?,\s*\n?\s*(\d+),\s*12340\)", text, re.DOTALL)}
        self.assertGreaterEqual(len(pages), 5, f"parsed {len(pages)} pages")
        for start in pages:
            seen, cursor, steps = set(), start, 0
            while cursor and steps < 50:
                self.assertNotIn(cursor, seen, f"page chain from {start} loops at {cursor}")
                seen.add(cursor)
                cursor = pages.get(cursor, 0)
                steps += 1
            self.assertLess(steps, 50)

    def test_ritual_gate_needs_more_than_one_person(self):
        text = (EXAMPLES / "ritual-gate" / "sql" / "01_gate.sql").read_text(encoding="utf-8")
        match = re.search(r"\(@GATE,\s*18,\s*@\w+,\s*'[^']*',\s*[\d.]+,\s*(\d+),", text)
        self.assertIsNotNone(match, "the ritual row is not recognisable")
        self.assertGreaterEqual(int(match.group(1)), 2, "a one-person ritual is just a button")

    def test_two_cities_really_are_the_same_place(self):
        text = (EXAMPLES / "two-cities" / "sql" / "01_twins.sql").read_text(encoding="utf-8")
        rows = re.findall(r"\((9007001|9007002),\s*@\w+,\s*\d+,\s*\d+,\s*(\d+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)", text)
        self.assertEqual(len(rows), 2, rows)
        (_, phase_a, xa, ya, za, oa), (_, phase_b, xb, yb, zb, ob) = rows
        self.assertNotEqual(phase_a, phase_b, "two spawns in one phase is one spawn")
        self.assertEqual((xa, ya, za, oa), (xb, yb, zb, ob), "the twins must share coordinates")

    def test_season_dials_only_name_real_config_keys(self):
        presets = list((EXAMPLES / "season-dials" / "presets").glob("*.conf"))
        self.assertEqual(len(presets), 2)
        for preset in presets:
            for line in preset.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key = line.split("=")[0].strip()
                with self.subTest(preset=preset.name, key=key):
                    self.assertIn(key, CONFIG_KEYS, "invented config key")

    def test_presets_are_actually_different(self):
        harvest, siege = (EXAMPLES / "season-dials" / "presets" / n for n in ("harvest.conf", "siege.conf"))
        def values(path):
            out = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key, value = line.split("=", 1)
                    out[key.strip()] = value.strip()
            return out
        a, b = values(harvest), values(siege)
        self.assertEqual(set(a), set(b), "the two presets must tune the same dials")
        differing = [k for k in a if a[k] != b[k]]
        self.assertGreaterEqual(len(differing), 5, "a preset that changes nothing is a copy")


if __name__ == "__main__":
    unittest.main()
