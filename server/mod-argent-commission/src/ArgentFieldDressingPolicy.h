#pragma once

#include <algorithm>
#include <cstdint>

namespace ArgentFieldDressingPolicy
{
    constexpr uint32_t DEFAULT_HONORED_CAP = 150;
    constexpr uint32_t DEFAULT_EXALTED_CAP = 250;

    constexpr uint32_t CalculateHeal(uint32_t maxHealth, uint32_t missingHealth,
                                    uint32_t percent = 2, uint32_t cap = DEFAULT_HONORED_CAP)
    {
        uint32_t const boundedPercent = std::min(percent, 100u);
        uint32_t const percentHeal = (maxHealth / 100) * boundedPercent
            + ((maxHealth % 100) * boundedPercent) / 100;
        return std::min({ percentHeal, cap, missingHealth });
    }
}
