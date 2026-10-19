local t = require("testlib")
local oc_env = require("oc_env")

local suite = t.suite()
local test = suite.test

local function oc_file(name)
  local f = io.open(oc_env.OC_DIR .. "/" .. name, "rb")
  if not f then return nil end
  local body = f:read("a")
  f:close()
  return body
end

-- A fake GitHub: tags list v1.1.0 (and older ones), raw files come from
-- this checkout's oc/ for any ref, and `missing` files 404.
local function github_env(missing)
  local env = t.env({ routes = {
    ["GET /repos/Maselkov/gtnh%-craft%-monitor/tags.*"] = {
      status = 200, body = '[{"name":"v1.0.0"},{"name":"v1.1.0"},{"name":"v1.2.0-rc1"}]',
    },
    ["GET /Maselkov/gtnh%-craft%-monitor/[^/]+/oc/.+"] = function(req)
      local name = req.path:match("/oc/(.+)$")
      local body = not (missing and missing[name]) and oc_file(name)
      if not body then return { status = 404, body = "404: Not Found" } end
      return { status = 200, body = body }
    end,
  } })
  env:mkdir("/etc")
  env:mkdir("/usr/bin")
  return env
end

local function gcm(env, ...)
  local fn = assert(loadfile(oc_env.OC_DIR .. "/gcm.lua", "t", env.globals))
  return fn(...)
end

local function state(env)
  return load(env:read_file("/etc/gcm.cfg"), "=state", "t", {})()
end

local function count(list, value)
  local n = 0
  for _, v in ipairs(list) do
    if v == value then n = n + 1 end
  end
  return n
end

test("update --no-reboot installs the agent, enables it once, doesn't reboot", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", 'return { version = "v1.0.0", components = { "power" } }')
  t.eq(gcm(env, "update", "--no-reboot"), nil)
  t.truthy(env:exists("/etc/rc.d/gcm_agent.lua"))
  t.truthy(env:exists("/etc/rc.d/power_monitor.lua"))
  t.eq(state(env), {
    version = "v1.1.0", components = { "power" }, services = { "power_monitor" }, agent_set_up = true,
  })
  t.eq(count(env.shell_log, "rc gcm_agent enable"), 1)
  t.eq(env.shutdown, nil)

  -- Set up already: a later update leaves rc alone.
  t.eq(gcm(env, "update", "--no-reboot", "--ref=main"), nil)
  t.eq(state(env).version, "main")
  t.eq(count(env.shell_log, "rc gcm_agent enable"), 1)
end)

test("already up to date", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", 'return { version = "v1.1.0", components = { "power" } }')
  t.eq(gcm(env, "update", "--no-reboot"), nil)
  t.truthy(env:printed("Already up to date (v1.1.0)."))
  t.eq(env:exists("/etc/rc.d/gcm_agent.lua"), false)
end)

test("a failed update returns its message, for gcm_agent", function()
  local env = github_env({ ["manifest.lua"] = true })
  env:write_file("/etc/gcm.cfg", 'return { version = "v1.0.0", components = { "power" } }')
  local code, message = gcm(env, "update", "--no-reboot")
  t.eq(code, 1)
  t.truthy(message:find("couldn't download manifest.lua for v1.1.0 (HTTP 404)", 1, true), message)
  t.eq(state(env).version, "v1.0.0")
end)

return suite
