-- Assertions and a scripted HTTP server for the oc/tests/*_test.lua
-- suites (run by oc/tests/run.lua).

local oc_env = require("oc_env")

local M = {}
local json = oc_env.json

-- ----------------------------------------------------------- assertions

local function describe(v)
  if type(v) == "table" then return oc_env.serialize(v) end
  if type(v) == "string" then return string.format("%q", v) end
  return tostring(v)
end

local function deep_eq(a, b)
  if a == b then return true end
  if type(a) ~= "table" or type(b) ~= "table" then return false end
  for k, v in pairs(a) do
    if not deep_eq(v, b[k]) then return false end
  end
  for k in pairs(b) do
    if a[k] == nil then return false end
  end
  return true
end

function M.eq(actual, expected, msg)
  if not deep_eq(actual, expected) then
    error((msg and (msg .. ": ") or "") .. "expected " .. describe(expected) .. ", got " .. describe(actual), 2)
  end
end

function M.truthy(v, msg)
  if not v then error(msg or ("expected a truthy value, got " .. describe(v)), 2) end
  return v
end

function M.near(actual, expected, tolerance, msg)
  if type(actual) ~= "number" or math.abs(actual - expected) > tolerance then
    error((msg and (msg .. ": ") or "") .. "expected " .. expected .. " +/- " .. tolerance .. ", got " .. describe(actual), 2)
  end
end

-- ------------------------------------------------------------------ HTTP

-- A fake server for env.http_handler. Routes are "METHOD /path" (path
-- is a Lua pattern, anchored) -> function(req) returning a response
-- table (see oc_env Env:_respond), or a table to send as JSON. Anything
-- unrouted gets {"ok": true}.
function M.server(routes)
  local server = { routes = routes or {} }
  function server.handle(req)
    for key, handler in pairs(server.routes) do
      local method, pattern = key:match("^(%u+) (.+)$")
      if method == req.method and req.path:match("^" .. pattern .. "$") then
        local res = type(handler) == "function" and handler(req) or handler
        if res and res.status == nil and res.body == nil and res.chunks == nil
            and res.connect_error == nil then
          return { status = 200, body = json.json_encode(res) }
        end
        return res
      end
    end
    return { status = 200, body = '{"ok":true}' }
  end
  return server
end

-- An env wired to a fresh M.server, with config.lua written.
function M.env(opts)
  opts = opts or {}
  local server = M.server(opts.routes)
  local env = oc_env.new({ http_handler = server.handle, internet = opts.internet })
  env.server = server
  local cfg = { SERVER_URL = "http://gcm.test", API_KEY = "test-key" }
  for k, v in pairs(opts.config or {}) do cfg[k] = v end
  env:write_config(cfg)
  return env
end

-- --------------------------------------------------------------- suites

function M.suite()
  local suite = { tests = {} }
  function suite.test(name, fn)
    suite.tests[#suite.tests + 1] = { name = name, fn = fn }
  end
  return suite
end

return M
