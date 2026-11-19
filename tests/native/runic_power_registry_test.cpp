#include "RunicPowerRegistry.h"
#include <cassert>
int main()
{
    using namespace RunicPowerRegistry;
    static_assert(POWERS.size() == 2);
    auto const* arcane = Find("arcane_echo");
    auto const* aegis = Find("aegis_echo");
    assert(arcane && arcane->discoveryItem == 22739 && arcane->discoveryCastSpell == 28271);
    assert(aegis && aegis->discoveryItem == 3122 && aegis->discoveryCastSpell == 600);
    assert(Contains(aegis->prerequisites, 48066));
    assert(Contains(aegis->affectedSpells, 17));
    assert(FindByDiscovery(3122, 600) == aegis);
    assert(FindByDiscovery(3122, 17) == nullptr);
    assert(CalculateRefund(200, 200, 5, 60, true, true) == 10);
    assert(CalculateRefund(2000, 2000, 5, 60, true, true) == 60);
    assert(CalculateRefund(200, 199, 5, 60, true, true) == 0);
    assert(CalculateRefund(200, 200, 5, 60, false, true) == 0);
    assert(CalculateRefund(200, 200, 5, 60, true, false) == 0);
    assert(CalculateRefund(200, 200, 1000, 1000, true, true) == 100);
}
