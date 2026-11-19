#include "RunicPowerRegistry.h"
#include <cassert>
int main()
{
    using namespace RunicPowerRegistry;
    static_assert(POWERS.size() == 4);
    auto const* arcane = Find("arcane_echo");
    auto const* aegis = Find("aegis_echo");
    auto const* ward = Find("arcane_ward");
    auto const* verdant = Find("verdant_ward");
    assert(arcane && arcane->discoveryItem == 22739 && arcane->discoveryCastSpell == 28271);
    assert(aegis && aegis->discoveryItem == 3122 && aegis->discoveryCastSpell == 600);
    assert(Contains(aegis->prerequisites, 48066));
    assert(Contains(aegis->affectedSpells, 17));
    assert(FindByDiscovery(3122, 600) == aegis);
    assert(FindByDiscovery(3122, 17) == nullptr);
    assert(ward && ward->discoveryItem == 22739 && ward->spellDamageReductionPercent == 8);
    assert(Contains(ward->prerequisites, 42846));
    assert(verdant && verdant->healingReceivedBonusPercent == 8);
    assert(Contains(verdant->prerequisites, 48441));
    assert(CalculateRefund(200, 200, 5, 60, true, true) == 10);
    assert(CalculateRefund(2000, 2000, 5, 60, true, true) == 60);
    assert(CalculateRefund(200, 199, 5, 60, true, true) == 0);
    assert(CalculateRefund(200, 200, 5, 60, false, true) == 0);
    assert(CalculateRefund(200, 200, 5, 60, true, false) == 0);
    assert(CalculateRefund(UINT32_MAX, UINT32_MAX, 1000, UINT32_MAX, true, true) == 100);
    assert(CalculateCombatBonus(1000, 8, 300, true, true) == 80);
    assert(CalculateCombatBonus(10000, 8, 300, true, true) == 300);
    assert(CalculateCombatBonus(1000, 8, 300, false, true) == 0);
    assert(CalculateCombatBonus(1000, 8, 300, true, false) == 0);
    assert(CalculateCombatBonus(0, 8, 300, true, true) == 0);
}
