--[[
  GTNH Craft Monitor - agent
  ---------------------------
  Reports this computer to the web page's Scripts dialog (admins only)
  and carries out its buttons. Every POLL_SECONDS it POSTs a heartbeat
  to /api/agent/heartbeat: the installed version and components (from
  gcm's /etc/gcm.cfg), whether each script's rc service is running,
  free memory and uptime. The reply may carry one command:

    { id = n, action = "update", ref = "release" | "main" }
        runs `gcm update --no-reboot` (with --ref=main for "main"),
        reports how it went, and reboots if the version changed
    { id = n, action = "reboot" }
        reports, then reboots

  A result about to reboot says rebooting = true, and the server shows
  the command as under way until a heartbeat from after the reboot.

  The reply also says whether to restart scripts that stop
  (auto_restart, the Status dialog's checkbox; the last answer is kept
  while the server is unreachable). After each heartbeat, a script is
  restarted if it's enabled in /etc/rc.cfg, rc has loaded it, it isn't
  running, and its stop() didn't say it was stopped on purpose (the
  stopped_on_purpose global). Not in the first RESTART_GRACE_SECONDS
  after boot, while rc starts them itself, and at most MAX_RESTARTS
  times an hour each: a script that keeps crashing is left stopped and
  the heartbeat says so (restarts, gave_up).

  Anything else is reported back as refused. The server never sends a
  URL or code: gcm.lua downloads from its own fixed GitHub repository,
  so the most a server can ask for is a real release, main, or a
  reboot.

  API NOTE: a service's running state comes from rc.loaded[name], the
  service's own global table that OpenOS's lib/rc.lua keeps once a
  service is loaded, and the is_running() each script defines there.
  A service rc hasn't loaded since boot counts as stopped.

  Installed and enabled by gcm (it's in the shared files). Manage it
  like the scripts: rc gcm_agent start|stop|enable|disable|status.
--]]

local computer = require("computer")
local thread = require("thread")

local CONFIG_PATH = "/home/config.lua"
local configOk, config = pcall(dofile, CONFIG_PATH)
if not configOk then
  print("[gcm_agent] Could not load " .. CONFIG_PATH .. ": " .. tostring(config))
  return
end

local jsonOk, json = pcall(require, "json")
local httpOk, http = pcall(require, "http")
if not jsonOk or not httpOk then
  print("[gcm_agent] Could not require json/http - run `gcm update`.")
  return
end

-- ===================== CONFIG =====================
local CONFIG = {
  URL = config.SERVER_URL .. "/api/agent",
  API_KEY = config.API_KEY,
  POLL_SECONDS = 60,
  HTTP_TIMEOUT_SECONDS = 20,
  GCM_PATH = "/usr/bin/gcm.lua",
  STATE_PATH = "/etc/gcm.cfg",
  RC_CFG_PATH = "/etc/rc.cfg",
  RESTART_GRACE_SECONDS = 120,
  MAX_RESTARTS = 3,
}
for key, value in pairs(config.gcm_agent or {}) do
  CONFIG[key] = value
end
-- ====================================================

-- gcm's state from before services were recorded in it.
local KNOWN_SERVICES = { craft = "craft_monitor", power = "power_monitor", network = "network_browser" }

-- Runs a Lua file in env, in a sandbox; returns what it returned, or nil.
local function read_lua(path, env)
  local file = io.open(path, "r")
  if not file then return nil end
  local body = file:read("*a")
  file:close()
  local chunk = load(body, "=" .. path, "t", env or {})
  local ok, result = pcall(chunk or error)
  if ok then return result end
  return nil
end

local function read_state()
  local state = read_lua(CONFIG.STATE_PATH)
  if type(state) == "table" then return state end
  return nil
end

-- The services `rc <name> enable` starts on boot: /etc/rc.cfg sets
-- `enabled = { ... }`, which OpenOS's rc loads the same way.
local function enabled_services()
  local env = {}
  read_lua(CONFIG.RC_CFG_PATH, env)
  local out = {}
  for _, name in ipairs(type(env.enabled) == "table" and env.enabled or {}) do out[name] = true end
  return out
end

local function loaded_service(name)
  local ok, rc = pcall(require, "rc")
  local svc = ok and type(rc) == "table" and rc.loaded and rc.loaded[name]
  if type(svc) == "table" then return svc end
  return nil
end

local function service_running(name)
  local svc = loaded_service(name)
  if not svc or type(svc.is_running) ~= "function" then return false end
  local called, running = pcall(svc.is_running)
  return called and running == true
end

local function service_names(state)
  local names = state.services
  if not names or #names == 0 then
    names = {}
    for _, c in ipairs(state.components or {}) do names[#names + 1] = KNOWN_SERVICES[c] or c end
  end
  return names
end

local auto_restart = true
local restarted_at = {}  -- service name -> uptimes it was restarted at

-- How often name was restarted in the last hour.
local function recent_restarts(name)
  local now, kept = computer.uptime(), {}
  for _, at in ipairs(restarted_at[name] or {}) do
    if now - at < 3600 then kept[#kept + 1] = at end
  end
  restarted_at[name] = kept
  return #kept
end

local function restart_stopped()
  if not auto_restart or computer.uptime() < CONFIG.RESTART_GRACE_SECONDS then return end
  local enabled = enabled_services()
  for _, name in ipairs(service_names(read_state() or {})) do
    local svc = loaded_service(name)
    if name ~= "gcm_agent" and enabled[name] and svc and type(svc.start) == "function"
        and svc.stopped_on_purpose ~= true and not service_running(name)
        and recent_restarts(name) < CONFIG.MAX_RESTARTS then
      print("[gcm_agent] " .. name .. " stopped; restarting it.")
      table.insert(restarted_at[name], computer.uptime())
      local ok, err = pcall(svc.start)
      if not ok then print("[gcm_agent] Couldn't restart " .. name .. ": " .. tostring(err)) end
    end
  end
end

local function heartbeat_body()
  local state = read_state() or {}
  local components = state.components or {}
  local services = {}
  for _, name in ipairs(service_names(state)) do
    local running, restarts = service_running(name), recent_restarts(name)
    services[#services + 1] = {
      name = name, running = running, restarts = restarts,
      gave_up = auto_restart and not running and restarts >= CONFIG.MAX_RESTARTS,
    }
  end
  return {
    address = computer.address(),
    version = state.version,
    components = components,
    services = services,
    free_memory = computer.freeMemory(),
    total_memory = computer.totalMemory(),
    uptime = computer.uptime(),
  }
end

local function post(path, payload)
  local ok, result = http.request_with_timeout(CONFIG.URL .. path, json.json_encode(payload), {
    ["Content-Type"] = "application/json",
    ["X-API-Key"] = CONFIG.API_KEY,
  }, CONFIG.HTTP_TIMEOUT_SECONDS)
  if not ok then return nil, tostring(result) end
  local decoded = json.json_decode(result)
  if type(decoded) ~= "table" then return nil, "bad reply: " .. tostring(result):sub(1, 80) end
  return decoded
end

-- rebooting tells the server to watch for the computer coming back.
local function report(command, ok, message, version, rebooting)
  post("/commands/" .. tostring(command.id) .. "/result", {
    ok = ok, message = message, version = version, rebooting = rebooting,
  })
end

-- Runs gcm.lua the way the shell would, but with loadfile() so its
-- error message comes back too. Returns ok, message.
local function run_gcm(...)
  local fn, loadErr = loadfile(CONFIG.GCM_PATH)
  if not fn then return false, "couldn't load gcm: " .. tostring(loadErr) end
  local ok, code, message = pcall(fn, ...)
  if not ok then return false, tostring(code) end
  if code ~= nil and code ~= 0 then return false, tostring(message or ("gcm exited with " .. tostring(code))) end
  return true
end

-- Returns true when the computer is about to reboot.
local function carry_out(command)
  if command.action == "reboot" then
    report(command, true, "Rebooting.", (read_state() or {}).version, true)
    computer.shutdown(true)
    return true
  end
  if command.action == "update" and (command.ref == "release" or command.ref == "main") then
    local before = (read_state() or {}).version
    local args = { "update", "--no-reboot" }
    if command.ref == "main" then args[#args + 1] = "--ref=main" end
    local ok, err = run_gcm(table.unpack(args))
    local after = (read_state() or {}).version
    if not ok then
      report(command, false, err, after)
      return false
    end
    -- --ref=main reinstalls even when the version reads the same.
    if after == before and command.ref ~= "main" then
      report(command, true, "Already up to date.", after)
      return false
    end
    report(command, true, "Updated " .. tostring(before) .. " -> " .. tostring(after) .. ", rebooting.", after, true)
    computer.shutdown(true)
    return true
  end
  report(command, false, "Refused: unknown command.", nil)
  return false
end

local running = false
local worker = nil

local function service_loop()
  print("[gcm_agent] Reporting every " .. CONFIG.POLL_SECONDS .. "s.")
  while running do
    local reply = post("/heartbeat", heartbeat_body())
    if reply and type(reply.auto_restart) == "boolean" then auto_restart = reply.auto_restart end
    if reply and type(reply.command) == "table" and reply.command.id ~= nil then
      if carry_out(reply.command) then
        running = false
        return
      end
    end
    restart_stopped()
    if not running then break end
    os.sleep(CONFIG.POLL_SECONDS)
  end
end

function is_running()
  return worker ~= nil and worker:status() ~= "dead"
end

function start()
  if is_running() then
    print("[gcm_agent] Already running.")
    return
  end
  running = true
  worker = thread.create(service_loop)
  worker:detach()
end

function stop()
  running = false
end

function status()
  print("[gcm_agent] " .. (is_running() and "running" or "stopped"))
end
