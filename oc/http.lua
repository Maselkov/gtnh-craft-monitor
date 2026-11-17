-- http.lua - HTTP request-with-timeout helper, shared across
-- craft_monitor.lua, power_monitor.lua, and network_browser.lua.
--
-- Deploy to /usr/lib/http.lua and load via require("http") - a real
-- library, not dofile()'d by absolute path like config.lua is. This
-- works reliably even under rc's sandboxed loader for the same reason
-- json.lua does (see that file's own header for the full reasoning):
-- OpenOS's own package.path includes /usr/lib/?.lua as a fixed,
-- ABSOLUTE entry, not resolved relative to wherever the calling script
-- happens to live - a genuinely different thing from the directory-
-- relative search that's uncertain under rc, which is why config.lua
-- still uses dofile() instead.
--
-- Previously bundled into shared_config.lua alongside SERVER_URL/
-- API_KEY - split out because this was the majority of that file (72
-- of its 99 lines) and is entirely independent of those two config
-- values (request_with_timeout takes url/headers as PARAMETERS, never
-- referencing config internally at all) - a file called "config"
-- holding this much real transport logic was the wrong shape.

local component = require("component")
local computer = require("computer")

-- IMPORTANT - do not "improve" this by wrapping it in thread.create() +
-- thread.waitForAny() again. That was tried, and reverted: it's a
-- CONFIRMED, still-open OpenComputers bug (MightyPirates/OpenComputers
-- #3580, "Memory leak in thread API") - repeatedly creating threads,
-- even ones that finish completely normally, leaks memory and thread
-- count without bound. network_browser.lua alone creates 600+ requests
-- per scan, every 20 minutes - directly hitting that leak hundreds of
-- times an hour. It was confirmed as the actual cause of a real,
-- reported symptom: the computer running these scripts became
-- progressively laggier over time, NOT fixed by restarting the
-- computer (computer state isn't where the leak lives), only fixed by
-- restarting the whole Minecraft server (a fresh JVM clears it).
-- No thread is needed anyway: both functions below poll the Internet
-- Card's request handle themselves, sleeping a tick between empty reads,
-- so they can give up at any point - before the connection is up, before
-- the first chunk, or between chunks.
--
-- Elapsed time is measured with computer.uptime(), NOT os.date(). An
-- earlier version of this used os.date("*t") based on what looked like
-- real-time-correlated timestamps in a captured hang's debug log - that
-- was wrong. os.date() reflects Minecraft's in-game day-night cycle
-- (24000 ticks = 20 REAL minutes at normal TPS - confirmed from OC's
-- own wiki, and confirmed by the numbers: dividing that earlier log's
-- os.date()-derived gaps by 72 matches computer.uptime()'s gaps almost
-- exactly), meaning it runs 72x faster than real time by design - not
-- slower, and not neutral. At 72x speed, "20 seconds" on that clock
-- passes after well under half a real second, which is exactly the bug
-- this caused: a timeout firing near-instantly on every single call,
-- confirmed directly (reported as "timed out after 20 seconds" when
-- only ~1 real second had actually passed). computer.uptime() is
-- monotonic (no rollover math needed either) and tracks real elapsed
-- time correctly under normal conditions - it can still stall during
-- genuine severe TPS lag (the scenario the original in-loop check was
-- built for), which remains an accepted, known gap rather than
-- something this can fully close - but it does not fire falsely during
-- ordinary operation, which a 72x-compressed clock provably does.
local function real_seconds_elapsed(startSeconds)
  return computer.uptime() - startSeconds
end

-- How often to ask again for a connection while the card is full.
local CONNECTION_RETRY_SECONDS = 0.25

-- The Internet Card's handle for a new request, or nil and why not.
-- A card holds only a few connections at once (OC's maxTcpConnections,
-- 4 by default), counted across every script on the computer, and
-- refuses another with "too many open connections". When the scripts'
-- requests overlap, a slot frees as soon as one of them finishes, so
-- this asks again until timeoutSeconds have passed instead of failing
-- the request - a lost batch fails a whole network scan.
local function open_request(url, body, headers, timeoutSeconds)
  local started = computer.uptime()
  while true do
    local ok, handle, reason = pcall(function()
      return component.internet.request(url, body, headers)
    end)
    if ok and handle then
      return handle
    end
    local err = tostring(ok and reason or handle)
    if not err:find("too many open connections", 1, true) then
      return nil, err
    end
    local waited = real_seconds_elapsed(started)
    if waited >= timeoutSeconds then
      return nil, err .. string.format(" (waited %.0fs)", waited)
    end
    os.sleep(CONNECTION_RETRY_SECONDS)
  end
end

-- Sends a request (a POST with body, a GET without) and reads the whole
-- response: true and the body, or false and an error message. Gives up
-- once nothing has arrived for timeoutSeconds, whether the connection
-- never comes up, the server never answers, or the body stops partway -
-- a connection that goes silent can't hold the script forever. A card
-- with no free connection is waited on for up to timeoutSeconds too. Not
-- OpenOS's internet.request(): its iterator sleeps in its own loop until
-- data comes, so nothing can time out a request that never sends any.
-- As with that iterator, an error status fails the read (the card raises
-- it, body unread), so a 4xx or 5xx comes back as false and the card's
-- message.
local function request_with_timeout(url, body, headers, timeoutSeconds)
  local handle, requestErr = open_request(url, body, headers, timeoutSeconds)
  if not handle then
    return false, requestErr
  end
  local chunks = {}
  local lastData = computer.uptime()
  while true do
    local okRead, data, readErr = pcall(handle.read)
    if not okRead or data == nil then
      handle.close()
      if not okRead or readErr then
        return false, tostring(okRead and readErr or data)
      end
      return true, table.concat(chunks)
    end
    if #data > 0 then
      chunks[#chunks + 1] = data
      lastData = computer.uptime()
    elseif real_seconds_elapsed(lastData) > timeoutSeconds then
      -- Closed, or the card keeps the connection - it allows only a few.
      handle.close()
      return false, "timed out after " .. timeoutSeconds .. "s waiting for "
        .. (#chunks == 0 and "a response" or "the rest of the response")
    else
      os.sleep(0.05)
    end
  end
end

-- Downloads url into the file at path, writing each chunk as it arrives
-- instead of building the body in memory - for the item catalog (~330KB),
-- which a low-memory computer can't hold as one string. Unlike the
-- internet library's request iterator, this checks the HTTP status, so an
-- error page is never saved as if it were the file. Same approach as
-- gcm.lua's download(). Gives up if connecting, or any gap between
-- chunks, takes longer than timeoutSeconds. Returns true, or false and an
-- error message (the file at path may then be partial).
local function download_to_file(url, path, headers, timeoutSeconds)
  local handle, requestErr = open_request(url, nil, headers, timeoutSeconds)
  if not handle then
    return false, requestErr
  end
  local started = computer.uptime()
  local code
  repeat
    local okConnect, connected, err = pcall(handle.finishConnect)
    if not okConnect or connected == nil then
      handle.close()
      return false, tostring(okConnect and err or connected)
    end
    if connected then
      code = handle.response()
    end
    if not code then
      if real_seconds_elapsed(started) > timeoutSeconds then
        handle.close()
        return false, "timed out connecting"
      end
      os.sleep(0.05)
    end
  until code
  if code ~= 200 then
    handle.close()
    return false, "HTTP " .. tostring(code)
  end

  local file, openErr = io.open(path, "wb")
  if not file then
    handle.close()
    return false, tostring(openErr)
  end
  local total = 0
  local lastData = computer.uptime()
  while true do
    local okRead, data, readErr = pcall(handle.read, 8192)
    if not okRead or data == nil then
      handle.close()
      file:close()
      if not okRead or readErr then
        return false, tostring(okRead and readErr or data)
      end
      if total == 0 then
        return false, "empty response"
      end
      return true
    end
    if #data > 0 then
      file:write(data)
      total = total + #data
      lastData = computer.uptime()
    elseif real_seconds_elapsed(lastData) > timeoutSeconds then
      handle.close()
      file:close()
      return false, "timed out waiting for data"
    else
      os.sleep(0.05)
    end
  end
end

return {
  request_with_timeout = request_with_timeout,
  download_to_file = download_to_file,
}
