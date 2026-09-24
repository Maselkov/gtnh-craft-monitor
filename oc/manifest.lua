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
  },

  components = {
    {
      name = "craft",
      service = "craft_monitor",
      description = "Crafts tab, remote crafting",
      files = {
        { src = "craft_monitor.lua", dst = "/etc/rc.d/craft_monitor.lua" },
      },
    },
    {
      name = "power",
      service = "power_monitor",
      description = "Power tab",
      files = {
        { src = "power_monitor.lua", dst = "/etc/rc.d/power_monitor.lua" },
      },
    },
    {
      name = "network",
      service = "network_browser",
      description = "Network tab",
      files = {
        { src = "network_browser.lua", dst = "/etc/rc.d/network_browser.lua" },
        { src = "item_catalog.txt",    dst = "/home/item_catalog.txt" },
      },
    },
  },

  -- Written on first install only. Updates never touch it.
  config = { src = "config.lua", dst = "/home/config.lua" },
}
