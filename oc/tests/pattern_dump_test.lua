local t = require("testlib")

local suite = t.suite()
local test = suite.test

-- me_interface_terminal as the GTNH OC source describes it:
-- getInterfaces() returns a list value that, called, gives the next
-- interface ({name, location, side, patterns}) and nil at the end.
local function add_terminal(env, interfaces, opts)
  opts = opts or {}
  return env:add_component("me_interface_terminal", {
    getInterfaces = function()
      if opts.fail then error(opts.fail) end
      local i = 0
      return setmetatable({
        count = function() return #interfaces end,
      }, {
        __call = function()
          i = i + 1
          return interfaces[i]
        end,
      })
    end,
  })
end

local function item(name, size, label)
  return { name = name, damage = 0, size = size, label = label }
end

local function pattern(inputs, outputs, craftable)
  return { name = "appliedenergistics2:item.ItemEncodedPattern", inputs = inputs, outputs = outputs,
    isCraftable = craftable }
end

local function dumps(env)
  local out = {}
  for _, r in ipairs(env:requests_to("^/api/debug$", "POST")) do out[#out + 1] = r.json end
  return out
end

test("posts a summary, one example per pattern shape and full interfaces", function()
  local env = t.env()
  local plate = pattern({ item("gregtech:ingot", 1, "Iron Ingot") }, { item("gregtech:plate", 1, "Iron Plate") }, false)
  add_terminal(env, {
    { name = "Plates", location = { x = 1, y = 2, z = 3 }, side = "NORTH", patterns = { [0] = plate, [1] = plate } },
    { name = "Empty", location = { x = 4, y = 5, z = 6 }, side = "UNKNOWN", patterns = {} },
    { name = "Fluids", location = { x = 7, y = 8, z = 9 }, side = "UP", patterns = {
      [0] = pattern({ { name = "water", amount = 1000 } }, { item("minecraft:ice", 1, "Ice") }, false),
    } },
  })
  env:load_service("pattern_dump")

  local posts = dumps(env)
  t.eq(#posts, 2)
  t.eq(posts[1].tag, "pattern_dump summary")
  t.truthy(posts[1].dump:find("interfaces walked: 3", 1, true), posts[1].dump)
  t.truthy(posts[1].dump:find("interfaces with patterns: 2", 1, true))
  t.truthy(posts[1].dump:find("patterns: 3", 1, true))
  t.eq(posts[2].tag, "pattern_dump 1/1")
  local body = posts[2].dump
  t.truthy(body:find("shape 1: 2 pattern(s)", 1, true), body)
  t.truthy(body:find("inputs{amount,name}", 1, true), body)
  t.truthy(body:find("interface 2 in full", 1, true))
  t.truthy(not body:find("interface 3 in full", 1, true))
  t.truthy(body:find('["amount"] = 1000,', 1, true))
end)

test("splits a large dump into numbered posts", function()
  local env = t.env()
  local inputs = {}
  for i = 1, 40 do inputs[i] = item("mod:item" .. i, i, string.rep("x", 100)) end
  local patterns = {}
  for i = 0, 35 do patterns[i] = pattern(inputs, { item("mod:out", 1, "Out") }, true) end
  add_terminal(env, { { name = "Big", patterns = patterns } })
  env:load_service("pattern_dump")

  local posts = dumps(env)
  t.truthy(#posts > 2, "expected several parts, got " .. #posts)
  t.eq(posts[2].tag, "pattern_dump 1/" .. (#posts - 1))
end)

test("reports a failing getInterfaces()", function()
  local env = t.env()
  add_terminal(env, {}, { fail = "no grid" })
  env:load_service("pattern_dump")
  local posts = dumps(env)
  t.eq(#posts, 1)
  t.eq(posts[1].tag, "pattern_dump error")
  t.truthy(posts[1].dump:find("no grid", 1, true))
end)

test("says what it needs when there's no terminal", function()
  local env = t.env()
  env:add_component("me_interface", {})
  env:load_service("pattern_dump")
  t.eq(#dumps(env), 0)
  t.truthy(env:printed("No me_interface_terminal component"))
  t.truthy(env:printed("me_interface "))
end)

return suite
