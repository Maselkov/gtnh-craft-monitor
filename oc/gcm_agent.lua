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
}
for key, value in pairs(config.gcm_agent or {}) do
  CONFIG[key] = value
end
-- ====================================================

-- gcm's state from before services were recorded in it.
local KNOWN_SERVICES = { craft = "craft_monitor", power = "power_monitor", network = "network_browser" }

local function read_state()
  local file = io.open(CONFIG.STATE_PATH, "r")
  if not file then return nil end
  local body = file:read("*a")
  file:close()
  local chunk = load(body, "=" .. CONFIG.STATE_PATH, "t", {})
  local ok, state = pcall(chunk or error)
  if ok and type(state) == "table" then return state end
  return nil
end

local function service_running(name)
  local ok, rc = pcall(require, "rc")
  local svc = ok and type(rc) == "table" and rc.loaded and rc.loaded[name]
  if type(svc) ~= "table" or type(svc.is_running) ~= "function" then return false end
  local called, running = pcall(svc.is_running)
  return called and running == true
end

local function heartbeat_body()
  local state = read_state() or {}
  local components = state.components or {}
  local names = state.services
  if not names or #names == 0 then
    names = {}
    for _, c in ipairs(components) do names[#names + 1] = KNOWN_SERVICES[c] or c end
  end
  local services = {}
  for _, name in ipairs(names) do
    services[#services + 1] = { name = name, running = service_running(name) }
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

local function report(command, ok, message, version)
  post("/commands/" .. tostring(command.id) .. "/result", {
    ok = ok, message = message, version = version,
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
    report(command, true, "Rebooting.", (read_state() or {}).version)
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
    report(command, true, "Updated " .. tostring(before) .. " -> " .. tostring(after) .. ", rebooting.", after)
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
    if reply and type(reply.command) == "table" and reply.command.id ~= nil then
      if carry_out(reply.command) then
        running = false
        return
      end
    end
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
