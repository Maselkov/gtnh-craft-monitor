-- config.lua - the ONE place to set your server URL and API key, plus
-- any per-script settings you want to change.
-- craft_monitor.lua, power_monitor.lua, and network_browser.lua all
-- dofile() this once instead of hardcoding their own copies.
--
-- gcm.lua writes this file on first install and never overwrites it, so
-- put local changes here rather than in the scripts: `gcm update`
-- replaces the scripts themselves.
--
-- Just data, deliberately - the HTTP transport logic that used to live
-- alongside these two values (in the old shared_config.lua) is now its
-- own library, http.lua, loaded separately via require("http"). A file
-- called "config" holding real, non-trivial code (72 of its old 99
-- lines) alongside two config values was the wrong shape - this file
-- now only ever holds what its name says it holds.
--
-- Must be reachable via an absolute path (dofile(), not require()) from
-- each rc-managed script that loads it - whether OpenOS's require()
-- directory-relative search (finding a file next to the CALLING
-- script) works reliably for a script loaded through rc's sandboxed
-- loader was never confirmed, and dofile() with an absolute path
-- sidesteps that question entirely. Update CONFIG_PATH near the top of
-- each script if you keep this file somewhere other than /home/.
--
-- Each script still appends its own endpoint path (/api/crafts,
-- /api/power, /api/network) to SERVER_URL itself - that part is
-- genuinely script-specific, only the shared base URL + key live here.

return {
  SERVER_URL = "https://YOUR-SERVER-HOST:8420",  -- base URL only - NO
                                                   -- trailing slash, NO
                                                   -- /api/... path
  API_KEY    = "change-me",                       -- must match the
                                                   -- server's API_KEY
                                                   -- environment variable

  -- Per-script overrides. Any key from a script's CONFIG block can be
  -- set here; anything left out keeps the script's default. Uncomment
  -- and edit as needed.
  --
  -- craft_monitor = {
  --   POLL_SECONDS = 5,
  --   SHOW_STATUS  = true,
  -- },
  -- power_monitor = {
  --   POLL_SECONDS      = 60,
  --   COMPONENT_ADDRESS = "put-a-gt_machine-address-here",
  -- },
  -- network_browser = {
  --   SCAN_INTERVAL_SECONDS = 600,
  -- },
}
