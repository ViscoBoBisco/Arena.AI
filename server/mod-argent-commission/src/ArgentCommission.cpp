// Argent Commission: persistent Honored-gated access to the core Argent Champion mechanic.
#include "ScriptMgr.h"
#include "Player.h"
#include "Chat.h"
#include "DatabaseEnv.h"
#include "ReputationMgr.h"
#include "SpellMgr.h"
#include "StringFormat.h"

#include <string>
#include <vector>

namespace
{
    constexpr char const* PREFIX = "AC1";
    constexpr uint32 ARGENT_CRUSADE_FACTION = 1106;
    constexpr uint32 ARGENT_CHAMPION_SPELL = 57819;
    constexpr std::size_t MAX_FRAME = 160;

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

    bool HasCommission(Player* player)
    {
        return CharacterDatabase.Query(
            "SELECT guid FROM `argent_commission_entitlements` "
            "WHERE guid={} AND faction_id={} AND effect_spell={}",
            player->GetGUID().GetCounter(), ARGENT_CRUSADE_FACTION, ARGENT_CHAMPION_SPELL) != nullptr;
    }

    bool ApplyChampionMechanic(Player* player)
    {
        if (!sSpellMgr->GetSpellInfo(ARGENT_CHAMPION_SPELL))
            return false;
        player->CastSpell(player, ARGENT_CHAMPION_SPELL, true);
        return true;
    }

    void SendStatus(Player* player)
    {
        bool const earned = HasCommission(player);
        bool const honored = player->GetReputationRank(ARGENT_CRUSADE_FACTION) >= REP_HONORED;
        Send(player, Acore::StringFormat("{}\tSTATUS\t{}\t{}",
            PREFIX, earned ? 1 : 0, honored ? 1 : 0));
    }

    void Claim(Player* player)
    {
        if (player->GetReputationRank(ARGENT_CRUSADE_FACTION) < REP_HONORED)
        {
            Send(player, std::string(PREFIX) + "\tERR\tHONORED_REQUIRED");
            return;
        }
        if (!sSpellMgr->GetSpellInfo(ARGENT_CHAMPION_SPELL))
        {
            Send(player, std::string(PREFIX) + "\tERR\tSPELL_UNAVAILABLE");
            return;
        }
        uint32 const guid = player->GetGUID().GetCounter();
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "INSERT IGNORE INTO `argent_commission_entitlements` "
            "(guid,faction_id,effect_spell,granted_by) VALUES({}, {}, {}, 'REPUTATION_CLAIM')",
            guid, ARGENT_CRUSADE_FACTION, ARGENT_CHAMPION_SPELL));
        CharacterDatabase.DirectCommitTransaction(transaction);
        if (!HasCommission(player))
        {
            Send(player, std::string(PREFIX) + "\tERR\tRETRY");
            return;
        }
        if (!ApplyChampionMechanic(player))
        {
            Send(player, std::string(PREFIX) + "\tERR\tSPELL_UNAVAILABLE");
            return;
        }
        Send(player, std::string(PREFIX) + "\tCLAIMED\t1");
    }

    class ArgentCommissionPlayerScript final : public PlayerScript
    {
    public:
        ArgentCommissionPlayerScript() : PlayerScript("ArgentCommissionPlayerScript", {
            PLAYERHOOK_ON_BEFORE_SEND_CHAT_MESSAGE, PLAYERHOOK_ON_LOGIN
        }) { }

        void OnPlayerLogin(Player* player) override
        {
            if (HasCommission(player))
                ApplyChampionMechanic(player);
        }

        void OnPlayerBeforeSendChatMessage(Player* player, uint32& /*type*/, uint32& language,
                                           std::string& message) override
        {
            if (language != LANG_ADDON || message.rfind("AC1\t", 0) != 0)
                return;
            std::string frame = message;
            message.clear();
            if (frame.size() > MAX_FRAME)
                return;
            std::vector<std::string> fields = SplitTabs(frame);
            if (fields.size() != 2 || fields[0] != PREFIX)
                return;
            if (fields[1] == "STATUS")
                SendStatus(player);
            else if (fields[1] == "CLAIM")
                Claim(player);
        }
    };
}

void AddArgentCommissionScripts()
{
    new ArgentCommissionPlayerScript();
}
