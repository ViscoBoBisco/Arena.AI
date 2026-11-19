"""MySQL acceptance for the Portable Hole virtual commodity reservation contract."""
from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402
sys.path.insert(0, str(helpers.REPO))
sys.path.insert(0, str(helpers.REPO / "server/mod-portable-hole/harness"))
import portable_hole_mysql as ledger  # noqa: E402

_REQUIRED = ("ARENA_MYSQL_HOST", "ARENA_MYSQL_DATABASE", "ARENA_MYSQL_USER",
             "ARENA_MYSQL_PASSWORD")
_ENABLED = all(os.environ.get(key) is not None for key in _REQUIRED)
_MIGRATIONS = sorted((helpers.REPO / "server/mod-portable-hole/data/sql/characters").glob("*.sql"))
_MODULE = helpers.REPO / "server/mod-portable-hole/src/PortableHole.cpp"


def _crash_after_reserve_update(guid: int, item: int, request_id: str, env: dict) -> None:
    for key, value in env.items():
        os.environ[key] = value
    import pymysql
    conn = ledger.connect(pymysql)
    conn.begin()
    with conn.cursor() as cursor:
        fingerprint = ledger._fingerprint("RESERVE", item, 60, request_id)
        cursor.execute(
            "INSERT IGNORE INTO portable_hole_receipts "
            "(guid,request_id,fingerprint,action,status,item_entry,amount,reservation_id,source,actor_guid) "
            "VALUES(%s,%s,%s,'RESERVE','PENDING',%s,60,%s,'TEST_PLAYER',%s)",
            (guid, request_id, fingerprint, item, request_id, guid))
        cursor.execute(
            "UPDATE portable_hole_commodities SET last_request_id=%s,reserved=reserved+60 "
            "WHERE guid=%s AND item_entry=%s AND balance-reserved>=60",
            (request_id, guid, item))
    os._exit(77)  # the DB connection dies before receipt/reservation commit


@unittest.skipUnless(_ENABLED, "requires explicitly configured disposable MySQL")
class PortableHoleMySQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pymysql
        except ImportError as exc:
            raise unittest.SkipTest("install PyMySQL for disposable MySQL integration") from exc
        cls.pymysql = pymysql
        cls.database = os.environ["ARENA_MYSQL_DATABASE"]
        if not cls.database.startswith(ledger.PREFIX):
            raise AssertionError("target must be a named disposable schema")
        cls.guid = 990701
        cls.other_guid = 990702
        cls.item = 9907001  # synthetic fixture key; not a shipped/stock item claim
        cls.conn = ledger.connect(pymysql)
        with cls.conn.cursor() as cursor:
            for migration in _MIGRATIONS:
                sql = "\n".join(line for line in migration.read_text(encoding="utf-8").splitlines()
                                   if not line.lstrip().startswith("--"))
                for statement in sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        cursor.execute(statement)
        cls.conn.commit()
        cls.conn.close()
        cls._next_guid = 990710

    @classmethod
    def tearDownClass(cls):
        conn = ledger.connect(cls.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM portable_hole_vault_access WHERE owner_account_id BETWEEN 9907100 AND 9907199")
                for guid in range(990701, cls._next_guid + 1):
                    cursor.execute("DELETE FROM portable_hole_receipts WHERE guid=%s", (guid,))
                    cursor.execute("DELETE FROM portable_hole_reservations WHERE guid=%s", (guid,))
                    cursor.execute("DELETE FROM portable_hole_commodities WHERE guid=%s", (guid,))
                    cursor.execute("DELETE FROM portable_hole_pins WHERE guid=%s", (guid,))
                    cursor.execute("DELETE FROM portable_hole_pin_state WHERE guid=%s", (guid,))
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        type(self)._next_guid += 1
        self.guid = type(self)._next_guid
        self.other_guid = self.guid + 1000
        self.item = 9907001
        conn = ledger.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                for guid in (self.guid, self.other_guid):
                    cursor.execute("DELETE FROM portable_hole_receipts WHERE guid=%s", (guid,))
                    cursor.execute("DELETE FROM portable_hole_reservations WHERE guid=%s", (guid,))
                    cursor.execute("DELETE FROM portable_hole_commodities WHERE guid=%s", (guid,))
                    cursor.execute("DELETE FROM portable_hole_pins WHERE guid=%s", (guid,))
                    cursor.execute("DELETE FROM portable_hole_pin_state WHERE guid=%s", (guid,))
            conn.commit()
        finally:
            conn.close()

    def balance(self, guid=None):
        conn = ledger.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT balance,reserved FROM portable_hole_commodities "
                               "WHERE guid=%s AND item_entry=%s", (guid or self.guid, self.item))
                return cursor.fetchone()
        finally:
            conn.close()

    def test_vault_access_is_explicit_idempotent_and_revocable(self):
        owner_account, viewer_account, actor_guid = 9907101, 9907102, 990701
        conn = ledger.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                for _ in range(2):
                    cursor.execute(
                        "INSERT IGNORE INTO portable_hole_vault_access "
                        "(owner_account_id,viewer_account_id,granted_by_guid) VALUES(%s,%s,%s)",
                        (owner_account, viewer_account, actor_guid))
                cursor.execute("SELECT granted_by_guid FROM portable_hole_vault_access "
                               "WHERE owner_account_id=%s AND viewer_account_id=%s",
                               (owner_account, viewer_account))
                self.assertEqual(cursor.fetchall(), ((actor_guid,),))
                with self.assertRaises(self.pymysql.err.DatabaseError) as raised:
                    cursor.execute(
                        "INSERT INTO portable_hole_vault_access "
                        "(owner_account_id,viewer_account_id,granted_by_guid) VALUES(%s,%s,%s)",
                        (owner_account, owner_account, actor_guid))
                self.assertEqual(raised.exception.args[0], 3819)
                cursor.execute("DELETE FROM portable_hole_vault_access "
                               "WHERE owner_account_id=%s AND viewer_account_id=%s",
                               (owner_account, viewer_account))
                cursor.execute("SELECT owner_account_id FROM portable_hole_vault_access "
                               "WHERE owner_account_id=%s AND viewer_account_id=%s",
                               (owner_account, viewer_account))
                self.assertEqual(cursor.fetchall(), ())
            conn.commit()
        finally:
            conn.close()

    def test_receipt_replay_reserve_release_consume_and_fingerprint_conflict(self):
        first = ledger.credit(self.guid, self.item, 50, "credit-1", actor=77)
        self.assertEqual((first[1], first[2], first[6], first[7], first[8]),
                         ("CREDIT", "CREDITED", 50, 0, "TEST_SOURCE"))
        replay = ledger.credit(self.guid, self.item, 50, "credit-1", actor=77)
        self.assertEqual(replay[6], 50, "same request must not double credit")
        with self.assertRaisesRegex(ValueError, "different payload"):
            ledger.credit(self.guid, self.item, 5, "credit-1", actor=77)

        held = ledger.reserve(self.guid, self.item, 30, "reserve-1")
        self.assertEqual((held[1], held[2], held[6], held[7]),
                         ("RESERVE", "RESERVED", 50, 30))
        replayed = ledger.reserve(self.guid, self.item, 30, "reserve-1")
        self.assertEqual(replayed[2], "RESERVED")
        denied = ledger.reserve(self.guid, self.item, 30, "reserve-overdraw")
        self.assertEqual(denied[2], "DENIED_FUNDS")
        released = ledger.transition(self.guid, "reserve-1", "release-1", "RELEASE")
        self.assertEqual((released[2], released[6], released[7]), ("RELEASED", 50, 0))
        held_again = ledger.reserve(self.guid, self.item, 20, "reserve-2")
        self.assertEqual(held_again[2], "RESERVED")
        spent = ledger.transition(self.guid, "reserve-2", "consume-1", "CONSUME")
        self.assertEqual((spent[2], spent[6], spent[7]), ("CONSUMED", 30, 0))
        self.assertEqual(self.balance(), (30, 0))
        replayed_spend = ledger.transition(self.guid, "reserve-2", "consume-1", "CONSUME")
        self.assertEqual(replayed_spend[2], "CONSUMED")

    def test_cross_character_reads_and_reservation_transitions_are_scoped(self):
        ledger.credit(self.guid, self.item, 100, "private-credit")
        ledger.reserve(self.guid, self.item, 25, "private-reservation")
        self.assertIsNone(self.balance(self.other_guid))
        with self.assertRaisesRegex(ValueError, "reservation not found"):
            ledger.transition(self.other_guid, "private-reservation", "foreign-release", "RELEASE")
        self.assertEqual(self.balance(), (100, 25))
        conn = ledger.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM portable_hole_receipts WHERE guid=%s",
                               (self.other_guid,))
                self.assertEqual(cursor.fetchone()[0], 0)
        finally:
            conn.close()

    def test_concurrent_replay_of_same_reservation_request_mutates_once(self):
        ledger.credit(self.guid, self.item, 100, "same-request-credit")
        barrier = threading.Barrier(2)
        receipts, errors = [], []

        def worker():
            try:
                barrier.wait(timeout=5)
                receipts.append(ledger.reserve(self.guid, self.item, 70, "same-request"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads), "same-request race deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual([receipt[2] for receipt in receipts], ["RESERVED", "RESERVED"])
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(self.balance(), (100, 70))
        conn = ledger.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM portable_hole_reservations "
                               "WHERE guid=%s AND reservation_id='same-request' AND state='ACTIVE'",
                               (self.guid,))
                self.assertEqual(cursor.fetchone()[0], 1)
        finally:
            conn.close()

    def test_competing_reservations_are_serialized_and_cannot_oversubscribe(self):
        ledger.credit(self.guid, self.item, 100, "race-credit")
        barrier = threading.Barrier(2)
        outcomes, errors = [], []

        def worker(index):
            try:
                barrier.wait(timeout=5)
                outcomes.append(ledger.reserve(self.guid, self.item, 70, f"race-{index}")[2])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads), "reserve race deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(sorted(outcomes), ["DENIED_FUNDS", "RESERVED"])
        balance, reserved = self.balance()
        self.assertEqual(balance, 100)
        self.assertEqual(reserved, 70)

    def test_process_death_rolls_back_balance_receipt_and_reservation_then_same_id_retries(self):
        ledger.credit(self.guid, self.item, 100, "crash-credit")
        env = {key: os.environ[key] for key in _REQUIRED}
        proc = multiprocessing.get_context("fork").Process(
            target=_crash_after_reserve_update,
            args=(self.guid, self.item, "crash-reserve", env))
        proc.start(); proc.join(timeout=20)
        if proc.is_alive():
            proc.kill(); proc.join(timeout=5)
            self.fail("warehouse crash worker hung")
        self.assertEqual(proc.exitcode, 77)
        self.assertEqual(self.balance(), (100, 0))
        conn = ledger.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM portable_hole_receipts "
                               "WHERE guid=%s AND request_id=%s", (self.guid, "crash-reserve"))
                self.assertEqual(cursor.fetchone()[0], 0)
                cursor.execute("SELECT COUNT(*) FROM portable_hole_reservations "
                               "WHERE guid=%s AND reservation_id=%s", (self.guid, "crash-reserve"))
                self.assertEqual(cursor.fetchone()[0], 0)
        finally:
            conn.close()
        receipt = ledger.reserve(self.guid, self.item, 60, "crash-reserve")
        self.assertEqual(receipt[2], "RESERVED")
        self.assertEqual(self.balance(), (100, 60))

    def test_server_pins_are_persistent_scoped_capped_and_idempotent(self):
        for item in range(9907100, 9907110):
            self.assertTrue(ledger.mutate_pin(self.guid, item, True))
        self.assertTrue(ledger.mutate_pin(self.guid, 9907100, True), "duplicate pin is idempotent")
        self.assertFalse(ledger.mutate_pin(self.guid, 9907110, True), "eleventh pin is refused")
        self.assertEqual(len(ledger.pins(self.guid)), 10)
        self.assertEqual(ledger.pins(self.other_guid), [])
        self.assertTrue(ledger.mutate_pin(self.guid, 9907105, False))
        self.assertTrue(ledger.mutate_pin(self.guid, 9907110, True))
        self.assertEqual(set(ledger.pins(self.guid)),
                         set(range(9907100, 9907105)) | set(range(9907106, 9907111)))

    def test_concurrent_pin_adds_serialize_at_ten(self):
        for item in range(9907200, 9907209):
            self.assertTrue(ledger.mutate_pin(self.guid, item, True))
        barrier = threading.Barrier(2)
        results, errors = [], []

        def worker(item):
            try:
                barrier.wait(timeout=5)
                results.append(ledger.mutate_pin(self.guid, item, True))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(entry,))
                   for entry in (9907209, 9907210)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads), "pin cap race deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(ledger.pins(self.guid)), 10)

    def test_credit_capacity_rejects_overflow_without_mutation(self):
        conn = ledger.connect(self.pymysql)
        try:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO portable_hole_commodities "
                               "(guid,item_entry,balance,reserved) VALUES(%s,%s,%s,0)",
                               (self.guid, self.item, ledger.MAX_BALANCE))
            conn.commit()
        finally:
            conn.close()
        result = ledger.credit(self.guid, self.item, 1, "overflow-credit")
        self.assertEqual(result[2], "DENIED_CAPACITY")
        self.assertEqual(self.balance(), (ledger.MAX_BALANCE, 0))


class PortableHoleStaticContract(unittest.TestCase):
    def test_addon_parses_under_wotlk_lua_51(self):
        try:
            from lupa.lua51 import LuaRuntime
        except ImportError:
            self.skipTest("install lupa==2.6 for Lua 5.1 addon syntax validation")
        source = (helpers.REPO / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua").read_text(
            encoding="utf-8")
        compiled = LuaRuntime().eval("loadstring")(source, "@PortableHole.lua")
        self.assertIsNotNone(compiled)

    def test_native_module_and_addon_preserve_server_owned_identity_contract(self):
        source = _MODULE.read_text(encoding="utf-8")
        lua = (helpers.REPO / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua").read_text(
            encoding="utf-8")
        self.assertIn("player->GetGUID().GetCounter()", source)
        self.assertIn("player->IsGameMaster()", source)
        self.assertIn("GetMaxStackSize() <= 1", source)
        self.assertIn("portable_hole_receipts", source)
        self.assertIn("portable_hole_pins", source)
        self.assertIn("portable_hole_pin_state", source)
        self.assertIn("request_id", source)
        self.assertNotIn("GetGUID", lua, "client must not choose a character identity")
        self.assertNotIn("RegisterAddonMessagePrefix", lua,
                         "Interface 30300 clients do not expose modern prefix registration")
        self.assertIn("data.pending", lua)
        header = (helpers.REPO / "server/mod-portable-hole/src/mod-portable-hole.h").read_text()
        self.assertIn("PortableHoleCreditFromServer", header)
        self.assertIn("PortableHoleConsumeReservation", header)

    def test_shared_vault_requires_owner_consent_and_only_expands_read_views(self):
        source = _MODULE.read_text(encoding="utf-8")
        addon = (helpers.REPO / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua").read_text(
            encoding="utf-8")
        migration = (helpers.REPO / "server/mod-portable-hole/data/sql/characters/002_portable_hole_vault_access.sql").read_text(
            encoding="utf-8")
        self.assertIn("player->GetSession()->GetAccountId()", source)
        self.assertIn("owner_account_id={} AND viewer_account_id={}", source)
        self.assertIn('action == "SHARE"', source)
        self.assertIn('fields[3] != "GRANT" && fields[3] != "REVOKE"', source)
        self.assertIn("INNER JOIN `characters` c ON c.guid=b.guid WHERE c.account={}", source)
        self.assertIn('SendError(player, "VAULT_ACCESS_DENIED")', source)
        self.assertIn("CREATE TABLE IF NOT EXISTS `portable_hole_vault_access`", migration)
        self.assertIn("PRIMARY KEY (`owner_account_id`, `viewer_account_id`)", migration)
        self.assertIn('send("SHARE", account, grant and "GRANT" or "REVOKE")', addon)
        self.assertIn('send("LIST", page, sortModes[sortIndex], viewAccountId)', addon)
        self.assertIn("read-only account view", addon)
        reserve = source[source.index("void HandleReserve("):source.index("void HandleReservationTransition(")]
        self.assertIn("player->GetGUID().GetCounter()", reserve)
        self.assertNotIn("viewAccount", reserve,
                         "shared views must not redirect mutations or reservation ownership")

    def test_profession_recipe_reagents_are_server_verified_and_read_only(self):
        source = _MODULE.read_text(encoding="utf-8")
        addon = (helpers.REPO / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua").read_text(
            encoding="utf-8")
        planner = source[source.index("void SendProfessionReagentPlan("):source.index("void SendBagCount(")]
        self.assertIn("sSpellMgr->GetSpellInfo(spellId)", planner)
        self.assertIn("player->HasSpell(spellId)", planner)
        self.assertIn("spellInfo->IsProfession()", planner)
        self.assertIn("MAX_SPELL_REAGENTS", planner)
        self.assertIn("spellInfo->ReagentCount[index]", planner)
        self.assertIn("player->GetItemCount(itemEntry)", planner)
        self.assertIn("VIRTUAL_AVAILABLE_NOT_CRAFTABLE", planner)
        self.assertIn("STANDARD_BAG_ONLY", planner)
        self.assertIn('action == "REAGENTS"', source)
        self.assertIn('send("REAGENTS", spell)', addon)
        self.assertIn("crafting still uses bags", addon)
        for mutation in ("RemoveItem(", "StoreItem(", "DestroyItem("):
            self.assertNotIn(mutation, planner)

    def test_saved_pins_are_per_character_deduplicated_and_capped(self):
        try:
            from lupa.lua51 import LuaRuntime
        except ImportError:
            self.skipTest("install lupa==2.6 for Lua 5.1 addon behavior validation")
        source = (helpers.REPO / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua").read_text(
            encoding="utf-8")
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute(r"""
            created, activeGuid, sent, clock = {}, "Player-A", {}, 1
            UIParent, SlashCmdList, PortableHoleDB = {}, {}, {}
            function UnitGUID() return activeGuid end
            function UnitName() return "Tester" end
            function RegisterAddonMessagePrefix() end
            function SendAddonMessage(_, payload) sent[#sent + 1] = payload end
            function time() return 1 end
            function GetTime() return clock end
            function CreateFrame()
                local f = { scripts = {}, shown = false, text = "",
                            TitleText = { SetText = function() end } }
                function f:SetSize() end
                function f:SetPoint() end
                function f:SetBackdrop() end
                function f:SetBackdropColor() end
                function f:SetBackdropBorderColor() end
                function f:SetMovable() end
                function f:EnableMouse() end
                function f:RegisterForDrag() end
                function f:RegisterEvent() end
                function f:SetAutoFocus() end
                function f:SetNumeric() self.numeric = true end
                function f:SetText(value) self.text = value end
                function f:GetText() return self.text end
                function f:SetScript(name, callback) self.scripts[name] = callback end
                function f:Hide() self.shown = false end
                function f:Show() self.shown = true end
                function f:IsShown() return self.shown end
                function f:StartMoving() end
                function f:StopMovingOrSizing() end
                function f:CreateFontString()
                    local label = {}
                    function label:SetPoint() end
                    function label:SetText(value) self.text = value end
                    return label
                end
                table.insert(created, f)
                return f
            end
        """)
        lua.execute(source)
        event_frame = lua.globals().created[1]
        event_frame.scripts.OnEvent(event_frame, "PLAYER_LOGIN")
        lua.globals().SlashCmdList.PORTABLEHOLE()
        created = lua.globals().created
        item_input = next(created[index] for index in range(1, len(created) + 1)
                          if created[index].numeric and created[index].text == "0")
        buttons = {created[index].text: created[index]
                   for index in range(1, len(created) + 1)
                   if created[index].text in ("Pin item", "Next pin")}
        item_input.SetText(item_input, "123")
        buttons["Pin item"].scripts.OnClick(buttons["Pin item"])
        buttons["Pin item"].scripts.OnClick(buttons["Pin item"])
        pins = lua.globals().PortableHoleDB.characters["Player-A"].pins
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[1], 123)
        for expected_count, value in enumerate(range(1000, 1009), start=2):
            item_input.SetText(item_input, str(value))
            buttons["Pin item"].scripts.OnClick(buttons["Pin item"])
            current = lua.globals().PortableHoleDB.characters["Player-A"].pins
            self.assertEqual(len(current), expected_count, f"failed to add pin {value}")
        item_input.SetText(item_input, "9999")
        buttons["Pin item"].scripts.OnClick(buttons["Pin item"])
        pins = lua.globals().PortableHoleDB.characters["Player-A"].pins
        self.assertEqual(len(pins), 10)
        buttons["Next pin"].scripts.OnClick(buttons["Next pin"])
        self.assertEqual(item_input.GetText(item_input), "123")
        lua.globals().activeGuid = "Player-B"
        item_input.SetText(item_input, "222")
        buttons["Pin item"].scripts.OnClick(buttons["Pin item"])
        self.assertEqual(len(lua.globals().PortableHoleDB.characters["Player-B"].pins), 1)
        self.assertEqual(len(lua.globals().PortableHoleDB.characters["Player-A"].pins), 10)

        vault_input = next(created[index] for index in range(1, len(created) + 1)
                           if created[index].role == "vaultAccount")
        share_input = next(created[index] for index in range(1, len(created) + 1)
                           if created[index].role == "shareAccount")
        account_buttons = {created[index].text: created[index]
                           for index in range(1, len(created) + 1)
                           if created[index].text in ("Open view", "Grant", "Revoke")}
        vault_input.SetText(vault_input, "12345")
        lua.globals().clock = 2
        account_buttons["Open view"].scripts.OnClick(account_buttons["Open view"])
        self.assertEqual(lua.globals().sent[len(lua.globals().sent)],
                         "PH1\tLIST\t0\tENTRY\t12345")
        share_input.SetText(share_input, "98765")
        lua.globals().clock = 3
        account_buttons["Grant"].scripts.OnClick(account_buttons["Grant"])
        self.assertEqual(lua.globals().sent[len(lua.globals().sent)], "PH1\tSHARE\t98765\tGRANT")
        lua.globals().clock = 4
        account_buttons["Revoke"].scripts.OnClick(account_buttons["Revoke"])
        self.assertEqual(lua.globals().sent[len(lua.globals().sent)], "PH1\tSHARE\t98765\tREVOKE")
        recipe_input = next(created[index] for index in range(1, len(created) + 1)
                            if created[index].role == "recipeSpell")
        reagent_button = next(created[index] for index in range(1, len(created) + 1)
                              if created[index].text == "Reagents")
        recipe_input.SetText(recipe_input, "1234")
        lua.globals().clock = 5
        reagent_button.scripts.OnClick(reagent_button)
        self.assertEqual(lua.globals().sent[len(lua.globals().sent)], "PH1\tREAGENTS\t1234")

    def test_need_comparison_separates_carried_gap_and_virtual_available(self):
        source = _MODULE.read_text(encoding="utf-8")
        need = source.split("void SendNeedComparison(", 1)[1].split("void SendBagCount(", 1)[0]
        self.assertIn("player->GetItemCount(itemEntry)", need)
        self.assertIn("required > carried ? required - carried : 0", need)
        self.assertIn("balance - reserved", need)
        self.assertIn("bagGap, virtualAvailable", need)
        lua = (helpers.REPO / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua").read_text(
            encoding="utf-8")
        self.assertIn('send("NEED", item, required)', lua)
        self.assertIn("not craftable from this view", lua)

    def test_bag_count_is_read_only_server_derived_and_excludes_bank(self):
        source = _MODULE.read_text(encoding="utf-8")
        bag_count = source.split("void SendBagCount(", 1)[1].split("void SendBalancePage(", 1)[0]
        self.assertIn("player->GetItemCount(itemEntry)", bag_count)
        self.assertNotIn("DestroyItem", bag_count)
        self.assertNotIn("inBankAlso", bag_count)
        lua = (helpers.REPO / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua").read_text(
            encoding="utf-8")
        self.assertIn('send("BAG", item)', lua)
        self.assertIn("bank excluded; read-only", lua)
        self.assertIn('action == "BAG"', source)
        self.assertIn('SendBagCount(player, itemEntry)', source)

    def test_search_sort_and_bulk_deposit_plan_are_read_only(self):
        source = _MODULE.read_text(encoding="utf-8")
        self.assertIn('action == "SEARCH"', source)
        self.assertIn('action == "PLAN"', source)
        self.assertIn('sort != "ENTRY" && sort != "NAME" && sort != "BALANCE" && sort != "AVAILABLE"', source)
        planner = source.split("void SendBulkPlan(", 1)[1].split("void SendPins(", 1)[0]
        self.assertIn("player->GetItemCount(itemEntry, false)", planner)
        self.assertIn("std::min(carried, requested)", planner)
        for forbidden in ("RemoveItem(", "StoreItem(", "DestroyItem("):
            self.assertNotIn(forbidden, planner)
        lua = (helpers.REPO / "server/mod-portable-hole/addon/PortableHole/PortableHole.lua").read_text(
            encoding="utf-8")
        self.assertIn('send("SEARCH", query, viewAccountId)', lua)
        self.assertIn('send("PLAN", item, amount)', lua)
        self.assertIn("PLAN ONLY — no item moved", lua)

    def test_balance_row_is_locked_before_receipt_to_serialize_mutation_order(self):
        source = _MODULE.read_text(encoding="utf-8")
        methods = (
            source.split("void HandleCredit(", 1)[1].split("void HandleReserve(", 1)[0],
            source.split("void HandleReserve(", 1)[1].split("void HandleReservationTransition(", 1)[0],
            source.split("void HandleReservationTransition(", 1)[1].split("bool ParseAction(", 1)[0],
        )
        for method in methods:
            self.assertLess(method.index("EnsureBalanceRow(transaction"),
                            method.index("InsertPendingReceipt(transaction"))
        balance_helper = source.split("void EnsureBalanceRow(", 1)[1].split("enum class ReceiptLookup", 1)[0]
        self.assertIn("ON DUPLICATE KEY UPDATE", balance_helper)
        self.assertNotIn("INSERT IGNORE", balance_helper)


if __name__ == "__main__":
    unittest.main()
