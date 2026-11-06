--[[
  A fake ME network for oc_env, behind an me_controller component.

  Implements what the scripts call, as the API notes at the top of
  craft_monitor.lua describe it:
    getCpus() -> rows { name, storage, coprocessors, busy, cpu }, where
      cpu has isBusy/isActive/activeItems/pendingItems/storedItems/
      finalOutput/cancel
    getCraftable(detail, type) -> the craftable making exactly that stack
      (type "item": name, damage, tag; "fluid": name) or nil, with
      getItemStack() (items only) and request(amount) -> status with
      isComputing/hasFailed/isDone/isCanceled
    getItemsInNetworkById(ids), getItemInNetwork(detail),
    getFluidsInNetwork()
    with opts.pattern_slots (an me_interface): getInterfacePattern(slot)
      and set/clearInterfacePatternInput/Output, as OC edits an encoded
      pattern in place (see FakeME:set_pattern); me:add_interface()
      adds another me_interface on the same network
    getEssentiaInNetwork() (Thaumic Energistics; leave it out with
      opts.without = { "getEssentiaInNetwork" })

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

-- Essentia as getEssentiaInNetwork() reports it: the aspect's tag,
-- its display name, and the amount.
function M.essentia(name, label, amount)
  return { name = name, label = label, amount = amount or 1 }
end

local function copy(t)
  local c = {}
  for k, v in pairs(t) do c[k] = v end
  return c
end

-- A pattern's stacks as OC returns them: keyed by position, empty
-- entries left out.
local function stacks_out(list)
  local out = {}
  for i, s in ipairs(list) do
    if s then out[i] = copy(s) end
  end
  return out
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
    essentia = {},
    statuses = {},
    failures = {},
    calls = {},
    interfaces = {},
    patterns_changed = -math.huge,
  }, FakeME)
  local methods = me:_methods()
  if opts.pattern_slots then
    local iface = me:_new_interface(opts.pattern_slots)
    me.patterns = iface.patterns
    for k, v in pairs(me:_interface_methods(iface)) do methods[k] = v end
  end
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
  if type(name) == "table" then return name end  -- a CPU, as add_cpu() gave it
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
  if job.on_end then job.on_end(how) end
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

-- getCraftable()'s match: AE2 finds this exact stack, NBT included.
local function makes(c, detail, stackType)
  if stackType == "fluid" then
    return c.fluid and c.stack.name == detail.name
  end
  return not c.fluid and c.stack.name == detail.name and c.stack.damage == (detail.damage or 0)
    and c.stack.tag == detail.tag
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
        on_end = c.on_end,
      }
      status.cpu = cpu.name
      if c.on_start then c.on_start() end
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
      self:end_job(target, "finished")
    end
  end
end

-- --------------------------------------------------------------- patterns

-- A pattern in an interface slot (1-based, as OC counts them):
--   inputs, outputs  stacks by position from 1; false for an empty entry
--   crafting         a crafting pattern rather than a processing one
-- A pattern makes its output craftable on the network's next tick, as
-- AE2 re-reads patterns then. me.kit says what requesting it does:
--   fake_card  the job ends once the inputs are pushed (default true);
--              without it, it never ends
--   returns    the pushed inputs come back into the network when it ends
--              (default true)
--   duration, fail  as add_craftable's
function FakeME:set_pattern(slot, pattern, iface)
  iface = iface or self.interfaces[1]
  local p = { inputs = {}, outputs = {}, crafting = pattern.crafting == true }
  for i, s in ipairs(pattern.inputs or {}) do p.inputs[i] = s and copy(s) or false end
  for i, s in ipairs(pattern.outputs or {}) do p.outputs[i] = s and copy(s) or false end
  iface.patterns[slot] = p
  self.patterns_changed = self.env.now
  return p
end

function FakeME:_new_interface(slots)
  local iface = { slots = slots, patterns = {} }
  self.interfaces[#self.interfaces + 1] = iface
  return iface
end

-- Another ME Interface on this network, with its own Adapter (an
-- me_interface component that can edit its patterns, and do what any
-- network component can). Returns it, for set_pattern().
function FakeME:add_interface(slots)
  local iface = self:_new_interface(slots or 9)
  local methods = self:_methods()
  for k, v in pairs(self:_interface_methods(iface)) do methods[k] = v end
  iface.proxy = self.env:add_component("me_interface", methods)
  return iface
end

function FakeME:_interface_methods(iface)
  local me = self
  local methods = {
    getInterfacePattern = function(slot)
      me:_call("getInterfacePattern")
      local p = me:_pattern_at(iface, slot)
      if not p then return nil end
      return { name = "appliedenergistics2:item.ItemEncodedPattern", size = 1, damage = 0,
        inputs = stacks_out(p.inputs), outputs = stacks_out(p.outputs), isCraftable = p.crafting }
    end,
  }
  for _, which in ipairs({ "Input", "Output" }) do
    local list = which == "Input" and "inputs" or "outputs"
    methods["setInterfacePattern" .. which] = function(slot, index, detail, stackType)
      me:_call("setInterfacePattern" .. which)
      return me:_edit_pattern(iface, list, slot, index, detail, stackType)
    end
    methods["clearInterfacePattern" .. which] = function(slot, index)
      me:_call("clearInterfacePattern" .. which)
      return me:_edit_pattern(iface, list, slot, index, nil)
    end
  end
  return methods
end

local function has_stack(list)
  for _, s in ipairs(list) do
    if s then return true end
  end
  return false
end

-- After every edit, as AE2 reads it: a pattern without inputs or
-- outputs is marked invalid in its NBT, for good.
function FakeME:_pattern_edited(p)
  if not (has_stack(p.inputs) and has_stack(p.outputs)) then p.invalid = true end
  self.patterns_changed = self.env.now
end

function FakeME:_pattern_at(iface, slot)
  slot = slot or 1
  if math.type(slot) ~= "integer" or slot < 1 or slot > iface.slots then
    error("invalid slot", 0)
  end
  return iface.patterns[slot]
end

-- OC's setPatternSlot: pads the list with empty entries up to index,
-- then sets it, or removes it when there's no stack.
function FakeME:_edit_pattern(iface, which, slot, index, detail, stackType)
  local p = self:_pattern_at(iface, slot)
  if math.type(index) ~= "integer" or index < 1 or index > 512 then error("Invalid index!", 0) end
  if not p then error("No pattern here!", 0) end
  local list = p[which]
  while #list < index do list[#list + 1] = false end
  if detail ~= nil then
    if stackType ~= "item" then error("Type " .. tostring(stackType) .. " hasn't been registered", 0) end
    -- A tag gives the stack a custom name: the test says which, as
    -- me.tag_labels[tag] (the fake can't read NBT).
    local label = detail.tag and (self.tag_labels or {})[detail.tag] or nil
    list[index] = { name = detail.name, damage = detail.damage or 0, size = detail.size or 1,
      tag = detail.tag, hasTag = detail.tag ~= nil or nil, label = label }
  else
    table.remove(list, index)
  end
  self:_pattern_edited(p)
  return true
end

function FakeME:_add_stock(stack, sign)
  for _, it in ipairs(self.items) do
    if it.name == stack.name and (it.damage or 0) == (stack.damage or 0) then
      it.size = it.size + sign * stack.size
      return
    end
  end
  if sign > 0 then self.items[#self.items + 1] = M.stack(stack.name, stack.name, stack.size, { damage = stack.damage }) end
end

-- The craftable a valid pattern in some slot gives detail, or nil.
function FakeME:_pattern_craftable(detail)
  if self.env.now <= self.patterns_changed then return nil end
  local kit = self.kit or {}
  for _, iface in ipairs(self.interfaces) do
    for _, p in pairs(iface.patterns) do
      local out = p.outputs[1]
      if not p.invalid and out and out.name == detail.name and out.damage == (detail.damage or 0)
          and out.tag == detail.tag then
        local me = self
        local inputs = {}
        for _, s in ipairs(p.inputs) do
          if s then inputs[#inputs + 1] = copy(s) end
        end
        return {
          stack = copy(out),
          plan_seconds = 1,
          duration = kit.fake_card == false and math.huge or (kit.duration or 2),
          fail = kit.fail,
          pending = { copy(out) },
          on_start = function()
            for _, s in ipairs(inputs) do me:_add_stock(s, -1) end
          end,
          on_end = function()
            if kit.returns ~= false then
              for _, s in ipairs(inputs) do me:_add_stock(s, 1) end
            end
          end,
        }
      end
    end
  end
  return nil
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
      return me:end_job(cpu, "cancelled")
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

    getCraftable = function(detail, stackType)
      me:_call("getCraftable")
      if stackType ~= "item" and stackType ~= "fluid" then
        error("Type " .. tostring(stackType) .. " hasn't been registered")
      end
      for _, c in ipairs(me.craftables) do
        if makes(c, detail, stackType) then
          local craftable = {
            request = function(amount) return me:_request(c, amount or 1) end,
          }
          if not c.fluid then
            craftable.getItemStack = function() return copy(c.stack) end
          end
          return craftable
        end
      end
      if stackType == "item" and #me.interfaces > 0 then
        local c = me:_pattern_craftable(detail)
        if c then
          return {
            request = function(amount) return me:_request(c, amount or 1) end,
            getItemStack = function() return copy(c.stack) end,
          }
        end
      end
      return nil
    end,

    getItemInNetwork = function(detail)
      me:_call("getItemInNetwork")
      for _, it in ipairs(me.items) do
        if it.name == detail.name and (it.damage or 0) == (detail.damage or 0) then return copy(it) end
      end
      return nil
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

    getEssentiaInNetwork = function()
      me:_call("getEssentiaInNetwork")
      local out = {}
      for i, e in ipairs(me.essentia) do out[i] = copy(e) end
      return out
    end,
  }
end

return M
