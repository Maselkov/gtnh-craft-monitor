--[[
  GTNH Craft Monitor - OpenComputers side
  ----------------------------------------
  Polls an ME Controller / ME Interface (via Adapter) for crafting CPU
  status and POSTs it to a small web server outside the game, using an
  Internet Card.

  CORRECTED API NOTE:
  GTNH's AE2<->OpenComputers bridge does NOT expose getCraftingCPUs().
  The real method (confirmed from GTNewHorizons/OpenComputers source,
  li/cil/oc/integration/appeng/NetworkControl.scala) is:

    me.getCpus() -> array of {
      name         = string,
      storage      = number,
      coprocessors = number,
      busy         = boolean,
      cpu          = <proxy object>,  -- has its own methods, below
    }

  Each row's `cpu` proxy supports:
    cpu.isActive()      -> boolean
    cpu.isBusy()         -> boolean
    cpu.activeItems()    -> list of item stacks currently being crafted
    cpu.pendingItems()   -> list of item stacks still waiting to be crafted
    cpu.storedItems()    -> list of item stacks already produced/stored
    cpu.finalOutput()    -> item stack of the end product (requires a
                             Crafting Monitor tile in that CPU cluster;
                             returns nil/error if there isn't one)
    cpu.cancel()          -> cancels the job

  Item stack tables include (at least) `label`, `name`, `size`,
  `isCraftable` per the same source.

  We use storedItems() vs pendingItems() to derive an actual progress
  percentage (sum of sizes stored / sum of sizes stored+pending).

  REQUIREMENTS (in-game):
    - Adapter block touching your ME Controller (or an ME Interface).
    - Internet Card in the computer.
    - Server config allowInternet must be true, and if there's a
      whitelist/blacklist for hosts/ports in OpenComputers.cfg, your
      server's host must be allowed. This is a server-admin setting,
      not something fixable from Lua.

  SETUP:
    1. Edit the CONFIG block below (URL, API_KEY, interval).
    2. Copy this file to /etc/rc.d/craft_monitor.lua.
    3. Optionally run once with DEBUG_DUMP = true to sanity-check the
       raw structure your build actually returns before trusting the
       parsed/derived progress numbers.

  Deployed as an OpenOS rc service. Manage it with:
    rc craft_monitor start    - start now, this boot only (starts BOTH
                                 the main status loop and the craft-
                                 request polling loop)
    rc craft_monitor stop     - stop both loops (flags they check; the
                                 request loop's own in-flight requests
                                 are unaffected, same limitation as before)
    rc craft_monitor enable   - also auto-start on every future boot
    rc craft_monitor status   - is it currently running
--]]

local component = require("component")
local event = require("event")
local serialization = require("serialization")
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
  print("[craft_monitor] Could not load " .. CONFIG_PATH .. " - make sure")
  print("  it exists at that path (edit CONFIG_PATH near the top of this")
  print("  script if you keep it somewhere else). Error: " .. tostring(config))
  return
end

-- Real libraries, not dofile()'d by absolute path like config.lua
-- above - see json.lua's own header for why require() is reliable here
-- specifically (an absolute package.path entry, /usr/lib/?.lua, not the
-- directory-relative search that's uncertain under rc) even though
-- dofile() is still used for config.lua. Deploy both to /usr/lib/ for
-- this to find them.
local jsonOk, json = pcall(require, "json")
if not jsonOk then
  print("[craft_monitor] Could not require(\"json\") - make sure json.lua is")
  print("  deployed to /usr/lib/json.lua. Error: " .. tostring(json))
  return
end
local json_encode = json.json_encode
local json_decode = json.json_decode

local httpOk, http = pcall(require, "http")
if not httpOk then
  print("[craft_monitor] Could not require(\"http\") - make sure http.lua is")
  print("  deployed to /usr/lib/http.lua. Error: " .. tostring(http))
  return
end

-- ===================== CONFIG =====================
local CONFIG = {
  URL          = config.SERVER_URL .. "/api/crafts",
  API_KEY      = config.API_KEY,
  POLL_SECONDS = 5,                 -- how often to poll + POST
  DEBUG_DUMP   = false,             -- true = print raw structure once and exit
  DEBUG_CPU_FILTER = nil,           -- e.g. "W01" - only dump this one CPU when
                                     -- DEBUG_DUMP is true (nil = dump all)
  VERBOSE      = false,             -- true = print a line per CPU every cycle
                                     -- (this is what was making things slow -
                                     -- default is a compact status screen instead)
  COMPONENT    = nil,               -- "me_controller" / "me_interface" / nil = auto
  SHOW_STATUS  = false,             -- set to true if you want the live
                                     -- status screen back - every
                                     -- screen-owning script calls
                                     -- term.clear() on its own poll
                                     -- cycle, so more than one drawing to one
                                     -- terminal fight each other and flicker.
                                     -- Only one should draw at a time.
  HTTP_TIMEOUT_SECONDS = 20,        -- see http.lua's
                                     -- read_response_with_timeout - bails
                                     -- out of an individual POST/GET if a
                                     -- real, confirmed-from-a-live-hang
                                     -- stall happens mid-response

  CRAFT_API_URL = config.SERVER_URL .. "/api/craft",  -- base for
                                     -- /requests/pending, /requests/<id>/result
  CRAFT_REQUEST_POLL_SECONDS = 1.5,  -- separate, much faster loop than the
                                      -- main status poll above - request()
                                      -- itself returns near-instantly, the
                                      -- slow part (if any) happens inside
                                      -- AE2's own background computation,
                                      -- not in anything this loop blocks on
}

-- Overrides from the craft_monitor table in config.lua, so local settings
-- survive `gcm update` replacing this file.
for key, value in pairs(config.craft_monitor or {}) do
  CONFIG[key] = value
end
-- ====================================================

-- Shared by both loops below (the main status loop and the separate
-- craft-request loop) - a single flag both check, so stop() halts both
-- with one assignment rather than needing per-loop state.
local running = false

local function find_me_component()
  if CONFIG.COMPONENT and component.isAvailable(CONFIG.COMPONENT) then
    return component[CONFIG.COMPONENT], CONFIG.COMPONENT
  end
  for _, name in ipairs({"me_controller", "me_interface"}) do
    if component.isAvailable(name) then
      return component[name], name
    end
  end
  return nil, nil
end

-- is_array()/json_encode() now come from json.lua, loaded near
-- the top of this file - see that file's own header for the encoder's
-- array-detection reasoning (an empty table counts as an array [] by
-- default, not {} - otherwise empty item lists, a common valid case,
-- would break array-only consumers like the webpage's .map() calls).

-- AE2's item stack tables give `name` as "modid:internalname" (per GTNH's
-- documented OC item spec, e.g. "gregtech:gt.metaitem.01"). We split that
-- apart so it lines up with NESQL's separate mod_id/internal_name columns,
-- which together with damage form the icon lookup key server-side.
local function split_mod_name(fullname)
  if not fullname then return nil, nil end
  local colon = fullname:find(":", 1, true)
  if not colon then return nil, fullname end
  return fullname:sub(1, colon - 1), fullname:sub(colon + 1)
end

local function simplify_items(list)
  local out = {}
  for _, it in ipairs(list or {}) do
    local mod, internal = split_mod_name(it.name)
    out[#out + 1] = {
      name = it.label or it.name or "?",
      size = it.size or 0,
      mod = mod,
      internal = internal,
      damage = it.damage,
    }
  end
  return out
end

local function sum_size(list)
  local total = 0
  for _, it in ipairs(list or {}) do
    total = total + (it.size or 0)
  end
  return total
end

local function extract_jobs(raw_cpus)
  local jobs = {}
  local errors = {}  -- collected, not printed immediately - see VERBOSE below
  for idx, row in ipairs(raw_cpus or {}) do
    local active, pending, stored = {}, {}, {}
    if row.cpu then
      local ok1, a = pcall(row.cpu.activeItems)
      if ok1 then active = a else errors[#errors+1] = "CPU " .. idx .. " activeItems(): " .. tostring(a) end
      local ok2, p = pcall(row.cpu.pendingItems)
      if ok2 then pending = p else errors[#errors+1] = "CPU " .. idx .. " pendingItems(): " .. tostring(p) end
      local ok3, s = pcall(row.cpu.storedItems)
      if ok3 then stored = s else errors[#errors+1] = "CPU " .. idx .. " storedItems(): " .. tostring(s) end
    else
      errors[#errors+1] = "CPU " .. idx .. " has no 'cpu' proxy field at all."
    end

    local storedTotal = sum_size(stored)
    local pendingTotal = sum_size(pending)
    local progress = nil
    if (storedTotal + pendingTotal) > 0 then
      progress = math.floor((storedTotal / (storedTotal + pendingTotal)) * 100 + 0.5)
    end

    -- finalOutput() needs an AE2 Crafting Monitor tile physically present
    -- in that CPU's multiblock cluster - if there isn't one, this errors
    -- or returns nil, and we just omit final_output for that CPU.
    local finalName, finalMod, finalInternal, finalDamage = nil, nil, nil, nil
    if row.cpu then
      local okf, finalStack = pcall(row.cpu.finalOutput)
      if okf and finalStack then
        finalName = finalStack.label or finalStack.name
        finalMod, finalInternal = split_mod_name(finalStack.name)
        finalDamage = finalStack.damage
      end
    end

    jobs[#jobs + 1] = {
      name             = row.name or "(unnamed CPU)",
      busy             = row.busy and true or false,
      storage          = row.storage,
      coprocessors     = row.coprocessors,
      progress_percent = progress,
      final_output          = finalName,
      final_output_mod      = finalMod,
      final_output_internal = finalInternal,
      final_output_damage   = finalDamage,
      active  = simplify_items(active),
      pending = simplify_items(pending),
      stored  = simplify_items(stored),
    }

    if CONFIG.VERBOSE then
      print(string.format("[craft_monitor] CPU %d '%s': busy=%s active=%d pending=%d stored=%d",
        idx, tostring(row.name), tostring(row.busy), #active, #pending, #stored))
    end
  end
  return jobs, errors
end

local function post_json(payload)
  -- CONFIRMED root cause of every "stuck" service this whole
  -- investigation chased: json_encode() was called completely
  -- unprotected - no pcall - anywhere it was used to build a POST body.
  -- On the rare payload that's too large for available memory ("not
  -- enough memory for buffer allocation"), the resulting error
  -- propagated straight up, uncaught, out of a DETACHED thread
  -- (service_loop runs inside thread.create():detach()). An uncaught
  -- error in a detached thread just kills it silently - under the old
  -- start_monitors.lua launch model there was nothing to catch or
  -- report that, so the service just stopped dead with zero
  -- explanation. rc happening to log uncaught exceptions to
  -- /tmp/event.log is what actually surfaced this.
  local encodeOk, body = pcall(json_encode, payload)
  if not encodeOk then
    return false, "json_encode failed: " .. tostring(body)
  end
  return http.request_with_timeout(CONFIG.URL, body, {
    ["Content-Type"] = "application/json",
    ["X-API-Key"] = CONFIG.API_KEY,
  }, CONFIG.HTTP_TIMEOUT_SECONDS)
end

-- Sends a raw dump to the server as plain text instead of printing to
-- the OC terminal - the default screen/GPU setup has essentially no
-- scrollback, so anything longer than a screenful just gets clipped
-- with no way to review it. The server exposes this back at GET
-- /api/debug so you can just open it in a normal browser.
local function post_debug(tag, text)
  local debugUrl = CONFIG.URL:gsub("/api/crafts$", "/api/debug")
  local encodeOk, body = pcall(json_encode, {tag = tag, dump = text})
  if not encodeOk then
    print("[craft_monitor] debug POST '" .. tag .. "': FAILED - json_encode failed: " .. tostring(body))
    return
  end
  local ok, result = http.request_with_timeout(debugUrl, body, {
    ["Content-Type"] = "application/json",
    ["X-API-Key"] = CONFIG.API_KEY,
  }, CONFIG.HTTP_TIMEOUT_SECONDS)
  print("[craft_monitor] debug POST '" .. tag .. "': " .. (ok and "OK" or ("FAILED - " .. tostring(result))))
end

-- json_decode() now comes from json.lua, loaded near the top
-- of this file - the craft-request loop below needs to read a list of
-- pending requests the server hands it, the first place in this
-- project that ever needed to PARSE JSON back rather than just send it.

-- ---------------------------------------------------------------------
-- Craft requests: a separate, much faster thread than the main status
-- loop, handling getCraftables() -> .request() -> CraftingStatus.
--
-- KEY TIMING FACT, confirmed through real testing (not assumed): once a
-- request is accepted, isDone() on the returned handle does NOT
-- reliably become true - per the actual AE2/OC source, it only tracks a
-- "link" that's set on SUCCESS, so a request that's REJECTED (missing
-- resources) leaves isDone() false FOREVER. The real signal is
-- isComputing() becoming false: at that point, hasFailed() tells you
-- whether it was accepted or rejected. If accepted, the target CPU
-- flips busy=true at essentially the same moment (confirmed via a live
-- Robot Arm (UHV) test) - that's the actual "done, for our purposes"
-- point. What happens after that (the real, possibly long physical
-- crafting process) is a different concern, already handled by the
-- existing pin/completion infrastructure - this loop hands off to that
-- rather than tracking completion itself.

local function craft_get_json(path)
  local url = CONFIG.CRAFT_API_URL .. path
  local ok, result = http.request_with_timeout(url, nil, { ["X-API-Key"] = CONFIG.API_KEY }, CONFIG.HTTP_TIMEOUT_SECONDS)
  if not ok then return nil, tostring(result) end
  local decoded, err = json_decode(result)
  if not decoded then return nil, "JSON decode failed: " .. tostring(err) end
  return decoded, nil
end

local function craft_post_json(path, payload)
  local url = CONFIG.CRAFT_API_URL .. path
  local encodeOk, body = pcall(json_encode, payload)
  if not encodeOk then
    return false, "json_encode failed: " .. tostring(body)
  end
  return http.request_with_timeout(url, body, {
    ["Content-Type"] = "application/json",
    ["X-API-Key"] = CONFIG.API_KEY,
  }, CONFIG.HTTP_TIMEOUT_SECONDS)
end

local function build_craftable_filter(mod, internal, damage)
  local filter = {}
  if mod and mod ~= "" and internal then
    filter.name = mod .. ":" .. internal
  elseif internal then
    filter.name = internal
  end
  if damage ~= nil then
    filter.damage = damage
  end
  return filter
end

-- Fluids need a DIFFERENT filter shape than items - confirmed via a
-- real, successful Molten Neutronium craft request test: filtering
-- getCraftables() by {name=..., damage=...} (the item-style filter)
-- never matched a fluid pattern, but {label=...} did, and .request()
-- on the resulting match genuinely succeeded. The matched object also
-- lacked getItemStack() entirely (confirmed via that same test), so
-- GTNH's fork is very likely returning some fluid-specific Craftable
-- variant here rather than the vanilla item one - but since we never
-- call getItemStack() ourselves (all display data already comes from
-- what the browser sent), that difference doesn't affect this loop at
-- all, only the filter shape used to find it in the first place does.
local function build_fluid_craftable_filter(label)
  return { label = label }
end

local function snapshot_cpu_busy(me)
  local ok, cpus = pcall(me.getCpus)
  if not ok then return {} end
  local snap = {}
  for _, c in ipairs(cpus) do
    snap[c.name] = c.busy and true or false
  end
  return snap
end

-- What a CPU's job is making, as split_mod_name() pieces - or nil when
-- the CPU has no Crafting Monitor to ask (or isn't running anything).
local function cpu_output(cpuProxy)
  local ok, stack = pcall(cpuProxy.finalOutput)
  if not ok or not stack then return nil end
  local mod, internal = split_mod_name(stack.name)
  return { mod = mod, internal = internal, damage = stack.damage }
end

-- Which CPU an accepted request went to. AE2's request() doesn't say,
-- so it's found by elimination: a CPU busy now that was idle when the
-- request was submitted and isn't already credited to another request.
-- Planning can take minutes, so other jobs (a player's, or another
-- request's) can start in that window too - a candidate whose final
-- output is known and isn't the requested item is ruled out. Fluids
-- compare the same way: finalOutput() reports a fluid job as the fluid
-- itself (name "molten.neutronium", no mod or damage - confirmed on a
-- real Molten Neutronium craft), the same form the browser sends.
-- Without a match, a lone remaining candidate is taken only if its
-- output is unknown (no Crafting Monitor to ask). nil when none can be
-- told apart - the request is still accepted, just without a pin,
-- rather than pinning its user to someone else's job.
local function find_request_cpu(me, entry, claimed)
  local ok, cpus = pcall(me.getCpus)
  if not ok then return nil end
  local unknown = {}
  for _, c in ipairs(cpus) do
    if c.busy and not entry.before[c.name] and not claimed[c.name] then
      local output = c.cpu and cpu_output(c.cpu)
      if output then
        if output.mod == entry.mod and output.internal == entry.internal
            and (entry.damage == nil or output.damage == entry.damage) then
          return c.name
        end
      else
        unknown[#unknown + 1] = c.name
      end
    end
  end
  if #unknown == 1 then return unknown[1] end
  return nil
end

-- Whether the job on a CPU is still the one a cancel was meant for.
-- expected is what the server last saw the CPU making; nil when that
-- wasn't reported, and then there's nothing to compare.
local function is_expected_job(cpuProxy, expected)
  if not expected or not expected.internal then return true end
  local output = cpu_output(cpuProxy)
  return output ~= nil
    and output.mod == expected.mod
    and output.internal == expected.internal
    and (expected.damage == nil or output.damage == expected.damage)
end

-- Finds a specific CPU's own callable proxy (the same `.cpu` field used
-- everywhere else in this script for activeItems()/pendingItems()/etc.)
-- by name, for cancel() - the only place this script needs to act on
-- one SPECIFIC named CPU rather than just reading getCpus()'s summary.
local function find_cpu_proxy_by_name(me, cpuName)
  local ok, cpus = pcall(me.getCpus)
  if not ok then return nil, tostring(cpus) end
  for _, c in ipairs(cpus) do
    if c.name == cpuName then
      return c.cpu, nil
    end
  end
  return nil, "no CPU named " .. tostring(cpuName) .. " found"
end

-- One tracked in-flight request: { status (CraftingStatus handle),
-- before (CPU busy snapshot), kind, mod, internal, damage }. Lives only in this thread's own memory - if
-- craft_monitor.lua restarts mid-request, whatever was in flight is
-- lost track of. A known, accepted limitation (same category as the
-- network browser's non-persistent state) rather than something this
-- version tries to solve.
local function run_craft_request_loop(me)
  local tracked = {}  -- request id -> { status = <CraftingStatus>, before = <snapshot>, ... }
  -- CPUs already credited to an accepted request, until they go idle -
  -- so a second request finishing planning can't claim the same one.
  local claimed = {}

  while running do
    -- Pick up any new pending requests.
    if me.getCraftables then
      local pendingResp, err = craft_get_json("/requests/pending")
      if pendingResp and pendingResp.requests then
        for _, reqData in ipairs(pendingResp.requests) do
          if not tracked[reqData.id] then
            local filter = (reqData.kind == "fluid")
              and build_fluid_craftable_filter(reqData.label)
              or build_craftable_filter(reqData.mod, reqData.internal, reqData.damage)
            local foundOk, craftables = pcall(me.getCraftables, filter)

            if not foundOk or #craftables == 0 then
              craft_post_json("/requests/" .. reqData.id .. "/result", {

                status = "failed",
                reason = "no matching craftable pattern found",
              })
            else
              local before = snapshot_cpu_busy(me)
              local reqOk, status = pcall(craftables[1].request, reqData.amount)
              if not reqOk then
                craft_post_json("/requests/" .. reqData.id .. "/result", {
                  status = "failed",
                  reason = tostring(status),
                })
              else
                tracked[reqData.id] = {
                  status = status,
                  before = before,
                  kind = reqData.kind,
                  mod = reqData.mod,
                  internal = reqData.internal,
                  damage = reqData.damage,
                }
              end
            end
          end
        end
      end
    end

    -- Pick up any new pending cancel requests. Much simpler than the
    -- craft-request flow above - cancel() (confirmed from source)
    -- resolves synchronously, no multi-poll isComputing/CraftingStatus
    -- tracking needed, just call it and report the result immediately.
    local cancelResp = craft_get_json("/cancel/pending")
    if cancelResp and cancelResp.requests then
      for _, cancelData in ipairs(cancelResp.requests) do
        local cpuProxy, findErr = find_cpu_proxy_by_name(me, cancelData.cpu_name)
        if not cpuProxy then
          craft_post_json("/cancel/" .. cancelData.id .. "/result", {
            success = false,
            reason = findErr,
          })
        elseif not is_expected_job(cpuProxy, cancelData.expected_output) then
          -- The job the user saw has ended since; cancelling now would
          -- hit whatever the CPU moved on to.
          craft_post_json("/cancel/" .. cancelData.id .. "/result", {
            success = false,
            reason = "that job already ended - nothing was cancelled",
          })
        else
          local cancelOk, cancelResult = pcall(cpuProxy.cancel)
          if not cancelOk then
            craft_post_json("/cancel/" .. cancelData.id .. "/result", {
              success = false,
              reason = tostring(cancelResult),
            })
          else
            -- cancelResult is cancel()'s own boolean: true if it was
            -- actually busy and got cancelled, false if it wasn't busy
            -- (a safe AE2-side no-op, but still worth reporting back as
            -- "there was nothing to cancel" rather than a blank success).
            craft_post_json("/cancel/" .. cancelData.id .. "/result", {
              success = cancelResult and true or false,
              reason = cancelResult and nil or "CPU was not busy - nothing to cancel",
            })
          end
        end
      end
    end

    -- Check on everything currently in flight.
    local busyNow = snapshot_cpu_busy(me)
    for name in pairs(claimed) do
      if not busyNow[name] then claimed[name] = nil end
    end
    for reqId, entry in pairs(tracked) do
      local computingOk, computing = pcall(entry.status.isComputing)
      if computingOk and not computing then
        local failOk, failed, failReason = pcall(entry.status.hasFailed)
        if failOk and failed then
          craft_post_json("/requests/" .. reqId .. "/result", {
            status = "failed",
            reason = tostring(failReason or "request failed"),
          })
        else
          local cpuName = find_request_cpu(me, entry, claimed)
          if cpuName then claimed[cpuName] = true end
          craft_post_json("/requests/" .. reqId .. "/result", {
            status = "accepted",
            cpu_name = cpuName,
          })
        end
        tracked[reqId] = nil
      end
    end

    os.sleep(CONFIG.CRAFT_REQUEST_POLL_SECONDS)
  end
end


-- Compact "GUI": redraws a fixed handful of lines in place each cycle
-- instead of scrolling a log. This is what replaces the old per-CPU
-- print loop, which was the actual source of the slowness - a real
-- terminal redraw/scroll for every one of 30+ lines, every cycle, adds
-- up fast even though the AE2 calls themselves are cheap.
local function draw_status(kind, jobs, post_ok, post_err, errors)
  term.clear()
  term.setCursor(1, 1)
  print("=== GTNH Craft Monitor ===")
  print("Component:    " .. kind)
  print("Last poll:    " .. os.date("%H:%M:%S"))

  local busy = 0
  for _, j in ipairs(jobs) do
    if j.busy then busy = busy + 1 end
  end
  print(string.format("CPUs:         %d total (%d busy, %d idle)", #jobs, busy, #jobs - busy))
  print("Last POST:    " .. (post_ok and "OK" or ("FAILED - " .. tostring(post_err))))

  if #errors > 0 then
    print(string.format("Read errors:  %d this cycle (set CONFIG.VERBOSE = true for detail)", #errors))
  else
    print("Read errors:  none")
  end

  print("")
  print("Polling every " .. CONFIG.POLL_SECONDS .. "s. 'rc craft_monitor stop' to stop.")
end

local function service_loop()
  local me, kind = find_me_component()
  if not me then
    print("[craft_monitor] No me_controller / me_interface found. Check Adapter placement.")
    running = false
    return
  end
  print("[craft_monitor] Using component: " .. kind)

  if not component.isAvailable("internet") then
    print("[craft_monitor] No Internet Card installed.")
    running = false
    return
  end

  if not me.getCpus then
    print("[craft_monitor] This component has no getCpus() method - unexpected AE2/OC bridge version.")
    running = false
    return
  end

  if CONFIG.DEBUG_DUMP then
    local raw = me.getCpus()
    print("[craft_monitor] getCpus() returned " .. tostring(#raw) .. " CPU(s).")
    print("[craft_monitor] Sending dumps to the server - check " .. CONFIG.URL:gsub("/api/crafts$", "/api/debug") .. " in a browser once this finishes.")
    for i, row in ipairs(raw) do
      -- Optional: set CONFIG.DEBUG_CPU_FILTER = "W01" (a CPU name) to
      -- only dump that one CPU, instead of posting all of them - keeps
      -- the debug page small and easy to read when you just need to
      -- inspect one specific item/fluid.
      if not CONFIG.DEBUG_CPU_FILTER or row.name == CONFIG.DEBUG_CPU_FILTER then
        local summary = string.format("CPU %d: name=%s busy=%s storage=%s coprocessors=%s",
          i, tostring(row.name), tostring(row.busy), tostring(row.storage), tostring(row.coprocessors))
        if row.cpu then
          -- Full row dump (everything getCpus() gave us for this CPU,
          -- minus the callable cpu proxy itself which serialization
          -- can't handle) - catches any field we don't already know to
          -- look for, like a possible suspended/paused flag we've never
          -- printed because we only ever pulled out 4 specific keys.
          local rowCopy = {}
          for k, v in pairs(row) do
            if k ~= "cpu" then rowCopy[k] = v end
          end
          local rowOk, rowSerialized = pcall(serialization.serialize, rowCopy, true)
          local rowDump = "full row (minus cpu proxy):\n" ..
            (rowOk and rowSerialized or ("serialize failed: " .. tostring(rowSerialized)))

          -- Speculative probes: plausible method names for a
          -- suspend/pause signal we have no confirmed documentation
          -- for. Harmless if any don't exist - pcall just reports that
          -- cleanly instead of erroring the whole dump.
          local probeNames = {"isSuspended", "isPaused", "suspended", "paused", "getSuspended", "getPaused"}
          local probeLines = {}
          for _, pname in ipairs(probeNames) do
            if row.cpu[pname] then
              local pok, pval = pcall(row.cpu[pname])
              probeLines[#probeLines + 1] = pname .. "() = " .. (pok and tostring(pval) or ("ERROR: " .. tostring(pval)))
            else
              probeLines[#probeLines + 1] = pname .. " -> method does not exist"
            end
          end
          local probeDump = "speculative method probes:\n" .. table.concat(probeLines, "\n")

          local ok, active = pcall(row.cpu.activeItems)
          local ok2, pending = pcall(row.cpu.pendingItems)
          local ok3, stored = pcall(row.cpu.storedItems)
          local text = summary
            .. "\n\n" .. rowDump
            .. "\n\n" .. probeDump
            .. "\n\nactiveItems ok=" .. tostring(ok) .. "\n" .. serialization.serialize(active, true)
            .. "\n\npendingItems ok=" .. tostring(ok2) .. "\n" .. serialization.serialize(pending, true)
            .. "\n\nstoredItems ok=" .. tostring(ok3) .. "\n" .. serialization.serialize(stored, true)
          post_debug("cpu-" .. tostring(row.name), text)
        else
          post_debug("cpu-" .. tostring(row.name), summary .. "\n(no cpu proxy field)")
        end
      end
    end
    print("[craft_monitor] Done. Set DEBUG_DUMP = false to start normal polling.")
    running = false
    return
  end

  if me.getCraftables then
    thread.create(function()
      run_craft_request_loop(me)
    end):detach()
    print("[craft_monitor] Craft-request thread started (polling every " ..
      CONFIG.CRAFT_REQUEST_POLL_SECONDS .. "s).")
  else
    print("[craft_monitor] No getCraftables() method on this component - craft requests won't be available.")
  end

  print("[craft_monitor] Polling every " .. CONFIG.POLL_SECONDS .. "s.")
  if not CONFIG.SHOW_STATUS then
    print("[craft_monitor] SHOW_STATUS = false - running quietly, no further screen output.")
  end
  while running do
    local ok, raw = pcall(me.getCpus)
    local jobs, errors = {}, {}
    local post_ok, post_err = false, "getCpus() failed"

    if ok then
      jobs, errors = extract_jobs(raw)
      -- No timestamp field sent here - OC's os.time() is in-game clock
      -- time, not a Unix timestamp (see oc/power_monitor.lua's comment
      -- on this for the full explanation). The server already stamps
      -- arrival time with its own real clock, which is what /api/crafts's
      -- age_seconds is actually built from - the game_timestamp field
      -- this used to send was stored server-side but never used for
      -- anything, so removed rather than left as a misleading value.
      post_ok, post_err = post_json({
        source = kind,
        jobs = jobs,
      })
    else
      post_err = tostring(raw)
    end

    if not CONFIG.SHOW_STATUS then
      -- Stay fully quiet - this terminal belongs to another script
      -- (network_browser.lua or power_monitor.lua) to draw on instead.
    elseif CONFIG.VERBOSE then
      -- verbose mode: leave the per-CPU lines from extract_jobs() as a
      -- scrolling log, skip the redraw entirely.
      print("[craft_monitor] Last POST: " .. (post_ok and "OK" or ("FAILED - " .. tostring(post_err))))
    else
      draw_status(kind, jobs, post_ok, post_err, errors)
    end

    if not running then break end
    -- Plain os.sleep, not event.pull(..., "interrupted") - that
    -- listened for OC's global Ctrl+Alt+C signal, which is genuinely
    -- ambiguous now that three independent rc services can all be
    -- running as separate background threads at once: whether that
    -- signal reaches one, several, or all of their event.pull() calls
    -- isn't something this could verify, and it would undermine the
    -- whole point of converting to rc (independent per-service stop)
    -- if it could. `rc craft_monitor stop` - which just flips `running`
    -- - is the one sanctioned way to stop this service now.
    os.sleep(CONFIG.POLL_SECONDS)
  end
  print("[craft_monitor] Stopped.")
end

-- rc calls these as plain global functions (no local scope - loaded in
-- a sandboxed environment per OC's own rc documentation), not a
-- returned table. The rc system doesn't wait for start() to finish, so
-- it hands off to its own thread and returns quickly rather than
-- looping directly - a bare while-true here would hang `rc craft_monitor
-- start` itself forever. This starts BOTH loops (main status + the
-- separate craft-request loop, spawned from inside service_loop as
-- before) under the SAME running flag.
function start()
  if running then
    print("[craft_monitor] Already running.")
    return
  end
  running = true
  thread.create(service_loop):detach()
end

-- Optional per OC's rc docs, but defined so `rc craft_monitor restart`
-- gets its default stop-then-start behavior. Just flips a flag both
-- loops check - not a thread kill. Any craft request already in flight
-- when stop() is called is unaffected either way (same limitation as
-- before this conversion - craft_monitor restarting mid-request loses
-- track of it, a known, accepted limitation).
function stop()
  running = false
end

function status()
  print("[craft_monitor] " .. (running and "running" or "stopped"))
end
