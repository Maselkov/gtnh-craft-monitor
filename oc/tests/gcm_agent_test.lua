local t = require("testlib")
local gt = require("fake_gt")

local suite = t.suite()
local test = suite.test

local STATE_V1 = 'return { version = "v1.0.0", components = { "craft", "power" }, '
  .. 'services = { "craft_monitor", "power_monitor" }, agent_set_up = true }'

-- An env whose server hands out `commands` one per heartbeat, then none.
local function agent_env(commands)
  local queue = commands or {}
  local env = t.env({ routes = {
    ["POST /api/agent/heartbeat"] = function()
      return { command = table.remove(queue, 1) or false }
    end,
  } })
  env:mkdir("/etc")
  env:mkdir("/usr/bin")
  env:write_file("/etc/gcm.cfg", STATE_V1)
  return env
end

-- A stand-in /usr/bin/gcm.lua: records its arguments, then sets the
-- installed version to `version` (or leaves it), or fails with `err`.
local function stub_gcm(env, version, err)
  local lines = {
    'local f = io.open("/tmp/gcm-args", "w"); f:write(table.concat({ ... }, " ")); f:close()',
  }
  if err then
    lines[#lines + 1] = string.format("do return 1, %q end", err)
  end
  if version then
    lines[#lines + 1] = string.format(
      'local s = io.open("/etc/gcm.cfg", "w"); s:write(%q); s:close()',
      'return { version = "' .. version .. '", components = { "craft", "power" } }')
  end
  env:write_file("/usr/bin/gcm.lua", table.concat(lines, "\n"))
end

local function heartbeats(env)
  local out = {}
  for _, r in ipairs(env:requests_to("^/api/agent/heartbeat$", "POST")) do out[#out + 1] = r.json end
  return out
end

local function results(env)
  local out = {}
  for _, r in ipairs(env:requests_to("^/api/agent/commands/", "POST")) do
    out[#out + 1] = { path = r.path, body = r.json }
  end
  return out
end

test("heartbeat reports version, services and memory", function()
  local env = agent_env()
  gt.new(env)
  env:start_service("power_monitor")
  env:start_service("gcm_agent")
  env:run({ seconds = 1 })
  local hb = heartbeats(env)[1]
  t.eq(hb.address, "computer-0000")
  t.eq(hb.version, "v1.0.0")
  t.eq(hb.components, { "craft", "power" })
  t.eq(hb.services, {
    { name = "craft_monitor", running = false, restarts = 0, gave_up = false },  -- never started
    { name = "power_monitor", running = true, restarts = 0, gave_up = false },
  })
  t.eq(hb.free_memory, 2 * 1024 * 1024)
  t.eq(hb.total_memory, 4 * 1024 * 1024)
end)

test("polls every POLL_SECONDS and sees a stopped service", function()
  local env = agent_env()
  gt.new(env)
  local power = env:start_service("power_monitor")
  env:start_service("gcm_agent")
  env:run({ seconds = 1 })
  power.stop()
  env:run({ seconds = 125 })
  local hbs = heartbeats(env)
  t.eq(#hbs, 3)
  t.eq(hbs[3].services[2], { name = "power_monitor", running = false, restarts = 0, gave_up = false })
end)

test("a service whose thread died counts as stopped", function()
  local env = agent_env()
  gt.new(env)
  env:start_service("power_monitor")  -- the first thread
  env:start_service("gcm_agent")
  env:run({ seconds = 1 })
  env.threads[1].dead = true  -- as when an error ends it; its running flag stays true
  env:run({ seconds = 65 })
  t.eq(heartbeats(env)[2].services[2].running, false)
end)

test("state from before services were recorded maps components to services", function()
  local env = agent_env()
  env:write_file("/etc/gcm.cfg", 'return { version = "v0.9.0", components = { "network" } }')
  env:start_service("gcm_agent")
  env:run({ seconds = 1 })
  t.eq(heartbeats(env)[1].services, { { name = "network_browser", running = false, restarts = 0, gave_up = false } })
end)

test("update to the latest release, report, reboot", function()
  local env = agent_env({ { id = 7, action = "update", ref = "release" } })
  stub_gcm(env, "v1.1.0")
  env:start_service("gcm_agent")
  env:run({ seconds = 1 })
  t.eq(env:read_file("/tmp/gcm-args"), "update --no-reboot")
  t.eq(results(env), { { path = "/api/agent/commands/7/result", body = {
    ok = true, message = "Updated v1.0.0 -> v1.1.0, rebooting.", version = "v1.1.0", rebooting = true,
  } } })
  t.eq(env.shutdown.reboot, true)
  t.eq(env:alive(), 0)
end)

test("update to main passes --ref=main", function()
  local env = agent_env({ { id = 1, action = "update", ref = "main" } })
  stub_gcm(env, "main")
  env:start_service("gcm_agent")
  env:run({ seconds = 1 })
  t.eq(env:read_file("/tmp/gcm-args"), "update --no-reboot --ref=main")
  t.eq(env.shutdown.reboot, true)
end)

test("already up to date: no reboot, keeps reporting", function()
  local env = agent_env({ { id = 2, action = "update", ref = "release" } })
  stub_gcm(env, nil)
  env:start_service("gcm_agent")
  env:run({ seconds = 70 })
  t.eq(results(env)[1].body, { ok = true, message = "Already up to date.", version = "v1.0.0" })
  t.eq(env.shutdown, nil)
  t.eq(#heartbeats(env), 2)
end)

test("a failed update reports gcm's error and doesn't reboot", function()
  local env = agent_env({ { id = 3, action = "update", ref = "release" } })
  stub_gcm(env, nil, "couldn't download manifest.lua: HTTP 404")
  env:start_service("gcm_agent")
  env:run({ seconds = 1 })
  t.eq(results(env)[1].body, { ok = false, message = "couldn't download manifest.lua: HTTP 404", version = "v1.0.0" })
  t.eq(env.shutdown, nil)
end)

test("reboot", function()
  local env = agent_env({ { id = 4, action = "reboot" } })
  env:start_service("gcm_agent")
  env:run({ seconds = 1 })
  t.eq(results(env)[1].body, { ok = true, message = "Rebooting.", version = "v1.0.0", rebooting = true })
  t.eq(env.shutdown.reboot, true)
end)

test("anything else is refused", function()
  local env = agent_env({
    { id = 5, action = "update", ref = "https://evil.test/x.lua" },
    { id = 6, action = "run", code = "os.exit()" },
  })
  stub_gcm(env, "v9")
  env:start_service("gcm_agent")
  env:run({ seconds = 70 })
  local r = results(env)
  t.eq(#r, 2)
  t.eq(r[1].body, { ok = false, message = "Refused: unknown command." })
  t.eq(r[2].body, { ok = false, message = "Refused: unknown command." })
  t.eq(env:read_file("/tmp/gcm-args"), nil)
  t.eq(env.shutdown, nil)
end)

-- Auto-restart: power_monitor enabled in rc, started, then its thread
-- dies as when an error ends it. `reply` is merged into every heartbeat
-- reply.
local function crashing_env(reply)
  local env = t.env({ routes = {
    ["POST /api/agent/heartbeat"] = function()
      local out = { command = false }
      for k, v in pairs(reply or {}) do out[k] = v end
      return out
    end,
  } })
  env:mkdir("/etc")
  env:write_file("/etc/gcm.cfg", STATE_V1)
  env:write_file("/etc/rc.cfg", 'enabled = {"power_monitor", "gcm_agent"}')
  gt.new(env)
  local power = env:start_service("power_monitor")
  env:start_service("gcm_agent")
  return env, power
end

-- Every thread but gcm_agent's (the second one started) dies.
local function crash(env, power)
  for i, th in ipairs(env.threads) do
    if i ~= 2 then th.dead = true end
  end
  t.eq(power.is_running(), false)
end

test("auto-restart: a crashed enabled script is restarted after the boot grace", function()
  local env, power = crashing_env()
  env:run({ seconds = 1 })
  crash(env, power)
  env:run({ seconds = 65 })  -- still within RESTART_GRACE_SECONDS of boot
  t.eq(power.is_running(), false)
  env:run({ seconds = 65 })
  t.eq(power.is_running(), true)
  env:run({ seconds = 60 })
  local hb = heartbeats(env)
  t.eq(hb[#hb].services[2], { name = "power_monitor", running = true, restarts = 1, gave_up = false })
end)

test("auto-restart: gives up after MAX_RESTARTS in an hour, and says so", function()
  local env, power = crashing_env()
  for _ = 1, 4 do
    env:run({ seconds = 125 })
    crash(env, power)
  end
  env:run({ seconds = 125 })
  t.eq(power.is_running(), false)
  local hb = heartbeats(env)
  t.eq(hb[#hb].services[2], { name = "power_monitor", running = false, restarts = 3, gave_up = true })
end)

test("auto-restart: not a script stopped on purpose, disabled in rc, or with the switch off", function()
  local env, power = crashing_env()
  env:run({ seconds = 1 })
  power.stop()
  env:run({ seconds = 200 })
  t.eq(power.is_running(), false)

  env, power = crashing_env()
  env:write_file("/etc/rc.cfg", 'enabled = {"gcm_agent"}')
  env:run({ seconds = 1 })
  crash(env, power)
  env:run({ seconds = 200 })
  t.eq(power.is_running(), false)

  env, power = crashing_env({ auto_restart = false })
  env:run({ seconds = 1 })
  crash(env, power)
  env:run({ seconds = 200 })
  t.eq(power.is_running(), false)
  local hb = heartbeats(env)
  t.eq(hb[#hb].services[2].gave_up, false)
end)

return suite
