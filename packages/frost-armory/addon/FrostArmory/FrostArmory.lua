-- Frost Armory (3.3.5a). Presentation + player intent only.
-- Protocol prefix: FA1. The server revalidates everything.
local frame = CreateFrame("Frame", "FA1Frame", UIParent)
frame:SetSize(220, 120)
frame:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
frame:SetBackdropBorderColor(0.6, 0.8, 1.0, 1.0)
frame:RegisterEvent("CHAT_MSG_ADDON")
frame:SetScript("OnEvent", function(self, event, prefix, message, dist, sender)
    if prefix ~= "FA1" then return end
    -- TODO: render server state; send intents, never decisions.
end)
