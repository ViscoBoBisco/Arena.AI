// Argent Commission: persistent Honored-gated access to the core Argent Champion mechanic.
#include "ScriptMgr.h"
#include "Player.h"
#include "Chat.h"
#include "Config.h"
#include "DatabaseEnv.h"
#include "ReputationMgr.h"
#include "SpellMgr.h"
#include "Creature.h"
#include "StringFormat.h"
#include "ArgentFieldDressingPolicy.h"
#include "ArgentMedicPolicy.h"

#include <algorithm>
#include <chrono>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace
{
    constexpr char const* PREFIX = "AC1";
    constexpr uint32 ARGENT_CRUSADE_FACTION = 1106;
    constexpr uint32 ARGENT_CHAMPION_SPELL = 57819;
    constexpr std::size_t MAX_FRAME = 160;
    std::unordered_set<uint32> earnedCommissions;
    std::unordered_map<uint32, std::chrono::steady_clock::time_point> medicCooldowns;
    std::mutex medicCooldownMutex;

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
        earnedCommissions.insert(guid);
        if (!ApplyChampionMechanic(player))
        {
            Send(player, std::string(PREFIX) + "\tERR\tSPELL_UNAVAILABLE");
            return;
        }
        Send(player, std::string(PREFIX) + "\tCLAIMED\t1");
    }

    void RequestMedicService(Player* player)
    {
        if (!HasCommission(player))
        {
            Send(player, std::string(PREFIX) + "\tERR\tCOMMISSION_REQUIRED");
            return;
        }
        bool const honored = player->GetReputationRank(ARGENT_CRUSADE_FACTION) >= REP_HONORED;
        if (!honored)
        {
            Send(player, std::string(PREFIX) + "\tERR\tHONORED_REQUIRED");
            return;
        }
        Creature* medic = player->FindNearestCreature(ArgentMedicPolicy::MEDIC_CREATURE_ENTRY,
            static_cast<float>(ArgentMedicPolicy::SERVICE_RANGE_YARDS), true);
        if (!medic)
        {
            Send(player, std::string(PREFIX) + "\tERR\tARGENT_MEDIC_REQUIRED");
            return;
        }
        if (!sSpellMgr->GetSpellInfo(ArgentMedicPolicy::CURE_DISEASE_SPELL))
        {
            Send(player, std::string(PREFIX) + "\tERR\tSERVICE_UNAVAILABLE");
            return;
        }
        if (!ArgentMedicPolicy::CanRequestService(true, honored, medic->IsAlive(), true))
        {
            Send(player, std::string(PREFIX) + "\tERR\tSERVICE_UNAVAILABLE");
            return;
        }

        uint32 const cooldownSeconds = ArgentMedicPolicy::BoundCooldown(sConfigMgr->GetOption<uint32>(
            "ArgentCommission.Medic.CooldownSeconds", ArgentMedicPolicy::DEFAULT_COOLDOWN_SECONDS));
        auto const now = std::chrono::steady_clock::now();
        uint32 const guid = player->GetGUID().GetCounter();
        bool ready = false;
        {
            std::lock_guard<std::mutex> lock(medicCooldownMutex);
            for (auto iter = medicCooldowns.begin(); iter != medicCooldowns.end();)
                if (now >= iter->second)
                    iter = medicCooldowns.erase(iter);
                else
                    ++iter;
            auto const previous = medicCooldowns.find(guid);
            if (previous == medicCooldowns.end() || now >= previous->second)
            {
                medicCooldowns[guid] = now + std::chrono::seconds(cooldownSeconds);
                ready = true;
            }
        }
        if (!ready)
        {
            Send(player, std::string(PREFIX) + "\tERR\tSERVICE_COOLDOWN");
            return;
        }

        // Keep the existing SmartAI and its gossip behavior intact: this addon request
        // only asks the nearby stock Argent Medic to cast its native disease cure.
        medic->CastSpell(player, ArgentMedicPolicy::CURE_DISEASE_SPELL, true);
        Send(player, Acore::StringFormat("{}\tMEDIC\tREADY\t{}", PREFIX, cooldownSeconds));
    }

    class ArgentCommissionPlayerScript final : public PlayerScript
    {
    public:
        ArgentCommissionPlayerScript() : PlayerScript("ArgentCommissionPlayerScript", {
            PLAYERHOOK_ON_BEFORE_SEND_CHAT_MESSAGE, PLAYERHOOK_ON_LOGIN, PLAYERHOOK_ON_LOGOUT,
            PLAYERHOOK_ON_CREATURE_KILL
        }) { }

        void OnPlayerLogin(Player* player) override
        {
            uint32 const guid = player->GetGUID().GetCounter();
            if (HasCommission(player))
            {
                earnedCommissions.insert(guid);
                ApplyChampionMechanic(player);
            }
        }

        void OnPlayerLogout(Player* player) override
        {
            earnedCommissions.erase(player->GetGUID().GetCounter());
        }

        void OnPlayerCreatureKill(Player* killer, Creature* /*killed*/) override
        {
            uint32 const guid = killer->GetGUID().GetCounter();
            if (earnedCommissions.find(guid) == earnedCommissions.end()
                || killer->GetReputationRank(ARGENT_CRUSADE_FACTION) < REP_HONORED)
                return;
            bool const exalted = killer->GetReputationRank(ARGENT_CRUSADE_FACTION) >= REP_EXALTED;
            uint32 const percent = sConfigMgr->GetOption<uint32>(exalted
                ? "ArgentCommission.FieldDressing.ExaltedPercent"
                : "ArgentCommission.FieldDressing.HonoredPercent", exalted ? 3u : 2u);
            uint32 const cap = sConfigMgr->GetOption<uint32>(exalted
                ? "ArgentCommission.FieldDressing.ExaltedCap"
                : "ArgentCommission.FieldDressing.HonoredCap", exalted ? 250u : 150u);
            uint32 const heal = ArgentFieldDressingPolicy::CalculateHeal(
                killer->GetMaxHealth(), killer->GetMaxHealth() - killer->GetHealth(), percent, cap);
            if (heal > 0)
                killer->ModifyHealth(static_cast<int32>(heal));
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
            else if (fields[1] == "MEDIC")
                RequestMedicService(player);
        }
    };
}

void AddArgentCommissionScripts()
{
    new ArgentCommissionPlayerScript();
}
