#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace RunicPowerRegistry
{
    constexpr std::size_t MAX_POWER_SPELLS = 14;
    constexpr std::uint32_t MAX_MANA_REFUND = 100;

    struct PowerDefinition
    {
        std::string_view key;
        std::string_view name;
        std::uint32_t discoveryItem;
        std::uint32_t discoveryCastSpell;
        std::array<std::uint32_t, MAX_POWER_SPELLS> prerequisites;
        std::array<std::uint32_t, MAX_POWER_SPELLS> affectedSpells;
        std::uint32_t refundPercent;
        std::uint32_t refundCap;
        std::uint8_t slotCost;
    };

    // Sources: AzerothCore pin 6ffb62abb867dabb258de6813ff5d06d6b011543.
    // 22739 -> item spell 28271, Gahz'ranka 15% loot; Arcane Missiles rank chain.
    // 3122 -> item spell 600; quest 3121 rewards the Codex; spell_ranks.sql lists
    // the complete Power Word: Shield rank chain, beginning with spell 17.
    constexpr std::array<PowerDefinition, 2> POWERS = {{
        {"arcane_echo", "Arcane Echo", 22739, 28271,
         {5143,5144,5145,8416,8417,10211,10212,25345,27075,38699,38704,42843,42846,0},
         {5143,5144,5145,8416,8417,10211,10212,25345,27075,38699,38704,42843,42846,0},
         10, MAX_MANA_REFUND, 1},
        {"aegis_echo", "Aegis Echo", 3122, 600,
         {17,592,600,3747,6065,6066,10898,10899,10900,10901,25217,25218,48065,48066},
         {17,592,600,3747,6065,6066,10898,10899,10900,10901,25217,25218,48065,48066},
         5, 60, 1},
    }};

    constexpr PowerDefinition const* Find(std::string_view key)
    {
        for (PowerDefinition const& power : POWERS)
            if (power.key == key)
                return &power;
        return nullptr;
    }

    constexpr PowerDefinition const* FindByDiscovery(std::uint32_t item, std::uint32_t castSpell)
    {
        for (PowerDefinition const& power : POWERS)
            if (power.discoveryItem == item && power.discoveryCastSpell == castSpell)
                return &power;
        return nullptr;
    }

    constexpr bool Contains(std::array<std::uint32_t, MAX_POWER_SPELLS> const& spells,
                            std::uint32_t spell)
    {
        for (std::uint32_t candidate : spells)
            if (candidate == spell)
                return true;
        return false;
    }

    constexpr std::uint32_t CalculateRefund(std::uint32_t cost, std::uint32_t spent,
                                            std::uint32_t percent, std::uint32_t cap,
                                            bool unlocked, bool equipped)
    {
        if (!unlocked || !equipped || cost == 0 || spent < cost)
            return 0;
        std::uint32_t const boundedPercent = percent > 100 ? 100 : percent;
        std::uint32_t const percentage = (cost / 100) * boundedPercent
            + ((cost % 100) * boundedPercent) / 100;
        std::uint32_t const boundedCap = cap < MAX_MANA_REFUND ? cap : MAX_MANA_REFUND;
        return percentage < boundedCap ? percentage : boundedCap;
    }
}
