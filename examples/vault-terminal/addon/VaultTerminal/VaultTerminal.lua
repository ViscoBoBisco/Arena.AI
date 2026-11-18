-- Vault Terminal: generated from arena-counter-terminal/2.
-- WotLK 3.3.5a Interface 30300. This addon renders server state only.
local PREFIX = "VT1"
local VERSION = "2"
local MAX_BYTES = 255
local MIN_REQUEST_INTERVAL = 0.250
local RESPONSE_TIMEOUT = 10
local ADDON_VERSION = "1.0.0"
local VALID_RANKS = {['Initiate'] = true, ['Adept'] = true, ['Keeper'] = true, ['Vaultmaster'] = true}

local frame
local fields = {}
local pending = false
local pendingOpcode = nil
local retryQueued = false
local stopped = false
local requestAt = 0
local lastSend = -MIN_REQUEST_INTERVAL
local lastState = nil

local function characterData()
    if type(VaultTerminalDB) ~= "table" then VaultTerminalDB = {} end
    if type(VaultTerminalDB.characters) ~= "table" then VaultTerminalDB.characters = {} end
    local key = UnitGUID("player") or UnitName("player") or "unknown"
    if type(VaultTerminalDB.characters[key]) ~= "table" then
        VaultTerminalDB.characters[key] = { requestSequence = 0 }
    end
    return VaultTerminalDB.characters[key]
end

local function currentPendingRequestId()
    local data = characterData()
    if type(data.pendingRequestId) ~= "string" then return nil end
    return data.pendingRequestId
end

local function newRequestId()
    local data = characterData()
    data.requestSequence = (tonumber(data.requestSequence) or 0) + 1
    data.pendingRequestId = tostring(time()) .. "-" .. tostring(data.requestSequence)
        .. "-" .. tostring(math.floor(GetTime() * 1000))
    return data.pendingRequestId
end

local function clearPendingRequestId()
    characterData().pendingRequestId = nil
end

local function setStatus(text)
    if fields.status then fields.status:SetText(text or "") end
end

local function parseUInt(value)
    if type(value) ~= "string" or not value:match("^%d+$") then return nil end
    local number = tonumber(value)
    if not number or number < 0 or number > 2147483647 then return nil end
    return number
end

local function splitTabs(value)
    local result = {}
    local start = 1
    while true do
        local at = value:find("\t", start, true)
        if not at then
            result[#result + 1] = value:sub(start)
            break
        end
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

local function send(opcode, ...)
    if not frame or not frame:IsShown() or (stopped and opcode ~= "HELLO") then return false end
    local now = GetTime()
    if now - lastSend < MIN_REQUEST_INTERVAL then
        setStatus("Please wait before sending another request.")
        return false
    end
    local parts = { PREFIX, opcode }
    for index = 1, select("#", ...) do
        parts[#parts + 1] = tostring(select(index, ...))
    end
    local payload = table.concat(parts, "\t")
    if not validFrame(payload) then
        setStatus("Request refused: invalid or oversized frame.")
        return false
    end
    lastSend = now
    requestAt = now
    pending = true
    pendingOpcode = opcode
    SendAddonMessage(PREFIX, payload, "WHISPER", UnitName("player"))
    return true
end

local function setStale(message)
    fields.seals:SetText("—")
    fields.rank:SetText("—")
    fields.claim:SetText("No current server state.")
    if currentPendingRequestId() then
        fields.claimButton:SetText("Retry Claim")
        fields.claimButton:Enable()
    else
        fields.claimButton:Disable()
    end
    setStatus(message or "No server response. Use Refresh to retry.")
end

local function renderState(values)
    lastState = values
    fields.seals:SetText(tostring(values.seals) .. " / " .. tostring(values.target))
    fields.rank:SetText(values.rank)
    if currentPendingRequestId() then
        fields.claim:SetText("A claim request is awaiting confirmation.")
        fields.claimButton:SetText("Retry Claim")
        fields.claimButton:Enable()
    elseif values.flag == "1" and values.claimsLeft > 0 then
        fields.claim:SetText("A claim is available today.")
        fields.claimButton:SetText("Claim")
        fields.claimButton:Enable()
    else
        fields.claim:SetText("No claim available today.")
        fields.claimButton:SetText("Claim")
        fields.claimButton:Disable()
    end
    setStatus("Connected to server.")
end

local function dispatch(prefix, message, distribution, sender)
    if prefix ~= PREFIX or distribution ~= "WHISPER" or sender ~= UnitName("player")
        or not validFrame(message) then return end
    local parts = splitTabs(message)
    if parts[1] ~= PREFIX or not parts[2] then return end
    local opcode = parts[2]

    if opcode == "S" then
        if #parts ~= 7 then setStale("Malformed state response."); return end
        local seals = parseUInt(parts[3])
        local target = parseUInt(parts[4])
        local claimsLeft = parseUInt(parts[5])
        local rank = parts[6]
        local flag = parts[7]
        if not seals or target ~= 50 or claimsLeft == nil or claimsLeft > 1
            or not VALID_RANKS[rank] or (flag ~= "0" and flag ~= "1") then
            setStale("Invalid state response.")
            return
        end
        pending = false
        local previousOpcode = pendingOpcode
        pendingOpcode = nil
        renderState({seals = seals, target = target, claimsLeft = claimsLeft, rank = rank, flag = flag})
        if previousOpcode == "HELLO" and currentPendingRequestId() then
            retryQueued = true
        end
    elseif opcode == "RCPT" then
        if #parts ~= 5 or parts[3] ~= currentPendingRequestId() then return end
        local totalClaims = parseUInt(parts[5])
        local status = parts[4]
        if totalClaims == nil or (status ~= "GRANTED" and status ~= "DENIED_COOLDOWN"
            and status ~= "DENIED_TARGET" and status ~= "DENIED_INTERNAL" and status ~= "RETRY") then
            setStatus("Malformed claim receipt.")
            return
        end
        pending = false
        pendingOpcode = nil
        retryQueued = false
        if status == "RETRY" then
            fields.claimButton:SetText("Retry Claim")
            fields.claimButton:Enable()
            setStatus("Claim outcome is not confirmed. Retry safely.")
            return
        end
        clearPendingRequestId()
        fields.claimButton:Disable()
        if status == "GRANTED" then
            setStatus("Claim confirmed by server.")
        elseif status == "DENIED_COOLDOWN" then
            setStatus("COOLDOWN: already claimed today.")
        elseif status == "DENIED_TARGET" then
            setStatus("Target already reached.")
        else
            setStatus("Claim was refused; refresh server state.")
        end
    elseif opcode == "OK" then
        if #parts > 3 then setStatus("Malformed success response."); return end
        pending = false
        pendingOpcode = nil
        setStatus(parts[3] or "Request accepted.")
    elseif opcode == "ERR" then
        if #parts ~= 4 then setStatus("Malformed error response."); return end
        if pendingOpcode ~= "CLAIM" then
            pending = false
            pendingOpcode = nil
        else
            pending = false
            pendingOpcode = nil
            fields.claimButton:SetText("Retry Claim")
            fields.claimButton:Enable()
        end
        setStatus(parts[3] .. ": " .. parts[4])
    elseif opcode == "BYE" then
        pending = false
        pendingOpcode = nil
        stopped = true
        setStale(parts[3] or "Server disabled this feature.")
    end
    -- Future server opcodes are ignored. Never infer a reward or state locally.
end

local function addLine(label, key, y)
    local left = frame:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
    left:SetPoint("TOPLEFT", frame, "TOPLEFT", 16, y)
    left:SetText(label)
    local value = frame:CreateFontString(nil, "ARTWORK", "GameFontHighlightSmall")
    value:SetPoint("TOPLEFT", frame, "TOPLEFT", 96, y)
    value:SetWidth(170)
    value:SetJustifyH("LEFT")
    value:SetText("—")
    fields[key] = value
end

local function build()
    frame = CreateFrame("Frame", "VaultTerminalFrame", UIParent)
    frame:SetWidth(290)
    frame:SetHeight(190)
    frame:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
    frame:SetBackdrop({bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
        edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Border", tile = true,
        tileSize = 32, edgeSize = 32, insets = {left = 8, right = 8, top = 8, bottom = 8}})
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", function(self) self:StartMoving() end)
    frame:SetScript("OnDragStop", function(self) self:StopMovingOrSizing() end)
    frame:Hide()

    local titleText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    titleText:SetPoint("TOP", frame, "TOP", 0, -12)
    titleText:SetText("Vault Terminal")
    addLine("Claims", "seals", -38)
    addLine("Rank", "rank", -58)
    addLine("Today", "claim", -78)
    local status = frame:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
    status:SetPoint("TOPLEFT", frame, "TOPLEFT", 16, -105)
    status:SetWidth(250)
    status:SetJustifyH("LEFT")
    status:SetText("")
    fields.status = status

    local claim = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    claim:SetWidth(115); claim:SetHeight(22)
    claim:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 18, 14)
    claim:SetText("Claim")
    claim:Disable()
    claim:SetScript("OnClick", function()
        local requestId = currentPendingRequestId() or newRequestId()
        claim:Disable()
        setStatus("Sending claim request…")
        if not send("CLAIM", requestId) then
            claim:SetText("Retry Claim")
            claim:Enable()
        end
    end)
    fields.claimButton = claim

    local refresh = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    refresh:SetWidth(90); refresh:SetHeight(22)
    refresh:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -18, 14)
    refresh:SetText("Refresh")
    refresh:SetScript("OnClick", function()
        stopped = false
        send("HELLO", VERSION, ADDON_VERSION)
    end)

    -- Required addon-prefix registration was added after WotLK; 3.3.5a
    -- routes messages directly through SendAddonMessage/CHAT_MSG_ADDON.
    frame:RegisterEvent("CHAT_MSG_ADDON")
    frame:SetScript("OnEvent", function(_, event, responsePrefix, message, distribution, sender)
        if event == "CHAT_MSG_ADDON" then
            dispatch(responsePrefix, message, distribution, sender)
        end
    end)
    frame:SetScript("OnUpdate", function()
        if pending and GetTime() - requestAt >= RESPONSE_TIMEOUT then
            pending = false
            pendingOpcode = nil
            setStale("No server response. Retry uses the same request ID.")
        elseif retryQueued and not pending and GetTime() - lastSend >= MIN_REQUEST_INTERVAL then
            retryQueued = false
            local requestId = currentPendingRequestId()
            if requestId then send("CLAIM", requestId) end
        end
    end)
end

SLASH_VAULTTERMINAL1 = "/vt"
SLASH_VAULTTERMINAL2 = "/vaultterminal"
SlashCmdList["VAULTTERMINAL"] = function()
    if not frame then build() end
    if frame:IsShown() then
        frame:Hide()
        pending = false
    else
        frame:Show()
        stopped = false
        send("HELLO", VERSION, ADDON_VERSION)
    end
end
