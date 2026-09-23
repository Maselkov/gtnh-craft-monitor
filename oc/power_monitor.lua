--[[
  GTNH Power Monitor - OpenComputers side
  -----------------------------------------
  Polls a GregTech multiblock's stored/max energy (intended for a
  Lapotronic Super Capacitor, but works for any GT machine with an
  Adapter attached) and POSTs it to the same server the craft monitor
  uses, under a separate /api/power endpoint.

  This is a SEPARATE script from craft_monitor.lua on purpose - power
  doesn't need 5-second resolution the way crafting progress does, and
  running it as its own program lets you poll at a much slower interval
  (default 60s here) without that decision being tangled up with the
  crafting side's timing at all. Run both on the same computer, or on
  two separate ones - either works, they don't know about each other.

  API NOTE:
  A GregTech machine reachable via Adapter exposes a "gt_machine"
  component with (among others):
    getEUStored()     -> number, current stored EU
    getEUMaxStored()  -> number, max EU capacity
    getSensorInformation() -> list of display strings (what a screwdriver
                               / advanced info tool would show you)

  KNOWN BUG (now fixed upstream, kept as a defensive fallback anyway):
  GTNewHorizons/GT-New-Horizons-Modpack#8619 - getEUStored()/getEUMaxStored()
  could overflow for values above 2^32 EU on older OpenComputers builds.
  Fixed via GTNewHorizons/OpenComputers#78, but since this script can't
  know which exact version you're on, it double-checks: if the direct
  numeric calls fail, or return a stored value greater than capacity
  (a telltale sign of overflow), it falls back to parsing the text from
  getSensorInformation() instead, which isn't subject to the same bug.
  This parsing approach (info[2]=stored, info[3]=max) is a
  community-confirmed workaround, not something guessed at.

  REQUIREMENTS (in-game):
    - Adapter block touching the multiblock (for an LSC, any casing
      block touching the controller should work, same as any other GT
      multiblock <-> Adapter hookup).
    - Internet Card in the computer.
    - Same allowInternet / host-allowlist requirements as craft_monitor.lua.

  SETUP:
    1. Edit the CONFIG block below.
    2. Copy this file to /etc/rc.d/power_monitor.lua.

  Deployed as an OpenOS rc service. Manage it with:
    rc power_monitor start    - start now, this boot only
    rc power_monitor stop     - stop it (flips a flag the loop checks;
                                 finishes its current poll first)
    rc power_monitor enable   - also auto-start on every future boot
    rc power_monitor status   - is it currently running
--]]

local component = require("component")
local event = require("event")
local term = require("term")
local thread = require("thread")

-- config.lua is NOT require()'d relative to this script's own
-- directory - this script lives in /etc/rc.d/, and whether OpenOS's
-- require() directory search still finds files alongside a script
-- loaded through rc's own sandboxed loader (rather than run directly)
-- isn't something that could be confirmed without a real OC environment
-- to test against. dofile() with an absolute path sidesteps the
-- question entirely. Update CONFIG_PATH if your file lives
-- somewhere other than /home/.
local CONFIG_PATH = "/home/config.lua"
local configOk, config = pcall(dofile, CONFIG_PATH)
if not configOk then
  print("[power_monitor] Could not load " .. CONFIG_PATH .. " - make sure")
  print("  it exists at that path (edit CONFIG_PATH near the top of this")
  print("  script if you keep it somewhere else). Error: " .. tostring(config))
  return
end

-- A real library, not dofile()'d by absolute path like config.lua
-- above - see json.lua's own header (used by the other two scripts)
-- for why require() is reliable here specifically (an absolute
-- package.path entry, /usr/lib/?.lua, not the directory-relative
-- search that's uncertain under rc) even though dofile() is still used
-- for config.lua. Deploy to /usr/lib/http.lua for this to find it. This
-- script doesn't need json.lua at all - its payload is always a flat
-- object of plain numbers, built directly with string.format below.
local httpOk, http = pcall(require, "http")
if not httpOk then
  print("[power_monitor] Could not require(\"http\") - make sure http.lua is")
  print("  deployed to /usr/lib/http.lua. Error: " .. tostring(http))
  return
end

-- ===================== CONFIG =====================
local CONFIG = {
  URL          = config.SERVER_URL .. "/api/power",
  API_KEY      = config.API_KEY,
  SHOW_STATUS  = false,             -- set to true if you want the live
                                     -- status screen back - both scripts
                                     -- call term.clear() on their
                                     -- own poll cycle, so two screen-owning
                                     -- scripts on one terminal fight each
                                     -- other and flicker. Only one should draw.
  POLL_SECONDS = 60,                -- energy changes slowly - no need to poll often
  COMPONENT_ADDRESS = nil,          -- set this (a component address string) if you
                                     -- have more than one gt_machine on the network
                                     -- and need to pick a specific one
  HTTP_TIMEOUT_SECONDS = 20,        -- see http.lua's
                                     -- read_response_with_timeout - bails
                                     -- out of a POST if a real, confirmed-
                                     -- from-a-live-hang stall happens
                                     -- mid-response
}
-- ====================================================

local function find_gt_machine()
  if CONFIG.COMPONENT_ADDRESS then
    local ok, proxy = pcall(component.proxy, CONFIG.COMPONENT_ADDRESS)
    if ok then return proxy, nil end
    return nil, "CONFIG.COMPONENT_ADDRESS set but couldn't proxy it: " .. tostring(proxy)
  end

  local addresses = {}
  for address in component.list("gt_machine") do
    addresses[#addresses + 1] = address
  end

  if #addresses == 0 then
    return nil, "No gt_machine component found. Check the Adapter is touching the multiblock."
  elseif #addresses > 1 then
    return nil, string.format(
      "Found %d gt_machine components - set CONFIG.COMPONENT_ADDRESS to pick one. Addresses: %s",
      #addresses, table.concat(addresses, ", "))
  end

  return component.proxy(addresses[1]), nil
end

local function parse_number_from_text(s)
  if not s then return nil end
  local digits = tostring(s):gsub("[^0-9]", "")
  if digits == "" then return nil end
  return tonumber(digits)
end

-- Splits a getSensorInformation() line on backslash-delimited segments.
-- Confirmed from a real captured dump (not assumed): each line is
-- "key\value" or "key\value\extra" (the extra segment being, e.g., the
-- averaging window size in seconds - not needed here, just skipped by
-- only ever reading parts[2]). [^\\]+ correctly means "not a backslash"
-- here even though Lua patterns don't treat \ as an escape character
-- the way regex does (% is Lua's own escape character) - verified
-- directly against the real dump's exact line shapes, not assumed from
-- pattern syntax alone.
local function sensor_line_parts(line)
  local parts = {}
  for part in line:gmatch("[^\\]+") do
    parts[#parts + 1] = part
  end
  return parts
end

-- suffix is matched as a PLAIN substring (find(..., 1, true), no
-- pattern interpretation - so a literal "." in a suffix like
-- "avg_eu_in.sec" is matched literally, not as a pattern wildcard),
-- searched for anywhere in the line rather than requiring an exact key
-- match - this is deliberate: the real dump's keys are prefixed
-- "kekztech.infodata.lapotronic_super_capacitor." (confirmed from an
-- actual Lapotronic Super Capacitor), and a substring search stays
-- correct without needing to know or assume that exact prefix for
-- every possible storage structure type OC might be attached to.
local function find_sensor_value(info, suffix)
  for _, line in ipairs(info) do
    if line:find(suffix, 1, true) then
      local parts = sensor_line_parts(line)
      if parts[2] then
        return tonumber((parts[2]:gsub(",", "")))
      end
    end
  end
  return nil
end

-- time_to.empty's value is free text ("10.06 minutes"), not a clean
-- number - extract just the leading numeric portion.
local function find_sensor_text_number(info, suffix)
  for _, line in ipairs(info) do
    if line:find(suffix, 1, true) then
      local parts = sensor_line_parts(line)
      if parts[2] then
        return tonumber(parts[2]:match("^[%d%.]+"))
      end
    end
  end
  return nil
end

-- Returns a table of trend fields - any of which may be nil if this
-- particular storage structure doesn't report them, getSensorInformation()
-- itself fails, or the component has no such method at all. Confirmed
-- available for free from the SAME getSensorInformation() call already
-- used as a stored/capacity fallback above, via a real captured dump -
-- not assumed from documentation.
local function read_power_trend(gt)
  if not gt.getSensorInformation then return {} end
  local ok, info = pcall(gt.getSensorInformation)
  if not ok or not info then return {} end
  return {
    avg_eu_in_5s = find_sensor_value(info, "avg_eu_in.sec"),
    avg_eu_out_5s = find_sensor_value(info, "avg_eu_out.sec"),
    avg_eu_in_5m = find_sensor_value(info, "avg_eu_in.min5"),
    avg_eu_out_5m = find_sensor_value(info, "avg_eu_out.min5"),
    avg_eu_in_1h = find_sensor_value(info, "avg_eu_in.hour1"),
    avg_eu_out_1h = find_sensor_value(info, "avg_eu_out.hour1"),
    time_to_empty_minutes = find_sensor_text_number(info, "time_to.empty"),
  }
end

-- Returns stored, capacity, err
local function read_energy(gt)
  local ok1, stored = pcall(gt.getEUStored)
  local ok2, capacity = pcall(gt.getEUMaxStored)

  local suspicious = ok1 and ok2 and type(stored) == "number" and type(capacity) == "number"
    and (stored < 0 or capacity < 0 or stored > capacity)

  if not ok1 or not ok2 or suspicious then
    local ok3, info = pcall(gt.getSensorInformation)
    if ok3 and info then
      local parsedStored = parse_number_from_text(info[2])
      local parsedCapacity = parse_number_from_text(info[3])
      if parsedStored then stored = parsedStored end
      if parsedCapacity then capacity = parsedCapacity end
    elseif not ok1 or not ok2 then
      return nil, nil, "getEUStored/getEUMaxStored failed and getSensorInformation fallback also failed"
    end
  end

  if type(stored) ~= "number" or type(capacity) ~= "number" then
    return nil, nil, "could not obtain numeric stored/capacity values"
  end

  return stored, capacity, nil
end

local function json_encode_number(n)
  if n ~= n or n == math.huge or n == -math.huge then return "0" end
  return string.format("%.0f", n)  -- EU values are always whole numbers, can be huge
end

local function post_reading(stored, capacity, trend)
  -- Deliberately NOT sending a timestamp here. OpenComputers' os.time()
  -- is not a Unix timestamp - it's reimplemented to return in-game
  -- seconds since the world was created, on the game's own clock (which
  -- pauses when the game does, and runs at a different rate than real
  -- time). Sending that would silently corrupt every reading, since the
  -- server compares timestamps against ITS real wall-clock time for
  -- range filtering (Hour/Day/Week/Month) - an in-game-clock value would
  -- never line up with that. The server already stamps arrival time with
  -- its own real clock when "timestamp" is omitted, which is the correct
  -- source of truth here anyway (POST latency is negligible).
  trend = trend or {}
  local parts = {
    string.format('"stored":%s', json_encode_number(stored)),
    string.format('"capacity":%s', json_encode_number(capacity)),
  }
  -- Only included when actually present - trend fields are entirely
  -- optional server-side, so a storage structure that doesn't report
  -- them (or an older getSensorInformation() shape) just omits them
  -- rather than sending a placeholder.
  for _, key in ipairs({"avg_eu_in_5s", "avg_eu_out_5s", "avg_eu_in_5m", "avg_eu_out_5m", "avg_eu_in_1h", "avg_eu_out_1h"}) do
    if trend[key] ~= nil then
      parts[#parts + 1] = string.format('"%s":%s', key, json_encode_number(trend[key]))
    end
  end
  if trend.time_to_empty_minutes ~= nil then
    -- NOT json_encode_number() here - that formatter rounds to a whole
    -- number (correct for EU amounts, wrong for a decimal minutes value
    -- like 10.06).
    parts[#parts + 1] = string.format('"time_to_empty_minutes":%s', tostring(trend.time_to_empty_minutes))
  end
  local body = "{" .. table.concat(parts, ",") .. "}"
  local ok, result = http.request_with_timeout(CONFIG.URL, body, {
    ["Content-Type"] = "application/json",
    ["X-API-Key"] = CONFIG.API_KEY,
  }, CONFIG.HTTP_TIMEOUT_SECONDS)
  return ok, result
end

local function draw_status(stored, capacity, post_ok, post_err, read_err)
  term.clear()
  term.setCursor(1, 1)
  print("=== GTNH Power Monitor ===")
  print("Last poll:    " .. os.date("%H:%M:%S"))
  if read_err then
    print("Read error:   " .. read_err)
  else
    local pct = capacity > 0 and (stored / capacity * 100) or 0
    print(string.format("Stored:       %.0f EU", stored))
    print(string.format("Capacity:     %.0f EU", capacity))
    print(string.format("Level:        %.1f%%", pct))
  end
  print("Last POST:    " .. (post_ok and "OK" or ("FAILED - " .. tostring(post_err))))
  print("")
  print("Polling every " .. CONFIG.POLL_SECONDS .. "s. 'rc power_monitor stop' to stop.")
end

local running = false

local function service_loop()
  local gt, findErr = find_gt_machine()
  if not gt then
    print("[power_monitor] " .. findErr)
    running = false
    return
  end

  if not component.isAvailable("internet") then
    print("[power_monitor] No Internet Card installed.")
    running = false
    return
  end

  print("[power_monitor] Found gt_machine. Polling every " .. CONFIG.POLL_SECONDS .. "s.")
  if not CONFIG.SHOW_STATUS then
    print("[power_monitor] SHOW_STATUS = false - running quietly, no further screen output.")
  end
  while running do
    local stored, capacity, read_err = read_energy(gt)
    local post_ok, post_err = false, "read failed"

    if not read_err then
      post_ok, post_err = post_reading(stored, capacity, read_power_trend(gt))
    end

    if CONFIG.SHOW_STATUS then
      draw_status(stored, capacity, post_ok, post_err, read_err)
    end

    if not running then break end
    -- Plain os.sleep, not event.pull(..., "interrupted") - see
    -- craft_monitor.lua's comment on this same change for why: that
    -- listened for OC's global Ctrl+Alt+C signal, genuinely ambiguous
    -- now that multiple independent rc services can be running as
    -- separate background threads at once. `rc power_monitor stop` is
    -- the one sanctioned way to stop this service now.
    os.sleep(CONFIG.POLL_SECONDS)
  end
  print("[power_monitor] Stopped.")
end

-- rc calls these as plain global functions (no local scope - loaded in
-- a sandboxed environment per OC's own rc documentation), not a
-- returned table. The rc system doesn't wait for start() to finish, so
-- it hands off to its own thread and returns quickly rather than
-- looping directly - a bare while-true here would hang `rc power_monitor
-- start` itself forever.
function start()
  if running then
    print("[power_monitor] Already running.")
    return
  end
  running = true
  thread.create(service_loop):detach()
end

-- Optional per OC's rc docs, but defined so `rc power_monitor restart`
-- gets its default stop-then-start behavior. Just flips a flag the loop
-- checks - not a thread kill.
function stop()
  running = false
end

function status()
  print("[power_monitor] " .. (running and "running" or "stopped"))
end
