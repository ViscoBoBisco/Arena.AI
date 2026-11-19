"""Executable unit and source-contract regression tests for the Argent support perk."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POLICY = REPO / "server/mod-argent-commission/src/ArgentFieldDressingPolicy.h"
MODULE = REPO / "server/mod-argent-commission/src/ArgentCommission.cpp"


class ArgentFieldDressingPolicy(unittest.TestCase):
    def test_policy_boundaries_in_native_cpp(self):
        compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a C++ compiler is required for the native policy regression")
        harness = r'''#include "ArgentFieldDressingPolicy.h"
#include <cassert>
int main() {
    using ArgentFieldDressingPolicy::CalculateHeal;
    assert(CalculateHeal(1000, 500) == 20);
    assert(CalculateHeal(10000, 9000) == 150); // hard cap
    assert(CalculateHeal(10000, 50) == 50); // never overheal
    assert(CalculateHeal(10000, 0) == 0); // full health
    assert(CalculateHeal(99, 99) == 1); // integer-floor rounding
    assert(CalculateHeal(10000, 9000, 3, 250) == 250); // Exalted tier
    assert(CalculateHeal(10000, 9000, 3, 0) == 0); // configured cap can disable benefit
    assert(CalculateHeal(10000, 9000, 1000, 500) == 500); // percent is bounded
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "policy_test.cpp"
            binary = Path(temporary) / "policy_test"
            source.write_text(harness, encoding="utf-8")
            subprocess.run([compiler, "-std=c++14", "-Wall", "-Wextra", "-Werror",
                            "-I", str(POLICY.parent), str(source), "-o", str(binary)],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)

    def test_effect_requires_earned_entitlement_and_live_native_honored_rank(self):
        source = MODULE.read_text(encoding="utf-8")
        hook = source[source.index("void OnPlayerCreatureKill("):source.index("void OnPlayerBeforeSendChatMessage")]
        self.assertIn("PLAYERHOOK_ON_CREATURE_KILL", source)
        self.assertIn("earnedCommissions.find(guid) == earnedCommissions.end()", hook)
        self.assertIn("GetReputationRank(ARGENT_CRUSADE_FACTION) < REP_HONORED", hook)
        self.assertIn("CalculateHeal(", hook)
        self.assertIn('"ArgentCommission.FieldDressing.ExaltedPercent"', hook)
        self.assertIn('"ArgentCommission.FieldDressing.ExaltedCap"', hook)
        self.assertIn("ModifyHealth(static_cast<int32>(heal))", hook)
        self.assertIn("killer->GetMaxHealth() - killer->GetHealth()", hook)
        self.assertNotIn("SetFaction(", source)
        self.assertNotIn("ModifyReputation(", source)


if __name__ == "__main__":
    unittest.main()
