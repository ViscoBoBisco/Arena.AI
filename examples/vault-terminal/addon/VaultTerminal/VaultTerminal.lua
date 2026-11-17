--[[ VaultTerminal — a live panel fed by the server, no client patch.

3.3.5a (build 12340) API only: global functions, OnUpdate timers, no C_* namespaces, no
prefix registration (that arrived in a later expansion). Protocol: see ../../PROTOCOL.md.

Design rule: this file owns pixels, the server owns truth. Nothing here computes a game value;
it renders what the server sent, and says so plainly when the server has not answered.
]]

local PREFIX          = "VT1"
local PROTOCOL_VERSION = "1"
local ADDON_VERSION   = "demo-1.0.0"
local POLL_INTERVAL   = 5.0      -- seconds between voluntary STATE polls
local RESPONSE_TIMEOUT = 10.0    -- after this, the panel admits it has heard nothing

local frame, fields
local lastReply = 0
local pollElapsed = 0
local requested = false

local function now()
    return GetTime()
end

-- ------------------------------------------------------------------ transport
local function send(opcode, ...)
    local parts = { PREFIX, opcode }
    for i = 1, select("#", ...) do
        parts[#parts + 1] = tostring((select(i, ...)))
    end
    local payload = table.concat(parts, "\t")
    if #payload > 255 then
        DEFAULT_CHAT_FRAME:AddMessage("|cffff5555VaultTerminal: refusing to send " ..
            #payload .. " bytes (limit 255)|r")
        return false
    end
    SendAddonMessage(PREFIX, payload, "WHISPER", UnitName("player"))
    return true
end

-- ------------------------------------------------------------------- rendering
local function setField(key, value)
    if fields and fields[key] then
        fields[key]:SetText(value)
    end
end

local function renderStale()
    setField("seals", "—")
    setField("rank", "—")
    setField("claim", "no server response")
    if fields and fields.claimButton then
        fields.claimButton:Disable()
    end
end

local function render(state)
    setField("seals", state.seals .. " / " .. state.target)
    setField("rank", state.rank)
    if state.flag == "1" then
        setField("claim", "a seal is available (" .. state.claimsLeft .. " left today)")
        fields.claimButton:Enable()
    else
        setField("claim", "nothing to claim (" .. state.claimsLeft .. " left today)")
        fields.claimButton:Disable()
    end
end

-- --------------------------------------------------------------------- handlers
local function onState(f1, f2, f3, f4, f5)
    local state = {
        seals = tonumber(f1) or 0,
        target = tonumber(f2) or 0,
        claimsLeft = tonumber(f3) or 0,
        rank = f4 or "—",
        flag = f5 or "0",
    }
    lastReply = now()
    render(state)
end

local function onOk(text)
    lastReply = now()
    setField("status", text ~= "" and text or "ok")
    -- ask for a fresh snapshot rather than guessing what changed
    send("STATE")
end

local function onErr(code, text)
    lastReply = now()
    setField("status", "|cffff5555" .. (code or "ERR") .. "|r " .. (text or ""))
end

local function onBye(text)
    lastReply = now()
    setField("status", "server closed the session: " .. (text or ""))
    renderStale()
end

local function dispatch(prefix, message)
    if prefix ~= PREFIX or type(message) ~= "string" then return end
    local opcode, rest = message:match("^VT1\t([^\t]+)\t?(.*)$")
    if not opcode then return end

    if opcode == "S" then
        local a, b, c, d, e = rest:match("^([^\t]*)\t([^\t]*)\t([^\t]*)\t([^\t]*)\t?([^\t]*)$")
        if a then onState(a, b, c, d, e)
        else setField("status", "|cffff5555malformed S|r") end
    elseif opcode == "OK" then
        onOk(rest)
    elseif opcode == "ERR" then
        local code, text = rest:match("^([^\t]*)\t?(.*)$")
        onErr(code, text)
    elseif opcode == "BYE" then
        onBye(rest)
    end
    -- unknown opcodes are ignored on purpose (forward compatibility, PROTOCOL.md rule 2)
end

-- ------------------------------------------------------------------------- UI
local function build()
    frame = CreateFrame("Frame", "VaultTerminalFrame", UIParent)
    frame:SetWidth(240)
    frame:SetHeight(140)
    frame:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
    frame:SetBackdrop({
        bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
        edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Border",
        tile = true, tileSize = 32, edgeSize = 32,
        insets = { left = 8, right = 8, top = 8, bottom = 8 },
    })
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:Hide()

    local title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    title:SetPoint("TOP", frame, "TOP", 0, -12)
    title:SetText("Vault Terminal")

    fields = {}

    local function line(label, y, key)
        local left = frame:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
        left:SetPoint("TOPLEFT", frame, "TOPLEFT", 16, y)
        left:SetText(label)

        local value = frame:CreateFontString(nil, "ARTWORK", "GameFontHighlightSmall")
        value:SetPoint("TOPLEFT", frame, "TOPLEFT", 96, y)
        value:SetWidth(130)
        value:SetJustifyH("LEFT")
        value:SetText("—")
        fields[key] = value
        return value
    end

    line("Seals", -36, "seals")
    line("Rank", -54, "rank")
    line("Claim", -72, "claim")

    local status = frame:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
    status:SetPoint("TOPLEFT", frame, "TOPLEFT", 16, -96)
    status:SetWidth(208)
    status:SetJustifyH("LEFT")
    status:SetText("")
    fields.status = status

    local button = CreateFrame("Button", "VaultTerminalClaim", frame, "UIPanelButtonTemplate")
    button:SetWidth(110)
    button:SetHeight(22)
    button:SetPoint("BOTTOM", frame, "BOTTOM", 0, 12)
    button:SetText("Claim seal")
    button:Disable()
    button:SetScript("OnClick", function()
        setField("status", "requesting…")
        send("CLAIM")
    end)
    fields.claimButton = button

    frame:RegisterEvent("CHAT_MSG_ADDON")
    frame:SetScript("OnEvent", function(_, event, prefix, message)
        if event == "CHAT_MSG_ADDON" then
            dispatch(prefix, message)
        end
    end)

    frame:SetScript("OnUpdate", function(_, elapsed)
        pollElapsed = pollElapsed + elapsed
        if pollElapsed >= POLL_INTERVAL then
            pollElapsed = 0
            send("STATE")
        end
        if requested and (now() - lastReply) > RESPONSE_TIMEOUT and not frame.staleShown then
            frame.staleShown = true
            renderStale()
        end
    end)
end

-- ------------------------------------------------------------------------ start
SLASH_VAULTTERMINAL1 = "/vt"
SLASH_VAULTTERMINAL2 = "/vaultterminal"
SlashCmdList["VAULTTERMINAL"] = function()
    if not VaultTerminalDB then VaultTerminalDB = {} end
    if frame:IsShown() then
        frame:Hide()
    else
        frame:Show()
        requested = true
        frame.staleShown = false
        send("HELLO", PROTOCOL_VERSION, ADDON_VERSION)
    end
end

local boot = CreateFrame("Frame")
boot:RegisterEvent("PLAYER_LOGIN")
boot:SetScript("OnEvent", function()
    build()
    if VaultTerminalDB.showOnLogin then
        SlashCmdList["VAULTTERMINAL"]()
    end
end)
