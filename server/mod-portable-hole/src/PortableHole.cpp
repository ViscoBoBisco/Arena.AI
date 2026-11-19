// Portable Hole: server-authoritative character ledger with explicit read-only account views.
// This module stores fungible stackable counts only. It never owns/serializes
// unique Item instances and never trusts a client-supplied character GUID.
#include "ScriptMgr.h"
#include "Player.h"
#include "Chat.h"
#include "Config.h"
#include "DatabaseEnv.h"
#include "ObjectMgr.h"
#include "SpellInfo.h"
#include "SpellMgr.h"
#include "StringFormat.h"
#include "WorldSession.h"

#include "mod-portable-hole.h"

#include <algorithm>
#include <atomic>
#include <charconv>
#include <cctype>
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
    constexpr char const* VAULT_ACCESS = "portable_hole_vault_access";
    constexpr char const* PROJECTS = "portable_hole_projects";
    constexpr char const* PROJECT_ACCESS = "portable_hole_project_access";
    constexpr char const* PROJECT_RESERVATIONS = "portable_hole_project_reservations";

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

    void SendNeedComparison(Player* player, uint32 itemEntry, uint64 required)
    {
        ItemTemplate const* item = Commodity(itemEntry);
        if (!item || required == 0 || required > MAX_OPERATION_AMOUNT)
        {
            SendError(player, "INVALID_COMMODITY_OR_AMOUNT");
            return;
        }

        uint64 balance = 0;
        uint64 reserved = 0;
        QueryResult row = CharacterDatabase.Query(
            "SELECT balance, reserved FROM `{}` WHERE guid={} AND item_entry={}",
            BALANCES, player->GetGUID().GetCounter(), itemEntry);
        if (row)
        {
            Field* fields = row->Fetch();
            balance = fields[0].Get<uint64>();
            reserved = fields[1].Get<uint64>();
        }
        if (reserved > balance)
        {
            SendError(player, "LEDGER_INCONSISTENT");
            return;
        }

        uint64 const carried = player->GetItemCount(itemEntry);
        uint64 const bagGap = required > carried ? required - carried : 0;
        uint64 const virtualAvailable = balance - reserved;
        Send(player, Acore::StringFormat("{}NEED\t{}\t{}\t{}\t{}\t{}\t{}",
            PREFIX, itemEntry, required, carried, bagGap, virtualAvailable, SafeLabel(item->Name1)));
    }

    void SendProfessionReagentPlan(Player* player, uint32 spellId)
    {
        SpellInfo const* spellInfo = sSpellMgr->GetSpellInfo(spellId);
        if (!spellInfo || !player->HasSpell(spellId) || !spellInfo->IsProfession())
        {
            SendError(player, "KNOWN_PROFESSION_SPELL_REQUIRED");
            return;
        }

        uint32 const guid = player->GetGUID().GetCounter();
        uint32 reagentRows = 0;
        for (uint32 index = 0; index < MAX_SPELL_REAGENTS; ++index)
        {
            int32 const reagentEntry = spellInfo->Reagent[index];
            uint32 const required = spellInfo->ReagentCount[index];
            if (reagentEntry <= 0 || required == 0)
                continue;

            uint32 const itemEntry = static_cast<uint32>(reagentEntry);
            ItemTemplate const* item = sObjectMgr->GetItemTemplate(itemEntry);
            if (!item)
            {
                SendError(player, "REAGENT_DATA_UNAVAILABLE");
                return;
            }

            uint64 balance = 0;
            uint64 reserved = 0;
            bool const eligible = Commodity(itemEntry) != nullptr;
            if (eligible)
            {
                QueryResult row = CharacterDatabase.Query(
                    "SELECT balance,reserved FROM `{}` WHERE guid={} AND item_entry={}",
                    BALANCES, guid, itemEntry);
                if (row)
                {
                    Field* fields = row->Fetch();
                    balance = fields[0].Get<uint64>();
                    reserved = fields[1].Get<uint64>();
                }
                if (reserved > balance)
                {
                    SendError(player, "LEDGER_INCONSISTENT");
                    return;
                }
            }

            uint64 const carried = player->GetItemCount(itemEntry);
            uint64 const bagGap = required > carried ? required - carried : 0;
            Send(player, Acore::StringFormat("{}RGT\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                PREFIX, spellId, itemEntry, required, carried, bagGap, balance - reserved,
                eligible ? "VIRTUAL_AVAILABLE_NOT_CRAFTABLE" : "STANDARD_BAG_ONLY",
                SafeLabel(item->Name1)));
            ++reagentRows;
        }
        Send(player, Acore::StringFormat("{}REAGEND\t{}\t{}", PREFIX, spellId, reagentRows));
    }

    void SendBagCount(Player* player, uint32 itemEntry)
    {
        ItemTemplate const* item = Commodity(itemEntry);
        if (!item)
        {
            SendError(player, "INVALID_COMMODITY");
            return;
        }

        uint32 const count = player->GetItemCount(itemEntry);
        Send(player, Acore::StringFormat("{}BAG\t{}\t{}\t{}",
            PREFIX, itemEntry, count, SafeLabel(item->Name1)));
    }

    std::string BalanceName(uint32 itemEntry)
    {
        ItemTemplate const* item = Commodity(itemEntry);
        return item ? SafeLabel(item->Name1) : "Unavailable item";
    }

    std::string LowerAscii(std::string value)
    {
        std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c)
        {
            return static_cast<char>(std::tolower(c));
        });
        return value;
    }

    struct BalanceLine
    {
        uint32 item = 0;
        uint64 balance = 0;
        uint64 reserved = 0;
        std::string name;
    };

    uint32 CurrentAccountId(Player* player)
    {
        return player && player->GetSession() ? player->GetSession()->GetAccountId() : 0;
    }

    bool CanViewVaultAccount(Player* player, uint32 ownerAccount)
    {
        uint32 const viewerAccount = CurrentAccountId(player);
        if (ownerAccount == 0 || viewerAccount == 0)
            return false;
        if (ownerAccount == viewerAccount)
            return true;
        return CharacterDatabase.Query(
            "SELECT owner_account_id FROM `{}` WHERE owner_account_id={} AND viewer_account_id={}",
            VAULT_ACCESS, ownerAccount, viewerAccount) != nullptr;
    }

    void ShareVaultAccount(Player* player, uint32 viewerAccount, bool grant)
    {
        uint32 const ownerAccount = CurrentAccountId(player);
        if (ownerAccount == 0 || viewerAccount == 0 || ownerAccount == viewerAccount)
        {
            SendError(player, "INVALID_SHARE_ACCOUNT");
            return;
        }
        auto transaction = CharacterDatabase.BeginTransaction();
        if (grant)
            transaction->Append(Acore::StringFormat(
                "INSERT IGNORE INTO `{}` (owner_account_id,viewer_account_id,granted_by_guid) "
                "VALUES ({},{},{})", VAULT_ACCESS, ownerAccount, viewerAccount,
                player->GetGUID().GetCounter()));
        else
            transaction->Append(Acore::StringFormat(
                "DELETE FROM `{}` WHERE owner_account_id={} AND viewer_account_id={}",
                VAULT_ACCESS, ownerAccount, viewerAccount));
        CharacterDatabase.DirectCommitTransaction(transaction);
        bool const persisted = CharacterDatabase.Query(
            "SELECT owner_account_id FROM `{}` WHERE owner_account_id={} AND viewer_account_id={}",
            VAULT_ACCESS, ownerAccount, viewerAccount) != nullptr;
        if (persisted != grant)
        {
            SendError(player, "RETRY");
            return;
        }
        Send(player, Acore::StringFormat("{}SHARE\t{}\t{}", PREFIX, viewerAccount,
            grant ? "GRANTED" : "REVOKED"));
    }

    bool CanAccessProject(Player* player, uint32 ownerAccount, std::string const& projectKey,
                          bool contribute)
    {
        uint32 const account = CurrentAccountId(player);
        if (account == 0 || ownerAccount == 0)
            return false;
        if (account == ownerAccount)
            return true;
        QueryResult permission = CharacterDatabase.Query(
            "SELECT owner_account_id FROM `{}` WHERE owner_account_id={} AND project_key='{}' "
            "AND member_account_id={} AND (role='CONTRIBUTE' OR role='VIEW')",
            PROJECT_ACCESS, ownerAccount, projectKey, account);
        if (!permission)
            return false;
        if (!contribute)
            return true;
        QueryResult contributor = CharacterDatabase.Query(
            "SELECT owner_account_id FROM `{}` WHERE owner_account_id={} AND project_key='{}' "
            "AND member_account_id={} AND role='CONTRIBUTE'",
            PROJECT_ACCESS, ownerAccount, projectKey, account);
        return contributor != nullptr;
    }

    void SendBalancePage(Player* player, uint32 page, std::string const& sort,
                         uint32 viewAccount = 0)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        if (viewAccount != 0 && !CanViewVaultAccount(player, viewAccount))
        {
            SendError(player, "VAULT_ACCESS_DENIED");
            return;
        }
        std::vector<BalanceLine> rows;
        QueryResult result = viewAccount == 0
            ? CharacterDatabase.Query(
                "SELECT item_entry,balance,reserved FROM `{}` WHERE guid={} ORDER BY item_entry",
                BALANCES, guid)
            : CharacterDatabase.Query(
                "SELECT b.item_entry,SUM(b.balance),SUM(b.reserved) FROM `{}` b "
                "INNER JOIN `characters` c ON c.guid=b.guid WHERE c.account={} "
                "AND (c.account={} OR EXISTS (SELECT 1 FROM `{}` a "
                "WHERE a.owner_account_id=c.account AND a.viewer_account_id={})) "
                "GROUP BY b.item_entry ORDER BY b.item_entry",
                BALANCES, viewAccount, CurrentAccountId(player), VAULT_ACCESS, CurrentAccountId(player));
        if (result)
        {
            do
            {
                Field* fields = result->Fetch();
                BalanceLine line{ fields[0].Get<uint32>(), fields[1].Get<uint64>(),
                                  fields[2].Get<uint64>(), {} };
                if (line.reserved > line.balance)
                {
                    SendError(player, "LEDGER_INCONSISTENT");
                    return;
                }
                line.name = BalanceName(line.item);
                rows.emplace_back(std::move(line));
            } while (result->NextRow());
        }

        std::stable_sort(rows.begin(), rows.end(), [&sort](BalanceLine const& a, BalanceLine const& b)
        {
            if (sort == "NAME" && a.name != b.name)
                return a.name < b.name;
            if (sort == "BALANCE" && a.balance != b.balance)
                return a.balance > b.balance;
            if (sort == "AVAILABLE")
            {
                uint64 const availableA = a.balance - a.reserved;
                uint64 const availableB = b.balance - b.reserved;
                if (availableA != availableB)
                    return availableA > availableB;
            }
            return a.item < b.item;
        });

        std::size_t const offset = static_cast<std::size_t>(page) * LIST_PAGE_SIZE;
        uint32 count = 0;
        for (std::size_t index = offset; index < rows.size() && count < LIST_PAGE_SIZE; ++index)
        {
            BalanceLine const& row = rows[index];
            Send(player, Acore::StringFormat("{}ROW\t{}\t{}\t{}\t{}\t{}",
                PREFIX, page, row.item, row.balance, row.reserved, row.name));
            ++count;
        }
        Send(player, Acore::StringFormat("{}END\t{}\t{}\t{}\t{}", PREFIX, page, count,
            viewAccount, CurrentAccountId(player)));
    }

    void SendSearch(Player* player, std::string const& term, uint32 viewAccount = 0)
    {
        if (term.empty() || term.size() > 32 || !std::all_of(term.begin(), term.end(),
            [](unsigned char c) { return std::isalnum(c) || c == ' ' || c == '-' || c == '\''; }))
        {
            SendError(player, "BAD_SEARCH");
            return;
        }
        std::string const needle = LowerAscii(term);
        uint32 const guid = player->GetGUID().GetCounter();
        if (viewAccount != 0 && !CanViewVaultAccount(player, viewAccount))
        {
            SendError(player, "VAULT_ACCESS_DENIED");
            return;
        }
        std::vector<BalanceLine> matches;
        QueryResult result = viewAccount == 0
            ? CharacterDatabase.Query(
                "SELECT item_entry,balance,reserved FROM `{}` WHERE guid={} ORDER BY item_entry",
                BALANCES, guid)
            : CharacterDatabase.Query(
                "SELECT b.item_entry,SUM(b.balance),SUM(b.reserved) FROM `{}` b "
                "INNER JOIN `characters` c ON c.guid=b.guid WHERE c.account={} "
                "AND (c.account={} OR EXISTS (SELECT 1 FROM `{}` a "
                "WHERE a.owner_account_id=c.account AND a.viewer_account_id={})) "
                "GROUP BY b.item_entry ORDER BY b.item_entry",
                BALANCES, viewAccount, CurrentAccountId(player), VAULT_ACCESS, CurrentAccountId(player));
        if (result)
        {
            do
            {
                Field* fields = result->Fetch();
                BalanceLine line{ fields[0].Get<uint32>(), fields[1].Get<uint64>(),
                                  fields[2].Get<uint64>(), BalanceName(fields[0].Get<uint32>()) };
                if (line.reserved > line.balance)
                {
                    SendError(player, "LEDGER_INCONSISTENT");
                    return;
                }
                if (LowerAscii(line.name).find(needle) != std::string::npos)
                    matches.emplace_back(std::move(line));
            } while (result->NextRow());
        }
        uint32 sent = 0;
        for (BalanceLine const& row : matches)
        {
            if (sent >= LIST_PAGE_SIZE)
                break;
            Send(player, Acore::StringFormat("{}FINDROW\t{}\t{}\t{}\t{}",
                PREFIX, row.item, row.balance, row.reserved, row.name));
            ++sent;
        }
        Send(player, Acore::StringFormat("{}FINDEND\t{}\t{}", PREFIX, sent,
            matches.size() > LIST_PAGE_SIZE ? 1 : 0));
    }

    void SendBulkPlan(Player* player, uint32 itemEntry, uint64 requested)
    {
        ItemTemplate const* item = Commodity(itemEntry);
        if (!item || requested == 0 || requested > MAX_OPERATION_AMOUNT)
        {
            SendError(player, "INVALID_COMMODITY_OR_AMOUNT");
            return;
        }
        uint64 const carried = player->GetItemCount(itemEntry, false);
        uint64 const planned = std::min(carried, requested);
        Send(player, Acore::StringFormat("{}PLAN\t{}\t{}\t{}\t{}\t{}",
            PREFIX, itemEntry, requested, carried, planned, SafeLabel(item->Name1)));
    }

    void SendPins(Player* player)
    {
        uint32 const guid = player->GetGUID().GetCounter();
        QueryResult rows = CharacterDatabase.Query(
            "SELECT item_entry FROM `portable_hole_pins` WHERE guid={} ORDER BY created_at,item_entry LIMIT 10",
            guid);
        uint32 count = 0;
        if (rows)
        {
            do
            {
                Field* fields = rows->Fetch();
                uint32 const itemEntry = fields[0].Get<uint32>();
                ItemTemplate const* item = Commodity(itemEntry);
                if (item)
                {
                    Send(player, Acore::StringFormat("{}PINROW\t{}\t{}",
                        PREFIX, itemEntry, SafeLabel(item->Name1)));
                    ++count;
                }
            } while (rows->NextRow());
        }
        Send(player, Acore::StringFormat("{}PINEND\t{}", PREFIX, count));
    }

    void MutatePin(Player* player, uint32 itemEntry, bool add)
    {
        if (!Commodity(itemEntry))
        {
            SendError(player, "INVALID_COMMODITY");
            return;
        }
        uint32 const guid = player->GetGUID().GetCounter();
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "INSERT INTO `portable_hole_pin_state` (guid) VALUES ({}) "
            "ON DUPLICATE KEY UPDATE guid=VALUES(guid)", guid));
        transaction->Append(Acore::StringFormat(
            "SELECT guid FROM `portable_hole_pin_state` WHERE guid={} FOR UPDATE", guid));
        if (add)
            transaction->Append(Acore::StringFormat(
                "INSERT IGNORE INTO `portable_hole_pins` (guid,item_entry) "
                "SELECT {},{} WHERE (SELECT COUNT(*) FROM `portable_hole_pins` WHERE guid={}) < 10",
                guid, itemEntry, guid));
        else
            transaction->Append(Acore::StringFormat(
                "DELETE FROM `portable_hole_pins` WHERE guid={} AND item_entry={}", guid, itemEntry));
        CharacterDatabase.DirectCommitTransaction(transaction);

        QueryResult state = CharacterDatabase.Query(
            "SELECT COUNT(*) FROM `portable_hole_pins` WHERE guid={}", guid);
        uint32 count = state ? state->Fetch()[0].Get<uint32>() : 0;
        QueryResult pin = CharacterDatabase.Query(
            "SELECT item_entry FROM `portable_hole_pins` WHERE guid={} AND item_entry={}", guid, itemEntry);
        if (add && !pin && count >= 10)
            SendError(player, "PIN_LIMIT");
        else
            Send(player, Acore::StringFormat("{}PIN\t{}\t{}", PREFIX, itemEntry, pin ? 1 : 0));
        SendPins(player);
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

    void CreateProject(Player* player, std::string const& projectKey, uint32 itemEntry,
                       uint64 targetAmount)
    {
        uint32 const account = CurrentAccountId(player);
        if (!account || !IsValidToken(projectKey, 32) || !Commodity(itemEntry)
            || !targetAmount || targetAmount > MAX_OPERATION_AMOUNT)
        {
            SendError(player, "INVALID_PROJECT");
            return;
        }
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "INSERT IGNORE INTO `{}` (owner_account_id,project_key,item_entry,target_amount,created_by_guid) "
            "VALUES({},'{}',{},{},{})", PROJECTS, account, projectKey, itemEntry, targetAmount,
            player->GetGUID().GetCounter()));
        CharacterDatabase.DirectCommitTransaction(transaction);
        QueryResult project = CharacterDatabase.Query(
            "SELECT item_entry,target_amount FROM `{}` WHERE owner_account_id={} AND project_key='{}'",
            PROJECTS, account, projectKey);
        if (!project)
        {
            SendError(player, "RETRY");
            return;
        }
        Field* fields = project->Fetch();
        if (fields[0].Get<uint32>() != itemEntry || fields[1].Get<uint64>() != targetAmount)
        {
            SendError(player, "PROJECT_KEY_CONFLICT");
            return;
        }
        Send(player, Acore::StringFormat("{}PROJECT\tCREATED\t{}\t{}\t{}\t{}",
            PREFIX, account, projectKey, itemEntry, targetAmount));
    }

    void ChangeProjectAccess(Player* player, std::string const& projectKey,
                             uint32 memberAccount, std::string const& role, bool grant)
    {
        uint32 const ownerAccount = CurrentAccountId(player);
        if (!ownerAccount || !IsValidToken(projectKey, 32) || !memberAccount
            || memberAccount == ownerAccount || (grant && role != "VIEW" && role != "CONTRIBUTE"))
        {
            SendError(player, "INVALID_PROJECT_ACCESS");
            return;
        }
        if (!CharacterDatabase.Query(
                "SELECT owner_account_id FROM `{}` WHERE owner_account_id={} AND project_key='{}'",
                PROJECTS, ownerAccount, projectKey))
        {
            SendError(player, "PROJECT_NOT_FOUND");
            return;
        }
        auto transaction = CharacterDatabase.BeginTransaction();
        if (grant)
            transaction->Append(Acore::StringFormat(
                "INSERT INTO `{}` (owner_account_id,project_key,member_account_id,role,granted_by_guid) "
                "VALUES({},'{}',{},'{}',{}) ON DUPLICATE KEY UPDATE role=VALUES(role),granted_by_guid=VALUES(granted_by_guid)",
                PROJECT_ACCESS, ownerAccount, projectKey, memberAccount, role,
                player->GetGUID().GetCounter()));
        else
            transaction->Append(Acore::StringFormat(
                "DELETE FROM `{}` WHERE owner_account_id={} AND project_key='{}' AND member_account_id={}",
                PROJECT_ACCESS, ownerAccount, projectKey, memberAccount));
        CharacterDatabase.DirectCommitTransaction(transaction);
        QueryResult access = CharacterDatabase.Query(
            "SELECT role FROM `{}` WHERE owner_account_id={} AND project_key='{}' AND member_account_id={}",
            PROJECT_ACCESS, ownerAccount, projectKey, memberAccount);
        if ((grant && (!access || access->Fetch()[0].Get<std::string>() != role)) || (!grant && access))
        {
            SendError(player, "RETRY");
            return;
        }
        Send(player, Acore::StringFormat("{}PROJECT\tACCESS\t{}\t{}\t{}\t{}",
            PREFIX, projectKey, memberAccount, grant ? role : "REVOKED", ownerAccount));
    }

    void ShowProject(Player* player, uint32 ownerAccount, std::string const& projectKey)
    {
        if (!ownerAccount || !IsValidToken(projectKey, 32)
            || !CanAccessProject(player, ownerAccount, projectKey, false))
        {
            SendError(player, "PROJECT_ACCESS_DENIED");
            return;
        }
        QueryResult project = CharacterDatabase.Query(
            "SELECT p.item_entry,p.target_amount,p.reserved_amount,p.status, "
            "(SELECT COUNT(DISTINCT r.contributor_account_id) FROM `{}` r "
            "WHERE r.owner_account_id=p.owner_account_id AND r.project_key=p.project_key AND r.state='HELD') "
            "FROM `{}` p WHERE p.owner_account_id={} AND p.project_key='{}'",
            PROJECT_RESERVATIONS, PROJECTS, ownerAccount, projectKey);
        if (!project)
        {
            SendError(player, "PROJECT_NOT_FOUND");
            return;
        }
        Field* fields = project->Fetch();
        Send(player, Acore::StringFormat("{}PROJECT\tSUMMARY\t{}\t{}\t{}\t{}\t{}\t{}",
            PREFIX, ownerAccount, projectKey, fields[0].Get<uint32>(), fields[1].Get<uint64>(),
            fields[2].Get<uint64>(), fields[3].Get<std::string>(), fields[4].Get<uint32>()));
    }

    void ReserveProject(Player* player, uint32 ownerAccount, std::string const& projectKey,
                        uint64 amount, std::string const& requestId)
    {
        uint32 const account = CurrentAccountId(player);
        uint32 const guid = player->GetGUID().GetCounter();
        if (!account || !ownerAccount || !IsValidToken(projectKey, 32)
            || !IsValidToken(requestId) || !amount || amount > MAX_OPERATION_AMOUNT
            || !CanAccessProject(player, ownerAccount, projectKey, true))
        {
            SendError(player, "PROJECT_ACCESS_OR_REQUEST_INVALID");
            return;
        }
        QueryResult project = CharacterDatabase.Query(
            "SELECT item_entry FROM `{}` WHERE owner_account_id={} AND project_key='{}' AND status='OPEN'",
            PROJECTS, ownerAccount, projectKey);
        if (!project)
        {
            SendError(player, "PROJECT_NOT_OPEN");
            return;
        }
        uint32 const itemEntry = project->Fetch()[0].Get<uint32>();
        if (!Commodity(itemEntry))
        {
            SendError(player, "INVALID_COMMODITY");
            return;
        }
        auto transaction = CharacterDatabase.BeginTransaction();
        EnsureBalanceRow(transaction, guid, itemEntry);
        transaction->Append(Acore::StringFormat(
            "INSERT IGNORE INTO `{}` (owner_account_id,project_key,request_id,contributor_account_id,guid,item_entry,amount,state) "
            "VALUES({},'{}','{}',{},{},{},{},'PENDING')", PROJECT_RESERVATIONS,
            ownerAccount, projectKey, requestId, account, guid, itemEntry, amount));
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` b, `{}` p, `{}` r SET b.reserved=b.reserved+r.amount, "
            "p.reserved_amount=p.reserved_amount+r.amount, r.state='HELD' "
            "WHERE r.owner_account_id={} AND r.project_key='{}' AND r.request_id='{}' "
            "AND r.state='PENDING' AND r.contributor_account_id={} AND r.guid={} "
            "AND r.item_entry={} AND r.amount={} AND b.guid=r.guid AND b.item_entry=r.item_entry "
            "AND p.owner_account_id=r.owner_account_id AND p.project_key=r.project_key "
            "AND p.status='OPEN' AND b.balance-b.reserved>=r.amount "
            "AND p.reserved_amount+r.amount<=p.target_amount "
            "AND (p.owner_account_id={} OR EXISTS (SELECT 1 FROM `{}` a "
            "WHERE a.owner_account_id=p.owner_account_id AND a.project_key=p.project_key "
            "AND a.member_account_id={} AND a.role='CONTRIBUTE'))",
            BALANCES, PROJECTS, PROJECT_RESERVATIONS, ownerAccount, projectKey, requestId,
            account, guid, itemEntry, amount, account, PROJECT_ACCESS, account));
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` SET state='DENIED' WHERE owner_account_id={} AND project_key='{}' "
            "AND request_id='{}' AND state='PENDING'", PROJECT_RESERVATIONS,
            ownerAccount, projectKey, requestId));
        CharacterDatabase.DirectCommitTransaction(transaction);
        QueryResult result = CharacterDatabase.Query(
            "SELECT contributor_account_id,guid,item_entry,amount,state FROM `{}` "
            "WHERE owner_account_id={} AND project_key='{}' AND request_id='{}'",
            PROJECT_RESERVATIONS, ownerAccount, projectKey, requestId);
        if (!result)
        {
            SendError(player, "RETRY");
            return;
        }
        Field* fields = result->Fetch();
        if (fields[0].Get<uint32>() != account || fields[1].Get<uint32>() != guid
            || fields[2].Get<uint32>() != itemEntry || fields[3].Get<uint64>() != amount)
        {
            SendError(player, "REQUEST_CONFLICT");
            return;
        }
        Send(player, Acore::StringFormat("{}PROJECT\tRESERVATION\t{}\t{}\t{}\t{}\t{}",
            PREFIX, requestId, fields[4].Get<std::string>(), itemEntry, amount, ownerAccount, projectKey));
    }

    void ReleaseProjectReservation(Player* player, uint32 ownerAccount,
                                   std::string const& projectKey,
                                   std::string const& reservationId,
                                   std::string const& releaseId)
    {
        uint32 const account = CurrentAccountId(player);
        if (!account || !ownerAccount || !IsValidToken(projectKey, 32)
            || !IsValidToken(reservationId) || !IsValidToken(releaseId)
            || !CanAccessProject(player, ownerAccount, projectKey, true))
        {
            SendError(player, "PROJECT_ACCESS_OR_REQUEST_INVALID");
            return;
        }
        auto transaction = CharacterDatabase.BeginTransaction();
        transaction->Append(Acore::StringFormat(
            "UPDATE `{}` r, `{}` p, `{}` b SET r.state='RELEASED',r.release_request_id='{}', "
            "p.reserved_amount=p.reserved_amount-r.amount,b.reserved=b.reserved-r.amount "
            "WHERE r.owner_account_id={} AND r.project_key='{}' AND r.request_id='{}' "
            "AND r.state='HELD' AND r.guid=b.guid AND r.item_entry=b.item_entry "
            "AND p.owner_account_id=r.owner_account_id AND p.project_key=r.project_key "
            "AND p.reserved_amount>=r.amount AND b.reserved>=r.amount "
            "AND ({}=p.owner_account_id OR {}=r.contributor_account_id)",
            PROJECT_RESERVATIONS, PROJECTS, BALANCES, releaseId, ownerAccount, projectKey,
            reservationId, account, account));
        CharacterDatabase.DirectCommitTransaction(transaction);
        QueryResult result = CharacterDatabase.Query(
            "SELECT state,COALESCE(release_request_id,'') FROM `{}` WHERE owner_account_id={} "
            "AND project_key='{}' AND request_id='{}'", PROJECT_RESERVATIONS,
            ownerAccount, projectKey, reservationId);
        if (!result)
        {
            SendError(player, "PROJECT_RESERVATION_NOT_FOUND");
            return;
        }
        Field* fields = result->Fetch();
        if (fields[0].Get<std::string>() != "RELEASED"
            || fields[1].Get<std::string>() != releaseId)
        {
            SendError(player, "PROJECT_RELEASE_CONFLICT");
            return;
        }
        Send(player, Acore::StringFormat("{}PROJECT\tRELEASED\t{}\t{}\t{}",
            PREFIX, releaseId, ownerAccount, projectKey));
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
        if (action == "PROJECT")
        {
            uint32 account = 0;
            uint32 itemEntry = 0;
            uint32 memberAccount = 0;
            uint64 amount = 0;
            if (fields.size() == 6 && fields[2] == "CREATE"
                && ParseUnsigned(fields[4], itemEntry) && ParseUnsigned(fields[5], amount))
                CreateProject(player, fields[3], itemEntry, amount);
            else if (fields.size() == 6 && fields[2] == "GRANT"
                     && ParseUnsigned(fields[4], memberAccount))
                ChangeProjectAccess(player, fields[3], memberAccount, fields[5], true);
            else if (fields.size() == 5 && fields[2] == "REVOKE"
                     && ParseUnsigned(fields[4], memberAccount))
                ChangeProjectAccess(player, fields[3], memberAccount, "", false);
            else if (fields.size() == 5 && fields[2] == "SHOW"
                     && ParseUnsigned(fields[3], account))
                ShowProject(player, account, fields[4]);
            else if (fields.size() == 7 && fields[2] == "RESERVE"
                     && ParseUnsigned(fields[3], account) && ParseUnsigned(fields[5], amount))
                ReserveProject(player, account, fields[4], amount, fields[6]);
            else if (fields.size() == 7 && fields[2] == "RELEASE"
                     && ParseUnsigned(fields[3], account))
                ReleaseProjectReservation(player, account, fields[4], fields[5], fields[6]);
            else
                SendError(player, "PROJECT_MALFORMED");
            return true;
        }
        if (action == "NEED")
        {
            uint32 itemEntry = 0;
            uint64 required = 0;
            if (fields.size() != 4 || !ParseUnsigned(fields[2], itemEntry)
                || !ParseUnsigned(fields[3], required))
                SendError(player, "MALFORMED");
            else
                SendNeedComparison(player, itemEntry, required);
            return true;
        }
        if (action == "BAG")
        {
            uint32 itemEntry = 0;
            if (fields.size() != 3 || !ParseUnsigned(fields[2], itemEntry))
                SendError(player, "MALFORMED");
            else
                SendBagCount(player, itemEntry);
            return true;
        }
        if (action == "PLAN")
        {
            uint32 itemEntry = 0;
            uint64 amount = 0;
            if (fields.size() != 4 || !ParseUnsigned(fields[2], itemEntry)
                || !ParseUnsigned(fields[3], amount))
                SendError(player, "MALFORMED");
            else
                SendBulkPlan(player, itemEntry, amount);
            return true;
        }
        if (action == "REAGENTS")
        {
            uint32 spellId = 0;
            if (fields.size() != 3 || !ParseUnsigned(fields[2], spellId) || spellId == 0)
                SendError(player, "MALFORMED");
            else
                SendProfessionReagentPlan(player, spellId);
            return true;
        }
        if (action == "SEARCH")
        {
            uint32 viewAccount = 0;
            if ((fields.size() != 3 && fields.size() != 4)
                || (fields.size() == 4 && !ParseUnsigned(fields[3], viewAccount)))
                SendError(player, "MALFORMED");
            else
                SendSearch(player, fields[2], viewAccount);
            return true;
        }
        if (action == "SHARE")
        {
            uint32 viewerAccount = 0;
            if (fields.size() != 4 || !ParseUnsigned(fields[2], viewerAccount)
                || (fields[3] != "GRANT" && fields[3] != "REVOKE"))
                SendError(player, "MALFORMED");
            else
                ShareVaultAccount(player, viewerAccount, fields[3] == "GRANT");
            return true;
        }
        if (action == "PINS")
        {
            if (fields.size() != 2)
                SendError(player, "MALFORMED");
            else
                SendPins(player);
            return true;
        }
        if (action == "PIN")
        {
            uint32 itemEntry = 0;
            if (fields.size() != 4 || !ParseUnsigned(fields[2], itemEntry)
                || (fields[3] != "ADD" && fields[3] != "REMOVE"))
                SendError(player, "MALFORMED");
            else
                MutatePin(player, itemEntry, fields[3] == "ADD");
            return true;
        }
        if (action == "LIST")
        {
            uint32 page = 0;
            uint32 viewAccount = 0;
            std::string sort = fields.size() >= 4 ? fields[3] : "ENTRY";
            if ((fields.size() < 3 || fields.size() > 5)
                || !ParseUnsigned(fields[2], page) || page > MAX_PAGE
                || (fields.size() == 5 && !ParseUnsigned(fields[4], viewAccount))
                || (sort != "ENTRY" && sort != "NAME" && sort != "BALANCE" && sort != "AVAILABLE"))
                SendError(player, "BAD_PAGE_OR_SORT");
            else
                SendBalancePage(player, page, sort, viewAccount);
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
        bool const known = fields[1] == "HELLO" || fields[1] == "LIST" || fields[1] == "BAG"
            || fields[1] == "NEED" || fields[1] == "REAGENTS" || fields[1] == "SEARCH"
            || fields[1] == "SHARE" || fields[1] == "PLAN"
            || fields[1] == "PINS" || fields[1] == "PIN"
            || fields[1] == "CREDIT" || fields[1] == "RESERVE" || fields[1] == "PROJECT"
            || fields[1] == "RELEASE" || fields[1] == "CONSUME";
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
