#!/bin/sh
# Prints the oldest release with <rev>'s in-game scripts: the one holding
# the last change to oc/ (tests and the debug dumps, which aren't
# installed, aside). The page asks admins to update computers on anything
# older (gcm/config.py SCRIPTS_VERSION), so a release that only changed
# the server asks nothing. Prints nothing when no release has them yet,
# e.g. on main after a scripts change. Needs the tags and history.
set -eu

rev=$1
changed=$(git log -1 --format=%H "$rev" -- oc ':!oc/tests' ':!oc/*_dump.lua')
if [ -n "$changed" ]; then
    git tag --contains "$changed" --merged "$rev" --sort=v:refname |
        grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true
fi
