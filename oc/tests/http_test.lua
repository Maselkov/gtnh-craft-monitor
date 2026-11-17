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
  t.eq(err, "timed out after 20s waiting for the rest of the response")
  t.near(env.now, 20, 0.5, "gives up once the gap reaches the timeout, not at the late chunk")
  t.eq(env.open_connections, 0, "the connection is let go")
end)

test("request_with_timeout gives up on a connection that never comes up", function()
  local env, http = http_lib({ ["GET /x"] = { status = 200, body = "x", connect_delay = 1000 } })
  local ok, err = http.request_with_timeout("http://gcm.test/x", nil, {}, 20)
  t.eq(ok, false)
  t.eq(err, "timed out after 20s waiting for a response")
  t.near(env.now, 20, 0.5)
  t.eq(env.open_connections, 0, "the connection is let go")
end)

test("request_with_timeout gives up on a server that never sends anything", function()
  local env, http = http_lib({ ["POST /x"] = { status = 200, chunks = { "a" }, stall_after = 0 } })
  local ok, err = http.request_with_timeout("http://gcm.test/x", "{}", {}, 20)
  t.eq(ok, false)
  t.eq(err, "timed out after 20s waiting for a response")
  t.near(env.now, 20, 0.5)
end)

test("request_with_timeout waits out a slow body that keeps coming", function()
  local env, http = http_lib({
    ["GET /slow"] = { status = 200, chunks = { "a", "b", "c", "d" }, chunk_delay = 15 },
  })
  local ok, body = http.request_with_timeout("http://gcm.test/slow", nil, {}, 20)
  t.eq(ok, true)
  t.eq(body, "abcd")
  -- The fake card ends the body one chunk_delay after the last chunk.
  t.near(env.now, 60, 0.5, "three times the timeout in all, but no gap was as long")
  t.eq(env.open_connections, 0)
end)

test("request_with_timeout reports a connection failure", function()
  local env, http = http_lib({ ["GET /down"] = { connect_error = "connection refused" } })
  local ok, err = http.request_with_timeout("http://gcm.test/down", nil, {}, 20)
  t.eq(ok, false)
  t.truthy(tostring(err):find("connection refused", 1, true), err)
  t.eq(env.open_connections, 0)
end)

test("request_with_timeout fails on an error status, as the card does", function()
  local env, http = http_lib({ ["POST /x"] = { status = 500, body = "boom" } })
  local ok, err = http.request_with_timeout("http://gcm.test/x", "{}", {}, 20)
  t.eq(ok, false)
  t.truthy(tostring(err):find("HTTP response code: 500", 1, true), err)
  t.eq(env.open_connections, 0)
end)

test("request_with_timeout fails without an Internet Card", function()
  local env = t.env({ internet = false })
  local http = env.globals.require("http")
  local ok, err = http.request_with_timeout("http://gcm.test/x", nil, {}, 20)
  t.eq(ok, false)
  t.truthy(err)
end)

-- Takes every connection the card has, as other scripts on the computer
-- would; returns the handles.
local function fill_card(env)
  local internet = env.globals.require("component").internet
  local held = {}
  for i = 1, env.max_connections do
    held[i] = internet.request("http://gcm.test/other", "{}")
  end
  return held
end

test("request_with_timeout waits for a connection when the card is full", function()
  local env, http = http_lib({ ["POST /x"] = { status = 200, body = "done" } })
  local held = fill_card(env)
  env:spawn(function()
    env:sleep(3)
    held[1].close()
  end, "other script")
  local ok, body
  env:spawn(function()
    ok, body = http.request_with_timeout("http://gcm.test/x", "{}", {}, 20)
  end, "caller")
  env:run()
  t.eq(ok, true, body)
  t.eq(body, "done")
  t.near(env.requests[#env.requests].at, 3, 0.3, "sent once a connection was free")
end)

test("request_with_timeout gives up on a card that stays full", function()
  local env, http = http_lib({ ["POST /x"] = { status = 200, body = "done" } })
  fill_card(env)
  local ok, err = http.request_with_timeout("http://gcm.test/x", "{}", {}, 20)
  t.eq(ok, false)
  t.truthy(err:find("too many open connections", 1, true), err)
  t.truthy(err:find("(waited 20s)", 1, true), err)
  t.near(env.now, 20, 0.5)
  t.eq(#env:requests_to("/x"), 0, "never sent")
  t.eq(env.open_connections, env.max_connections, "took no connection of its own")
end)

test("download_to_file waits for a connection when the card is full", function()
  local env, http = http_lib({ ["GET /file"] = { status = 200, body = "data" } })
  local held = fill_card(env)
  env:spawn(function()
    env:sleep(2)
    held[2].close()
  end, "other script")
  local ok
  env:spawn(function()
    ok = http.download_to_file("http://gcm.test/file", "/home/out.txt", {}, 20)
  end, "caller")
  env:run()
  t.eq(ok, true)
  t.eq(env:read_file("/home/out.txt"), "data")
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
