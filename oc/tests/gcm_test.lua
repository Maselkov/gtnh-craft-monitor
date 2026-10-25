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

-- A fresh computer for `gcm install`: no config.lua yet, the monitor's
-- ping answered by ping(req), and no to starting on boot and rebooting.
local function install_env(ping)
  local env = github_env()
  os.remove(env:path("/home/config.lua"))
  env.server.routes["POST /api/agent/ping"] = ping
  env.stdin = { "n", "n" }
  return env
end

local function config(env)
  return load(env:read_file("/home/config.lua"), "=config", "t", {})()
end

test("install --server --api-key saves both once the server accepts the key", function()
  local env = install_env(function(req)
    if req.headers["X-API-Key"] ~= "k3y" then return { status = 401, body = "{}" } end
    return { ok = true }
  end)
  t.eq(gcm(env, "install", "power", "--server=http://gtnh.example.com:8420/", "--api-key=k3y"), nil)
  local cfg = config(env)
  t.eq(cfg.SERVER_URL, "http://gtnh.example.com:8420")
  t.eq(cfg.API_KEY, "k3y")
  t.eq(#env:requests_to("^/api/agent/ping$", "POST"), 1)
  t.truthy(env:printed("The server accepted the API key."))
  t.eq(env:printed("Leave a value blank"), false)
end)

test("install says when the server refuses the key, and saves it anyway", function()
  local env = install_env({ status = 401, body = '{"error":"unauthorized"}' })
  t.eq(gcm(env, "install", "power", "--server=https://gtnh.example.com", "--api-key=wrong"), nil)
  t.truthy(env:printed("The server refused the API key."))
  t.eq(config(env).API_KEY, "wrong")
end)

test("install points at OpenComputers' blacklist when a local address can't connect", function()
  local env = install_env({ connect_error = "address is blacklisted" })
  t.eq(gcm(env, "install", "power", "--server=http://192.168.1.20:8420", "--api-key=k3y"), nil)
  t.truthy(env:printed("Couldn't connect: address is blacklisted"))
  t.truthy(env:printed("OpenComputers blocks local addresses by default"))

  local public = install_env({ connect_error = "connection refused" })
  t.eq(gcm(public, "install", "power", "--server=https://gtnh.example.com", "--api-key=k3y"), nil)
  t.truthy(public:printed("The request comes from the Minecraft server"))
  t.eq(public:printed("OpenComputers blocks local addresses"), false)
end)

test("install asks for the address and key without the options", function()
  local env = install_env({ ok = true })
  env.stdin = { "http://gtnh.example.com", "k3y", "n", "n" }
  t.eq(gcm(env, "install", "power"), nil)
  t.eq(config(env).SERVER_URL, "http://gtnh.example.com")
  t.truthy(env:printed("Leave a value blank"))
end)

test("install --server --api-key replace them in an existing config.lua, keeping the rest", function()
  local env = install_env({ ok = true })
  env:write_config({
    SERVER_URL = "http://old.example.com", API_KEY = "old", craft_monitor = { DEBUG_DUMP = true },
  })
  t.eq(gcm(env, "install", "power", "--server=http://new.example.com/", "--api-key=new"), nil)
  t.eq(config(env), {
    SERVER_URL = "http://new.example.com", API_KEY = "new", craft_monitor = { DEBUG_DUMP = true },
  })
  local pings = env:requests_to("^/api/agent/ping$", "POST")
  t.eq(#pings, 1)
  t.eq(pings[1].headers["X-API-Key"], "new")
  t.truthy(env:printed("Updating /home/config.lua."))
end)

test("install --server alone keeps the existing key, and checks with it", function()
  local env = install_env({ ok = true })
  env:write_config({ SERVER_URL = "http://old.example.com", API_KEY = "old" })
  t.eq(gcm(env, "install", "power", "--server=http://new.example.com"), nil)
  t.eq(config(env), { SERVER_URL = "http://new.example.com", API_KEY = "old" })
  t.eq(env:requests_to("^/api/agent/ping$", "POST")[1].headers["X-API-Key"], "old")
end)

test("install without the options leaves an existing config.lua alone", function()
  local env = install_env({ ok = true })
  env:write_config({ SERVER_URL = "http://old.example.com", API_KEY = "old" })
  local before = env:read_file("/home/config.lua")
  t.eq(gcm(env, "install", "power"), nil)
  t.eq(env:read_file("/home/config.lua"), before)
  t.truthy(env:printed("Keeping existing /home/config.lua"))
  t.eq(#env:requests_to("^/api/agent/ping$"), 0)
end)

test("install replaces the values in a config.lua made from the template, not its comments", function()
  local env = install_env({ ok = true })
  local template = oc_file("config.lua")
  local filled = template:gsub('"https://YOUR%-SERVER%-HOST:8420"', '"http://old.example.com"')
    :gsub('"change%-me"', "'old'")
  env:write_file("/home/config.lua", filled)
  t.eq(gcm(env, "install", "power", "--server=http://new.example.com", "--api-key=new"), nil)
  local text = env:read_file("/home/config.lua")
  t.eq(config(env).SERVER_URL, "http://new.example.com")
  t.eq(config(env).API_KEY, "new")
  t.truthy(text:find("to SERVER_URL itself", 1, true))
  t.truthy(text:find('API_KEY    = "new",', 1, true))
end)

local OPTS = { "--server=https://gtnh.example.com", "--api-key=k3y" }

test("install warns when a chosen script's block isn't connected, and installs it anyway", function()
  local env = install_env({ ok = true })
  -- craft yes, power yes, network no, then no to boot and reboot.
  env.stdin = { "y", "y", "n", "n", "n" }
  env:add_component("me_interface", {})
  t.eq(gcm(env, "install", table.unpack(OPTS)), nil)
  t.truthy(env:printed("Warning: no gt_machine found. power needs an Adapter touching a GregTech machine"))
  t.eq(env:printed("no me_controller or me_interface found"), false)
  t.eq(state(env).components, { "craft", "power" })
  t.truthy(env:exists("/etc/rc.d/power_monitor.lua"))
end)

test("install doesn't warn when the block is connected", function()
  local env = install_env({ ok = true })
  env.stdin = { "n", "y", "n", "n", "n" }
  env:add_component("gt_machine", {})
  t.eq(gcm(env, "install", table.unpack(OPTS)), nil)
  t.eq(env:printed("Warning:"), false)
end)

test("install warns for scripts named on the command line too", function()
  local env = install_env({ ok = true })
  t.eq(gcm(env, "install", "network", table.unpack(OPTS)), nil)
  t.truthy(env:printed("Warning: no me_controller or me_interface found. network needs an Adapter"))
end)

test("install notes a missing optional Interface Terminal for network, but not once it's there", function()
  local env = install_env({ ok = true })
  env:add_component("me_controller", {})
  t.eq(gcm(env, "install", "network", table.unpack(OPTS)), nil)
  t.eq(env:printed("Warning:"), false)
  t.truthy(env:printed("Note: no me_interface_terminal found (optional)."))

  local with_terminal = install_env({ ok = true })
  with_terminal:add_component("me_controller", {})
  with_terminal:add_component("me_interface_terminal", {})
  t.eq(gcm(with_terminal, "install", "network", table.unpack(OPTS)), nil)
  t.eq(with_terminal:printed("Note:"), false)
end)

test("an option without a value is an error", function()
  local env = install_env({ ok = true })
  local code, message = gcm(env, "install", "power", "--api-key")
  t.eq(code, 1)
  t.truthy(message:find("--api-key needs a value", 1, true), message)
end)

return suite
