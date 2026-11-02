-- gcm motd
-- motd.lua - the GTNH Craft Monitor banner. gcm installs it as /etc/motd,
-- which OpenOS's /etc/profile.lua runs with dofile() when the shell
-- starts, right after clearing the screen. It only replaces the stock
-- OpenOS motd or an earlier copy of this one (the first line above is how
-- gcm recognizes it), never one you've changed; edit this one and gcm
-- leaves it alone from then on.
--
-- Fits a tier 1 screen (50x16), with room below for anything the scripts
-- print as they start.

local LOGO = {
  "  ____  ____ __  __",
  " / ___|/ ___|  \\/  |",
  "| |  _| |   | |\\/| |",
  "| |_| | |___| |  | |",
  " \\____|\\____|_|  |_|",
}

-- Runs a Lua file in env and returns what it returned, or nil.
local function read_lua(path, env)
  local file = io.open(path, "r")
  if not file then
    return nil
  end
  local body = file:read("*a")
  file:close()
  local chunk = load(body, "=" .. path, "t", env)
  local ok, result = pcall(chunk or error)
  return ok and result or nil
end

-- version, components and their services from gcm's own record of the
-- install, or nil.
local function installed()
  local state = read_lua("/etc/gcm.cfg", {})
  if type(state) == "table" and type(state.components) == "table" then
    return state
  end
  return nil
end

-- Each script with how it came up, the way gcm_agent tells the web page:
-- ✓ enabled and running, ✗ enabled but not running (it crashed starting),
-- - not enabled. rc has started the enabled ones by the time the shell
-- runs this. Plain names for a gcm.cfg from before services were in it.
local function scripts(state)
  local services = state.services
  if type(services) ~= "table" or #services ~= #state.components then
    return "\27[37m" .. table.concat(state.components, ", ")
  end
  local rcEnv = {}
  read_lua("/etc/rc.cfg", rcEnv)
  local enabled = {}
  for _, name in ipairs(type(rcEnv.enabled) == "table" and rcEnv.enabled or {}) do
    enabled[name] = true
  end
  local okRc, rc = pcall(require, "rc")
  local loaded = okRc and type(rc) == "table" and rc.loaded or {}
  local parts = {}
  for i, name in ipairs(state.components) do
    local svc = loaded[services[i]]
    local running = type(svc) == "table" and type(svc.is_running) == "function"
      and select(2, pcall(svc.is_running)) == true
    local mark = not enabled[services[i]] and "\27[37m-"
      or running and "\27[32m✓" or "\27[31m✗"
    parts[i] = "\27[37m" .. name .. " " .. mark
  end
  return table.concat(parts, " ")
end

local function banner()
  local computer = require("computer")
  local state = installed()
  -- Beside logo rows 2 to 4.
  local info = {
    [2] = "\27[37mGTNH Craft Monitor",
    [3] = state and ("\27[33m" .. tostring(state.version)) or nil,
    [4] = state and scripts(state) or nil,
  }
  io.write("\n")
  for i, row in ipairs(LOGO) do
    io.write("\27[36m", row)
    if info[i] then
      io.write(string.rep(" ", 23 - #row), info[i])
    end
    io.write("\n")
  end
  io.write("\n\27[37m", _OSVERSION, " (", math.floor(computer.totalMemory() / 1024), "k RAM)\n")
  if require("filesystem").get("home").isReadOnly() then
    io.write("\27[33mNote: /home is readonly. Run `install`, reboot.\n")
  end
  io.write("\27[37m\n")
end

-- profile.lua doesn't catch errors from here, and one would stop the
-- shell's setup halfway, so a broken banner falls back to one line.
if not pcall(banner) then
  io.write("\27[37m", tostring(_OSVERSION), "\n")
end
