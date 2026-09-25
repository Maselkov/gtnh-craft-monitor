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

local internet = require("internet")
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
-- Whatever gap this leaves - a request that never receives even its
-- first chunk can't be timed out this way, only a stall BETWEEN chunks
-- already arriving can - is a far better trade than a leak that
-- degrades the entire server over hours, not just this one script.
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

-- Reads a full response body from an internet.request() iterator, but
-- bails out with an error if too much time passes between two
-- successfully-received chunks - rather than the loop just sitting
-- there indefinitely if the connection stalls partway through. This can
-- only catch a stall AFTER at least one chunk has already arrived - see
-- the note above for why that known gap is accepted rather than closed
-- with a thread-based approach.
local function read_response_with_timeout(req, timeoutSeconds)
  local startSeconds = computer.uptime()
  local chunks = {}
  for chunk in req do
    chunks[#chunks + 1] = chunk
    if real_seconds_elapsed(startSeconds) > timeoutSeconds then
      error("timed out after " .. timeoutSeconds .. "s waiting for the rest of the response")
    end
  end
  return table.concat(chunks)
end

-- Thin wrapper matching the OLD request_with_timeout(url, body, headers,
-- timeout) call shape, so the three scripts didn't need every call site
-- rewritten again on top of everything else that changed reverting this.
local function request_with_timeout(url, body, headers, timeoutSeconds)
  local ok, result = pcall(function()
    local req = internet.request(url, body, headers)
    return read_response_with_timeout(req, timeoutSeconds)
  end)
  return ok, result
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
  local ok, handle, reason = pcall(component.internet.request, url, nil, headers)
  if not ok or not handle then
    return false, tostring(ok and reason or handle)
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
