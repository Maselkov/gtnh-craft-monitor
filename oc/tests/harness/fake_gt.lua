--[[
  A fake GregTech machine (gt_machine component) for oc_env, shaped like
  a Lapotronic Supercapacitor as power_monitor.lua reads one.

  machine.stored / machine.capacity feed getEUStored()/getEUMaxStored();
  machine.sensor is getSensorInformation()'s lines (built from those and
  machine.trend unless set directly). machine:fail(method, message)
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
  return lines
end

function M.new(env, opts)
  opts = opts or {}
  local m = setmetatable({
    stored = opts.stored or 5e9,
    capacity = opts.capacity or 1e10,
    trend = opts.trend,
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
