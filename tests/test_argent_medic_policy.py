"""Native policy tests for the bounded Argent Medic NPC service."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "server/mod-argent-commission/src/ArgentMedicPolicy.h"


class ArgentMedicPolicyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "a C++ compiler is required")
    def test_authority_range_spell_and_cooldown_bounds(self):
        source = r'''#include "ArgentMedicPolicy.h"
#include <cassert>
int main() {
    using namespace ArgentMedicPolicy;
    static_assert(MEDIC_CREATURE_ENTRY == 16284);
    static_assert(CURE_DISEASE_SPELL == 28133);
    static_assert(SERVICE_RANGE_YARDS == 10);
    assert(CanRequestService(true, true, true, true));
    assert(!CanRequestService(false, true, true, true));
    assert(!CanRequestService(true, false, true, true));
    assert(!CanRequestService(true, true, false, true));
    assert(!CanRequestService(true, true, true, false));
    assert(BoundCooldown(0) == MIN_COOLDOWN_SECONDS);
    assert(BoundCooldown(DEFAULT_COOLDOWN_SECONDS) == 60);
    assert(BoundCooldown(MAX_COOLDOWN_SECONDS + 1) == MAX_COOLDOWN_SECONDS);
}
'''
        with tempfile.TemporaryDirectory(prefix="argent-medic-policy-") as temp:
            src = Path(temp) / "policy_test.cpp"
            binary = Path(temp) / "policy_test"
            src.write_text(source, encoding="utf-8")
            subprocess.run([shutil.which("c++"), "-std=c++14", "-Wall", "-Wextra",
                            "-Werror", "-I", str(HEADER.parent), str(src), "-o", str(binary)],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)

    def test_native_commission_service_uses_live_authority_and_existing_npc(self):
        source = (ROOT / "server/mod-argent-commission/src/ArgentCommission.cpp").read_text(
            encoding="utf-8")
        self.assertIn("ArgentMedicPolicy::CanRequestService", source)
        self.assertIn("HasCommission(player)", source)
        self.assertIn("GetReputationRank(ARGENT_CRUSADE_FACTION) >= REP_HONORED", source)
        self.assertIn("FindNearestCreature(ArgentMedicPolicy::MEDIC_CREATURE_ENTRY", source)
        self.assertIn("medic->CastSpell(player, ArgentMedicPolicy::CURE_DISEASE_SPELL, true)", source)
        self.assertIn("std::chrono::steady_clock", source)
        self.assertNotIn("ModifyReputation(", source)
        self.assertNotIn("SetFaction(", source)


if __name__ == "__main__":
    unittest.main()
