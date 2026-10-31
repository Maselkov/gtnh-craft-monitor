-- manifest.lua - which files gcm.lua installs, and where.
--
-- gcm downloads this from the release it's installing, so files can be
-- added, renamed or moved between releases without changing gcm itself.
-- CI checks that every src listed here exists in oc/.

return {
  -- Installed with any component, and on every update.
  shared = {
    { src = "gcm.lua",  dst = "/usr/bin/gcm.lua" },
    { src = "http.lua", dst = "/usr/lib/http.lua" },
    { src = "json.lua", dst = "/usr/lib/json.lua" },
    { src = "gcm_agent.lua", dst = "/etc/rc.d/gcm_agent.lua" },
  },

  -- needs: component types the script looks for; gcm install warns when
  -- none of them is connected, with needs_hint saying what to place.
  -- wants: the same for an optional block; wants_hint says what it adds.
  components = {
    {
      name = "craft",
      service = "craft_monitor",
      description = "Crafts tab, remote crafting",
      needs = { "me_controller", "me_interface" },
      needs_hint = "an Adapter touching an ME controller or ME interface",
      files = {
        { src = "craft_monitor.lua", dst = "/etc/rc.d/craft_monitor.lua" },
      },
    },
    {
      name = "power",
      service = "power_monitor",
      description = "Power tab",
      needs = { "gt_machine" },
      needs_hint = "an Adapter touching a GregTech machine (e.g. the LSC)",
      files = {
        { src = "power_monitor.lua", dst = "/etc/rc.d/power_monitor.lua" },
      },
    },
    {
      name = "network",
      service = "network_browser",
      description = "Network tab",
      needs = { "me_controller", "me_interface" },
      needs_hint = "an Adapter touching an ME controller or ME interface",
      wants = { "me_interface_terminal" },
      wants_hint = "The crafting plan and job tree need an Adapter touching a block"
        .. " with an ME Interface Terminal part",
      files = {
        { src = "network_browser.lua", dst = "/etc/rc.d/network_browser.lua" },
      },
    },
  },

  -- Written on first install only. Updates never touch it.
  config = { src = "config.lua", dst = "/home/config.lua" },
}
