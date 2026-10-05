--[[
  pattern_dump.lua - ONE-OFF DIAGNOSTIC, not a production script.

  Read-only: dumps what the ME Interface Terminal driver (GTNH 2.9's
  OpenComputers, PR #173) returns for the network's patterns, to see
  whether it carries enough to build a recipe tree on the site. Going
  by the OC source, getInterfaces() snapshots every interface the
  terminal would list, and each pattern converts to
  {inputs=..., outputs=..., isCraftable=...} - but that's never been
  seen on a real network, so this sends what actually comes back.

  Needs an Adapter touching a block that has an ME Interface Terminal
  part on it (the me_interface_terminal component), plus config.lua
  and the http/json libraries gcm installs.

  Run it directly (not via rc):
    pattern_dump        full dump of the first 2 interfaces with patterns
    pattern_dump 5      ... of the first 5

  Everything goes to the server's /api/debug, which keeps the last 10
  posts - open it in a browser. The screen only gets a summary.
--]]

local component = require("component")

local CONFIG_PATH = "/home/config.lua"
local FULL_DUMPS = tonumber((...)) or 2
-- Each distinct shape of pattern (which keys a pattern and its
-- inputs/outputs carry) gets one example in the dump, up to this many.
local MAX_SHAPES = 12
-- Per /api/debug post. The server takes up to 1MB, but OC's memory is
-- the tighter limit for building and encoding the body.
local CHUNK_CHARS = 48 * 1024
-- /api/debug keeps 10 posts; the summary needs one of them.
local MAX_CHUNKS = 9

local function log(msg)
  print("[pattern_dump] " .. msg)
end

local configOk, config = pcall(dofile, CONFIG_PATH)
if not configOk then
  log("Could not load " .. CONFIG_PATH .. ": " .. tostring(config))
  return
end
local jsonOk, json = pcall(require, "json")
local httpOk, http = pcall(require, "http")
if not (jsonOk and httpOk) then
  log("Could not require json/http - deploy json.lua and http.lua to /usr/lib/.")
  return
end
local DEBUG_URL = config.SERVER_URL .. "/api/debug"

local function post_debug(tag, text)
  local body = json.json_encode({ tag = tag, dump = text })
  local ok, result = http.request_with_timeout(DEBUG_URL, body, {
    ["Content-Type"] = "application/json",
    ["X-API-Key"] = config.API_KEY,
  }, 20)
  log("POST '" .. tag .. "': " .. (ok and "OK" or ("FAILED - " .. tostring(result))))
end

-- ------------------------------------------------------------ printing

-- Shows types as well as values: whether a size is an integer or a
-- float, a key a number or a string, matters for parsing it later.
local function key_order(a, b)
  local ta, tb = type(a), type(b)
  if ta ~= tb then return ta < tb end
  if ta == "number" or ta == "string" then return a < b end
  return tostring(a) < tostring(b)
end

local function show_scalar(v)
  local tv = type(v)
  if tv == "string" then return string.format("%q", v) end
  if tv == "number" then return tostring(v) .. (math.type(v) == "float" and " (float)" or "") end
  if tv == "table" then return "{...}" end
  if tv == "boolean" or tv == "nil" then return tostring(v) end
  return "<" .. tv .. " " .. tostring(v) .. ">"
end

local function dump(v, out, indent, depth)
  if type(v) ~= "table" then
    out[#out + 1] = show_scalar(v)
    return
  end
  if depth > 10 then
    out[#out + 1] = "{ <deeper than 10 levels> }"
    return
  end
  local keys = {}
  for k in pairs(v) do keys[#keys + 1] = k end
  if #keys == 0 then
    out[#out + 1] = "{}"
    return
  end
  table.sort(keys, key_order)
  out[#out + 1] = "{\n"
  local inner = indent .. "  "
  for _, k in ipairs(keys) do
    out[#out + 1] = inner .. "[" .. show_scalar(k) .. "] = "
    dump(v[k], out, inner, depth + 1)
    out[#out + 1] = ",\n"
  end
  out[#out + 1] = indent .. "}"
end

local function dumped(v)
  local out = {}
  dump(v, out, "", 0)
  return table.concat(out)
end

-- ------------------------------------------------------------- shapes

local function key_list(t)
  if type(t) ~= "table" then return type(t) end
  local keys = {}
  for k in pairs(t) do keys[#keys + 1] = tostring(k) end
  table.sort(keys)
  return table.concat(keys, ",")
end

-- The keys of one stack list's entries, each distinct set once:
-- "label,name,size|amount,name" for a list mixing items and fluids.
local function entry_shapes(list)
  if type(list) ~= "table" then return type(list) end
  local seen, shapes = {}, {}
  for _, entry in pairs(list) do
    local s = key_list(entry)
    if not seen[s] then
      seen[s] = true
      shapes[#shapes + 1] = s
    end
  end
  table.sort(shapes)
  return table.concat(shapes, "|")
end

local function pattern_shape(p)
  if type(p) ~= "table" then return type(p) end
  return "pattern{" .. key_list(p) .. "} inputs{" .. entry_shapes(p.inputs)
    .. "} outputs{" .. entry_shapes(p.outputs) .. "}"
end

-- -------------------------------------------------------------- main

local function find_terminal()
  local addresses = {}
  for address in component.list("me_interface_terminal", true) do
    addresses[#addresses + 1] = address
  end
  if #addresses > 0 then
    return component.proxy(addresses[1]), #addresses
  end
  local me = {}
  for address, ctype in component.list() do
    if ctype:find("me_", 1, true) then me[#me + 1] = ctype .. " " .. address end
  end
  log("No me_interface_terminal component. Put an Adapter against a block")
  log("holding an ME Interface Terminal part.")
  log("ME components seen: " .. (#me > 0 and table.concat(me, ", ") or "none"))
  return nil
end

local function method_docs(terminal)
  if not (component.methods and component.doc) then return "(component.methods/doc unavailable)" end
  local ok, methods = pcall(component.methods, terminal.address)
  if not ok or type(methods) ~= "table" then return "component.methods failed: " .. tostring(methods) end
  local names = {}
  for name in pairs(methods) do names[#names + 1] = name end
  table.sort(names)
  local lines = {}
  for _, name in ipairs(names) do
    local docOk, doc = pcall(component.doc, terminal.address, name)
    lines[#lines + 1] = name .. ": " .. tostring(docOk and doc or "?")
  end
  return table.concat(lines, "\n")
end

local function count_patterns(patterns)
  local n = 0
  if type(patterns) == "table" then
    for _, p in pairs(patterns) do
      if p ~= nil then n = n + 1 end
    end
  end
  return n
end

local function main()
  local terminal, terminals = find_terminal()
  if not terminal then return end
  if terminals > 1 then log("Found " .. terminals .. " terminals, using " .. terminal.address) end

  local ok, list = pcall(terminal.getInterfaces)
  if not ok then
    log("getInterfaces() failed: " .. tostring(list))
    post_debug("pattern_dump error", "getInterfaces() failed: " .. tostring(list)
      .. "\n\nMethods:\n" .. method_docs(terminal))
    return
  end

  local countOk, listed = pcall(function() return list.count() end)
  local stats = { interfaces = 0, with_patterns = 0, patterns = 0, names = {} }
  local shapes, shape_order = {}, {}
  local full = {}

  -- Calling the list returns the next interface, then nil at the end.
  -- Bounded in case it never returns nil.
  local limit = (countOk and tonumber(listed) or 100000) + 1
  for _ = 1, limit do
    local callOk, info = pcall(list)
    if not callOk then
      stats.error = "list() failed after " .. stats.interfaces .. " interfaces: " .. tostring(info)
      break
    end
    if info == nil then break end
    stats.interfaces = stats.interfaces + 1
    local n = count_patterns(type(info) == "table" and info.patterns)
    if n > 0 then
      stats.with_patterns = stats.with_patterns + 1
      stats.patterns = stats.patterns + n
      if #full < FULL_DUMPS then full[#full + 1] = info end
      for slot, p in pairs(info.patterns) do
        local s = pattern_shape(p)
        if shapes[s] then
          shapes[s].count = shapes[s].count + 1
        elseif #shape_order < MAX_SHAPES then
          shapes[s] = { count = 1, slot = slot, name = info.name, example = p }
          shape_order[#shape_order + 1] = s
        end
      end
    end
    local name = type(info) == "table" and tostring(info.name) or "?"
    stats.names[name] = (stats.names[name] or 0) + 1
  end

  local summary = {
    "terminal: " .. terminal.address,
    "list.count(): " .. (countOk and tostring(listed) or ("failed - " .. tostring(listed))),
    "interfaces walked: " .. stats.interfaces,
    "interfaces with patterns: " .. stats.with_patterns,
    "patterns: " .. stats.patterns,
  }
  if stats.error then summary[#summary + 1] = "ERROR: " .. stats.error end
  local names = {}
  for name, n in pairs(stats.names) do names[#names + 1] = string.format("  %5d  %s", n, name) end
  table.sort(names)
  summary[#summary + 1] = "\ninterface names:\n" .. table.concat(names, "\n")
  summary[#summary + 1] = "\nmethods:\n" .. method_docs(terminal)
  for _, line in ipairs(summary) do
    if not line:find("\n") then log(line) end
  end
  post_debug("pattern_dump summary", table.concat(summary, "\n"))

  local parts = {}
  for i, s in ipairs(shape_order) do
    local shape = shapes[s]
    parts[#parts + 1] = string.format("----- shape %d: %d pattern(s), e.g. slot %s of %q\n%s\n%s",
      i, shape.count, tostring(shape.slot), tostring(shape.name), s, dumped(shape.example))
  end
  if #shape_order == MAX_SHAPES then
    parts[#parts + 1] = "(stopped collecting shapes at " .. MAX_SHAPES .. ")"
  end
  for i, info in ipairs(full) do
    parts[#parts + 1] = "----- interface " .. i .. " in full\n" .. dumped(info)
  end
  local text = table.concat(parts, "\n\n")

  local chunks = math.max(1, math.ceil(#text / CHUNK_CHARS))
  local sent = math.min(chunks, MAX_CHUNKS)
  for i = 1, sent do
    local piece = text:sub((i - 1) * CHUNK_CHARS + 1, i * CHUNK_CHARS)
    if i == sent and sent < chunks then
      piece = piece .. "\n\n(cut off: " .. (chunks - sent) .. " more part(s) didn't fit; run with a smaller count)"
    end
    post_debug(string.format("pattern_dump %d/%d", i, sent), piece)
  end
  log("Done - open " .. DEBUG_URL .. " in a browser.")
end

main()
