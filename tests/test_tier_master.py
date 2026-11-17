"""TIER 3 — master-engineer tests: mutation, fuzzing, scale, and "who tests the tests".

The engineer asked "does it behave at the edges". This tier asks the harder questions:

  * is the validator actually capable of failing? (mutation testing — every check gets a
    deliberate defect that it must catch, or the check is decorative)
  * what happens with adversarial input nobody designed for? (deterministic fuzz corpus)
  * does it still work at 10,000x the fixture size? (scale)
  * are the guarantees real? (no partial writes, no temp litter, no tracebacks, idempotence)
  * do the *claims* hold? (every table/key named in the catalog exists in the captured schema)

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import datetime
import json
import random
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

catalog_mod = helpers.import_tool("catalog")
dbc_mod = helpers.import_tool("dbc_tool")


# --------------------------------------------------------------------- mutation
class CatalogValidatorIsNotDecorative(unittest.TestCase):
    """Each mutation must be caught. A mutation that validates cleanly is a hole in the validator."""

    def setUp(self):
        self.base = helpers.load_catalog()

    def mutations(self):
        def first(data):
            return data["entries"][0]

        def drop_field(data):
            del first(data)["effect"]

        def unknown_layer(data):
            first(data)["layer"] = "vibes"

        def wrong_prefix(data):
            first(data)["id"] = "content.xp-rate"      # policy entry with a content prefix

        def unknown_patch_kind(data):
            first(data)["patch"] = "maybe"

        def bad_clock(data):
            first(data)["clock"] = 7

        def empty_surfaces(data):
            first(data)["surfaces"] = []

        def malformed_surface(data):
            first(data)["surfaces"] = ["Rate.XP.Kill"]   # no "kind:" prefix

        def unknown_surface_kind(data):
            first(data)["surfaces"] = ["database:creature_template"]

        def invented_table(data):
            first(data)["surfaces"] = ["table:definitely_not_a_real_table"]

        def renamed_conf_key(data):
            first(data)["surfaces"] = ["conf:Rate.XP.Killlll"]

        def duplicate_id(data):
            data["entries"].append(copy.deepcopy(first(data)))

        def dangling_doc_ref(data):
            first(data)["docs"] = ["99"]

        def empty_effect(data):
            first(data)["effect"] = "   "

        def verify_not_a_list(data):
            first(data)["verify"] = "kill a mob"

        return {
            "drop_field": drop_field,
            "unknown_layer": unknown_layer,
            "wrong_prefix": wrong_prefix,
            "unknown_patch_kind": unknown_patch_kind,
            "bad_clock": bad_clock,
            "empty_surfaces": empty_surfaces,
            "malformed_surface": malformed_surface,
            "unknown_surface_kind": unknown_surface_kind,
            "invented_table": invented_table,
            "renamed_conf_key": renamed_conf_key,
            "duplicate_id": duplicate_id,
            "dangling_doc_ref": dangling_doc_ref,
            "empty_effect": empty_effect,
            "verify_not_a_list": verify_not_a_list,
        }

    def test_the_real_catalog_is_clean(self):
        self.assertEqual(catalog_mod.validate(copy.deepcopy(self.base)), [])

    def test_every_mutation_is_caught(self):
        survivors = []
        for name, mutate in self.mutations().items():
            data = copy.deepcopy(self.base)
            mutate(data)
            if not catalog_mod.validate(data):
                survivors.append(name)
        self.assertEqual(survivors, [], f"validator accepted these mutations: {survivors}")


class PreflightIsNotDecorative(unittest.TestCase):
    """Same discipline for the release gate: one defect at a time, each must stop the release."""

    def control(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        client, server = helpers.item_pair(Path(tmp.name))
        return client, server

    def assert_fails(self, client, server, why):
        result = helpers.run_bash(helpers.PREFLIGHT, client, server)
        self.assertEqual(result.returncode, 1, f"{why}: gate let it through\n{result.stdout}")

    def test_control_passes(self):
        client, server = self.control()
        self.assertEqual(helpers.run_bash(helpers.PREFLIGHT, client, server).returncode, 0)

    def test_catches_client_server_drift(self):
        client, server = helpers.item_pair(Path(tempfile.mkdtemp()), drift=True)
        self.assert_fails(client, server, "value drift")

    def test_catches_invented_display_id(self):
        client, server = helpers.item_pair(Path(tempfile.mkdtemp()), dangling=True)
        self.assert_fails(client, server, "dangling display id")

    def test_catches_truncated_table(self):
        client, server = self.control()
        path = client / "ItemDisplayInfo.dbc"
        path.write_bytes(path.read_bytes()[:-8])
        self.assert_fails(client, server, "truncated table")

    def test_catches_duplicate_ids_in_a_shipped_table(self):
        client, server = self.control()
        helpers.write_dbc(client / "Item.dbc", 8,
                          [(800000, 2, 7, -1, 1, 41179, 17, 3)] * 2)
        (server / "Item.dbc").write_bytes((client / "Item.dbc").read_bytes())
        self.assert_fails(client, server, "duplicate ids")

    def test_catches_field_count_regression(self):
        client, server = self.control()
        helpers.write_dbc(client / "Item.dbc", 7, [(800000, 2, 7, -1, 1, 41179, 17)])
        (server / "Item.dbc").write_bytes((client / "Item.dbc").read_bytes())
        self.assert_fails(client, server, "wrong field count (client cannot render it)")


# ------------------------------------------------------------------------ fuzz
class DbcParserSurvivesHostileInput(unittest.TestCase):
    def test_random_buffers_raise_only_dbc_errors(self):
        """Any bytes at all must end in a clean DbcError, never a TypeError/IndexError/struct error."""
        rng = random.Random(20261117)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bad = []
        for i in range(150):
            if i % 3 == 0:
                blob = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 256)))
            elif i % 3 == 1:
                blob = b"WDBC" + bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 160)))
            else:
                n = rng.randint(0, 50)
                blob = (struct.pack("<4sIIII", b"WDBC", n, rng.randint(0, 64),
                                    rng.randint(0, 256), rng.randint(0, 64))
                        + bytes(rng.getrandbits(8) for _ in range(n)))
            path = root / f"f{i}.dbc"
            path.write_bytes(blob)
            try:
                dbc_mod.Dbc.load(path)
            except dbc_mod.DbcError:
                pass
            except Exception as exc:                       # noqa: BLE001 - that is the point
                bad.append((i, type(exc).__name__, str(exc)[:60]))
        self.assertEqual(bad, [], f"non-Dbc exceptions escaped the parser: {bad}")

    def test_cli_never_prints_a_traceback(self):
        rng = random.Random(7)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for i in range(8):
            path = root / f"c{i}.dbc"
            path.write_bytes(b"WDBC" + bytes(rng.getrandbits(8) for _ in range(rng.randint(4, 80))))
            for command in ("info", "ids", "strings"):
                result = helpers.run_tool(helpers.DBC_TOOL, command, path)
                self.assertIn(result.returncode, (0, 1, 2))
                self.assertNotIn("Traceback", result.stderr, f"{command} on {path.name}")


# -------------------------------------------------------------------- encoding
class EncodingsAreTolerated(unittest.TestCase):
    def test_bom_is_accepted(self):
        data = helpers.load_catalog()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "bom.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data).encode("utf-8"))
        self.assertEqual(catalog_mod.validate(catalog_mod.load(path)), [])

    def test_crlf_and_unicode_survive(self):
        data = helpers.load_catalog()
        data["entries"][0]["title"] = "XP rate — naïve café ☕"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "crlf.json"
        path.write_bytes(json.dumps(data, ensure_ascii=False).replace("\n", "\r\n").encode("utf-8"))
        self.assertIn("☕", catalog_mod.load(path)["entries"][0]["title"])


# ----------------------------------------------------------------------- scale
class ItStillWorksBigger(unittest.TestCase):
    def test_hundred_thousand_records(self):
        rows = [(i, i % 7, i % 13, 0, 0, 0, 0, 0) for i in range(100_000)]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "big.dbc"
        path.write_bytes(helpers.dbc_bytes(8, rows))

        started = time.monotonic()
        dbc = dbc_mod.Dbc.load(path)
        self.assertEqual(len(dbc.records), 100_000)
        # ids 0..99999 are all used, so the window scan must find nothing (exit 1 = no free ids)
        code = dbc_mod.check_free_ids(path, 0, 99_999)
        elapsed = time.monotonic() - started

        self.assertEqual(code, 1)
        self.assertLess(elapsed, 20.0, f"parsing + windowing took {elapsed:.1f}s")

    def test_manifest_handles_many_files(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        stage = root / "stage"
        (stage / "DBFilesClient").mkdir(parents=True)
        for i in range(300):
            (stage / "DBFilesClient" / f"t{i:03d}.dbc").write_bytes(b"x" * 32)
        result = helpers.run_tool(helpers.MANIFEST, "make", "--staging", stage,
                                  "--version", "big", "--out", root / "m.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((root / "m.json").read_text())
        self.assertEqual(data["summary"]["files"], 300)


# ------------------------------------------------------------------- guarantees
class Guarantees(unittest.TestCase):
    def test_manifest_write_is_atomic_and_leaves_no_litter(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        stage = root / "stage"
        (stage / "DBFilesClient").mkdir(parents=True)
        (stage / "DBFilesClient" / "Item.dbc").write_bytes(b"x")

        first = helpers.run_tool(helpers.MANIFEST, "make", "--staging", stage,
                                 "--version", "1", "--out", root / "m.json")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = helpers.run_tool(helpers.MANIFEST, "make", "--staging", stage,
                                  "--version", "1", "--out", root / "m.json")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(sorted(p.name for p in root.iterdir() if p.name.endswith(".tmp")), [])

    def test_failed_write_is_a_clean_usage_error(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        blocker = root / "blocker"
        blocker.write_text("not a directory")
        result = helpers.run_tool(helpers.MANIFEST, "make", "--staging", blocker,
                                  "--version", "1", "--out", root / "m.json")
        self.assertEqual(result.returncode, 2)

    def test_validation_is_idempotent(self):
        data = helpers.load_catalog()
        self.assertEqual(catalog_mod.validate(copy.deepcopy(data)),
                         catalog_mod.validate(copy.deepcopy(data)))

    def test_every_catalog_surface_names_real_schema(self):
        """The claims check: table: and conf: surfaces must exist in the captured inventories."""
        data = helpers.load_catalog()
        tables = set((helpers.REPO / "catalog" / "world_tables.txt").read_text().split())
        keys = set((helpers.REPO / "catalog" / "config_keys.txt").read_text().split())
        problems = []
        for entry in data["entries"]:
            for surface in entry["surfaces"]:
                kind, _, rest = surface.partition(":")
                target = rest.split(":", 1)[0]
                if kind == "table" and target not in tables:
                    problems.append(f"{entry['id']}: {surface}")
                if kind == "conf" and target not in keys:
                    problems.append(f"{entry['id']}: {surface}")
        self.assertEqual(problems, [], f"surfaces not found in schema inventory: {problems}")


class VaultTerminalSurvivesHostileClients(unittest.TestCase):
    """The addon channel is a socket any client can write to. Every field is hostile input."""

    def setUp(self):
        import datetime
        self.m = helpers.vt_model()
        self.today = datetime.date(2026, 11, 17)

    def session(self, **kw):
        return self.m.Session(today=self.today, **kw)

    def test_fuzz_never_crashes_and_never_speaks_out_of_turn(self):
        import random
        rng = random.Random(20261117)
        seeds = ["VT1\tHELLO\t1\tx", "VT1\tSTATE", "VT1\tCLAIM", "VT1\tPING",
                 "VT1\tS\t12\t50\t1\tAdept\t1", "AzerothCore\tp\t1234\thelp"]
        alphabet = "VT1\\tHELLO STATESCLAIMPING xyz\t\n\r\x00héllo" + "0123456789ABC"
        session = self.session()
        produced = 0
        for _ in range(4000):
            mode = rng.random()
            if mode < 0.45:
                payload = rng.choice(seeds)
                for _ in range(rng.randrange(0, 4)):
                    pos = rng.randrange(len(payload) + 1)
                    payload = payload[:pos] + rng.choice(alphabet) + payload[pos:]
            elif mode < 0.8:
                payload = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 300)))
            else:
                payload = "VT1\t" + "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 40)))
            replies = session.handle(payload)
            self.assertIsInstance(replies, list)
            for reply in replies:
                produced += 1
                self.assertLessEqual(len(reply), self.m.MAX_BYTES, reply)
                parsed = self.m.parse(reply)
                self.assertIsNotNone(parsed, f"the model emitted an unparseable frame: {reply!r}")
                self.assertIn(parsed[0], self.m.SERVER_OPCODES)
                if parsed[0] == "ERR":
                    self.assertIn(parsed[1][0], self.m.ERRORS)
        self.assertGreater(produced, 100, "fuzz corpus never reached a reply — the test would be decorative")

    def test_things_that_are_not_ours_are_silent(self):
        session = self.session()
        for payload in ["AzerothCore\tp\t1234\thelp", "AzerothCore\th\t1234\t.reload",
                        "vt1\tSTATE", "VT1STATE", "VT1", "VT1\t", "VT1\tstate",
                        "\tSTATE", "VT1\t" + "A" * 300]:
            self.assertEqual(session.handle(payload), [], payload)

    def test_the_cap_is_the_transports_not_ours(self):
        self.assertEqual(len("VT1\tOK\t") + 248, 255)          # 7 bytes of framing
        self.assertEqual(self.session().handle("VT1\tOK\t" + "x" * 248), [])
        self.assertEqual(self.session().handle("VT1\tOK\t" + "x" * 249), [])   # 256: dropped
        self.assertEqual(self.session().handle("VT1\t" + "x" * 252), [])

    def test_cooldown_resets_at_midnight_not_at_logout(self):
        import datetime
        session = self.session()
        self.assertTrue(session.handle("VT1\tCLAIM")[0].startswith("VT1\tOK"))
        session.advance_to(datetime.date(2026, 11, 18))
        self.assertTrue(session.handle("VT1\tCLAIM")[0].startswith("VT1\tOK"))
        self.assertEqual(session.total_seals, 2)
        self.assertEqual(session.handle("VT1\tSTATE")[0].split("\t")[4], "0")   # claimsLeft

    def test_a_client_cannot_claim_twice_by_reconnecting(self):
        first = self.session(guid=7)
        second = self.m.Session(today=self.today, store=first.store, guid=7)   # a second connection
        self.assertTrue(first.handle("VT1\tCLAIM")[0].startswith("VT1\tOK"))
        for connection in (second, first, second):
            self.assertEqual(connection.handle("VT1\tCLAIM"),
                             ["VT1\tERR\tCOOLDOWN\talready claimed today"])
        self.assertEqual(first.total_seals, 1)
        other = self.m.Session(today=self.today, store=first.store, guid=8)    # a different character
        self.assertTrue(other.handle("VT1\tCLAIM")[0].startswith("VT1\tOK"))

    def test_a_forged_state_frame_does_not_change_anything(self):
        session = self.session()
        session.handle("VT1\tS\t9999\t50\t1\tVaultmaster\t1")
        self.assertEqual(session.total_seals, 0)
        self.assertEqual(session.handle("VT1\tSTATE"), ["VT1\tS\t0\t50\t1\tInitiate\t1"])

    def test_disabled_server_still_speaks(self):
        session = self.session(disabled=True)
        for payload in ("VT1\tHELLO\t1\tx", "VT1\tSTATE", "VT1\tCLAIM", "VT1\tPING"):
            self.assertEqual(session.handle(payload), ["VT1\tBYE\tserver is under maintenance"])
        self.assertEqual(session.total_seals, 0)


class TheComparisonCanFail(unittest.TestCase):
    """A conformance check nobody has seen fail is a check nobody should trust."""

    def test_a_drifted_peer_is_reported(self):
        m = helpers.vt_model()
        good = {
            "prefix": m.PREFIX, "version": str(m.VERSION), "max_bytes": m.MAX_BYTES,
            "target_seals": m.TARGET_SEALS, "claims_per_day": m.CLAIMS_PER_DAY,
            "ranks": {s: n for s, n in m.RANKS},
        }
        self.assertEqual(m.constant_mismatches(good), [])
        for key, bad in (("prefix", "VT2"), ("version", "2"), ("max_bytes", 256),
                         ("ranks", {50: "Keeper", 25: "Adept", 10: "Initiate", 0: "Novice"})):
            problems = m.constant_mismatches({**good, key: bad})
            self.assertTrue(any(key in p for p in problems), f"{key}={bad!r} was not detected")

    def test_the_model_rejects_what_a_sloppy_implementation_would_send(self):
        m = helpers.vt_model()
        with self.assertRaises(m.FrameError):
            m.build("OK", "y" * 250)              # 258 bytes: the transport would truncate it
        with self.assertRaises(m.FrameError):
            m.build("err")                        # opcodes are upper-case by grammar
        with self.assertRaises(m.FrameError):
            m.build("OK", "two\tfields")          # a separator inside a field
        with self.assertRaises(AssertionError):
            m.Session(today=datetime.date(2026, 11, 17))._err("NOTACODE", "x")  # closed set

    def test_throughput_is_not_a_reason_to_batch(self):
        import datetime
        session = helpers.vt_model().Session(today=datetime.date(2026, 11, 17))
        start = time.perf_counter()
        for _ in range(20000):
            session.handle("VT1\tSTATE")
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0, f"20k STATE round trips took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
