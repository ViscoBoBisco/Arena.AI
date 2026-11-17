/*
 * mod-vault-terminal — reference server side of the VaultTerminal protocol.
 *
 * NOT COMPILED IN THIS REPOSITORY. It is here so the wire format, the hook and the send path are
 * concrete rather than hand-waved. Two core APIs it depends on were read in AzerothCore master:
 *
 *   1. PlayerScript::OnPlayerBeforeSendChatMessage(Player*, uint32& type, uint32& lang, std::string& msg)
 *      (src/server/game/Scripting/ScriptDefines/PlayerScript.h) — the receive hook. Clearing `msg`
 *      stops the message being relayed onwards (ChatHandler returns early on an empty message).
 *   2. ChatHandler::BuildChatPacket(data, CHAT_MSG_WHISPER, LANG_ADDON, player, player, payload)
 *      followed by SendDirectMessage — the same packet the core's own AddonChannelCommandHandler
 *      builds in Chat.cpp::AddonChannelCommandHandler::Send().
 *
 * The core's own addon protocol uses the prefix "AzerothCore\t" with opcodes p/h/i and replies
 * a/o/f/m. Its parser only claims messages with that exact prefix, so a different prefix is safe.
 */

#include "ScriptMgr.h"
#include "Player.h"
#include "Chat.h"
#include "Config.h"
#include "DatabaseEnv.h"
#include "Log.h"

#include <cstdlib>
#include <string>
#include <vector>

namespace
{
    constexpr char const* VT_PREFIX = "VT1\t";
    constexpr uint32 VT_PROTOCOL_VERSION = 1;
    constexpr uint32 VT_TARGET_SEALS = 50;      // the panel's progress bar ceiling
    constexpr uint32 VT_CLAIMS_PER_DAY = 1;

    bool enabled = true;

    // ---------------------------------------------------------------- utilities
    std::vector<std::string> Split(std::string const& text, char sep)
    {
        std::vector<std::string> out;
        std::string current;
        for (char c : text)
        {
            if (c == sep)
            {
                out.push_back(current);
                current.clear();
            }
            else
            {
                current.push_back(c);
            }
        }
        out.push_back(current);
        return out;
    }

    // The only way to talk back to the addon: the same packet shape the core uses.
    void Send(Player* player, std::string const& payload)
    {
        if (payload.size() > 255)
        {
            LOG_WARN("module.vault-terminal", "dropping oversized reply ({} bytes)", payload.size());
            return;
        }
        WorldPacket data;
        ChatHandler::BuildChatPacket(data, CHAT_MSG_WHISPER, LANG_ADDON, player, player, payload);
        player->SendDirectMessage(&data);
    }

    void SendError(Player* player, std::string const& code, std::string const& text)
    {
        Send(player, std::string(VT_PREFIX) + "ERR\t" + code + "\t" + text);
    }

    // ------------------------------------------------------------------- state
    struct VaultState
    {
        uint32 seals = 0;
        uint32 claimsToday = 0;
        std::string rank = "Novice";
    };

    VaultState LoadState(Player* player)
    {
        VaultState state;

        QueryResult result = CharacterDatabase.Query(
            "SELECT COUNT(*) FROM vault_terminal_claims WHERE guid = {}", player->GetGUID().GetCounter());
        if (result)
        {
            state.seals = result->Fetch()[0].Get<uint32>();
        }

        QueryResult today = CharacterDatabase.Query(
            "SELECT COUNT(*) FROM vault_terminal_claims WHERE guid = {} AND claim_day = CURDATE()",
            player->GetGUID().GetCounter());
        if (today)
        {
            state.claimsToday = today->Fetch()[0].Get<uint32>();
        }

        if (state.seals >= VT_TARGET_SEALS)      state.rank = "Keeper";
        else if (state.seals >= 25)              state.rank = "Adept";
        else if (state.seals >= 10)              state.rank = "Initiate";

        return state;
    }

    void SendState(Player* player)
    {
        VaultState state = LoadState(player);
        uint32 claimsLeft = state.claimsToday >= VT_CLAIMS_PER_DAY ? 0 : VT_CLAIMS_PER_DAY - state.claimsToday;
        std::string flag = claimsLeft > 0 ? "1" : "0";

        Send(player, std::string(VT_PREFIX) + "S\t" + std::to_string(state.seals) + "\t"
                      + std::to_string(VT_TARGET_SEALS) + "\t" + std::to_string(claimsLeft) + "\t"
                      + state.rank + "\t" + flag);
    }

    void HandleClaim(Player* player)
    {
        VaultState state = LoadState(player);
        if (state.claimsToday >= VT_CLAIMS_PER_DAY)
        {
            SendError(player, "COOLDOWN", "already claimed today");
            return;
        }

        CharacterDatabase.Execute(
            "INSERT INTO vault_terminal_claims (guid, claim_day) VALUES ({}, CURDATE())",
            player->GetGUID().GetCounter());

        Send(player, std::string(VT_PREFIX) + "OK\tseal claimed");
        SendState(player);                      // the client asks for state anyway; save it a round trip
    }
}

// ---------------------------------------------------------------------- script
class VaultTerminalPlayerScript : public PlayerScript
{
public:
    VaultTerminalPlayerScript() : PlayerScript("VaultTerminalPlayerScript", {
        PLAYERHOOK_ON_BEFORE_SEND_CHAT_MESSAGE
    }) { }

    void OnPlayerBeforeSendChatMessage(Player* player, uint32& /*type*/, uint32& lang, std::string& msg) override
    {
        if (!enabled || lang != LANG_ADDON)
        {
            return;
        }
        if (msg.rfind(VT_PREFIX, 0) != 0)        // not ours: leave the core's channel alone
        {
            return;
        }

        std::vector<std::string> parts = Split(msg.substr(std::string(VT_PREFIX).size()), '\t');
        if (parts.empty())
        {
            return;
        }

        std::string const& opcode = parts[0];

        if (opcode == "HELLO")
        {
            uint32 version = parts.size() > 1 ? static_cast<uint32>(std::strtoul(parts[1].c_str(), nullptr, 10)) : 0;
            if (version != VT_PROTOCOL_VERSION)
            {
                SendError(player, "VERSION", "client protocol is not supported");
            }
            else
            {
                SendState(player);
            }
        }
        else if (opcode == "STATE")
        {
            SendState(player);
        }
        else if (opcode == "CLAIM")
        {
            HandleClaim(player);
        }
        else if (opcode == "PING")
        {
            Send(player, std::string(VT_PREFIX) + "OK\t");
        }
        else
        {
            SendError(player, "INTERNAL", "unknown opcode");
        }

        msg.clear();                             // claimed: stop the core relaying it further
    }
};

class VaultTerminalWorldScript : public WorldScript
{
public:
    VaultTerminalWorldScript() : WorldScript("VaultTerminalWorldScript") { }

    void OnAfterConfigLoad(bool /*reload*/) override
    {
        enabled = sConfigMgr->GetOption<bool>("VaultTerminal.Enable", true);
    }
};

void AddVaultTerminalScripts()
{
    new VaultTerminalPlayerScript();
    new VaultTerminalWorldScript();
}
