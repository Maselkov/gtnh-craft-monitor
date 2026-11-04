"""The log of AI assistants' tool calls through /mcp (app.db's
mcp_calls), shown to admins in the AI assistant dialog."""

import json
import time

from gcm import db

KEEP_SECONDS = 30 * 86400
MAX_ARGUMENTS = 2000


def record_call(user_id, tool, arguments, is_error, error, result_bytes, ms):
    """Logs one call, dropping calls older than KEEP_SECONDS."""
    now = time.time()
    text = json.dumps(arguments, ensure_ascii=False, default=str)
    if len(text) > MAX_ARGUMENTS:
        text = text[:MAX_ARGUMENTS] + "…"
    with db.transaction(db.app_db) as conn:
        conn.execute("DELETE FROM mcp_calls WHERE at < ?", (now - KEEP_SECONDS,))
        conn.execute(
            "INSERT INTO mcp_calls (at, user_id, tool, arguments, is_error, error, result_bytes, ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, user_id, tool, text, int(is_error), error, result_bytes, ms),
        )


def recent_calls(limit):
    """The latest calls, newest first, each with its user's display name
    (None for a user deleted since)."""
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            "SELECT mcp_calls.at, mcp_calls.user_id, users.display_name, mcp_calls.tool, mcp_calls.arguments, "
            "mcp_calls.is_error, mcp_calls.error, mcp_calls.result_bytes, mcp_calls.ms "
            "FROM mcp_calls LEFT JOIN users ON users.id = mcp_calls.user_id "
            "ORDER BY mcp_calls.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"at": r[0], "user_id": r[1], "user": r[2], "tool": r[3], "arguments": r[4],
         "is_error": bool(r[5]), "error": r[6], "result_bytes": r[7], "ms": r[8]}
        for r in rows
    ]
