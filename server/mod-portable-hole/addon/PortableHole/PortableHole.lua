local PREFIX = "PH1"
local MAX_BYTES = 255
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
    return PortableHoleDB.characters[key]
end
local frame, statusText, itemInput, amountInput, reservationInput, rowButtons, pageLabel
local page = 0

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
    local values = { strsplit("\t", pending.arguments) }
    values[#values + 1] = pending.id
    retryAt = GetTime() + 5
    return send(action, unpack(values))
end

local function retryPending()
    local pending = savedData().pending
    if not pending then return end
    local arguments = { strsplit("\t", pending.arguments or "") }
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
    send("LIST", page)
end

local function reserve()
    local item = uint(itemInput:GetText(), 4294967295)
    local amount = uint(amountInput:GetText(), 1000000000)
    if not item or not amount then statusText:SetText("Enter a positive item entry and amount."); return end
    sendMutation("RESERVE", { tostring(item), tostring(amount) })
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
    if fields[2] == "ROW" and #fields == 7 then
        local row = { page = tonumber(fields[3]), item = tonumber(fields[4]),
                      balance = tonumber(fields[5]), reserved = tonumber(fields[6]), name = fields[7] }
        if row.page == page and row.item and row.balance and row.reserved then
            local rows = frame.rows or {}
            rows[#rows + 1] = row
            frame.rows = rows
            repaint(rows)
        end
    elseif fields[2] == "END" and #fields == 4 then
        if tonumber(fields[3]) == page then
            pageLabel:SetText("Page " .. tostring(page + 1))
            statusText:SetText((tonumber(fields[4]) or 0) .. " commodity row(s) on this page.")
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
    frame = CreateFrame("Frame", "PortableHoleFrame", UIParent, "BasicFrameTemplateWithInset")
    frame:SetSize(620, 390)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:Hide()
    frame.rows = {}
    rowButtons = {}
    frame.TitleText:SetText("Portable Hole — Warehouse")

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

    local function button(text, x, callback)
        local b = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        b:SetSize(90, 24); b:SetPoint("LEFT", amountInput, "RIGHT", x, 0)
        b:SetText(text); b:SetScript("OnClick", callback); return b
    end
    button("Refresh", 15, function() showPage(page) end)
    button("Reserve", 112, reserve)
    button("GM Credit", 209, adminCredit)

    for i = 1, 5 do
        local b = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        b:SetSize(570, 26); b:SetPoint("TOPLEFT", 20, -112 - (i - 1) * 31)
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
        end
    end
end)
events:RegisterEvent("CHAT_MSG_ADDON")
events:RegisterEvent("PLAYER_LOGIN")
events:SetScript("OnEvent", function(self, event, ...)
    if event == "PLAYER_LOGIN" then
        RegisterAddonMessagePrefix(PREFIX)
        createFrame()
        SLASH_PORTABLEHOLE1 = "/portablehole"
        SLASH_PORTABLEHOLE2 = "/ph"
        SlashCmdList.PORTABLEHOLE = function()
            frame:Show()
            send("HELLO", 1)
            showPage(0)
        end
    else
        onMessage(self, ...)
    end
end)
