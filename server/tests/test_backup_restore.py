"""备份 / 校验 / 恢复。

这批守的是"数据真的拿得回来"，以及三条容易被忽略的：
  · WAL 里还没落盘的事务不能丢（所以必须用在线备份而不是 cp）；
  · 备份包里不能有可用的明文凭据（老装机盘上可能还是明文）；
  · 别人给的备份包里的 ``../`` 不能解压穿越出去。
"""
from __future__ import annotations

import json
import sqlite3
import zipfile

import pytest

from app.core import backup, secrets

SECRET = "sk-livekey1234567890ABCDEFghij"
PASSPHRASE = "correct horse battery staple"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    conn = sqlite3.connect(tmp_path / "biz.sqlite3")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, asin TEXT)")
    conn.executemany("INSERT INTO orders(asin) VALUES(?)", [("B01",), ("B02",), ("B03",)])
    conn.commit()
    conn.close()

    (tmp_path / "hub_settings.json").write_text(
        json.dumps({"deepseek_api_key": SECRET, "hermes_base_url": "http://x"}),
        encoding="utf-8")
    (tmp_path / "imagegen-jobs").mkdir()
    (tmp_path / "imagegen-jobs" / "big.png").write_bytes(b"x" * 2048)
    return tmp_path


def _names(path):
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


# ── 备份内容 ────────────────────────────────────────────────────────────

def test_backup_contains_databases_and_config(data_dir):
    names = _names(backup.create(data_dir=data_dir))
    assert "db/biz.sqlite3" in names
    assert "files/hub_settings.json" in names
    assert backup.MANIFEST in names


def test_wal_and_shm_are_not_included(data_dir):
    """在线备份产出的是一致快照，包里不该有 -wal/-shm（带了反而会造成不一致）。"""
    (data_dir / "biz.sqlite3-wal").write_bytes(b"junk")
    (data_dir / "biz.sqlite3-shm").write_bytes(b"junk")
    names = _names(backup.create(data_dir=data_dir))
    assert not any(n.endswith(("-wal", "-shm")) for n in names)


def test_media_is_excluded_by_default(data_dir):
    """data 目录实测 429MB，其中大头是可再生的图片 —— 默认不收。"""
    assert not any("imagegen-jobs" in n for n in _names(backup.create(data_dir=data_dir)))
    assert any("imagegen-jobs" in n
               for n in _names(backup.create(data_dir=data_dir, include_media=True)))


def test_backup_never_carries_plaintext_credentials(data_dir):
    """老装机盘上可能还是明文；直接塞进备份等于把密钥随备份一起发出去。"""
    path = backup.create(data_dir=data_dir)
    assert SECRET.encode() not in path.read_bytes()

    with zipfile.ZipFile(path) as zf:
        cfg = json.loads(zf.read("files/hub_settings.json").decode("utf-8"))
    assert cfg["deepseek_api_key"].startswith("enc:v1:")
    assert cfg["hermes_base_url"] == "http://x"       # 非凭据保持可读


def test_master_key_only_travels_when_a_passphrase_is_given(data_dir):
    secrets.master_key()          # 生成 .master.key

    plain = backup.create(data_dir=data_dir)
    assert "secrets/master.key.enc" not in _names(plain)
    assert backup.inspect(plain)["master_key_included"] is False

    sealed = backup.create(data_dir=data_dir, passphrase=PASSPHRASE)
    assert "secrets/master.key.enc" in _names(sealed)
    # 主密钥即使带上也必须是密文
    raw_key = (data_dir / ".master.key").read_bytes()
    assert raw_key not in sealed.read_bytes()


# ── 校验 ────────────────────────────────────────────────────────────────

def test_inspect_accepts_a_good_backup(data_dir):
    report = backup.inspect(backup.create(data_dir=data_dir, passphrase=PASSPHRASE))
    assert report["ok"] is True
    assert report["manifest"]["format"] == backup.FORMAT_VERSION


def test_missing_master_key_is_a_warning_not_a_failure(data_dir):
    """没带密钥仍然是个可用的备份 —— 只是恢复后要重填 key。"""
    report = backup.inspect(backup.create(data_dir=data_dir))
    assert report["ok"] is True
    assert any("主密钥" in p for p in report["problems"])


def test_inspect_detects_tampering(data_dir, tmp_path):
    path = backup.create(data_dir=data_dir)
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(broken, "w") as dst:
        for name in src.namelist():
            blob = src.read(name)
            if name == "files/hub_settings.json":
                blob = b'{"tampered": true}'
            dst.writestr(name, blob)
    report = backup.inspect(broken)
    assert report["ok"] is False
    assert any("损坏" in p for p in report["problems"])


def test_inspect_rejects_a_non_backup_zip(tmp_path):
    junk = tmp_path / "junk.zip"
    with zipfile.ZipFile(junk, "w") as zf:
        zf.writestr("hello.txt", "hi")
    assert backup.inspect(junk)["ok"] is False


# ── 恢复 ────────────────────────────────────────────────────────────────

def test_restore_roundtrip_preserves_every_row(data_dir, tmp_path):
    path = backup.create(data_dir=data_dir, passphrase=PASSPHRASE)

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    report = backup.restore(path, passphrase=PASSPHRASE, dry_run=False, data_dir=fresh)
    assert report["ok"] and report["restored"] >= 2

    conn = sqlite3.connect(fresh / "biz.sqlite3")
    rows = [r[0] for r in conn.execute("SELECT asin FROM orders ORDER BY id")]
    conn.close()
    assert rows == ["B01", "B02", "B03"], "恢复后数据对不上"


def test_restore_recovers_secrets_when_the_passphrase_is_supplied(data_dir, tmp_path):
    from app.core import hub_settings

    hub_settings.save({"deepseek_api_key": SECRET})
    path = backup.create(data_dir=data_dir, passphrase=PASSPHRASE)

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    backup.restore(path, passphrase=PASSPHRASE, dry_run=False, data_dir=fresh)

    from app.core.config import settings
    settings.data_dir = fresh
    assert hub_settings.get("deepseek_api_key") == SECRET, "带口令的备份必须能完整恢复"


def test_dry_run_is_the_default_and_changes_nothing(data_dir, tmp_path):
    path = backup.create(data_dir=data_dir)
    fresh = tmp_path / "fresh"
    fresh.mkdir()

    report = backup.restore(path, data_dir=fresh)       # 不传 dry_run
    assert report["dry_run"] is True
    assert report["restored"] == 0
    assert list(fresh.iterdir()) == [], "干跑不该写任何文件"


def test_dry_run_reports_what_would_be_overwritten(data_dir):
    path = backup.create(data_dir=data_dir)
    report = backup.restore(path, data_dir=data_dir)
    assert "biz.sqlite3" in report["will_overwrite"]


def test_wrong_passphrase_fails_closed(data_dir, tmp_path):
    path = backup.create(data_dir=data_dir, passphrase=PASSPHRASE)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    report = backup.restore(path, passphrase="猜错的口令", dry_run=False, data_dir=fresh)
    assert report["ok"] is False
    assert any("口令" in p for p in report["problems"])
    assert list(fresh.iterdir()) == [], "口令不对时不该写进去一半"


def test_sealed_backup_without_passphrase_is_refused(data_dir, tmp_path):
    path = backup.create(data_dir=data_dir, passphrase=PASSPHRASE)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    report = backup.restore(path, dry_run=False, data_dir=fresh)
    assert report["ok"] is False


def test_restore_blocks_zip_path_traversal(data_dir, tmp_path):
    """别人给的备份包不能无条件信 —— ../ 是经典的解压穿越写法。"""
    path = backup.create(data_dir=data_dir)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(evil, "w") as dst:
        manifest = json.loads(src.read(backup.MANIFEST).decode("utf-8"))
        payload = b"pwned"
        import hashlib
        manifest["entries"].append({
            "path": "files/../../escaped.txt", "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()})
        for name in src.namelist():
            if name != backup.MANIFEST:
                dst.writestr(name, src.read(name))
        dst.writestr("files/../../escaped.txt", payload)
        dst.writestr(backup.MANIFEST, json.dumps(manifest))

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    backup.restore(evil, dry_run=False, data_dir=fresh)
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


# ── 保留策略 ────────────────────────────────────────────────────────────

def test_prune_keeps_the_newest_n(data_dir):
    import os
    import time

    made = []
    for i in range(5):
        p = data_dir / "backups" / f"ivyea-ops-backup-2026010{i}-000000.zip"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        os.utime(p, (time.time() + i, time.time() + i))
        made.append(p)

    assert backup.prune(keep=2, data_dir=data_dir) == 3
    left = sorted(p.name for p in (data_dir / "backups").glob("*.zip"))
    assert left == sorted(p.name for p in made[-2:])


# ── 接口 ────────────────────────────────────────────────────────────────

def test_backup_endpoints_require_admin():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    assert c.post("/api/admin/backup", json={}).status_code in (401, 403)
    assert c.get("/api/admin/backups").status_code in (401, 403)
    assert c.post("/api/admin/restore", json={"path": "x"}).status_code in (401, 403)


def test_restore_endpoint_refuses_to_execute_without_confirm(data_dir):
    """恢复不可逆：干跑报告和执行之间必须隔着一次有意识的确认。"""
    from fastapi.testclient import TestClient

    from app.core import security as sec
    from app.main import app

    from app import main as main_mod

    path = backup.create(data_dir=data_dir)
    app.dependency_overrides[sec.require_admin] = lambda: "admin"
    # POST 到 /api/ 会先过 CSRF Origin 守卫；不带 Origin 会被它 403 掉，
    # 那样测出来的就不是"确认开关"而是守卫本身。
    origin = next(iter(main_mod._ALLOWED), "http://testserver")
    hdr = {"Origin": origin}
    try:
        c = TestClient(app)
        r = c.post("/api/admin/restore", json={"path": str(path), "dry_run": False}, headers=hdr)
        assert r.status_code == 400, r.text
        assert "confirm" in r.text

        ok = c.post("/api/admin/restore", json={"path": str(path)}, headers=hdr)  # 默认干跑
        assert ok.status_code == 200, ok.text
        assert ok.json()["dry_run"] is True
    finally:
        app.dependency_overrides.pop(sec.require_admin, None)
