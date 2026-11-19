#include "ArcaneEchoPolicy.h"

#include <cassert>
#include <cstdint>

int main()
{
    using RunicCodexPolicy::CalculateRefund;
    using RunicCodexPolicy::ManaSpent;

    assert(ManaSpent(1000, 900) == 100);
    assert(ManaSpent(1000, 1000) == 0);
    assert(ManaSpent(900, 1000) == 0); // regeneration cannot masquerade as a paid cast

    // Real rank-chain members only; flooring and the 100-mana ceiling are stable.
    assert(CalculateRefund(5143, true, true, 99, 99) == 9);
    assert(CalculateRefund(42846, true, true, 1, 1) == 0);
    assert(CalculateRefund(42846, true, true, 999, 999) == 99);
    assert(CalculateRefund(42846, true, true, 1000, 1000) == 100);
    assert(CalculateRefund(42846, true, true, 2500, 2500) == 100);

    // The effect fails closed when the power is locked/unequipped or not paid in mana.
    assert(CalculateRefund(42846, false, true, 1000, 1000) == 0);
    assert(CalculateRefund(42846, true, false, 1000, 1000) == 0);
    assert(CalculateRefund(42846, true, true, 1000, 999) == 0);
    assert(CalculateRefund(42846, true, true, 0, 0) == 0);
    assert(CalculateRefund(5142, true, true, 1000, 1000) == 0);
    return 0;
}
