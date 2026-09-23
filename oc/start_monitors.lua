--[[
  RETIRED - this file is no longer needed.

  craft_monitor.lua, power_monitor.lua, and network_browser.lua are now
  each their own OpenOS rc service, managed independently rather than
  launched together as threads from one hand-rolled script. See the
  README for the current deployment steps.

  Quick reference:
    rc craft_monitor start / stop / restart / status / enable / disable
    rc power_monitor start / stop / restart / status / enable / disable
    rc network_browser start / stop / restart / status / enable / disable

  This file is kept only so an old copy sitting on a computer doesn't
  silently do nothing without explanation if someone runs it directly.
--]]

print("[start_monitors] This script is retired - see the README.")
print("  Each script is now its own rc service:")
print("    rc craft_monitor start")
print("    rc power_monitor start")
print("    rc network_browser start")
print("  Add 'enable' instead of/after 'start' to auto-start on boot.")
