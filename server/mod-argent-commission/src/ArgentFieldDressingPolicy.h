#pragma once

#include <algorithm>
#include <cstdint>

namespace ArgentFieldDressingPolicy
{
    constexpr uint32_t MAX_HEAL = 150;
    constexpr uint32_t HEAL_PERCENT = 2;

    constexpr uint32_t CalculateHeal(uint32_t maxHealth, uint32_t missingHealth)
    {
        uint32_t const percent = (maxHealth / 100) * HEAL_PERCENT
            + ((maxHealth % 100) * HEAL_PERCENT) / 100;
        return std::min({ percent, MAX_HEAL, missingHealth });
    }
}
