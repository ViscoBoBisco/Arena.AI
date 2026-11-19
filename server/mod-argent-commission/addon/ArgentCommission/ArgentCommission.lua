local PREFIX = "AC1"
local MAX_BYTES = 160
local frame, statusText, claimButton
local earned, honored = false, false

local function send(action)
    if frame and frame:IsShown() then
        SendAddonMessage(PREFIX, PREFIX .. "\t" .. action, "WHISPER", UnitName("player"))
        statusText:SetText("Request sent; the server checks native reputation.")
    end
end

local function paint()
    if earned then
        statusText:SetText("Argent Commission earned. The core Argent Champion effect is active.")
        claimButton:SetText("Claimed")
        claimButton:Disable()
    elseif honored then
        statusText:SetText("Honored with the Argent Crusade. Claim your commission.")
        claimButton:SetText("Claim commission")
        claimButton:Enable()
    else
        statusText:SetText("Requires Honored standing with the Argent Crusade.")
        claimButton:SetText("Locked")
        claimButton:Disable()
    end
end

local function createFrame()
    frame = CreateFrame("Frame", "ArgentCommissionFrame", UIParent, "BasicFrameTemplateWithInset")
    frame:SetSize(420, 150)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:Hide()
    frame.TitleText:SetText("Argent Commission")
    statusText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    statusText:SetPoint("TOPLEFT", 20, -50)
    statusText:SetWidth(380)
    statusText:SetJustifyH("LEFT")
    claimButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    claimButton:SetSize(150, 26)
    claimButton:SetPoint("BOTTOM", frame, "BOTTOM", 0, 20)
    claimButton:SetScript("OnClick", function() if honored and not earned then send("CLAIM") end end)
    paint()
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("CHAT_MSG_ADDON")
events:SetScript("OnEvent", function(_, event, prefix, message, distribution, sender)
    if event == "PLAYER_LOGIN" then
        RegisterAddonMessagePrefix(PREFIX)
        createFrame()
        SLASH_ARGENTCOMMISSION1 = "/argentcommission"
        SlashCmdList.ARGENTCOMMISSION = function()
            frame:Show()
            send("STATUS")
        end
        return
    end
    if prefix ~= PREFIX or distribution ~= "WHISPER" or sender ~= UnitName("player")
        or type(message) ~= "string" or #message > MAX_BYTES then return end
    local fields = {}
    for field in (message .. "\t"):gmatch("(.-)\t") do fields[#fields + 1] = field end
    if fields[1] == PREFIX and fields[2] == "STATUS" and #fields == 4 then
        earned, honored = fields[3] == "1", fields[4] == "1"
        paint()
    elseif fields[1] == PREFIX and fields[2] == "CLAIMED" then
        earned = true
        paint()
    elseif fields[1] == PREFIX and fields[2] == "ERR" then
        statusText:SetText("Server refused: " .. (fields[3] or "unknown"))
    end
end)
