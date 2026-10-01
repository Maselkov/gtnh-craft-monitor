local t = require("testlib")
local gt = require("fake_gt")

local suite = t.suite()
local test = suite.test

local function readings(env)
  local out = {}
  for _, r in ipairs(env:requests_to("^/api/power$", "POST")) do out[#out + 1] = r.json end
  return out
end

test("posts stored, capacity and the sensor's trend fields", function()
  local env = t.env()
  gt.new(env, {
    stored = 4e9, capacity = 1e10,
    trend = { avg_eu_in_5s = 1200, avg_eu_out_5s = 800, avg_eu_in_1h = 1100, time_to_empty_minutes = 10.06 },
  })
  env:start_service("power_monitor")
  env:run({ seconds = 1 })
  t.eq(readings(env), { {
    stored = 4000000000, capacity = 10000000000,
    avg_eu_in_5s = 1200, avg_eu_out_5s = 800, avg_eu_in_1h = 1100, time_to_empty_minutes = 10.06,
  } })
end)

test("polls every POLL_SECONDS", function()
  local env = t.env({ config = { power_monitor = { POLL_SECONDS = 30 } } })
  gt.new(env)
  env:start_service("power_monitor")
  env:run({ seconds = 100 })
  t.eq(#readings(env), 4)
end)

test("falls back to the sensor text when getEUStored() errors", function()
  local env = t.env()
  local machine = gt.new(env, { stored = 123456789, capacity = 999999999 })
  machine:fail("getEUStored")
  env:start_service("power_monitor")
  env:run({ seconds = 1 })
  local r = readings(env)[1]
  t.eq(r.stored, 123456789)
  t.eq(r.capacity, 999999999)
end)

test("falls back to the sensor text when stored > capacity", function()
  local env = t.env()
  local machine = gt.new(env, { stored = 5, capacity = 10 })
  machine.sensor = { "Operational Data:", "EU Stored: 7 EU", "EU Capacity: 10 EU" }
  machine.stored = 50
  env:start_service("power_monitor")
  env:run({ seconds = 1 })
  t.eq(readings(env)[1], { stored = 7, capacity = 10 })
end)

test("sends nothing when no reading can be had", function()
  local env = t.env()
  local machine = gt.new(env)
  machine:fail("getEUStored")
  machine:fail("getSensorInformation")
  env:start_service("power_monitor")
  env:run({ seconds = 1 })
  t.eq(#readings(env), 0)
end)

test("with two gt_machines it asks for COMPONENT_ADDRESS", function()
  local env = t.env()
  gt.new(env)
  gt.new(env)
  env:start_service("power_monitor")
  env:run({ seconds = 1 })
  t.truthy(env:printed("set CONFIG.COMPONENT_ADDRESS to pick one"))
  t.eq(env:alive(), 0)
end)

test("COMPONENT_ADDRESS picks one of several", function()
  local env = t.env({ config = { power_monitor = { COMPONENT_ADDRESS = "lsc-2" } } })
  gt.new(env, { stored = 1, capacity = 10, address = "lsc-1" })
  gt.new(env, { stored = 2, capacity = 10, address = "lsc-2" })
  env:start_service("power_monitor")
  env:run({ seconds = 1 })
  t.eq(readings(env)[1].stored, 2)
end)

return suite
