local t = require("testlib")
local ae2 = require("fake_ae2")
local fake_terminal = require("fake_terminal")

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
    ["POST /api/network/patterns/start"] = { ok = true, scan_token = "ptok-1" },
    ["POST /api/network/patterns/finish"] = { ok = true, pattern_count = 0 },
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

-- ------------------------------------------------------------ patterns

local function pattern_batches(env)
  local out = {}
  for _, r in ipairs(env:requests_to("^/api/network/patterns/batch$", "POST")) do out[#out + 1] = r.json end
  return out
end

-- Logs -> 4 planks, the way OC reports a crafting pattern: sizes 0,
-- the counts only in its NBT (here just some bytes).
local function planks_pattern()
  return fake_terminal.pattern(
    { [1] = fake_terminal.item("minecraft:log", "Oak Wood", 0, { id = 17 }) },
    { [1] = fake_terminal.item("minecraft:planks", "Oak Wood Planks", 0, { id = 5 }) },
    { crafting = true, tag = "\31\139AB" })
end

local function neodymium_pattern()
  return fake_terminal.pattern(
    { [1] = fake_terminal.item("gregtech:gt.metaitem.01", "Neodymium Ingot", 16, { damage = 11067, id = 7444 }) },
    { [1] = fake_terminal.fluid("molten.neodymium", "Molten Neodymium", 2304),
      [2] = fake_terminal.essentia("ordo", "Ordo", 2) },
    { tag = "not sent" })
end

test("after an item scan, every pattern goes to the server in chunks", function()
  local env, me = setup({ config = { PATTERN_CHUNK_SIZE = 2 } })
  fill(me, 3)
  fake_terminal.new(env, {
    fake_terminal.interface("Molecular Assembler", -1413, 57, -123, { planks_pattern() }),
    fake_terminal.interface("Nothing", 0, 0, 0, {}),
    fake_terminal.interface("Fluid Extractor p1", 1, 2, 3, { neodymium_pattern(), neodymium_pattern() }),
  })
  env:start_service("network_browser")
  env:run({ seconds = 60 })

  local order = {}
  for i, r in ipairs(env.requests) do order[r.path] = order[r.path] or i end
  t.truthy(order["/api/network/scan/finish"] < order["/api/network/patterns/start"],
    "patterns come after the item scan")

  local b = pattern_batches(env)
  t.eq(#b, 2)
  t.eq(b[1].scan_token, "ptok-1")
  t.eq(#b[1].patterns, 2)
  t.eq(#b[2].patterns, 1)
  t.eq(b[1].patterns[1], {
    provider = { name = "Molecular Assembler", x = -1413, y = 57, z = -123, dim = 0 },
    slot = 0,
    crafting = true,
    inputs = { { name = "Oak Wood", size = 0, mod = "minecraft", internal = "log", damage = 0,
      isCraftable = false, kind = "item", id = 17 } },
    outputs = { { name = "Oak Wood Planks", size = 0, mod = "minecraft", internal = "planks", damage = 0,
      isCraftable = false, kind = "item", id = 5 } },
    tag = "1f8b4142",
  })
  -- A processing pattern's sizes are real, so its NBT isn't sent.
  t.eq(b[1].patterns[2].tag, nil)
  t.eq(b[1].patterns[2].crafting, false)
  t.eq(b[1].patterns[2].outputs, {
    { name = "Molten Neodymium", size = 2304, internal = "molten.neodymium", isCraftable = false, kind = "fluid" },
    { name = "Ordo", size = 2, internal = "ordo", isCraftable = false, kind = "essentia" },
  })
  t.eq(b[2].patterns[1].slot, 1)

  t.eq(env:requests_to("^/api/network/patterns/finish$")[1].json,
    { scan_token = "ptok-1", chunks_sent = 2, total_errors = 0 })
end)

test("without an interface terminal there are no pattern scans", function()
  local env, me = setup()
  fill(me, 3)
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  t.eq(#env:requests_to("^/api/network/patterns"), 0)
  t.eq(#batches(env), 1)
end)

test("patterns are scanned every PATTERN_SCAN_INTERVAL_SECONDS, not every item scan", function()
  local env, me = setup({ config = { SCAN_INTERVAL_SECONDS = 100, PATTERN_SCAN_INTERVAL_SECONDS = 250,
    WATCH_INTERVAL_SECONDS = 0 } })
  fill(me, 3)
  fake_terminal.new(env, { fake_terminal.interface("EBF", 0, 0, 0, { neodymium_pattern() }) })
  env:start_service("network_browser")
  env:run({ seconds = 450 })
  t.eq(#env:requests_to("^/api/network/scan/finish$"), 5)
  t.eq(#env:requests_to("^/api/network/patterns/finish$"), 2)
end)

test("PATTERN_SCAN_INTERVAL_SECONDS = 0 turns pattern scans off", function()
  local env, me = setup({ config = { PATTERN_SCAN_INTERVAL_SECONDS = 0 } })
  fill(me, 3)
  fake_terminal.new(env, { fake_terminal.interface("EBF", 0, 0, 0, { neodymium_pattern() }) })
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  t.eq(#env:requests_to("^/api/network/patterns"), 0)
end)

test("a broken pattern list is reported as an error, tried again, and item scans go on", function()
  local env, me = setup({ config = { SCAN_INTERVAL_SECONDS = 100, WATCH_INTERVAL_SECONDS = 0 } })
  fill(me, 3)
  local term = fake_terminal.new(env, {
    fake_terminal.interface("A", 0, 0, 0, { neodymium_pattern() }),
    fake_terminal.interface("B", 0, 0, 1, { neodymium_pattern() }),
  })
  term.fail_list_at = 2
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  t.eq(env:requests_to("^/api/network/patterns/finish$")[1].json,
    { scan_token = "ptok-1", chunks_sent = 1, total_errors = 1 })
  t.truthy(env:read_file("network_browser_debug.log"):find("pattern list failed after 1 interfaces", 1, true))

  -- Not an hour later: after the next item scan.
  env:run({ seconds = 100 })
  t.eq(#env:requests_to("^/api/network/scan/finish$"), 2)
  t.eq(#env:requests_to("^/api/network/patterns/finish$"), 2)
end)

test("a failing getInterfaces() finishes the scan with an error", function()
  local env, me = setup()
  fill(me, 3)
  local term = fake_terminal.new(env, {})
  term:fail("getInterfaces", "no grid")
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  t.eq(#pattern_batches(env), 0)
  t.eq(env:requests_to("^/api/network/patterns/finish$")[1].json,
    { scan_token = "ptok-1", chunks_sent = 0, total_errors = 1 })
end)

test("a stale pattern scan token stops sending", function()
  local env, me = setup({ config = { PATTERN_CHUNK_SIZE = 1 }, routes = {
    ["POST /api/network/patterns/batch"] = { ok = false, error = "stale_scan_token" },
  } })
  fill(me, 3)
  fake_terminal.new(env, { fake_terminal.interface("EBF", 0, 0, 0,
    { neodymium_pattern(), neodymium_pattern(), neodymium_pattern() }) })
  env:start_service("network_browser")
  env:run({ seconds = 60 })
  t.eq(#pattern_batches(env), 1)
  t.eq(#env:requests_to("^/api/network/patterns/finish$"), 0)
  t.truthy(env:read_file("network_browser_debug.log"):find("stale_scan_token", 1, true))
end)

return suite
