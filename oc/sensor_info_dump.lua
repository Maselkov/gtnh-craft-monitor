--[[
  sensor_info_dump.lua - ONE-OFF DIAGNOSTIC, not a production script.

  Purely read-only - getSensorInformation()/getEUStored()/getEUMaxStored()
  have no game-state side effects at all, so there's no confirm-flag
  needed here the way the craft-request test scripts had.

  Dumps EVERY line of getSensorInformation(), not just the two lines
  (stored/capacity) power_monitor.lua currently uses - the goal is to
  see whether an "Average EU Input/Output" line (matching what a
  Scanner shows when right-clicking the controller) is present
  somewhere else in that same array, which we've genuinely never
  checked before.

  Run this directly (not via rc) and read the output.
--]]

local component = require("component")

-- Same discovery logic already proven in power_monitor.lua - reused
-- as-is rather than reinventing it for a one-off diagnostic.
local function find_gt_machine()
  local addresses = {}
  for address in component.list("gt_machine") do
    addresses[#addresses + 1] = address
  end

  if #addresses == 0 then
    return nil, "No gt_machine component found. Check the Adapter is touching the multiblock."
  elseif #addresses > 1 then
    return nil, string.format(
      "Found %d gt_machine components - edit this script to component.proxy() a specific address. Addresses: %s",
      #addresses, table.concat(addresses, ", "))
  end

  return component.proxy(addresses[1]), nil
end

local function main()
  local gt, err = find_gt_machine()
  if not gt then
    print("[sensor_info_dump] " .. tostring(err))
    return
  end
  print("[sensor_info_dump] Found gt_machine component.")

  if gt.getEUStored then
    local ok, stored = pcall(gt.getEUStored)
    print("[sensor_info_dump] getEUStored(): " .. (ok and tostring(stored) or ("ERROR: " .. tostring(stored))))
  else
    print("[sensor_info_dump] No getEUStored() method on this component.")
  end

  if gt.getEUMaxStored then
    local ok, cap = pcall(gt.getEUMaxStored)
    print("[sensor_info_dump] getEUMaxStored(): " .. (ok and tostring(cap) or ("ERROR: " .. tostring(cap))))
  else
    print("[sensor_info_dump] No getEUMaxStored() method on this component.")
  end

  if not gt.getSensorInformation then
    print("[sensor_info_dump] No getSensorInformation() method on this component - can't check further.")
    return
  end

  local ok, info = pcall(gt.getSensorInformation)
  if not ok then
    print("[sensor_info_dump] getSensorInformation() errored: " .. tostring(info))
    return
  end

  print("")
  print("[sensor_info_dump] Full getSensorInformation() array (" .. #info .. " lines):")
  print("---------------------------------------------------------")
  for i, line in ipairs(info) do
    print(string.format("[%d] %s", i, tostring(line)))
  end
  print("---------------------------------------------------------")
  print("[sensor_info_dump] Look through the lines above for anything mentioning")
  print("  'Average', 'In:', 'Out:', or similar - that's the stat we're checking for.")
end

main()
