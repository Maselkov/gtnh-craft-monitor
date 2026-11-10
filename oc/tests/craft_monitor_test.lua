local t = require("testlib")
local ae2 = require("fake_ae2")

local suite = t.suite()
local test = suite.test

local GEAR = ae2.stack("gregtech:gt.metaitem.01", "Titanium Gear", 1, { damage = 32600 })
local PLATE = ae2.stack("gregtech:gt.metaitem.01", "Steel Plate", 1, { damage = 11305 })

local function sized(stack, size)
  local s = {}
  for k, v in pairs(stack) do s[k] = v end
  s.size = size
  return s
end

-- A server whose pending-request queue hands out each list once.
local function queue(...)
  local batches = { ... }
  return function()
    return { requests = table.remove(batches, 1) or {} }
  end
end

local function request_for(id, stack, amount)
  local mod, internal = stack.name:match("^([^:]+):(.+)$")
  return { id = id, kind = "item", mod = mod, internal = internal, damage = stack.damage,
    label = stack.label, amount = amount or 1 }
end

local function crafts_posts(env)
  local out = {}
  for _, r in ipairs(env:requests_to("^/api/crafts$", "POST")) do out[#out + 1] = r.json end
  return out
end

local function results_for(env, id)
  local out = {}
  for _, r in ipairs(env:requests_to("^/api/craft/requests/" .. id .. "/result$", "POST")) do
    out[#out + 1] = r.json
  end
  return out
end

local function setup(routes)
  local env = t.env({ routes = routes })
  local me = ae2.new(env)
  me:add_cpu({ name = "CPU 1" })
  me:add_cpu({ name = "CPU 2" })
  return env, me
end

test("reports every CPU, with the busy one's job", function()
  local env, me = setup()
  me:start_job("CPU 1", { output = sized(GEAR, 16), pending = { sized(GEAR, 16), sized(PLATE, 64) } })
  env:start_service("craft_monitor")
  env:run({ seconds = 0.5 })

  local post = crafts_posts(env)[1]
  t.eq(post.source, "me_controller")
  t.eq(#post.jobs, 2)
  local busy, idle = post.jobs[1], post.jobs[2]
  t.eq(busy.name, "CPU 1")
  t.eq(busy.busy, true)
  t.eq(busy.final_output, "Titanium Gear")
  t.eq(busy.final_output_mod, "gregtech")
  t.eq(busy.final_output_internal, "gt.metaitem.01")
  t.eq(busy.final_output_damage, 32600)
  t.eq(#busy.pending, 2)
  t.eq(busy.pending[2], { name = "Steel Plate", size = 64, mod = "gregtech", internal = "gt.metaitem.01", damage = 11305 })
  t.eq(idle.busy, false)
  t.eq(idle.pending, {})
end)

-- AE2 gives a CPU nobody has named an empty name.
local function unnamed_cpu(me, storage, job)
  local cpu = me:add_cpu({ name = "tmp", storage = storage })
  if job then me:start_job("tmp", job) end
  cpu.name = ""
  return cpu
end

local function names_of(post)
  local out = {}
  for i, j in ipairs(post.jobs) do out[i] = j.name end
  return out
end

test("CPUs nobody named are named after their size, and flagged", function()
  local env = t.env({})
  local me = ae2.new(env)
  unnamed_cpu(me, 69632)
  unnamed_cpu(me, 69632, { output = GEAR, duration = 4 })
  unnamed_cpu(me, 1048576)
  me:add_cpu({ name = "Big", storage = 69632 })
  env:start_service("craft_monitor")
  env:run({ seconds = 0.5 })

  local jobs = crafts_posts(env)[1].jobs
  t.eq(names_of(crafts_posts(env)[1]), { "68k #1", "68k #2", "1M #1", "Big" })
  t.eq({ jobs[1].auto_named, jobs[3].auto_named, jobs[4].auto_named }, { true, true, nil })
  t.eq({ jobs[1].busy, jobs[2].busy }, { false, true })

  -- Going idle doesn't rename them.
  env:run({ seconds = 10 })
  local posts = crafts_posts(env)
  local last = posts[#posts]
  t.eq(names_of(last), { "68k #1", "68k #2", "1M #1", "Big" })
  t.eq(last.jobs[2].busy, false)
end)

test("within a size, more coprocessors come first", function()
  local env = t.env({})
  local me = ae2.new(env)
  unnamed_cpu(me, 4096).coprocessors = 1
  unnamed_cpu(me, 4096).coprocessors = 8
  env:start_service("craft_monitor")
  env:run({ seconds = 0.5 })
  t.eq(names_of(crafts_posts(env)[1]), { "4k #2", "4k #1" })
end)

test("naming, adding or losing a CPU of another size renames nothing", function()
  local env = t.env({})
  local me = ae2.new(env)
  local big = unnamed_cpu(me, 1048576)
  unnamed_cpu(me, 4096, { output = GEAR, duration = 100 })
  env:start_service("craft_monitor")
  env:run({ seconds = 0.5 })
  t.eq(names_of(crafts_posts(env)[1]), { "1M #1", "4k #1" })

  big.name = "Big"
  me:add_cpu({ name = "", storage = 2097152 })
  env:run({ seconds = 10 })
  local posts = crafts_posts(env)
  t.eq(names_of(posts[#posts]), { "Big", "4k #1", "2M #1" })
end)

test("a name a CPU is really called is skipped", function()
  local env = t.env({})
  local me = ae2.new(env)
  me:add_cpu({ name = "4k #1", storage = 1000 })
  unnamed_cpu(me, 4096)
  env:start_service("craft_monitor")
  env:run({ seconds = 0.5 })
  t.eq(names_of(crafts_posts(env)[1]), { "4k #1", "4k #2" })
end)

test("a CPU whose size isn't a usable number is named as 0k, not crashed on", function()
  local env = t.env({})
  local me = ae2.new(env)
  unnamed_cpu(me, 0 / 0)
  unnamed_cpu(me, "4096")
  unnamed_cpu(me, 1e300)  -- past 2^63: no integer for %d
  unnamed_cpu(me, 2 ^ 53)
  unnamed_cpu(me, 2 ^ 53 - 1024)
  env:start_service("craft_monitor")
  env:run({ seconds = 0.5 })
  t.eq(names_of(crafts_posts(env)[1]), { "0k #1", "0k #2", "0k #3", "0k #4", "8796093022207k #1" })
end)

test("CPUs sharing a name each get it with their size and a number", function()
  local env = t.env({})
  local me = ae2.new(env)
  me:add_cpu({ name = "Main", storage = 65536, coprocessors = 1 })
  me:add_cpu({ name = "Main", storage = 65536, coprocessors = 8 })
  me:add_cpu({ name = "Main", storage = 4096 })
  me:add_cpu({ name = "Other", storage = 65536 })
  unnamed_cpu(me, 65536)
  env:start_service("craft_monitor")
  env:run({ seconds = 0.5 })
  local post = crafts_posts(env)[1]
  t.eq(names_of(post), { "Main 64k #2", "Main 64k #1", "Main 4k #1", "Other", "64k #1" })
  t.eq({ post.jobs[1].auto_named, post.jobs[4].auto_named }, { true, nil })
end)

test("a CPU whose shared name stops being shared gets it back", function()
  local env = t.env({})
  local me = ae2.new(env)
  me:add_cpu({ name = "Main", storage = 65536 })
  local other = me:add_cpu({ name = "Main", storage = 4096 })
  env:start_service("craft_monitor")
  env:run({ seconds = 0.5 })
  t.eq(names_of(crafts_posts(env)[1]), { "Main 64k #1", "Main 4k #1" })

  other.name = "Small"
  env:run({ seconds = 10 })
  local posts = crafts_posts(env)
  t.eq(names_of(posts[#posts]), { "Main", "Small" })
end)

test("DEBUG_CPU_FILTER takes an unnamed CPU's name as the page shows it", function()
  local env = t.env({ config = { craft_monitor = { DEBUG_DUMP = true, DEBUG_CPU_FILTER = "4k #2" } } })
  local me = ae2.new(env)
  unnamed_cpu(me, 4096)
  unnamed_cpu(me, 4096, { output = GEAR, duration = 100 })
  env:start_service("craft_monitor")
  env:run({ seconds = 5 })
  local tags = {}
  for _, r in ipairs(env:requests_to("^/api/debug$", "POST")) do tags[#tags + 1] = r.json.tag end
  t.eq(tags, { "cpu-4k #2" })
end)

test("a cancel finds an unnamed CPU by the name it was reported under", function()
  local env = t.env({ routes = {
    ["GET /api/craft/cancel/pending"] = queue({
      { id = 9, cpu_name = "4k #2", expected_output = { mod = "gregtech", internal = "gt.metaitem.01", damage = 32600 } },
    }),
  } })
  local me = ae2.new(env)
  unnamed_cpu(me, 4096)
  local busy = unnamed_cpu(me, 4096, { output = GEAR, duration = 100 })
  env:start_service("craft_monitor")
  env:run({ seconds = 3 })
  t.eq(env:requests_to("^/api/craft/cancel/9/result$")[1].json, { success = true })
  t.eq(busy.job, nil)
end)

test("a job ending is reported right away, with how it last looked", function()
  local env, me = setup()
  me:start_job("CPU 1", { output = GEAR, pending = { sized(GEAR, 10) }, duration = 7 })
  env:start_service("craft_monitor")
  env:run({ seconds = 9 })

  local reqs = env:requests_to("^/api/crafts$", "POST")
  -- t=0 and t=5 on the poll interval, then t=7 when the job ended
  -- rather than waiting for t=10.
  t.eq(#reqs, 3)
  t.eq(reqs[1].json.poll_seconds, 5)
  t.eq(reqs[3].at, 7)
  local job = reqs[3].json.jobs[1]
  t.eq(job.busy, false)
  t.truthy(job.last_busy, "last_busy sent")
  t.eq(job.last_busy.age, 1, "from the watch tick a second before it ended")
  t.eq(job.last_busy.pending[1].size, 2)
  -- Sent once: the next report has nothing left to say about it.
  env:run({ seconds = 5 })
  t.eq(crafts_posts(env)[4].jobs[1].last_busy, nil)
end)

test("last_busy is kept until a report gets through", function()
  local failing = true
  local env, me = setup({
    ["POST /api/crafts"] = function()
      if failing then return { connect_error = "server down" } end
      return { ok = true }
    end,
  })
  me:start_job("CPU 1", { output = GEAR, duration = 3 })
  env:start_service("craft_monitor")
  env:run({ seconds = 4 })
  failing = false
  env:run({ seconds = 5 })
  local posts = crafts_posts(env)
  t.truthy(posts[#posts].jobs[1].last_busy, "still sent after the failed report")
end)

test("a getCpus() error skips one report and the loop carries on", function()
  local env, me = setup()
  me:fail("getCpus", "network unavailable", 1)
  env:start_service("craft_monitor")
  env:run({ seconds = 6 })
  local reqs = env:requests_to("^/api/crafts$", "POST")
  t.eq(#reqs, 1)
  t.eq(reqs[1].at, 5)
end)

test("an accepted request names its CPU, then reports how it ended", function()
  local env, me = setup({
    ["GET /api/craft/requests/pending"] = queue({ request_for(7, GEAR, 4) }),
  })
  me:start_job("CPU 1", { output = PLATE, duration = 100 })
  me:add_craftable({ stack = GEAR, plan_seconds = 2, duration = 10 })
  env:start_service("craft_monitor")
  env:run({ seconds = 20 })

  local results = results_for(env, 7)
  t.eq(#results, 1)
  t.eq(results[1], { status = "accepted", cpu_name = "CPU 2", watching = true })
  local outcomes = env:requests_to("^/api/craft/requests/7/outcome$", "POST")
  t.eq(#outcomes, 1)
  t.eq(outcomes[1].json, { outcome = "finished", cpu_name = "CPU 2" })
end)

test("two requests finishing planning together each get their own CPU", function()
  local env, me = setup({
    ["GET /api/craft/requests/pending"] = queue({ request_for(1, GEAR), request_for(2, PLATE) }),
  })
  me:add_craftable({ stack = GEAR })
  me:add_craftable({ stack = PLATE })
  env:start_service("craft_monitor")
  env:run({ seconds = 5 })
  t.eq(results_for(env, 1)[1].cpu_name, me.statuses[1].cpu)
  t.eq(results_for(env, 2)[1].cpu_name, me.statuses[2].cpu)
end)

test("without Crafting Monitors, simultaneous requests aren't pinned to a guess", function()
  local env = t.env({
    routes = { ["GET /api/craft/requests/pending"] = queue({ request_for(1, GEAR), request_for(2, PLATE) }) },
  })
  local me = ae2.new(env)
  me:add_cpu({ name = "CPU 1", monitor = false })
  me:add_cpu({ name = "CPU 2", monitor = false })
  me:add_craftable({ stack = GEAR })
  me:add_craftable({ stack = PLATE })
  env:start_service("craft_monitor")
  env:run({ seconds = 5 })
  t.eq(results_for(env, 1)[1], { status = "accepted" })
  t.eq(results_for(env, 2)[1], { status = "accepted" })
end)

test("a request that fails planning reports why", function()
  local env, me = setup({
    ["GET /api/craft/requests/pending"] = queue({ request_for(3, GEAR) }),
  })
  me:add_craftable({ stack = GEAR, fail = "missing 64x Titanium Plate" })
  env:start_service("craft_monitor")
  env:run({ seconds = 5 })
  t.eq(results_for(env, 3), { { status = "failed", reason = "missing 64x Titanium Plate" } })
end)

test("a request with no pattern fails without calling request()", function()
  local env = setup({
    ["GET /api/craft/requests/pending"] = queue({ request_for(4, GEAR) }),
  })
  env:start_service("craft_monitor")
  env:run({ seconds = 3 })
  t.eq(results_for(env, 4), { { status = "failed", reason = "no matching craftable pattern found" } })
end)

-- Each material's turbine: one id, damage and label, only the NBT differs.
local NEUTRONIUM_TAG, STEEL_TAG = "\31\139nbt-neutronium", "\31\139nbt-steel"
local function turbine(tag)
  return ae2.stack("gregtech:gt.metatool.01", "Huge Turbine", 1, { damage = 176, hasTag = true, tag = tag })
end

local function hex(bytes)
  return (bytes:gsub(".", function(c) return string.format("%02x", c:byte()) end))
end

test("an NBT variant's request crafts that exact variant", function()
  local req = request_for(5, turbine(NEUTRONIUM_TAG))
  req.variant, req.tag = "v1", hex(NEUTRONIUM_TAG)
  local env, me = setup({ ["GET /api/craft/requests/pending"] = queue({ req }) })
  me:add_craftable({ stack = turbine(STEEL_TAG) })
  me:add_craftable({ stack = turbine(NEUTRONIUM_TAG) })
  env:start_service("craft_monitor")
  env:run({ seconds = 5 })
  t.eq(results_for(env, 5)[1].status, "accepted")
  t.eq(me:cpu("CPU 1").job.output.tag, NEUTRONIUM_TAG)
end)

test("a plain request doesn't craft an NBT variant of the item", function()
  local env, me = setup({ ["GET /api/craft/requests/pending"] = queue({ request_for(6, GEAR) }) })
  local tagged = ae2.stack(GEAR.name, GEAR.label, 1, { damage = GEAR.damage, hasTag = true, tag = STEEL_TAG })
  me:add_craftable({ stack = tagged })
  env:start_service("craft_monitor")
  env:run({ seconds = 3 })
  t.eq(results_for(env, 6), { { status = "failed", reason = "no matching craftable pattern found" } })
end)

test("a variant whose NBT the server doesn't have yet isn't guessed at", function()
  local req = request_for(8, turbine(NEUTRONIUM_TAG))
  req.variant = "v1"
  local env, me = setup({ ["GET /api/craft/requests/pending"] = queue({ req }) })
  me:add_craftable({ stack = turbine(NEUTRONIUM_TAG) })
  env:start_service("craft_monitor")
  env:run({ seconds = 3 })
  t.eq(results_for(env, 8), { {
    status = "failed", reason = "this item's NBT isn't known yet - wait for the next network scan" } })
end)

test("a fluid is found by its name", function()
  local req = { id = 10, kind = "fluid", internal = "molten.neutronium", label = "Molten Neutronium", amount = 144 }
  local env, me = setup({ ["GET /api/craft/requests/pending"] = queue({ req }) })
  me:add_craftable({ stack = { name = "molten.neutronium", label = "Molten Neutronium" }, fluid = true })
  env:start_service("craft_monitor")
  env:run({ seconds = 5 })
  t.eq(results_for(env, 10)[1].status, "accepted")
end)

-- Multiblock kits as one job: a kit interface (its own Adapter) on the
-- network, with a fresh kit slot (a pattern making a Command Block) in
-- slot 2 and a real pattern in slot 1.
local COMMAND_BLOCK = { name = "minecraft:command_block", damage = 0, size = 1 }
local KIT_TAG = "\31\139nbt-ebf-kit"
local CONTROLLER = { name = "gregtech:gt.blockmachines", damage = 1000 }

local KIT_TAG_2 = "\31\139nbt-ebf-kit-2"

local function kit_request(id, inputs)
  return { id = id, kind = "item", mod = "gregtech", internal = "gt.blockmachines", damage = 1000,
    label = "Electric Blast Furnace kit", amount = 1, tag = hex(KIT_TAG),
    kit_inputs = inputs or {
      { name = "gregtech:gt.blockcasings", damage = 11, size = 34 },
      { name = "gregtech:gt.blockcasings5", damage = 0, size = 16 },
    } }
end

-- A kit request from a server that offers numbered names too.
local function named_kit_request(id, inputs)
  local req = kit_request(id, inputs)
  req.kit_names = { { label = "Electric Blast Furnace kit", tag = hex(KIT_TAG) },
    { label = "Electric Blast Furnace kit #2", tag = hex(KIT_TAG_2) } }
  return req
end

-- A pattern a kit request wrote earlier: these inputs, and the kit named
-- by tag.
local function old_kit(inputs, tag)
  return { inputs = inputs, outputs = { { name = CONTROLLER.name, damage = CONTROLLER.damage, size = 1, tag = tag,
    hasTag = true, label = tag == KIT_TAG and "Electric Blast Furnace kit" or "Electric Blast Furnace kit #2" } } }
end

local GLASS = { { name = "minecraft:glass", damage = 0, size = 4 } }

local function kit_setup(routes, opts)
  opts = opts or {}
  local env, me = setup(routes)
  me.tag_labels = { [KIT_TAG] = "Electric Blast Furnace kit", [KIT_TAG_2] = "Electric Blast Furnace kit #2" }
  me.kit = opts.kit
  local iface = me:add_interface(9)
  me:set_pattern(1, { inputs = { { name = "gregtech:ingot", damage = 0, size = 1 } },
    outputs = { { name = "gregtech:plate", damage = 0, size = 1 } } }, iface)
  if opts.fresh ~= false then
    me:set_pattern(2, { inputs = { { name = "minecraft:dirt", damage = 0, size = 1 }, false,
      { name = "minecraft:log", damage = 0, size = 2 }, { name = "minecraft:sand", damage = 0, size = 3 } },
      outputs = { COMMAND_BLOCK, { name = "minecraft:dirt", damage = 0, size = 1 } } }, iface)
  end
  return env, me, iface
end

test("a kit is written into a kit slot and requested as one job", function()
  local env, _, iface = kit_setup({ ["GET /api/craft/requests/pending"] = queue({ kit_request(20) }) })
  env:start_service("craft_monitor")
  env:run({ seconds = 10 })

  local p = iface.patterns[2]
  t.eq(p.invalid, nil)
  t.eq(p.inputs, {
    { name = "gregtech:gt.blockcasings", damage = 11, size = 34 },
    { name = "gregtech:gt.blockcasings5", damage = 0, size = 16 },
  })
  t.eq(p.outputs, { { name = CONTROLLER.name, damage = CONTROLLER.damage, size = 1, tag = KIT_TAG, hasTag = true,
    label = "Electric Blast Furnace kit" } })
  t.eq(iface.patterns[1].outputs, { { name = "gregtech:plate", damage = 0, size = 1 } })

  t.eq(results_for(env, 20)[1], { status = "accepted", cpu_name = "CPU 1", watching = true,
    label = "Electric Blast Furnace kit" })
  local outcomes = env:requests_to("^/api/craft/requests/20/outcome$", "POST")
  t.eq(outcomes[1].json, { outcome = "finished", cpu_name = "CPU 1" })
  local posts = crafts_posts(env)
  t.eq(posts[#posts].kit_slots, 1)
end)

test("a kit slot is used again once its job has ended, and never shrinks to nothing", function()
  local env, me, iface = kit_setup({
    ["GET /api/craft/requests/pending"] = queue({ kit_request(21) }, {}, {}, {}, {}, {},
      { kit_request(22, { { name = "minecraft:glass", damage = 0, size = 4 } }) }),
  })
  env:start_service("craft_monitor")
  env:run({ seconds = 20 })
  t.eq(results_for(env, 21)[1].status, "accepted")
  t.eq(results_for(env, 22)[1].status, "accepted")
  t.eq(iface.patterns[2].inputs, { { name = "minecraft:glass", damage = 0, size = 4 } })
  t.eq(iface.patterns[2].invalid, nil)
  t.eq(#me.statuses, 2)
end)

test("a second kit waits for no slot: it fails while the only one is in use", function()
  local env, me = kit_setup({ ["GET /api/craft/requests/pending"] = queue({ kit_request(23), kit_request(24) }) },
    { kit = { duration = 100 } })
  env:start_service("craft_monitor")
  env:run({ seconds = 10 })
  t.eq(results_for(env, 23)[1].status, "accepted")
  t.eq(results_for(env, 24), { {
    status = "failed", reason = "every kit slot is in use by another kit - try again when one finishes" } })
  t.eq(#me.statuses, 1)
end)

test("a slot whose kit is still crafting isn't rewritten after a restart", function()
  local env, me, iface = kit_setup({ ["GET /api/craft/requests/pending"] = queue({ kit_request(25) }) },
    { fresh = false })
  -- Written by an earlier run, its job still going.
  local out = { name = CONTROLLER.name, damage = CONTROLLER.damage, size = 1, tag = KIT_TAG, hasTag = true,
    label = "Electric Blast Furnace kit" }
  me:set_pattern(2, { inputs = { { name = "minecraft:glass", damage = 0, size = 4 } }, outputs = { out } }, iface)
  me:start_job("CPU 2", { output = out, duration = 100 })
  env:start_service("craft_monitor")
  env:run({ seconds = 5 })
  t.eq(results_for(env, 25)[1].reason, "every kit slot is in use by another kit - try again when one finishes")
  t.eq(iface.patterns[2].inputs, { { name = "minecraft:glass", damage = 0, size = 4 } })
end)

test("without a kit slot a kit fails and no pattern is touched", function()
  local env, me = kit_setup({ ["GET /api/craft/requests/pending"] = queue({ kit_request(26) }) }, { fresh = false })
  env:start_service("craft_monitor")
  env:run({ seconds = 5 })
  t.truthy(results_for(env, 26)[1].reason:find("no kit slot found", 1, true))
  t.eq(me.calls.setInterfacePatternInput, nil)
  t.eq(me.calls.setInterfacePatternOutput, nil)
  local posts = crafts_posts(env)
  t.eq(posts[#posts].kit_slots, 0)
end)

test("a second kit of the same multiblock takes the next name while the first is crafting", function()
  local env, me, iface = kit_setup({
    ["GET /api/craft/requests/pending"] = queue({ named_kit_request(40) }, {}, {}, {},
      { named_kit_request(41, GLASS) }),
  }, { kit = { duration = 100 } })
  me:set_pattern(3, { inputs = { { name = "minecraft:dirt", damage = 0, size = 1 } },
    outputs = { COMMAND_BLOCK } }, iface)
  env:start_service("craft_monitor")
  env:run({ seconds = 15 })
  t.eq(results_for(env, 40)[1].label, "Electric Blast Furnace kit")
  t.eq(results_for(env, 41)[1], { status = "accepted", cpu_name = "CPU 2", watching = true,
    label = "Electric Blast Furnace kit #2" })
  t.eq(iface.patterns[2].outputs[1].tag, KIT_TAG)
  t.eq(iface.patterns[3].outputs[1].tag, KIT_TAG_2)
  t.eq(iface.patterns[3].inputs, GLASS)
end)

test("a free slot that already has the kit's name is the one rewritten", function()
  local env, _, iface = kit_setup({ ["GET /api/craft/requests/pending"] = queue({ named_kit_request(42) }) })
  -- Slot 2 is a fresh Command Block; slot 3 kept an earlier EBF kit.
  iface.patterns[3] = old_kit(GLASS, KIT_TAG)
  env:start_service("craft_monitor")
  env:run({ seconds = 10 })
  t.eq(results_for(env, 42)[1].label, "Electric Blast Furnace kit")
  t.eq(iface.patterns[2].outputs[1], COMMAND_BLOCK)
  t.eq(iface.patterns[3].inputs, {
    { name = "gregtech:gt.blockcasings", damage = 11, size = 34 },
    { name = "gregtech:gt.blockcasings5", damage = 0, size = 16 },
  })
end)

test("other free slots with the kit's name go back to making a Command Block", function()
  local env, me, iface = kit_setup({ ["GET /api/craft/requests/pending"] = queue({ named_kit_request(43) }) },
    { fresh = false })
  iface.patterns[2] = old_kit(GLASS, KIT_TAG)
  iface.patterns[3] = old_kit({ { name = "minecraft:sand", damage = 0, size = 1 } }, KIT_TAG)
  env:start_service("craft_monitor")
  env:run({ seconds = 10 })
  t.eq(results_for(env, 43)[1].status, "accepted")
  t.eq(iface.patterns[2].inputs[1].name, "gregtech:gt.blockcasings")
  t.eq(iface.patterns[3].outputs[1].name, COMMAND_BLOCK.name)
  t.eq(iface.patterns[3].outputs[1].tag, nil)
  t.eq(iface.patterns[3].inputs, { { name = "minecraft:sand", damage = 0, size = 1 } })
  t.eq(iface.patterns[3].invalid, nil)
  t.eq(#me.statuses, 1)
end)

test("a slot named with a numbered kit name is still a kit slot", function()
  local env, _, iface = kit_setup({ ["GET /api/craft/requests/pending"] = queue({ named_kit_request(44) }) },
    { fresh = false })
  iface.patterns[2] = old_kit(GLASS, KIT_TAG_2)
  env:start_service("craft_monitor")
  env:run({ seconds = 10 })
  t.eq(results_for(env, 44)[1].label, "Electric Blast Furnace kit")
  t.eq(iface.patterns[2].outputs[1].tag, KIT_TAG)
end)

test("a crafting pattern making a Command Block isn't a kit slot", function()
  local env, me, iface = kit_setup({ ["GET /api/craft/requests/pending"] = queue({ kit_request(27) }) },
    { fresh = false })
  me:set_pattern(3, { inputs = { { name = "minecraft:dirt", damage = 0, size = 1 } }, outputs = { COMMAND_BLOCK },
    crafting = true }, iface)
  env:start_service("craft_monitor")
  env:run({ seconds = 5 })
  t.truthy(results_for(env, 27)[1].reason:find("no kit slot found", 1, true))
  t.eq(me.calls.setInterfacePatternInput, nil)
end)

test("a kit AE2 refuses frees its slot for the next one", function()
  local env, me = kit_setup({
    ["GET /api/craft/requests/pending"] = queue({ kit_request(28) }, {}, {}, { kit_request(29) }),
  }, { kit = { fail = "missing 16x Cupronickel Coil Block" } })
  env:start_service("craft_monitor")
  env:run({ seconds = 10 })
  t.eq(results_for(env, 28), { { status = "failed", reason = "missing 16x Cupronickel Coil Block",
    label = "Electric Blast Furnace kit" } })
  t.eq(results_for(env, 29), { { status = "failed", reason = "missing 16x Cupronickel Coil Block",
    label = "Electric Blast Furnace kit" } })
  t.eq(#me.statuses, 2)
end)

test("a cancel stops the job it was meant for", function()
  local env, me = setup({
    ["GET /api/craft/cancel/pending"] = queue({
      { id = 9, cpu_name = "CPU 1", expected_output = { mod = "gregtech", internal = "gt.metaitem.01", damage = 32600 } },
    }),
  })
  me:start_job("CPU 1", { output = GEAR, duration = 100 })
  env:start_service("craft_monitor")
  env:run({ seconds = 3 })
  local result = env:requests_to("^/api/craft/cancel/9/result$")[1]
  t.eq(result.json, { success = true })
  t.eq(me:cpu("CPU 1").job, nil)
end)

test("a cancel for a job that already ended leaves the CPU's new job alone", function()
  local env, me = setup({
    ["GET /api/craft/cancel/pending"] = queue({
      { id = 9, cpu_name = "CPU 1", expected_output = { mod = "gregtech", internal = "gt.metaitem.01", damage = 32600 } },
    }),
  })
  me:start_job("CPU 1", { output = PLATE, duration = 100 })
  env:start_service("craft_monitor")
  env:run({ seconds = 3 })
  local result = env:requests_to("^/api/craft/cancel/9/result$")[1]
  t.eq(result.json, { success = false, reason = "that job already ended - nothing was cancelled" })
  t.truthy(me:cpu("CPU 1").job, "Steel Plate job still running")
end)

test("stop() ends both loops", function()
  local env = setup()
  local svc = env:start_service("craft_monitor")
  env:run({ seconds = 3 })
  t.eq(env:alive(), 2)
  svc.stop()
  env:run({ seconds = 30 })
  t.eq(env:alive(), 0)
end)

test("prints only when stop() is called, not from its loops", function()
  local env = setup()
  local svc = env:start_service("craft_monitor")
  env:run({ seconds = 30 })
  t.eq(env.output, {})
  svc.stop()
  t.eq(env.output, { "[craft_monitor] Stopping." })
  env:run({ seconds = 30 })
  t.eq(env.output, { "[craft_monitor] Stopping." })
  svc.stop()
  t.eq(env.output[2], "[craft_monitor] Not running.")
end)

test("without an ME component it says so and doesn't start", function()
  local env = t.env()
  env:start_service("craft_monitor")
  env:run({ seconds = 10 })
  t.truthy(env:printed("No me_controller / me_interface found"))
  t.eq(#env.requests, 0)
end)

test("a crash is kept in last_error", function()
  local env = t.env()
  local me = ae2.new(env)
  me.proxy.getCpus = function() return 5 end  -- not a list: reading it throws
  local svc = env:start_service("craft_monitor")
  env:run({ seconds = 1, allow_errors = true })
  t.eq(svc.is_running(), false)
  t.truthy(svc.last_error and svc.last_error.message ~= "", "last_error set")
  t.eq(svc.last_error.uptime, 0)
end)

return suite
