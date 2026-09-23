--[[
  json.lua - JSON encode/decode, shared by craft_monitor.lua and
  network_browser.lua (not power_monitor.lua - see the bottom of this
  header for why).

  Deploy to /usr/lib/json.lua and load via require("json") - a real
  library, not dofile()'d by absolute path like config.lua is.
  This works reliably even under rc's sandboxed loader for a specific
  reason: OpenOS's own package.lua sets
  package.path = "/lib/?.lua;/usr/lib/?.lua;/home/lib/?.lua;./?.lua;..."
  (confirmed directly from OpenComputers' own source, not assumed) -
  /usr/lib/?.lua is a fixed, ABSOLUTE entry there, not resolved relative
  to wherever the calling script happens to live. That's a genuinely
  different thing from the directory-relative search (a bare require()
  looking for a file next to the calling script) whose behavior under
  rc was never confirmed and is why config.lua still uses
  dofile() instead - this doesn't depend on that same uncertain
  behavior at all.

  One thing this does NOT resolve, worth being honest about: whether
  rc's sandbox preserves the standard package/require machinery
  completely unmodified as part of managing a service. The path being
  absolute rules out the SPECIFIC concern that motivated dofile()
  elsewhere in this project, but it isn't the same as having confirmed
  require() itself behaves identically under rc - worth actually
  watching after deploying this, the same "verify empirically" spirit
  as sensor_info_dump.lua, rather than assumed to definitely work.

  Previously duplicated across scripts (json_encode existed separately
  in craft_monitor.lua and network_browser.lua; json_decode was
  literally copy-pasted from craft_monitor.lua into network_browser.lua
  when the scan-token work needed it) - confirmed by diffing them
  directly that the two json_encode copies had only ever drifted in
  formatting, never in actual behavior, so consolidating here doesn't
  change what either script does, just removes the duplication. This
  is exactly the kind of thing worth sharing: the json_encode-crash pcall
  fix earlier in this project had to be applied separately to multiple
  call sites across multiple files - a fix like that only needs to
  happen once against this single copy from now on.

  OpenComputers has no built-in JSON library (confirmed against its own
  official API list - buffer, colors, component, computer, event,
  filesystem, internet, sides, term, text, unicode, and a handful of
  others, no json among them) and its serialization library is OC's own
  Lua-table-literal format, not JSON, so it wouldn't interoperate with
  the server's json.loads() at all. Third-party Lua JSON libraries exist
  but would mean vendoring a separate file into the OC filesystem and
  keeping it in sync - real self-contained deployment cost for a need
  this narrow (just the flat-ish shapes this project's own endpoints
  send and expect, not full JSON-spec compliance). Hand-rolled and kept
  in-repo instead, matching how this whole project is built: every
  script a self-contained file you drop in and run.

  power_monitor.lua doesn't need either function - its payload is always
  a flat object of plain numbers, built directly via string.format - so
  it doesn't load this file at all.
--]]

local function is_array(t)
  local n = 0
  for k, _ in pairs(t) do
    if type(k) ~= "number" or k < 1 or math.floor(k) ~= k then
      return false
    end
    n = n + 1
  end
  for i = 1, n do
    if t[i] == nil then return false end
  end
  return true
end

local function json_encode(v, seen)
  seen = seen or {}
  local t = type(v)
  if t == "nil" then
    return "null"
  elseif t == "boolean" then
    return tostring(v)
  elseif t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "0" end
    return tostring(v)
  elseif t == "string" then
    local s = v:gsub('[\\"\n\r\t]', {
      ['\\'] = '\\\\', ['"'] = '\\"', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t'
    })
    return '"' .. s .. '"'
  elseif t == "table" then
    if seen[v] then return '"<circular>"' end
    seen[v] = true
    local parts = {}
    if is_array(v) then
      for i = 1, #v do
        parts[#parts + 1] = json_encode(v[i], seen)
      end
      seen[v] = nil
      return "[" .. table.concat(parts, ",") .. "]"
    else
      for k, val in pairs(v) do
        parts[#parts + 1] = json_encode(tostring(k), seen) .. ":" .. json_encode(val, seen)
      end
      seen[v] = nil
      return "{" .. table.concat(parts, ",") .. "}"
    end
  else
    return '"' .. tostring(v) .. '"'
  end
end

local function json_decode(str)
  local pos = 1
  local len = #str

  local function skip_ws()
    while pos <= len do
      local c = str:sub(pos, pos)
      if c == " " or c == "\t" or c == "\n" or c == "\r" then pos = pos + 1 else break end
    end
  end

  local parse_value

  local function parse_string()
    pos = pos + 1
    local parts = {}
    while pos <= len do
      local c = str:sub(pos, pos)
      if c == '"' then
        pos = pos + 1
        return table.concat(parts)
      elseif c == '\\' then
        local nextC = str:sub(pos + 1, pos + 1)
        if nextC == 'n' then parts[#parts + 1] = '\n'
        elseif nextC == 't' then parts[#parts + 1] = '\t'
        elseif nextC == 'r' then parts[#parts + 1] = '\r'
        elseif nextC == '"' then parts[#parts + 1] = '"'
        elseif nextC == '\\' then parts[#parts + 1] = '\\'
        elseif nextC == '/' then parts[#parts + 1] = '/'
        elseif nextC == 'u' then
          local hex = str:sub(pos + 2, pos + 5)
          local code = tonumber(hex, 16) or 63
          parts[#parts + 1] = string.char(code < 256 and code or 63)
          pos = pos + 4
        else
          parts[#parts + 1] = nextC
        end
        pos = pos + 2
      else
        parts[#parts + 1] = c
        pos = pos + 1
      end
    end
    error("unterminated string in JSON")
  end

  local function parse_number()
    local start = pos
    while pos <= len do
      local c = str:sub(pos, pos)
      if c:match("[%d%.%-%+eE]") then pos = pos + 1 else break end
    end
    return tonumber(str:sub(start, pos - 1))
  end

  local function parse_array()
    pos = pos + 1
    local arr = {}
    skip_ws()
    if str:sub(pos, pos) == "]" then pos = pos + 1; return arr end
    while true do
      skip_ws()
      arr[#arr + 1] = parse_value()
      skip_ws()
      local c = str:sub(pos, pos)
      if c == "," then pos = pos + 1
      elseif c == "]" then pos = pos + 1; break
      else error("expected , or ] in JSON array, got: " .. tostring(c)) end
    end
    return arr
  end

  local function parse_object()
    pos = pos + 1
    local obj = {}
    skip_ws()
    if str:sub(pos, pos) == "}" then pos = pos + 1; return obj end
    while true do
      skip_ws()
      if str:sub(pos, pos) ~= '"' then error("expected string key in JSON object") end
      local key = parse_string()
      skip_ws()
      if str:sub(pos, pos) ~= ":" then error("expected : in JSON object") end
      pos = pos + 1
      skip_ws()
      obj[key] = parse_value()
      skip_ws()
      local c = str:sub(pos, pos)
      if c == "," then pos = pos + 1
      elseif c == "}" then pos = pos + 1; break
      else error("expected , or } in JSON object, got: " .. tostring(c)) end
    end
    return obj
  end

  parse_value = function()
    skip_ws()
    local c = str:sub(pos, pos)
    if c == '"' then return parse_string()
    elseif c == "{" then return parse_object()
    elseif c == "[" then return parse_array()
    elseif c == "-" or c:match("%d") then return parse_number()
    elseif str:sub(pos, pos + 3) == "true" then pos = pos + 4; return true
    elseif str:sub(pos, pos + 4) == "false" then pos = pos + 5; return false
    elseif str:sub(pos, pos + 3) == "null" then pos = pos + 4; return nil
    else error("unexpected character in JSON at pos " .. pos .. ": " .. tostring(c)) end
  end

  skip_ws()
  local ok, result = pcall(parse_value)
  if not ok then return nil, result end
  return result, nil
end

return {
  json_encode = json_encode,
  json_decode = json_decode,
}
