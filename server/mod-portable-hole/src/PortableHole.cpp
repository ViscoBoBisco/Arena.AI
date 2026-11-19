// Portable Hole: character-private, server-authoritative commodity ledger.
// This module stores fungible stackable counts only. It never owns/serializes
// unique Item instances and never trusts a client-supplied character GUID.
#include "ScriptMgr.h"
#include "Player.h"
#include "Chat.h"
#include "Config.h"
#include "DatabaseEnv.h"
#include "ObjectMgr.h"
#include "StringFormat.h"

#include "mod-portable-hole.h"

#include <algorithm>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace
{
    constexpr char const* PREFIX = "PH1\t";
    constexpr char const* PREFIX_TOKEN = "PH1";
    constexpr uint32 PROTOCOL_VERSION = 1;
    constexpr std::size_t MAX_FRAME = 255;
    constexpr uint32 MIN_REQUEST_INTERVAL_MS = 200;
    constexpr uint32 LIST_PAGE_SIZE = 5;
    constexpr uint32 MAX_PAGE = 20;
    constexpr uint64 MAX_OPERATION_AMOUNT = 1000000000ULL;
    constexpr uint64 MAX_CHARACTER_BALANCE = 9000000000000000ULL;

    constexpr char const* BALANCES = "portable_hole_commodities";
    constexpr char const* RESERVATIONS = "portable_hole_reservations";
    constexpr char const* RECEIPTS = "portable_hole_receipts";

    std::atomic_bool enabled{true};
    std::mutex throttleMutex;
    std::unordered_map<uint32, std::chrono::steady_clock::time_point> lastRequest;

    struct Receipt
    {
        std::string requestId;
        std::string fingerprint;
        std::string action;
        std::string status;
        uint32 itemEntry = 0;
        uint64 amount = 0;
        std::string reservationId;
        uint64 balance = 0;
        uint64 reserved = 0;
    };

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

    bool IsValidFrame(std::string const& value)
    {
        if (value.empty() || value.size() > MAX_FRAME)
            return false;
        return std::all_of(value.begin(), value.end(), [](unsigned char c)
        {
            return c == '\t' || (c >= 0x20 && c <= 0x7e);
        });
    }

    bool IsValidToken(std::string const& value, std::size_t maxLength = 64)
    {
        return !value.empty() && value.size() <= maxLength
            && std::all_of(value.begin(), value.end(), [](unsigned char c)
            {
                return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z')
                    || (c >= 'A' && c <= 'Z') || c == '-' || c == '_' || c == '.' || c == ':';
            });
    }

    template <typename T>
    bool ParseUnsigned(std::string const& text, T& value)
    {
        if (text.empty() || !std::all_of(text.begin(), text.end(), [](unsigned char c)
            { return c >= '0' && c <= '9'; }))
            return false;
        auto const parsed = std::from_chars(text.data(), text.data() + text.size(), value);
        return parsed.ec == std::errc{} && parsed.ptr == text.data() + text.size();
    }

    bool AllowRequest(Player* player)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        auto const now = std::chrono::steady_clock::now();
        std::lock_guard<std::mutex> lock(throttleMutex);
        auto const found = lastRequest.find(guid);
        if (found != lastRequest.end()
            && now - found->second < std::chrono::milliseconds(MIN_REQUEST_INTERVAL_MS))
            return false;
        lastRequest[guid] = now;
        return true;
    }

    std::string SafeLabel(std::string value)
    {
        for (char& character : value)
        {
            unsigned char const byte = static_cast<unsigned char>(character);
            if (byte < 0x20 || byte > 0x7e || character == '\t')
                character = ' ';
        }
        if (value.size() > 64)
            value.resize(64);
        return value;
    }

    void Send(Player* player, std::string const& payload)
    {
        if (payload.size() > MAX_FRAME || !IsValidFrame(payload))
            return;
        WorldPacket packet;
        ChatHandler::BuildChatPacket(packet, CHAT_MSG_WHISPER, LANG_ADDON, player, player, payload);
        player->SendDirectMessage(&packet);
    }

    void SendError(Player* player, char const* code)
    {
        Send(player, std::string(PREFIX) + "ERR\t" + code);
    }

    ItemTemplate const* Commodity(uint32 itemEntry)
    {
        ItemTemplate const* item = sObjectMgr->GetItemTemplate(itemEntry);
        if (!item || item->GetMaxStackSize() <= 1 || item->MaxCount > 0
            || item->Bonding == BIND_QUEST_ITEM)
            return nullptr;
        return item;
    }

    std::string Fingerprint(char const* action, uint32 itemEntry, uint64 amount,
                            std::string const& reservationId)
    {
        return Acore::StringFormat("{}|{}|{}|{}", action, itemEntry, amount, reservationId);
    }

    template <typename Transaction>
    void InsertPendingReceipt(Transaction& transaction,
                              uint32 guid, std::string const& requestId,
                              std::string const& fingerprint, char const* action,
                              uint32 itemEntry, uint64 amount,
                              std::string const& reservationId,
                              char const* source)
    {
        transaction->Append(Acore::StringFormat(
            "INSERT IGNORE INTO `{}` "
            "(guid, request_id, fingerprint, action, status, item_entry, amount, reservation_id, source, actor_guid) "
            "VALUES ({}, '{}', '{}', '{}', 'PENDING', {}, {}, {}, '{}', {})",
            RECEIPTS, guid, requestId, fingerprint, action, itemEntry, amount,
            reservationId.empty() ? "NULL" : "'" + reservationId + "'", source, guid));
    }

    template <typename Transaction>
    void EnsureBalanceRow(Transaction& transaction,
                          uint32 guid, uint32 itemEntry)
    {
        transaction->Append(Acore::StringFormat(
            "INSERT INTO `{}` (guid, item_entry, balance, reserved) VALUES ({}, {}, 0, 0) "
            "ON DUPLICATE KEY UPDATE guid=VALUES(guid)",
            BALANCES, guid, itemEntry));
    }


    enum class ReceiptLookup { MISSING, MATCHED, FINGERPRINT_CONFLICT };

    ReceiptLookup ReadReceipt(uint32 guid, std::string const& requestId,
                              std::string const& fingerprint, Receipt& receipt)
    {
        QueryResult result = CharacterDatabase.Query(
            "SELECT request_id, fingerprint, action, status, item_entry, amount, "
            "COALESCE(reservation_id, ''), balance_after, reserved_after "
            "FROM `{}` WHERE guid={} AND request_id='{}'", RECEIPTS, guid, requestId);
        if (!result)
            return ReceiptLookup::MISSING;
        Field* fields = result->Fetch();
        receipt.requestId = fields[0].Get<std::string>();
        receipt.fingerprint = fields[1].Get<std::string>();
        receipt.action = fields[2].Get<std::string>();
        receipt.status = fields[3].Get<std::string>();
        receipt.itemEntry = fields[4].Get<uint32>();
        receipt.amount = fields[5].Get<uint64>();
        receipt.reservationId = fields[6].Get<std::string>();
        receipt.balance = fields[7].Get<uint64>();
        receipt.reserved = fields[8].Get<uint64>();
        if (receipt.fingerprint != fingerprint)
            return ReceiptLookup::FINGERPRINT_CONFLICT;
        return ReceiptLookup::MATCHED;
    }

    void SendReceipt(Player* player, Receipt const& receipt)
    {
        Send(player, Acore::StringFormat("{}RCPT\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
            PREFIX, receipt.requestId, receipt.status, receipt.itemEntry, receipt.amount,
            receipt.reservationId.empty() ? "-" : receipt.reservationId,
            receipt.balance, receipt.reserved));
    }

    void SendBalancePage(Player* player, uint32 page)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        uint32 const offset = page * LIST_PAGE_SIZE;
        QueryResult rows = CharacterDatabase.Query(
            "SELECT item_entry, balance, reserved FROM `{}` WHERE guid={} "
            "ORDER BY item_entry LIMIT {} OFFSET {}", BALANCES, guid, LIST_PAGE_SIZE, offset);
        uint32 count = 0;
        if (rows)
        {
            do
            {
                Field* fields = rows->Fetch();
                uint32 const itemEntry = fields[0].Get<uint32>();
                uint64 const balance = fields[1].Get<uint64>();
                uint64 const reserved = fields[2].Get<uint64>();
                ItemTemplate const* item = Commodity(itemEntry);
                std::string const name = item ? SafeLabel(item->Name1) : "Unavailable item";
                Send(player, Acore::StringFormat("{}ROW\t{}\t{}\t{}\t{}\t{}",
                    PREFIX, page, itemEntry, balance, reserved, name));
                ++count;
            } while (rows->NextRow());
        }
        Send(player, Acore::StringFormat("{}END\t{}\t{}", PREFIX, page, count));
    }

    void HandleCredit(Player* player, uint32 itemEntry, uint64 amount,
                      std::string const& requestId, bool serverAuthority)
    {
        if ((!serverAuthority && !player->IsGameMaster()) || !Commodity(itemEntry)
            || !IsValidToken(requestId) || amount == 0 || amount > MAX_OPERATION_AMOUNT)
        {
            SendError(player, "FORBIDDEN_OR_INVALID");
            return;
        }
        uint32 const guid = player->GetGUID().GetCounter();
        std::string const fingerprint = Fingerprint("CREDIT", itemEntry, amount, "");
        auto transaction = CharacterDatabase.BeginTransaction();
        EnsureBalanceRow(transaction, guid, itemEntry);
        InsertPendingReceipt(transaction, guid, requestId, fingerprint, "CREDIT", itemEntry,
                             amount, "", serverAuthority ? "SERVER_SOURCE" : "GM_CREDIT");
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` SET last_request_id=IF(balance <= {} - {}, '{}', last_request_id), "
            "balance=IF(last_request_id='{}', balance + {}, balance) "
            "WHERE guid={} AND item_entry={} AND EXISTS (SELECT 1 FROM `{}` "
            "WHERE guid={} AND request_id='{}' AND status='PENDING')",
            BALANCES, MAX_CHARACTER_BALANCE, amount, requestId, requestId, amount,
            guid, itemEntry, RECEIPTS, guid, requestId));
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` AS r LEFT JOIN `{}` AS b "
            "ON b.guid=r.guid AND b.item_entry=r.item_entry "
            "SET r.status=IF(b.last_request_id='{}', 'CREDITED', 'DENIED_CAPACITY'), "
            "r.balance_after=COALESCE(b.balance, 0), r.reserved_after=COALESCE(b.reserved, 0) "
            "WHERE r.guid={} AND r.request_id='{}' AND r.status='PENDING'",
            RECEIPTS, BALANCES, requestId, guid, requestId));
        CharacterDatabase.DirectCommitTransaction(transaction);
        Receipt receipt;
        ReceiptLookup const found = ReadReceipt(guid, requestId, fingerprint, receipt);
        if (found == ReceiptLookup::MISSING)
            SendError(player, "RETRY");
        else if (found == ReceiptLookup::MATCHED)
            SendReceipt(player, receipt);
        else
            SendError(player, "REQUEST_CONFLICT");
    }

    void HandleReserve(Player* player, uint32 itemEntry, uint64 amount,
                       std::string const& requestId)
    {
        if (!Commodity(itemEntry) || amount == 0 || amount > MAX_OPERATION_AMOUNT)
        {
            SendError(player, "INVALID_COMMODITY");
            return;
        }
        uint32 const guid = player->GetGUID().GetCounter();
        std::string const fingerprint = Fingerprint("RESERVE", itemEntry, amount, requestId);
        auto transaction = CharacterDatabase.BeginTransaction();
        EnsureBalanceRow(transaction, guid, itemEntry);
        InsertPendingReceipt(transaction, guid, requestId, fingerprint, "RESERVE",
                             itemEntry, amount, requestId, "PLAYER");
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` SET last_request_id=IF(balance-reserved >= {}, '{}', last_request_id), "
            "reserved=IF(last_request_id='{}', reserved + {}, reserved) "
            "WHERE guid={} AND item_entry={} AND EXISTS (SELECT 1 FROM `{}` "
            "WHERE guid={} AND request_id='{}' AND status='PENDING')",
            BALANCES, amount, requestId, requestId, amount, guid, itemEntry,
            RECEIPTS, guid, requestId));
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` AS r JOIN `{}` AS b ON b.guid=r.guid AND b.item_entry=r.item_entry "
            "SET r.status=IF(b.last_request_id='{}', 'RESERVED', 'DENIED_FUNDS'), "
            "r.balance_after=b.balance, r.reserved_after=b.reserved "
            "WHERE r.guid={} AND r.request_id='{}' AND r.status='PENDING'",
            RECEIPTS, BALANCES, requestId, guid, requestId));
        transaction->Append(Acore::StringFormat(
            "INSERT IGNORE INTO `{}` (guid, reservation_id, item_entry, amount, state) "
            "SELECT guid, '{}', {}, {}, 'ACTIVE' FROM `{}` "
            "WHERE guid={} AND item_entry={} AND last_request_id='{}' "
            "AND EXISTS (SELECT 1 FROM `{}` WHERE guid={} AND request_id='{}' AND status='RESERVED')",
            RESERVATIONS, requestId, itemEntry, amount, BALANCES, guid, itemEntry,
            requestId, RECEIPTS, guid, requestId));
        CharacterDatabase.DirectCommitTransaction(transaction);
        Receipt receipt;
        ReceiptLookup const found = ReadReceipt(guid, requestId, fingerprint, receipt);
        if (found == ReceiptLookup::MISSING)
            SendError(player, "RETRY");
        else if (found == ReceiptLookup::MATCHED)
            SendReceipt(player, receipt);
        else
            SendError(player, "REQUEST_CONFLICT");
    }

    void HandleReservationTransition(Player* player, std::string const& action,
                                     std::string const& reservationId,
                                     std::string const& requestId)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        QueryResult reservation = CharacterDatabase.Query(
            "SELECT item_entry, amount FROM `{}` WHERE guid={} AND reservation_id='{}'",
            RESERVATIONS, guid, reservationId);
        if (!reservation)
        {
            SendError(player, "NO_RESERVATION");
            return;
        }
        Field* initialFields = reservation->Fetch();
        uint32 const itemEntry = initialFields[0].Get<uint32>();
        uint64 const amount = initialFields[1].Get<uint64>();
        if (!Commodity(itemEntry))
        {
            SendError(player, "INVALID_COMMODITY");
            return;
        }
        std::string const fingerprint = Fingerprint(action.c_str(), itemEntry, amount, reservationId);
        auto transaction = CharacterDatabase.BeginTransaction();
        EnsureBalanceRow(transaction, guid, itemEntry);
        InsertPendingReceipt(transaction, guid, requestId, fingerprint, action.c_str(),
                             itemEntry, amount, reservationId, "PLAYER");
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` SET last_request_id=IF(EXISTS (SELECT 1 FROM `{}` "
            "WHERE guid={} AND reservation_id='{}' AND state='ACTIVE'), '{}', last_request_id), "
            "balance=IF('{}'='CONSUME' AND last_request_id='{}', balance - {}, balance), "
            "reserved=IF(last_request_id='{}', reserved - {}, reserved) "
            "WHERE guid={} AND item_entry={} AND EXISTS (SELECT 1 FROM `{}` "
            "WHERE guid={} AND request_id='{}' AND status='PENDING')",
            BALANCES, RESERVATIONS, guid, reservationId, requestId, action, requestId,
            amount, requestId, amount, guid, itemEntry, RECEIPTS, guid, requestId));
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` SET state='{}' WHERE guid={} AND reservation_id='{}' "
            "AND state='ACTIVE' AND EXISTS (SELECT 1 FROM `{}` "
            "WHERE guid={} AND request_id='{}' AND status='PENDING')",
            RESERVATIONS, action == "RELEASE" ? "RELEASED" : "CONSUMED",
            guid, reservationId, RECEIPTS, guid, requestId));
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` AS r LEFT JOIN `{}` AS b "
            "ON b.guid=r.guid AND b.item_entry=r.item_entry "
            "SET r.status=IF(b.last_request_id='{}', '{}', 'DENIED_RESERVATION'), "
            "r.balance_after=COALESCE(b.balance, 0), r.reserved_after=COALESCE(b.reserved, 0) "
            "WHERE r.guid={} AND r.request_id='{}' AND r.status='PENDING'",
            RECEIPTS, BALANCES, requestId, action == "RELEASE" ? "RELEASED" : "CONSUMED",
            guid, requestId));
        CharacterDatabase.DirectCommitTransaction(transaction);
        Receipt receipt;
        ReceiptLookup const found = ReadReceipt(guid, requestId, fingerprint, receipt);
        if (found == ReceiptLookup::MISSING)
            SendError(player, "RETRY");
        else if (found == ReceiptLookup::MATCHED)
            SendReceipt(player, receipt);
        else
            SendError(player, "REQUEST_CONFLICT");
    }

    bool ParseAction(Player* player, std::vector<std::string> const& fields)
    {
        if (fields.size() < 2 || fields[0] != PREFIX_TOKEN)
            return false;
        std::string const& action = fields[1];
        if (action == "HELLO")
        {
            if (fields.size() != 3 || fields[2] != std::to_string(PROTOCOL_VERSION))
                SendError(player, "VERSION");
            else
                Send(player, std::string(PREFIX) + "OK\t1");
            return true;
        }
        if (action == "LIST")
        {
            uint32 page = 0;
            if (fields.size() != 3 || !ParseUnsigned(fields[2], page) || page > MAX_PAGE)
                SendError(player, "BAD_PAGE");
            else
                SendBalancePage(player, page);
            return true;
        }
        if (action == "CREDIT")
        {
            uint32 itemEntry = 0;
            uint64 amount = 0;
            if (fields.size() != 5 || !ParseUnsigned(fields[2], itemEntry)
                || !ParseUnsigned(fields[3], amount) || !IsValidToken(fields[4]))
                SendError(player, "MALFORMED");
            else
                HandleCredit(player, itemEntry, amount, fields[4], false);
            return true;
        }
        if (action == "RESERVE")
        {
            uint32 itemEntry = 0;
            uint64 amount = 0;
            if (fields.size() != 5 || !ParseUnsigned(fields[2], itemEntry)
                || !ParseUnsigned(fields[3], amount) || !IsValidToken(fields[4]))
                SendError(player, "MALFORMED");
            else
                HandleReserve(player, itemEntry, amount, fields[4]);
            return true;
        }
        if (action == "RELEASE" || action == "CONSUME")
        {
            if (fields.size() != 4 || !IsValidToken(fields[2]) || !IsValidToken(fields[3]))
                SendError(player, "MALFORMED");
            else
                HandleReservationTransition(player, action, fields[2], fields[3]);
            return true;
        }
        SendError(player, "UNKNOWN_ACTION");
        return true;
    }
}

// Server-side credit adapter for trusted profession/reward modules. Client
// opcodes cannot call this entry point or select another character's owner.
void PortableHoleCreditFromServer(Player* player, std::uint32_t itemEntry, std::uint64_t amount,
                                  std::string const& requestId)
{
    if (player && IsValidToken(requestId))
        HandleCredit(player, itemEntry, amount, requestId, true);
}

void PortableHoleConsumeReservation(Player* player, std::string const& reservationId,
                                    std::string const& requestId)
{
    if (player && IsValidToken(reservationId) && IsValidToken(requestId))
        HandleReservationTransition(player, "CONSUME", reservationId, requestId);
}

class PortableHolePlayerScript final : public PlayerScript
{
public:
    PortableHolePlayerScript() : PlayerScript("PortableHolePlayerScript", {
        PLAYERHOOK_ON_BEFORE_SEND_CHAT_MESSAGE, PLAYERHOOK_ON_LOGOUT
    }) { }

    void OnPlayerLogout(Player* player) override
    {
        std::lock_guard<std::mutex> lock(throttleMutex);
        lastRequest.erase(player->GetGUID().GetCounter());
    }

    void OnPlayerBeforeSendChatMessage(Player* player, uint32& /*type*/, uint32& language,
                                       std::string& message) override
    {
        if (language != LANG_ADDON || message.rfind(PREFIX, 0) != 0)
            return;
        std::string frame = message;
        message.clear();
        if (!IsValidFrame(frame))
            return;
        std::vector<std::string> fields = SplitTabs(frame);
        if (fields.size() < 2 || fields[0] != PREFIX_TOKEN)
            return;
        bool const known = fields[1] == "HELLO" || fields[1] == "LIST" || fields[1] == "CREDIT"
            || fields[1] == "RESERVE" || fields[1] == "RELEASE" || fields[1] == "CONSUME";
        if (!known)
            return;
        if (!enabled.load())
        {
            Send(player, std::string(PREFIX) + "ERR\tDISABLED");
            return;
        }
        if (!AllowRequest(player))
        {
            SendError(player, "RATE_LIMIT");
            return;
        }
        ParseAction(player, fields);
    }
};

class PortableHoleWorldScript final : public WorldScript
{
public:
    PortableHoleWorldScript() : WorldScript("PortableHoleWorldScript") { }
    void OnAfterConfigLoad(bool /*reload*/) override
    {
        enabled.store(sConfigMgr->GetOption<bool>("PortableHole.Enable", true));
    }
};

void AddPortableHoleScripts()
{
    new PortableHolePlayerScript();
    new PortableHoleWorldScript();
}
