local PREFIX = "RC1"
local MAX_BYTES = 180
local frame, statusText, loadoutInput
local powers = {
    arcane_echo = { name = "Arcane Echo", unlocked = false, equipped = false },
    aegis_echo = { name = "Aegis Echo", unlocked = false, equipped = false },
}
local powerOrder = { "arcane_echo", "aegis_echo" }
local powerRows = {}

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
        statusText:SetText("Request sent; discovery, slots and saved loadouts are server-authoritative.")
    end
end

local function paint()
    for _, key in ipairs(powerOrder) do
        local state, row = powers[key], powerRows[key]
        if row then
            local summary
            if not state.unlocked then summary = "locked"
            elseif state.equipped then summary = "equipped in slot " .. tostring((state.slot or 0) + 1)
            else summary = "unlocked, unequipped" end
            row.label:SetText(state.name .. ": " .. summary)
            if state.unlocked then
                row.slot1:Enable(); row.slot2:Enable(); row.remove:Enable()
            else
                row.slot1:Disable(); row.slot2:Disable(); row.remove:Disable()
            end
        end
    end
end

local function createFrame()
    frame = CreateFrame("Frame", "RunicCodexFrame", UIParent)
    frame:SetSize(490, 300)
    frame:SetBackdrop({
        bgFile = "Interface/Tooltips/UI-Tooltip-Background",
        edgeFile = "Interface/Tooltips/UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    frame:SetBackdropColor(0.04, 0.05, 0.07, 0.96)
    frame:SetBackdropBorderColor(0.45, 0.55, 0.8, 1)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:Hide()

    local title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    title:SetPoint("TOPLEFT", 20, -12); title:SetText("Runic Codex")
    statusText = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    statusText:SetPoint("TOPLEFT", 20, -42); statusText:SetWidth(450); statusText:SetJustifyH("LEFT")

    for index, key in ipairs(powerOrder) do
        local powerKey = key
        local y = -84 - ((index - 1) * 52)
        local row = { label = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal") }
        row.label:SetPoint("TOPLEFT", 20, y); row.label:SetWidth(190); row.label:SetJustifyH("LEFT")
        row.slot1 = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        row.slot1:SetSize(62, 22); row.slot1:SetPoint("TOPLEFT", 230, y + 3); row.slot1:SetText("Slot 1")
        row.slot1:SetScript("OnClick", function() send("EQUIP", powerKey .. "\t0") end)
        row.slot2 = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        row.slot2:SetSize(62, 22); row.slot2:SetPoint("LEFT", row.slot1, "RIGHT", 4, 0)
        row.slot2:SetText("Slot 2")
        row.slot2:SetScript("OnClick", function() send("EQUIP", powerKey .. "\t1") end)
        row.remove = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        row.remove:SetSize(74, 22); row.remove:SetPoint("LEFT", row.slot2, "RIGHT", 4, 0)
        row.remove:SetText("Unequip")
        row.remove:SetScript("OnClick", function() send("UNEQUIP", powerKey) end)
        powerRows[key] = row
    end

    loadoutInput = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    loadoutInput.role = "loadoutName"
    loadoutInput:SetSize(110, 24); loadoutInput:SetPoint("BOTTOMLEFT", 20, 24)
    loadoutInput:SetAutoFocus(false); loadoutInput:SetText("")
    local save = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    save:SetSize(90, 24); save:SetPoint("LEFT", loadoutInput, "RIGHT", 8, 0); save:SetText("Save set")
    save:SetScript("OnClick", function() send("LOADOUT", "SAVE\t" .. loadoutInput:GetText()) end)
    local apply = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    apply:SetSize(90, 24); apply:SetPoint("LEFT", save, "RIGHT", 5, 0); apply:SetText("Apply set")
    apply:SetScript("OnClick", function() send("LOADOUT", "APPLY\t" .. loadoutInput:GetText()) end)
    paint()
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("CHAT_MSG_ADDON")
events:SetScript("OnEvent", function(_, event, prefix, message, distribution, sender)
    if event == "PLAYER_LOGIN" then
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
    if fields[1] == PREFIX and fields[2] == "POWER" and #fields == 7
        and powers[fields[3]] then
        local state = powers[fields[3]]
        state.name = fields[4]
        state.unlocked = fields[5] == "1"
        state.equipped = fields[6] == "1"
        state.slot = tonumber(fields[7])
        paint()
    elseif fields[1] == PREFIX and fields[2] == "END" then
        statusText:SetText("Two independent, source-verified item discoveries; two power slots; loadouts are character-bound.")
    elseif fields[1] == PREFIX and fields[2] == "LOADOUT" then
        statusText:SetText("Saved loadout " .. (fields[4] or "") .. ".")
    elseif fields[1] == PREFIX and fields[2] == "ERR" then
        statusText:SetText("Server refused: " .. (fields[3] or "unknown"))
    end
end)
