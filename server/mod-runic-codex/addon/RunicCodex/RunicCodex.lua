local PREFIX = "RC1"
local MAX_BYTES = 180
local frame, statusText, equipButton
local unlocked, equipped = false, false

local function validFrame(value)
    if type(value) ~= "string" or #value > MAX_BYTES then return false end
    for i = 1, #value do
        local byte = string.byte(value, i)
        if byte ~= 9 and (byte < 32 or byte > 126) then return false end
    end
    return true
end

local function send(action, value)
    if not frame or not frame:IsShown() then return end
    local payload = PREFIX .. "\t" .. action .. (value and ("\t" .. value) or "")
    if validFrame(payload) then
        SendAddonMessage(PREFIX, payload, "WHISPER", UnitName("player"))
        statusText:SetText("Request sent; the server decides discovery and equip state.")
    end
end

local function paint()
    if unlocked then
        statusText:SetText("Arcane Echo: " .. (equipped and "equipped" or "unlocked")
            .. ". A paid Arcane Missiles cast returns floor(10% mana cost, max 100).")
        equipButton:SetText(equipped and "Unequip" or "Equip")
        equipButton:Enable()
    else
        statusText:SetText("Discover Arcane Echo by learning Arcane Missiles.")
        equipButton:SetText("Locked")
        equipButton:Disable()
    end
end

local function createFrame()
    frame = CreateFrame("Frame", "RunicCodexFrame", UIParent, "BasicFrameTemplateWithInset")
    frame:SetSize(430, 170)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:Hide()
    frame.TitleText:SetText("Runic Codex")
    statusText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    statusText:SetPoint("TOPLEFT", 20, -48)
    statusText:SetWidth(380)
    statusText:SetJustifyH("LEFT")
    equipButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    equipButton:SetSize(100, 26)
    equipButton:SetPoint("BOTTOM", frame, "BOTTOM", 0, 22)
    equipButton:SetScript("OnClick", function()
        if unlocked then send("EQUIP", equipped and "0" or "1") end
    end)
    paint()
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("CHAT_MSG_ADDON")
events:SetScript("OnEvent", function(_, event, prefix, message, distribution, sender)
    if event == "PLAYER_LOGIN" then
        RegisterAddonMessagePrefix(PREFIX)
        createFrame()
        SLASH_RUNICCODEX1 = "/runiccodex"
        SlashCmdList.RUNICCODEX = function()
            frame:Show()
            send("STATE")
        end
        return
    end
    if prefix ~= PREFIX or distribution ~= "WHISPER" or sender ~= UnitName("player")
        or not validFrame(message) then return end
    local fields = {}
    for field in (message .. "\t"):gmatch("(.-)\t") do fields[#fields + 1] = field end
    if fields[1] == PREFIX and fields[2] == "STATE" and #fields >= 4 then
        unlocked = fields[3] == "1"
        equipped = fields[4] == "1"
        paint()
    elseif fields[1] == PREFIX and fields[2] == "ERR" then
        statusText:SetText("Server refused: " .. (fields[3] or "unknown"))
    end
end)
