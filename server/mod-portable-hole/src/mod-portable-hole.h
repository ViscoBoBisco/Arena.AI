#ifndef ARENA_MOD_PORTABLE_HOLE_LOADER_H
#define ARENA_MOD_PORTABLE_HOLE_LOADER_H

#include <cstdint>
#include <string>

class Player;

void AddPortableHoleScripts();
void PortableHoleCreditFromServer(Player* player, std::uint32_t itemEntry, std::uint64_t amount,
                                  std::string const& requestId);
void PortableHoleConsumeReservation(Player* player, std::string const& reservationId,
                                    std::string const& requestId);

#endif
