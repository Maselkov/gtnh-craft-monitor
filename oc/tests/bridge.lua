--[[
  The Lua end of server/tests/oc_game.py: a harness computer driven over
  stdin/stdout, whose HTTP requests are answered by the other end (the
  real Flask app, in pytest).

  Every message is a header line, then exactly as many raw bytes as the
  header says.

  The other end sends a command: "<n>\n" + n bytes of Lua. It runs with
  env (the oc_env computer), ae2, gt and json in scope; globals it sets
  stay set for later commands. This end answers
    "RESULT <n>\n" + its return value as JSON, or
    "ERROR <n>\n" + the error with traceback.
  While the command runs, each HTTP request a script makes is sent as
    "HTTP <m> <n>\n" + m bytes of JSON {method, url, headers} + n body
    bytes (n = -1 and no bytes for a GET)
  and waits for "<status> <n>\n" + n bytes of response body.
--]]

local dir = arg[0]:match("^(.*)/[^/]*$") or "."
package.path = dir .. "/harness/?.lua;" .. package.path

local oc_env = require("oc_env")
local json = oc_env.json

local stdin, stdout = io.stdin, io.stdout

local function send(header, ...)
  stdout:write(header, "\n", ...)
  stdout:flush()
end

local function read_exactly(n)
  if n <= 0 then return "" end
  local data = stdin:read(n)
  if not data or #data ~= n then error("bridge: stdin closed mid-message", 0) end
  return data
end

local function http_handler(req)
  local meta = json.json_encode({ method = req.method, url = req.url, headers = req.headers })
  local body = req.body or ""
  send("HTTP " .. #meta .. " " .. (req.body and #body or -1), meta, body)
  local line = stdin:read("l")
  if not line then error("bridge: stdin closed waiting for a response", 0) end
  local status, n = line:match("^(%d+) (%d+)$")
  if not status then error("bridge: bad response header " .. line, 0) end
  return { status = tonumber(status), body = read_exactly(tonumber(n)) }
end

local env = oc_env.new({ http_handler = http_handler })
local scope = setmetatable({
  env = env,
  ae2 = require("fake_ae2"),
  gt = require("fake_gt"),
  json = json,
}, { __index = _G })

while true do
  local line = stdin:read("l")
  if not line then break end
  local code = read_exactly(tonumber(line))
  local ok, result = xpcall(function()
    local fn = assert(load(code, "=command", "t", scope))
    return json.json_encode(fn())
  end, debug.traceback)
  if ok then
    send("RESULT " .. #result, result)
  else
    result = tostring(result)
    send("ERROR " .. #result, result)
  end
end

env:close()
