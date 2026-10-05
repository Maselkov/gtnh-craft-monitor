--[[
  A fake ME Interface Terminal (me_interface_terminal) for oc_env, as
  GTNH 2.9's OpenComputers source has it and oc/pattern_dump.lua saw it
  on a real network: getInterfaces() returns a list that, called, gives
  the next interface {name, location = {x, y, z, dimId}, patterns} and
  nil at the end; list.count() says how many. patterns is keyed by slot
  from 0, a pattern's inputs/outputs by position from 1.

  What it doesn't model: a real crafting pattern reports every size as
  0 and carries its NBT as gzipped bytes (tag) - tests pass those in
  as they want them.

  Errors: term:fail("getInterfaces", message) makes that call raise;
  term.fail_list_at = n makes the list's nth call raise.
--]]

local M = {}

-- An item in a pattern. name is "modid:internalname"; id is the numeric
-- item id the pattern's NBT uses.
function M.item(name, label, size, extra)
  local s = { name = name, label = label, size = size or 1, damage = 0, hasTag = false, id = 1,
    isCraftable = false }
  for k, v in pairs(extra or {}) do s[k] = v end
  return s
end

-- A fluid in a pattern: amount (and size) in mB.
function M.fluid(name, label, amount)
  return { name = name, label = label, amount = amount, size = amount, id = 1, hasTag = false,
    isCraftable = false }
end

-- Essentia: an amount and no item id.
function M.essentia(name, label, amount)
  return { name = name, label = label, amount = amount, size = amount, isCraftable = false }
end

-- opts: crafting (a crafting-table pattern), tag (its NBT, raw bytes).
function M.pattern(inputs, outputs, opts)
  opts = opts or {}
  return {
    name = opts.crafting and "appliedenergistics2:item.ItemEncodedPattern"
      or "appliedenergistics2:item.ItemEncodedUltimatePattern",
    label = "Encoded Pattern",
    isCraftable = opts.crafting and true or false,
    hasTag = true,
    tag = opts.tag,
    inputs = inputs,
    outputs = outputs,
  }
end

-- patterns: a list, stored from slot 0.
function M.interface(name, x, y, z, patterns)
  local slots = {}
  for i, p in ipairs(patterns or {}) do slots[i - 1] = p end
  return { name = name, location = { x = x, y = y, z = z, dimId = 0 }, patterns = slots }
end

local Terminal = {}
Terminal.__index = Terminal

function M.new(env, interfaces)
  local term = setmetatable({ interfaces = interfaces or {}, failures = {}, list_calls = 0 }, Terminal)
  term.proxy = env:add_component("me_interface_terminal", {
    getInterfaces = function() return term:_list() end,
  })
  return term
end

function Terminal:fail(method, message)
  self.failures[method] = message or (method .. " failed")
end

function Terminal:_list()
  if self.failures.getInterfaces then error(self.failures.getInterfaces) end
  -- A snapshot, as the real one is.
  local snapshot = {}
  for i, info in ipairs(self.interfaces) do snapshot[i] = info end
  local i = 0
  local term = self
  return setmetatable({
    count = function() return #snapshot end,
  }, {
    __call = function()
      term.list_calls = term.list_calls + 1
      if term.fail_list_at and term.list_calls == term.fail_list_at then error("list broke") end
      i = i + 1
      return snapshot[i]
    end,
  })
end

return M
