--[[
  A fake OpenComputers computer for running the real oc/*.lua scripts
  outside the game.

  What it fakes: the OpenOS libraries the scripts require (component,
  computer, internet, thread, event, term, filesystem, serialization,
  shell), plus os.sleep, print, and io/dofile/loadfile pointed at a
  temp directory standing in for the computer's disk.

  Time is virtual. thread.create() makes a coroutine; os.sleep() yields
  it until the virtual clock reaches its wake time, and computer.uptime()
  reads that clock. env:run() resumes whichever thread wakes first, so
  a script that sleeps 600s between scans runs instantly and the same
  way every time. Every os.sleep() takes at least one game tick (0.05s),
  as in the game, so a loop that sleeps 0 still lets time move.

  HTTP goes through a fake Internet Card to env.http_handler(req), which
  returns { status, body } (see Env:_respond for the rest). The real
  http.lua and json.lua are loaded on top of it, so those are under test
  too.

  Known gaps: component calls are instant (in the game each waits a
  tick), memory is unlimited, and the AE2/GT fakes in this directory
  only know the API as the notes in craft_monitor.lua describe it.
--]]

local M = {}

local HARNESS_DIR = debug.getinfo(1, "S").source:match("^@(.*)/[^/]*$") or "."
M.OC_DIR = HARNESS_DIR .. "/../.."

local TICK = 0.05

local function shell_quote(s)
  return "'" .. s:gsub("'", "'\\''") .. "'"
end

local function load_plain(path)
  return assert(loadfile(path, "t", setmetatable({}, { __index = _G })))()
end

-- The repo's own json.lua, for tests and the bridge to read bodies with.
M.json = load_plain(M.OC_DIR .. "/json.lua")

local Env = {}
Env.__index = Env

-- Every env not yet closed, so a test runner can clean up after a test
-- that failed before closing its own.
local live = {}

function M.new(opts)
  opts = opts or {}
  local root = os.tmpname()
  os.remove(root)
  assert(os.execute("mkdir -p " .. shell_quote(root)))
  local env = setmetatable({
    root = root,
    now = opts.start_time or 0,
    threads = {},
    thread_seq = 0,
    current = nil,
    requests = {},
    output = {},
    shell_log = {},
    thread_errors = {},
    echo = opts.echo or os.getenv("OC_HARNESS_ECHO") ~= nil,
    free_memory = opts.free_memory or 2 * 1024 * 1024,
    components = {},
    address_seq = 0,
    http_handler = opts.http_handler,
  }, Env)
  for _, dir in ipairs({ "/home", "/usr/lib", "/etc/rc.d", "/tmp" }) do
    env:mkdir(dir)
  end
  env:_build_globals()
  if opts.internet ~= false then
    env:add_component("internet", env:_internet_card())
  end
  live[env] = true
  return env
end

function M.close_all()
  for env in pairs(live) do env:close() end
end

function Env:close()
  if self.root then
    os.execute("rm -rf " .. shell_quote(self.root))
    self.root = nil
  end
  live[self] = nil
end

-- ------------------------------------------------------------------ disk

function Env:path(p)
  if p:sub(1, 1) ~= "/" then p = "/" .. p end
  return self.root .. p
end

function Env:mkdir(p)
  assert(os.execute("mkdir -p " .. shell_quote(self:path(p))))
end

function Env:write_file(p, content)
  local f = assert(io.open(self:path(p), "wb"))
  f:write(content)
  f:close()
end

function Env:read_file(p)
  local f = io.open(self:path(p), "rb")
  if not f then return nil end
  local content = f:read("a")
  f:close()
  return content
end

function Env:exists(p)
  return os.rename(self:path(p), self:path(p)) and true or false
end

-- Writes /home/config.lua, where the scripts dofile() it from.
function Env:write_config(cfg)
  self:write_file("/home/config.lua", "return " .. M.serialize(cfg))
end

-- ----------------------------------------------------------- scheduling

function Env:spawn(fn, name)
  self.thread_seq = self.thread_seq + 1
  local t = {
    seq = self.thread_seq,
    name = name or ("thread " .. self.thread_seq),
    wake = self.now,
  }
  t.co = coroutine.create(function()
    local ok, err = xpcall(fn, debug.traceback)
    if not ok then error(err, 0) end
  end)
  self.threads[#self.threads + 1] = t
  return t
end

function Env:sleep(seconds)
  seconds = math.max(tonumber(seconds) or 0, TICK)
  local t = self.current
  if t and coroutine.running() == t.co then
    coroutine.yield(seconds)
  else
    -- Outside any thread (a script's top level, or a test calling
    -- in directly): nothing else can run meanwhile, so just let time pass.
    self.now = self.now + seconds
  end
end

function Env:alive()
  local n = 0
  for _, t in ipairs(self.threads) do
    if not t.dead then n = n + 1 end
  end
  return n
end

-- Runs threads until one of:
--   seconds / until_time - the virtual clock would pass it
--   until_fn             - returns true (checked between resumes)
--   every thread has finished
-- Returns true when until_fn was satisfied. A thread that errors raises
-- here with its traceback unless allow_errors is set (then it's recorded
-- in env.thread_errors, the way OpenOS logs a dead detached thread).
function Env:run(o)
  o = o or {}
  local deadline = o.until_time or (o.seconds and self.now + o.seconds) or math.huge
  local max_steps = o.max_steps or 1000000
  for _ = 1, max_steps do
    if o.until_fn and o.until_fn() then return true end
    local next_t
    for _, t in ipairs(self.threads) do
      if not t.dead and (not next_t or t.wake < next_t.wake) then next_t = t end
    end
    if not next_t then return false end
    if next_t.wake > deadline then
      self.now = math.max(self.now, deadline)
      return false
    end
    self.now = math.max(self.now, next_t.wake)
    self.current = next_t
    local ok, slept = coroutine.resume(next_t.co)
    self.current = nil
    if not ok then
      next_t.dead = true
      self.thread_errors[#self.thread_errors + 1] = { thread = next_t.name, error = slept }
      if not o.allow_errors then
        error("[" .. next_t.name .. "] " .. tostring(slept), 0)
      end
    elseif coroutine.status(next_t.co) == "dead" then
      next_t.dead = true
    else
      next_t.wake = self.now + (slept or TICK)
    end
  end
  error("env:run() gave up after " .. max_steps .. " steps - a thread is spinning without sleeping")
end

-- ----------------------------------------------------------- components

-- methods: a table of plain functions, called the OC way (proxy.fn(),
-- no self). Returns the proxy.
function Env:add_component(ctype, methods, address)
  self.address_seq = self.address_seq + 1
  address = address or string.format("%08x-0000-4000-8000-%012x", self.address_seq, self.address_seq)
  local proxy = { type = ctype, address = address }
  for k, v in pairs(methods) do proxy[k] = v end
  self.components[#self.components + 1] = { type = ctype, address = address, proxy = proxy }
  return proxy
end

function Env:remove_component(address)
  for i, c in ipairs(self.components) do
    if c.address == address then
      table.remove(self.components, i)
      return true
    end
  end
  return false
end

function Env:_primary(ctype)
  for _, c in ipairs(self.components) do
    if c.type == ctype then return c.proxy end
  end
  return nil
end

-- ----------------------------------------------------------------- HTTP

local function url_path(url)
  return url:match("^%a[%w+.-]*://[^/]+(/.*)$") or url:match("^%a[%w+.-]*://[^/]+$") and "/" or url
end

-- What a handler may return:
--   status        HTTP status (default 200)
--   body          response body (default "")
--   chunks        the body pre-split, instead of body (default: 1KB pieces)
--   chunk_delay   seconds between chunks arriving
--   stall_after   stop sending after this many chunks, and never finish
--   connect_delay seconds before the connection is up
--   connect_error connection fails with this message
-- A handler that errors makes the request fail to connect with that error.
function Env:_respond(req)
  self.requests[#self.requests + 1] = req
  if not self.http_handler then
    return { connect_error = "no http_handler set: " .. req.method .. " " .. req.url }
  end
  local ok, res = pcall(self.http_handler, req)
  if not ok then return { connect_error = tostring(res) } end
  return res or { status = 200, body = "" }
end

function Env:_internet_card()
  local env = self
  return {
    isHttpEnabled = function() return true end,
    isTcpEnabled = function() return false end,
    request = function(url, body, headers, method)
      local req = {
        method = method or (body and "POST" or "GET"),
        url = url,
        path = url_path(url),
        body = body,
        headers = headers or {},
        at = env.now,
      }
      if body then req.json = M.json.json_decode(body) end
      local res = env:_respond(req)
      local chunks = res.chunks
      if not chunks then
        chunks = {}
        local b = res.body or ""
        for i = 1, #b, 1024 do chunks[#chunks + 1] = b:sub(i, i + 1023) end
      end
      local connect_at = env.now + (res.connect_delay or 0)
      local next_chunk, next_at = 1, connect_at
      local closed = false
      local handle = {}
      function handle.finishConnect()
        if res.connect_error then return nil, res.connect_error end
        return env.now >= connect_at
      end
      function handle.response()
        if res.connect_error or env.now < connect_at then return nil end
        return res.status or 200, "", res.headers or {}
      end
      function handle.read()
        if closed then return nil, "connection closed" end
        if res.connect_error then return nil, res.connect_error end
        if env.now < next_at then return "" end
        if res.stall_after and next_chunk > res.stall_after then return "" end
        if next_chunk > #chunks then return nil end
        local data = chunks[next_chunk]
        next_chunk = next_chunk + 1
        next_at = env.now + (res.chunk_delay or 0)
        return data
      end
      function handle.close() closed = true end
      return handle
    end,
  }
end

-- ------------------------------------------------------- OpenOS globals

function M.serialize(v, pretty, indent)
  indent = indent or ""
  local t = type(v)
  if t == "string" then return string.format("%q", v)
  elseif t == "number" or t == "boolean" or t == "nil" then return tostring(v)
  elseif t ~= "table" then return string.format("%q", tostring(v)) end
  local keys = {}
  for k in pairs(v) do keys[#keys + 1] = k end
  table.sort(keys, function(a, b)
    if type(a) == type(b) and (type(a) == "number" or type(a) == "string") then return a < b end
    return type(a) < type(b)
  end)
  local inner = pretty and (indent .. "  ") or ""
  local parts = {}
  for _, k in ipairs(keys) do
    local key = (type(k) == "string" and k:match("^[%a_][%w_]*$")) and k or ("[" .. M.serialize(k) .. "]")
    parts[#parts + 1] = inner .. key .. "=" .. M.serialize(v[k], pretty, inner)
  end
  if pretty and #parts > 0 then
    return "{\n" .. table.concat(parts, ",\n") .. "\n" .. indent .. "}"
  end
  return "{" .. table.concat(parts, ",") .. "}"
end

function Env:_print(...)
  local parts = {}
  for i = 1, select("#", ...) do parts[i] = tostring((select(i, ...))) end
  local line = table.concat(parts, "\t")
  self.output[#self.output + 1] = line
  if self.echo then io.stderr:write("[oc t=" .. self.now .. "] " .. line .. "\n") end
end

-- Whether any printed line contains text (plain match).
function Env:printed(text)
  for _, line in ipairs(self.output) do
    if line:find(text, 1, true) then return true end
  end
  return false
end

function Env:_build_globals()
  local env = self
  local base = {}
  for _, name in ipairs({
    "assert", "error", "getmetatable", "ipairs", "next", "pairs", "pcall", "rawequal",
    "rawget", "rawlen", "rawset", "select", "setmetatable", "tonumber", "tostring",
    "type", "xpcall", "string", "table", "math", "coroutine", "utf8",
  }) do
    base[name] = _G[name]
  end
  base._G = base
  base._VERSION = _VERSION
  base._OSVERSION = "OpenOS 1.8.3 (harness)"
  base.print = function(...) env:_print(...) end
  base.checkArg = function(n, have, ...)
    have = type(have)
    for i = 1, select("#", ...) do
      if have == select(i, ...) then return end
    end
    error(string.format("bad argument #%d (%s expected, got %s)", n, table.concat({ ... }, " or "), have), 3)
  end

  local real_load = load
  base.load = function(chunk, name, mode, e)
    return real_load(chunk, name, mode or "t", e == nil and base or e)
  end
  base.loadfile = function(p, mode, e)
    return loadfile(env:path(p), mode or "t", e == nil and base or e)
  end
  base.dofile = function(p)
    local fn = assert(base.loadfile(p))
    return fn()
  end

  local fake_io = {}
  for k, v in pairs(io) do fake_io[k] = v end
  fake_io.open = function(p, mode) return io.open(env:path(p), mode) end
  fake_io.write = function(...)
    for i = 1, select("#", ...) do env:_print(tostring((select(i, ...)))) end
    return fake_io
  end
  fake_io.read = function() return table.remove(env.stdin or {}, 1) end
  fake_io.stdout = { write = function(_, ...) fake_io.write(...) end }
  fake_io.stderr = { write = function(_, ...) fake_io.write(...) end }
  fake_io.lines = nil
  fake_io.popen = nil
  base.io = fake_io

  base.os = {
    clock = os.clock,
    date = os.date,
    difftime = os.difftime,
    time = os.time,
    getenv = function() return nil end,
    tmpname = function() return "/tmp/" .. tostring(math.random(1e9)) end,
    remove = function(p) return os.remove(env:path(p)) end,
    rename = function(a, b) return os.rename(env:path(a), env:path(b)) end,
    sleep = function(s) env:sleep(s) end,
    exit = function() error("os.exit() called", 2) end,
  }

  local loaded = {
    string = string, table = table, math = math, coroutine = coroutine,
    utf8 = utf8, io = fake_io, os = base.os,
  }
  self.loaded = loaded
  for name, lib in pairs(self:_libs()) do loaded[name] = lib end

  -- Anything not faked is looked for in oc/ - standing in for
  -- /usr/lib/?.lua, where gcm installs json.lua and http.lua.
  base.require = function(name)
    if loaded[name] ~= nil then return loaded[name] end
    local fn, err = loadfile(M.OC_DIR .. "/" .. name .. ".lua", "t", base)
    if not fn then error("module '" .. name .. "' not found: " .. tostring(err), 2) end
    local v = fn(name)
    if v == nil then v = true end
    loaded[name] = v
    return v
  end
  base.package = { loaded = loaded, path = "/lib/?.lua;/usr/lib/?.lua;/home/lib/?.lua;./?.lua" }
  self.globals = base
end

function Env:_libs()
  local env = self

  local component = {}
  function component.list(filter, exact)
    local matches = {}
    for _, c in ipairs(env.components) do
      if not filter or (exact and c.type == filter) or (not exact and c.type:find(filter, 1, true)) then
        matches[#matches + 1] = c
      end
    end
    local i = 0
    return function()
      i = i + 1
      local c = matches[i]
      if c then return c.address, c.type end
    end
  end
  function component.isAvailable(ctype) return env:_primary(ctype) ~= nil end
  function component.getPrimary(ctype)
    local p = env:_primary(ctype)
    if not p then error("no primary '" .. ctype .. "' available", 2) end
    return p
  end
  function component.proxy(address)
    for _, c in ipairs(env.components) do
      if c.address == address then return c.proxy end
    end
    return nil, "no such component"
  end
  function component.type(address)
    for _, c in ipairs(env.components) do
      if c.address == address then return c.type end
    end
    return nil, "no such component"
  end
  setmetatable(component, {
    __index = function(_, k) return component.getPrimary(k) end,
  })

  local computer = {
    uptime = function() return env.now end,
    freeMemory = function() return env.free_memory end,
    totalMemory = function() return 4 * 1024 * 1024 end,
    address = function() return "computer-0000" end,
    shutdown = function(reboot)
      env.shutdown = { reboot = reboot and true or false, at = env.now }
    end,
    pushSignal = function() end,
    beep = function() end,
  }

  -- OpenOS's internet.request(): a callable that reads the body chunk by
  -- chunk and never looks at the status code.
  local internet = {}
  function internet.request(url, data, headers, method)
    local handle, reason = component.internet.request(url, data, headers, method)
    if not handle then error(reason, 2) end
    return setmetatable({}, {
      __call = function()
        while true do
          local chunk, err = handle.read()
          if not chunk then
            handle.close()
            if err then error(err, 2) end
            return nil
          elseif #chunk > 0 then
            return chunk
          end
          env:sleep(0)
        end
      end,
      __index = handle,
    })
  end

  local thread = {}
  function thread.create(fn, ...)
    local args = table.pack(...)
    local t = env:spawn(function() return fn(table.unpack(args, 1, args.n)) end)
    local handle = {}
    function handle:detach() t.detached = true; return self end
    function handle:status() return t.dead and "dead" or "running" end
    function handle:kill() t.dead = true end
    function handle:join()
      while not t.dead do env:sleep(0) end
      return true
    end
    return handle
  end
  function thread.current() return env.current end

  local event = {
    pull = function(timeout)
      env:sleep(timeout or TICK)
      return nil
    end,
    push = function() return true end,
    listen = function() return true end,
    ignore = function() return true end,
    timer = function() return 1 end,
    cancel = function() return true end,
  }

  local term = {
    clear = function() end,
    setCursor = function() end,
    getCursor = function() return 1, 1 end,
    clearLine = function() end,
    write = function(s) env:_print(s) end,
    isAvailable = function() return true end,
  }

  local filesystem = {}
  function filesystem.exists(p) return env:exists(p) end
  function filesystem.isDirectory(p)
    return os.execute("test -d " .. shell_quote(env:path(p))) == true
  end
  function filesystem.makeDirectory(p) env:mkdir(p); return true end
  function filesystem.remove(p)
    if not env:exists(p) then return nil, "no such file or directory" end
    os.execute("rm -rf " .. shell_quote(env:path(p)))
    return true
  end
  -- Refuses to overwrite: the scripts all remove the target first, and
  -- being stricter than the game here is the safe direction.
  function filesystem.rename(a, b)
    if not env:exists(a) then return nil, "no such file or directory" end
    if env:exists(b) then return nil, "file already exists" end
    local ok, err = os.rename(env:path(a), env:path(b))
    if not ok then return nil, err end
    return true
  end
  function filesystem.path(p) return p:match("^(.*/)[^/]*$") or "" end
  function filesystem.name(p) return p:match("([^/]+)/?$") end
  function filesystem.concat(...)
    return (table.concat({ ... }, "/"):gsub("//+", "/"))
  end
  function filesystem.size(p)
    local content = env:read_file(p)
    return content and #content or 0
  end

  local serialization = {
    serialize = function(v, pretty) return M.serialize(v, pretty) end,
    unserialize = function(s)
      local fn = load("return " .. s, "=unserialize", "t", {})
      if not fn then return nil end
      local ok, v = pcall(fn)
      if ok then return v end
    end,
  }

  local shell = {
    getWorkingDirectory = function() return "/" end,
    execute = function(cmd)
      env.shell_log[#env.shell_log + 1] = cmd
      return true
    end,
    parse = function(...)
      local args, opts = {}, {}
      for _, a in ipairs({ ... }) do
        if a:sub(1, 2) == "--" then
          local k, v = a:match("^%-%-([^=]+)=?(.*)$")
          opts[k] = v ~= "" and v or true
        elseif a:sub(1, 1) == "-" and #a > 1 then
          for c in a:sub(2):gmatch(".") do opts[c] = true end
        else
          args[#args + 1] = a
        end
      end
      return args, opts
    end,
  }

  return {
    component = component,
    computer = computer,
    internet = internet,
    thread = thread,
    event = event,
    term = term,
    filesystem = filesystem,
    serialization = serialization,
    shell = shell,
  }
end

-- ------------------------------------------------------------- services

-- Loads oc/<name>.lua the way rc does: its own global table over the
-- shared one, top-level code run once. Returns that table, holding the
-- script's start()/stop()/status().
function Env:load_service(name)
  local svc = setmetatable({}, { __index = self.globals })
  local fn = assert(loadfile(M.OC_DIR .. "/" .. name .. ".lua", "t", svc))
  fn()
  return svc
end

-- load_service() + start(): the service's loop is then a thread waiting
-- for env:run().
function Env:start_service(name)
  local svc = self:load_service(name)
  assert(svc.start, name .. ".lua did not define start() - it probably returned early, see env.output")
  svc.start()
  return svc
end

-- Requests made so far, optionally only those whose path matches a
-- Lua pattern (and method, if given).
function Env:requests_to(pattern, method)
  local out = {}
  for _, r in ipairs(self.requests) do
    if r.path:find(pattern) and (not method or r.method == method) then out[#out + 1] = r end
  end
  return out
end

return M
