-- Runs every oc/tests/*_test.lua suite against the harness in
-- oc/tests/harness/. From the repo root:
--
--   lua5.3 oc/tests/run.lua            all suites
--   lua5.3 oc/tests/run.lua craft      only tests whose name contains "craft"
--
-- Set OC_HARNESS_ECHO=1 to see what the scripts print.

local dir = arg[0]:match("^(.*)/[^/]*$") or "."
package.path = dir .. "/harness/?.lua;" .. dir .. "/?.lua;" .. package.path

local oc_env = require("oc_env")
local filter = arg[1]

local files = {}
local ls = assert(io.popen("ls " .. dir .. "/*_test.lua"))
for path in ls:lines() do files[#files + 1] = path end
ls:close()

local passed, failed = 0, 0
for _, path in ipairs(files) do
  local suite_name = path:match("([^/]+)_test%.lua$")
  local suite = dofile(path)
  for _, t in ipairs(suite.tests) do
    local full = suite_name .. ": " .. t.name
    if not filter or full:find(filter, 1, true) then
      local ok, err = xpcall(t.fn, debug.traceback)
      oc_env.close_all()
      if ok then
        passed = passed + 1
        print("ok    " .. full)
      else
        failed = failed + 1
        print("FAIL  " .. full .. "\n" .. tostring(err):gsub("\n", "\n      "))
      end
    end
  end
end

print(string.format("\n%d passed, %d failed", passed, failed))
os.exit(failed == 0 and passed > 0)
