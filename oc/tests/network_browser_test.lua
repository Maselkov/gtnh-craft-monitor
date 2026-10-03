local t = require("testlib")
local ae2 = require("fake_ae2")

local suite = t.suite()
local test = suite.test

local function catalog_of(n)
  local lines = {}
  for i = 1, n do lines[i] = "testmod:item" .. i end
  return table.concat(lines, "\n") .. "\n"
end

-- n items, one per catalog line.
local function fill(me, n)
  for i = 1, n do
    me.items[i] = ae2.stack("testmod:item" .. i, "Item " .. i, i)
  end
end

local function scan_server(overrides)
  local routes = {
    ["POST /api/network/scan/start"] = { ok = true, scan_token = "tok-1", catalog_version = "2.8.0" },
    ["GET /api/network/catalog"] = { status = 200, body = catalog_of(250) },
    ["POST /api/network/scan/finish"] = { ok = true },
  }
  for k, v in pairs(overrides or {}) do routes[k] = v end
  return routes
end

local function batches(env)
  local out = {}
  for _, r in ipairs(env:requests_to("^/api/network/scan/batch$", "POST")) do out[#out + 1] = r.json end
  return out
end

local function setup(opts)
  opts = opts or {}
  local env = t.env({ routes = scan_server(opts.routes), config = { network_browser = opts.config } })
  local me = ae2.new(env)
  return env, me
end

test("a full scan: catalog download, chunked batches, fluids, finish", function()
  local env, me = setup()
  fill(me, 250)
  me.fluids = { ae2.fluid("molten.neutronium", "Molten Neutronium", 144000) }
  env:start_service("network_browser")
  env:run({ seconds = 60 })

  t.eq(env:read_file("/home/item_catalog.server.txt"), catalog_of(250))
  t.eq(env:read_file("/home/item_catalog.server.txt.version"), "2.8.0")

  local b = batches(env)
  -- 250 items in 100-item chunks, then one chunk of fluids.
  t.eq(#b, 4)
  t.eq(#b[1].items, 100)
  t.eq(#b[3].items, 50)
  t.eq(b[1].scan_token, "tok-1")
  t.eq(b[1].items[1], { name = "Item 1", size = 1, mod = "testmod", internal = "item1",
    damage = 0, isCraftable = false, kind = "item" })
  t.eq(b[4].items[1], { name = "Molten Neutronium", size = 144000, internal = "molten.neutronium",
    isCraftable = false, kind = "fluid" })

  local finish = env:requests_to("^/api/network/scan/finish$")
  t.eq(#finish, 1)
  t.eq(finish[1].json, { scan_token = "tok-1", chunks_sent = 4, total_errors = 0 })
end)

test("the catalog is only downloaded again when its version changes", function()
  local env, me = setup({ config = { SCAN_INTERVAL_SECONDS = 100 } })
  fill(me, 10)
  env:start_service("network_browser")
  env:run({ seconds = 250 })
  t.eq(#env:requests_to("^/api/network/scan/finish$"), 3)
  t.eq(#env:requests_to("^/api/network/catalog$"), 1)
end)

test("NBT tags are hex-encoded, and huge ones left out", function()
  local env, me = setup()
  me.items[1] = ae2.stack("testmod:item1", "Seeds", 3, { hasTag = true, tag = "\1\2\255" })
  me.items[2] = ae2.stack("testmod:item2", "Storage Cell", 1, { hasTag = true, tag = string.rep("x", 4096) })
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  local items = batches(env)[1].items
  t.eq(items[1].tag, "0102ff")
  t.eq(items[1].hasTag, true)
  t.eq(items[2].tag, nil)
  t.eq(items[2].tag_too_large, true)
end)

test("a stale scan token stops the scan without finishing it", function()
  local calls = 0
  local env, me = setup({ routes = {
    ["POST /api/network/scan/batch"] = function()
      calls = calls + 1
      if calls >= 2 then return { ok = false, error = "stale_scan_token" } end
      return { ok = true }
    end,
  } })
  fill(me, 250)
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  t.eq(#batches(env), 2)
  t.eq(#env:requests_to("^/api/network/scan/finish$"), 0)
end)

test("gives up after MAX_CONSECUTIVE_ERRORS failed batches", function()
  local env, me = setup({ config = { BATCH_SIZE = 10 } })
  me:fail("getItemsInNetworkById", "network busy")
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  t.eq(me.calls.getItemsInNetworkById, 5)
  t.eq(#batches(env), 0)
  t.eq(#env:requests_to("^/api/network/scan/finish$"), 0)
end)

test("a failed catalog download keeps the current catalog", function()
  local env, me = setup({ routes = { ["GET /api/network/catalog"] = { status = 404, body = "not found" } } })
  env:write_file("/home/item_catalog.txt", "testmod:item1\n")
  fill(me, 3)
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  t.eq(env:exists("/home/item_catalog.server.txt"), false)
  t.eq(env:exists("/home/item_catalog.server.txt.tmp"), false)
  t.eq(#batches(env)[1].items, 1, "scanned with the hand-placed catalog")
  t.eq(#env:requests_to("^/api/network/scan/finish$"), 1)
end)

test("with no catalog at all the scan fails before scanning", function()
  local env, me = setup({ routes = {
    ["POST /api/network/scan/start"] = { ok = true, scan_token = "tok-1" },
  } })
  fill(me, 3)
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  t.eq(me.calls.getItemsInNetworkById, nil)
  t.eq(#env:requests_to("^/api/network/scan/finish$"), 0)
end)

test("stop() takes effect within a few seconds of the wait", function()
  local env, me = setup()
  fill(me, 5)
  local svc = env:start_service("network_browser")
  env:run({ seconds = 60 })
  svc.stop()
  env:run({ seconds = 6 })
  t.eq(env:alive(), 0)
end)

-- ---------------------------------------------------------- watched items

local IRON_WATCH = { items = { { name = "gregtech:gt.metaitem.01", damage = 11028 } }, fluids = { "water" } }

-- Three stacks sharing one id (as GT materials do), one of them watched,
-- plus an unrelated item and two fluids.
local function fill_watch(me)
  me.items = {
    ae2.stack("gregtech:gt.metaitem.01", "Neutronium Ingot", 5, { damage = 11028 }),
    ae2.stack("gregtech:gt.metaitem.01", "Iron Dust", 900, { damage = 2032 }),
    ae2.stack("gregtech:gt.metaitem.01", "Gold Plate", 40, { damage = 17086 }),
    ae2.stack("testmod:item1", "Item 1", 1),
  }
  me.fluids = { ae2.fluid("water", "Water", 64000), ae2.fluid("lava", "Lava", 1000) }
end

local function levels(env)
  local out = {}
  for _, r in ipairs(env:requests_to("^/api/network/levels$", "POST")) do out[#out + 1] = r.json end
  return out
end

test("between full scans, checks just the watched items every WATCH_INTERVAL_SECONDS", function()
  local env, me = setup({ routes = { ["GET /api/network/watch"] = IRON_WATCH } })
  fill_watch(me)
  env:start_service("network_browser")
  env:run({ seconds = 200 })

  local sent = levels(env)
  t.eq(#sent, 3, "at about 60, 120 and 180s")
  t.eq(sent[1].checked, IRON_WATCH)
  t.eq(#sent[1].items, 2, "the watched stack and fluid, not the others sharing the id")
  t.eq(sent[1].items[1], { name = "Neutronium Ingot", size = 5, mod = "gregtech", internal = "gt.metaitem.01",
    damage = 11028, isCraftable = false, kind = "item" })
  t.eq(sent[1].items[2], { name = "Water", size = 64000, internal = "water", isCraftable = false, kind = "fluid" })
  t.eq(type(sent[1].elapsed), "number")
  t.eq(#env:requests_to("^/api/network/scan/start$"), 1, "no full scan in between")
end)

test("a watched item that's gone is reported as not found", function()
  local env, me = setup({ routes = { ["GET /api/network/watch"] = IRON_WATCH } })
  env:start_service("network_browser")
  env:run({ seconds = 70 })
  t.eq(levels(env)[1].items, {})
end)

test("with no rules, nothing is looked up or sent", function()
  local env, me = setup({ routes = { ["GET /api/network/watch"] = { items = {}, fluids = {} } } })
  fill(me, 3)
  env:start_service("network_browser")
  env:run({ seconds = 200 })
  t.eq(#env:requests_to("^/api/network/watch$"), 3)
  t.eq(#levels(env), 0)
  t.eq(me.calls.getItemsInNetworkById, 1, "only the full scan's")
end)

test("WATCH_INTERVAL_SECONDS = 0 turns the checks off", function()
  local env, me = setup({ config = { WATCH_INTERVAL_SECONDS = 0 },
    routes = { ["GET /api/network/watch"] = IRON_WATCH } })
  fill_watch(me)
  env:start_service("network_browser")
  env:run({ seconds = 200 })
  t.eq(#env:requests_to("^/api/network/watch$"), 0)
end)

test("no check just before a full scan", function()
  local env, me = setup({ config = { SCAN_INTERVAL_SECONDS = 100 },
    routes = { ["GET /api/network/watch"] = IRON_WATCH } })
  fill_watch(me)
  env:start_service("network_browser")
  env:run({ seconds = 250 })
  -- Scans at about 0, 100 and 200; one check after each of the first two.
  t.eq(#env:requests_to("^/api/network/scan/start$"), 3)
  t.eq(#levels(env), 2)
end)

test("a failed check is skipped, and the next one and the next scan still run", function()
  local calls = 0
  local env, me = setup({ config = { SCAN_INTERVAL_SECONDS = 200 }, routes = {
    ["GET /api/network/watch"] = IRON_WATCH,
    ["POST /api/network/levels"] = function()
      calls = calls + 1
      if calls == 1 then return { connect_error = "connection refused" } end
      return { ok = true }
    end,
  } })
  fill_watch(me)
  me:fail("getItemsInNetworkById", "network busy", 2)  -- the full scan's call, then the first check's
  env:start_service("network_browser")
  env:run({ seconds = 250 })
  -- Checks at about 60, 120 and 180s: the first fails looking items
  -- up, the second posting them, the third gets through.
  t.eq(#env:requests_to("^/api/network/watch$"), 3)
  t.eq(calls, 2)
  t.eq(#env:requests_to("^/api/network/scan/start$"), 2)
end)

-- ------------------------------------------------------------- crashes

-- getItemsInNetworkById() returning junk once raises an error outside
-- any pcall - standing in for running out of memory, which can strike
-- wherever the script allocates.
local function crash_once(me)
  local real = me.proxy.getItemsInNetworkById
  local crashed = false
  me.proxy.getItemsInNetworkById = function(ids)
    if not crashed then
      crashed = true
      return "junk"
    end
    return real(ids)
  end
end

test("an error that kills the scan is logged, reported, and scanning starts over", function()
  local env, me = setup({ config = { CRASH_RESTART_SECONDS = 30 } })
  fill(me, 5)
  crash_once(me)
  env:start_service("network_browser")
  env:run({ seconds = 25 })

  local reports = env:requests_to("^/api/network/crashed$", "POST")
  t.eq(#reports, 1)
  t.truthy(reports[1].json.error:find("attempt to index", 1, true), reports[1].json.error)
  t.eq(reports[1].json.phase, "Batch 1: getItemsInNetworkById() returned (ok=true)")
  t.eq(type(reports[1].json.free_memory), "number")
  local log = env:read_file("network_browser_debug.log")
  t.truthy(log:find("CRASHED during", 1, true), "logged")
  t.truthy(log:find("stack traceback", 1, true), "with the traceback")
  t.eq(#env:requests_to("^/api/network/scan/finish$"), 0)

  env:run({ seconds = 60 })
  t.eq(#env:requests_to("^/api/network/scan/start$"), 2, "started over after the wait")
  t.eq(#env:requests_to("^/api/network/scan/finish$"), 1)
  t.eq(env:alive(), 1)
end)

test("stop() during the wait after a crash takes effect", function()
  local env, me = setup({ config = { CRASH_RESTART_SECONDS = 300 } })
  fill(me, 5)
  crash_once(me)
  local svc = env:start_service("network_browser")
  env:run({ seconds = 20 })
  svc.stop()
  env:run({ seconds = 6 })
  t.eq(env:alive(), 0)
end)

test("the debug log records free memory", function()
  local env, me = setup()
  fill(me, 5)
  env.free_memory = 512 * 1024
  env:start_service("network_browser")
  env:run({ seconds = 30 })
  t.truthy(env:read_file("network_browser_debug.log"):find("mem=512k", 1, true))
end)

return suite
