-- CI runs `luacheck oc` (the Lint job in ci.yml).

-- OpenComputers runs Lua 5.3, and OpenOS adds os.sleep.
stds.openos = { read_globals = { os = { fields = { "sleep" } } } }
std = "lua53+openos"
max_line_length = false

ignore = {
  -- `x = nil` once a big table is done with, so the garbage collector can
  -- have it back on a computer with a few hundred KB.
  "311",
  -- An empty branch with a comment saying why that case does nothing.
  "542",
}

-- rc runs these as services and calls their start/stop/status globals;
-- gcm_agent calls is_running and reads stopped_on_purpose.
local rc = { globals = { "start", "stop", "status", "is_running", "stopped_on_purpose" } }
files["oc/craft_monitor.lua"] = rc
files["oc/gcm_agent.lua"] = rc
files["oc/network_browser.lua"] = rc
files["oc/power_monitor.lua"] = rc

-- The fakes implement whole APIs, so arguments go unused, and their
-- methods define methods.
files["oc/tests"] = { unused_args = false, ignore = { "432/self" } }
