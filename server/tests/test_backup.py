import io
import os
import shutil
import sqlite3
import zipfile

from gcm import auth, backup, commands, config, store
from conftest import login_as


def make(client):
    res = client.post("/api/admin/backup")
    assert res.status_code == 200, res.get_json()
    return res.get_json()


class TestBackup:
    def test_admins_only(self, client):
        assert client.post("/api/admin/backup").status_code == 403
        login_as(client, "olive", role="operator")
        assert client.post("/api/admin/backup").status_code == 403
        assert client.get("/api/admin/backup/whatever").status_code == 403

    def test_download_holds_every_database_whole(self, client, tmp_path, api_headers):
        client.post("/api/power", json={"stored": 5, "capacity": 10}, headers=api_headers)
        login_as(client, "root", role="admin")
        made = make(client)
        assert made["name"].startswith("gcm-backup-") and made["name"].endswith(".zip")

        res = client.get(f"/api/admin/backup/{made['id']}")
        assert res.status_code == 200
        assert res.headers["Content-Disposition"].startswith("attachment")
        zf = zipfile.ZipFile(io.BytesIO(res.data))
        names = set(zf.namelist())
        assert {"app.db", "power.db", "item_history.db"} <= names
        for name in ("app.db", "power.db", "item_history.db"):
            zf.extract(name, tmp_path)
            conn = sqlite3.connect(tmp_path / name)
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            conn.close()
        conn = sqlite3.connect(tmp_path / "power.db")
        assert conn.execute("SELECT COUNT(*) FROM power_readings").fetchone()[0] == 1
        conn.close()
        res.close()

    def test_includes_the_push_and_api_keys_when_there_are_some(self, client, monkeypatch, tmp_path):
        key = tmp_path / "vapid_private.pem"
        key.write_text("-----BEGIN PRIVATE KEY-----\n")
        monkeypatch.setattr(config, "VAPID_KEY_PATH", str(key))
        api_key = tmp_path / "api_key"
        api_key.write_text("k" * 64 + "\n")
        monkeypatch.setattr(config, "API_KEY_PATH", str(api_key))
        login_as(client, "root", role="admin")
        res = client.get(f"/api/admin/backup/{make(client)['id']}")
        names = zipfile.ZipFile(io.BytesIO(res.data)).namelist()
        assert "vapid_private.pem" in names
        assert "api_key" in names
        res.close()

    def test_downloads_once_then_is_deleted(self, client):
        login_as(client, "root", role="admin")
        made = make(client)
        res = client.get(f"/api/admin/backup/{made['id']}")
        assert res.status_code == 200
        assert client.get(f"/api/admin/backup/{made['id']}").status_code == 404
        res.close()  # the server closes it once it's sent
        assert not [n for n in os.listdir(config.DATA_DIR) if n.startswith(".backup-")]

    def test_wrong_id_and_expired(self, client, monkeypatch):
        login_as(client, "root", role="admin")
        made = make(client)
        assert client.get("/api/admin/backup/nope").status_code == 404
        monkeypatch.setattr(backup, "BACKUP_KEEP_SECONDS", -1)
        assert client.get(f"/api/admin/backup/{made['id']}").status_code == 404

    def test_a_new_backup_replaces_the_last(self, client):
        login_as(client, "root", role="admin")
        first = make(client)
        second = make(client)
        assert client.get(f"/api/admin/backup/{first['id']}").status_code == 404
        res = client.get(f"/api/admin/backup/{second['id']}")
        assert res.status_code == 200
        res.close()

    def test_one_at_a_time(self, client):
        login_as(client, "root", role="admin")
        assert backup._lock.acquire()
        try:
            assert client.post("/api/admin/backup").status_code == 409
        finally:
            backup._lock.release()

    def test_refused_without_the_space(self, client, monkeypatch):
        login_as(client, "root", role="admin")
        monkeypatch.setattr(shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(100, 100, 0))
        assert client.post("/api/admin/backup").status_code == 507


def download(client):
    res = client.get(f"/api/admin/backup/{make(client)['id']}")
    data = res.data
    res.close()
    return data


def restore(client, data):
    return client.post("/api/admin/restore", data=data, content_type="application/zip")


def rezip(data, change):
    """The backup zip `data` with change({name: bytes}) applied."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    change(files)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return out.getvalue()


def edit_db(files, name, sql):
    """Runs sql on the copy of database `name` in a rezip() file set."""
    path = os.path.join(config.DATA_DIR, "scratch.db")
    with open(path, "wb") as f:
        f.write(files[name])
    conn = sqlite3.connect(path)
    conn.executescript(sql)
    conn.close()
    with open(path, "rb") as f:
        files[name] = f.read()
    os.remove(path)


def user_names():
    conn = sqlite3.connect(config.APP_DB_PATH)
    try:
        return [name for (name,) in conn.execute("SELECT display_name FROM users ORDER BY display_name")]
    finally:
        conn.close()


def power_rows():
    conn = sqlite3.connect(config.POWER_DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM power_readings").fetchone()[0]
    finally:
        conn.close()


class TestRestore:
    def test_admins_only(self, client):
        assert restore(client, b"x").status_code == 403
        login_as(client, "olive", role="operator")
        assert restore(client, b"x").status_code == 403

    def test_puts_back_the_databases_users_and_all(self, client, api_headers):
        client.post("/api/power", json={"stored": 5, "capacity": 10}, headers=api_headers)
        login_as(client, "root", role="admin")
        saved = download(client)
        store.users.create("Later", "viewer")
        client.post("/api/power", json={"stored": 6, "capacity": 10}, headers=api_headers)
        assert "Later" in user_names() and power_rows() == 2

        res = restore(client, saved)
        assert res.status_code == 200, res.get_json()
        assert res.get_json() == {"restored": ["app.db", "item_history.db", "power.db"], "signed_in": True}
        assert "Later" not in user_names() and "root" in user_names()
        assert power_rows() == 1
        # The admin's session came back with the backup, so they're still in.
        assert client.get("/api/auth/session").get_json()["user"]["display_name"] == "root"

    def test_a_session_newer_than_the_backup_is_signed_out(self, client):
        login_as(client, "root", role="admin")
        saved = download(client)
        other = login_as(client.application.test_client(), "second", role="admin")
        res = restore(other, saved)
        assert res.get_json()["signed_in"] is False
        assert other.get("/api/auth/session").get_json()["authenticated"] is False

    def test_a_backup_with_no_users_reopens_setup(self, client):
        login_as(client, "root", role="admin")
        saved = rezip(download(client), lambda f: edit_db(f, "app.db", "DELETE FROM sessions; DELETE FROM access_tokens; DELETE FROM users;"))
        assert restore(client, saved).get_json()["signed_in"] is False
        assert auth.setup_code()

    def test_restores_the_keys(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "API_KEY_PATH", str(tmp_path / "api_key"))
        monkeypatch.setattr(config, "VAPID_KEY_PATH", str(tmp_path / "vapid_private.pem"))
        monkeypatch.setattr(config, "API_KEY", config.API_KEY)
        monkeypatch.setattr(config, "_FILE_API_KEY", config.API_KEY)
        login_as(client, "root", role="admin")

        def keys(files):
            files["api_key"] = b"r" * 64 + b"\n"
            files["vapid_private.pem"] = b"-----BEGIN PRIVATE KEY-----\n"

        assert restore(client, rezip(download(client), keys)).status_code == 200
        assert config.API_KEY == "r" * 64
        assert (tmp_path / "vapid_private.pem").read_bytes() == b"-----BEGIN PRIVATE KEY-----\n"
        assert os.stat(tmp_path / "api_key").st_mode & 0o777 == 0o600

    def test_drops_pending_craft_requests(self, client):
        login_as(client, "root", role="admin")
        saved = download(client)
        commands.craft_requests.requests[99] = {"id": 99}
        assert restore(client, saved).status_code == 200
        assert not commands.craft_requests.requests

    def test_refuses_what_isnt_a_backup_and_changes_nothing(self, client):
        login_as(client, "root", role="admin")
        saved = download(client)
        store.users.create("Later", "viewer")

        cases = {
            "not a zip": b"hello",
            "a stranger": rezip(saved, lambda f: f.update({"notes.txt": b"hi"})),
            "a path": rezip(saved, lambda f: f.update({"../app.db": f["app.db"]})),
            "no databases": rezip(saved, lambda f: [f.pop(n) for n in ("app.db", "power.db", "item_history.db")]),
            "damaged": rezip(saved, lambda f: f.update({"app.db": b"not sqlite at all" * 100})),
            "newer": rezip(saved, lambda f: edit_db(f, "power.db", "PRAGMA user_version = 999;")),
        }
        for case, data in cases.items():
            res = restore(client, data)
            assert res.status_code == 400, case
            assert res.get_json()["error"], case
            assert "Later" in user_names(), case
        assert not [n for n in os.listdir(config.DATA_DIR) if n.startswith(".backup-")]

    def test_one_at_a_time(self, client):
        login_as(client, "root", role="admin")
        assert backup._lock.acquire()
        try:
            assert restore(client, b"x").status_code == 409
        finally:
            backup._lock.release()

    def test_too_big_and_no_space(self, client, monkeypatch):
        login_as(client, "root", role="admin")
        saved = download(client)
        monkeypatch.setattr(backup, "RESTORE_MAX_BYTES", 10)
        assert restore(client, saved).status_code == 413
        monkeypatch.undo()
        monkeypatch.setattr(shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(100, 100, 0))
        assert restore(client, saved).status_code == 507
