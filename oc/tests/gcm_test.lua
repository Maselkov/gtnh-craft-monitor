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

-- OpenOS's own /etc/motd, as every computer has it until someone edits it.
local STOCK_MOTD = assert(io.open(oc_env.OC_DIR .. "/tests/fixtures/openos_motd", "rb")):read("a")

test("install replaces OpenOS's own motd with the banner", function()
  local env = install_env({ ok = true })
  env:write_file("/etc/motd", STOCK_MOTD)
  t.eq(gcm(env, "install", "power", table.unpack(OPTS)), nil)
  t.eq(env:read_file("/etc/motd"), oc_file("motd.lua"))
  t.eq(env:exists("/etc/motd.new"), false)
end)

test("install puts the banner in when there's no motd", function()
  local env = install_env({ ok = true })
  t.eq(gcm(env, "install", "power", table.unpack(OPTS)), nil)
  t.eq(env:read_file("/etc/motd"), oc_file("motd.lua"))
end)

test("install keeps a motd the player changed, and says so", function()
  local env = install_env({ ok = true })
  local changed = STOCK_MOTD .. 'print("my base")\n'
  env:write_file("/etc/motd", changed)
  t.eq(gcm(env, "install", "power", table.unpack(OPTS)), nil)
  t.eq(env:read_file("/etc/motd"), changed)
  t.truthy(env:printed("Kept your /etc/motd (it's been changed), so no GCM banner."))
end)

test("update refreshes an earlier banner, and quietly keeps a changed motd", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", 'return { version = "v1.0.0", components = { "power" } }')
  env:write_file("/etc/motd", "-- gcm motd\nprint('old banner')\n")
  t.eq(gcm(env, "update", "--no-reboot"), nil)
  t.eq(env:read_file("/etc/motd"), oc_file("motd.lua"))

  local changed = github_env()
  changed:write_file("/etc/gcm.cfg", 'return { version = "v1.0.0", components = { "power" } }')
  changed:write_file("/etc/motd", "print('mine')\n")
  t.eq(gcm(changed, "update", "--no-reboot"), nil)
  t.eq(changed:read_file("/etc/motd"), "print('mine')\n")
  t.eq(changed:printed("Kept your"), false)
end)

test("a motd that fails to download leaves the old one, and the install goes on", function()
  local env = github_env({ ["motd.lua"] = true })
  env:write_file("/etc/gcm.cfg", 'return { version = "v1.0.0", components = { "power" } }')
  env:write_file("/etc/motd", STOCK_MOTD)
  t.eq(gcm(env, "update", "--no-reboot"), nil)
  t.eq(env:read_file("/etc/motd"), STOCK_MOTD)
  t.eq(env:exists("/etc/motd.new"), false)
  t.truthy(env:printed("Couldn't download motd.lua (HTTP 404); /etc/motd is unchanged."))
  t.eq(state(env).version, "v1.1.0")
end)

local function motd(env)
  return assert(loadfile(oc_env.OC_DIR .. "/motd.lua", "t", env.globals))()
end

-- What the banner put on screen, without the color codes.
local function screen(env)
  return (table.concat(env.output):gsub("\27%[%d*m", ""))
end

local ALL = '{ version = "v1.1.0", components = { "craft", "power", "network" },'
  .. ' services = { "craft_monitor", "power_monitor", "network_browser" } }'

test("the banner shows the version, and how each script came up", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", "return " .. ALL)
  env:write_file("/etc/rc.cfg", 'enabled = {"craft_monitor", "network_browser", "gcm_agent"}')
  env.rc_loaded.craft_monitor = { is_running = function() return true end }
  -- Crashed starting: loaded, but its thread is dead.
  env.rc_loaded.network_browser = { is_running = function() return false end }
  motd(env)
  local text = screen(env)
  t.truthy(text:find("GTNH Craft Monitor", 1, true), text)
  t.truthy(text:find("v1.1.0", 1, true), text)
  t.truthy(text:find("craft ✓ power - network ✗", 1, true), text)
  t.truthy(text:find("OpenOS 1.8.3 (harness)", 1, true), text)
  t.eq(text:find("readonly", 1, true), nil)
end)

test("the banner marks an enabled script rc never loaded, or whose is_running errors, as down", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", "return " .. ALL)
  env:write_file("/etc/rc.cfg", 'enabled = {"craft_monitor", "power_monitor", "network_browser"}')
  env.rc_loaded.power_monitor = { is_running = function() error("boom") end }
  env.rc_loaded.network_browser = { is_running = function() return true end }
  motd(env)
  t.truthy(screen(env):find("craft ✗ power ✗ network ✓", 1, true), screen(env))
end)

test("the banner waits for scripts rc hasn't started yet, but no longer than it needs", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", "return " .. ALL)
  env:write_file("/etc/rc.cfg", 'enabled = {"craft_monitor", "power_monitor", "network_browser"}')
  local start = env.now
  -- How the game boots: the shell runs the motd before the scripts are up.
  for _, name in ipairs({ "craft_monitor", "power_monitor", "network_browser" }) do
    env.rc_loaded[name] = { is_running = function() return env.now >= start + 1 end }
  end
  motd(env)
  t.truthy(screen(env):find("craft ✓ power ✓ network ✓", 1, true), screen(env))
  t.truthy(env.now - start < 1.5, "waited " .. (env.now - start) .. "s")

  -- One that never comes up costs the whole wait, then shows as down.
  local down = github_env()
  down:write_file("/etc/gcm.cfg", "return " .. ALL)
  down:write_file("/etc/rc.cfg", 'enabled = {"craft_monitor"}')
  start = down.now
  motd(down)
  t.truthy(screen(down):find("craft ✗ power - network -", 1, true), screen(down))
  t.truthy(down.now - start >= 3 and down.now - start < 3.5, "waited " .. (down.now - start) .. "s")
end)

test("the banner lists plain names for a gcm.cfg from before services were in it", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", 'return { version = "v1.1.0", components = { "craft", "power", "network" } }')
  motd(env)
  t.truthy(screen(env):find("craft, power, network", 1, true), screen(env))
end)

test("status says how each script's service is doing", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", "return " .. ALL)
  env:write_file("/etc/rc.cfg", 'enabled = {"craft_monitor", "power_monitor", "gcm_agent"}')
  env.rc_loaded.craft_monitor = { is_running = function() return true end }
  -- Crashed: loaded, but its thread is dead.
  env.rc_loaded.power_monitor = { is_running = function() return false end }
  t.eq(gcm(env, "status"), nil)
  t.truthy(env:printed("Installed:  v1.1.0"))
  t.truthy(env:printed("  craft     running"))
  t.truthy(env:printed("  power     not running: `rc power_monitor start` shows why"))
  t.truthy(env:printed("  network   off (`rc network_browser enable` starts it on boot)"))
  t.truthy(env:printed("Latest:     v1.1.0"))
end)

test("status tells a script stopped with rc, or started by hand, from a crashed one", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", "return " .. ALL)
  env:write_file("/etc/rc.cfg", 'enabled = {"craft_monitor", "power_monitor"}')
  env.rc_loaded.craft_monitor = { is_running = function() return false end, stopped_on_purpose = true }
  env.rc_loaded.power_monitor = { is_running = function() error("boom") end }
  env.rc_loaded.network_browser = { is_running = function() return true end }
  t.eq(gcm(env, "status"), nil)
  t.truthy(env:printed("  craft     stopped (`rc craft_monitor start`)"))
  t.truthy(env:printed("  power     not running: `rc power_monitor start` shows why"))
  t.truthy(env:printed("  network   running, but not started on boot"))
end)

test("status lists plain names for a gcm.cfg from before services were in it", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", 'return { version = "v1.1.0", components = { "craft", "power" } }')
  t.eq(gcm(env, "status"), nil)
  t.truthy(env:printed("Components: craft, power"))
end)

test("the banner fits a tier 1 screen", function()
  local env = github_env()
  env:write_file("/etc/gcm.cfg", "return " .. ALL:gsub("v1.1.0", "v10.10.10"))
  env:write_file("/etc/rc.cfg", 'enabled = {"craft_monitor", "power_monitor", "network_browser"}')
  env.readonly_home = true
  motd(env)
  local lines = 0
  for line in screen(env):gmatch("([^\n]*)\n") do
    lines = lines + 1
    t.truthy(utf8.len(line) <= 50, "too wide for 50 columns: " .. line)
  end
  t.truthy(lines <= 10, "takes " .. lines .. " of 16 rows")
  t.truthy(env:printed("/home is readonly"))
end)

test("the banner gets by without gcm.cfg, and falls back to one line if it breaks", function()
  local env = github_env()
  motd(env)
  t.truthy(env:printed("GTNH Craft Monitor"))
  t.truthy(env:printed("OpenOS 1.8.3 (harness)"))

  local broken = github_env()
  broken.loaded.computer = nil
  broken.loaded.filesystem = nil
  motd(broken)
  t.truthy(broken:printed("OpenOS 1.8.3 (harness)"))
end)

return suite
