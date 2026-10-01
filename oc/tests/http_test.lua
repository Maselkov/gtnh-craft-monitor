local t = require("testlib")

local suite = t.suite()
local test = suite.test

local function http_lib(routes)
  local env = t.env({ routes = routes })
  return env, env.globals.require("http")
end

test("request_with_timeout returns the whole body", function()
  local body = string.rep("abcdefghij", 500)
  local env, http = http_lib({ ["POST /x"] = { status = 200, body = body } })
  local ok, result = http.request_with_timeout("http://gcm.test/x", "{}", {}, 20)
  t.eq(ok, true)
  t.eq(result, body)
  t.eq(env.requests[1].method, "POST")
end)

test("request_with_timeout gives up when chunks stop coming", function()
  local env, http = http_lib({
    ["GET /slow"] = { status = 200, chunks = { "a", "b", "c" }, chunk_delay = 25 },
  })
  local ok, err = http.request_with_timeout("http://gcm.test/slow", nil, {}, 20)
  t.eq(ok, false)
  t.truthy(tostring(err):find("timed out after 20s", 1, true), err)
  t.near(env.now, 25, 0.5, "noticed at the first late chunk")
end)

test("request_with_timeout reports a connection failure", function()
  local _, http = http_lib({ ["GET /down"] = { connect_error = "connection refused" } })
  local ok, err = http.request_with_timeout("http://gcm.test/down", nil, {}, 20)
  t.eq(ok, false)
  t.truthy(tostring(err):find("connection refused", 1, true), err)
end)

test("download_to_file writes the body", function()
  local env, http = http_lib({ ["GET /file"] = { status = 200, body = "line1\nline2\n", connect_delay = 0.3 } })
  t.eq(http.download_to_file("http://gcm.test/file", "/home/out.txt", {}, 20), true)
  t.eq(env:read_file("/home/out.txt"), "line1\nline2\n")
end)

test("download_to_file refuses an error page", function()
  local env, http = http_lib({ ["GET /file"] = { status = 404, body = "not found" } })
  local ok, err = http.download_to_file("http://gcm.test/file", "/home/out.txt", {}, 20)
  t.eq(ok, false)
  t.eq(err, "HTTP 404")
  t.eq(env:exists("/home/out.txt"), false)
end)

test("download_to_file times out on a connection that never comes up", function()
  local _, http = http_lib({ ["GET /file"] = { status = 200, body = "x", connect_delay = 1000 } })
  t.eq(select(2, http.download_to_file("http://gcm.test/file", "/home/out.txt", {}, 20)), "timed out connecting")
end)

test("download_to_file times out when data stops coming", function()
  local _, http = http_lib({ ["GET /file"] = { status = 200, chunks = { "a", "b" }, stall_after = 1 } })
  t.eq(select(2, http.download_to_file("http://gcm.test/file", "/home/out.txt", {}, 20)), "timed out waiting for data")
end)

return suite
