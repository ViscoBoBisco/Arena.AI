// Runic Codex: registry-driven, character-bound spell discovery and loadouts.
#include "ScriptMgr.h"
#include "Player.h"
#include "Spell.h"
#include "Chat.h"
#include "Config.h"
#include "DatabaseEnv.h"
#include "StringFormat.h"
#include "RunicPowerRegistry.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace
{
    constexpr char const* PREFIX = "RC1";
    constexpr uint32 MAX_FRAME = 180;
    constexpr uint8 MAX_SLOTS = 2;

    struct State
    {
        bool unlocked = false;
        bool equipped = false;
        uint8 slot = 255;
    };
    struct PendingRefund
    {
        std::string powerKey;
        uint32 spellId = 0;
        uint32 beforeMana = 0;
        int32 cost = 0;
    };
    using PlayerPowerStates = std::unordered_map<std::string, State>;
    std::unordered_map<uint32, PlayerPowerStates> playerStates;
    std::unordered_map<uint32, PendingRefund> pendingRefunds;
    std::unordered_map<uint32, std::chrono::steady_clock::time_point> lastRequest;
    std::mutex requestMutex;

    void LoadState(Player* player);

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

    bool ValidToken(std::string const& value)
    {
        return !value.empty() && value.size() <= 24
            && std::all_of(value.begin(), value.end(), [](unsigned char c)
            {
                return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                    || (c >= '0' && c <= '9') || c == '_' || c == '-';
            });
    }

    bool AllowRequest(Player* player)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        auto const now = std::chrono::steady_clock::now();
        std::lock_guard<std::mutex> lock(requestMutex);
        auto const found = lastRequest.find(guid);
        if (found != lastRequest.end()
            && now - found->second < std::chrono::milliseconds(200))
            return false;
        lastRequest[guid] = now;
        return true;
    }

    void Send(Player* player, std::string const& payload)
    {
        if (payload.size() > MAX_FRAME)
            return;
        WorldPacket packet;
        ChatHandler::BuildChatPacket(packet, CHAT_MSG_WHISPER, LANG_ADDON, player, player, payload);
        player->SendDirectMessage(&packet);
    }

    bool KnowsPrerequisite(Player const* player, RunicPowerRegistry::PowerDefinition const& power)
    {
        return std::any_of(power.prerequisites.begin(), power.prerequisites.end(),
            [player](uint32 spell) { return spell != 0 && player->HasSpell(spell); });
    }

    bool HasUsedInscription(Player* player, RunicPowerRegistry::PowerDefinition const& power)
    {
        return CharacterDatabase.Query(
            "SELECT guid FROM `runic_codex_discoveries` WHERE guid={} AND power_key='{}' AND item_entry={}",
            player->GetGUID().GetCounter(), std::string(power.key), power.discoveryItem) != nullptr;
    }

    void RecordInscriptionUse(Player* player, RunicPowerRegistry::PowerDefinition const& power)
    {
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "INSERT IGNORE INTO `runic_codex_discoveries` (guid,power_key,item_entry) VALUES({},'{}',{})",
            player->GetGUID().GetCounter(), std::string(power.key), power.discoveryItem));
        CharacterDatabase.DirectCommitTransaction(transaction);
    }

    void PersistDiscovery(Player* player, RunicPowerRegistry::PowerDefinition const& power)
    {
        if (!KnowsPrerequisite(player, power) || !HasUsedInscription(player, power))
            return;
        uint32 const guid = player->GetGUID().GetCounter();
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "INSERT INTO `runic_codex_powers` (guid,power_key,unlocked,equipped,equipped_slot) "
            "VALUES({},'{}',1,0,NULL) ON DUPLICATE KEY UPDATE unlocked=1",
            guid, std::string(power.key)));
        CharacterDatabase.DirectCommitTransaction(transaction);
        LoadState(player);
    }

    void LoadState(Player* player)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        PlayerPowerStates states;
        QueryResult rows = CharacterDatabase.Query(
            "SELECT power_key,unlocked,equipped,COALESCE(equipped_slot,255) "
            "FROM `runic_codex_powers` WHERE guid={}", guid);
        if (rows)
            do
            {
                Field* fields = rows->Fetch();
                std::string const key = fields[0].Get<std::string>();
                if (!RunicPowerRegistry::Find(key))
                    continue;
                states[key] = { fields[1].Get<uint8>() != 0, fields[2].Get<uint8>() != 0,
                    fields[3].Get<uint8>() };
            } while (rows->NextRow());
        playerStates[guid] = std::move(states);
    }

    State GetState(Player* player, RunicPowerRegistry::PowerDefinition const& power)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        auto playerIt = playerStates.find(guid);
        if (playerIt == playerStates.end())
        {
            LoadState(player);
            playerIt = playerStates.find(guid);
        }
        auto const state = playerIt->second.find(std::string(power.key));
        return state == playerIt->second.end() ? State{} : state->second;
    }

    void SendState(Player* player)
    {
        for (RunicPowerRegistry::PowerDefinition const& power : RunicPowerRegistry::POWERS)
        {
            State const state = GetState(player, power);
            Send(player, Acore::StringFormat("{}\tPOWER\t{}\t{}\t{}\t{}\t{}",
                PREFIX, std::string(power.key), std::string(power.name), state.unlocked ? 1 : 0,
                state.equipped ? 1 : 0, state.equipped ? state.slot : 255));
        }
        Send(player, std::string(PREFIX) + "\tEND");
    }

    void EquipPower(Player* player, RunicPowerRegistry::PowerDefinition const& power, uint8 slot)
    {
        if (slot >= MAX_SLOTS || !GetState(player, power).unlocked)
        {
            Send(player, std::string(PREFIX) + "\tERR\tLOCKED_OR_BAD_SLOT");
            return;
        }
        uint32 const guid = player->GetGUID().GetCounter();
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "UPDATE `runic_codex_powers` SET equipped=0,equipped_slot=NULL "
            "WHERE guid={} AND equipped_slot={}", guid, slot));
        transaction->Append(Acore::StringFormat(
            "UPDATE `runic_codex_powers` SET equipped=1,equipped_slot={} "
            "WHERE guid={} AND power_key='{}' AND unlocked=1", slot, guid, std::string(power.key)));
        CharacterDatabase.DirectCommitTransaction(transaction);
        LoadState(player);
        State const result = GetState(player, power);
        if (!result.equipped || result.slot != slot)
        {
            Send(player, std::string(PREFIX) + "\tERR\tRETRY");
            return;
        }
        SendState(player);
    }

    void UnequipPower(Player* player, RunicPowerRegistry::PowerDefinition const& power)
    {
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "UPDATE `runic_codex_powers` SET equipped=0,equipped_slot=NULL "
            "WHERE guid={} AND power_key='{}' AND unlocked=1",
            player->GetGUID().GetCounter(), std::string(power.key)));
        CharacterDatabase.DirectCommitTransaction(transaction);
        LoadState(player);
        SendState(player);
    }

    void SaveLoadout(Player* player, std::string const& name)
    {
        if (!ValidToken(name))
        {
            Send(player, std::string(PREFIX) + "\tERR\tBAD_LOADOUT_NAME");
            return;
        }
        uint32 const guid = player->GetGUID().GetCounter();
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "INSERT INTO `runic_codex_loadouts` (guid,loadout_name) VALUES({},'{}') "
            "ON DUPLICATE KEY UPDATE updated_at=CURRENT_TIMESTAMP", guid, name));
        transaction->Append(Acore::StringFormat(
            "DELETE FROM `runic_codex_loadout_powers` WHERE guid={} AND loadout_name='{}'", guid, name));
        transaction->Append(Acore::StringFormat(
            "INSERT INTO `runic_codex_loadout_powers` (guid,loadout_name,slot_index,power_key) "
            "SELECT guid,'{}',equipped_slot,power_key FROM `runic_codex_powers` "
            "WHERE guid={} AND equipped=1 AND equipped_slot<{}", name, guid, MAX_SLOTS));
        CharacterDatabase.DirectCommitTransaction(transaction);
        Send(player, Acore::StringFormat("{}\tLOADOUT\tSAVED\t{}", PREFIX, name));
    }

    void ApplyLoadout(Player* player, std::string const& name)
    {
        if (!ValidToken(name))
        {
            Send(player, std::string(PREFIX) + "\tERR\tBAD_LOADOUT_NAME");
            return;
        }
        uint32 const guid = player->GetGUID().GetCounter();
        QueryResult invalid = CharacterDatabase.Query(
            "SELECT 1 FROM `runic_codex_loadout_powers` l LEFT JOIN `runic_codex_powers` p "
            "ON p.guid=l.guid AND p.power_key=l.power_key AND p.unlocked=1 "
            "WHERE l.guid={} AND l.loadout_name='{}' AND p.guid IS NULL LIMIT 1", guid, name);
        QueryResult exists = CharacterDatabase.Query(
            "SELECT guid FROM `runic_codex_loadouts` WHERE guid={} AND loadout_name='{}'", guid, name);
        if (!exists || invalid)
        {
            Send(player, std::string(PREFIX) + "\tERR\tLOADOUT_MISSING_OR_LOCKED");
            return;
        }
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "UPDATE `runic_codex_powers` SET equipped=0,equipped_slot=NULL WHERE guid={}", guid));
        transaction->Append(Acore::StringFormat(
            "UPDATE `runic_codex_powers` p JOIN `runic_codex_loadout_powers` l "
            "ON l.guid=p.guid AND l.power_key=p.power_key "
            "SET p.equipped=1,p.equipped_slot=l.slot_index "
            "WHERE l.guid={} AND l.loadout_name='{}' AND p.unlocked=1", guid, name));
        CharacterDatabase.DirectCommitTransaction(transaction);
        LoadState(player);
        SendState(player);
    }

    class RunicCodexPlayerScript final : public PlayerScript
    {
    public:
        RunicCodexPlayerScript() : PlayerScript("RunicCodexPlayerScript", {
            PLAYERHOOK_ON_BEFORE_SEND_CHAT_MESSAGE, PLAYERHOOK_ON_LOGIN, PLAYERHOOK_ON_LOGOUT,
            PLAYERHOOK_ON_LEARN_SPELL, PLAYERHOOK_ON_SPELL_CAST, PLAYERHOOK_ON_UPDATE
        }) { }

        void OnPlayerLogin(Player* player) override
        {
            LoadState(player);
            // Finish an inscription if the process stopped after recording item use but before
            // the unlock write; both inputs remain server-observed and character-bound.
            for (RunicPowerRegistry::PowerDefinition const& power : RunicPowerRegistry::POWERS)
                PersistDiscovery(player, power);
        }

        void OnPlayerLogout(Player* player) override
        {
            uint32 const guid = player->GetGUID().GetCounter();
            playerStates.erase(guid);
            pendingRefunds.erase(guid);
            std::lock_guard<std::mutex> lock(requestMutex);
            lastRequest.erase(guid);
        }

        void OnPlayerLearnSpell(Player* player, uint32 spellID) override
        {
            for (RunicPowerRegistry::PowerDefinition const& power : RunicPowerRegistry::POWERS)
                if (RunicPowerRegistry::Contains(power.prerequisites, spellID)
                    && HasUsedInscription(player, power))
                    PersistDiscovery(player, power);
            SendState(player);
        }

        void OnPlayerSpellCast(Player* player, Spell* spell, bool /*skipCheck*/) override
        {
            if (!spell || !spell->GetSpellInfo())
                return;
            uint32 const spellId = spell->GetSpellInfo()->Id;
            if (spell->m_CastItem)
                if (RunicPowerRegistry::PowerDefinition const* discovery =
                    RunicPowerRegistry::FindByDiscovery(spell->m_CastItem->GetEntry(), spellId))
                {
                    RecordInscriptionUse(player, *discovery);
                    PersistDiscovery(player, *discovery);
                    SendState(player);
                }
            if (spell->GetSpellInfo()->PowerType != POWER_MANA)
                return;
            for (RunicPowerRegistry::PowerDefinition const& power : RunicPowerRegistry::POWERS)
                if (RunicPowerRegistry::Contains(power.affectedSpells, spellId))
                {
                    State const state = GetState(player, power);
                    if (!state.unlocked || !state.equipped)
                        continue;
                    int32 const cost = spell->GetPowerCost();
                    if (cost > 0)
                        pendingRefunds[player->GetGUID().GetCounter()] = {
                            std::string(power.key), spellId, player->GetPower(POWER_MANA), cost };
                    break;
                }
        }

        void OnPlayerUpdate(Player* player, uint32 /*elapsed*/) override
        {
            uint32 const guid = player->GetGUID().GetCounter();
            auto found = pendingRefunds.find(guid);
            if (found == pendingRefunds.end())
                return;
            PendingRefund const pending = found->second;
            pendingRefunds.erase(found);
            uint32 const manaSpent = player->GetPower(POWER_MANA) < pending.beforeMana
                ? pending.beforeMana - player->GetPower(POWER_MANA) : 0;
            RunicPowerRegistry::PowerDefinition const* power = RunicPowerRegistry::Find(pending.powerKey);
            State const state = power ? GetState(player, *power) : State{};
            uint32 const percent = power ? sConfigMgr->GetOption<uint32>(
                "RunicCodex." + std::string(power->key) + ".RefundPercent",
                power->refundPercent) : 0;
            uint32 const cap = power ? sConfigMgr->GetOption<uint32>(
                "RunicCodex." + std::string(power->key) + ".RefundCap",
                power->refundCap) : 0;
            uint32 const refund = power ? RunicPowerRegistry::CalculateRefund(
                static_cast<uint32>(pending.cost), manaSpent, percent, cap,
                state.unlocked, state.equipped) : 0;
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
            if (frame.size() > MAX_FRAME || !AllowRequest(player))
                return;
            std::vector<std::string> fields = SplitTabs(frame);
            if (fields.size() < 2 || fields[0] != PREFIX)
                return;
            if (fields[1] == "STATE" && fields.size() == 2)
            {
                LoadState(player);
                SendState(player);
            }
            else if (fields[1] == "EQUIP" && fields.size() == 4
                     && ValidToken(fields[2]) && fields[3].size() == 1
                     && fields[3][0] >= '0' && fields[3][0] < '0' + MAX_SLOTS)
            {
                if (RunicPowerRegistry::PowerDefinition const* power = RunicPowerRegistry::Find(fields[2]))
                    EquipPower(player, *power, static_cast<uint8>(fields[3][0] - '0'));
            }
            else if (fields[1] == "UNEQUIP" && fields.size() == 3 && ValidToken(fields[2]))
            {
                if (RunicPowerRegistry::PowerDefinition const* power = RunicPowerRegistry::Find(fields[2]))
                    UnequipPower(player, *power);
            }
            else if (fields[1] == "LOADOUT" && fields.size() == 4 && ValidToken(fields[3]))
            {
                if (fields[2] == "SAVE") SaveLoadout(player, fields[3]);
                else if (fields[2] == "APPLY") ApplyLoadout(player, fields[3]);
            }
        }
    };
}

void AddRunicCodexScripts()
{
    new RunicCodexPlayerScript();
}
