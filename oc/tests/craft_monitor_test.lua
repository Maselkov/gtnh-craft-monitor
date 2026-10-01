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

test("a job ending is reported right away, with how it last looked", function()
  local env, me = setup()
  me:start_job("CPU 1", { output = GEAR, pending = { sized(GEAR, 10) }, duration = 7 })
  env:start_service("craft_monitor")
  env:run({ seconds = 9 })

  local reqs = env:requests_to("^/api/crafts$", "POST")
  -- t=0 and t=5 on the poll interval, then t=7 when the job ended
  -- rather than waiting for t=10.
  t.eq(#reqs, 3)
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
  t.truthy(env:printed("[craft_monitor] Stopped."))
end)

test("without an ME component it says so and doesn't start", function()
  local env = t.env()
  env:start_service("craft_monitor")
  env:run({ seconds = 10 })
  t.truthy(env:printed("No me_controller / me_interface found"))
  t.eq(#env.requests, 0)
end)

return suite
