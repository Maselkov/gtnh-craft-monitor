import os
import re

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SQL belongs in gcm/db.py (schemas and migrations) and gcm/store/
# (everything else). A connection or query anywhere else fails here.
ALLOWED = {os.path.join("gcm", "db.py")}
ALLOWED_DIRS = (os.path.join("gcm", "store") + os.sep,)
SQL_ACCESS = re.compile(
    r"\bsqlite3\b|\.execute(many|script)?\(|\bdb\.(craft_db|power_db|item_history_db|transaction)\b"
)


def python_files():
    yield "app.py"
    for dirpath, _, filenames in os.walk(os.path.join(SERVER_DIR, "gcm")):
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.relpath(os.path.join(dirpath, name), SERVER_DIR)


def test_sql_only_in_db_and_store():
    offenders = []
    for path in python_files():
        if path in ALLOWED or path.startswith(ALLOWED_DIRS):
            continue
        with open(os.path.join(SERVER_DIR, path), encoding="utf-8") as f:
            for number, line in enumerate(f, 1):
                if SQL_ACCESS.search(line):
                    offenders.append(f"{path}:{number}: {line.strip()}")
    assert offenders == []
