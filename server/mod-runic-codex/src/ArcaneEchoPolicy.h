#ifndef ARENA_RUNIC_CODEX_ARCANE_ECHO_POLICY_H
#define ARENA_RUNIC_CODEX_ARCANE_ECHO_POLICY_H

#include <algorithm>
#include <array>
#include <cstdint>

namespace RunicCodexPolicy
{
    constexpr std::uint32_t MAX_MANA_REFUND = 100;
    constexpr std::array<std::uint32_t, 13> DISCOVERY_SPELLS = {
        5143, 5144, 5145, 8416, 8417, 10211, 10212, 25345, 27075, 38699, 38704, 42843, 42846
    };

    inline bool IsDiscoverySpell(std::uint32_t spellId)
    {
        return std::find(DISCOVERY_SPELLS.begin(), DISCOVERY_SPELLS.end(), spellId)
            != DISCOVERY_SPELLS.end();
    }

    inline std::uint32_t ManaSpent(std::uint32_t before, std::uint32_t after)
    {
        return before > after ? before - after : 0;
    }

    // The caller supplies mana actually removed by AzerothCore after the spell cast.
    // A cast rejected before full mana deduction, a free/triggered/unrelated spell,
    // or an unequipped power receives no refund.
    inline std::uint32_t CalculateRefund(std::uint32_t spellId, bool unlocked, bool equipped,
                                         std::uint32_t manaCost, std::uint32_t manaSpent)
    {
        if (!IsDiscoverySpell(spellId) || !unlocked || !equipped || manaCost == 0
            || manaSpent < manaCost)
            return 0;
        return std::min(MAX_MANA_REFUND, manaCost / 10);
    }
}

#endif
