--[[
  network_browser.lua - periodically scans the full ME network's item
  contents and reports them to the server, powering the site's Network
  tab (searchable/sortable browse of everything currently stored).

  Deployed as an OpenOS rc service - lives at /etc/rc.d/network_browser.lua.
  Manage it with:
    rc network_browser start    - start now, this boot only
    rc network_browser stop     - stop it (finishes any scan already in
                                   progress first, then stops rather than
                                   starting the next one - see service_loop)
    rc network_browser enable   - also auto-start on every future boot
    rc network_browser status   - is it currently running
  This is a SEPARATE, independent service from craft_monitor.lua and
  power_monitor.lua - own timing, doesn't know about either of them.
  start_monitors.lua is retired - each script is its own rc service now,
  managed independently rather than launched together as threads from
  one hand-rolled launcher script.

  WHY THIS APPROACH, CONFIRMED THROUGH ACTUAL TESTING (not just reading
  the source):
    - getItemsInNetwork() (the plain bulk call) converts the ENTIRE
      network before returning anything - one huge table, no way to
      bound its size. Repeatedly OOM'd this exact computer.
    - allItems() (the memory-safe iterator OC added for this reason) has
      its own, separate problem: it's a stateful Java iterator held open
      across thousands of individual calls, and a live network changing
      underneath it (crafting, import/export buses - i.e. any real base)
      corrupts it mid-walk. Confirmed via repeated testing: wildly
      inconsistent item counts between runs (as low as 22% of the true
      total), sometimes with zero reported errors - it doesn't just fail
      loudly, it can silently claim to be done when it isn't.
    - getItemsInNetworkById(idList) - found in a third-party exporter
      (uncountablyinfinite1056/oc-influxdb-exporter) and confirmed
      against the real Scala source - filters on raw item identity
      BEFORE converting, and is stateless per call (no iterator to
      invalidate). Tested twice on a real ~6,000-item network: matched
      the true count both times (6091 and 6085 vs a confirmed 6084),
      zero batch errors either run.
    - The remaining risk was memory for the CANDIDATE list itself - an
      early version held the whole ~10,885-entry catalog in memory via
      require(), leaving only ~850KB free at the low point on this
      computer's ~4MB ceiling. Streaming the catalog from disk too (this
      script) roughly doubled that margin to ~1.9MB in testing - real
      headroom, not just "happened not to crash."

  config.lua and item_catalog.txt are NOT loaded
  relative to this script anymore - this script now lives in /etc/rc.d/,
  and whether OpenOS's require() directory search still finds files
  alongside a script loaded through rc's own sandboxed loader (rather
  than run directly) isn't something that could be confirmed without a
  real OC environment to test against, given the track record of that
  kind of assumption in this project. Using absolute paths (dofile()
  instead of require() for config.lua, an absolute CATALOG_PATH)
  sidesteps the question entirely rather than depending on unverified
  path-search behavior. Update the paths below if your files live
  somewhere other than /home/. json.lua and http.lua ARE require()'d,
  but that's a different, reliable case - see json.lua's own header for
  why (an absolute /usr/lib/?.lua entry in OpenOS's own package.path,
  not resolved relative to wherever the calling script lives).
]]

local component = require("component")
local computer = require("computer")
local event = require("event")
local filesystem = require("filesystem")
local term = require("term")
local thread = require("thread")

local CONFIG_PATH = "/home/config.lua"
local configOk, config = pcall(dofile, CONFIG_PATH)
if not configOk then
  print("[network_browser] Could not load " .. CONFIG_PATH .. " - make sure")
  print("  it exists at that path (edit CONFIG_PATH near the top of this")
  print("  script if you keep it somewhere else). Error: " .. tostring(config))
  return
end

-- Real libraries, not dofile()'d by absolute path like config.lua
-- above - see json.lua's own header for why require() is reliable here
-- specifically (an absolute package.path entry, /usr/lib/?.lua, not the
-- directory-relative search that's uncertain under rc) even though
-- dofile() is still used for config.lua. Deploy both to /usr/lib/ for
-- this to find them. json.lua was previously its own local copy in
-- this file, including a literal copy-paste of json_decode from
-- craft_monitor.lua when the scan-token work needed it; http.lua was
-- previously bundled into shared_config.lua alongside the config
-- values (a version-skew check used to live right here, guarding
-- against an old shared_config.lua missing request_with_timeout - no
-- longer needed now that http.lua is its own dedicated file: it either
-- has that function or fails to require() at all, caught below).
local jsonOk, json = pcall(require, "json")
if not jsonOk then
  print("[network_browser] Could not require(\"json\") - make sure json.lua is")
  print("  deployed to /usr/lib/json.lua. Error: " .. tostring(json))
  return
end

local httpOk, http = pcall(require, "http")
if not httpOk then
  print("[network_browser] Could not require(\"http\") - make sure http.lua is")
  print("  deployed to /usr/lib/http.lua. Error: " .. tostring(http))
  return
end
local json_encode = json.json_encode
local json_decode = json.json_decode

-- ===================== CONFIG =====================
local CONFIG = {
  URL          = config.SERVER_URL .. "/api/network",  -- base path, /scan/start etc. appended
  API_KEY      = config.API_KEY,
  CATALOG_PATH = "/home/item_catalog.txt",  -- absolute path - see the
                                             -- header comment on why this
                                             -- isn't relative anymore
  BATCH_SIZE   = 300,     -- candidate IDs per getItemsInNetworkById() call
  RESULT_CHUNK_SIZE = 100,  -- max ITEMS json_encode'd/POSTed at once,
                            -- independent of BATCH_SIZE - see the
                            -- comment above the sub-chunking loop below
                            -- for why these can't be the same number
  DELAY_BETWEEN_BATCHES_SECONDS = 0.1,
  SCAN_INTERVAL_SECONDS = 600,  -- 10 min between full scans - this data
                                  -- doesn't need to be anywhere near as
                                  -- fresh as crafting status, and a scan
                                  -- costs real time (a minute or two)
  MAX_CONSECUTIVE_ERRORS = 5,
  HTTP_TIMEOUT_SECONDS = 20,  -- see http.lua's read_response_with_timeout -
                              -- bails out of an individual POST if a real,
                              -- confirmed-from-a-live-hang stall happens
                              -- mid-response, rather than that call sitting
                              -- there indefinitely
  SHOW_STATUS = false,  -- set true if you want the live status screen back -
                        -- same reasoning as power_monitor.lua's flag: two
                        -- screen-owning scripts fighting over one terminal
                        -- just flickers, only one should draw
}

-- Overrides from the network_browser table in config.lua, so local settings
-- survive `gcm update` replacing this file.
for key, value in pairs(config.network_browser or {}) do
  CONFIG[key] = value
end
-- ====================================================

local function find_me_component()
  for _, name in ipairs({"me_controller", "me_interface"}) do
    if component.isAvailable(name) then
      return component[name], name
    end
  end
  return nil, nil
end

-- is_array()/json_encode() now come from json.lua, loaded near
-- the top of this file.

local function post_json(path, body)
  local url = CONFIG.URL .. path
  return http.request_with_timeout(url, body, {
    ["Content-Type"] = "application/json",
    ["X-API-Key"] = CONFIG.API_KEY,
  }, CONFIG.HTTP_TIMEOUT_SECONDS)
end

-- json_decode() now comes from json.lua, loaded near the top
-- of this file - this script never needed to PARSE a response body
-- before (every endpoint it called used to just return {"ok": true}
-- with nothing worth reading back); the scan_token mechanism below is
-- the first time that changes.

-- Posts one result sub-chunk carrying the given scan token, and checks
-- the RESPONSE BODY for a stale-token rejection - not just whether the
-- HTTP round-trip itself succeeded. Returns (ok, message, rejected):
-- rejected=true means the server has explicitly told us this scan is no
-- longer the active one (a restart happened, or a newer scan started),
-- and the caller should stop posting further sub-chunks for THIS scan
-- entirely, rather than keep sending data nobody's listening for
-- anymore.
--
-- The rejection signal has to be read from the body, not the HTTP
-- status - confirmed directly from http.lua's own source:
-- request_with_timeout/read_response_with_timeout never inspect the
-- status code at all, only whether reading the response itself
-- succeeded. A 200 with {"ok": false, ...} in the body and an actual
-- transport failure both come back as "the read succeeded", so the two
-- have to be told apart by decoding and checking the body every time.
local function post_result_subchunk(subChunk, scanToken)
  local sendOk, transportOk, body = pcall(function()
    local payload = json_encode({ items = subChunk, scan_token = scanToken })
    return post_json("/scan/batch", payload)
  end)
  if not sendOk then
    -- pcall itself failed (e.g. json_encode threw - see the comment on
    -- the item sub-chunk loop below for why that's caught here, not
    -- left to propagate uncaught). transportOk here is actually pcall's
    -- own error message in this branch, not a real boolean.
    return false, transportOk, false
  end
  if not transportOk then
    return false, body, false
  end
  local decoded = json_decode(body)
  if decoded and decoded.ok == false and decoded.error == "stale_scan_token" then
    return false, "scan rejected by server (stale_scan_token)", true
  end
  return true, body, false
end

local function safe_free_memory()
  local ok, result = pcall(computer.freeMemory)
  if ok then return result end
  return nil
end

-- Same "modid:internalname" split as craft_monitor.lua, and the same
-- output field names (name=label, size, mod, internal, damage) - so the
-- server's existing _attach_item_icons() works unchanged on these items
-- too, no new icon-matching logic needed for this script.
local function split_mod_name(fullname)
  if not fullname then return nil, nil end
  local colon = fullname:find(":", 1, true)
  if not colon then return nil, fullname end
  return fullname:sub(1, colon - 1), fullname:sub(colon + 1)
end

local function simplify_item(item)
  local mod, internal = split_mod_name(item.name)
  return {
    name = item.label or item.name or "?",
    size = item.size or 0,
    mod = mod,
    internal = internal,
    damage = item.damage,
    -- AE2's own convert() sets this deliberately - a 0-size entry with
    -- isCraftable=true means "no current stock, but a crafting pattern
    -- exists" (confirmed from source: aePotentialItem() specifically
    -- keeps these in the results rather than filtering them out). Without
    -- forwarding this, the page had no way to tell that apart from a
    -- genuinely-empty entry, showing a bare "0" for both.
    isCraftable = item.isCraftable and true or false,
    kind = "item",
  }
end

-- Fluids from getFluidsInNetwork() use "amount" instead of "size", but
-- everything downstream (formatQty, tooltips, sorting) already only
-- knows about "size" - normalized here rather than teaching every
-- consumer about a second field name. "name" for a fluid is a bare
-- registry name with no colon (confirmed early in this project via the
-- GT Fluid Discretizer investigation - Forge's fluid registry is flat,
-- not per-mod-namespaced like the Item registry), so split_mod_name()
-- already handles it correctly with zero changes: mod=nil, internal=
-- the bare name, which resolve_icon() already knows to look up in
-- fluids_by_key.
local function simplify_fluid(item)
  local mod, internal = split_mod_name(item.name)
  return {
    name = item.label or item.name or "?",
    size = item.amount or 0,
    mod = mod,
    internal = internal,
    damage = item.damage,
    isCraftable = item.isCraftable and true or false,
    kind = "fluid",
  }
end

-- ---------------------------------------------------------------------
-- Diagnostics for an intermittent hang: after ~2 hours of runtime
-- (several successful scan cycles), the script sometimes stops making
-- progress entirely - scan/start fires, in_progress stays true forever,
-- scan/finish never arrives. No OOM, and craft_monitor.lua/power_monitor.lua
-- keep running fine on the same computer the whole time - if the WHOLE
-- computer had frozen (hit OC's execution budget, a real deadlock, etc.)
-- those would stop updating too, since they share the same machine. That
-- points at one specific blocking call inside THIS thread never
-- returning, not a computer-wide issue - most plausibly a stalled
-- internet.request (a TCP connection that goes half-open or silent
-- without a clean close, which OC's internet card doesn't always reliably
-- time out on its own), though a stuck native getItemsInNetworkById/
-- getFluidsInNetwork call can't be ruled out without more evidence. This
-- is a real hypothesis, not a confirmed cause - the logging below is
-- what actually pins it down next time, rather than guessing further.
--
-- Written to a LOCAL FILE deliberately, not POSTed to the server: if the
-- hang genuinely turns out to be network-related, a network-dependent
-- log would be equally likely to hang trying to report the very failure
-- it's trying to capture. A local file has no such dependency, and the
-- LAST line ever written is exactly the answer - whatever checkpoint
-- never got a matching "done"/"failed" line after it is where the hang
-- actually is.
local DEBUG_LOG_PATH = "network_browser_debug.log"
local DEBUG_LOG_MAX_BYTES = 200000  -- crude single-rotation cap, not a
                                     -- full multi-file scheme - this is
                                     -- for actively debugging one
                                     -- specific issue, not indefinite
                                     -- long-term logging

local function debug_log(msg)
  pcall(function()
    local line = string.format("[%s] %s\n", os.date("%H:%M:%S"), msg)
    local f = io.open(DEBUG_LOG_PATH, "a")
    if not f then return end
    local size = f:seek("end")
    if size and size > DEBUG_LOG_MAX_BYTES then
      f:close()
      local f2 = io.open(DEBUG_LOG_PATH, "w")
      if f2 then
        f2:write("--- log rotated (exceeded " .. DEBUG_LOG_MAX_BYTES .. " bytes) ---\n")
        f2:write(line)
        f2:close()
      end
      return
    end
    f:write(line)
    f:close()
  end)
end

-- Updated throughout run_scan() and shown live on screen (not just at
-- the end of a scan) - if SHOW_STATUS is on and it hangs while you're
-- watching, this is the same thing the log file would tell you, just
-- visible in real time without needing to go read a file.
local currentPhase = "idle"
local scanStartUptime = nil

local function set_phase(msg)
  currentPhase = msg
  local elapsed = scanStartUptime and (computer.uptime() - scanStartUptime) or 0
  debug_log(string.format("t=%.1fs  %s", elapsed, msg))
  if CONFIG.SHOW_STATUS then
    term.clear()
    term.setCursor(1, 1)
    print("=== Network Browser ===")
    print(string.format("Scanning... (%.1fs elapsed)", elapsed))
    print("")
    print(currentPhase)
    print("")
    print("Free memory: " .. tostring(safe_free_memory()))
  end
end

-- The catalog follows the GTNH version picked on the server's Game data
-- page. scan/start reports that version (catalog_version); when it isn't
-- the one downloaded last, the server's catalog replaces the local file.
-- A failed download keeps the current catalog: scanning with a slightly
-- stale list beats not scanning. Servers without game data send no
-- version, and the catalog gcm installed stays in use.
local CATALOG_VERSION_PATH = CONFIG.CATALOG_PATH .. ".version"

local function read_catalog_version()
  local f = io.open(CATALOG_VERSION_PATH, "r")
  if not f then
    return nil
  end
  local version = f:read("*l")
  f:close()
  return version
end

local function sync_catalog(version)
  if not version or version == read_catalog_version() then
    return
  end
  set_phase("Downloading the item catalog for GTNH " .. version .. "...")
  local tmp = CONFIG.CATALOG_PATH .. ".tmp"
  local ok, err = http.download_to_file(CONFIG.URL .. "/catalog", tmp, {
    ["X-API-Key"] = CONFIG.API_KEY,
  }, CONFIG.HTTP_TIMEOUT_SECONDS)
  if not ok then
    debug_log("catalog download failed, keeping the current one: " .. tostring(err))
    filesystem.remove(tmp)
    return
  end
  filesystem.remove(CONFIG.CATALOG_PATH)
  if not filesystem.rename(tmp, CONFIG.CATALOG_PATH) then
    debug_log("couldn't move the new catalog into place")
    return
  end
  local f = io.open(CATALOG_VERSION_PATH, "w")
  if f then
    f:write(version)
    f:close()
  end
  debug_log("item catalog updated to " .. version)
end

-- Returns ok, result-or-error-message. On success, result is
-- {total_items=, total_errors=}.
local function run_scan(me)
  scanStartUptime = computer.uptime()
  set_phase("Calling scan/start...")
  local ok1, result1 = post_json("/scan/start", "{}")
  if not ok1 then
    debug_log("scan/start FAILED: " .. tostring(result1))
    return false, "scan/start failed: " .. tostring(result1)
  end
  local decoded1 = json_decode(result1)
  local scanToken = decoded1 and decoded1.scan_token
  if not scanToken then
    -- Older server without the scan_token mechanism, or a genuinely
    -- malformed response - either way, there's nothing safe to thread
    -- through the rest of this scan, so it aborts rather than proceeding
    -- with a scan the server can never actually validate as complete.
    debug_log("scan/start response missing scan_token: " .. tostring(result1))
    return false, "scan/start response missing a scan_token - can't safely proceed"
  end
  debug_log("scan/start OK, token=" .. scanToken)
  sync_catalog(decoded1.catalog_version)

  set_phase("Opening item catalog...")
  local f = io.open(CONFIG.CATALOG_PATH, "r")
  if not f then
    debug_log("couldn't open catalog")
    return false, "couldn't open " .. CONFIG.CATALOG_PATH
  end

  local totalItems = 0
  local totalErrors = 0
  local consecutiveErrors = 0
  local batchNum = 0
  -- Every sub-chunk this scan ATTEMPTS to send, success or failure alike
  -- - incremented before the outcome of each post_result_subchunk() call
  -- is even known, not just on success. Sent to the server at
  -- scan/finish as chunks_sent, verified there against how many it
  -- actually received independently - counting only successes here
  -- would let a failed POST silently vanish from both sides' tallies,
  -- defeating the whole point of the comparison.
  local chunksSent = 0
  -- Set true the MOMENT the server tells us this scan is no longer the
  -- active one (a restart happened, or a newer scan started) - checked
  -- before every further POST below, so nothing keeps sending data for a
  -- scan the server has already discarded. See post_result_subchunk()
  -- and the server's own network_scan_finish() for the full mechanism
  -- this closes: a scan interrupted mid-way used to still get its
  -- (incomplete) results committed, silently recording every item that
  -- never got re-sent as "vanished" even though it never really left the
  -- network - confirmed as a real reported bug from that exact symptom.
  local scanRejected = false

  while not scanRejected do
    -- Stream the candidate list from disk, same reasoning as the results
    -- below - never hold more than one batch's worth in memory.
    local batch = {}
    for i = 1, CONFIG.BATCH_SIZE do
      local line = f:read("*l")
      if line == nil then break end
      if line ~= "" then batch[#batch + 1] = line end
    end
    if #batch == 0 then break end
    batchNum = batchNum + 1

    set_phase(string.format("Batch %d: calling getItemsInNetworkById() with %d candidate ids...", batchNum, #batch))
    local ok, result = pcall(me.getItemsInNetworkById, batch)
    batch = nil
    set_phase(string.format("Batch %d: getItemsInNetworkById() returned (ok=%s)", batchNum, tostring(ok)))

    if ok then
      consecutiveErrors = 0
      local count = #result
      totalItems = totalItems + count

      -- Chunk the RESULTS themselves before encoding/POSTing, separate
      -- from CONFIG.BATCH_SIZE (which only bounds how many candidate ids
      -- go into one getItemsInNetworkById call). Those aren't the same
      -- thing: some GT "mega" meta-items pack many distinct materials
      -- under one base item id, so a single candidate can expand into
      -- far more actual items than the candidate count would suggest.
      -- json_encode builds one big string via table.concat for whatever
      -- it's given - encoding an unexpectedly huge result in one shot is
      -- exactly what caused "not enough memory for buffer allocation",
      -- which is why post_result_subchunk() above wraps the whole
      -- encode+post in a pcall rather than calling json_encode bare.
      local subChunk = {}
      local subChunkNum = 0
      for j = 1, count do
        subChunk[#subChunk + 1] = simplify_item(result[j])
        if (#subChunk >= CONFIG.RESULT_CHUNK_SIZE or j == count) and not scanRejected then
          subChunkNum = subChunkNum + 1
          set_phase(string.format("Batch %d: POSTing result sub-chunk %d (%d items)...", batchNum, subChunkNum, #subChunk))
          chunksSent = chunksSent + 1
          local postOk, postErr, rejected = post_result_subchunk(subChunk, scanToken)
          set_phase(string.format("Batch %d: sub-chunk %d POST returned (ok=%s)", batchNum, subChunkNum, tostring(postOk)))
          if rejected then
            scanRejected = true
            debug_log(string.format("scan rejected by server mid-scan (batch %d, sub-chunk %d) - aborting", batchNum, subChunkNum))
          elseif not postOk then
            totalErrors = totalErrors + 1
            if CONFIG.SHOW_STATUS then print("[network_browser] batch POST failed: " .. tostring(postErr)) end
            debug_log("batch POST failed: " .. tostring(postErr))
          end
          subChunk = {}
          pcall(collectgarbage, "collect")
        end
      end
      result = nil
    else
      totalErrors = totalErrors + 1
      consecutiveErrors = consecutiveErrors + 1
      debug_log(string.format("Batch %d errored: %s", batchNum, tostring(result)))
      if consecutiveErrors >= CONFIG.MAX_CONSECUTIVE_ERRORS then
        f:close()
        return false, CONFIG.MAX_CONSECUTIVE_ERRORS .. " consecutive batch errors, last: " .. tostring(result)
      end
    end

    if not scanRejected then
      pcall(collectgarbage, "collect")
      set_phase(string.format("Batch %d done, sleeping %ss before next batch...", batchNum, CONFIG.DELAY_BETWEEN_BATCHES_SECONDS))
      os.sleep(CONFIG.DELAY_BETWEEN_BATCHES_SECONDS)
    end
  end
  f:close()

  if scanRejected then
    debug_log("Scan aborted early (stale_scan_token) - skipping fluids and scan/finish; the next scheduled scan will start fresh")
    return false, "scan rejected mid-way by server (stale_scan_token) - a restart or newer scan invalidated it"
  end

  set_phase("Item catalog exhausted, moving to fluids...")

  -- Fluids: a single bulk call, unlike items - getFluidsInNetwork() has
  -- no filtered/batched variant to begin with (confirmed from source:
  -- no fluid equivalent of getItemsInNetworkById exists), but real
  -- GTNH networks have far fewer distinct fluids than items (tens to
  -- low hundreds, not thousands), so the same bulk-call approach that
  -- OOM'd for ~6,000 items should be safely within bounds here. Still
  -- chunked on the way OUT (same RESULT_CHUNK_SIZE discipline as items)
  -- as cheap insurance in case that assumption is ever wrong for a
  -- particular network.
  local totalFluids = 0
  if me.getFluidsInNetwork then
    set_phase("Calling getFluidsInNetwork()...")
    local fluidOk, fluids = pcall(me.getFluidsInNetwork)
    set_phase("getFluidsInNetwork() returned (ok=" .. tostring(fluidOk) .. ")")
    if fluidOk then
      local count = #fluids
      totalFluids = count
      local subChunk = {}
      local subChunkNum = 0
      for j = 1, count do
        subChunk[#subChunk + 1] = simplify_fluid(fluids[j])
        if (#subChunk >= CONFIG.RESULT_CHUNK_SIZE or j == count) and not scanRejected then
          subChunkNum = subChunkNum + 1
          set_phase(string.format("Fluids: POSTing sub-chunk %d (%d fluids)...", subChunkNum, #subChunk))
          chunksSent = chunksSent + 1
          local postOk, postErr, rejected = post_result_subchunk(subChunk, scanToken)
          set_phase(string.format("Fluids: sub-chunk %d POST returned (ok=%s)", subChunkNum, tostring(postOk)))
          if rejected then
            scanRejected = true
            debug_log("scan rejected by server mid-fluids-scan (stale_scan_token) - aborting")
          elseif not postOk then
            totalErrors = totalErrors + 1
            if CONFIG.SHOW_STATUS then print("[network_browser] fluid batch POST failed: " .. tostring(postErr)) end
            debug_log("fluid batch POST failed: " .. tostring(postErr))
          end
          subChunk = {}
          pcall(collectgarbage, "collect")
        end
      end
      fluids = nil
    else
      totalErrors = totalErrors + 1
      debug_log("getFluidsInNetwork() failed: " .. tostring(fluids))
      if CONFIG.SHOW_STATUS then print("[network_browser] getFluidsInNetwork() failed: " .. tostring(fluids)) end
    end
  end

  if scanRejected then
    debug_log("Scan aborted during fluids (stale_scan_token) - skipping scan/finish; the next scheduled scan will start fresh")
    return false, "scan rejected mid-way by server (stale_scan_token) - a restart or newer scan invalidated it"
  end

  set_phase("Calling scan/finish...")
  local finishPayload = json_encode({ scan_token = scanToken, chunks_sent = chunksSent, total_errors = totalErrors })
  local ok2, result2 = post_json("/scan/finish", finishPayload)
  if not ok2 then
    debug_log("scan/finish FAILED: " .. tostring(result2))
    return false, "scan/finish failed: " .. tostring(result2)
  end
  local decoded2 = json_decode(result2)
  if decoded2 and decoded2.ok == false then
    debug_log("scan/finish REJECTED by server (stale token): " .. tostring(decoded2.error))
    return false, "scan/finish rejected by server: " .. tostring(decoded2.error)
  end
  if decoded2 and decoded2.rejected == true then
    -- A DIFFERENT rejection shape than the stale-token case above -
    -- ok:true (the request itself was processed fine) but rejected:true
    -- (the server verified chunks_sent/total_errors and chose not to
    -- commit this scan's results). Still a failure from run_scan()'s own
    -- point of view - the caller cares whether the data actually landed,
    -- not just whether the HTTP calls succeeded.
    debug_log("scan/finish REJECTED by server (incomplete scan): " .. tostring(decoded2.reason))
    return false, "scan discarded by server as incomplete: " .. tostring(decoded2.reason)
  end
  debug_log(string.format("scan/finish OK - %d items, %d fluids, %d errors, %d chunks", totalItems, totalFluids, totalErrors, chunksSent))

  return true, { total_items = totalItems, total_fluids = totalFluids, total_errors = totalErrors, batches = batchNum, chunks_sent = chunksSent }
end


local function draw_status(lastResult, lastError, nextScanIn)
  term.clear()
  term.setCursor(1, 1)
  print("=== Network Browser ===")
  print("Last scan:    " .. os.date("%H:%M:%S"))
  if lastError then
    print("Last result:  FAILED - " .. tostring(lastError))
  elseif lastResult then
    print(string.format("Last result:  %d items, %d fluids, %d batch errors, %d batches",
      lastResult.total_items, lastResult.total_fluids or 0, lastResult.total_errors, lastResult.batches))
  else
    print("Last result:  (scanning...)")
  end
  print("Free memory:  " .. tostring(safe_free_memory()))
  print("")
  print("Next scan in " .. tostring(nextScanIn) .. "s. 'rc network_browser stop' to stop.")
end

local running = false

-- Sleeps in short increments rather than one long os.sleep(), so
-- stop() (which just flips `running`) takes effect within a few
-- seconds instead of potentially waiting out the entire interval -
-- see the comment at this function's call site for why that matters
-- much more here than in the other two scripts.
local function sleep_while_running(totalSeconds)
  local checkInterval = 5
  local remaining = totalSeconds
  while remaining > 0 and running do
    os.sleep(math.min(checkInterval, remaining))
    remaining = remaining - checkInterval
  end
end

local function service_loop()
  local me, kind = find_me_component()
  if not me then
    print("[network_browser] No me_controller / me_interface found. Check Adapter placement.")
    running = false
    return
  end
  if not me.getItemsInNetworkById then
    print("[network_browser] No getItemsInNetworkById() method on this component.")
    running = false
    return
  end
  if not component.isAvailable("internet") then
    print("[network_browser] No Internet Card installed.")
    running = false
    return
  end

  print("[network_browser] Using " .. kind .. ". Scanning every " .. CONFIG.SCAN_INTERVAL_SECONDS .. "s.")

  while running do
    -- No placeholder screen here - run_scan()'s own set_phase() draws
    -- the live "Calling scan/start..." state almost immediately, so a
    -- separate static "Scanning..." print would just flash and vanish.
    local ok, result = run_scan(me)

    if CONFIG.SHOW_STATUS then
      -- Deliberately NOT "ok and X or Y" here - that idiom breaks when
      -- the intended true-branch value is itself falsy, which nil is.
      -- (That's exactly what caused a SUCCESSFUL scan to print "FAILED"
      -- with the raw success table's address - lastError inherited the
      -- success result instead of nil, since "ok and nil" is always nil
      -- regardless of ok, and "nil or result" always falls through.)
      if ok then
        draw_status(result, nil, CONFIG.SCAN_INTERVAL_SECONDS)
      else
        draw_status(nil, result, CONFIG.SCAN_INTERVAL_SECONDS)
      end
    else
      if ok then
        print(string.format("[network_browser] Scan done: %d items, %d fluids, %d batch errors",
          result.total_items, result.total_fluids or 0, result.total_errors))
      else
        print("[network_browser] Scan failed: " .. tostring(result))
      end
    end

    -- stop() may have been called WHILE that scan was running - honor
    -- it now, before starting another wait/scan, rather than trying to
    -- interrupt a scan already in flight (which would need per-batch
    -- flag-checking deep inside run_scan(), and would risk leaving the
    -- server's in_progress flag stuck at true forever since scan/finish
    -- would never get called).
    if not running then break end

    -- A single os.sleep(SCAN_INTERVAL_SECONDS) here would mean stop()
    -- could silently take up to the full default interval to actually
    -- take effect, if called right after a scan just finished - a real,
    -- noticeable problem at this scale (unlike craft_monitor's 5s or
    -- power_monitor's 60s, where the same approach would be an
    -- unnoticeable delay). Sleeping in short increments and re-checking
    -- `running` between each means `rc network_browser stop` takes
    -- effect within a few seconds instead.
    sleep_while_running(CONFIG.SCAN_INTERVAL_SECONDS)
  end
  print("[network_browser] Stopped.")
end

-- rc calls these as plain global functions (no local scope - loaded in
-- a sandboxed environment per OC's own rc documentation) - NOT a
-- returned table the way OpenOS library modules normally work. The rc
-- system doesn't wait for start() to finish or care what it returns, so
-- start() has to hand the actual work off to its own thread and return
-- quickly - a bare while-true loop directly in start() would hang
-- `rc network_browser start` itself forever, since the function call
-- would never return control back to the caller.
function start()
  if running then
    print("[network_browser] Already running.")
    return
  end
  running = true
  thread.create(service_loop):detach()
end

-- Optional per OC's rc docs, but defined here so `rc network_browser
-- restart` gets its default stop-then-start behavior for free. Just
-- flips a flag the loop itself checks - not a thread kill, matching the
-- lesson already learned elsewhere in this project about being cautious
-- with anything thread-lifecycle-related.
function stop()
  running = false
end

function status()
  print("[network_browser] " .. (running and "running" or "stopped"))
end
