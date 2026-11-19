#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace RunicPowerRegistry
{
    constexpr std::size_t MAX_POWER_SPELLS = 14;
    constexpr std::uint32_t MAX_MANA_REFUND = 100;
    // AzerothCore SpellSchoolMask bit values from SharedDefines.h.
    constexpr std::uint32_t SPELL_SCHOOL_MASK_FIRE = 0x04;
    constexpr std::uint32_t SPELL_SCHOOL_MASK_SHADOW = 0x20;

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
        std::uint32_t spellDamageReductionPercent;
        std::uint32_t spellDamageReductionCap;
        std::uint32_t spellDamageSchoolMask;
        std::uint32_t healingReceivedBonusPercent;
        std::uint32_t healingReceivedBonusCap;
    };

    // Sources: AzerothCore pin 6ffb62abb867dabb258de6813ff5d06d6b011543.
    // ItemTemplate/loot evidence: 22739 -> Polymorph Turtle (28271), Gahz'ranka;
    // 3122 -> Holy Word: Shield III (600), quest 3121; 21281 -> Shadow Bolt X (25307),
    // AQ reference loot 34024; 22891 -> Shadow Ward IV (28610), reference loot 30171
    // used by Ragnaros and Nefarian. Spell-rank prerequisites are from spell_ranks.sql.
    constexpr std::array<PowerDefinition, 6> POWERS = {{
        {"arcane_echo", "Arcane Echo", 22739, 28271,
         {5143,5144,5145,8416,8417,10211,10212,25345,27075,38699,38704,42843,42846,0},
         {5143,5144,5145,8416,8417,10211,10212,25345,27075,38699,38704,42843,42846,0},
         10, MAX_MANA_REFUND, 1, 0, 0, 0, 0, 0},
        {"aegis_echo", "Aegis Echo", 3122, 600,
         {17,592,600,3747,6065,6066,10898,10899,10900,10901,25217,25218,48065,48066},
         {17,592,600,3747,6065,6066,10898,10899,10900,10901,25217,25218,48065,48066},
         5, 60, 1, 0, 0, 0, 0, 0},
        // Turtle tome use unlocks this combat ward only for characters who know a native
        // Arcane Missiles rank; an unset school filter preserves the original all-magic rule.
        {"arcane_ward", "Arcane Ward", 22739, 28271,
         {5143,5144,5145,8416,8417,10211,10212,25345,27075,38699,38704,42843,42846,0},
         {}, 0, 0, 1, 8, 300, 0, 0, 0},
        // The same verified inscription can reveal a second, class-appropriate effect;
        // Rejuvenation is the prerequisite and native spell healing remains authoritative.
        {"verdant_ward", "Verdant Ward", 22739, 28271,
         {774,1058,1430,2090,2091,3627,8910,9839,9840,9841,25299,26981,48440,48441},
         {}, 0, 0, 1, 0, 0, 0, 8, 300},
        // AQ-sourced Grimoire of Shadow Bolt X refunds a capped share of actually spent
        // mana on native Shadow Bolt ranks; acquisition and prerequisite chains stay separate.
        {"umbral_echo", "Umbral Echo", 21281, 25307,
         {686,695,705,1088,1106,7641,11659,11660,11661,25307,27209,47808,47809,0},
         {686,695,705,1088,1106,7641,11659,11660,11661,25307,27209,47808,47809,0},
         5, 75, 1, 0, 0, 0, 0, 0},
        // The AQ Grimoire of Shadow Ward IV reduces only incoming Shadow-school damage.
        {"shadow_ward", "Shadow Ward", 22891, 28610,
         {6229,11739,11740,28610,47890,47891,0,0,0,0,0,0,0,0},
         {}, 0, 0, 1, 6, 200, SPELL_SCHOOL_MASK_SHADOW, 0, 0},
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
        std::uint32_t const percentage = static_cast<std::uint32_t>(
            (static_cast<std::uint64_t>(cost) * boundedPercent) / 100);
        std::uint32_t const boundedCap = cap < MAX_MANA_REFUND ? cap : MAX_MANA_REFUND;
        return percentage < boundedCap ? percentage : boundedCap;
    }

    constexpr std::uint32_t CalculateCombatBonus(std::uint32_t amount, std::uint32_t percent,
                                                std::uint32_t cap, bool unlocked, bool equipped)
    {
        if (!unlocked || !equipped || amount == 0 || percent == 0 || cap == 0)
            return 0;
        std::uint32_t const boundedPercent = percent > 100 ? 100 : percent;
        std::uint64_t const raw = (static_cast<std::uint64_t>(amount) * boundedPercent) / 100;
        return static_cast<std::uint32_t>(raw < cap ? raw : cap);
    }

    constexpr bool MatchesSpellSchool(std::uint32_t incomingMask, std::uint32_t requiredMask)
    {
        return requiredMask == 0 || (incomingMask & requiredMask) != 0;
    }

    constexpr std::uint32_t CalculateSchoolFilteredCombatBonus(
        std::uint32_t amount, std::uint32_t percent, std::uint32_t cap,
        bool unlocked, bool equipped, std::uint32_t incomingMask, std::uint32_t requiredMask)
    {
        if (!MatchesSpellSchool(incomingMask, requiredMask))
            return 0;
        return CalculateCombatBonus(amount, percent, cap, unlocked, equipped);
    }
}
