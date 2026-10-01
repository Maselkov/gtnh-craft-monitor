"""Runs the real oc/*.lua scripts against the app under test.

OcGame starts oc/tests/bridge.lua: a fake OpenComputers computer
(oc/tests/harness/) with a fake ME network and GT machine. Tests set it
up and advance its virtual clock with Lua snippets; every HTTP request a
script makes along the way is answered here by a Flask test client, so a
payload goes through the real routes exactly as one from the game would.
See bridge.lua for the wire format."""

import json
import os
import shutil
import subprocess
from urllib.parse import urlsplit

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRIDGE = os.path.join(REPO_ROOT, "oc", "tests", "bridge.lua")

# OpenComputers runs Lua 5.3; later versions run these scripts the same.
LUA_CANDIDATES = ("lua5.3", "lua5.4", "lua5.5", "lua")


def find_lua():
    override = os.environ.get("OC_LUA")
    if override:
        return override
    return next((path for path in map(shutil.which, LUA_CANDIDATES) if path), None)


class LuaError(Exception):
    pass


class OcGame:
    def __init__(self, client, api_key, lua=None):
        self.client = client
        self.requests = []
        self.proc = subprocess.Popen(
            [lua or find_lua(), BRIDGE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=REPO_ROOT,
        )
        self.lua(
            "env:write_config({ SERVER_URL = 'http://gcm.test', API_KEY = %s })"
            % _lua_string(api_key)
        )

    def lua(self, code):
        """Runs code on the fake computer, answering its HTTP requests
        until it finishes; returns its return value (decoded JSON)."""
        data = code.encode()
        self.proc.stdin.write(b"%d\n" % len(data) + data)
        self.proc.stdin.flush()
        while True:
            header = self.proc.stdout.readline().decode()
            if not header:
                raise LuaError("bridge.lua exited")
            kind, *sizes = header.split()
            if kind == "HTTP":
                meta_len, body_len = int(sizes[0]), int(sizes[1])
                meta = json.loads(self._read(meta_len))
                body = self._read(body_len) if body_len >= 0 else None
                self._answer(meta, body)
            elif kind == "RESULT":
                return json.loads(self._read(int(sizes[0])))
            elif kind == "ERROR":
                raise LuaError(self._read(int(sizes[0])).decode())
            else:
                raise LuaError("unexpected line from bridge.lua: %r" % header)

    def run(self, seconds):
        """Advances the virtual clock, running the started services."""
        self.lua("env:run({ seconds = %r })" % float(seconds))

    def write_file(self, path, content):
        self.lua("env:write_file(%s, %s)" % (_lua_string(path), _lua_string(content)))

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)

    def _read(self, n):
        data = self.proc.stdout.read(n)
        if len(data) != n:
            raise LuaError("bridge.lua exited mid-message")
        return data

    def _answer(self, meta, body):
        url = urlsplit(meta["url"])
        path = url.path + ("?" + url.query if url.query else "")
        response = self.client.open(
            path, method=meta["method"], data=body, headers=meta.get("headers") or {}
        )
        self.requests.append((meta["method"], path, response.status_code))
        payload = response.get_data()
        self.proc.stdin.write(b"%d %d\n" % (response.status_code, len(payload)) + payload)
        self.proc.stdin.flush()


def _lua_string(s):
    """s as a Lua string literal, escaping everything but plain ASCII
    letters, digits and a few safe symbols."""
    safe = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-:/"
    return '"' + "".join(chr(b) if b in safe else "\\%03d" % b for b in s.encode()) + '"'
