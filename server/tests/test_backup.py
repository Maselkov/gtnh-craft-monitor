import io
import os
import shutil
import sqlite3
import zipfile

from gcm import backup, config
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

    def test_includes_the_push_key_when_there_is_one(self, client, monkeypatch, tmp_path):
        key = tmp_path / "vapid_private.pem"
        key.write_text("-----BEGIN PRIVATE KEY-----\n")
        monkeypatch.setattr(config, "VAPID_KEY_PATH", str(key))
        login_as(client, "root", role="admin")
        res = client.get(f"/api/admin/backup/{make(client)['id']}")
        assert "vapid_private.pem" in zipfile.ZipFile(io.BytesIO(res.data)).namelist()
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
