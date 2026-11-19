local PREFIX = "PH1"
local MAX_BYTES = 255
local MAX_PINS = 10
local MIN_INTERVAL = 0.200
local sequence = 0
local lastSend = 0
local retryAt = 0
local selectedItem = nil

local function savedData()
    if type(PortableHoleDB) ~= "table" then PortableHoleDB = {} end
    if type(PortableHoleDB.characters) ~= "table" then PortableHoleDB.characters = {} end
    local key = UnitGUID("player") or UnitName("player") or "unknown"
    if type(PortableHoleDB.characters[key]) ~= "table" then
        PortableHoleDB.characters[key] = { sequence = 0, pending = nil }
    end
    local data = PortableHoleDB.characters[key]
    local pins, seen = {}, {}
    if type(data.pins) == "table" then
        for _, value in ipairs(data.pins) do
            local item = tonumber(value)
            if item and item >= 1 and item <= 4294967295 and item == math.floor(item)
                and not seen[item] then
                pins[#pins + 1] = item
                seen[item] = true
                if #pins >= MAX_PINS then break end
            end
        end
    end
    data.pins = pins
    return data
end
local frame, statusText, itemInput, amountInput, reservationInput, rowButtons, pageLabel, pinStatus
local vaultAccountInput, shareAccountInput, accountInfo
local viewAccountId, ownAccountId = 0, 0
local page = 0
local pendingPinAdds = {}
local sortModes, sortIndex = { "ENTRY", "NAME", "BALANCE", "AVAILABLE" }, 1

local function splitTabs(value)
    local result, start = {}, 1
    while true do
        local at = value:find("\t", start, true)
        if not at then result[#result + 1] = value:sub(start); break end
        result[#result + 1] = value:sub(start, at - 1)
        start = at + 1
    end
    return result
end

local function validFrame(value)
    if type(value) ~= "string" or #value > MAX_BYTES then return false end
    for index = 1, #value do
        local byte = string.byte(value, index)
        if byte ~= 9 and (byte < 32 or byte > 126) then return false end
    end
    return true
end

local function uint(value, maximum)
    if type(value) ~= "string" or not value:match("^%d+$") then return nil end
    local n = tonumber(value)
    if not n or n < 1 or n > maximum then return nil end
    return math.floor(n)
end

local function requestId()
    local data = savedData()
    data.sequence = (tonumber(data.sequence) or 0) + 1
    sequence = data.sequence
    return tostring(time()) .. "-" .. tostring(sequence) .. "-" .. tostring(math.floor(GetTime() * 1000))
end

local function send(opcode, ...)
    if not frame or not frame:IsShown() then return false end
    local now = GetTime()
    if now - lastSend < MIN_INTERVAL then
        statusText:SetText("Please wait before sending another request.")
        return false
    end
    local parts = { PREFIX, opcode }
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring(select(i, ...)) end
    local payload = table.concat(parts, "\t")
    if not validFrame(payload) then statusText:SetText("Invalid or oversized request."); return false end
    lastSend = now
    SendAddonMessage(PREFIX, payload, "WHISPER", UnitName("player"))
    statusText:SetText("Request sent; waiting for the server receipt.")
    return true
end

local function sendMutation(action, arguments)
    local data = savedData()
    local canonical = table.concat(arguments, "\t")
    local pending = data.pending
    if pending and (pending.action ~= action or pending.arguments ~= canonical) then
        statusText:SetText("A previous mutation is unconfirmed. Retry it before starting another.")
        return false
    end
    if not pending then
        pending = { action = action, arguments = canonical, id = requestId() }
        data.pending = pending
    end
    local values = splitTabs(pending.arguments)
    values[#values + 1] = pending.id
    retryAt = GetTime() + 5
    return send(action, unpack(values))
end

local function retryPending()
    local pending = savedData().pending
    if not pending then return end
    local arguments = splitTabs(pending.arguments or "")
    arguments[#arguments + 1] = pending.id
    retryAt = GetTime() + 5
    send(pending.action, unpack(arguments))
end

local function repaint(rows)
    for i = 1, #rowButtons do rowButtons[i]:Hide() end
    for i, row in ipairs(rows) do
        if i > #rowButtons then break end
        local button = rowButtons[i]
        button.itemEntry = row.item
        button:SetText(string.format("%d  %s   %d total / %d reserved",
            row.item, row.name, row.balance, row.reserved))
        button:Show()
    end
end

local function refresh()
    send("LIST", page, sortModes[sortIndex], viewAccountId)
end

local function selectedAccount(box, allowCurrentCharacter)
    local value = box:GetText()
    if allowCurrentCharacter and (value == "" or value == "0") then return 0 end
    if type(value) ~= "string" or not value:match("^%d+$") then return nil end
    local account = tonumber(value)
    if not account or account < 1 or account > 4294967295 then return nil end
    return math.floor(account)
end

local function openVaultView()
    local account = selectedAccount(vaultAccountInput, true)
    if account == nil then
        statusText:SetText("Enter a valid account ID, or 0 for this character's vault.")
        return
    end
    viewAccountId = account
    page = 0
    frame.rows = {}
    repaint(frame.rows)
    refresh()
end

local function changeShare(grant)
    local account = selectedAccount(shareAccountInput, false)
    if not account then
        statusText:SetText("Enter the other account's numeric ID.")
        return
    end
    send("SHARE", account, grant and "GRANT" or "REVOKE")
end

local function reserve()
    local item = uint(itemInput:GetText(), 4294967295)
    local amount = uint(amountInput:GetText(), 1000000000)
    if not item or not amount then statusText:SetText("Enter a positive item entry and amount."); return end
    sendMutation("RESERVE", { tostring(item), tostring(amount) })
end

local function bagCount()
    local item = uint(itemInput:GetText(), 4294967295)
    if not item then statusText:SetText("Enter a positive item entry."); return end
    send("BAG", item)
end

local function compareNeed()
    local item = uint(itemInput:GetText(), 4294967295)
    local required = uint(amountInput:GetText(), 1000000000)
    if not item or not required then
        statusText:SetText("Enter a positive item entry and required count.")
        return
    end
    send("NEED", item, required)
end

local function updatePinStatus(data)
    pinStatus:SetText(string.format("Saved pins %d/%d", #data.pins, MAX_PINS))
end

local function pinItem()
    local item = uint(itemInput:GetText(), 4294967295)
    if not item then statusText:SetText("Enter a positive item entry to pin."); return end
    local data = savedData()
    for index, pinned in ipairs(data.pins) do
        if pinned == item then
            data.pinCursor = index
            updatePinStatus(data)
            send("PIN", item, "ADD")
            statusText:SetText("Already pinned locally; syncing to the server.")
            return
        end
    end
    if #data.pins >= MAX_PINS then
        statusText:SetText("Pin limit reached; remove a saved pin before adding another.")
        return
    end
    data.pins[#data.pins + 1] = item
    data.pinCursor = #data.pins
    updatePinStatus(data)
    send("PIN", item, "ADD")
    statusText:SetText("Saved item " .. tostring(item) .. " to this character's pins.")
end

local function unpinItem()
    local item = uint(itemInput:GetText(), 4294967295)
    if not item then statusText:SetText("Enter a positive item entry to unpin."); return end
    local data, found = savedData(), false
    local pins = {}
    for _, pinned in ipairs(data.pins) do
        if pinned == item then found = true else pins[#pins + 1] = pinned end
    end
    data.pins = pins
    data.pinCursor = math.min(tonumber(data.pinCursor) or 1, math.max(1, #pins))
    updatePinStatus(data)
    send("PIN", item, "REMOVE")
    statusText:SetText(found and "Removed pin and syncing with server." or "Requested server pin removal.")
end

local function syncPins()
    frame.serverPins = {}
    send("PINS")
end

local function cyclePin()
    local data = savedData()
    if #data.pins == 0 then
        statusText:SetText("No saved pins for this character yet.")
        updatePinStatus(data)
        return
    end
    data.pinCursor = ((tonumber(data.pinCursor) or 0) % #data.pins) + 1
    local item = data.pins[data.pinCursor]
    itemInput:SetText(tostring(item))
    updatePinStatus(data)
    statusText:SetText("Selected saved item " .. tostring(item) .. ".")
end

local function transition(action)
    local reservation = reservationInput:GetText()
    if type(reservation) ~= "string" or not reservation:match("^[%w%._:%-]+$") or #reservation > 64 then
        statusText:SetText("Enter a valid reservation ID from a receipt.")
        return
    end
    sendMutation(action, { reservation })
end

local function adminCredit()
    local item = uint(itemInput:GetText(), 4294967295)
    local amount = uint(amountInput:GetText(), 1000000000)
    if not item or not amount then statusText:SetText("Enter a positive item entry and amount."); return end
    sendMutation("CREDIT", { tostring(item), tostring(amount) })
end

local function onMessage(_, prefix, message, distribution, sender)
    if prefix ~= PREFIX or distribution ~= "WHISPER" or sender ~= UnitName("player")
        or not validFrame(message) then return end
    local fields = splitTabs(message)
    if fields[1] ~= PREFIX then return end
    if fields[2] == "NEED" and #fields == 8 then
        local required, carried = tonumber(fields[4]), tonumber(fields[5])
        local gap, available = tonumber(fields[6]), tonumber(fields[7])
        if required and carried and gap and available then
            statusText:SetText(fields[8] .. ": need " .. tostring(required) .. ", carried "
                .. tostring(carried) .. ", bag gap " .. tostring(gap) .. "; virtual available "
                .. tostring(available) .. " (not craftable from this view).")
        end
    elseif fields[2] == "BAG" and #fields == 5 then
        local item, count = tonumber(fields[3]), tonumber(fields[4])
        if item and count and item >= 1 and count >= 0 then
            statusText:SetText("Carried: " .. tostring(count) .. " x " .. fields[5]
                .. " (bank excluded; read-only)")
        end
    elseif fields[2] == "FINDROW" and #fields == 6 then
        local item, balance, reserved = tonumber(fields[3]), tonumber(fields[4]), tonumber(fields[5])
        if item and balance and reserved then
            statusText:SetText("Match " .. fields[6] .. " (" .. tostring(item) .. "): "
                .. tostring(balance - reserved) .. " available.")
        end
    elseif fields[2] == "FINDEND" and #fields == 4 then
        if fields[4] == "1" then statusText:SetText("Showing first five matches; narrow the search.")
        elseif (tonumber(fields[3]) or 0) == 0 then statusText:SetText("No matching ledger entries.") end
    elseif fields[2] == "SHARE" and #fields == 4 then
        local account = tonumber(fields[3])
        if account and fields[4] == "GRANTED" then
            statusText:SetText("Granted read-only vault access to account " .. tostring(account) .. ".")
        elseif account and fields[4] == "REVOKED" then
            statusText:SetText("Revoked read-only vault access for account " .. tostring(account) .. ".")
        end
    elseif fields[2] == "PLAN" and #fields == 7 then
        local item, requested, carried, planned = tonumber(fields[3]), tonumber(fields[4]),
            tonumber(fields[5]), tonumber(fields[6])
        if item and requested and carried and planned then
            statusText:SetText(fields[7] .. ": plan to deposit " .. tostring(planned) .. " of "
                .. tostring(requested) .. " requested; carried " .. tostring(carried)
                .. ". PLAN ONLY — no item moved.")
        end
    elseif fields[2] == "PINROW" and #fields == 4 then
        local item = tonumber(fields[3])
        if item and item >= 1 and item <= 4294967295 then
            frame.serverPins = frame.serverPins or {}
            frame.serverPins[#frame.serverPins + 1] = item
        end
    elseif fields[2] == "PINEND" and #fields == 3 then
        local data = savedData()
        local serverPins = frame.serverPins or {}
        local combined, seen = {}, {}
        for _, pinned in ipairs(data.pins) do
            if not seen[pinned] and #combined < MAX_PINS then
                combined[#combined + 1] = pinned; seen[pinned] = true
                local present = false
                for _, serverPin in ipairs(serverPins) do if serverPin == pinned then present = true end end
                if not present then pendingPinAdds[#pendingPinAdds + 1] = pinned end
            end
        end
        for _, pinned in ipairs(serverPins) do
            if not seen[pinned] and #combined < MAX_PINS then
                combined[#combined + 1] = pinned; seen[pinned] = true
            end
        end
        data.pins = combined
        updatePinStatus(data)
        statusText:SetText("Loaded " .. tostring(tonumber(fields[3]) or #serverPins)
            .. " server-saved pin(s); local pins are syncing.")
    elseif fields[2] == "ROW" and #fields == 7 then
        local row = { page = tonumber(fields[3]), item = tonumber(fields[4]),
                      balance = tonumber(fields[5]), reserved = tonumber(fields[6]), name = fields[7] }
        if row.page == page and row.item and row.balance and row.reserved then
            local rows = frame.rows or {}
            rows[#rows + 1] = row
            frame.rows = rows
            repaint(rows)
        end
    elseif fields[2] == "END" and (#fields == 4 or #fields == 6) then
        if tonumber(fields[3]) == page then
            pageLabel:SetText("Page " .. tostring(page + 1))
            if #fields == 6 then
                local targetAccount, currentAccount = tonumber(fields[5]), tonumber(fields[6])
                if targetAccount and currentAccount then
                    ownAccountId = currentAccount
                    accountInfo:SetText("Your account ID: " .. tostring(ownAccountId)
                        .. "  |  View: " .. (targetAccount == 0 and "this character" or tostring(targetAccount)))
                end
            end
            local viewNote = viewAccountId == 0 and "current character" or "read-only account view"
            statusText:SetText((tonumber(fields[4]) or 0) .. " commodity row(s) on this page (" .. viewNote .. ").")
        end
    elseif fields[2] == "RCPT" and #fields == 9 then
        local id, result = fields[3], fields[4]
        local data = savedData()
        local pending = data.pending
        if not pending or id ~= pending.id then return end
        if result == "RETRY" then
            retryAt = GetTime() + 5
            statusText:SetText("Outcome is not confirmed. Retry will reuse the same request ID.")
        elseif result == "REQUEST_CONFLICT" then
            statusText:SetText("Request ID conflict. Contact an administrator; no new mutation was sent.")
        else
            data.pending = nil
            if pending.action == "RESERVE" and fields[7] ~= "-" then reservationInput:SetText(fields[7]) end
            statusText:SetText(pending.action .. " → " .. result .. "; balance " .. fields[8]
                .. ", reserved " .. fields[9])
            refresh()
        end
    elseif fields[2] == "ERR" then
        local code = fields[3] or "unknown"
        if code == "RETRY" then
            retryAt = GetTime() + 5
            statusText:SetText("Outcome is not confirmed. Retry will reuse the same request ID.")
        else
            savedData().pending = nil
            statusText:SetText("Server refused the request: " .. code)
        end
    elseif fields[2] == "OK" then
        statusText:SetText("Connected to Portable Hole.")
        retryPending()
    end
end

local function showPage(newPage)
    page = math.max(0, math.min(20, newPage))
    frame.rows = {}
    repaint(frame.rows)
    refresh()
end

local function createFrame()
    frame = CreateFrame("Frame", "PortableHoleFrame", UIParent)
    frame:SetSize(620, 420)
    frame:SetBackdrop({
        bgFile = "Interface/Tooltips/UI-Tooltip-Background",
        edgeFile = "Interface/Tooltips/UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    frame:SetBackdropColor(0.04, 0.05, 0.07, 0.96)
    frame:SetBackdropBorderColor(0.75, 0.62, 0.35, 1)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:Hide()
    frame.rows = {}
    rowButtons = {}
    local titleText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    titleText:SetPoint("TOPLEFT", 18, -10)
    titleText:SetText("Portable Hole - Warehouse")
    accountInfo = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    accountInfo:SetPoint("TOPRIGHT", -18, -14)
    accountInfo:SetText("Account IDs load when the list opens")

    statusText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    statusText:SetPoint("TOPLEFT", 18, -35)
    statusText:SetText("Character-private commodity ledger. No unique item restoration.")

    itemInput = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    itemInput:SetSize(100, 24); itemInput:SetPoint("TOPLEFT", 20, -65)
    itemInput:SetAutoFocus(false); itemInput:SetNumeric(true); itemInput:SetText("0")
    amountInput = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    amountInput:SetSize(100, 24); amountInput:SetPoint("LEFT", itemInput, "RIGHT", 20, 0)
    amountInput:SetAutoFocus(false); amountInput:SetNumeric(true); amountInput:SetText("1")
    local label = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    label:SetPoint("BOTTOMLEFT", itemInput, "TOPLEFT", 0, 2); label:SetText("Item entry")
    local amountLabel = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    amountLabel:SetPoint("BOTTOMLEFT", amountInput, "TOPLEFT", 0, 2); amountLabel:SetText("Amount")

    local function button(text, x, callback, width)
        local b = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        b:SetSize(width or 90, 24); b:SetPoint("LEFT", amountInput, "RIGHT", x, 0)
        b:SetText(text); b:SetScript("OnClick", callback); return b
    end
    button("Refresh", 15, function() showPage(page) end)
    button("Reserve", 112, reserve)
    button("GM Credit", 209, adminCredit)
    button("Bag count", 300, bagCount, 74)

    local pinButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    pinButton:SetSize(92, 24); pinButton:SetPoint("TOPLEFT", 20, -96)
    pinButton:SetText("Pin item"); pinButton:SetScript("OnClick", pinItem)
    local nextPinButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    nextPinButton:SetSize(92, 24); nextPinButton:SetPoint("LEFT", pinButton, "RIGHT", 8, 0)
    nextPinButton:SetText("Next pin"); nextPinButton:SetScript("OnClick", cyclePin)
    local unpinButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    unpinButton:SetSize(70, 24); unpinButton:SetPoint("LEFT", nextPinButton, "RIGHT", 8, 0)
    unpinButton:SetText("Unpin"); unpinButton:SetScript("OnClick", unpinItem)
    local syncPinButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    syncPinButton:SetSize(72, 24); syncPinButton:SetPoint("LEFT", unpinButton, "RIGHT", 6, 0)
    syncPinButton:SetText("Sync pins"); syncPinButton:SetScript("OnClick", syncPins)
    local compareButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    compareButton:SetSize(92, 24); compareButton:SetPoint("LEFT", syncPinButton, "RIGHT", 6, 0)
    compareButton:SetText("Compare need"); compareButton:SetScript("OnClick", compareNeed)
    pinStatus = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    pinStatus:SetPoint("LEFT", compareButton, "RIGHT", 12, 0)
    updatePinStatus(savedData())
    local searchInput = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    searchInput:SetSize(160, 22); searchInput:SetPoint("TOPLEFT", 20, -126)
    searchInput:SetAutoFocus(false); searchInput:SetText("")
    local searchButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    searchButton:SetSize(74, 22); searchButton:SetPoint("LEFT", searchInput, "RIGHT", 8, 0)
    searchButton:SetText("Search")
    searchButton:SetScript("OnClick", function()
        local query = searchInput:GetText()
        if query and query ~= "" then send("SEARCH", query, viewAccountId) end
    end)
    local planButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    planButton:SetSize(112, 22); planButton:SetPoint("LEFT", searchButton, "RIGHT", 8, 0)
    planButton:SetText("Plan deposit")
    planButton:SetScript("OnClick", function()
        local item, amount = uint(itemInput:GetText(), 4294967295), uint(amountInput:GetText(), 1000000000)
        if item and amount then send("PLAN", item, amount) end
    end)
    local sortButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    sortButton:SetSize(90, 22); sortButton:SetPoint("LEFT", planButton, "RIGHT", 8, 0)
    sortButton:SetText("Sort: ENTRY")
    sortButton:SetScript("OnClick", function()
        sortIndex = (sortIndex % #sortModes) + 1
        sortButton:SetText("Sort: " .. sortModes[sortIndex])
        showPage(page)
    end)

    vaultAccountInput = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    vaultAccountInput.role = "vaultAccount"
    vaultAccountInput:SetSize(90, 22); vaultAccountInput:SetPoint("TOPLEFT", 20, -154)
    vaultAccountInput:SetAutoFocus(false); vaultAccountInput:SetNumeric(true); vaultAccountInput:SetText("0")
    local vaultLabel = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    vaultLabel:SetPoint("BOTTOMLEFT", vaultAccountInput, "TOPLEFT", 0, 2)
    vaultLabel:SetText("View account (0 = this character)")
    local viewButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    viewButton:SetSize(78, 22); viewButton:SetPoint("LEFT", vaultAccountInput, "RIGHT", 6, 0)
    viewButton:SetText("Open view"); viewButton:SetScript("OnClick", openVaultView)

    shareAccountInput = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    shareAccountInput.role = "shareAccount"
    shareAccountInput:SetSize(90, 22); shareAccountInput:SetPoint("LEFT", viewButton, "RIGHT", 28, 0)
    shareAccountInput:SetAutoFocus(false); shareAccountInput:SetNumeric(true); shareAccountInput:SetText("")
    local shareLabel = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    shareLabel:SetPoint("BOTTOMLEFT", shareAccountInput, "TOPLEFT", 0, 2)
    shareLabel:SetText("Share with account")
    local grantButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    grantButton:SetSize(68, 22); grantButton:SetPoint("LEFT", shareAccountInput, "RIGHT", 6, 0)
    grantButton:SetText("Grant"); grantButton:SetScript("OnClick", function() changeShare(true) end)
    local revokeButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    revokeButton:SetSize(68, 22); revokeButton:SetPoint("LEFT", grantButton, "RIGHT", 4, 0)
    revokeButton:SetText("Revoke"); revokeButton:SetScript("OnClick", function() changeShare(false) end)

    for i = 1, 5 do
        local b = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        b:SetSize(570, 26); b:SetPoint("TOPLEFT", 20, -190 - (i - 1) * 31)
        b:SetScript("OnClick", function() if b.itemEntry then itemInput:SetText(tostring(b.itemEntry)) end end)
        b:Hide(); rowButtons[i] = b
    end
    pageLabel = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    pageLabel:SetPoint("BOTTOM", frame, "BOTTOM", 0, 40); pageLabel:SetText("Page 1")
    local prev = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    prev:SetSize(70, 24); prev:SetPoint("BOTTOMLEFT", 20, 12); prev:SetText("Previous")
    prev:SetScript("OnClick", function() showPage(page - 1) end)
    local nextButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    nextButton:SetSize(70, 24); nextButton:SetPoint("BOTTOMRIGHT", -20, 12); nextButton:SetText("Next")
    nextButton:SetScript("OnClick", function() showPage(page + 1) end)

    reservationInput = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    reservationInput:SetSize(145, 24); reservationInput:SetPoint("BOTTOM", frame, "BOTTOM", 0, 12)
    reservationInput:SetAutoFocus(false)
    local release = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    release:SetSize(86, 24); release:SetPoint("RIGHT", reservationInput, "LEFT", -8, 0)
    release:SetText("Release"); release:SetScript("OnClick", function() transition("RELEASE") end)
    local retry = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    retry:SetSize(70, 24); retry:SetPoint("RIGHT", release, "LEFT", -6, 0)
    retry:SetText("Retry"); retry:SetScript("OnClick", retryPending)
    local consume = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    consume:SetSize(86, 24); consume:SetPoint("LEFT", reservationInput, "RIGHT", 8, 0)
    consume:SetText("Consume"); consume:SetScript("OnClick", function() transition("CONSUME") end)
end

local events = CreateFrame("Frame")
local retryElapsed = 0
events:SetScript("OnUpdate", function(_, elapsed)
    retryElapsed = retryElapsed + elapsed
    if retryElapsed >= 1 then
        retryElapsed = 0
        local pending = savedData().pending
        if frame and frame:IsShown() and pending and GetTime() >= retryAt then
            retryPending()
        elseif frame and frame:IsShown() and #pendingPinAdds > 0 and GetTime() - lastSend >= MIN_INTERVAL then
            local item = table.remove(pendingPinAdds, 1)
            send("PIN", item, "ADD")
        end
    end
end)
events:RegisterEvent("CHAT_MSG_ADDON")
events:RegisterEvent("PLAYER_LOGIN")
events:SetScript("OnEvent", function(self, event, ...)
    if event == "PLAYER_LOGIN" then
        createFrame()
        SLASH_PORTABLEHOLE1 = "/portablehole"
        SLASH_PORTABLEHOLE2 = "/ph"
        SlashCmdList.PORTABLEHOLE = function()
            frame:Show()
            showPage(0)
        end
    else
        onMessage(self, ...)
    end
end)
