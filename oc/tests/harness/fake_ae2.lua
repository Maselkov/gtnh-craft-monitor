--[[
  A fake ME network for oc_env, behind an me_controller component.

  Implements what the scripts call, as the API notes at the top of
  craft_monitor.lua describe it:
    getCpus() -> rows { name, storage, coprocessors, busy, cpu }, where
      cpu has isBusy/isActive/activeItems/pendingItems/storedItems/
      finalOutput/cancel
    getCraftables(filter) -> craftables with getItemStack() (items only)
      and request(amount) -> status with isComputing/hasFailed/isDone/
      isCanceled
    getItemsInNetworkById(ids), getFluidsInNetwork()

  State follows the env's virtual clock: every call first catches the
  network up to env.now, so a job started with duration = 10 is busy for
  10 virtual seconds, and a request finishes planning plan_seconds after
  it was made, then takes the first idle CPU.

  Errors: me:fail(method, message, times) makes the next `times` calls
  of that method (default: every call) raise message. method is a
  getCpus()-level name, or "cpu.<name>" for the per-CPU proxy methods.
--]]

local M = {}

-- An AE2 item stack. name is "modid:internalname".
function M.stack(name, label, size, extra)
  local s = { name = name, label = label, size = size or 1, damage = 0, isCraftable = false, hasTag = false }
  for k, v in pairs(extra or {}) do s[k] = v end
  return s
end

-- A fluid as getFluidsInNetwork() reports it: bare name, amount in mB.
function M.fluid(name, label, amount, extra)
  local f = { name = name, label = label, amount = amount or 1000, isCraftable = false }
  for k, v in pairs(extra or {}) do f[k] = v end
  return f
end

local function copy(t)
  local c = {}
  for k, v in pairs(t) do c[k] = v end
  return c
end

local FakeME = {}
FakeME.__index = FakeME

function M.new(env, opts)
  opts = opts or {}
  local me = setmetatable({
    env = env,
    cpus = {},
    craftables = {},
    items = {},
    fluids = {},
    statuses = {},
    failures = {},
    calls = {},
  }, FakeME)
  local methods = me:_methods()
  if opts.without then
    for _, name in ipairs(opts.without) do methods[name] = nil end
  end
  me.proxy = env:add_component(opts.type or "me_controller", methods)
  return me
end

function FakeME:fail(method, message, times)
  self.failures[method] = { message = message or (method .. " failed"), times = times }
end

function FakeME:_call(method)
  self.calls[method] = (self.calls[method] or 0) + 1
  local f = self.failures[method]
  if f then
    if f.times then
      f.times = f.times - 1
      if f.times <= 0 then self.failures[method] = nil end
    end
    error(f.message, 0)
  end
  self:update()
end

-- ------------------------------------------------------------------ CPUs

-- opts: name, storage, coprocessors, monitor (false = no Crafting
-- Monitor, so finalOutput() returns nil).
function FakeME:add_cpu(opts)
  local cpu = {
    name = opts.name,
    storage = opts.storage or 65536,
    coprocessors = opts.coprocessors or 1,
    monitor = opts.monitor ~= false,
  }
  self.cpus[#self.cpus + 1] = cpu
  return cpu
end

function FakeME:cpu(name)
  for _, c in ipairs(self.cpus) do
    if c.name == name then return c end
  end
  error("no fake CPU named " .. tostring(name))
end

-- Starts a job on a CPU, as a player (or a request) would.
--   output    the final output stack
--   pending   stacks still to craft (default: just the output)
--   duration  virtual seconds until it finishes (default 10)
--   status    the request status to mark done when it finishes
function FakeME:start_job(cpu_name, job)
  local cpu = self:cpu(cpu_name)
  assert(not cpu.job, "fake CPU " .. cpu_name .. " is already busy")
  cpu.job = {
    output = job.output,
    pending = job.pending or { copy(job.output) },
    started = self.env.now,
    ends = self.env.now + (job.duration or 10),
    status = job.status,
  }
  return cpu.job
end

-- Ends a CPU's job now, as finishing or a cancel does.
function FakeME:end_job(cpu_name, how)
  local cpu = self:cpu(cpu_name)
  local job = cpu.job
  if not job then return false end
  cpu.job = nil
  if job.status then
    if how == "cancelled" then job.status.canceled = true else job.status.done = true end
  end
  return true
end

-- What's left of a job at the current time: each pending stack shrinks
-- linearly over the job's duration; the first one left is the one being
-- crafted, and what's done so far is stored.
local function job_lists(job, now)
  local span = job.ends - job.started
  local frac = span > 0 and math.min(1, math.max(0, (now - job.started) / span)) or 1
  local pending, active, stored = {}, {}, {}
  for _, s in ipairs(job.pending) do
    local left = math.ceil(s.size * (1 - frac))
    local done = s.size - left
    if left > 0 then
      local p = copy(s)
      p.size = left
      pending[#pending + 1] = p
      if #active == 0 then
        local a = copy(s)
        a.size = 1
        active[1] = a
      end
    end
    if done > 0 then
      local d = copy(s)
      d.size = done
      stored[#stored + 1] = d
    end
  end
  return pending, active, stored
end

-- --------------------------------------------------------------- crafting

-- opts:
--   stack         what the pattern makes (getItemStack()); for a fluid,
--                 a { name, label } fluid
--   fluid         true for a fluid pattern (no getItemStack())
--   plan_seconds  how long request() stays computing (default 1)
--   fail          make planning fail with this reason
--   duration      how long the resulting job runs (default 10)
--   pending       the job's pending stacks (default: the output)
function FakeME:add_craftable(opts)
  local c = copy(opts)
  c.plan_seconds = c.plan_seconds or 1
  c.duration = c.duration or 10
  self.craftables[#self.craftables + 1] = c
  return c
end

local function matches(stack, filter)
  for k, v in pairs(filter or {}) do
    if stack[k] ~= v then return false end
  end
  return true
end

function FakeME:_request(craftable, amount)
  local status = {
    craftable = craftable,
    amount = amount,
    plan_done = self.env.now + craftable.plan_seconds,
    computing = true,
  }
  self.statuses[#self.statuses + 1] = status
  local me = self
  return {
    isComputing = function() me:update(); return status.computing end,
    hasFailed = function()
      me:update()
      return status.failed ~= nil, status.failed
    end,
    isDone = function() me:update(); return status.done == true end,
    isCanceled = function() me:update(); return status.canceled == true end,
  }
end

function FakeME:_finish_planning(status)
  status.computing = false
  local c = status.craftable
  if c.fail then
    status.failed = c.fail
    return
  end
  for _, cpu in ipairs(self.cpus) do
    if not cpu.job then
      local output = copy(c.stack)
      output.size = status.amount
      local pending = {}
      for i, s in ipairs(c.pending or { output }) do pending[i] = copy(s) end
      cpu.job = {
        output = output,
        pending = pending,
        started = status.plan_done,
        ends = status.plan_done + c.duration,
        status = status,
      }
      status.cpu = cpu.name
      return
    end
  end
  status.failed = "no crafting CPU available"
end

-- Catches the network up to env.now, applying planning finishes and job
-- ends in the order they happened.
function FakeME:update()
  local now = self.env.now
  while true do
    local soonest, kind, target
    for _, s in ipairs(self.statuses) do
      if s.computing and s.plan_done <= now and (not soonest or s.plan_done < soonest) then
        soonest, kind, target = s.plan_done, "plan", s
      end
    end
    for _, cpu in ipairs(self.cpus) do
      if cpu.job and cpu.job.ends <= now and (not soonest or cpu.job.ends < soonest) then
        soonest, kind, target = cpu.job.ends, "job", cpu
      end
    end
    if not soonest then return end
    if kind == "plan" then
      self:_finish_planning(target)
    else
      self:end_job(target.name, "finished")
    end
  end
end

-- ------------------------------------------------------------- the proxy

function FakeME:_cpu_proxy(cpu)
  local me = self
  local function method(name, fn)
    return function(...)
      me:_call("cpu." .. name)
      return fn(...)
    end
  end
  return {
    isBusy = method("isBusy", function() return cpu.job ~= nil end),
    isActive = method("isActive", function() return cpu.job ~= nil end),
    pendingItems = method("pendingItems", function()
      if not cpu.job then return {} end
      local p = job_lists(cpu.job, me.env.now)
      return p
    end),
    activeItems = method("activeItems", function()
      if not cpu.job then return {} end
      local _, a = job_lists(cpu.job, me.env.now)
      return a
    end),
    storedItems = method("storedItems", function()
      if not cpu.job then return {} end
      local _, _, s = job_lists(cpu.job, me.env.now)
      return s
    end),
    finalOutput = method("finalOutput", function()
      if not cpu.monitor or not cpu.job then return nil end
      return copy(cpu.job.output)
    end),
    cancel = method("cancel", function()
      return me:end_job(cpu.name, "cancelled")
    end),
  }
end

function FakeME:_methods()
  local me = self
  return {
    getCpus = function()
      me:_call("getCpus")
      local rows = {}
      for _, cpu in ipairs(me.cpus) do
        rows[#rows + 1] = {
          name = cpu.name,
          storage = cpu.storage,
          coprocessors = cpu.coprocessors,
          busy = cpu.job ~= nil,
          cpu = me:_cpu_proxy(cpu),
        }
      end
      return rows
    end,

    getCraftables = function(filter)
      me:_call("getCraftables")
      local out = {}
      for _, c in ipairs(me.craftables) do
        if matches(c.stack, filter) then
          local craftable = {
            request = function(amount) return me:_request(c, amount or 1) end,
          }
          if not c.fluid then
            craftable.getItemStack = function() return copy(c.stack) end
          end
          out[#out + 1] = craftable
        end
      end
      return out
    end,

    getItemsInNetworkById = function(ids)
      me:_call("getItemsInNetworkById")
      local wanted = {}
      for _, id in ipairs(ids) do wanted[id] = true end
      local out = {}
      for _, it in ipairs(me.items) do
        if wanted[it.name] then out[#out + 1] = copy(it) end
      end
      return out
    end,

    getFluidsInNetwork = function()
      me:_call("getFluidsInNetwork")
      local out = {}
      for i, f in ipairs(me.fluids) do out[i] = copy(f) end
      return out
    end,
  }
end

return M
