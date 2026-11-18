// Vault Terminal — generated functional daily-counter module.
// The client submits intent only. CharacterDatabase is authoritative.
#include "ScriptMgr.h"
#include "Player.h"
#include "Chat.h"
#include "Config.h"
#include "DatabaseEnv.h"
#include "Log.h"
#include "StringFormat.h"

#include "mod-vault-terminal.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace
{
    constexpr char const* PREFIX = "VT1\t";
    constexpr char const* PREFIX_TOKEN = "VT1";
    constexpr uint32 PROTOCOL_VERSION = 2;
    constexpr uint32 MAX_PAYLOAD_BYTES = 255;
    constexpr uint64 TARGET = 50;
    constexpr uint32 CLAIMS_PER_DAY = 1;
    constexpr uint32 MIN_REQUEST_INTERVAL_MS = 250;
    constexpr char const* CLAIM_TABLE = "vault_terminal_claims";
    constexpr char const* REQUEST_TABLE = "vault_terminal_claims_requests";

    struct RankStep { uint64 threshold; char const* name; };
    constexpr RankStep RANKS[] = {
        {50, "Vaultmaster"},
        {25, "Keeper"},
        {10, "Adept"},
        {0, "Initiate"}
    };

    struct CounterState
    {
        uint64 totalClaims = 0;
        uint64 claimsToday = 0;
    };

    std::atomic_bool enabled{true};
    std::mutex requestMutex;
    std::unordered_map<uint32, std::chrono::steady_clock::time_point> lastRequest;

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

    bool IsUpperOpcode(std::string const& value)
    {
        return !value.empty() && value.size() <= 8
            && std::all_of(value.begin(), value.end(), [](unsigned char c) { return c >= 'A' && c <= 'Z'; });
    }

    bool IsValidFrame(std::string const& value)
    {
        if (value.size() > MAX_PAYLOAD_BYTES)
            return false;
        return std::all_of(value.begin(), value.end(), [](unsigned char c)
        {
            return c == '\t' || (c >= 0x20 && c <= 0x7e);
        });
    }

    char const* RankFor(uint64 seals)
    {
        for (RankStep const& step : RANKS)
            if (seals >= step.threshold)
                return step.name;
        return RANKS[(sizeof(RANKS) / sizeof(RANKS[0])) - 1].name;
    }

    bool AllowRequest(Player* player)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        auto const now = std::chrono::steady_clock::now();
        std::lock_guard<std::mutex> lock(requestMutex);
        auto const found = lastRequest.find(guid);
        if (found != lastRequest.end()
            && now - found->second < std::chrono::milliseconds(MIN_REQUEST_INTERVAL_MS))
            return false;
        lastRequest[guid] = now;
        return true;
    }

    void ForgetRequest(Player* player)
    {
        std::lock_guard<std::mutex> lock(requestMutex);
        lastRequest.erase(player->GetGUID().GetCounter());
    }

    void Send(Player* player, std::string const& payload)
    {
        if (payload.size() > MAX_PAYLOAD_BYTES)
        {
            LOG_ERROR("module.vault-terminal", "refusing oversized terminal response ({} bytes)", payload.size());
            return;
        }
        WorldPacket packet;
        ChatHandler::BuildChatPacket(packet, CHAT_MSG_WHISPER, LANG_ADDON, player, player, payload);
        player->SendDirectMessage(&packet);
    }

    void SendError(Player* player, char const* code, char const* text)
    {
        Send(player, std::string(PREFIX) + "ERR\t" + code + "\t" + text);
    }

    bool IsValidRequestId(std::string const& requestId)
    {
        return !requestId.empty() && requestId.size() <= 64
            && std::all_of(requestId.begin(), requestId.end(), [](unsigned char c)
            {
                return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z')
                    || (c >= 'A' && c <= 'Z') || c == '-' || c == '_';
            });
    }

    void SendReceipt(Player* player, std::string const& requestId,
                     std::string const& status, uint64 totalClaims)
    {
        Send(player, std::string(PREFIX) + "RCPT\t" + requestId + "\t" + status + "\t"
            + std::to_string(totalClaims));
    }

    bool LoadState(Player* player, CounterState& state)
    {
        QueryResult result = CharacterDatabase.Query(
            "SELECT COALESCE(MAX(total_claims), 0), "
            "COALESCE(MAX(last_claim_day = CURRENT_DATE()), 0) "
            "FROM `" + std::string(CLAIM_TABLE) + "` WHERE guid = {}",
            player->GetGUID().GetCounter());
        if (!result)
            return false;
        Field* fields = result->Fetch();
        state.totalClaims = fields[0].Get<uint64>();
        state.claimsToday = fields[1].Get<uint64>();
        return true;
    }

    void SendState(Player* player, CounterState const& state)
    {
        uint64 const claimsLeft = state.totalClaims >= TARGET || state.claimsToday >= CLAIMS_PER_DAY
            ? 0 : CLAIMS_PER_DAY - state.claimsToday;
        char const* const flag = claimsLeft ? "1" : "0";
        Send(player, std::string(PREFIX) + "S\t" + std::to_string(state.totalClaims) + "\t"
            + std::to_string(TARGET) + "\t" + std::to_string(claimsLeft) + "\t"
            + RankFor(state.totalClaims) + "\t" + flag);
    }

    void HandleState(Player* player)
    {
        CounterState state;
        if (!LoadState(player, state))
        {
            SendError(player, "INTERNAL", "state is temporarily unavailable");
            return;
        }
        SendState(player, state);
    }

    void HandleClaim(Player* player, std::string const& requestId)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "INSERT IGNORE INTO `vault_terminal_claims_requests` (guid, request_id, status, total_claims, claim_day) VALUES ({}, '{}', 'PENDING', 0, CURRENT_DATE())", guid, requestId));
        transaction->Append(Acore::StringFormat(
            "INSERT IGNORE INTO `vault_terminal_claims` (guid, total_claims, last_claim_day, last_request_id) VALUES ({}, 0, NULL, NULL)", guid));
        transaction->Append(Acore::StringFormat(
            "UPDATE `vault_terminal_claims` SET last_request_id = IF((last_claim_day IS NULL OR last_claim_day <> CURRENT_DATE()) AND total_claims < 50, '{}', last_request_id), total_claims = IF(last_request_id = '{}', total_claims + 1, total_claims), last_claim_day = IF(last_request_id = '{}', CURRENT_DATE(), last_claim_day) WHERE guid = {} AND EXISTS (SELECT 1 FROM `vault_terminal_claims_requests` WHERE guid = {} AND request_id = '{}' AND status = 'PENDING')", requestId, requestId, requestId, guid, guid, requestId));
        transaction->Append(Acore::StringFormat(
            "UPDATE `vault_terminal_claims_requests` AS receipt JOIN `vault_terminal_claims` AS claims ON claims.guid = receipt.guid SET receipt.status = CASE WHEN claims.last_request_id = '{}' THEN 'GRANTED' WHEN claims.total_claims >= 50 THEN 'DENIED_TARGET' WHEN claims.last_claim_day = CURRENT_DATE() THEN 'DENIED_COOLDOWN' ELSE 'DENIED_INTERNAL' END, receipt.total_claims = claims.total_claims, receipt.claim_day = CURRENT_DATE() WHERE receipt.guid = {} AND receipt.request_id = '{}' AND receipt.status = 'PENDING'", requestId, guid, requestId));
        CharacterDatabase.DirectCommitTransaction(transaction);

        // Read the durable request receipt by its unique request key. This is
        // intentionally not inferred from a post-write character-state query.
        QueryResult receipt = CharacterDatabase.Query(
            "SELECT request_id, status, total_claims FROM `" + std::string(REQUEST_TABLE)
                + "` WHERE guid = {} AND request_id = '{}'", guid, requestId);
        if (!receipt)
        {
            SendReceipt(player, requestId, "RETRY", 0);
            return;
        }
        Field* fields = receipt->Fetch();
        std::string const storedRequestId = fields[0].Get<std::string>();
        std::string const status = fields[1].Get<std::string>();
        uint64 const totalClaims = fields[2].Get<uint64>();
        if (storedRequestId != requestId || (status != "GRANTED" && status != "DENIED_COOLDOWN"
            && status != "DENIED_TARGET" && status != "DENIED_INTERNAL"))
        {
            SendReceipt(player, requestId, "RETRY", 0);
            return;
        }

        SendReceipt(player, requestId, status, totalClaims);
        CounterState state;
        if (LoadState(player, state))
            SendState(player, state);
    }


}

class VaultTerminalPlayerScript final : public PlayerScript
{
public:
    VaultTerminalPlayerScript() : PlayerScript("VaultTerminalPlayerScript", {
        PLAYERHOOK_ON_BEFORE_SEND_CHAT_MESSAGE,
        PLAYERHOOK_ON_LOGOUT
    }) { }

    void OnPlayerLogout(Player* player) override
    {
        ForgetRequest(player);
    }

    void OnPlayerBeforeSendChatMessage(Player* player, uint32& /*type*/, uint32& lang,
                                       std::string& message) override
    {
        if (lang != LANG_ADDON || message.rfind(PREFIX, 0) != 0)
            return;
        std::string frame = message;
        message.clear();
        if (!IsValidFrame(frame))
            return;
        std::vector<std::string> fields = SplitTabs(frame);
        if (fields.size() < 2 || fields[0] != PREFIX_TOKEN || !IsUpperOpcode(fields[1])
            || std::any_of(fields.begin(), fields.end(), [](std::string const& field) { return field.size() > 64; }))
            return;
        std::string const& opcode = fields[1];
        bool const known = opcode == "HELLO" || opcode == "STATE" || opcode == "CLAIM" || opcode == "PING";
        if (!known)
            return; // Forward-compatible unknown opcodes are silently consumed.
        if (!enabled.load())
        {
            Send(player, std::string(PREFIX) + "BYE\tserver is under maintenance");
            return;
        }
        if (!AllowRequest(player))
        {
            if (opcode == "CLAIM" && fields.size() == 3 && IsValidRequestId(fields[2]))
                SendReceipt(player, fields[2], "RETRY", 0);
            else
                SendError(player, "BUSY", "please wait before sending another request");
            return;
        }
        if (opcode == "HELLO")
        {
            if (fields.size() != 4 || fields[2] != std::to_string(PROTOCOL_VERSION))
                SendError(player, "VERSION", "client protocol is not supported");
            else
                HandleState(player);
        }
        else if (opcode == "CLAIM")
        {
            if (fields.size() != 3 || !IsValidRequestId(fields[2]))
                return; // Malformed action frames never reach persistence.
            HandleClaim(player, fields[2]);
        }
        else if (fields.size() != 2)
            return;
        else if (opcode == "STATE")
            HandleState(player);
        else if (opcode == "PING")
            Send(player, std::string(PREFIX) + "OK");
    }
};

class VaultTerminalWorldScript final : public WorldScript
{
public:
    VaultTerminalWorldScript() : WorldScript("VaultTerminalWorldScript") { }
    void OnAfterConfigLoad(bool /*reload*/) override
    {
        enabled.store(sConfigMgr->GetOption<bool>("VaultTerminal.Enable", true));
    }
};

void AddVaultTerminalScripts()
{
    new VaultTerminalPlayerScript();
    new VaultTerminalWorldScript();
}
