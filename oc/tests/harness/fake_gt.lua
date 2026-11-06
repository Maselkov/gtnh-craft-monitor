--[[
  A fake GregTech machine (gt_machine component) for oc_env, shaped like
  a Lapotronic Supercapacitor as power_monitor.lua reads one.

  machine.stored / machine.capacity feed getEUStored()/getEUMaxStored();
  machine.sensor is getSensorInformation()'s lines (built from those and
  machine.trend and machine.wireless_eu unless set directly; wireless_eu
  is a string of digits, as it can be past what a Lua number holds
  exactly; with it come machine.wireless_mode (true for enabled) and
  machine.capacitors, counts by tier: { UEV = 1 }). machine:fail(method, message)
  makes a method raise; machine.methods_missing lists ones to leave out.
--]]

local M = {}

local Machine = {}
Machine.__index = Machine

local function sensor_lines(m)
  local prefix = "kekztech.infodata.lapotronic_super_capacitor."
  local t = m.trend or {}
  local lines = {
    "Operational Data:",
    "EU Stored: " .. string.format("%.0f", m.stored) .. " EU",
    "EU Capacity: " .. string.format("%.0f", m.capacity) .. " EU",
  }
  local function add(key, value)
    if value ~= nil then lines[#lines + 1] = prefix .. key .. "\\" .. tostring(value) end
  end
  add("avg_eu_in.sec", t.avg_eu_in_5s and (t.avg_eu_in_5s .. "\\5"))
  add("avg_eu_out.sec", t.avg_eu_out_5s and (t.avg_eu_out_5s .. "\\5"))
  add("avg_eu_in.min5", t.avg_eu_in_5m)
  add("avg_eu_out.min5", t.avg_eu_out_5m)
  add("avg_eu_in.hour1", t.avg_eu_in_1h)
  add("avg_eu_out.hour1", t.avg_eu_out_1h)
  add("time_to.empty", t.time_to_empty_minutes and (t.time_to_empty_minutes .. " minutes"))
  if m.wireless_eu then
    -- As the real LSC words it: a colour code ("§c" in UTF-8) and
    -- grouping, then the same key again in standard form.
    local grouped = m.wireless_eu:reverse():gsub("(%d%d%d)", "%1,"):reverse():gsub("^,", "")
    -- As a real LSC words them (a 2.9.0 dump): the mode in the key, and
    -- one line per wireless capacitor tier.
    lines[#lines + 1] = prefix .. "wireless_mode." .. (m.wireless_mode and "enabled" or "disabled")
    local codes = { UHV = "4", UEV = "5", UIV = "1\194\167l", UMV = "c\194\167l" }
    for _, tier in ipairs({ "UHV", "UEV", "UIV", "UMV" }) do
      add("capacitors", "\194\167" .. codes[tier] .. "\194\167n" .. tier .. "\194\167r\\" .. ((m.capacitors or {})[tier] or 0))
    end
    add("wireless_eu", "\194\167c" .. grouped)
    add("wireless_eu", "\194\167c" .. string.format("%.2fE%d", tonumber(m.wireless_eu:sub(1, 3)) / 100, #m.wireless_eu - 1))
  end
  return lines
end

function M.new(env, opts)
  opts = opts or {}
  local m = setmetatable({
    stored = opts.stored or 5e9,
    capacity = opts.capacity or 1e10,
    trend = opts.trend,
    wireless_eu = opts.wireless_eu,
    wireless_mode = opts.wireless_mode,
    capacitors = opts.capacitors,
    sensor = opts.sensor,
    failures = {},
  }, Machine)
  local methods = {
    getEUStored = function() m:_check("getEUStored"); return m.stored end,
    getEUMaxStored = function() m:_check("getEUMaxStored"); return m.capacity end,
    getSensorInformation = function()
      m:_check("getSensorInformation")
      return m.sensor or sensor_lines(m)
    end,
  }
  for _, name in ipairs(opts.without or {}) do methods[name] = nil end
  m.proxy = env:add_component("gt_machine", methods, opts.address)
  return m
end

function Machine:fail(method, message)
  self.failures[method] = message or (method .. " failed")
end

function Machine:_check(method)
  if self.failures[method] then error(self.failures[method], 0) end
end

return M
