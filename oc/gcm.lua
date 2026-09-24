-- gcm.lua - installs and updates the GTNH Craft Monitor scripts from the
-- project's GitHub releases.
--
--   gcm install [craft] [power] [network]   asks which, if none are given
--   gcm update                              update to the latest release
--   gcm status                              installed and latest version
--
-- Add --ref=<tag or branch> to install or update from something other
-- than the latest release, e.g. --ref=main to test unreleased changes.
--
-- First install (this is the only download that comes from main). A fresh
-- OpenOS has no /usr/bin yet, so the first copy goes to /tmp; install then
-- puts the permanent copy in /usr/bin:
--
--   wget -f https://raw.githubusercontent.com/Maselkov/gtnh-craft-monitor/main/oc/gcm.lua /tmp/gcm.lua
--   /tmp/gcm.lua install
--
-- Self-contained on purpose: json.lua and http.lua may not be installed
-- yet when this runs.

local component = require("component")
local computer = require("computer")
local filesystem = require("filesystem")
local shell = require("shell")

local REPO = "Maselkov/gtnh-craft-monitor"
local STATE_PATH = "/etc/gcm.cfg"
local CONNECT_TIMEOUT_SECONDS = 30
local STALL_TIMEOUT_SECONDS = 30

local function raw_url(ref, src)
  return "https://raw.githubusercontent.com/" .. REPO .. "/" .. ref .. "/oc/" .. src
end

-- Opens a GET request and waits for the status line. Returns the request
-- handle on HTTP 200, or nil and an error message.
local function open_request(url)
  local inet = component.internet
  local ok, handle, reason = pcall(inet.request, url, nil, { ["User-Agent"] = "gcm" })
  if not ok or not handle then
    return nil, tostring(ok and reason or handle)
  end
  local started = computer.uptime()
  local code
  repeat
    local okConnect, connected, err = pcall(handle.finishConnect)
    if not okConnect or connected == nil then
      handle.close()
      return nil, tostring(okConnect and err or connected)
    end
    if connected then
      code = handle.response()
    end
    if not code then
      if computer.uptime() - started > CONNECT_TIMEOUT_SECONDS then
        handle.close()
        return nil, "timed out connecting"
      end
      os.sleep(0.05)
    end
  until code
  if code ~= 200 then
    handle.close()
    return nil, "HTTP " .. tostring(code)
  end
  return handle
end

-- Streams a response body into write(chunk). Returns the byte count, or
-- nil and an error message.
local function read_body(handle, write)
  local total = 0
  local lastData = computer.uptime()
  while true do
    local ok, data, err = pcall(handle.read, 8192)
    if not ok or data == nil then
      handle.close()
      if not ok or err then
        return nil, tostring(ok and err or data)
      end
      return total
    end
    if #data > 0 then
      write(data)
      total = total + #data
      lastData = computer.uptime()
    elseif computer.uptime() - lastData > STALL_TIMEOUT_SECONDS then
      handle.close()
      return nil, "timed out waiting for data"
    else
      os.sleep(0.05)
    end
  end
end

local function fetch_text(url)
  local handle, err = open_request(url)
  if not handle then
    return nil, err
  end
  local chunks = {}
  local total, readErr = read_body(handle, function(chunk)
    chunks[#chunks + 1] = chunk
  end)
  if not total then
    return nil, readErr
  end
  if total == 0 then
    return nil, "empty response"
  end
  return table.concat(chunks)
end

-- Written straight to disk instead of buffered, so the item catalog
-- doesn't need to fit in RAM.
local function download(url, path)
  local handle, err = open_request(url)
  if not handle then
    return nil, err
  end
  local file, openErr = io.open(path, "wb")
  if not file then
    handle.close()
    return nil, tostring(openErr)
  end
  local total, readErr = read_body(handle, function(chunk)
    file:write(chunk)
  end)
  file:close()
  if not total then
    return nil, readErr
  end
  if total == 0 then
    return nil, "empty response"
  end
  return true
end

local function newer(a, b)
  for i = 1, 3 do
    if a[i] ~= b[i] then
      return a[i] > b[i]
    end
  end
  return false
end

-- Highest vX.Y.Z tag. The tags API doesn't sort by version, and
-- pre-release tags like v1.2.0-rc1 are skipped.
local function latest_tag()
  local body, err = fetch_text("https://api.github.com/repos/" .. REPO .. "/tags?per_page=100")
  if not body then
    error("couldn't list releases from api.github.com: " .. err, 0)
  end
  local best, bestVersion
  for tag in body:gmatch('"name"%s*:%s*"(v%d+%.%d+%.%d+)"') do
    local major, minor, patch = tag:match("^v(%d+)%.(%d+)%.(%d+)$")
    local version = { tonumber(major), tonumber(minor), tonumber(patch) }
    if not bestVersion or newer(version, bestVersion) then
      best, bestVersion = tag, version
    end
  end
  if not best then
    error("no release tags found for " .. REPO, 0)
  end
  return best
end

local function get_manifest(ref)
  local body, err = fetch_text(raw_url(ref, "manifest.lua"))
  if not body then
    error("couldn't download manifest.lua for " .. ref .. " (" .. err .. "). "
      .. "Releases made before gcm existed can't be installed with it.", 0)
  end
  local chunk, loadErr = load(body, "=manifest.lua", "t", {})
  if not chunk then
    error("manifest.lua for " .. ref .. " is invalid: " .. loadErr, 0)
  end
  return chunk()
end

local function find_component(manifest, name)
  for _, c in ipairs(manifest.components) do
    if c.name == name then
      return c
    end
  end
  return nil
end

local function component_names(manifest)
  local names = {}
  for _, c in ipairs(manifest.components) do
    names[#names + 1] = c.name
  end
  return table.concat(names, ", ")
end

local function files_for(manifest, names)
  local files = {}
  for _, f in ipairs(manifest.shared) do
    files[#files + 1] = f
  end
  for _, name in ipairs(names) do
    for _, f in ipairs(find_component(manifest, name).files) do
      files[#files + 1] = f
    end
  end
  return files
end

-- Downloads every file to <dst>.new first and only moves them into place
-- once all of them arrived, so a failed download leaves the existing
-- install untouched.
local function install_files(ref, files)
  local staged = {}
  local ok, err = pcall(function()
    for _, f in ipairs(files) do
      print("  " .. f.dst)
      local dir = filesystem.path(f.dst)
      if not filesystem.exists(dir) then
        filesystem.makeDirectory(dir)
      end
      local tmp = f.dst .. ".new"
      staged[#staged + 1] = tmp
      local downloaded, downloadErr = download(raw_url(ref, f.src), tmp)
      if not downloaded then
        error(f.src .. ": " .. downloadErr, 0)
      end
    end
  end)
  if not ok then
    for _, tmp in ipairs(staged) do
      filesystem.remove(tmp)
    end
    error("download failed, nothing was changed. " .. err, 0)
  end
  for _, f in ipairs(files) do
    if filesystem.exists(f.dst) then
      filesystem.remove(f.dst)
    end
    local moved, moveErr = filesystem.rename(f.dst .. ".new", f.dst)
    if not moved then
      error("couldn't move " .. f.dst .. ".new into place: " .. tostring(moveErr), 0)
    end
  end
end

local function read_state()
  local file = io.open(STATE_PATH, "r")
  if not file then
    return nil
  end
  local body = file:read("*a")
  file:close()
  local chunk = load(body, "=" .. STATE_PATH, "t", {})
  local ok, state = pcall(chunk or error)
  if ok and type(state) == "table" and type(state.components) == "table" then
    return state
  end
  return nil
end

local function write_state(state)
  local quoted = {}
  for i, name in ipairs(state.components) do
    quoted[i] = string.format("%q", name)
  end
  local file = assert(io.open(STATE_PATH, "w"))
  file:write("-- Written by gcm. Lists what `gcm update` updates.\n")
  file:write("return {\n")
  file:write("  version = ", string.format("%q", state.version), ",\n")
  file:write("  components = { ", table.concat(quoted, ", "), " },\n")
  file:write("}\n")
  file:close()
end

local function ask(question, default)
  io.write(question, default and " [Y/n] " or " [y/N] ")
  local answer = io.read()
  if not answer or answer == "" then
    return default
  end
  return answer:sub(1, 1):lower() == "y"
end

local function prompt(question)
  io.write(question, ": ")
  local answer = io.read() or ""
  return (answer:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function replace_once(text, old, new)
  local first, last = text:find(old, 1, true)
  if not first then
    return text, false
  end
  return text:sub(1, first - 1) .. new .. text:sub(last + 1), true
end

-- Creates config.lua from the release's template, filling in the server
-- URL and API key. An existing config.lua is never touched.
local function write_config(ref, cfg)
  if filesystem.exists(cfg.dst) then
    print("Keeping existing " .. cfg.dst)
    return
  end
  local template, err = fetch_text(raw_url(ref, cfg.src))
  if not template then
    error("couldn't download " .. cfg.src .. ": " .. err, 0)
  end
  print()
  print("Creating " .. cfg.dst .. ". Leave a value blank to fill it in later.")
  local url = prompt("Server URL, e.g. https://monitor.example.com"):gsub("/+$", "")
  local key = prompt("API key (the server's API_KEY)")
  local replaced
  if url ~= "" then
    template, replaced = replace_once(template, '"https://YOUR-SERVER-HOST:8420"', string.format("%q", url))
    if not replaced then
      print("Couldn't find the SERVER_URL placeholder; set it in " .. cfg.dst .. " yourself.")
    end
  end
  if key ~= "" then
    template, replaced = replace_once(template, '"change-me"', string.format("%q", key))
    if not replaced then
      print("Couldn't find the API_KEY placeholder; set it in " .. cfg.dst .. " yourself.")
    end
  end
  local dir = filesystem.path(cfg.dst)
  if not filesystem.exists(dir) then
    filesystem.makeDirectory(dir)
  end
  local file = assert(io.open(cfg.dst, "w"))
  file:write(template)
  file:close()
  if url == "" or key == "" then
    print("Edit " .. cfg.dst .. " to set the missing values before starting the scripts.")
  end
end

-- rc caches loaded services, and http.lua/json.lua stay in package.loaded,
-- so `rc <name> restart` would keep running the old code. A reboot loads
-- everything fresh.
local function offer_reboot()
  print()
  if ask("Reboot now to run the new version?", true) then
    computer.shutdown(true)
  else
    print("The new version runs after the next reboot.")
  end
end

local function require_internet()
  if not component.isAvailable("internet") then
    error("no Internet Card found", 0)
  end
end

local function cmd_install(args, opts)
  require_internet()
  local ref = opts.ref or latest_tag()
  local manifest = get_manifest(ref)

  local chosen = {}
  if #args > 0 then
    for _, name in ipairs(args) do
      if not find_component(manifest, name) then
        error("unknown component '" .. name .. "'. Choose from: " .. component_names(manifest), 0)
      end
      chosen[#chosen + 1] = name
    end
  else
    for _, c in ipairs(manifest.components) do
      if ask("Install " .. c.name .. " (" .. c.description .. ")?", true) then
        chosen[#chosen + 1] = c.name
      end
    end
  end
  if #chosen == 0 then
    print("Nothing selected.")
    return
  end

  -- Keep components from an earlier install, so update still covers them.
  local previous = read_state()
  local all = {}
  local seen = {}
  for _, list in ipairs({ previous and previous.components or {}, chosen }) do
    for _, name in ipairs(list) do
      if not seen[name] and find_component(manifest, name) then
        seen[name] = true
        all[#all + 1] = name
      end
    end
  end

  print("Installing " .. ref .. ":")
  install_files(ref, files_for(manifest, all))
  write_state({ version = ref, components = all })
  write_config(ref, manifest.config)

  print()
  if ask("Start the installed scripts automatically on every boot?", true) then
    for _, name in ipairs(chosen) do
      shell.execute("rc " .. find_component(manifest, name).service .. " enable")
    end
  end
  offer_reboot()
end

local function cmd_update(opts)
  require_internet()
  local state = read_state()
  if not state then
    error("nothing installed yet; run `gcm install` first", 0)
  end
  local ref = opts.ref or latest_tag()
  if not opts.ref and ref == state.version then
    print("Already up to date (" .. ref .. ").")
    return
  end
  local manifest = get_manifest(ref)

  local names = {}
  for _, name in ipairs(state.components) do
    if find_component(manifest, name) then
      names[#names + 1] = name
    else
      print("Skipping '" .. name .. "': " .. ref .. " doesn't include it.")
    end
  end

  print("Updating " .. state.version .. " -> " .. ref .. ":")
  install_files(ref, files_for(manifest, names))
  write_state({ version = ref, components = names })
  offer_reboot()
end

local function cmd_status()
  local state = read_state()
  if state then
    print("Installed:  " .. state.version)
    print("Components: " .. table.concat(state.components, ", "))
  else
    print("Not installed.")
  end
  if not component.isAvailable("internet") then
    print("Latest:     unknown (no Internet Card)")
    return
  end
  local ok, latest = pcall(latest_tag)
  print("Latest:     " .. (ok and latest or "unknown (" .. tostring(latest) .. ")"))
end

local function usage()
  print("Usage:")
  print("  gcm install [craft] [power] [network]")
  print("  gcm update")
  print("  gcm status")
  print("Options:")
  print("  --ref=<tag or branch>   use this instead of the latest release")
end

local function main(...)
  local args, opts = shell.parse(...)
  local command = table.remove(args, 1)
  if opts.ref == true then
    error("--ref needs a value, e.g. --ref=main", 0)
  end
  if command == "install" then
    cmd_install(args, opts)
  elseif command == "update" then
    cmd_update(opts)
  elseif command == "status" then
    cmd_status()
  else
    usage()
  end
end

local ok, err = pcall(main, ...)
if not ok then
  io.stderr:write("gcm: " .. tostring(err) .. "\n")
  return 1
end
