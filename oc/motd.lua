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

-- version and components from gcm's own record of the install, or nil.
local function installed()
  local file = io.open("/etc/gcm.cfg", "r")
  if not file then
    return nil
  end
  local body = file:read("*a")
  file:close()
  local chunk = load(body, "=/etc/gcm.cfg", "t", {})
  local ok, state = pcall(chunk or error)
  if ok and type(state) == "table" and type(state.components) == "table" then
    return state
  end
  return nil
end

local function banner()
  local computer = require("computer")
  local state = installed()
  -- Beside logo rows 2 to 4.
  local info = {
    [2] = "\27[37mGTNH Craft Monitor",
    [3] = state and ("\27[33m" .. tostring(state.version)) or nil,
    -- Not whether they're running: at boot the shell runs this before
    -- rc has started them. `gcm status` says.
    [4] = state and ("\27[37m" .. table.concat(state.components, ", ")) or nil,
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
