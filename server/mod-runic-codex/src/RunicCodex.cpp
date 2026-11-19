// Runic Codex: character-bound spell discovery, persistent equip state, bounded effect.
#include "ScriptMgr.h"
#include "Player.h"
#include "Spell.h"
#include "Chat.h"
#include "DatabaseEnv.h"
#include "StringFormat.h"
#include "ArcaneEchoPolicy.h"

#include <algorithm>
#include <charconv>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace
{
    constexpr char const* PREFIX = "RC1";
    constexpr char const* POWER_KEY = "arcane_echo";
    constexpr uint32 MAX_FRAME = 180;
    struct State
    {
        bool unlocked = false;
        bool equipped = false;
    };
    struct PendingRefund
    {
        uint32 spellId = 0;
        int32 beforeMana = 0;
        int32 cost = 0;
    };
    std::unordered_map<uint32, State> playerStates;
    std::unordered_map<uint32, PendingRefund> pendingRefunds;

    std::vector<std::string> SplitTabs(std::string const& value)
    {
        std::vector<std::string> fields;
        std::size_t start = 0;
        for (;;)
        {
            std::size_t const end = value.find('\t', start);
            fields.emplace_back(value.substr(start, end == std::string::npos ? end : end - start));
            if (end == std::string::npos)
                break;
            start = end + 1;
        }
        return fields;
    }

    void Send(Player* player, std::string const& payload)
    {
        if (payload.size() > MAX_FRAME)
            return;
        WorldPacket packet;
        ChatHandler::BuildChatPacket(packet, CHAT_MSG_WHISPER, LANG_ADDON, player, player, payload);
        player->SendDirectMessage(&packet);
    }

    bool KnowsDiscoverySpell(Player const* player)
    {
        return std::any_of(RunicCodexPolicy::DISCOVERY_SPELLS.begin(),
            RunicCodexPolicy::DISCOVERY_SPELLS.end(),
            [player](uint32 spell) { return player->HasSpell(spell); });
    }

    bool IsDiscoverySpell(uint32 spell)
    {
        return RunicCodexPolicy::IsDiscoverySpell(spell);
    }

    void PersistDiscovery(Player* player)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "INSERT INTO `runic_codex_powers` (guid,power_key,unlocked,equipped) "
            "VALUES({},'{}',1,0) ON DUPLICATE KEY UPDATE unlocked=1",
            guid, POWER_KEY));
        CharacterDatabase.DirectCommitTransaction(transaction);

        QueryResult row = CharacterDatabase.Query(
            "SELECT unlocked,equipped FROM `runic_codex_powers` WHERE guid={} AND power_key='{}'",
            guid, POWER_KEY);
        if (!row)
        {
            playerStates[guid] = {};
            return;
        }
        Field* fields = row->Fetch();
        playerStates[guid] = { fields[0].Get<uint8>() != 0, fields[1].Get<uint8>() != 0 };
    }

    void LoadState(Player* player)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        QueryResult row = CharacterDatabase.Query(
            "SELECT unlocked,equipped FROM `runic_codex_powers` WHERE guid={} AND power_key='{}'",
            guid, POWER_KEY);
        if (!row)
        {
            playerStates[guid] = {};
            if (KnowsDiscoverySpell(player))
                PersistDiscovery(player);
            return;
        }
        Field* fields = row->Fetch();
        playerStates[guid] = { fields[0].Get<uint8>() != 0, fields[1].Get<uint8>() != 0 };
    }

    void SendState(Player* player)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        State const state = playerStates[guid];
        Send(player, Acore::StringFormat("{}\tSTATE\t{}\t{}\t{}",
            PREFIX, state.unlocked ? 1 : 0, state.equipped ? 1 : 0,
            state.unlocked ? "Arcane Echo" : "Learn Arcane Missiles to discover this power"));
    }

    void SetEquipped(Player* player, bool equipped)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "UPDATE `runic_codex_powers` SET equipped={} WHERE guid={} AND power_key='{}' AND unlocked=1",
            equipped ? 1 : 0, guid, POWER_KEY));
        CharacterDatabase.DirectCommitTransaction(transaction);
        QueryResult row = CharacterDatabase.Query(
            "SELECT unlocked,equipped FROM `runic_codex_powers` WHERE guid={} AND power_key='{}'",
            guid, POWER_KEY);
        if (!row)
        {
            playerStates[guid] = {};
            Send(player, std::string(PREFIX) + "\tERR\tLOCKED");
            return;
        }
        Field* fields = row->Fetch();
        playerStates[guid] = { fields[0].Get<uint8>() != 0, fields[1].Get<uint8>() != 0 };
        if (!playerStates[guid].unlocked)
            Send(player, std::string(PREFIX) + "\tERR\tLOCKED");
        else
            SendState(player);
    }

    class RunicCodexPlayerScript final : public PlayerScript
    {
    public:
        RunicCodexPlayerScript() : PlayerScript("RunicCodexPlayerScript", {
            PLAYERHOOK_ON_BEFORE_SEND_CHAT_MESSAGE, PLAYERHOOK_ON_LOGIN, PLAYERHOOK_ON_LOGOUT,
            PLAYERHOOK_ON_LEARN_SPELL, PLAYERHOOK_ON_SPELL_CAST, PLAYERHOOK_ON_UPDATE
        }) { }

        void OnPlayerLogin(Player* player) override { LoadState(player); }

        void OnPlayerLogout(Player* player) override
        {
            uint32 const guid = player->GetGUID().GetCounter();
            playerStates.erase(guid);
            pendingRefunds.erase(guid);
        }

        void OnPlayerLearnSpell(Player* player, uint32 spellID) override
        {
            if (IsDiscoverySpell(spellID))
                PersistDiscovery(player);
        }

        void OnPlayerSpellCast(Player* player, Spell* spell, bool /*skipCheck*/) override
        {
            // The pinned core commonly passes skipCheck=true for normal casts after
            // validating them during prepare; it is not evidence of a triggered cast.
            if (!spell || !IsDiscoverySpell(spell->GetSpellInfo()->Id)
                || spell->GetSpellInfo()->PowerType != POWER_MANA)
                return;
            uint32 const guid = player->GetGUID().GetCounter();
            auto const found = playerStates.find(guid);
            if (found == playerStates.end() || !found->second.unlocked || !found->second.equipped)
                return;
            int32 const cost = spell->GetPowerCost();
            if (cost <= 0)
                return;
            pendingRefunds[guid] = { spell->GetSpellInfo()->Id, player->GetPower(POWER_MANA), cost };
        }

        void OnPlayerUpdate(Player* player, uint32 /*elapsed*/) override
        {
            uint32 const guid = player->GetGUID().GetCounter();
            auto found = pendingRefunds.find(guid);
            if (found == pendingRefunds.end())
                return;
            PendingRefund const pending = found->second;
            pendingRefunds.erase(found);
            int32 const manaSpent = std::max(0, pending.beforeMana - player->GetPower(POWER_MANA));
            auto state = playerStates.find(guid);
            uint32 const refund = state == playerStates.end() ? 0
                : RunicCodexPolicy::CalculateRefund(pending.spellId, state->second.unlocked,
                    state->second.equipped, static_cast<uint32>(pending.cost),
                    static_cast<uint32>(manaSpent));
            if (refund > 0)
                player->ModifyPower(POWER_MANA, static_cast<int32>(refund));
        }

        void OnPlayerBeforeSendChatMessage(Player* player, uint32& /*type*/, uint32& language,
                                           std::string& message) override
        {
            if (language != LANG_ADDON || message.rfind("RC1\t", 0) != 0)
                return;
            std::string frame = message;
            message.clear();
            if (frame.size() > MAX_FRAME)
                return;
            std::vector<std::string> fields = SplitTabs(frame);
            if (fields.size() < 2 || fields[0] != PREFIX)
                return;
            uint32 const guid = player->GetGUID().GetCounter();
            if (fields[1] == "STATE" && fields.size() == 2)
            {
                if (playerStates.find(guid) == playerStates.end())
                    LoadState(player);
                SendState(player);
            }
            else if (fields[1] == "EQUIP" && fields.size() == 3
                     && (fields[2] == "0" || fields[2] == "1"))
                SetEquipped(player, fields[2] == "1");
        }
    };
}

void AddRunicCodexScripts()
{
    new RunicCodexPlayerScript();
}
