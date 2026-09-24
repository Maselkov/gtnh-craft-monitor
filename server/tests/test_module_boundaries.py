import ast
import os

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTES_DIR = os.path.join(SERVER_DIR, "gcm", "routes")


def imported_modules(path):
    """Every gcm module a file imports, as dotted names."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_route_modules_do_not_import_each_other():
    # Logic two routes share belongs in a service module (tracking,
    # commands, inventory), not in one route module the other imports.
    offenders = []
    for name in os.listdir(ROUTES_DIR):
        if name.endswith(".py") and name != "__init__.py":
            imports = imported_modules(os.path.join(ROUTES_DIR, name))
            offenders += [f"{name}: {i}" for i in imports if i.startswith("gcm.routes")]
    assert offenders == []


def test_state_holds_plain_data_only():
    imports = imported_modules(os.path.join(SERVER_DIR, "gcm", "state.py"))
    assert not {i for i in imports if i.startswith("gcm")}
