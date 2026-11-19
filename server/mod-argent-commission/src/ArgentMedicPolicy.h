#pragma once

#include <cstdint>

namespace ArgentMedicPolicy
{
    constexpr std::uint32_t MEDIC_CREATURE_ENTRY = 16284;
    constexpr std::uint32_t CURE_DISEASE_SPELL = 28133;
    constexpr std::uint32_t SERVICE_RANGE_YARDS = 10;
    constexpr std::uint32_t DEFAULT_COOLDOWN_SECONDS = 60;
    constexpr std::uint32_t MIN_COOLDOWN_SECONDS = 30;
    constexpr std::uint32_t MAX_COOLDOWN_SECONDS = 3600;

    constexpr bool CanRequestService(bool commissioned, bool currentlyHonored,
                                     bool nearLivingMedic, bool spellAvailable)
    {
        return commissioned && currentlyHonored && nearLivingMedic && spellAvailable;
    }

    constexpr std::uint32_t BoundCooldown(std::uint32_t seconds)
    {
        return seconds < MIN_COOLDOWN_SECONDS ? MIN_COOLDOWN_SECONDS
             : seconds > MAX_COOLDOWN_SECONDS ? MAX_COOLDOWN_SECONDS : seconds;
    }
}
